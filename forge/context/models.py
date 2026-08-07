"""RepositoryContext v1 — machine-verifiable repository snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GitInfo:
    commit: str | None  # None if not a git repo
    branch: str | None
    dirty: bool

    def to_dict(self) -> dict:
        return {
            "commit": self.commit,
            "branch": self.branch,
            "dirty": self.dirty,
        }


@dataclass
class FileEntry:
    path: str  # relative to repo_path
    size: int
    hash: str  # SHA-256 hex
    language: str | None
    content: str | None  # None if excluded by budget

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "size": self.size,
            "hash": self.hash,
            "language": self.language,
            "content": self.content,
        }


@dataclass
class ContextError:
    path: str
    reason: str  # "permission_denied", "binary", "too_large", "decode_error"

    def to_dict(self) -> dict:
        return {"path": self.path, "reason": self.reason}


@dataclass
class RepositoryContext:
    schema_version: str  # "1.0"
    repo_path: str
    git: GitInfo
    files: list[FileEntry]
    tree_hash: str
    errors: list[ContextError]

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "repo_path": self.repo_path,
            "git": self.git.to_dict(),
            "files": [f.to_dict() for f in self.files],
            "tree_hash": self.tree_hash,
            "errors": [e.to_dict() for e in self.errors],
        }

    def file_count(self) -> int:
        return len(self.files)

    def files_with_content(self) -> list[FileEntry]:
        return [f for f in self.files if f.content is not None]
