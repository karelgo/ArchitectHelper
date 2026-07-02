"""Document generation: renders governance deliverables from Jinja templates."""

from __future__ import annotations

from functools import lru_cache

from jinja2 import Environment, PackageLoader

from archflow.domain.models import ArchitectureRequest, utcnow


def md_cell(value: object) -> str:
    """Escape a value for use inside a markdown table cell.

    Pipes and newlines in user-supplied text (concerns, rationales) would
    otherwise split the table.
    """
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


@lru_cache(maxsize=1)
def _environment() -> Environment:
    """Jinja environment loading templates from the installed package."""
    env = Environment(
        loader=PackageLoader("archflow", "templates"),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["md_cell"] = md_cell
    return env


def render_psa(request: ArchitectureRequest) -> str:
    """Render the Project Start Architecture document as markdown."""
    template = _environment().get_template("psa.md.j2")
    return template.render(request=request, generated_at=utcnow())


def render_decision_log(request: ArchitectureRequest) -> str:
    """Render the decision log as markdown."""
    template = _environment().get_template("decision_log.md.j2")
    return template.render(request=request, generated_at=utcnow())
