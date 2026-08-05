"""Forge world identity persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


class IdentityStore:
    """Persist Forge's ObjectId on the host so restarts can re-attach."""

    def __init__(self, project_root: str | Path):
        root = Path(project_root).expanduser().resolve()
        self.path = root / ".forge" / "world_identity"

    def load(self) -> Optional[int]:
        if not self.path.exists():
            return None
        try:
            text = self.path.read_text(encoding="utf-8").strip()
            if not text:
                return None
            return int(text)
        except (ValueError, OSError):
            return None

    def save(self, object_id: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(int(object_id)) + "\n", encoding="utf-8")

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
