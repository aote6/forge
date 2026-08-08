"""RepositoryContext Runtime v1 — machine-verifiable repository snapshot.

Usage:
    from forge.context import build_context

    ctx = build_context("/path/to/repo")
    # ctx.to_dict() for checkpoint persistence
    # ctx.files_with_content() for Planner input
"""

from forge.context.repository import build_context
from forge.context.models import (
    RepositoryContext,
    GitInfo,
    FileEntry,
    ContextError,
)
from forge.context.errors import ContextFatalError, PartialContextError

__all__ = [
    "build_context",
    "RepositoryContext",
    "GitInfo",
    "FileEntry",
    "ContextError",
    "ContextFatalError",
    "PartialContextError",
]

from forge.context.snapshot import (
    RepositorySnapshot,
    StaleSnapshotError,
    assert_snapshot_match,
    take_snapshot,
)

__all__ = list(__all__) + [
    "RepositorySnapshot",
    "StaleSnapshotError",
    "assert_snapshot_match",
    "take_snapshot",
]

from forge.context.index import RepositoryIndex, Symbol, Reference, ImportRecord

__all__ = list(__all__) + [
    "RepositoryIndex",
    "Symbol",
    "Reference",
    "ImportRecord",
]
