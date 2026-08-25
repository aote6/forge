"""Project review: closed fact retrieval for work history / status / tests.

Authority model (see docs/PROJECT_REVIEW_CONTRACT.md):
  FACT     — Git worktree/commits; persisted last_test_result when present
  EVIDENCE — STATUS.md narrative (never auto-promoted to Fact)
  CONTEXT  — session_changes / project_memory / optional conversation
  CONFLICTS — unresolved pairwise disagreements (no silent merge)

This tool NEVER generates INFERENCE and NEVER runs pytest.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from forge.adapters.base import ToolResult
from forge.tools.display import format_block


_LAST_TEST_NAME = "last_test_result.json"
_STATUS_DATE_RE = re.compile(
    r"(?:^|\n)##?\s+[^\n]*?(20\d{2}-\d{2}-\d{2})[^\n]*\n",
    re.MULTILINE,
)
_PASS_CLAIM_RE = re.compile(
    r"(全部通过|全量\s*\d+\s*passed|\d+\s*passed|测试通过|全绿)",
    re.IGNORECASE,
)
_CLEAN_CLAIM_RE = re.compile(
    r"(工作树干净|无未提交|已提交|worktree\s*clean)",
    re.IGNORECASE,
)
_COMMIT_HASH_RE = re.compile(r"\b([0-9a-f]{7,40})\b")


def last_test_path(project_root: str) -> Path:
    return Path(project_root) / ".forge" / _LAST_TEST_NAME


def load_last_test_result(project_root: str) -> dict[str, Any] | None:
    p = last_test_path(project_root)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:
        print(f"[project_review] load_last_test_result failed: {e}", file=sys.stderr)
        return None


def save_last_test_result(project_root: str, data: dict[str, Any]) -> None:
    """Persist a completed test run (called from test tools). Does not invent results."""
    p = last_test_path(project_root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(data)
        payload.setdefault("timestamp", datetime.now().isoformat(timespec="seconds"))
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[project_review] save_last_test_result failed: {e}", file=sys.stderr)


def _run_git(project_root: str, args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except Exception as e:
        return 1, "", str(e)


def _resolve_since(since: str | None) -> str:
    if since and str(since).strip():
        s = str(since).strip()
        if s.lower() in ("today", "今日"):
            return date.today().isoformat()
        return s
    return date.today().isoformat()


def _git_fact(project_root: str, since: str, until: str | None) -> dict[str, Any]:
    code_b, out_b, err_b = _run_git(project_root, ["branch", "--show-current"])
    branch = (out_b.strip() if code_b == 0 else "") or "HEAD (detached)"

    code_h, out_h, _ = _run_git(project_root, ["rev-parse", "--short", "HEAD"])
    head = out_h.strip() if code_h == 0 else None

    code_l, out_l, _ = _run_git(
        project_root,
        ["log", "-1", "--format=%h %ad %s", "--date=format:%Y-%m-%d %H:%M"],
    )
    last_commit_line = out_l.strip() if code_l == 0 else None

    log_args = [
        "log",
        f"--since={since}",
        "--format=%H%x09%h%x09%ad%x09%s",
        "--date=format:%Y-%m-%d %H:%M:%S",
    ]
    if until and str(until).strip():
        log_args.insert(2, f"--until={until}")
    code_log, out_log, err_log = _run_git(project_root, log_args)
    commits: list[dict[str, str]] = []
    if code_log == 0:
        for line in out_log.splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                commits.append(
                    {
                        "hash": parts[0],
                        "short": parts[1],
                        "timestamp": parts[2],
                        "subject": parts[3],
                    }
                )
            elif line.strip():
                commits.append(
                    {"hash": "", "short": "", "timestamp": "", "subject": line.strip()}
                )

    code_st, out_st, _ = _run_git(project_root, ["status", "--porcelain"])
    dirty_files: list[str] = []
    if code_st == 0:
        for line in out_st.splitlines():
            if not line.strip():
                continue
            path = line[3:] if len(line) > 3 else line.strip()
            dirty_files.append(path)

    git_ok = code_log == 0 or code_st == 0
    return {
        "source": "git",
        "authority": "fact",
        "available": git_ok,
        "branch": branch,
        "head": head,
        "last_commit": last_commit_line,
        "since": since,
        "until": until,
        "commits": commits,
        "commit_count": len(commits),
        "worktree_clean": len(dirty_files) == 0,
        "dirty_files": dirty_files,
        "errors": [e for e in (err_log, err_b) if e and e.strip()][:3],
    }


def _extract_status_sections(text: str, since: str, until: str | None) -> list[dict[str, Any]]:
    if not text:
        return []
    try:
        since_d = date.fromisoformat(since[:10])
    except ValueError:
        since_d = date.today()
    until_d: date | None = None
    if until:
        try:
            until_d = date.fromisoformat(str(until)[:10])
        except ValueError:
            until_d = None

    matches = list(_STATUS_DATE_RE.finditer(text))
    sections: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        d_str = m.group(1)
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        if d < since_d:
            continue
        if until_d is not None and d >= until_d:
            continue
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if len(body) > 4000:
            body = body[:4000] + "\n...(truncated)"
        sections.append(
            {
                "date": d_str,
                "heading": text[m.start() : m.end()].strip().lstrip("#").strip()[:200],
                "body": body,
            }
        )
    if not sections and since:
        key = since[:10]
        if key in text:
            idx = text.find(key)
            lo = max(0, text.rfind("\n##", 0, idx))
            hi = text.find("\n## ", idx + 1)
            if hi < 0:
                hi = min(len(text), idx + 2500)
            body = text[lo:hi].strip()
            if len(body) > 4000:
                body = body[:4000] + "\n...(truncated)"
            sections.append(
                {"date": key, "heading": f"(loose match for {key})", "body": body}
            )
    return sections


def _status_evidence(project_root: str, since: str, until: str | None) -> dict[str, Any]:
    path = Path(project_root) / "STATUS.md"
    if not path.is_file():
        return {
            "source": "STATUS.md",
            "authority": "narrative/evidence",
            "verified": False,
            "available": False,
            "sections": [],
            "raw_claims": {},
        }
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {
            "source": "STATUS.md",
            "authority": "narrative/evidence",
            "verified": False,
            "available": False,
            "error": str(e),
            "sections": [],
            "raw_claims": {},
        }
    sections = _extract_status_sections(text, since, until)
    joined = "\n".join(s["body"] for s in sections)
    claims = {
        "mentions_tests_passed": bool(_PASS_CLAIM_RE.search(joined)),
        "mentions_worktree_clean": bool(_CLEAN_CLAIM_RE.search(joined)),
        "mentioned_hashes": list(dict.fromkeys(_COMMIT_HASH_RE.findall(joined)))[:30],
    }
    return {
        "source": "STATUS.md",
        "authority": "narrative/evidence",
        "verified": False,
        "available": True,
        "sections": sections,
        "raw_claims": claims,
    }


def _test_fact(project_root: str) -> dict[str, Any]:
    data = load_last_test_result(project_root)
    if not data:
        return {
            "source": "last_test_result",
            "authority": "fact",
            "status": "unverified",
            "available": False,
            "detail": "No .forge/last_test_result.json; do not claim tests passed from STATUS alone.",
        }
    status = data.get("status") or (
        "passed"
        if data.get("failed", 1) == 0 and data.get("returncode", 1) == 0
        else "failed"
    )
    return {
        "source": "last_test_result",
        "authority": "fact",
        "status": status,
        "available": True,
        "command": data.get("command"),
        "timestamp": data.get("timestamp"),
        "passed": data.get("passed"),
        "failed": data.get("failed"),
        "returncode": data.get("returncode"),
        "target": data.get("target"),
    }


def _memory_context(project_root: str) -> dict[str, Any]:
    try:
        from forge.tools.project_memory import load_memory

        data = load_memory(project_root) or {}
    except Exception as e:
        data = {}
        print(f"[project_review] load_memory failed: {e}", file=sys.stderr)
    return {
        "source": "project_memory",
        "authority": "heuristic",
        "freshness": "unknown",
        "may_be_stale": True,
        "not_project_fact": True,
        "data": data,
    }


def _session_context() -> dict[str, Any]:
    try:
        from forge.tools.session_changes import list_changes

        items = list_changes()
    except Exception:
        items = []
    return {
        "source": "session_changes",
        "authority": "context/evidence",
        "scope": "current_session",
        "calendar_scope": False,
        "not_project_fact": True,
        "changes": items,
        "count": len(items),
    }


def _conversation_context(project_root: str, max_lines: int = 20) -> dict[str, Any]:
    path = Path(project_root) / ".forge" / "conversation_log.jsonl"
    records: list[dict[str, Any]] = []
    if path.is_file():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            for ln in lines[-max_lines:]:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    records.append(json.loads(ln))
                except Exception:
                    continue
        except Exception as e:
            print(f"[project_review] conversation read failed: {e}", file=sys.stderr)
    return {
        "source": "conversation_log",
        "authority": "conversation/context",
        "scope": "conversation",
        "not_project_fact": True,
        "records": records,
        "count": len(records),
    }


def _detect_conflicts(
    project_root: str,
    git_f: dict[str, Any],
    status_e: dict[str, Any] | None,
    test_f: dict[str, Any],
    memory_c: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    claims = (status_e or {}).get("raw_claims") or {}
    sections = (status_e or {}).get("sections") or []

    if sections and git_f.get("commit_count", 0) == 0 and git_f.get("available"):
        conflicts.append(
            {
                "conflict_type": "status_work_without_commits",
                "source_a": "STATUS.md",
                "source_b": "git",
                "description": (
                    f"STATUS has {len(sections)} section(s) in range but git reports "
                    f"0 commits since {git_f.get('since')}"
                ),
                "resolution": "unresolved",
            }
        )

    if claims.get("mentions_worktree_clean") and not git_f.get("worktree_clean", True):
        conflicts.append(
            {
                "conflict_type": "status_clean_vs_dirty",
                "source_a": "STATUS.md",
                "source_b": "git status",
                "description": (
                    "STATUS narrative suggests clean worktree but git status is dirty: "
                    + ", ".join((git_f.get("dirty_files") or [])[:10])
                ),
                "resolution": "unresolved",
            }
        )

    if claims.get("mentions_tests_passed") and test_f.get("status") == "unverified":
        conflicts.append(
            {
                "conflict_type": "status_tests_unverified",
                "source_a": "STATUS.md",
                "source_b": "last_test_result",
                "description": (
                    "STATUS claims tests passed but no verifiable last_test_result.json exists"
                ),
                "resolution": "unresolved",
            }
        )

    for h in claims.get("mentioned_hashes") or []:
        if len(h) < 7:
            continue
        code, _, _ = _run_git(project_root, ["cat-file", "-t", h])
        if code != 0:
            conflicts.append(
                {
                    "conflict_type": "status_unknown_commit",
                    "source_a": "STATUS.md",
                    "source_b": "git",
                    "description": f"STATUS mentions commit {h} which is not in this repository",
                    "resolution": "unresolved",
                }
            )

    mem = (memory_c or {}).get("data") or {}
    recent = mem.get("recent_files") or []
    if recent and git_f.get("worktree_clean") and git_f.get("commit_count", 0) == 0:
        conflicts.append(
            {
                "conflict_type": "memory_vs_git_idle",
                "source_a": "project_memory",
                "source_b": "git",
                "description": (
                    "project_memory.recent_files is non-empty but git range has 0 commits "
                    "and worktree is clean (memory may be stale)"
                ),
                "resolution": "unresolved",
            }
        )

    return conflicts


def build_review(
    project_root: str,
    *,
    since: str | None = None,
    until: str | None = None,
    include_status: bool = True,
    include_session: bool = False,
    include_conversation: bool = False,
) -> dict[str, Any]:
    since_r = _resolve_since(since)
    until_r = str(until).strip() if until else None

    git_f = _git_fact(project_root, since_r, until_r)
    test_f = _test_fact(project_root)

    fact: dict[str, Any] = {"git": git_f, "tests": test_f}

    evidence: dict[str, Any] = {}
    status_e = None
    if include_status:
        status_e = _status_evidence(project_root, since_r, until_r)
        evidence["status_md"] = status_e

    context: dict[str, Any] = {}
    memory_c = _memory_context(project_root)
    context["project_memory"] = memory_c
    if include_session:
        context["session_changes"] = _session_context()
    if include_conversation:
        context["conversation"] = _conversation_context(project_root)

    conflicts = _detect_conflicts(project_root, git_f, status_e, test_f, memory_c)

    return {
        "fact": fact,
        "evidence": evidence,
        "context": context,
        "conflicts": conflicts,
        "meta": {
            "since": since_r,
            "until": until_r,
            "include_status": include_status,
            "include_session": include_session,
            "include_conversation": include_conversation,
            "inference": None,
            "note": "Tool does not generate INFERENCE; model must mark any thematic summary as Inference.",
        },
    }


def _format_display(payload: dict[str, Any]) -> str:
    fact = payload.get("fact") or {}
    git_f = fact.get("git") or {}
    test_f = fact.get("tests") or {}
    evidence = payload.get("evidence") or {}
    context = payload.get("context") or {}
    conflicts = payload.get("conflicts") or []
    meta = payload.get("meta") or {}

    lines: list[str] = []
    lines.append("## FACT")
    lines.append("source=git authority=fact")
    lines.append(f"branch={git_f.get('branch')} head={git_f.get('head')}")
    lines.append(f"since={meta.get('since')} until={meta.get('until')}")
    lines.append(f"commit_count={git_f.get('commit_count')}")
    for c in (git_f.get("commits") or [])[:40]:
        lines.append(f"  - {c.get('short')} {c.get('timestamp')} {c.get('subject')}")
    clean = "clean" if git_f.get("worktree_clean") else "dirty"
    lines.append(f"worktree={clean}")
    if git_f.get("dirty_files"):
        for f in git_f["dirty_files"][:30]:
            lines.append(f"  * {f}")
    lines.append(
        f"tests: status={test_f.get('status')} "
        f"source={test_f.get('source')} "
        f"passed={test_f.get('passed')} failed={test_f.get('failed')} "
        f"timestamp={test_f.get('timestamp')}"
    )
    if test_f.get("status") == "unverified":
        lines.append("  (no persisted last_test_result — do not claim pass from STATUS)")

    if evidence.get("status_md"):
        se = evidence["status_md"]
        lines.append("")
        lines.append("## EVIDENCE")
        lines.append(
            f"source=STATUS.md authority=narrative/evidence verified=false "
            f"sections={len(se.get('sections') or [])}"
        )
        for s in (se.get("sections") or [])[:8]:
            lines.append(f"  ### {s.get('date')} {s.get('heading')}")
            body = (s.get("body") or "")[:800]
            for bl in body.splitlines()[:25]:
                lines.append(f"  {bl}")

    lines.append("")
    lines.append("## CONTEXT")
    mem = context.get("project_memory") or {}
    lines.append(
        "project_memory: authority=heuristic may_be_stale=true not_project_fact=true"
    )
    data = mem.get("data") or {}
    if data:
        for k in ("last_task", "last_status", "test_command"):
            if data.get(k):
                lines.append(f"  {k}: {str(data[k])[:120]}")
        if data.get("recent_files"):
            lines.append(
                "  recent_files: " + ", ".join(str(x) for x in data["recent_files"][:8])
            )
    else:
        lines.append("  (empty)")
    if context.get("session_changes"):
        sc = context["session_changes"]
        lines.append(
            f"session_changes: scope=current_session calendar_scope=false count={sc.get('count')}"
        )
    else:
        lines.append("session_changes: (omitted; include_session=false)")
    if context.get("conversation"):
        cv = context["conversation"]
        lines.append(
            f"conversation: authority=conversation/context not_project_fact=true count={cv.get('count')}"
        )
    else:
        lines.append("conversation: (omitted; include_conversation=false default)")

    lines.append("")
    lines.append("## CONFLICTS")
    if not conflicts:
        lines.append("(none)")
    else:
        for c in conflicts:
            lines.append(
                f"- [{c.get('conflict_type')}] {c.get('source_a')} vs {c.get('source_b')}: "
                f"{c.get('description')} resolution={c.get('resolution')}"
            )

    lines.append("")
    lines.append("## INFERENCE")
    lines.append("(not generated by tool — model must mark any summary as Inference)")

    return "\n".join(lines)


def make_project_review_tools(workspace) -> dict:
    def project_review(
        since: str | None = None,
        until: str | None = None,
        include_status: bool = True,
        include_session: bool = False,
        include_conversation: bool = False,
    ) -> ToolResult:
        """Unified project fact retrieval. Does not run tests or invent conclusions."""
        try:
            payload = build_review(
                workspace.project_root,
                since=since,
                until=until,
                include_status=bool(include_status),
                include_session=bool(include_session),
                include_conversation=bool(include_conversation),
            )
            body = _format_display(payload)
            git_f = (payload.get("fact") or {}).get("git") or {}
            kv = {
                "since": payload.get("meta", {}).get("since"),
                "commits": git_f.get("commit_count"),
                "worktree": "clean" if git_f.get("worktree_clean") else "dirty",
                "conflicts": len(payload.get("conflicts") or []),
                "tests": (payload.get("fact") or {}).get("tests", {}).get("status"),
            }
            return ToolResult.ok(
                display=format_block(
                    "project_review",
                    "OK",
                    kv,
                    body,
                    hint="回答回顾类问题优先用本工具；INFERENCE 由模型标注，勿把 STATUS 升格为 Fact",
                ),
                payload={"mutation": False, **payload},
            )
        except Exception as e:
            return ToolResult.fail(display=f"project_review failed: {e}")

    return {"project_review": project_review}
