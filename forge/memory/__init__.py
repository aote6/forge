"""Memory package — process-local MemoryStore.

Task CheckpointStore (orchestrator recovery) was removed with the six-phase path.
"""
from __future__ import annotations

try:
    import importlib.util
    import os

    _path = os.path.join(os.path.dirname(__file__), "..", "memory.py")
    _spec = importlib.util.spec_from_file_location("forge._memory_store_mod", _path)
    _mod = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(_mod)
    MemoryStore = _mod.MemoryStore
except Exception:  # pragma: no cover
    from dataclasses import dataclass, field
    from typing import Any

    @dataclass
    class MemoryStore:
        facts: dict = field(default_factory=dict)
        preferences: dict = field(default_factory=dict)
        cache: dict = field(default_factory=dict)

        def remember(self, key: str, value: Any):
            self.facts[key] = value

        def recall(self, key: str) -> Any:
            return self.facts.get(key)


__all__ = ["MemoryStore"]
