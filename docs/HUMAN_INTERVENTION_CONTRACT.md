Human Intervention Contract v1

Type: Contract
Authority: Binding
Status: Active v1
Scope: Task-level durable escalation from main AI to user, with direct user arbitration


0. Positioning

human_intervention is a durable control-plane decision from main AI to user.

It answers exactly one question:

  Main AI believes continuing current execution requires a user decision.

It is not:

  - AgentResult.status of sub AI
  - PendingAction (single write confirmation)
  - Execution Pause (in-substack confirmation)
  - Durable Pause / SubtaskCheckpoint (process interrupt recovery)
  - AI refusal authority


1. State Model

Reuse RuntimeState phase and pending. Do not add a new phase.


1.1 pending.kind extension

Currently persistable pending.kind:

  sync_decision

New:

  human_intervention

Pending structure:

  pending.kind = human_intervention
  summary: string
  payload:
    reason: string
    options_context: optional string
    proposed_next: optional string
    requested_at: float

Do not store messages, sub-stack, or full model text.


1.2 phase semantics

When opening human_intervention:

  phase = AWAITING_USER

Shares the waiting-for-user phase with sync_decision. The pending.kind distinguishes semantics.


1.3 phase after user arbitration

  continue -> phase = IDLE
  modify   -> phase = IDLE
  abort    -> phase = ABORTED

abort does not return to IDLE directly. Persisted state must be able to express that the task was explicitly abandoned by the user.


1.4 recovery

After restart with persisted RuntimeState:

  AWAITING_USER + pending.kind=human_intervention
    -> recovery.mode = decision_required

Startup does not auto-resolve. Re-display reason and wait for user arbitration.


2. Interaction Model

Main AI invokes request_human_intervention.

  Main AI
    |
    | judges: cannot reliably continue
    v
  request_human_intervention(reason, ...)
    |
    v
  RuntimeState:
    phase = AWAITING_USER
    pending.kind = human_intervention
    |
    +-- pending durable
    +-- current AI turn ends
    +-- Gate blocks task progression
    |
    v
  User arbitrates directly via machine-confirmable input
    |
    +-- continue
    |     |
    |     v
    |   phase = IDLE
    |   new AI turn continues original goal
    |
    +-- modify <new instruction>
    |     |
    |     v
    |   phase = IDLE
    |   new AI turn replans under new instruction
    |
    +-- abort
          |
          v
        phase = ABORTED
        current task explicitly terminated


2.1 user arbitration must be machine-confirmable

Do not allow natural-language user input to be interpreted by main AI into a decision.

CLI minimal version requires explicit user input:

  continue
  modify <new instruction>
  abort

Forge maps the result directly to a decision, without AI semantic interpretation.


2.2 ABORTED to IDLE transition responsibility

ABORTED does not auto-recover.

When the next new user task input arrives, Runtime treats the aborted task as finished and transitions before starting the new task:

  ABORTED -> IDLE

This transition must not restore old pending, old active_subtask, or old task execution.


2.3 modify semantics

modify does not restore the AI turn that was interrupted by request_human_intervention.

The user note enters the new main AI turn as a new task constraint.

Old context may exist as history, but must not be treated as still-valid execution authorization.

If new execution is needed, main AI must replan and spawn_subagent again.


3. Control Plane Tools


3.1 request_human_intervention

Parameters:

  reason: string (required)
  options_context: optional string
  proposed_next: optional string (main AI suggestion, not auto-executed)

Preconditions:

  phase is allowed to be IDLE
  no durable pending exists
  active_subtask_id is None

Effects:

  phase = AWAITING_USER
  pending.kind = human_intervention
  RuntimeState persisted
  current AI turn ends


3.1.1 Turn Boundary

After request_human_intervention succeeds:

  1. RuntimeState must already be persisted successfully.
  2. Current main AI tool loop must end immediately.
  3. Must not request another model output from main AI.
  4. Must not execute any further tool calls.
  5. Runtime enters a waiting mode that accepts only direct user arbitration input.

Therefore request_human_intervention is a Runtime turn boundary, not an ordinary tool that continues the Agent loop after success.


3.2 resolve_human_intervention

Parameters:

  decision: continue | modify | abort
  user_note:
    - required and non-empty when decision is modify
    - optional when decision is continue or abort

Only succeeds when pending.kind == human_intervention.

Effects:

  clear pending
  phase set by decision (continue/modify -> IDLE, abort -> ABORTED)
  persist
  write decision into conversation/log for next main AI turn


4. Gate

When pending.kind == human_intervention, reject:

  - mutation
  - reconciliation
  - forge_sync
  - spawn_subagent
  - resume_subtask

Allow:

  - read-only tools
  - resolve_human_intervention
  - todo read operations if needed


5. Boundaries with other mechanisms

If sync_decision pending exists, refuse to open human_intervention, and vice versa. At most one durable pending at a time.

SubtaskCheckpoint and human_intervention may coexist, but while human pending is unresolved, Gate blocks spawn and resume.

resolve(abort) does not automatically clear SubtaskCheckpoint. Durable Pause boundary stays independent.


6. User display

Title:

  Needs human decision (main AI escalation)

Show:

  - reason
  - options_context if present
  - three options: continue / modify <new instruction> / abort

Strictly separate from write confirmation and sync_decision copy.


7. Minimal implementation boundary


Must change:

  - forge/runtime_state.py: allow persist/load human_intervention; derive_recovery branch
  - docs/RUNTIME_STATE_CONTRACT.md: pending.kind and recovery
  - forge/tools/schemas.py: add two CONTROL_PLANE_TOOL_DECLARATIONS
  - forge/runtime.py: tool handlers, Gate, turn boundary, recovery prompt
  - forge/system_prompt.py: when to call, forbid use as refusal authority


May change:

  - forge/terminal_present.py or CLI entry: dedicated human escalation display and input parsing
  - docs/MAIN_AGENT_BEHAVIOR.md: boundary note
  - tests: pending kind roundtrip, recovery, Gate, turn boundary


Must not change:

  - forge/subagent.py execution loop
  - forge/agent_abi.py status three values and assemble_agent_result
  - forge/subtask_checkpoint.py semantics
  - World / Projection / transaction model
  - stuffing PendingAction into RuntimeState


8. Not doing

  - not adding a new phase to express human_intervention
  - not adding human_intervention to AgentResult.status
  - not doing human interaction inside sub-loop
  - not making request_human_intervention an ordinary chat tool
  - not letting main AI interpret user arbitration through natural language
