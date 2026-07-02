"""Tests for the ArchiMate copilot: tool executor + scripted tool-use loop."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from archflow.archimate.model import ArchimateModel
from archflow.assistant.copilot import ArchiMateCopilot, CopilotUnavailable
from archflow.assistant.tools import ToolExecutor
from archflow.config import Settings
from archflow.studio.models import ViewProject


def make_project(name: str = "P") -> ViewProject:
    return ViewProject(name=name, model=ArchimateModel(name=name))


# -- tool executor ----------------------------------------------------------------


def test_add_elements_validates_types() -> None:
    project = make_project()
    executor = ToolExecutor(project)
    result = json.loads(
        executor.execute(
            "add_elements",
            {
                "elements": [
                    {"type": "ApplicationComponent", "name": "CRM"},
                    {"type": "NotAType", "name": "Bad"},
                ]
            },
        )
    )
    assert len(result["created"]) == 1
    assert "NotAType" in result["errors"][0]
    assert executor.changed
    assert project.model_rev == 1


def test_add_relationships_resolves_names_and_reports_missing() -> None:
    project = make_project()
    executor = ToolExecutor(project)
    executor.execute(
        "add_elements",
        {
            "elements": [
                {"type": "ApplicationComponent", "name": "CRM"},
                {"type": "ApplicationService", "name": "Customer data"},
            ]
        },
    )
    result = json.loads(
        executor.execute(
            "add_relationships",
            {
                "relationships": [
                    {"type": "Realization", "source": "CRM", "target": "Customer data"},
                    {"type": "Serving", "source": "Ghost", "target": "CRM"},
                ]
            },
        )
    )
    assert result["created"] == 1
    assert "Ghost" in result["errors"][0]
    assert project.model.relationships[0].type == "Realization"


def test_remove_elements_cascades() -> None:
    project = make_project()
    executor = ToolExecutor(project)
    executor.execute(
        "add_elements",
        {
            "elements": [
                {"type": "ApplicationComponent", "name": "CRM"},
                {"type": "BusinessProcess", "name": "Sales"},
            ]
        },
    )
    executor.execute(
        "add_relationships",
        {"relationships": [{"type": "Serving", "source": "CRM", "target": "Sales"}]},
    )
    executor.execute("create_view", {})
    removed = json.loads(executor.execute("remove_elements", {"elements": ["CRM"]}))
    assert removed["removed"] == ["CRM"]
    assert len(project.model.elements) == 1
    assert not project.model.relationships
    assert all(n.element_ref != "CRM" for n in project.model.views[0].nodes)
    # The rebuilt index still accepts new relationships on remaining elements.
    result = json.loads(
        executor.execute(
            "add_elements", {"elements": [{"type": "BusinessActor", "name": "Rep"}]}
        )
    )
    assert result["created"]


def test_create_view_lays_out_by_layer() -> None:
    project = make_project()
    executor = ToolExecutor(project)
    executor.execute(
        "add_elements",
        {
            "elements": [
                {"type": "BusinessProcess", "name": "Sell"},
                {"type": "ApplicationComponent", "name": "CRM"},
            ]
        },
    )
    executor.execute(
        "add_relationships",
        {"relationships": [{"type": "Serving", "source": "CRM", "target": "Sell"}]},
    )
    result = json.loads(executor.execute("create_view", {"name": "Overview"}))
    assert result == {"view": "Overview", "nodes": 2, "connections": 1}
    view = project.model.views[0]
    y_by_name = {
        next(e.name for e in project.model.elements if e.id == n.element_ref): n.y
        for n in view.nodes
    }
    assert y_by_name["Sell"] < y_by_name["CRM"], "business band above application band"


def test_create_view_requires_elements() -> None:
    executor = ToolExecutor(make_project())
    assert "Error" in executor.execute("create_view", {})


def test_unknown_tool_returns_error_string() -> None:
    executor = ToolExecutor(make_project())
    assert "unknown tool" in executor.execute("nope", {})


# -- copilot loop -------------------------------------------------------------------


@dataclass
class FakeBlock:
    type: str
    text: str = ""
    id: str = "toolu_1"
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        if self.type == "text":
            return {"type": "text", "text": self.text}
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


@dataclass
class FakeResponse:
    content: list[FakeBlock]
    stop_reason: str


class ScriptedMessages:
    """Feeds pre-scripted responses; records every request payload."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        snapshot = dict(kwargs)
        snapshot["messages"] = [dict(m) for m in kwargs.get("messages", [])]
        self.requests.append(snapshot)
        return self._responses.pop(0)


