"""Agent ABI v1 — task/result contract and AgentResult assembly.

Status enum (v1.3, exhaustive):
  - done
  - blocked
  - need_decision

Sub-agent output is only a *candidate*. Final status is decided here by
machine rules; the model never writes AgentResult.status.

---------------------------------------------------------------------------
done_when — v1 machine proxy (NOT full semantic evaluation)
---------------------------------------------------------------------------
True natural-language ``done_when`` is a task-level completion predicate.
v1 does **not** interpret that text. Instead the assembler uses a fixed
proxy:

    done_when_satisfied := stop_when_met AND len(verified_evidence) >= 1

This is an engineering stand-in so the executor can hard-gate ``status=done``
without NLP. It is **not** equivalent to evaluating the human-readable
``AgentTask.done_when`` string. Future ABI versions may replace or extend
this proxy with structured checks; until then every call site must treat
``done_when_satisfied`` as the v1 proxy only.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Sequence

# Agent ABI v1.3 — only these three values may appear on AgentResult.status.
STATUS_DONE = "done"
STATUS_BLOCKED = "blocked"
STATUS_NEED_DECISION = "need_decision"
VALID_STATUSES = frozenset({STATUS_DONE, STATUS_BLOCKED, STATUS_NEED_DECISION})

_EMPTY_MARKS = frozenset({"", "(无)", "(none)", "无", "n/a", "none"})

# EVIDENCE line: optional tool_call_id=... and path=... then free claim text.
_EVIDENCE_TC_RE = re.compile(
    r"tool_call_id\s*=\s*([A-Za-z0-9_\-]+)", re.IGNORECASE
)
_EVIDENCE_PATH_RE = re.compile(
    r"path\s*=\s*(\S+)", re.IGNORECASE
)
_SECTION_RE = re.compile(
    r"^\s*(CONCLUSION|EVIDENCE|UNCERTAIN|NEXT)\s*:\s*(.*)$", re.IGNORECASE
)


@dataclass(frozen=True)
class Evidence:
    """One machine-verifiable evidence item bound to a real tool call."""

    tool_call_id: str
    claim: str = ""
    path: str | None = None
    quote: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentTask:
    """Main-agent → executor task contract."""

    goal: str
    subtask_id: str | None = None
    constraints: dict[str, Any] = field(default_factory=dict)
    # Human-readable predicates injected into the sub-agent prompt.
    # Runtime termination still uses the explicit STOP_WHEN: met|not_met line.
    # done_when text is NOT NLP-evaluated; see module docstring for the v1 proxy.
    stop_when: str = ""
    done_when: str = ""
    max_steps: int = 15

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "subtask_id": self.subtask_id,
            "constraints": dict(self.constraints or {}),
            "stop_when": self.stop_when,
            "done_when": self.done_when,
            "max_steps": self.max_steps,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AgentTask":
        """Symmetric to to_dict for Durable Pause resume reconstruction."""
        if not isinstance(data, dict):
            data = {}
        constraints = data.get("constraints")
        if not isinstance(constraints, dict):
            constraints = {}
        try:
            max_steps = int(data.get("max_steps") or 15)
        except (TypeError, ValueError):
            max_steps = 15
        if max_steps < 1:
            max_steps = 15
        sid = data.get("subtask_id")
        if sid is not None:
            sid = str(sid).strip() or None
        return cls(
            goal=str(data.get("goal") or ""),
            subtask_id=sid,
            constraints=dict(constraints),
            stop_when=str(data.get("stop_when") or ""),
            done_when=str(data.get("done_when") or ""),
            max_steps=max_steps,
        )


@dataclass(frozen=True)
class AgentResult:
    """Executor → main-agent result. status is machine-authored only."""

    subtask_id: str
    status: str  # done | blocked | need_decision
    conclusion: str
    evidence: tuple[Evidence, ...]
    uncertain: str
    next: str
    stop_when_met: bool
    status_reason: str
    raw_conclusion: str = ""

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(
                f"AgentResult.status must be one of {sorted(VALID_STATUSES)}, "
                f"got {self.status!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "status": self.status,
            "conclusion": self.conclusion,
            "evidence": [e.to_dict() for e in self.evidence],
            "uncertain": self.uncertain,
            "next": self.next,
            "stop_when_met": self.stop_when_met,
            "status_reason": self.status_reason,
            "raw_conclusion": self.raw_conclusion,
        }


@dataclass
class CandidateResult:
    """Sub-agent candidate only. Never treated as final AgentResult."""

    conclusion: str = ""
    evidence_items: list[dict[str, Any]] = field(default_factory=list)
    uncertain: str = ""
    next: str = ""
    stop_when_met: bool = False
    # Exit kind for assembler: "stop_when" | "no_tools" | "max_steps" | "error"
    exit_kind: str = "no_tools"
    error_message: str = ""


def _section_body(structured: str) -> dict[str, str]:
    """Split a structure_conclusion blob into section name → body text."""
    content: dict[str, list[str]] = {
        "CONCLUSION": [],
        "EVIDENCE": [],
        "UNCERTAIN": [],
        "NEXT": [],
    }
    current: str | None = None
    for raw_line in (structured or "").splitlines():
        m = _SECTION_RE.match(raw_line)
        if m:
            current = m.group(1).upper()
            rest = m.group(2).strip()
            if rest:
                content[current].append(rest)
            continue
        if current is not None:
            content[current].append(raw_line.rstrip())
    return {
        name: "\n".join(ln for ln in lines if ln.strip()).strip()
        for name, lines in content.items()
    }


def _parse_evidence_line(line: str) -> dict[str, Any] | None:
    text = (line or "").strip()
    if not text or text in _EMPTY_MARKS:
        return None
    if text.startswith("-"):
        text = text[1:].strip()
    if not text or text in _EMPTY_MARKS:
        return None

    tc_id = None
    path = None
    m_tc = _EVIDENCE_TC_RE.search(text)
    if m_tc:
        tc_id = m_tc.group(1)
    m_path = _EVIDENCE_PATH_RE.search(text)
    if m_path:
        path = m_path.group(1)

    claim = text
    # Strip recognized key=value tokens from claim for readability.
    if m_tc:
        claim = _EVIDENCE_TC_RE.sub("", claim)
    if m_path:
        claim = _EVIDENCE_PATH_RE.sub("", claim)
    claim = re.sub(r"\s+", " ", claim).strip(" -\t")

    return {
        "tool_call_id": tc_id or "",
        "path": path,
        "claim": claim,
        "quote": None,
    }


def parse_candidate(
    structured_text: str,
    *,
    stop_when_met: bool,
    exit_kind: str,
    error_message: str = "",
) -> CandidateResult:
    """Build a CandidateResult from structure_conclusion output + loop flags."""
    sections = _section_body(structured_text)
    evidence_items: list[dict[str, Any]] = []
    for ln in (sections.get("EVIDENCE") or "").splitlines():
        item = _parse_evidence_line(ln)
        if item is not None:
            evidence_items.append(item)

    def _clean(s: str) -> str:
        t = (s or "").strip()
        return "" if t in _EMPTY_MARKS else t

    return CandidateResult(
        conclusion=_clean(sections.get("CONCLUSION", "")),
        evidence_items=evidence_items,
        uncertain=_clean(sections.get("UNCERTAIN", "")),
        next=_clean(sections.get("NEXT", "")),
        stop_when_met=bool(stop_when_met),
        exit_kind=exit_kind,
        error_message=error_message or "",
    )


def _record_index(
    records: Sequence[Any],
) -> dict[str, Any]:
    """Map tool_call_id → record (dataclass or dict)."""
    out: dict[str, Any] = {}
    for r in records:
        if hasattr(r, "tool_call_id"):
            out[str(r.tool_call_id)] = r
        elif isinstance(r, dict) and r.get("tool_call_id"):
            out[str(r["tool_call_id"])] = r
    return out


def _record_subtask_id(rec: Any) -> str:
    if hasattr(rec, "subtask_id"):
        return str(rec.subtask_id)
    if isinstance(rec, dict):
        return str(rec.get("subtask_id") or "")
    return ""


def verify_evidence(
    items: Iterable[dict[str, Any]],
    records: Sequence[Any],
    subtask_id: str,
) -> list[Evidence]:
    """Keep only evidence whose tool_call_id exists in records for this subtask."""
    by_id = _record_index(records)
    verified: list[Evidence] = []
    seen: set[str] = set()
    for item in items:
        tc_id = (item.get("tool_call_id") or "").strip()
        if not tc_id or tc_id in seen:
            continue
        rec = by_id.get(tc_id)
        if rec is None:
            continue
        # Main-agent records never count as subtask Evidence.
        actor = ""
        if hasattr(rec, "actor"):
            actor = str(getattr(rec, "actor") or "")
        elif isinstance(rec, dict):
            actor = str(rec.get("actor") or "")
        if actor == "main":
            continue
        rec_sid = _record_subtask_id(rec)
        if not rec_sid or rec_sid != subtask_id:
            continue
        seen.add(tc_id)
        verified.append(
            Evidence(
                tool_call_id=tc_id,
                claim=str(item.get("claim") or ""),
                path=item.get("path"),
                quote=item.get("quote"),
            )
        )
    return verified


def done_when_satisfied_v1(stop_when_met: bool, verified: Sequence[Evidence]) -> bool:
    """v1 machine proxy for done_when — NOT semantic evaluation of done_when text.

    done_when_satisfied := stop_when_met AND len(verified) >= 1

    See module docstring. Callers must not treat this as proof that the
    natural-language AgentTask.done_when predicate holds.
    """
    return bool(stop_when_met) and len(verified) >= 1


def assemble_agent_result(
    task: AgentTask,
    candidate: CandidateResult,
    records: Sequence[Any],
    *,
    subtask_id: str,
) -> AgentResult:
    """Assemble final AgentResult. Model never chooses status."""
    verified = verify_evidence(candidate.evidence_items, records, subtask_id)
    stop_met = bool(candidate.stop_when_met)
    exit_kind = candidate.exit_kind or "no_tools"

    # --- draft status from exit path (still provisional) ---
    # Phase 2 execution-plane exits (not ABI status values — still blocked only)
    if exit_kind == "confirmation_unavailable":
        status = STATUS_BLOCKED
        reason = candidate.error_message or "confirmation_unavailable"
    elif exit_kind == "user_denied_write":
        status = STATUS_BLOCKED
        reason = candidate.error_message or "user_denied_write"
    elif exit_kind == "unauthorized_world_change":
        status = STATUS_BLOCKED
        reason = candidate.error_message or "unauthorized_world_change"
    elif exit_kind == "error":
        status = STATUS_BLOCKED
        reason = f"loop error: {candidate.error_message or 'unknown'}"
    elif exit_kind == "max_steps":
        status = STATUS_BLOCKED
        reason = "max_steps reached without stop_when met"
    elif stop_met:
        # stop_when met → candidate for done; apply v1 done_when proxy
        if done_when_satisfied_v1(stop_met, verified):
            status = STATUS_DONE
            reason = (
                "stop_when met and v1 done_when proxy satisfied "
                "(stop_when_met && evidence non-empty)"
            )
        else:
            status = STATUS_BLOCKED
            reason = (
                "stop_when met but v1 done_when proxy not satisfied "
                "(require stop_when_met and non-empty verified evidence)"
            )
    else:
        # Natural end without stop_when: parent must decide.
        status = STATUS_NEED_DECISION
        reason = "loop ended without stop_when met; main agent must decide"

    # Hard rules: done requires verified evidence (also covers strip-to-empty).
    if status == STATUS_DONE and not verified:
        status = STATUS_BLOCKED
        reason = "status=done rejected: verified evidence is empty"

    conclusion = candidate.conclusion or ""
    if not conclusion and status == STATUS_BLOCKED:
        conclusion = "(subagent: no conclusion)"

    return AgentResult(
        subtask_id=subtask_id,
        status=status,
        conclusion=conclusion,
        evidence=tuple(verified),
        uncertain=candidate.uncertain or "",
        next=candidate.next or "",
        stop_when_met=stop_met,
        status_reason=reason,
        raw_conclusion="",  # filled by caller if desired
    )


def format_agent_result_for_parent(result: AgentResult) -> str:
    """Render AgentResult as main-agent readable text (spawn_subagent display)."""
    lines: list[str] = [
        f"RESULT: subagent_{result.status}",
        f"status: {result.status}",
        f"status_reason: {result.status_reason}",
        f"subtask_id: {result.subtask_id}",
        f"stop_when_met: {result.stop_when_met}",
        "",
        "CONCLUSION:",
        result.conclusion or "(无)",
        "",
        "EVIDENCE:",
    ]
    if result.evidence:
        for ev in result.evidence:
            bits = [f"tool_call_id={ev.tool_call_id}"]
            if ev.path:
                bits.append(f"path={ev.path}")
            if ev.claim:
                bits.append(ev.claim)
            lines.append("- " + " ".join(bits))
    else:
        lines.append("(无)")
    lines.extend(
        [
            "",
            "UNCERTAIN:",
            result.uncertain or "(无)",
            "",
            "NEXT:",
            result.next or "(无)",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def build_subagent_user_message(task: AgentTask) -> str:
    """User message for the sub-agent loop: goal + optional predicates."""
    parts = [task.goal.strip()]
    if task.stop_when.strip():
        parts.append(f"\nstop_when (human predicate): {task.stop_when.strip()}")
    if task.done_when.strip():
        parts.append(f"\ndone_when (human predicate): {task.done_when.strip()}")
    parts.append(
        "\nWhen citing evidence, each EVIDENCE line must include "
        "tool_call_id=<id> from a prior tool result in this subtask."
    )
    return "\n".join(parts).strip() + "\n"


def _coerce_agent_result(agent_result: AgentResult | dict[str, Any]) -> AgentResult:
    """Accept AgentResult or its to_dict() form."""
    if isinstance(agent_result, AgentResult):
        return agent_result
    if not isinstance(agent_result, dict):
        raise TypeError(
            f"agent_result must be AgentResult or dict, got {type(agent_result).__name__}"
        )
    raw_ev = agent_result.get("evidence") or []
    evidence: list[Evidence] = []
    for item in raw_ev:
        if isinstance(item, Evidence):
            evidence.append(item)
        elif isinstance(item, dict):
            tc = str(item.get("tool_call_id") or "").strip()
            if not tc:
                continue
            evidence.append(
                Evidence(
                    tool_call_id=tc,
                    claim=str(item.get("claim") or ""),
                    path=item.get("path"),
                    quote=item.get("quote"),
                )
            )
    return AgentResult(
        subtask_id=str(agent_result.get("subtask_id") or ""),
        status=str(agent_result.get("status") or STATUS_BLOCKED),
        conclusion=str(agent_result.get("conclusion") or ""),
        evidence=tuple(evidence),
        uncertain=str(agent_result.get("uncertain") or ""),
        next=str(agent_result.get("next") or ""),
        stop_when_met=bool(agent_result.get("stop_when_met")),
        status_reason=str(agent_result.get("status_reason") or ""),
        raw_conclusion=str(agent_result.get("raw_conclusion") or ""),
    )


def lookup_evidence_records(
    project_root: str | Any,
    agent_result: AgentResult | dict[str, Any],
) -> list[dict[str, Any]]:
    """Independently resolve each evidence tool_call_id via ToolCallRecord log.

    Returns a list of dicts:
      {
        "tool_call_id": str,
        "ok": bool,
        "record": dict|None,   # raw get_record result; never includes sub-agent claim
        "evidence": dict,      # original evidence fields (for audit only)
      }

    Does not trust conclusion/claim as fact — only whether the id exists on disk.
    """
    from forge.tool_call_record import get_record

    result = _coerce_agent_result(agent_result)
    out: list[dict[str, Any]] = []
    for ev in result.evidence:
        tc_id = (ev.tool_call_id or "").strip()
        rec = get_record(project_root, tc_id) if tc_id else None
        ok = rec is not None
        if ok:
            if str(rec.get("actor") or "") == "main":
                ok = False
            elif result.subtask_id:
                rec_sid = str(rec.get("subtask_id") or "").strip()
                if not rec_sid or rec_sid != str(result.subtask_id):
                    ok = False
        out.append(
            {
                "tool_call_id": tc_id,
                "ok": ok,
                "record": rec if ok else None,
                "evidence": ev.to_dict(),
            }
        )
    return out


def precheck_agent_result(
    project_root: str | Any,
    agent_result: AgentResult | dict[str, Any],
) -> AgentResult:
    """Machine precheck at the spawn_subagent return boundary.

    Re-validates evidence against on-disk ToolCallRecord (not in-memory assembly
    records). If status is done but no independently verifiable evidence remains,
    demote to blocked. Never elevates status to done.

    This does NOT evaluate conclusion semantics — that is the main agent's job
    after verify_tool_call lookups.
    """
    result = _coerce_agent_result(agent_result)
    looked = lookup_evidence_records(project_root, result)
    verified: list[Evidence] = []
    rejected: list[str] = []
    for row in looked:
        if row["ok"]:
            ev = row["evidence"]
            verified.append(
                Evidence(
                    tool_call_id=row["tool_call_id"],
                    claim=str(ev.get("claim") or ""),
                    path=ev.get("path"),
                    quote=ev.get("quote"),
                )
            )
        else:
            if row["tool_call_id"]:
                rejected.append(row["tool_call_id"])

    status = result.status
    reason = result.status_reason
    if rejected:
        note = f"acceptance_precheck: unverifiable tool_call_id(s) stripped: {rejected}"
        reason = f"{reason}; {note}" if reason else note

    if status == STATUS_DONE and not verified:
        status = STATUS_BLOCKED
        reason = (
            "acceptance_precheck: status=done demoted — "
            "no independently verifiable evidence on disk"
        )

    return AgentResult(
        subtask_id=result.subtask_id,
        status=status,
        conclusion=result.conclusion,
        evidence=tuple(verified),
        uncertain=result.uncertain,
        next=result.next,
        stop_when_met=result.stop_when_met,
        status_reason=reason,
        raw_conclusion=result.raw_conclusion,
    )
