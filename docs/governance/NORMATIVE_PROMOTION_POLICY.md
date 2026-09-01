# Normative Standard Promotion Policy

Type: Governance Procedure
Status: Candidate — Governance Procedure — Non-normative until adopted
Authority: Non-normative until formally adopted
Scope: How a clause that already exists in a Contract / Interface document may be formally elevated to a Normative Standard under docs/standards/.

Candidate — Governance Procedure — Non-normative until adopted

This document defines promotion procedure. It does not itself promote any Core Contract clause.

Until formally adopted through the governance entry points already defined in docs/standards/README.md, this document MUST NOT be treated as binding, and it MUST NOT be treated as a Normative Standard.

This procedure MUST NOT grant itself Normative status. Formal adoption, if any, MUST follow docs/standards/README.md (including explicit designation and registry rules). This procedure MUST NOT introduce documentation authority tiers that README does not already recognize.

Where an adopted form of this procedure and README conflict, README hierarchy and §4 registry win until both are revised together under README change rules.

Normative language in this procedure (applies only after formal adoption of the procedure itself):

- MUST / MUST NOT — required for a valid promotion
- SHOULD / SHOULD NOT — strong default
- MAY — optional

## 0. Purpose and non-goals

### 0.1 Purpose

Answer:

> When a clause already lives in a Contract / Interface document, under what conditions and through what process may it become a Normative Standard?

### 0.2 Non-goals

This procedure MUST NOT be read as:

- automatic promotion of any text that contains MUST / 必须
- bulk relocation of entire Contract files into docs/standards/
- a substitute for implementation work or test work
- a longevity essay (see docs/FORGE_LONGEVITY.md)
- authority to change product behavior without standard revision after promotion
- a resolution of undecided priority between Normative Standards and Constitution documents

### 0.3 Clause-level principle

Promotion MUST operate on stable semantic clauses (or coherent clause packages within the bounds of §7), not on whole documents by default.

A Contract document MAY remain the home of mixed content: some clauses Normative (by reference), others still Contract-only.

## 1. Definitions

| Term | Meaning |
|------|---------|
| Clause | A discrete behavioral or semantic requirement that can be stated without implementation identifiers. |
| Clause package | A set of clauses that express one invariant and its necessary closed loop (see §7). |
| Source Contract | A document registered under Contract / Interface in docs/standards/README.md §5 (or equivalent registry). |
| Normative Standard | A document that meets README §2 and is listed in README §4. |
| Promotion record | Durable audit + decision artifact for one promotion action (see §5). |
| Implementation form | Language, type names, field names, paths, schemas, CLI strings, module layout—anything replaceable under refactor. |

Current Core Contract Set (inputs only; not promoted by this procedure):

- docs/AGENT_ABI.md
- docs/RUNTIME_STATE_CONTRACT.md
- docs/HUMAN_INTERVENTION_CONTRACT.md
- docs/WORLD_DISK_SYNC.md
- docs/world_runtime_interface.md

## 2. Promotion gates

A clause MAY be proposed for promotion only if all of the following gates pass.

### Gate A — Behavioral semantics (not implementation form)

The clause MUST constrain observable system behavior, a stable protocol, or a long-lived semantic invariant.

It MUST NOT primarily specify:

- Python / Rust class or function names
- JSON field names or file paths
- particular CLI copy or tool identifiers
- current Runtime module structure
- temporary product scope unless restated as a durable invariant

Cross-structure check (required):

Ask: If the implementation language, Runtime, state machine, module layout, or internal data model is replaced, does this clause still express the same external obligation?

If yes, the clause may still be semantic. If no, the clause depends on current structure and is implementation form or design choice. It MUST NOT be promoted.

### Gate B — Explicit normative force

The promoted text MUST be expressible with clear MUST / MUST NOT (or 必须 / 严禁) such that a competent implementer can tell conformity from violation without guessing intent.

Descriptive architecture MUST NOT pass this gate unless rewritten as behavioral constraints.

### Gate C — Verifiability

There MUST exist a practical way to detect violation via at least one of: automated tests, state inspection, durable logs / ToolCallRecord / receipts, or other machine-checkable artifacts.

Model-only judgment MUST NOT be promoted as if it were machine-enforced truth.

For clauses that also pass Gate E, Gate C is not satisfied by a future test plan alone.

### Gate D — Cross-implementation stability

The clause MUST remain meaningful if Forge is rewritten in another language, Runtime is replaced, models change, or modules are refactored.

If the clause dies when identifiers rename, it is implementation form (Gate A fail).

### Gate E — Non-breakable core (operationalized)

Violation MUST threaten a core invariant of Forge correctness, safety boundary, recovery, user authority, or world/evidence semantics.

Passing Gate E requires an explicit failure-mode statement: the proposal MUST state concrete, observable, verifiable failure modes that occur if the clause is violated. Abstract labels alone are not sufficient.

Negative examples (MUST NOT pass Gate E by themselves): UX consistency only, code cleanliness, convenience of the current architecture, historical importance.

### Gate F — Explicit designation

Even if A–E pass, a clause is not Normative until:

1. a Normative Standard document (or section) states it; and
2. that document is listed in docs/standards/README.md §4; and
3. the promotion record is filed.

Presence of MUST in a Contract MUST NOT be treated as Gate F satisfaction.

## 3. Test coverage rules

For each clause (or package under §7), the promotion record MUST report coverage: Covered / Partial / Missing.

For any clause that claims Gate E:

- There MUST be at least one negative test showing that a conforming implementation fails (or rejects) when the clause is violated.
- The test MUST target semantic violation, not merely presence/absence of current field names or function names.
- Tests planned later MUST NOT be accepted as a basis to complete promotion of a Gate E clause.
- Partial or Missing MUST NOT complete promotion for Gate E clauses.

