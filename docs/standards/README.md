# Forge Documentation Standards

**Status:** Normative (governance)  
**Scope:** How Forge classifies documentation authority, what may live under `docs/standards/`, and how standards relate to code and tests.

This file is a **long-lived governance rule**, not a changelog and not an audit report.

---

## 1. Purpose

This directory registers **Normative Standards**: documents that define long-term, binding expectations for Forge’s observable behavior, stable protocols, or durable semantic contracts.

Readers MUST treat documents listed in §4 as binding for their stated scope.

Readers MUST NOT treat every important document under `docs/` as a Normative Standard solely because it is important, strict, or historically influential.

---

## 2. Normative Standard Definition

A **Normative Standard** is a document that:

1. Constrains **software-observable behavior**, a **stable protocol**, or a **long-lived semantic contract**; and  
2. Uses explicit normative language (**MUST** / **MUST NOT** / **SHOULD** / **MAY**, or equivalent Chinese **必须** / **严禁** / **应** where that document’s convention applies); and  
3. Is intended to remain in force across refactors; and  
4. Has been **explicitly designated** as a Normative Standard (currently: membership in this directory plus listing in §4).

**Implementation fact** (“the code does X today”) is **not** the same as **normative authority** (“the system MUST do X”).

- Code describes **what the system currently is**.  
- A Normative Standard describes **what the system is required to conform to** within that standard’s scope.  
- Tests **SHOULD** encode testable normative clauses; tests **MUST NOT** alone redefine or replace a standard.

---

## 3. Documentation Hierarchy

Authority flows **top-down**. A lower tier **MUST NOT** silently override a higher tier.

```text
Normative Standard          (docs/standards/* — §4)
        ↓
Constitution                (subsystem invariants; e.g. recovery)
        ↓
Contract / Interface        (cross-component protocols; e.g. WRI)
        ↓
Security / Product Boundary (trust model, capability narrative, product edges)
        ↓
Architecture / Design       (structure and rationale)
        ↓
Stance / Identity           (what Forge is; start/work conditions; roles)
        ↓
Audit / Historical / Informational
```

**Implementation** and **Tests** sit beside this stack as verification and current fact. They **MUST NOT** outrank Normative Standards when semantics conflict.

Rules:

- **Architecture / Design MUST NOT** override a Normative Standard without an explicit standard revision.  
- **Audit / Historical** documents **MUST NOT** automatically become normative sources.  
- **Stance / Identity** constrains product meaning and boundaries; it **MUST NOT** replace executable protocol standards (recovery, WRI, terminal contracts, etc.).  
- If two **binding** documents conflict, implementers **MUST NOT** pick one ad hoc; the conflict **MUST** be resolved by explicit authority decision and/or document revision.

---

## 4. Current Standards

Only the following documents are **current Normative Standards** under this directory:

| Document | Role |
|----------|------|
| [`terminal_presentation.md`](./terminal_presentation.md) | Terminal Presentation Layer: lifecycle, summary, heartbeat, pager, assistant streaming presentation, chrome vs body, extension and test expectations. |
| [`terminal_color_semantics.md`](./terminal_color_semantics.md) | Terminal color as semantic state/attention: palette, mappings, `paint`/RESET, leakage, emoji policy, plain-text dual channel. |

Together they govern the **Forge Terminal Presentation System** (presentation behavior and visual semantics). Their normative text **MUST NOT** be weakened by informal docs or drive-by code changes.

No other file under `docs/standards/` is authorized by this README. New standards **MUST** be added by explicit standardization (see §9), not by relocating a file alone.

---

## 5. Binding or Guidance Documents Outside `standards/`

The following remain under `docs/` (or project layout as today). **Paths MUST NOT be treated as upgraded solely by this registry.** Classification is governance metadata only.

### Constitution

| Document | Role |
|----------|------|
| `docs/RECOVERY_CONSTITUTION.md` | Projection recovery invariants (version watermark, ordered apply, prohibitions on dual recovery truth). Binding for recovery semantics; complementary to WRI. |

### Contract / Interface

| Document | Role |
|----------|------|
| `docs/world_runtime_interface.md` | World Runtime Interface (WRI) between Forge and Veritas-facing runtime surfaces. |
| `docs/AGENT_ABI.md` | Main-AI ↔ sub-AI task/result contract: AgentTask, AgentResult, Evidence ↔ ToolCallRecord binding. |
| `docs/RUNTIME_STATE_CONTRACT.md` | RuntimeState lifecycle, durable pending kinds, recovery derivation, SyncDecision boundary. |
| `docs/HUMAN_INTERVENTION_CONTRACT.md` | Main AI → user task-level escalation: turn boundary, machine arbitration, original_goal restore. |
| `docs/WORLD_DISK_SYNC.md` | World ↔ disk synchronization contract and design; synchronization states, decision boundaries, and safety invariants are contractual. |

注册说明：列入本表表示该文档是 Forge 的正式契约入口，不等于自动升级为 Normative Standard。Normative Standard 仅在 §4 单独列出。

### Security / Product Boundary

| Document | Role |
|----------|------|
| `docs/SECURITY_BOUNDARY.md` | Security boundary vs safety guards; local trust model; result severity narrative; future remote preconditions. |

### Architecture / Design

| Document | Role |
|----------|------|
| `docs/architecture.md` | System structure and design overview. |
| `docs/PLANNER_DECISION_BOUNDARY.md` | Tool-loop decision boundary: machine supplies tools/facts; model chooses next tool; listed prohibitions. |

### Stance / Identity

