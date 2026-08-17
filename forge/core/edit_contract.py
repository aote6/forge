"""Forge Edit Contract — frozen authoring ↔ machine schemas and the sole converter.

P0 CLOSED CONTRACT
==================

Authoring Edit (Planner / LLM / human only)
-------------------------------------------
{
  "type": "replace" | "delete" | "insert",   # optional; inferred if absent
  "start_line": int,   # 1-based inclusive
  "end_line": int,     # 1-based inclusive
  "new_text": str      # full replacement text ("" allowed for delete)
}

Semantics:
  - Lines are 1-based inclusive ranges over the current file.
  - replace: replace lines [start_line, end_line] with new_text
  - delete:  same range; new_text must be ""
  - insert:  insert new_text *before* line start_line; requires
             start_line == end_line (insert point). Machine range is empty.

Machine EditOp (Intent / Veritas state_id=2 / FileProjection / PatchEngine)
--------------------------------------------------------------------------
{
  "type": "replace" | "delete" | "insert",
  "start_line": int,   # 0-based
  "end_line": int,     # exclusive  → half-open [start_line, end_line)
  "new_lines": list[str]  # lines as produced by splitlines(keepends=True)
}

FORBIDDEN after the conversion boundary:
  - 1-based semantics
  - old_text / new_text as mutation fields

Sole conversion boundary
------------------------
  authoring_to_machine_ops(...)

  start0 = start_line - 1
  end0   = end_line          # inclusive 1-based end → exclusive 0-based end
  new_lines = text_to_new_lines(new_text)

Trailing newline rule (frozen)
------------------------------
  text_to_new_lines(s):
    - s == ""           → []
    - otherwise         → s.splitlines(keepends=True)
  Notes:
    - A final newline in s is preserved as a trailing empty-less last line
      that ends with "\\n" (splitlines keepends behaviour).
    - A string without a final newline yields a last element without "\\n".
    - This is stable and matches PatchEngine / difflib line conventions.

Intent.parameters["operations"] and Veritas state_id=2 MUST contain only
Machine EditOp dicts.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Union

AUTHORING_TYPES = frozenset({"replace", "delete", "insert"})
MACHINE_TYPES = frozenset({"replace", "delete", "insert"})


class EditContractError(ValueError):
    """Raised when authoring or machine edit ops violate the frozen contract."""


def text_to_new_lines(new_text: str) -> List[str]:
    """Frozen trailing-newline rule: splitlines(keepends=True); "" → []."""
    if not isinstance(new_text, str):
        raise EditContractError(f"new_text must be str, got {type(new_text).__name__}")
    if new_text == "":
        return []
    return new_text.splitlines(keepends=True)


def new_lines_to_text(new_lines: Sequence[str]) -> str:
    """Inverse of text_to_new_lines for tests / diagnostics."""
    return "".join(new_lines)


def is_machine_op(op: Mapping[str, Any]) -> bool:
    """True if op already looks like a Machine EditOp (has new_lines, no new_text)."""
    if not isinstance(op, Mapping):
        return False
    if "new_lines" not in op:
        return False
    # Authoring must not use new_lines; if both present, treat as invalid upstream.
    if "new_text" in op and op.get("new_text") not in (None, ""):
        # Ambiguous: prefer fail-closed at conversion time
        return False
    return True


def is_authoring_op(op: Mapping[str, Any]) -> bool:
    """True if op looks like Authoring Edit (has new_text key or lacks new_lines)."""
    if not isinstance(op, Mapping):
        return False
    if "new_lines" in op and "new_text" not in op:
        return False
    return "start_line" in op and "end_line" in op


def validate_authoring_op(op: Mapping[str, Any], *, require_type: bool = False) -> None:
    if not isinstance(op, Mapping):
        raise EditContractError(f"authoring op must be a dict, got {op!r}")
    if "start_line" not in op or "end_line" not in op:
        raise EditContractError(
            f"authoring op requires start_line and end_line, got {op!r}"
        )
    start = op["start_line"]
    end = op["end_line"]
    if not isinstance(start, int) or not isinstance(end, int):
        raise EditContractError(
            f"authoring start_line/end_line must be int, got {start!r}, {end!r}"
        )
    if start < 1:
        raise EditContractError(f"authoring start_line must be >= 1, got {start}")
    op_type = op.get("type") or op.get("operation_type") or "replace"
    if op_type not in AUTHORING_TYPES:
        raise EditContractError(f"authoring type must be one of {sorted(AUTHORING_TYPES)}, got {op_type!r}")
    if require_type and "type" not in op and "operation_type" not in op:
        raise EditContractError("authoring op requires type")
    if "new_text" not in op:
        raise EditContractError(
            f"authoring op requires new_text (use '' for delete), got {op!r}"
        )
    if op["new_text"] is None or not isinstance(op["new_text"], str):
        raise EditContractError("authoring new_text must be a str (not null)")
    if op_type == "delete" and op["new_text"] != "":
        raise EditContractError("authoring delete requires new_text == ''")
    if op_type == "insert":
        if start != end:
            raise EditContractError(
                f"authoring insert requires start_line == end_line (insert point), "
                f"got {start}..{end}"
            )
    else:
        if end < start:
            raise EditContractError(
                f"authoring end_line must be >= start_line, got {start}..{end}"
            )


def validate_machine_op(op: Mapping[str, Any]) -> None:
    if not isinstance(op, Mapping):
        raise EditContractError(f"machine op must be a dict, got {op!r}")
    for key in ("start_line", "end_line", "new_lines"):
        if key not in op:
            raise EditContractError(
                f"machine op requires start_line/end_line/new_lines, got {op!r}"
            )
    if "new_text" in op:
        raise EditContractError(
            f"machine op must not carry new_text; got {op!r}"
        )
    if "old_text" in op:
        raise EditContractError(
            f"machine op must not carry old_text; got {op!r}"
        )
    start = op["start_line"]
    end = op["end_line"]
    if not isinstance(start, int) or start < 0:
        raise EditContractError(
            f"machine start_line must be 0-based non-negative int, got {start!r}"
        )
    if not isinstance(end, int) or end < start:
        raise EditContractError(
            f"machine end_line must be >= start_line (half-open), got [{start}, {end})"
        )
    new_lines = op["new_lines"]
    if not isinstance(new_lines, list) or not all(isinstance(x, str) for x in new_lines):
        raise EditContractError(
            f"machine new_lines must be list[str], got {new_lines!r}"
        )
    op_type = op.get("type", "replace")
    if op_type not in MACHINE_TYPES:
        raise EditContractError(f"machine type invalid: {op_type!r}")
    if op_type == "delete" and new_lines:
        raise EditContractError("machine delete requires new_lines == []")
    if op_type == "insert" and start != end:
        raise EditContractError(
            f"machine insert requires empty range start==end, got [{start}, {end})"
        )


def authoring_to_machine_op(op: Mapping[str, Any]) -> dict:
    """Sole authoring → machine conversion for one op.

    start0 = start_line - 1
    end0   = end_line          # 1-based inclusive end → 0-based exclusive end
    For insert: machine range is [start0, start0).
    """
    validate_authoring_op(op)
    start = op["start_line"]
    end = op["end_line"]
    new_text = op["new_text"]
    op_type = op.get("type") or op.get("operation_type") or "replace"
    if op_type == "replace" and new_text == "":
        op_type = "delete"

    start0 = start - 1
    if op_type == "insert":
        end0 = start0
    else:
        end0 = end

    machine = {
        "type": op_type,
        "start_line": start0,
        "end_line": end0,
        "new_lines": text_to_new_lines(new_text),
    }
    validate_machine_op(machine)
    return machine


def authoring_to_machine_ops(ops: Sequence[Mapping[str, Any]]) -> List[dict]:
    """Convert a list of authoring ops to machine EditOps. Sole boundary API."""
    if not isinstance(ops, (list, tuple)):
        raise EditContractError(f"operations must be a list, got {type(ops).__name__}")
    if not ops:
        raise EditContractError("operations list must be non-empty")
    return [authoring_to_machine_op(op) for op in ops]


def ensure_machine_ops(ops: Sequence[Mapping[str, Any]]) -> List[dict]:
    """Normalize a list of ops to machine form.

    - If every op is already machine → validate and return copies.
    - If every op is authoring → convert once via authoring_to_machine_ops.
    - Mixed or ambiguous → EditContractError.

    Direct Intent callers that already pass machine ops stay valid without
    inventing a second converter; authoring ops always go through
    authoring_to_machine_ops.
    """
    if not isinstance(ops, (list, tuple)) or not ops:
        raise EditContractError("operations must be a non-empty list")

    machine_flags = [is_machine_op(op) for op in ops]
    authoring_flags = [is_authoring_op(op) and not is_machine_op(op) for op in ops]

    if all(machine_flags):
        out = []
        for op in ops:
            validate_machine_op(op)
            out.append({
                "type": op.get("type", "replace"),
                "start_line": op["start_line"],
                "end_line": op["end_line"],
                "new_lines": list(op["new_lines"]),
            })
        return out

    if all(authoring_flags):
        return authoring_to_machine_ops(ops)

    raise EditContractError(
        "operations list mixes authoring and machine schemas, or is ambiguous; "
        f"got {list(ops)!r}"
    )


def proposal_ops_to_machine(
    op: Mapping[str, Any],
) -> List[dict]:
    """Extract machine ops from a ChangeProposal operation entry.

    ChangeProposal.operations entries are authoring-shaped for modify:
      { type/operation_type, target_files, start_line, end_line, new_text, ... }
    or nested:
      { ..., operations: [ authoring or machine ops ] }
    """
    nested = op.get("operations")
    if isinstance(nested, list) and nested:
        return ensure_machine_ops(nested)

    # Flat authoring fields on the proposal op itself
    if "start_line" in op and "end_line" in op and (
        "new_text" in op or "new_lines" in op
    ):
        flat = {
            "type": op.get("edit_type")
            or (op.get("type") if op.get("type") in AUTHORING_TYPES else None)
            or "replace",
            "start_line": op["start_line"],
            "end_line": op["end_line"],
        }
        if "new_lines" in op and "new_text" not in op:
            flat["new_lines"] = op["new_lines"]
            return ensure_machine_ops([flat])
        flat["new_text"] = op.get("new_text", "")
        if flat["type"] not in AUTHORING_TYPES:
            flat["type"] = "replace"
        return authoring_to_machine_ops([flat])

    raise EditContractError(
        f"modify proposal op has no authoring/machine edit fields: {op!r}"
    )
