"""Git helpers for the Forge Sync Layer.

Sync 需要同时比较 Git commit ancestry、working tree 状态与已知文件 hash。
这些是纯 `git` CLI 封装，不含任何 World / Projection / 同步判定逻辑。

契约 §3 / §5：判定必须同时考虑 commit ancestry + working tree + World 元数据；
§5 要求持久化 last_known_commit / last_known_file_hashes。
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def is_git_repo(project_root: str) -> bool:
    """是否位于 Git 仓库内（决策 5：工作区必须是 Git repository）。"""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def git_head_commit(project_root: str) -> str:
    """返回 HEAD commit hash；无提交 / 非仓库时返回 ""。"""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def git_status_porcelain(project_root: str) -> str:
    """返回 `git status --porcelain` 输出（用于检测外部 working tree 变化）。"""
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def git_diff(project_root: str, paths: list[str] | None = None) -> str:
    """返回 unified diff（CONFLICT 时用于展示差异信息）。"""
    try:
        cmd = ["git", "diff", "--unified=3", "--"]
        if paths:
            cmd.extend(paths)
        r = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.stdout or ""
    except Exception:
        return ""


def hash_file(path: str) -> str | None:
    """文件 sha256 hex；缺失 / 不可读返回 None。

    用于 last_known_file_hashes，是"文件内容权威 = Disk + Git"的观测手段，
    不是 World 的文件内容副本（契约 §2）。
    """
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def git_status_porcelain_untracked_all(project_root: str) -> str:
    """`git status --porcelain -uall`：展开未跟踪目录内的文件。

    用于外部新建文件检测；默认 porcelain 对未跟踪目录只显示目录名，
    会漏掉目录内的具体文件。
    """
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""

