"""Content hashing — deterministic and collision-resistant."""

from __future__ import annotations

import hashlib

from forge.context.models import FileEntry


def hash_file(path: str) -> str:
    """SHA-256 of file content."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def compute_tree_hash(files: list[FileEntry]) -> str:
    """Deterministic tree hash over sorted path:hash pairs.

    Format: "{path}:{hash}\\n" per file, sorted by path.
    Prevents concatenation collisions (a+bc vs ab+c).
    """
    lines = sorted(f"{f.path}:{f.hash}" for f in files)
    serialized = "\n".join(lines) + "\n"
    return hashlib.sha256(serialized.encode()).hexdigest()
