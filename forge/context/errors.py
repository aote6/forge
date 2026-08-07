"""Context errors — explicit, never silent."""

from __future__ import annotations


class ContextFatalError(Exception):
    """Fatal: context cannot be built at all."""
    pass


class PartialContextError(Exception):
    """Non-fatal: context built but with errors."""
    pass
