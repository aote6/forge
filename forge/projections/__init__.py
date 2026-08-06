"""Projection layer — consumes committed Veritas transactions."""

from forge.projections.base import Projection, ProjectionManager, ProjectionResult
from forge.projections.file_projection import FileProjection
from forge.projections.git_projection import GitProjection
from forge.projections.index_projection import IndexProjection

__all__ = [
    "Projection",
    "ProjectionManager",
    "ProjectionResult",
    "FileProjection",
    "GitProjection",
    "IndexProjection",
]
