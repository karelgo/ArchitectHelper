"""Tests for document rendering (PSA and decision log)."""

from __future__ import annotations

from archflow.domain.models import (
    ArchitectureRequest,
    Classification,
    Decision,
    DecisionStatus,
    Driver,
    Goal,
    Stakeholder,
)
from archflow.workflow.documents import render_decision_log, render_psa


def test_render_psa_contains_core_content() -> None:
    request = ArchitectureRequest(
        title="CRM Replacement",
        description="Replace the legacy CRM",
        requester="alice",
        business_goal="Improve customer retention",
        impacted_domains=["sales", "marketing"],
        classification=Classification.LARGE,
        stakeholders=[
            Stakeholder(name="Eve Jansen", role="CFO", concerns=["cost", "compliance"]),
            Stakeholder(name="Tom Peters", role="Sales lead"),
        ],
        drivers=[Driver(name="Customer churn", description="Losing customers")],
        goals=[Goal(name="Retention +10%")],
        decisions=[
            Decision(title="Adopt SaaS", status=DecisionStatus.APPROVED, decided_by="board")
        ],
    )
    output = render_psa(request)

    assert "CRM Replacement" in output
    assert "Eve Jansen" in output
    assert "Tom Peters" in output
    assert "cost; compliance" in output
    assert "Improve customer retention" in output
    assert "sales" in output
    assert "Customer churn" in output
    assert "Adopt SaaS" in output
    assert "large" in output
    assert output.startswith("# Project Start Architecture")


def test_render_psa_handles_empty_collections() -> None:
    request = ArchitectureRequest(title="Bare minimum")
    output = render_psa(request)

    assert "Bare minimum" in output
    assert "No stakeholders recorded" in output
    assert "No decisions recorded" in output
    assert "No impacted domains recorded" in output
    assert "unclassified" in output


def test_render_decision_log() -> None:
    request = ArchitectureRequest(
        title="Log test",
        decisions=[
            Decision(
                title="Use Postgres",
                rationale="Team knows it",
                status=DecisionStatus.APPROVED,
                decided_by="board",
            )
        ],
    )
    output = render_decision_log(request)
    assert "Use Postgres" in output
    assert "approved" in output
    assert "Team knows it" in output
    assert "board" in output


def test_render_decision_log_empty() -> None:
    output = render_decision_log(ArchitectureRequest(title="Empty"))
    assert "No decisions recorded" in output
