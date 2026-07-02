"""Horizzon integration exceptions."""

from __future__ import annotations


class HorizzonError(Exception):
    """Any failure talking to the Bizzdesign Open API."""


class HorizzonAuthError(HorizzonError):
    """Authentication against the Horizzon token endpoint failed."""
