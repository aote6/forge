# Forge Terminal Presentation Standard

**Status:** Normative  
**Scope:** CLI human-facing terminal presentation for Forge  
**Applies to:** `forge/terminal_present.py`, `forge/terminal_color.py`, presentation hooks on `Runtime`, and any future terminal UI built on the same contracts  

This document defines **long-lived behavioral contracts**, not a changelog of implementation batches. Historical Batch 1–5 work established these capabilities; later refactors MAY change code layout but MUST NOT change the contracts below without an explicit standard revision.

Normative language:

- **MUST** / **MUST NOT** — required
- **SHOULD** / **SHOULD NOT** — strong default; deviations need justification
- **MAY** — optional

---

## 1. Role of the Terminal Presentation Layer

### 1.1 Definition

The **Terminal Presentation Layer** is Forge’s human–terminal presentation surface. It turns orchestration and tool outcomes into readable, controllable terminal output without participating in agent reasoning or world mutation.

### 1.2 MUST present

- Tool lifecycle feedback (start / running / end)
- Tool-output **summary** for default display
- Full tool output viewing via **pager** (`last` / `page_last`)
- Tool-running **heartbeat** when a call is long-lived
- Assistant text (streamed and non-streamed)
- Warnings and presentation-level status lines that are explicitly routed to the presenter

### 1.3 MUST NOT own

- Planner / intent decisions
- Tool execution, schema, or confirmation policy
- Veritas / transaction / WAL / persistence correctness
- Adapter provider policy beyond consuming adapter contracts
- Agent memory or conversation semantics as a source of truth

### 1.4 Data flow (normative)

```text
Business state
    → Runtime / Adapter
    → presentation callbacks / event subscriptions
    → TerminalPresenter (and helpers)
    → terminal writer
```

Presentation **MUST NOT** reverse this flow: it MUST NOT mutate Runtime state, ToolResult, world state, or conversation content as a side effect of rendering.

Informative: `dp.py` MAY wire `Runtime` event handlers and optional `_on_assistant_delta` / `_on_assistant_done` hooks to a `TerminalPresenter`. That wiring is composition, not a license for business logic in the presenter.

---

## 2. Capability model (not batch numbers)

Implementation history is informative. The stable model is **capabilities**:

| Capability | Responsibility |
|------------|----------------|
| **Output summarization** | Bound default tool display size; preserve high-value tails on failure |
| **TerminalPresenter** | Single façade for tool, assistant, warning, and last-output presentation |
| **Tool lifecycle feedback** | Explicit start and end status lines |
| **Heartbeat** | Periodic “still running” feedback without blocking execution |
| **Pager** | Controlled viewing of long text under user input |
| **Assistant streaming presentation** | Incremental text display driven by Runtime hooks |
| **Terminal visual semantics** | Shared color + plain-text status vocabulary (see Color Semantics Standard) |

---

## 3. Tool presentation

### 3.1 Lifecycle

```text
START → RUNNING → (HEARTBEAT)* → END → RESULT SUMMARY
```

- **START** MUST emit an active-state line for the tool name (plain-text status + visual semantics per Color Standard).
- **RUNNING** is implicit after START until END.
- **HEARTBEAT** MAY be emitted after the configured interval while still running.
- **END** MUST stop any heartbeat for that lifecycle **before** emitting the final status line.
- **RESULT SUMMARY** MUST use the summarization rules below; full text remains available via pager / last-output cache owned by Runtime.

### 3.2 Status vocabulary (plain text)

Presentation MUST use textual status tokens so semantics survive ANSI stripping:

- Success end: `OK` (optionally with elapsed seconds)
- Failure end: `FAIL` (optionally with elapsed seconds)
- Running heartbeat: `running...` (or equivalent fixed spelling used by tests/contracts)
- Tool name framing: `[tool_name]`

Emoji MUST NOT be used as status glyphs in Terminal Presentation (see Color Semantics Standard).

### 3.3 Tool body vs UI chrome

- **UI chrome** (start/end/heartbeat lines, pager footers, `FORGE>`, `WARN:`) MAY carry visual semantics.
- **Tool output body** (content of `ToolResult.display` / summarized body) MUST remain **uncolored by Forge** as a whole. Summarization MAY insert an omit marker line; that marker is chrome-adjacent text, not a recolor of the original body.

### 3.4 Summarization contract

Implemented by `summarize_tool_display(display, *, success)`:

