# VoiceBench — Evaluation Workbench UI Design & Architecture Notes
## Engineering an Evaluation Instrument, Not Generic AI Slop

**Author:** VoiceBench Core Team  
**Milestone:** Final Product Alignment & UI Redesign  
**Canvas Target:** http://127.0.0.1:8765

---

## 1. Visual Philosophy & Art Direction

VoiceBench is built as an **evaluation instrument for engineers and researchers**, not a consumer podcast player or marketing SaaS template.

### The Problem With Typical AI Hackathon UIs:
- Glowing purple-to-blue neon gradients that look like decorative video games.
- Giant cards with oversized numbers and meaningless percentage gauges.
- AI-generated robot illustrations, sparkles, and fabricated undulating audio waveforms that have no acoustic or mathematical relationship to real agent audio.
- Fake real-time activity counters, invented concurrent users, or synthetic streaming chatter designed to deceive judges.

### The VoiceBench Instrument Standard:
- **Canvas:** Warm mineral-white and pale parchment palette (`#f8f7f2` canvas, `#ffffff` card surfaces, `#e2dfd5` borders). Evokes calibrated paper charts, laboratory notebooks, and high-precision testing apparatus.
- **Typography:**
  - *Headings:* Classic editorial display serif (`Charter`, `Iowan Old Style`, `Palatino`, `Georgia`), establishing authority, readability, and serious engineering weight.
  - *Controls & Body:* Clean, neutral system sans-serif (`-apple-system`, `Segoe UI`, `Roboto`) for effortless legibility.
  - *Data, Timestamps & Traces:* High-legibility monospaced typography (`ui-monospace`, `SFMono-Regular`, `Consolas`) for IDs, millisecond timestamps, deltas, and event traces.
- **Accent:** Exactly one restrained burnt-orange accent (`#b44516`, hover `#963710`, tint `#fbf1eb`) reserved strictly for active operations, critical findings, and action buttons.
- **Status Palettes:**
  - *PASS / Compliant:* Forest olive (`#1e6b37`, background `#edf7f0`).
  - *FAIL / REGRESSION:* Brick crimson (`#b91c1c`, background `#fef2f2`).
  - *PARTIAL / WARNING:* Warm ochre (`#92400e`, background `#fffbeb`).
  - *NEUTRAL / UNCHANGED:* Slate graphite (`#535763`, background `#f1efea`).
- **Information Density:** Thin 1px rules, compact data tables, 4-6px restrained corners, structured timeline traces, and immediate access to evidence.

---

## 2. Multi-Page Information Architecture (7 Dedicated Views)

Rather than overloading a single scrollable view with competing cards, the redesigned VoiceBench interface divides the evaluation workflow into seven dedicated, refresh-safe workspaces powered by a client-side hash router (`#/overview`, `#/scenarios`, `#/run`, `#/regression`, `#/evidence`, `#/adapters`, `#/problem-fit`) with working back/forward browser history navigation:

```
[Overview] -> [Scenario Library] -> [Run Evaluation] -> [Regression Lab] -> [Evidence Explorer] -> [Adapters & Setup] -> [Problem Fit]
```

### 1. Overview — “What happened?”
A calm, authoritative starting point, free of decorative metric clutter:
- Clear product statement: *“Test voice agents. Inspect evidence. Catch regressions.”*
- One primary action: **Run an evaluation** (navigates to `#/run`).
- One secondary action: **Compare runs** (navigates to `#/regression`).
- Fast shortcut: **Try the Seeded Demo** (runs mock baseline and loads regression analysis).
- Latest actual run card: displays the latest run's action, duration, and verdict if executed; otherwise shows an honest empty state (*“No run recorded in this session”*).
- Project readiness strip: 13 Verified, 2 Partial, 0 Blocked indicators.
- Measurement provenance taxonomy reference table.

### 2. Scenario Library — “What are we testing?”
Displays the canonical 6 AI Roundtable benchmark scenarios as a dense, accessible matrix:
- Columns: Scenario ID, Name & Purpose, Failure Mode, Required Behavior, Schema Validity (`[VALID]`), Latest Status (`PASS`).
- Inspect button opens a slide-over detail drawer with:
  - Preconditions (active agent personas, roles).
  - Concrete conversation turn sequence with barge-in indicators.
  - Deterministic expectations and assertions.
  - Secondary disclosure toggle for raw YAML source directly from disk.
