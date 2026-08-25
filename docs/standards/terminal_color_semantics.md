# Forge Terminal Color Semantics Standard

**Status:** Normative  
**Companion:** `docs/standards/terminal_presentation.md`  
**Implementation anchor:** `forge/terminal_color.py` (`paint`, palette constants, `RESET`)

Forge terminal color is a **semantic state and attention system**, not decoration.

Normative language: **MUST** / **MUST NOT** / **SHOULD** / **MAY** as in the Presentation Standard.

---

## 1. Core principle

Color communicates:

- lifecycle (active vs idle)
- success vs failure
- secondary chrome vs primary attention
- assistant identity vs tool chrome
- warning / attention

Color MUST NOT be applied because a line “looks nicer” without a defined semantic.

---

## 2. Host theme ownership

- **Terminal background belongs to the host** (e.g. Termux theme).
- Forge **MUST NOT** set background colors (no `48;2` / reverse-video fills as a theme system).
- Forge **MUST NOT** write host files such as `~/.termux/colors.properties` as part of product behavior.
- Forge uses **ANSI 24-bit foreground** sequences only for its chrome.
- Default **body** text color is inherited from the host. Forge MUST NOT assume the user’s default foreground is `#22CC88`, even when a green-phosphor Termux skin is recommended informatively.

---

## 3. Formal palette

| Name | RGB | Role class |
|------|-----|------------|
| **PHOSPHOR** | `#22CC88` | Core — success / completed |
| **OSCILLOSCOPE** | `#00DC82` | Core — active / starting |
| **ALARM** | `#FF5500` | Core — failure / critical |
| **TUBE_BLUE** | `#50648C` | Core — secondary chrome |
| **AMBER** | `#FFBF00` | Core — assistant / warning |
| **DEEP_BLUE** | `#0066CC` | Reserved — future info/metadata |
| **AQUA** | `#7FFFD4` | Reserved — future highlight |
| **RESET** | ANSI `\x1b[0m` | Mandatory closer |

**CORE SEMANTICS** (current UI): PHOSPHOR, OSCILLOSCOPE, ALARM, TUBE_BLUE, AMBER.

**RESERVED**: DEEP_BLUE, AQUA — MAY be defined in code; MUST NOT be used in shipping UI without a standard update that assigns a stable semantic.

Exact RGB values MAY be tuned in a standard revision; **semantic names MUST NOT** be reassigned (e.g. PHOSPHOR MUST NOT mean failure).

---

## 4. Semantic mapping (stable)

| Token | Semantic family | MUST NOT also mean |
|-------|-----------------|--------------------|
| PHOSPHOR | success, normal completion | failure, pure warning |
| OSCILLOSCOPE | active, start, “work in progress” cue | success completion, failure |
| ALARM | failure, error, critical presentation | success |
| TUBE_BLUE | secondary UI, heartbeat, pager chrome | primary failure, primary success |
| AMBER | assistant output, warning, attention | success |

One color MUST map to a **coherent semantic family**. Conflicting dual use (e.g. AMBER as both success and failure) is forbidden.

---

## 5. Attention levels

| Level | Content | Color |
|-------|---------|-------|
| **0** | Plain tool body / neutral host text | none (inherit) |
| **1** | Secondary chrome (heartbeat, pager footer) | TUBE_BLUE |
| **2** | Active start | OSCILLOSCOPE |
| **3** | Attention (assistant, WARN) | AMBER |
| **4** | Failure | ALARM |
| Success end | Completed OK | PHOSPHOR (success, not “higher than failure”) |

Not everything SHOULD use level 3–4. Tool bodies stay level 0.

---

## 6. UI mapping (normative)

| Surface | Plain-text token | Color |
|---------|------------------|-------|
| Tool start | `[name] ...` | OSCILLOSCOPE |
| Heartbeat | `[name] running... Ns` | TUBE_BLUE |
| Tool success | `[name] OK …` | PHOSPHOR |
| Tool failure | `[name] FAIL …` | ALARM |
| Assistant | `FORGE> …` | AMBER |
| Warning | `WARN: …` | AMBER |
| Pager footer | page controls line | TUBE_BLUE |
| Tool body | original / summarized text | **PLAIN** |

