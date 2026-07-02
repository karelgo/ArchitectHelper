"""BiZZdesign Horizzon integration: Open API client and publisher.

The Bizzdesign Open API (v3.0) is read-only for native Enterprise Studio
model content and has no view/diagram endpoints. ArchFlow therefore pushes
element/relation data as *architecture automation* collections where the API
allows writes, and always produces an ArchiMate Open Exchange file so views
can be imported into Enterprise Studio. See ``docs/horizzon-integration.md``.
"""

from archflow.horizzon.auth import HorizzonAuth, HorizzonAuthError
from archflow.horizzon.client import HorizzonClient, HorizzonError
from archflow.horizzon.publisher import HorizzonPublisher, PublishResult

__all__ = [
    "HorizzonAuth",
    "HorizzonAuthError",
    "HorizzonClient",
    "HorizzonError",
    "HorizzonPublisher",
    "PublishResult",
]
