"""Persistent project memory for fewer re-exploration steps."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _path(project_root: str) -> Path:
    return Path(project_root) / ".forge" / "project_memory.json"


def load_memory(project_root: str) -> dict[str, Any]:
    p = _path(project_root)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_memory(project_root: str, data: dict[str, Any]) -> None:
    p = _path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def update_memory(project_root: str, **kwargs: Any) -> dict[str, Any]:
    data = load_memory(project_root)
    for k, v in kwargs.items():
        if v is None:
            continue
        if k == "recent_files" and isinstance(v, str):
            files = list(data.get("recent_files") or [])
            files = [v] + [f for f in files if f != v]
            data["recent_files"] = files[:10]
        elif k == "flaky_or_failed_tests" and isinstance(v, list):
            data["flaky_or_failed_tests"] = v[:20]
        else:
            data[k] = v
    save_memory(project_root, data)
    return data


def format_for_prompt(project_root: str) -> str:
    data = load_memory(project_root)
    if not data:
        return ""
    lines = ["\n\n## 项目记忆"]
    if data.get("test_command"):
        lines.append(f"- 测试命令: {data['test_command']}")
    if data.get("recent_files"):
        lines.append("- 最近改过: " + ", ".join(data["recent_files"][:8]))
    if data.get("flaky_or_failed_tests"):
        lines.append("- 曾失败测试: " + ", ".join(data["flaky_or_failed_tests"][:5]))
    if data.get("last_task"):
        lines.append(f"- 上次任务: {data['last_task']}")
    if data.get("last_status"):
        lines.append(f"- 上次状态: {data['last_status']}")
    if data.get("notes"):
        notes = data["notes"]
        if isinstance(notes, list):
            for n in notes[:3]:
                lines.append(f"- 备注: {n}")
        else:
            lines.append(f"- 备注: {notes}")
    return "\n".join(lines) if len(lines) > 1 else ""