| Document | Role |
|----------|------|
| `docs/FORGE_IDENTITY.md` | Identity criteria and layers (what is / is not Forge). |
| `docs/FORGE_PRODUCT_STANCE.md` | Minimal start path; AI required for agent work; Veritas not a start requirement. |
| `docs/FORGE_INTERACTION_STANCE.md` | Goal-driven interaction model; multi-frontend same essence. |
| `docs/FORGE_VERITAS_STANCE.md` | Capability availability with/without Veritas (implementation-fact stance). |
| `docs/FORGE_WORKSPACE_STANCE.md` | Software vs user workspace vs runtime state boundaries. |
| `docs/FORGE_ROLE_IN_VERITAS.md` | Role framing in the Veritas world; may lag tool-loop architecture—defer to newer loop docs and code when in doubt. |

### Audit / Historical / Informational

| Document | Role |
|----------|------|
| `docs/IDENTITY_BINDING_AUDIT.md` | Audit findings on identity attachment vs authentication. Not automatic law. |
| `docs/VERITAS_BOOTSTRAP_ANCHOR.md` | Design note for future self-hosting; explicitly not an execution plan. |

---

## 6. What Belongs in `standards/`

`docs/standards/` **MUST** only receive documents that meet §2 **and** are formally adopted as Normative Standards.

Typical candidates (only after standardization work):

- Observable CLI/runtime presentation contracts  
- Stable cross-version semantic protocols intended to bind implementations and tests  

`docs/standards/` is **not** “all important documentation.”

---

## 7. What Does Not Belong in `standards/`

The following **MUST NOT** be placed in `docs/standards/` merely by moving files:

- **Audit** reports and point-in-time verification notes  
- **Historical** design notes and non-plans  
- **Architecture** overviews without a separate normative adoption  
- **Stance / Identity** essays (unless rewritten and adopted as an Identity Standard)  
- **Constitution** documents under a different naming regime (keep distinct unless deliberately reissued as a Standard)  
- Large **interface** manuals may remain under `docs/` or a future `docs/contracts/` without implying they are weaker; path ≠ automatic demotion of interface authority  

Moving a file into this directory **without** meeting §2 and §9 **MUST NOT** be treated as creating a Normative Standard.

---

## 8. Authority and Conflict Rules

1. Within scope, a **Normative Standard (§4) outranks** Architecture, Stance, Audit, and informal notes.  
2. **Constitution** and **Contract** documents bind their subsystems; they **MUST NOT** be ignored because an architecture sketch differs.  
3. **Security / Product Boundary** documents bind trust and product-edge narrative; they **MUST NOT** be read as replacing Terminal or Recovery executable contracts.  
4. On conflict between two binding documents: **MUST** resolve by revision or explicit hierarchy note—**MUST NOT** “implement whichever is convenient.”  
5. **Code MUST NOT** redefine normative semantics by silent behavior drift.  
6. **Tests MUST NOT** be treated as the sole specification when a Normative Standard exists for the same behavior.

---

## 9. Change Rules

When changing behavior covered by a Normative Standard:

1. **MUST** update or revise the standard (or explicitly narrow its scope) **before** or **in the same change set as** shipping semantic code changes.  
2. **MUST** update tests that encode the affected clauses.  
3. **MUST NOT** ship a semantic change that contradicts §4 standards while leaving the standard text unchanged.  
4. Editorial clarifications that do not change requirements **MAY** land without code changes.  
5. Elevating an existing `docs/` document to a Normative Standard **MUST** be a deliberate standardization task (normative language, scope, hierarchy placement)—**not** a directory move alone.

---

## 10. Relationship Between Standards, Code, and Tests

| Artifact | Role |
|----------|------|
| Normative Standard | Required behavior / semantics within scope |
| Code | Current implementation fact |
| Tests | Verification; **SHOULD** track testable normative clauses |

If code and a Normative Standard disagree:

- Either the **code is non-conformant** and **SHOULD** be fixed, or  
- The **standard must be formally revised** if the product intentionally changes the contract.

“Current code is the authority” **MUST NOT** be used to erase a published standard without revision.

---

## 11. Current Documentation Governance Status

- `docs/standards/` exists.  
- **Terminal Presentation Standard** is adopted (`terminal_presentation.md`).  
- **Terminal Color Semantics Standard** is adopted (`terminal_color_semantics.md`).  
- Documentation types and hierarchy are registered in this README.  
- Selected binding/guidance documents under `docs/` carry a short **Type / Authority / Status / Scope** header for registry clarity; that header **does not** move them into this directory or promote them to Normative Standard.  
- **No bulk migration** of existing `docs/` files into `standards/` has been performed under this governance step.  
- Further Normative Standards **MUST** be added only through explicit standardization, not by relocating files alone.
- Governance Procedure candidate: `docs/governance/NORMATIVE_PROMOTION_POLICY.md` defines the clause-level promotion process for Contract → Normative Standard. It is **not** a Normative Standard and **MUST NOT** be listed in §4.

---

## Document types (reference)

| Type | Meaning |
|------|---------|
| Normative Standard | Binding behavioral/protocol/semantic contract (§2, §4) |
| Constitution | Subsystem invariants |
| Contract / Interface | Cross-component interface semantics |
| Security / Product Boundary | Trust model and product edges |
| Architecture / Design | Structure and design rationale |
| Stance / Identity | Product identity and stance |
| Audit / Historical / Informational | Verification or notes; not automatic law |
| Governance Procedure | Process rules for promotion/adoption; not a Normative Standard, not listed in §4 |
| Implementation / Tests | Fact and verification; not sole norm source |