- Short output within configured line/char limits MUST be shown in full.
- Long **success** output SHOULD keep a small head and a larger tail, with an explicit omit marker pointing users to full view (`last`).
- **Failure** (`success is False`) MUST prefer **tail** content so errors/tracebacks remain visible.
- Character budget MUST NOT be applied in a way that discards the protected tail after line selection (tail-first under pressure).
- Empty / whitespace-only display MUST produce no body print (start/end chrome still MAY appear).

Constants such as line/char caps are implementation parameters; changing them is allowed if the above priorities remain true.

### 3.5 Full output and Runtime cache

Runtime owns `_last_tool_display` / `_last_tool_name` (or successors). Presentation MUST read that cache for `page_last` and MUST NOT maintain a second business history store.

---

## 4. Heartbeat

### 4.1 Purpose

Heartbeat exists so long tool calls do not look like a hung process. It is **not** a progress bar and MUST NOT stream subprocess stdout.

### 4.2 Normative behavior

- Default interval is **10 seconds** (`HEARTBEAT_INTERVAL`) unless injected otherwise for tests.
- Heartbeat MUST be scheduled only after START; the first running line MUST NOT appear before the interval elapses.
- Timer mechanism MAY use `threading.Timer` (daemon) in production.
- Tests MUST be able to inject `time_fn` and `timer_factory` (or equivalent) so suites remain **deterministic** without real multi-second sleeps.
- A **token / generation guard** MUST invalidate in-flight ticks after stop so a late callback cannot print after END.
- **END MUST call stop before** printing OK/FAIL (or equivalent). After END returns, no further heartbeat lines for that lifecycle MUST appear.
- Fast tools (START→END under interval) MUST emit **zero** running lines.
- Sequential tools: lifecycle A MUST NOT leak heartbeat lines into lifecycle B.

### 4.3 Why token guard and no sleep in tests

- Real `sleep` in tests is non-deterministic and slow; injectable clocks are required.
- Without invalidation, a cancelled timer can still fire once and corrupt the terminal after END—hence token/guard is mandatory.

### 4.4 Concurrency model

Current Runtime runs tools **sequentially** (START → execute → END). A single active heartbeat lifecycle is sufficient. Future concurrent tool events MUST NOT reuse a single global timer without a per-call lifecycle design—that would be a standard revision, not a silent change.

---

## 5. Pager

### 5.1 APIs

- `page_text(text, *, title, writer, input_fn, page_size)` — generic pager.
- `TerminalPresenter.page_last(name, display)` — last tool output viewer.

### 5.2 Input contract (confirmation-driven)

- **Enter** (empty input): next page; on the **last** page, Enter ends the pager (explicit exit).
- **`b`**: previous page; on first page, stay on first page (MUST NOT crash).
- **`q`**: exit pager to caller (REPL).
- Other input: prompt again; MUST NOT exit the outer REPL.
- **EOF** / **KeyboardInterrupt**: MUST return safely to the caller.

Raw-mode single-key capture is NOT required. Reliability on Termux takes priority over desktop TUI gestures.

### 5.3 Content contract

- Empty / whitespace-only: show a fixed empty notice (e.g. `(no tool output)`), no multi-page loop.
- Legacy sentinel `(no tool output yet)` MUST be normalized to the same empty notice and MUST NOT open as real content.
- Single page: print content once; MUST NOT enter the interactive loop.
- Multi-page: footer MUST show page index and controls; footer is **chrome** (MAY be colored per Color Standard).

### 5.4 Ownership

Pager logic MUST live in the presentation layer. Runtime MUST NOT implement pagination.

Writer and input callables MUST remain injectable for tests.

---

## 6. Assistant presentation and streaming

### 6.1 Adapter contract

- **`send(messages, tools) -> Message`** remains the stable non-stream API. Existing callers MUST keep working.
- **`send_stream(messages, tools, on_text_delta=None) -> Message`** MAY stream **text** via `on_text_delta(str)` and MUST still return a **complete** `Message`.
- Default `BaseAdapter.send_stream` MAY implement “call `send` + one full-text delta”.
- Provider-specific SSE/chunk parsing MUST stay in adapters (e.g. shared stream utility). Runtime MUST NOT parse provider wire formats.

### 6.2 Tool-call boundary

- Text deltas MAY be shown immediately.
- Tool-call **fragments** MUST be buffered until the stream completes; only then MAY they appear as `Message.tool_calls`.
- Runtime MUST execute tools only from a complete `Message` with fully assembled tool calls—**never** from partial deltas.

