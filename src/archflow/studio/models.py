"""Domain model for Studio view projects.

A :class:`ViewProject` is a working ArchiMate model being designed in the
Studio UI: the canonical :class:`ArchimateModel`, the last draw.io document
the user saved (visual styling survives), and the copilot conversation.

``model_rev`` increments on every model mutation; ``drawio_rev`` records the
revision the stored draw.io document was rendered from. When they differ, the
diagram is regenerated from the model (layout survives via the synced view).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from archflow.archimate.model import ArchimateModel
from archflow.domain.models import new_id, utcnow


class ViewProject(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    description: str = ""
    request_id: str | None = None

    model: ArchimateModel
    drawio_xml: str = ""
    model_rev: int = 0
    drawio_rev: int = -1

    assistant_history: list[dict[str, Any]] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def touch(self) -> None:
        self.updated_at = utcnow()

    def bump_model(self) -> None:
        """Record that the canonical model changed (diagram needs re-render)."""
        self.model_rev += 1
        self.touch()

    @property
    def diagram_is_current(self) -> bool:
        return bool(self.drawio_xml) and self.drawio_rev == self.model_rev
