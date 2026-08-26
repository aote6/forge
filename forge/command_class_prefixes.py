"""command_class_prefixes - static prefix whitelist for run_command.

Pure data. No functions, no classes.

Matching contract:
- Prefix match only.
- Longer prefixes must be checked before shorter ones by the enforcer.
- Unknown prefix resolves to COMMAND_CLASS_UNKNOWN.
- When a constraint references command_class, unknown must be denied.
"""
from __future__ import annotations

COMMAND_CLASS_UNKNOWN = "unknown"

COMMAND_CLASS_PREFIXES: dict[str, str] = {
    "python -m pytest": "test",
    "pytest": "test",
    "git push": "vcs_write",
    "git commit": "vcs_write",
    "git log": "vcs_read",
    "git diff": "vcs_read",
    "git status": "vcs_read",
    "rm": "destructive",
    "mv": "destructive",
}
