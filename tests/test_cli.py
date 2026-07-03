"""Tests for the Typer CLI (CliRunner with env pointing at tmp storage)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from archflow.cli import app, parse_stakeholder
from archflow.config import get_settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the CLI at a temporary database and artifacts dir."""
    monkeypatch.setenv("ARCHFLOW_DATABASE_URL", f"sqlite:///{tmp_path}/cli.db")
    monkeypatch.setenv("ARCHFLOW_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def create_request() -> str:
    result = runner.invoke(
        app,
        [
            "new",
            "--title", "CRM renewal",
            "--description", "Replace the aging CRM",
            "--requester", "Alice",
            "--business-goal", "Happier customers",
            "--stakeholder", "Alice:Sales director:Adoption risk|Budget",
            "--stakeholder", "Bob:CISO:Data protection",
        ],
    )
    assert result.exit_code == 0, result.output
    match = re.search(r"Created request (\w+)", result.output)
    assert match, result.output
    return match.group(1)


def test_parse_stakeholder_full_syntax() -> None:
    s = parse_stakeholder("Alice:CIO:cost|continuity")
    assert s.name == "Alice"
    assert s.role == "CIO"
    assert s.concerns == ["cost", "continuity"]


def test_parse_stakeholder_name_only() -> None:
    s = parse_stakeholder("Bob")
    assert s.name == "Bob"
    assert s.role == ""
    assert s.concerns == []


def test_new_list_show() -> None:
    request_id = create_request()

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "CRM renewal" in result.output
    assert request_id in result.output

    result = runner.invoke(app, ["show", request_id])
    assert result.exit_code == 0
    assert "stage:          intake" in result.output
    assert "Alice" in result.output
    assert "[ ] triage.classified" in result.output


def test_unknown_id_exits_nonzero() -> None:
    for args in (["show", "nope"], ["advance", "nope"], ["events", "nope"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 1
        assert "Unknown request id" in result.output


def test_advance_blocked_exits_nonzero() -> None:
    request_id = create_request()
    # intake gate passes (title/description/requester present) -> triage
    result = runner.invoke(app, ["advance", request_id])
    assert result.exit_code == 0, result.output
    # triage blocks until classified
    result = runner.invoke(app, ["advance", request_id])
    assert result.exit_code == 1
    assert "Blocked" in result.output


def test_full_walk_to_done_with_real_automation() -> None:
    """End-to-end through the real actions: map, PSA and export generated."""
    request_id = create_request()

    def advance_ok() -> str:
        result = runner.invoke(app, ["advance", request_id])
        assert result.exit_code == 0, result.output
        return result.output

    advance_ok()  # intake -> triage
    result = runner.invoke(
        app, ["triage", request_id, "--classification", "medium", "--domain", "CRM"]
    )
    assert result.exit_code == 0, result.output

    out = advance_ok()  # triage -> stakeholder_analysis (real map generated)
    assert "generated stakeholder_map" in out
    out = advance_ok()  # -> drafting (real PSA generated)
    assert "generated psa_document" in out

    runner.invoke(app, ["decide", request_id, "--title", "Buy over build"])
    advance_ok()  # -> peer_review
    runner.invoke(
        app, ["review", request_id, "--reviewer", "Bob", "--verdict", "approve"]
    )
    advance_ok()  # -> board_approval
    runner.invoke(
        app,
        [
            "decide", request_id,
            "--title", "Board approval",
            "--status", "approved",
            "--decided-by", "Board",
        ],
    )
    out = advance_ok()  # -> publication (unconfigured Horizzon -> file export)
    assert "generated archimate_export" in out
    advance_ok()  # -> done

    result = runner.invoke(app, ["show", request_id])
    assert "stage:          done" in result.output

    result = runner.invoke(app, ["events", request_id])
    assert result.exit_code == 0
    assert "stage_entered" in result.output


def test_export_map_writes_exchange_file(tmp_path: Path) -> None:
    request_id = create_request()
    out_path = tmp_path / "maps" / "map.xml"
    result = runner.invoke(app, ["export-map", request_id, "--out", str(out_path)])
    assert result.exit_code == 0, result.output
    content = out_path.read_text(encoding="utf-8")
    assert "opengroup.org/xsd/archimate/3.0/" in content
    assert "Stakeholder" in content


def test_publish_without_horizzon_reports_file_export() -> None:
    request_id = create_request()
    result = runner.invoke(app, ["publish", request_id])
    assert result.exit_code == 0, result.output
    assert "[file_export]" in result.output


def test_reject_flow() -> None:
    request_id = create_request()
    result = runner.invoke(app, ["reject", request_id, "--reason", "Duplicate request"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["show", request_id])
    assert "rejected" in result.output


def test_add_stakeholder_command() -> None:
    request_id = create_request()
    result = runner.invoke(app, ["add-stakeholder", request_id, "Carol:Architect:coherence"])
    assert result.exit_code == 0, result.output
    shown = runner.invoke(app, ["show", request_id])
    assert "Carol" in shown.output


def test_manual_publish_lands_on_audit_trail() -> None:
    request_id = create_request()
    result = runner.invoke(app, ["publish", request_id])
    assert result.exit_code == 0, result.output
    events = runner.invoke(app, ["events", request_id])
    assert "artifact_generated" in events.output
    shown = runner.invoke(app, ["show", request_id])
    assert "archimate_export" in shown.output


def test_ai_commands_require_key() -> None:
    request_id = create_request()
    result = runner.invoke(app, ["ai", "review", request_id])
    assert result.exit_code == 1
    assert "ARCHFLOW_ANTHROPIC_API_KEY" in result.output


def test_ai_stakeholders_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    from archflow.assistant.governance import ProposedStakeholder, StakeholderProposal

    request_id = create_request()
    proposal = StakeholderProposal(
        stakeholders=[
            ProposedStakeholder(name="Works council", role="Consultation"),
            ProposedStakeholder(name="Alice", role="dup — must be skipped"),
        ],
    )

    class FakeAssistant:
        def draft_stakeholder_analysis(self, request: object) -> StakeholderProposal:
            return proposal

    monkeypatch.setattr("archflow.cli._assistant", lambda: FakeAssistant())
    result = runner.invoke(app, ["ai", "stakeholders", request_id, "--apply"])
    assert result.exit_code == 0, result.output
    assert "Works council" in result.output

    shown = runner.invoke(app, ["show", request_id])
    assert "Works council" in shown.output
    assert "dup — must be skipped" not in shown.output, "duplicate name must not be re-added"