MUST NOT promote a clause and simultaneously weaken tests so that non-conformant behavior passes.

## 4. What must not be locked by promotion

Promotion MUST NOT freeze the following as Normative: language/type names, serialization field names, schemas, CLI/UX copy, tool surface names, runtime topology, storage layout, model providers.

These MAY remain in Contracts as informative binding for the current implementation. Normative text SHOULD state the semantic invariant in implementation-neutral language.

## 5. Roles

Forge may be single-maintainer. Process still MUST produce durable records.

| Role | Responsibility |
|------|----------------|
| Proposer | Files a promotion proposal. |
| Auditor | Performs clause-level gate review and test-coverage review; may be same person as Proposer if dual-role is disclosed. |
| Approver | Maintainer authority that adopts the change into README §4 and the Normative document. |
| Implementer | Owns code/tests alignment when required. |

Self-approval is allowed but MUST be explicit in the promotion record.

## 6. Implementation conformity (no deferred promotion)

If a candidate clause is known not to hold in production implementation, promotion MUST NOT be completed.

The project MUST either fix the implementation so behavior conforms, or narrow/revise the candidate clause so that implementation conforms to that wording.

There is no deferred-conformity, transitional-normative, or register-now-fix-code-later path for completing Normative promotion.

## 7. Clause package boundaries

Default: one package = one invariant that can be violated, judged, and tested independently.

A package MAY include multiple sentences only when they form the necessary closed loop of the same invariant.

MUST NOT bind unrelated invariants in one package.

## 8. Promotion process

Promotion MUST follow these steps in order. Skipping steps MUST invalidate the promotion.

### Step 1 — Propose

Proposer creates a promotion proposal containing source path, clause locator, implementation-neutral wording, gate justifications, Gate E failure modes, non-goals, package boundary justification, and draft placement.

### Step 2 — Clause-level audit

Auditor MUST re-check gates A–E against the proposed wording, reject whole-document promotion unless every retained clause independently passes, enforce package bounds, and flag conflicts without silently picking a winner.

### Step 3 — Test coverage review

Auditor MUST apply §3. Gate E clauses without semantic negative tests MUST fail.

### Step 4 — Implementation conformity check

Conforms → proceed. Non-conforms → MUST NOT complete promotion. Intentional divergence from old text → revise Contract first.

### Step 5 — Approve

Approver MUST verify gates passed, tests satisfied, conformity satisfied, no unresolved conflicts, wording implementation-neutral.

### Step 6 — Author the Normative artifact

MUST add or update a document under docs/standards/ with Status: Normative, defined scope, promoted clauses only, MUST/MUST NOT language. MUST NOT claim priority over other Standards or Constitution documents in its own body. MUST NOT copy entire Source Contract bodies.

### Step 7 — Register in docs/standards/README.md

MUST add to §4 Current Standards with role description. MUST NOT remove Source Contract from §5 unless separate deprecation decision. Directory membership without §4 listing MUST NOT count as promotion.

### Step 8 — Source Contract annotation

SHOULD annotate Source Contract to state which clauses are now Normative and where. MUST NOT silently delete Contract text.

## 9. Authority after promotion

Normative Standard (§4) constrains behavior within its stated scope. Constitution constrains subsystem invariants. Contract / Interface may mix promoted references and non-promoted rules. Longevity / Stance is inheritance narrative, not a Normative source. Architecture / Design must not override Normative without revision. Implementation is current fact. Tests verify; must not sole-specify when a Standard exists.

If a Normative Standard and a Constitution document have an unresolved semantic conflict, this procedure MUST NOT decide priority. The conflict MUST enter governance resolution. Implementers MUST NOT privately choose one side.

Two §4 standards in conflict: MUST resolve by revision or explicit hierarchy note. MUST NOT pick ad hoc. MUST NOT resolve by a later Standard's self-declared priority.

If code and a Normative Standard disagree: fix code or formally revise the standard. Current code is the authority MUST NOT erase a published standard.

Longevity MUST NOT be used as sole evidence for Gate E or Gate F, and MUST NOT impose new executable obligations.

## 10. Amendment, breaking change, and withdrawal

Editorial change: clarifications that do not change conformity verdict MAY land without code changes.

Semantic change MUST: update the §4 text in the same change set as code, update/add tests, record breaking change, obtain approval, and re-check Gates A–E against the new wording.

Withdrawal MUST: remove or mark obsolete the clause, update README §4 description, state whether Source Contract still holds a weaker rule, provide migration notes.

## 11. Prohibited promotion patterns

MUST NOT: promote solely because Contract uses MUST; move a file into docs/standards/ without §4 listing and gates; promote entire Core Contracts in one step; lock implementation form; use audits or Longevity as silent Normative sources; resolve conflicts by convenience; let tests redefine a standard; complete promotion while production behavior is known not to conform; package unrelated invariants; let a Standard claim superiority; treat this Candidate as binding before adoption.

## 12. Minimal promotion record template

Title, Date, Proposer, Auditor, Approver, Dual-role disclosure, Source Contract, Clause locators, Proposed normative wording, Package boundary, Gate A–E results, Failure modes, Rejected over-broad wording, Explicitly not promoted, Conflicts, Test coverage, Negative semantic test paths, Implementation conformity, Normative target path, README §4 entry, Source Contract annotation plan, Breaking change.

## 13. Adoption of this procedure

This document is a Candidate. Before formal adoption, it is non-normative and non-binding.

Adopting this procedure MUST NOT promote any Core Contract clause.

Adopting this procedure MUST NOT by itself create a new Normative Standard unless README explicitly lists it under §4.