- Separate labelled section for the ElevenLabs Live Smoke Scenario (`SCEN-ELEVEN-SMOKE-01`), preventing confusion between single-agent tool checks and multi-agent roundtable benchmarks.

### 3. Run Evaluation — “Test a target”
A guided 3-step compact stepper:
- **Step 1 (Choose Target):** Select only genuinely configured targets (`mock_baseline`, `mock_defect`, `elevenlabs_conv`, `http_peer`, `agent_testing`).
- **Step 2 (Choose Suite):** Canonical Roundtable Suite (6 scenarios) or Live Smoke Scenario (1 scenario).
- **Step 3 (Review & Run):** Displays target provenance mode (`virtual_simulation`, `target_reported`, etc.), cost/remote mutation disclaimer (read-only), and execution button.
- Real execution log with expandable scenario rows showing reasons, duration, and failure categories.

### 4. Regression Lab — “What changed?” (Signature View)
Visual centerpiece for catching behavioral model drift:
- Run selectors populated from verified fixtures (`demo/baseline_run.json` vs `demo/candidate_bargein.json`).
- Seeded regression banner: **SCEN-INT-02 (Human Barge-In & Yield Latency)**:
  - Baseline: `320.0 ms` -> Candidate: `1,450.0 ms` (Δ `+1,130.0 ms`, threshold `750.0 ms`).
  - Transition: `PASS -> FAIL` (`None -> FAIL-INT`).
  - Label: *“Seeded fixture demonstration using virtual simulation timing. Not a physical acoustic measurement.”*
- Scenario-by-scenario comparison matrix with transitions (`UNCHANGED_PASS`, `REGRESSION`).
- Incompatibility warnings when comparing differing timing modes.
- Integrated Markdown viewer rendering `demo/diff_report.md`.

### 5. Evidence Explorer — “Why did it get this result?”
Searchable, filterable turn-by-turn trace timeline for deep auditability:
- Select from evaluated scenario traces (`SCEN-INT-02` Defect, `SCEN-INT-02` Baseline, etc.).
- Event stream table: Timestamp (ms), Type, Speaker, Content Preview, Provenance, Linked Assertion.
- Clicking an event opens full unredacted payload inspection (with credentials sanitized).
- Missing metrics render strictly as `—` (Unavailable), never `0.0ms`.

### 6. Adapters & Setup — “What can I connect?”
- Compact adapter registry table showing true configuration states and timing modes.
- Required environment variables listed by name only (never revealing secret values).
- Complete specification for external peer teams to evaluate their multi-agent roundtables against VoiceBench without code modifications via `POST /api/roundtable/turn`.

### 7. Problem Fit — “How does this match the brief?”
- Direct requirement audit rendering all 15 requirements from `docs/PROBLEM_STATEMENT_GAP_ANALYSIS.md`.
- Filterable by status (`All`, `Verified`, `Partial`).
- Transparently cites verified automated tests (160 passed) and documents the exact remaining boundary (live external peer audio barge-in).

---

## 3. Security, Performance & Zero-Dependency Execution

1. **Localhost Binding:** Server strictly binds to `127.0.0.1:8765`, rejecting remote network exposure.
2. **Allowlisted Execution:** Frontend buttons invoke specific server actions (`run_baseline`, `run_diff`, `validate_scenarios`, `run_tests`, `evaluate_elevenlabs`). No raw or arbitrary shell strings are accepted.
3. **Concurrent Lock Enforcement:** Single-execution lock (`threading.Lock`) prevents duplicate parallel background jobs from corrupting terminal state.
4. **Credential Redaction:** Automatic redaction regex sanitizes `sk_...`, `xi-api-key`, and tokens from all responses and UI displays.
5. **Zero External Font / Asset Delivery:** All fonts utilize system-installed typefaces (`Charter`, `Georgia`, `-apple-system`, `Consolas`). The interface renders instantly offline without internet access.
