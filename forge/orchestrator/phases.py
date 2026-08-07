"""Orchestrator phase definitions — single source of truth."""
from forge.protocols.models import OrchestratorPhase

# Re-export for convenience
Phase = OrchestratorPhase

TERMINAL = {OrchestratorPhase.COMPLETED, OrchestratorPhase.FAILED}

MAX_SELF_CORRECTION = 3
