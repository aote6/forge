"""command_class_prefixes — single source of truth for run_command classification.

Public API:
  - COMMAND_CLASS_PREFIXES / COMMAND_CLASS_UNKNOWN  (data)
  - is_compound_shell_command(cmd)                  (compound detection)
  - resolve_command_class(cmd)                      (compound → unknown, else prefix)

Matching contract:
  - Compound shell control/redirect structures → COMMAND_CLASS_UNKNOWN.
  - Otherwise prefix match only; longer prefixes win.
  - Unknown prefix → COMMAND_CLASS_UNKNOWN.
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
    "python -m mypy": "type_check",
    "mypy": "type_check",
    "rm": "destructive",
    "mv": "destructive",
}

# Multi-character shell control / redirect / substitution tokens.
_COMPOUND_MULTI = (
    "&&",
    "||",
    ">>",
    "<<",
    "$(",
    "\n",
    "\r",
)


def is_compound_shell_command(cmd: str) -> bool:
    """True if cmd contains shell control, redirect, or substitution structure.

    Detects at least:
      &&  ||  ;  |  &  \\n  >  >>  <  <<  `  $(
    """
    text = cmd if cmd is not None else ""
    if not text:
        return False
    for tok in _COMPOUND_MULTI:
        if tok in text:
            return True
    # Single-character markers (after multi-char checks so &&/||/>>/<< still count).
    for ch in (";", "|", "&", ">", "<", "`"):
        if ch in text:
            return True
    return False


def resolve_command_class(cmd: str) -> str:
    """Map a shell command string to a command_class.

    1. Compound structures → COMMAND_CLASS_UNKNOWN
    2. Static prefix whitelist (longer first)
    3. Else COMMAND_CLASS_UNKNOWN
    """
    text = " ".join((cmd or "").strip().split()) if cmd else ""
    # Preserve newlines for compound detection: do NOT collapse them away first.
    raw = cmd if isinstance(cmd, str) else ""
    if is_compound_shell_command(raw):
        return COMMAND_CLASS_UNKNOWN
    text = (cmd or "").strip()
    if not text:
        return COMMAND_CLASS_UNKNOWN
    # Normalize internal whitespace only for prefix match (not for compound).
    # Use original stripped text; prefix match allows space/tab boundaries.
    for prefix in sorted(COMMAND_CLASS_PREFIXES.keys(), key=len, reverse=True):
        if text == prefix or text.startswith(prefix + " ") or text.startswith(prefix + "\t"):
            return COMMAND_CLASS_PREFIXES[prefix]
        if text.startswith(prefix):
            rest = text[len(prefix) :]
            if rest == "" or rest[0].isspace():
                return COMMAND_CLASS_PREFIXES[prefix]
    return COMMAND_CLASS_UNKNOWN
