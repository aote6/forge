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
    # Common read-only investigation commands (prefix-only; compound still unknown).
    # Do NOT add: find/env/xargs/sort/sed/awk/python/bash/sh/node/less/date
    # (parameter forms can mutate or exec without compound tokens).
    "ls": "read_only",
    "cat": "read_only",
    "head": "read_only",
    "tail": "read_only",
    "wc": "read_only",
    "grep": "read_only",
    "rg": "read_only",  # --pre / --pre-glob forced unknown below
    "file": "read_only",
    "stat": "read_only",
    "du": "read_only",
    "df": "read_only",
    "pwd": "read_only",
    "which": "read_only",
    "whereis": "read_only",
    "uname": "read_only",
    "whoami": "read_only",
    "id": "read_only",
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


def _rg_uses_external_preprocessor(cmd: str) -> bool:
    """True if rg is asked to run an external preprocessor (--pre / --pre-glob).

    Prefix-only matching would otherwise ALLOW `rg --pre evil ...`, which executes
    arbitrary commands. Keep those unknown → PAUSE at the confirmation gate.
    """
    s = f" {cmd} "
    if " --pre " in s or " --pre=" in s:
        return True
    if cmd.startswith("--pre ") or cmd.startswith("--pre="):
        return True
    if " --pre-glob " in s or " --pre-glob=" in s:
        return True
    if cmd.startswith("--pre-glob ") or cmd.startswith("--pre-glob="):
        return True
    return False


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
    cls: str | None = None
    for prefix in sorted(COMMAND_CLASS_PREFIXES.keys(), key=len, reverse=True):
        if text == prefix or text.startswith(prefix + " ") or text.startswith(prefix + "\t"):
            cls = COMMAND_CLASS_PREFIXES[prefix]
            break
        if text.startswith(prefix):
            rest = text[len(prefix) :]
            if rest == "" or rest[0].isspace():
                cls = COMMAND_CLASS_PREFIXES[prefix]
                break
    if cls is None:
        return COMMAND_CLASS_UNKNOWN
    # rg --pre / --pre-glob runs an external preprocessor (arbitrary exec).
    if cls == "read_only" and (text == "rg" or text.startswith("rg ") or text.startswith("rg\t")):
        if _rg_uses_external_preprocessor(text):
            return COMMAND_CLASS_UNKNOWN
    return cls
