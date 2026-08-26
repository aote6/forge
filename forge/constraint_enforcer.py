"""constraint_enforcer — hard/advisory constraint checks for subagent tool calls.

Consumes pure-data maps only:
  - forge.tool_action_map.TOOL_ACTION_MAP_BY_NAME
  - forge.command_class_prefixes.COMMAND_CLASS_PREFIXES / COMMAND_CLASS_UNKNOWN

Judgment order (fixed):
  1. Unregistered tool → deny (always hard)
  2. not_allowed blacklist (action and/or tool_name)
  3. scope.paths whitelist (path extraction + __UNPARSEABLE_PATH__ rule)
  4. command_class allowlist (prefix match; unknown denied when constraint present)

Enforcement policy:
  - Main AI does NOT declare enforcement level as binding authority.
  - This module decides what it can hard-enforce from observable facts
    (mapped action, extractable path, resolvable command_class).
  - Constraint entries may carry level "machine" | "advisory".
    * machine  → hard deny when the check applies
    * advisory → record as advisory violation, still allow execution
  - If level is omitted, default is "machine" for checks this layer can evaluate.

apply_patch path_field is "__UNPARSEABLE_PATH__":
  - Any non-empty scope.paths → deny (cannot prove path is in scope)
  - Empty / absent scope.paths → allow (no path constraint to violate)

command_class:
  - Longer prefixes checked before shorter ones.
  - Unknown class + command_class constraint present → deny (machine) or advisory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from forge.command_class_prefixes import (
    COMMAND_CLASS_PREFIXES,
    COMMAND_CLASS_UNKNOWN,
)
from forge.tool_action_map import TOOL_ACTION_MAP_BY_NAME

# Sentinel matching tool_action_map for tools whose path cannot be parsed.
UNPARSEABLE_PATH = "__UNPARSEABLE_PATH__"

LEVEL_MACHINE = "machine"
LEVEL_ADVISORY = "advisory"
_VALID_LEVELS = frozenset({LEVEL_MACHINE, LEVEL_ADVISORY})


@dataclass(frozen=True)
class ConstraintDecision:
    """Result of one enforce() call.

    allowed=False means the call must not execute.
    advisory_violations lists soft constraint hits that did not block.
    """

    allowed: bool
    reason: str = ""
    advisory_violations: tuple[str, ...] = ()
    action: str = ""
    tool_name: str = ""
    extracted_paths: tuple[str, ...] = ()
    command_class: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "advisory_violations": list(self.advisory_violations),
            "action": self.action,
            "tool_name": self.tool_name,
            "extracted_paths": list(self.extracted_paths),
            "command_class": self.command_class,
        }


@dataclass(frozen=True)
class NormalizedConstraints:
    """Internal normalized view of a constraints dict.

    not_allowed: set of action names and/or tool_names to block.
    scope_paths: whitelist path prefixes (empty = no path constraint).
    command_classes: allowlist of command_class strings; None = no constraint.
    *_level: machine | advisory for each axis.
    """

    not_allowed: frozenset[str] = field(default_factory=frozenset)
    not_allowed_level: str = LEVEL_MACHINE
    scope_paths: tuple[str, ...] = ()
    scope_paths_level: str = LEVEL_MACHINE
    command_classes: frozenset[str] | None = None
    command_classes_level: str = LEVEL_MACHINE


def _normalize_level(raw: Any) -> str:
    if isinstance(raw, str) and raw.strip().lower() in _VALID_LEVELS:
        return raw.strip().lower()
    return LEVEL_MACHINE


def normalize_constraints(raw: dict[str, Any] | None) -> NormalizedConstraints:
    """Normalize a free-form constraints dict into NormalizedConstraints.

    Accepted shapes (tolerant):
      {
        "not_allowed": ["write", "delete"] | {"items": [...], "level": "machine"},
        "scope": {"paths": ["src/"], "level": "advisory"} | {"paths": [...]},
        "command_class": ["test", "vcs_read"] | {"items": [...], "level": "..."},
      }

    Unknown keys are ignored. Empty / None → no constraints.
    """
    if not raw or not isinstance(raw, dict):
        return NormalizedConstraints()

    # --- not_allowed ---
    na_raw = raw.get("not_allowed")
    na_items: list[str] = []
    na_level = LEVEL_MACHINE
    if isinstance(na_raw, dict):
        items = na_raw.get("items") or na_raw.get("actions") or na_raw.get("values") or []
        if isinstance(items, (list, tuple, set)):
            na_items = [str(x).strip() for x in items if str(x).strip()]
        na_level = _normalize_level(na_raw.get("level"))
    elif isinstance(na_raw, (list, tuple, set)):
        na_items = [str(x).strip() for x in na_raw if str(x).strip()]
        # top-level level override if present
        if "not_allowed_level" in raw:
            na_level = _normalize_level(raw.get("not_allowed_level"))

    # --- scope.paths ---
    scope_raw = raw.get("scope")
    paths: list[str] = []
    paths_level = LEVEL_MACHINE
    if isinstance(scope_raw, dict):
        p = scope_raw.get("paths")
        if isinstance(p, (list, tuple, set)):
            paths = [str(x).strip() for x in p if str(x).strip()]
        paths_level = _normalize_level(scope_raw.get("level") or scope_raw.get("paths_level"))
    # also accept top-level "paths" for convenience
    if not paths and isinstance(raw.get("paths"), (list, tuple, set)):
        paths = [str(x).strip() for x in raw["paths"] if str(x).strip()]
        if "scope_paths_level" in raw:
            paths_level = _normalize_level(raw.get("scope_paths_level"))

    # --- command_class ---
    cc_raw = raw.get("command_class")
    if cc_raw is None:
        cc_raw = raw.get("command_classes")
    cc_items: frozenset[str] | None = None
    cc_level = LEVEL_MACHINE
    if isinstance(cc_raw, dict):
        items = cc_raw.get("items") or cc_raw.get("values") or []
        if isinstance(items, (list, tuple, set)):
            cc_items = frozenset(str(x).strip() for x in items if str(x).strip())
        else:
            cc_items = frozenset()
        cc_level = _normalize_level(cc_raw.get("level"))
    elif isinstance(cc_raw, (list, tuple, set)):
        cc_items = frozenset(str(x).strip() for x in cc_raw if str(x).strip())
        if "command_class_level" in raw:
            cc_level = _normalize_level(raw.get("command_class_level"))

    return NormalizedConstraints(
        not_allowed=frozenset(na_items),
        not_allowed_level=na_level,
        scope_paths=tuple(paths),
        scope_paths_level=paths_level,
        command_classes=cc_items,
        command_classes_level=cc_level,
    )


def _dig(obj: Any, dotted: str) -> Any:
    """Walk a dotted path; supports one trailing '[]' for list-of-dicts field."""
    if not dotted or obj is None:
        return None
    # "input.edits[].path" → walk input.edits, then collect .path from each
    if "[]." in dotted:
        before, after = dotted.split("[].", 1)
        base = _dig(obj, before) if before else obj
        if not isinstance(base, list):
            return None
        out: list[Any] = []
        for item in base:
            v = _dig(item, after) if after else item
            if v is not None:
                if isinstance(v, list):
                    out.extend(v)
                else:
                    out.append(v)
        return out
    cur = obj
    for part in dotted.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def extract_paths(path_field: str, tool_input: dict[str, Any]) -> list[str]:
    """Extract path strings from tool input according to path_field.

    Returns:
      - [UNPARSEABLE_PATH] when path_field is the sentinel
      - list of non-empty path strings
      - empty list when path_field is empty or nothing extractable
    """
    if not path_field:
        return []
    if path_field == UNPARSEABLE_PATH:
        return [UNPARSEABLE_PATH]

    # path_field is relative to the full call envelope: usually "input.xxx"
    # Callers pass tool arguments as the `input` dict itself in many places;
    # support both { "input": {...} } and bare argument dict.
    envelope: dict[str, Any]
    if isinstance(tool_input, dict) and "input" in tool_input and path_field.startswith(
        "input."
    ):
        envelope = tool_input
    else:
        envelope = {"input": tool_input}

    value = _dig(envelope, path_field)
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, list):
        out: list[str] = []
        for v in value:
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        return out
    return []


def path_in_scope(path: str, scope_paths: tuple[str, ...] | list[str]) -> bool:
    """True if path is under any scope prefix (prefix match, not glob).

    Empty path is never in scope. A path is in scope only when:
      - path equals the scope entry exactly, or
      - path starts with scope_entry + "/"

    This prevents "src2/main.py" from matching scope "src".
    """
    if not path or path == UNPARSEABLE_PATH:
        return False
    p = path.replace("\\", "/")
    for scope in scope_paths:
        s = (scope or "").replace("\\", "/").strip()
        if not s:
            continue
        normalized = s.rstrip("/")
        if p == normalized or p.startswith(normalized + "/"):
            return True
    return False


def resolve_command_class(cmd: str) -> str:
    """Map a shell command string to a command_class via prefix whitelist.

    Longer prefixes are checked first. No match → COMMAND_CLASS_UNKNOWN.
    """
    text = (cmd or "").strip()
    if not text:
        return COMMAND_CLASS_UNKNOWN
    # Sort by prefix length descending so longer matches win.
    for prefix in sorted(COMMAND_CLASS_PREFIXES.keys(), key=len, reverse=True):
        if text == prefix or text.startswith(prefix + " ") or text.startswith(prefix + "\t"):
            return COMMAND_CLASS_PREFIXES[prefix]
        # also allow exact prefix as whole first token sequence
        if text.startswith(prefix):
            # avoid matching "git" against "gitignore" style — require boundary
            rest = text[len(prefix) :]
            if rest == "" or rest[0].isspace():
                return COMMAND_CLASS_PREFIXES[prefix]
    return COMMAND_CLASS_UNKNOWN


def resolve_command_class_from_rule(
    command_class_rule: str, tool_input: dict[str, Any]
) -> str:
    """Resolve command_class from a tool_action_map command_class_rule.

    Rules:
      ""                  → no class (empty string)
      "fixed:<class>"     → that class
      "prefix_match:<dotted path to cmd string>" → prefix whitelist on that field
    """
    rule = (command_class_rule or "").strip()
    if not rule:
        return ""
    if rule.startswith("fixed:"):
        return rule[len("fixed:") :].strip() or COMMAND_CLASS_UNKNOWN
    if rule.startswith("prefix_match:"):
        field_path = rule[len("prefix_match:") :].strip()
        envelope: dict[str, Any]
        if isinstance(tool_input, dict) and "input" in tool_input and field_path.startswith(
            "input."
        ):
            envelope = tool_input
        else:
            envelope = {"input": tool_input}
        cmd_val = _dig(envelope, field_path)
        if not isinstance(cmd_val, str):
            return COMMAND_CLASS_UNKNOWN
        return resolve_command_class(cmd_val)
    return COMMAND_CLASS_UNKNOWN


def _deny(
    reason: str,
    *,
    action: str = "",
    tool_name: str = "",
    extracted_paths: list[str] | None = None,
    command_class: str = "",
    advisory: list[str] | None = None,
) -> ConstraintDecision:
    return ConstraintDecision(
        allowed=False,
        reason=reason,
        advisory_violations=tuple(advisory or ()),
        action=action,
        tool_name=tool_name,
        extracted_paths=tuple(extracted_paths or ()),
        command_class=command_class,
    )


def _allow(
    *,
    action: str = "",
    tool_name: str = "",
    extracted_paths: list[str] | None = None,
    command_class: str = "",
    advisory: list[str] | None = None,
) -> ConstraintDecision:
    return ConstraintDecision(
        allowed=True,
        reason="",
        advisory_violations=tuple(advisory or ()),
        action=action,
        tool_name=tool_name,
        extracted_paths=tuple(extracted_paths or ()),
        command_class=command_class,
    )


def enforce(
    tool_name: str,
    tool_input: dict[str, Any] | None,
    constraints: dict[str, Any] | None,
) -> ConstraintDecision:
    """Decide whether a tool call is allowed under the given constraints.

    Parameters
    ----------
    tool_name:
        Exact tool name as registered in TOOL_ACTION_MAP.
    tool_input:
        Tool arguments dict (the kwargs that will be passed to the tool).
        May also be an envelope {"input": {...}} — both are accepted.
    constraints:
        Free-form constraints dict; see normalize_constraints().

    Returns
    -------
    ConstraintDecision
        allowed=False → caller must not execute the tool.
    """
    tool_name = (tool_name or "").strip()
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    nc = normalize_constraints(constraints)
    advisory: list[str] = []

    # ── 1. Unregistered tool → always hard deny ─────────────────────
    row = TOOL_ACTION_MAP_BY_NAME.get(tool_name)
    if row is None:
        return _deny(
            f"unregistered tool: {tool_name!r} (default deny)",
            tool_name=tool_name,
        )

    action = row.get("action") or ""
    path_field = row.get("path_field") or ""
    command_class_rule = row.get("command_class_rule") or ""

    # ── 2. not_allowed blacklist (action, then tool_name) ───────────
    if nc.not_allowed:
        hit: str | None = None
        if action and action in nc.not_allowed:
            hit = f"action {action!r}"
        elif tool_name in nc.not_allowed:
            hit = f"tool {tool_name!r}"
        if hit:
            msg = f"not_allowed: {hit} is blacklisted"
            if nc.not_allowed_level == LEVEL_ADVISORY:
                advisory.append(msg)
            else:
                return _deny(
                    msg,
                    action=action,
                    tool_name=tool_name,
                    advisory=advisory,
                )

    # ── 3. scope.paths whitelist ────────────────────────────────────
    extracted = extract_paths(path_field, tool_input)

    if nc.scope_paths:
        if path_field == UNPARSEABLE_PATH or (
            extracted == [UNPARSEABLE_PATH]
        ):
            # Cannot prove the path is in scope → reject when path constraint exists.
            msg = (
                f"scope.paths: tool {tool_name!r} has unparseable path; "
                "denied because path constraints are present"
            )
            if nc.scope_paths_level == LEVEL_ADVISORY:
                advisory.append(msg)
            else:
                return _deny(
                    msg,
                    action=action,
                    tool_name=tool_name,
                    extracted_paths=extracted,
                    advisory=advisory,
                )
        elif extracted:
            # Every extracted path must be in scope.
            out_of_scope = [p for p in extracted if not path_in_scope(p, nc.scope_paths)]
            if out_of_scope:
                msg = (
                    f"scope.paths: path(s) out of scope: {out_of_scope!r} "
                    f"(allowed prefixes: {list(nc.scope_paths)!r})"
                )
                if nc.scope_paths_level == LEVEL_ADVISORY:
                    advisory.append(msg)
                else:
                    return _deny(
                        msg,
                        action=action,
                        tool_name=tool_name,
                        extracted_paths=extracted,
                        advisory=advisory,
                    )
        # extracted empty + path_field non-empty + scope present:
        # nothing to check (missing optional path arg) → allow.
        # Tools with empty path_field are path-agnostic → allow.

    # ── 4. command_class allowlist ──────────────────────────────────
    resolved_cc = ""
    if command_class_rule:
        resolved_cc = resolve_command_class_from_rule(command_class_rule, tool_input)

    if nc.command_classes is not None:
        # Constraint present (possibly empty set).
        if not resolved_cc or resolved_cc == COMMAND_CLASS_UNKNOWN:
            msg = (
                f"command_class: unknown class for tool {tool_name!r}; "
                "denied because command_class constraint is present"
            )
            if nc.command_classes_level == LEVEL_ADVISORY:
                advisory.append(msg)
            else:
                return _deny(
                    msg,
                    action=action,
                    tool_name=tool_name,
                    extracted_paths=extracted,
                    command_class=resolved_cc or COMMAND_CLASS_UNKNOWN,
                    advisory=advisory,
                )
        elif resolved_cc not in nc.command_classes:
            msg = (
                f"command_class: {resolved_cc!r} not in allowlist "
                f"{sorted(nc.command_classes)!r}"
            )
            if nc.command_classes_level == LEVEL_ADVISORY:
                advisory.append(msg)
            else:
                return _deny(
                    msg,
                    action=action,
                    tool_name=tool_name,
                    extracted_paths=extracted,
                    command_class=resolved_cc,
                    advisory=advisory,
                )

    return _allow(
        action=action,
        tool_name=tool_name,
        extracted_paths=extracted,
        command_class=resolved_cc,
        advisory=advisory,
    )