These mappings are standard. Future UI MUST NOT silently reassign them.

---

## 7. ANSI implementation contract

- All product colors MUST go through the unified module (`forge/terminal_color.py` or a documented successor).
- Call sites MUST use `paint(text, color)` (or an equivalent that always appends **RESET**).
- **MUST NOT** embed raw `\x1b[38;2;…` sequences in Runtime, Adapter business logic, or ad-hoc `dp.py` UI except via the unified module.
- Every colored span MUST be: **COLOR + text + RESET**.
- **RESET MUST always be emitted** for each painted span so host state does not remain in a Forge foreground.

---

## 8. Streaming color contract

- Assistant streaming uses the **AMBER** semantic.
- Implementation MAY paint prefix and each delta as separate spans (each with RESET) to limit leakage risk.
- After stream completion (`on_assistant_done` or equivalent), terminal color state MUST be reset (no open foreground).
- If streaming is interrupted by an exception path, presentation SHOULD still restore RESET on any span it opened; Runtime correctness remains independent of UI recovery.

---

## 9. Color leakage

**Definition:** After Forge finishes a presentation action, the terminal remains stuck in a Forge foreground color affecting subsequent host output (including the shell prompt).

Color leakage is a **bug**.

Prevention MUST cover: writer errors, stream interruption, pager exit, heartbeat ticks, tool end, assistant done, and non-stream fallbacks.

---

## 10. Prohibitions

- MUST NOT use background fills as Forge theming
- MUST NOT use gradients, rainbows, or arbitrary decorative coloring
- MUST NOT color every line of tool output
- MUST NOT recolor entire tool bodies for “branding”
- MUST NOT invent module-specific colors without the application process below
- MUST NOT define parallel palettes in `dp.py`, Runtime, or Adapters
- MUST NOT scatter ANSI escapes outside the unified module (except non-color terminal control sequences such as bracketed paste in input handling, which are outside this standard’s palette rules)

---

## 11. Emoji policy

Terminal Presentation **MUST NOT** use emoji as status semantics, including but not limited to: 🔧 ✅ ❌ 🤖 ⚠️.

Required plain-text substitutes include forms such as: `[tool]`, `OK`, `FAIL`, `FORGE>`, `WARN`.

**Rationale:** Status is carried by **stable text tokens + optional ANSI color**, not by glyph availability across fonts and logs.

---

## 12. Accessibility / plain-text fallback

Color MUST NOT be the sole carrier of meaning.

- Failure MUST include a textual `FAIL` (or equivalent), not only ALARM color.
- Success MUST include textual `OK` (or equivalent), not only PHOSPHOR.
- Warnings MUST include textual `WARN:` (or equivalent), not only AMBER.

Redirected logs, disabled ANSI, or stripped escape sequences MUST still leave intelligible status.

---

## 13. Adding a new color

Before introducing a new named color, authors MUST document:

1. What semantic it expresses  
2. Why no existing core color fits  
3. Attention level (0–4)  
4. Conflicts with existing mappings  
5. Whether a new UI state is required  
6. Plain-text equivalent that works without ANSI  

Without a defined semantic, a new color MUST NOT be added. Reserved slots (DEEP_BLUE, AQUA) still require this process before first UI use.

---

## 14. Audit rules (informative process)

Recommended checks:

```bash
# Palette escapes should only appear in the unified color module (and tests).
grep -R $'\\x1b\\[38;2\\|\\\\033\\[38;2' forge dp.py

# Presentation status emoji should not reappear in presentation paths.
grep -R '🔧\|✅\|❌\|🤖\|⚠️' forge/terminal_present.py forge/terminal_color.py dp.py
```

Findings outside the unified module (for truecolor foreground) are **violations** of this standard unless explicitly exempted (e.g. input bracketed-paste CSI).

---

## 15. Stability

- Palette RGB MAY change under a versioned standard update.
- Semantic assignment of names (PHOSPHOR = success family, etc.) MUST NOT flip without a migration note in this document.
- Presentation Standard and this Color Standard MUST stay consistent; if they conflict, fix the implementation or revise both standards together—do not leave silent dual meanings.
