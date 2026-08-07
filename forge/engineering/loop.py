"""DEPRECATED: use forge.orchestrator.EngineeringOrchestrator.

This module remains only as a thin compatibility shim.
"""
from __future__ import annotations

import warnings

from forge.orchestrator.engine import EngineeringOrchestrator


class EngineeringLoop:
    def __init__(self, project_root: str, **kwargs):
        warnings.warn(
            "EngineeringLoop is deprecated; use EngineeringOrchestrator",
            DeprecationWarning,
            stacklevel=2,
        )
        self.project_root = project_root
        self._kwargs = kwargs
        self._orch = None

    def run(self, task: str, task_id: str = None) -> str:
        raise RuntimeError(
            "EngineeringLoop.run is disabled. "
            "Construct EngineeringOrchestrator with world/projections/planner."
        )
