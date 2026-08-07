"""Task checkpoint store — full recoverable orchestrator state."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import List, Optional

from forge.protocols.models import TaskCheckpoint

TASK_DIR = ".forge/tasks"


class CheckpointStore:
    def __init__(self, project_root: str):
        self.project_root = project_root
        self._dir = os.path.join(project_root, TASK_DIR)
        os.makedirs(self._dir, exist_ok=True)

    def _path(self, task_id: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in task_id)[:120]
        return os.path.join(self._dir, f"{safe}.json")

    def save(self, checkpoint: TaskCheckpoint) -> str:
        checkpoint.updated_at = datetime.now(timezone.utc).isoformat()
        path = self._path(checkpoint.task_id)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(checkpoint.to_json())
        os.replace(tmp, path)
        return path

    def load(self, task_id: str) -> Optional[TaskCheckpoint]:
        path = self._path(task_id)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return TaskCheckpoint.from_dict(data)

    def list_tasks(self) -> List[dict]:
        out = []
        if not os.path.isdir(self._dir):
            return out
        for name in sorted(os.listdir(self._dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self._dir, name)
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                out.append({
                    "task_id": data.get("task_id"),
                    "phase": data.get("phase"),
                    "updated_at": data.get("updated_at"),
                    "goal": data.get("goal", "")[:80],
                })
            except (OSError, json.JSONDecodeError):
                continue
        return out

    def delete(self, task_id: str) -> bool:
        path = self._path(task_id)
        if os.path.isfile(path):
            os.remove(path)
            return True
        return False
