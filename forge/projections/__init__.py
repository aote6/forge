"""Projection layer — consumes committed Veritas transactions."""

from forge.projections.base import Projection, ProjectionManager
from forge.projections.file_projection import FileProjection

__all__ = ["Projection", "ProjectionManager", "FileProjection"]
