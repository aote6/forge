# Project Review Contract

**Type:** Contract  
**Authority:** Contract (below Standard / Constitution; above ad-hoc STATUS narrative)  
**Status:** Active  
**Scope:** Answering project retrospectives — what was done, current status, recent edits, test status, progress.

This document binds the `project_review` tool and agent behavior for fact retrieval closure.

---

## 1. Authority map

| Concern | Authority |
|--------|-----------|
| Project **commit / calendar work history** | **Git** |
| Current **World** object state | World / Veritas runtime (`world_info`, receipts) |
| Normative behavior | Normative Standard / Constitution / Contract (see `docs/standards/`) |
| Engineering **narrative** | `STATUS.md` (Evidence only) |
| Session mutations | `session_changes` (Context; session ≠ calendar day) |
| Heuristic cache | `project_memory` (Context; may be stale) |
| Dialogue | conversation log (Context; not project fact) |

Git is the fact source for **committed project work history**. It is **not** the highest authority for World state, security policy, or normative standards.

---

## 2. Evidence levels

| Level | Meaning | Who produces |
|-------|---------|--------------|
| **FACT** | Directly verifiable from an authority fact source | Tool (`project_review`): Git commits/worktree; persisted `last_test_result` when present |
| **EVIDENCE** | Supporting narrative; not final truth by itself | Tool: `STATUS.md` sections (`verified=false`) |
| **CONTEXT** | Helps interpretation; MUST NOT be promoted to project fact | Tool: memory / optional session / optional conversation |
| **INFERENCE** | Model synthesis / thematic clustering | **Model only** — tool MUST NOT emit Inference |

---

## 3. Tool requirements (`project_review`)

- MUST be the preferred entry for retrospective questions.
- MUST default `since` to the local calendar **today**.
- MUST NOT include conversation history by default (`include_conversation=false`).
- MUST NOT run pytest (retrieval only).
- MUST NOT generate INFERENCE.
- MUST label STATUS as `authority=narrative/evidence`, `verified=false`.
- MUST set tests to `status=unverified` when `.forge/last_test_result.json` is absent.
- MUST list CONFLICTS with `resolution=unresolved` and MUST NOT auto-merge.

---

## 4. Conflicts (non-exhaustive)

Examples the tool SHOULD surface:

1. STATUS describes work in range but Git has zero commits in range.
2. STATUS implies clean worktree but Git is dirty.
3. STATUS claims tests passed but no verifiable `last_test_result`.
4. STATUS mentions a commit hash not present in the repository.
5. `project_memory` recent activity vs idle clean Git (stale memory).

Agents MUST surface conflicts; MUST NOT silently prefer STATUS over Git for commit history.

---

## 5. Test results

- Successful or failed **completed** pytest invocations MAY write `.forge/last_test_result.json`.
- Absence of that file ⇒ tests are **unverified**, not “passed”.
- STATUS test counts are Evidence only until closed by a persisted or live run.

---

## 6. Agent output

When summarizing, prefer:

```text
## FACT
## EVIDENCE
## CONTEXT
## CONFLICTS
## INFERENCE
```

Omit empty sections. Do not invent content to fill the template.
