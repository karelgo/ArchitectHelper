"""The ArchiMate copilot: Claude with tools over a Studio view project.

A manual tool-use loop (``messages.create`` + ``tool_use``/``tool_result``)
so every mutation runs through :class:`ToolExecutor` and is persisted. The
conversation lives on the project (``assistant_history``) in API wire format.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

from pydantic import BaseModel, Field

from archflow.assistant.tools import TOOLS, ToolExecutor
from archflow.config import Settings
from archflow.studio.models import ViewProject

_SYSTEM_PROMPT = """\
You are the ArchFlow Studio copilot: an expert ArchiMate 3.2 modeler who helps
the user create architecture views from A to Z. The model you build is
rendered live in an embedded draw.io editor next to this chat.

Working method:
1. Understand first. If the user's description leaves real modeling choices
   open, ask ONE focused question; otherwise start building.
2. Model in ArchiMate terms: pick the right layer and element type for each
   concept (business process vs application service vs capability...), and use
   semantically correct relationships (component REALIZES service, service
   SERVES process, process is ASSIGNED to role/actor, ACCESS for data,
   TRIGGERING/FLOW for sequence, motivation links via INFLUENCE/ASSOCIATION).
3. Build incrementally with the tools: add elements, wire relationships, then
   call create_view so the user sees the diagram. Always finish a modeling
   round with create_view — without it the canvas does not update.
4. Quality-check yourself: after building or changing the model, call
   lint_model and fix every error it reports (warnings: fix or explain why
   they are intentional) before replying.
5. Close each reply with a short summary of what changed and a concrete
   suggestion for the next step (e.g. "shall we add the technology layer?").

Rules:
- Never invent organisation-specific facts; ask instead.
- Prefer 5-15 elements per view; suggest splitting into multiple views when
  the scope grows beyond that.
- If a tool reports an error, correct your call — do not repeat it verbatim.
- The user may have edited the diagram in draw.io; call get_model rather than
  trusting your memory of earlier turns.
"""

#: Safety cap on tool-use rounds within one user turn.
_MAX_ROUNDS = 8

#: Keep at most this many messages of history (trimmed at user-turn borders).
_MAX_HISTORY = 40


class CopilotUnavailable(RuntimeError):
    """Raised when no Anthropic API key is configured."""


class CopilotReply(BaseModel):
    """What one user turn produced."""

    reply: str
    actions: list[str] = Field(default_factory=list)
    model_updated: bool = False


class MessagesClient(Protocol):
    """The slice of the Anthropic client the copilot uses (test seam)."""

    def create(self, **kwargs: Any) -> Any: ...


def _default_messages_client(settings: Settings) -> MessagesClient:
    import anthropic

    # The SDK's overloaded create() signature doesn't match the minimal
    # protocol structurally, but the call shape we use is identical.
    messages: MessagesClient = anthropic.Anthropic(
        api_key=settings.anthropic_api_key
    ).messages  # type: ignore[assignment]
    return messages


def _serialize_content(content: list[Any]) -> list[dict[str, Any]]:
    """SDK content blocks -> plain dicts for DB persistence and replay."""
    blocks: list[dict[str, Any]] = []
    for block in content:
        if hasattr(block, "model_dump"):
            blocks.append(block.model_dump())
        else:
            blocks.append(dict(block))
    return blocks


def _trim_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bound history size, cutting only at plain user-text turn boundaries."""
    if len(history) <= _MAX_HISTORY:
        return history
    for index in range(len(history) - _MAX_HISTORY, len(history)):
        message = history[index]
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return history[index:]
    return history[-_MAX_HISTORY:]  # no clean boundary found; keep the tail


class ArchiMateCopilot:
    """Drives one conversation turn against a view project."""

    def __init__(
        self,
        settings: Settings,
        messages_client: MessagesClient | None = None,
    ) -> None:
        if messages_client is None and not settings.assistant_configured:
            raise CopilotUnavailable(
                "No Anthropic API key configured. Set ARCHFLOW_ANTHROPIC_API_KEY "
                "to enable the copilot; template-based generation still works."
            )
        self._settings = settings
        self._messages = messages_client or _default_messages_client(settings)

    def chat(
        self, project: ViewProject, user_message: str, context: str | None = None
    ) -> CopilotReply:
        """One user turn: runs the tool loop and persists history on the project.

        ``context`` (e.g. the linked governance request) rides as an extra
        system block after the cached prompt, so it never pollutes history.
        """
        final: dict[str, Any] = {}
        for event in self.chat_stream(project, user_message, context=context):
            if event["type"] == "final":
                final = event
        return CopilotReply(
            reply=final["reply"], actions=final["actions"], model_updated=final["model_updated"]
        )

    def chat_stream(
        self, project: ViewProject, user_message: str, context: str | None = None
    ) -> Iterator[dict[str, Any]]:
        """:meth:`chat` as a stream of progress events.

        Yields a ``round`` event after each tool round (with that round's
        actions, so the UI can show live progress) and always ends with one
        ``final`` event carrying the reply; history persists at that point.
        """
        executor = ToolExecutor(project)
        messages: list[dict[str, Any]] = [*project.assistant_history]
        messages.append({"role": "user", "content": user_message})

        system: list[dict[str, Any]] = [
            {"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ]
        if context:
            system.append(
                {
                    "type": "text",
                    "text": f"Context — this view belongs to a governance request:\n{context}",
                }
            )

        reply_text = ""
        rounds = 0
        for _ in range(_MAX_ROUNDS):
            response = self._messages.create(
                model=self._settings.assistant_model,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                system=system,
                tools=TOOLS,
                messages=messages,
            )

            messages.append(
                {"role": "assistant", "content": _serialize_content(response.content)}
            )

            if response.stop_reason == "pause_turn":
                continue  # server-side pause: re-send to resume

            text_parts = [
                block.text for block in response.content if block.type == "text"
            ]
            if text_parts:
                reply_text = "\n".join(text_parts)

            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                break

            actions_before = len(executor.actions)
            results = [
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": executor.execute(block.name, block.input or {}),
                }
                for block in tool_uses
            ]
            messages.append({"role": "user", "content": results})
            rounds += 1
            yield {
                "type": "round",
                "round": rounds,
                "actions": executor.actions[actions_before:],
                "model_updated": executor.changed,
            }
        else:
            reply_text = reply_text or (
                "I hit the per-turn tool limit — the model so far is saved. "
                "Say 'continue' to keep going."
            )

        project.assistant_history = _trim_history(messages)
        project.touch()
        yield {
            "type": "final",
            "reply": reply_text or "(no reply)",
            "actions": executor.actions,
            "model_updated": executor.changed,
        }