### 6.3 Runtime integration

- Runtime SHOULD prefer `send_stream` when available.
- On stream failure, Runtime MUST fall back to `send` so the agent loop remains available.
- Optional presentation hooks (`_on_assistant_delta` / `_on_assistant_done` or successors) MAY be set by the CLI; absence of hooks MUST NOT break the loop.
- After each model turn, Runtime SHOULD invoke the done hook so presentation can close a stream scope.

### 6.4 Presenter contract

- **`on_assistant_delta`**: first delta opens with a single `FORGE>` prefix; subsequent deltas append without repeating the prefix.
- **`on_assistant_done`**: closes the open stream (e.g. trailing newline).
- **`show_assistant`**: non-stream path; MUST NOT duplicate text if this turn already streamed.
- Empty stream (no text, tool-calls only): MUST NOT emit a bare `FORGE>` with no content.

Streamed text shown to the user MUST match the final assistant `Message.content` concatenation (no silent drop or reordering of deltas).

---

## 7. Adapter / Runtime / Presenter boundaries

| Layer | Responsibility |
|-------|----------------|
| **Adapter** | Map model HTTP/SDK into Forge `Message` / `ToolCall`; optional streaming assembly |
| **Runtime** | Orchestration, tool loop, events, session; optional presentation hooks |
| **Presenter** | Terminal output only |

Presenter MUST NOT decide tool calls, alter planner behavior, or write business state.

---

## 8. UI chrome vs body (summary)

| Kind | Examples | Presentation |
|------|----------|--------------|
| Chrome | `[tool] ...`, `OK`/`FAIL`, `running...`, `FORGE>`, `WARN:`, pager footer | Semantic text + optional color |
| Body | Tool stdout/summary lines | Plain; no whole-body Forge recolor |
| Assistant text | Model prose | Assistant semantic (Color Standard) |

---

## 9. Errors in the presentation layer

- Writer, timer, or input failures in presentation **SHOULD NOT** abort tool execution or corrupt transactions.
- Delta callbacks that raise **SHOULD** be isolated so a UI failure does not discard an otherwise valid `Message`.
- Presentation is an observation layer: it MUST NOT be a single point of failure for Runtime correctness.

---

## 10. Extension rules

Before adding info / progress / confirmation UI / sub-agent status / A2A status / future TUI surfaces, authors MUST answer:

1. Which presentation semantic does it belong to?
2. Does it need a new callback or event, or can existing hooks serve?
3. Does it belong on `TerminalPresenter`?
4. Is body content plain by default?
5. Does it need color, and which Color Standard semantic?
6. Does it preserve tool and assistant lifecycles (no heartbeat leak, no double assistant print)?

**MUST NOT** grow ad-hoc `print` UI in `dp.py` / `runtime.py` for new surfaces when an existing presenter path can host them.

---

## 11. Prohibitions

- MUST NOT scatter presentation `print` UI through business tool code as the primary UX path.
- MUST NOT scatter ANSI sequences outside the unified color module (Color Standard).
- MUST NOT require a third-party TUI framework for the base CLI contract (stdlib presentation is the baseline).
- MUST NOT auto-color entire tool bodies.
- MUST NOT double-print streamed assistant text via `show_assistant`.
- MUST NOT allow heartbeat output after END.
- MUST NOT let Presenter mutate business state.
- MUST NOT invent color meanings outside the Color Standard.
- MUST NOT change core agent protocols solely for visual wording.
- MUST NOT prioritize visual polish over Termux stability (prefer newline heartbeats over fragile `\r` redraw unless a future standard revises this).

---

## 12. Testing requirements

New presentation capabilities MUST ship with **deterministic unit tests** that:

- Do not require a real Termux device
- Do not use multi-second real sleeps for heartbeat
- Do not call real LLM HTTP APIs
- Inject writer, input, clock, and timer where needed

Reference suites (informative): `tests/test_tool_display_summary.py`, `tests/test_terminal_presenter.py`, `tests/test_terminal_heartbeat.py`, `tests/test_assistant_streaming.py`, `tests/test_terminal_color.py`.

---

## 13. Stability

Code MAY be refactored (module splits, renames). Behavioral contracts in this document MUST NOT change without updating the standard and noting migration impact. Batch 1–5 are historical delivery phases; **this file** is the source of truth for “how Forge terminal presentation should continue.”
