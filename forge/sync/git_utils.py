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


class GitError(RuntimeError):
    """git 命令真实失败（非"空结果"）。调用方不得把 GitError 当成正常空返回。"""


def _run_git(args: list[str], project_root: str, timeout: int) -> subprocess.CompletedProcess:
    """运行 git 子进程；git 本身不可用 / 超时等真实故障转为 GitError。"""
    try:
        return subprocess.run(
            args,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as e:
        raise GitError(f"git {' '.join(args)} failed: {e}") from e


def is_git_repo(project_root: str) -> bool:
    """是否位于 Git 仓库内（决策 5：工作区必须是 Git repository）。"""
    try:
        r = _run_git(["git", "rev-parse", "--git-dir"], project_root, timeout=5)
        return r.returncode == 0
    except GitError:
        return False


def git_head_commit(project_root: str) -> str:
    """返回 HEAD commit hash；无提交 / 非仓库 / git 不可用时返回 ""。

    "" 在此处是合法信号（"未知 HEAD"），不是伪造的空 diff；真正的空结果
    语义（如"无差异"）由 git_status_porcelain / git_diff / untracked_all 承担，
    那些函数对真实 git 故障会抛 GitError 而非返回 ""。
    """
    try:
        r = _run_git(["git", "rev-parse", "HEAD"], project_root, timeout=5)
    except GitError:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def git_status_porcelain(project_root: str) -> str:
    """返回 `git status --porcelain` 输出（用于检测外部 working tree 变化）。

    `git status` 非零退出是真实 git 故障（脏工作区仍返回 0），不得伪装成空结果。
    """
    r = _run_git(["git", "status", "--porcelain"], project_root, timeout=5)
    if r.returncode != 0:
        raise GitError(
            f"git status --porcelain failed (exit={r.returncode}): {r.stderr.strip()}"
        )
    return r.stdout


def git_diff(project_root: str, paths: list[str] | None = None) -> str:
    """返回 unified diff（CONFLICT 时用于展示差异信息）。

    `git diff` 非零退出是真实 git 故障，不得伪装成"无差异"。
    """
    cmd = ["git", "diff", "--unified=3", "--"]
    if paths:
        cmd.extend(paths)
    r = _run_git(cmd, project_root, timeout=10)
    if r.returncode != 0:
        raise GitError(f"git diff failed (exit={r.returncode}): {r.stderr.strip()}")
    return r.stdout or ""


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

    非零退出是真实 git 故障；若伪装成空结果，会把"外部新建文件"漏检成 IN_SYNC。
    """
    r = _run_git(["git", "status", "--porcelain", "-uall"], project_root, timeout=5)
    if r.returncode != 0:
        raise GitError(
            f"git status --porcelain -uall failed (exit={r.returncode}): {r.stderr.strip()}"
        )
    return r.stdout