def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_copilot_requires_key_or_injected_client() -> None:
    with pytest.raises(CopilotUnavailable):
        ArchiMateCopilot(settings())


def test_copilot_tool_loop_mutates_project_and_persists_history() -> None:
    project = make_project("Loop")
    scripted = ScriptedMessages(
        [
            FakeResponse(
                content=[
                    FakeBlock(type="text", text="Adding the elements."),
                    FakeBlock(
                        type="tool_use",
                        id="toolu_a",
                        name="add_elements",
                        input={
                            "elements": [{"type": "ApplicationComponent", "name": "CRM"}]
                        },
                    ),
                ],
                stop_reason="tool_use",
            ),
            FakeResponse(
                content=[
                    FakeBlock(
                        type="tool_use",
                        id="toolu_b",
                        name="create_view",
                        input={"name": "Landscape"},
                    )
                ],
                stop_reason="tool_use",
            ),
            FakeResponse(
                content=[FakeBlock(type="text", text="Done — one component on the view.")],
                stop_reason="end_turn",
            ),
        ]
    )
    copilot = ArchiMateCopilot(settings(), messages_client=scripted)
    reply = copilot.chat(project, "Model our CRM landscape")

    assert reply.model_updated
    assert "Done" in reply.reply
    assert any("+1 element" in a for a in reply.actions)
    assert len(project.model.elements) == 1
    assert project.model.views and project.model.views[0].name == "Landscape"

    # History: user, assistant(tool), user(result), assistant(tool), user(result), assistant
    roles = [m["role"] for m in project.assistant_history]
    assert roles == ["user", "assistant", "user", "assistant", "user", "assistant"]
    tool_result = project.assistant_history[2]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "toolu_a"

    # Every request used the configured model, tools, and adaptive thinking.
    for request in scripted.requests:
        assert request["model"] == settings().assistant_model
        assert request["thinking"] == {"type": "adaptive"}
        assert request["tools"], "tools must be offered on every round"


def test_copilot_second_turn_replays_history() -> None:
    project = make_project("Turns")
    first = ScriptedMessages(
        [FakeResponse(content=[FakeBlock(type="text", text="Hi!")], stop_reason="end_turn")]
    )
    ArchiMateCopilot(settings(), messages_client=first).chat(project, "hello")

    second = ScriptedMessages(
        [FakeResponse(content=[FakeBlock(type="text", text="Again")], stop_reason="end_turn")]
    )
    ArchiMateCopilot(settings(), messages_client=second).chat(project, "and again")
    sent = second.requests[0]["messages"]
    assert sent[0] == {"role": "user", "content": "hello"}
    assert len(sent) == 3  # prior user + assistant, then the new user turn


def test_copilot_round_cap_saves_progress() -> None:
    project = make_project("Cap")
    tool_response = FakeResponse(
        content=[
            FakeBlock(
                type="tool_use",
                id="toolu_x",
                name="add_elements",
                input={"elements": [{"type": "Goal", "name": "G"}]},
            )
        ],
        stop_reason="tool_use",
    )
    scripted = ScriptedMessages([tool_response] * 8)
    reply = ArchiMateCopilot(settings(), messages_client=scripted).chat(project, "go")
    assert "tool limit" in reply.reply
    assert reply.model_updated
    assert len(project.model.elements) == 8
