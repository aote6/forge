"""仓库感知协议 — 对接 zhiwang"""
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class RepoContext:
    repo_id: str
    commit_hash: str
    file_tree: List[str]
    changed_files: List[str]
    recent_changes: List[str]
    status_excerpt: Optional[str] = None
