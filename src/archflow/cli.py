"""ArchFlow command-line interface (Typer).

The stakeholder option syntax is ``"Name:Role:concern one|concern two"``
(role and concerns optional): ``--stakeholder "Alice:CIO:cost|continuity"``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from archflow.config import get_settings
from archflow.domain.models import (
    ArchitectureRequest,
    Classification,
    DecisionStatus,
    ReviewVerdict,
    Stakeholder,
)
from archflow.storage import EventLog, RequestRepository, create_db_engine
from archflow.workflow.engine import WorkflowEngine

app = typer.Typer(
    name="archflow",
    help="Automate the architecture governance process, from intake to Horizzon publication.",
    no_args_is_help=True,
)


def _engine() -> WorkflowEngine:
    settings = get_settings()
    db = create_db_engine(settings.database_url)
    return WorkflowEngine(
        repository=RequestRepository(db), events=EventLog(db), settings=settings
    )


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def parse_stakeholder(raw: str) -> Stakeholder:
    """Parse ``Name:Role:concern1|concern2`` into a Stakeholder."""
    parts = raw.split(":", 2)
    name = parts[0].strip()
    if not name:
        raise typer.BadParameter(f"Stakeholder needs a name: {raw!r}")
    role = parts[1].strip() if len(parts) > 1 else ""
    concerns = (
        [c.strip() for c in parts[2].split("|") if c.strip()] if len(parts) > 2 else []
    )
    return Stakeholder(name=name, role=role, concerns=concerns)


def _print_request(request: ArchitectureRequest) -> None:
    typer.secho(f"{request.title}  [{request.id}]", bold=True)
    typer.echo(f"  stage:          {request.stage.value}")
    typer.echo(f"  requester:      {request.requester or '-'}")
    typer.echo(f"  classification: {request.classification.value if request.classification else '-'}")
    typer.echo(f"  domains:        {', '.join(request.impacted_domains) or '-'}")
    if request.stakeholders:
        typer.echo("  stakeholders:")
        for s in request.stakeholders:
            concerns = f" — concerns: {', '.join(s.concerns)}" if s.concerns else ""
            typer.echo(f"    - {s.name} ({s.role or 'role unknown'}){concerns}")
    typer.echo("  checklist:")
    for item in request.checklist:
        mark = "x" if item.done else " "
        typer.echo(f"    [{mark}] {item.key}  ({item.stage.value}) {item.description}")
    if request.decisions:
        typer.echo("  decisions:")
        for d in request.decisions:
            typer.echo(f"    - [{d.status.value}] {d.title}")
    if request.reviews:
        typer.echo("  reviews:")
        for r in request.reviews:
            typer.echo(f"    - {r.reviewer}: {r.verdict.value}")
    if request.artifacts:
        typer.echo("  artifacts:")
        for a in request.artifacts:
            typer.echo(f"    - {a.kind.value}: {a.path}")


@app.command()
def new(
    title: Annotated[str, typer.Option(help="Short title of the architecture request")],
    description: Annotated[str, typer.Option(help="What needs to change and why")] = "",
    requester: Annotated[str, typer.Option(help="Who asked for this change")] = "",
    business_goal: Annotated[str, typer.Option(help="Business goal this serves")] = "",
    domain: Annotated[
        list[str] | None, typer.Option(help="Impacted domain (repeatable)")
    ] = None,
    stakeholder: Annotated[
        list[str] | None,
        typer.Option(help='Stakeholder as "Name:Role:concern1|concern2" (repeatable)'),
    ] = None,
) -> None:
    """Register a new architecture request (intake)."""
    request = _engine().create_request(
        title=title,
        description=description,
        requester=requester,
        business_goal=business_goal,
        impacted_domains=domain or [],
        stakeholders=[parse_stakeholder(s) for s in (stakeholder or [])],
    )
    typer.secho(f"Created request {request.id}", fg=typer.colors.GREEN)
    _print_request(request)


@app.command("list")
def list_requests() -> None:
    """List all requests, most recently updated first."""
    requests = _engine().list_requests()
    if not requests:
        typer.echo("No requests yet. Create one with: archflow new --title '...'")
        return
    for r in requests:
        classification = r.classification.value if r.classification else "-"
        typer.echo(f"{r.id}  {r.stage.value:<20} {classification:<7} {r.title}")


@app.command()
def show(request_id: str) -> None:
    """Show one request in detail."""
    request = _engine().get(request_id)
    if request is None:
        _fail(f"Unknown request id: {request_id}")
        return
    _print_request(request)


@app.command()
def advance(
    request_id: str,
    actor: Annotated[str, typer.Option(help="Who is advancing the request")] = "cli",
) -> None:
    """Advance the request to the next stage (or list what blocks it)."""
    engine = _engine()
    if engine.get(request_id) is None:
        _fail(f"Unknown request id: {request_id}")
    result = engine.advance(request_id, actor=actor)
    if result.advanced:
        typer.secho(
            f"Advanced: {result.from_stage.value} -> {result.to_stage.value if result.to_stage else '?'}",
            fg=typer.colors.GREEN,
        )
        for artifact in result.generated:
            typer.echo(f"  generated {artifact.kind.value}: {artifact.path}")
    else:
        typer.secho(f"Blocked in stage '{result.from_stage.value}':", fg=typer.colors.YELLOW)
        for reason in result.reasons:
            typer.echo(f"  - {reason}")
        raise typer.Exit(code=1)


@app.command()
def triage(
    request_id: str,
    classification: Annotated[Classification, typer.Option(help="small / medium / large")],
    domain: Annotated[
        list[str] | None, typer.Option(help="Impacted domain (repeatable)")
    ] = None,
) -> None:
    """Record the triage outcome (classification + impacted domains)."""
    engine = _engine()
    if engine.get(request_id) is None:
        _fail(f"Unknown request id: {request_id}")
    request = engine.set_triage(request_id, classification, domain or None)
    typer.secho(f"Triage recorded: {classification.value}", fg=typer.colors.GREEN)
    _print_request(request)


@app.command()
def review(
    request_id: str,
    reviewer: Annotated[str, typer.Option(help="Reviewer name")],
    verdict: Annotated[ReviewVerdict, typer.Option(help="approve / request_changes")],
    comments: Annotated[str, typer.Option(help="Review comments")] = "",
) -> None:
    """Record a peer-review verdict."""
    engine = _engine()
    if engine.get(request_id) is None:
        _fail(f"Unknown request id: {request_id}")
    engine.record_review(request_id, reviewer=reviewer, verdict=verdict, comments=comments)
    typer.secho(f"Review recorded: {reviewer} -> {verdict.value}", fg=typer.colors.GREEN)


@app.command()
def decide(
    request_id: str,
    title: Annotated[str, typer.Option(help="Decision title")],
    rationale: Annotated[str, typer.Option(help="Why this decision")] = "",
    status: Annotated[
        DecisionStatus, typer.Option(help="proposed / approved / rejected")
    ] = DecisionStatus.PROPOSED,
    decided_by: Annotated[str, typer.Option(help="Who decided (e.g. the board)")] = "",
) -> None:
    """Record an architecture decision."""
    engine = _engine()
    if engine.get(request_id) is None:
        _fail(f"Unknown request id: {request_id}")
    engine.record_decision(
        request_id, title=title, rationale=rationale, status=status, decided_by=decided_by
    )
    typer.secho(f"Decision recorded: [{status.value}] {title}", fg=typer.colors.GREEN)


@app.command()
def complete(
    request_id: str,
    key: Annotated[str, typer.Argument(help="Checklist item key, e.g. triage.classified")],
    actor: Annotated[str, typer.Option(help="Who completed the item")] = "cli",
) -> None:
    """Manually complete a checklist item."""
    engine = _engine()
    if engine.get(request_id) is None:
        _fail(f"Unknown request id: {request_id}")
    try:
        engine.complete_item(request_id, key, actor=actor)
    except KeyError as err:
        _fail(str(err))
    typer.secho(f"Completed checklist item {key}", fg=typer.colors.GREEN)


@app.command()
def reject(
    request_id: str,
    reason: Annotated[str, typer.Option(help="Why the request is rejected")],
    actor: Annotated[str, typer.Option(help="Who rejects")] = "cli",
) -> None:
    """Reject the request (terminal side-exit)."""
    engine = _engine()
    if engine.get(request_id) is None:
        _fail(f"Unknown request id: {request_id}")
    engine.reject(request_id, actor=actor, reason=reason)
    typer.secho("Request rejected.", fg=typer.colors.YELLOW)


@app.command()
def events(request_id: str) -> None:
    """Show the audit trail for one request."""
    engine = _engine()
    try:
        trail = engine.events_for(request_id)
    except KeyError:
        _fail(f"Unknown request id: {request_id}")
        return
    for event in trail:
        stamp = event.occurred_at.strftime("%Y-%m-%d %H:%M:%S")
        typer.echo(f"{stamp}  {event.type.value:<22} {event.actor:<12} {event.payload}")


@app.command("export-map")
def export_map(
    request_id: str,
    out: Annotated[Path | None, typer.Option(help="Output path for the exchange file")] = None,
) -> None:
    """Export the stakeholder map as an ArchiMate Open Exchange file."""
    from archflow.archimate.stakeholder_map import build_stakeholder_map

    engine = _engine()
    request = engine.get(request_id)
    if request is None:
        _fail(f"Unknown request id: {request_id}")
        return
    model = build_stakeholder_map(request)
    path = out or Path(f"stakeholder_map_{request_id}.archimate.xml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.to_open_exchange_xml(), encoding="utf-8")
    typer.secho(f"Exported stakeholder map to {path}", fg=typer.colors.GREEN)


@app.command()
def publish(
    request_id: str,
    repository_id: Annotated[
        int | None, typer.Option(help="Horizzon repository id (default: first accessible)")
    ] = None,
) -> None:
    """Publish the stakeholder map to Horizzon (or export the exchange file)."""
    from archflow.horizzon.publisher import HorizzonPublisher

    engine = _engine()
    request = engine.get(request_id)
    if request is None:
        _fail(f"Unknown request id: {request_id}")
        return
    result = HorizzonPublisher(get_settings()).publish(request, repository_id=repository_id)
    color = typer.colors.GREEN if result.mode == "api" else typer.colors.YELLOW
    typer.secho(f"[{result.mode}] {result.detail}", fg=color)


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind address")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port")] = 8000,
) -> None:
    """Run the REST API (interactive docs at /docs)."""
    import uvicorn

    uvicorn.run("archflow.api.app:create_app", factory=True, host=host, port=port)


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
