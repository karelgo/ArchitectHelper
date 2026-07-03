"""Tests for the SVG view renderer and its preview endpoints."""

from __future__ import annotations

import json

from archflow.archimate.model import ArchimateModel
from archflow.archimate.svg import view_to_svg
from archflow.assistant.tools import ToolExecutor
from archflow.studio.models import ViewProject


def project_with_view() -> ViewProject:
    project = ViewProject(name="P", model=ArchimateModel(name="P"))
    executor = ToolExecutor(project)
    result = executor.execute(
        "add_elements",
        {
            "elements": [
                {"type": "ApplicationComponent", "name": "CRM <suite>"},
                {"type": "BusinessProcess", "name": "Sales"},
            ]
        },
    )
    assert json.loads(result)["created"]
    executor.execute(
        "add_relationships",
        {"relationships": [{"type": "Serving", "source": "CRM <suite>", "target": "Sales"}]},
    )
    executor.execute("create_view", {"name": "Overview"})
    return project


def test_view_to_svg_renders_nodes_and_connections() -> None:
    project = project_with_view()
    svg = view_to_svg(project.model)

    assert svg.startswith("<svg ")
    assert svg.count("<rect ") == 3  # background + two element boxes
    assert svg.count("<line ") == 1
    assert "CRM &lt;suite&gt;" in svg, "names are XML-escaped"
    assert "#ffff99".lower() in svg.lower(), "business layer fill applied"
    assert "ArchiMate view: Overview" in svg


def test_view_to_svg_placeholder_without_views() -> None:
    svg = view_to_svg(ArchimateModel(name="Empty"))
    assert "No view yet" in svg


def test_view_to_svg_truncates_long_names() -> None:
    project = ViewProject(name="P", model=ArchimateModel(name="P"))
    executor = ToolExecutor(project)
    long_name = "A very long application component name that cannot possibly fit"
    executor.execute("add_elements", {"elements": [{"type": "Goal", "name": long_name}]})
    executor.execute("create_view", {})
    svg = view_to_svg(project.model)
    assert long_name not in svg
    assert "…" in svg
