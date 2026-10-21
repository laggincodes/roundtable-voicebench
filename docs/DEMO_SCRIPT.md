# VoiceBench — 3-Minute Technical Demonstration Script

This script provides an exact, step-by-step walkthrough for technical judges evaluating VoiceBench during the **FOSS FORGE 2.0 (Build with ElevenLabs)** hackathon.

---

## ⏱️ Minute 0:00 – 0:45: The Problem & VoiceBench's Approach

### The Problem
When voice systems transition from 1-on-1 bots to autonomous multi-agent roundtables (e.g., ElevenLabs AI Roundtable track), traditional LLM text benchmarks (MMLU, HumanEval) fail completely. They measure single-turn text accuracy but cannot evaluate real-time conversational dynamics:
- **Did the targeted expert take the floor, or did an unprompted panelist interject?**
- **Did a panelist yield immediately ($\le 750\text{ ms}$) when interrupted by a human moderator?**
- **Did the panel resume the original debate after an interruption without topic amnesia?**
- **Did a tool call succeed and ground its exact numeric output in speech?**

### The Solution: VoiceBench
VoiceBench is an offline-first evaluation and regression testing framework that:
1. Replaces non-deterministic LLM judging with **verifiable deterministic assertions** (exact regex token sets, mathematical latency differentials, schema validation, and nested JSON path checks).
2. Captures structured, immutable event logs with **strict provenance tracking** (`virtual_simulation`, `target_reported`, `client_observed`).
3. Connects cleanly to external roundtable services via **Generic HTTP** and evaluates hosted agents via an **ElevenLabs Conversational AI adapter**.

---

## ⏱️ Minute 0:45 – 1:30: Live Verification of the Baseline Suite

Execute the scenario validation and baseline evaluation live in your terminal:

```bash
# 1. Validate canonical roundtable scenarios (6/6 schema compliant)
python -m voicebench.cli scenarios validate scenarios/roundtable

# 2. Run the full baseline test suite (All 6 scenarios pass)
python -m voicebench.cli run --target mock -c baseline
```

### Expected Output
```text
Validating 6 scenario file(s)...
  [VALID]   SCEN-COORD-06.yaml
  [VALID]   SCEN-INT-02.yaml
  [VALID]   SCEN-REC-04.yaml
  [VALID]   SCEN-ROLE-03.yaml
  [VALID]   SCEN-ROUT-01.yaml
  [VALID]   SCEN-TOOL-05.yaml
Validation complete: 6 checked, ALL VALID.

Starting VoiceBench test run:
  Target: LocalMockAdapter (Config: baseline, Timing: virtual_simulation)
  Scenarios: 6
======================================================================
[PASS]          SCEN-COORD-06    Multi-Speaker Floor Arbitration & Dead Air Detection
[PASS]          SCEN-INT-02      Human Barge-In & Yield Latency
[PASS]          SCEN-REC-04      Post-Interruption Debate Topic Recovery
[PASS]          SCEN-ROLE-03     Persona & Stance Consistency Under Adversarial Prompt
[PASS]          SCEN-ROUT-01     Targeted Expert Addressed-Routing
[PASS]          SCEN-TOOL-05     Tool-Call Correctness & Fact Grounding
======================================================================
Summary: 6 total | 6 passed | 0 failed | 0 inconclusive | 0 not run (100.0% pass rate)
```

Every pass is backed by verifiable trace events with timestamps, speaker attributions, and tool invocations.

---

## ⏱️ Minute 1:30 – 2:15: Automated Regression Differencing (`voicebench diff`)

When a developer changes system prompts or switches models, regressions often slip through. VoiceBench includes first-class differential analysis (`voicebench diff`) that compares baseline and candidate runs, classifies state transitions, and fails CI/CD builds if a regression is detected.

Run the regression comparison using pre-recorded fixtures:

```bash
python -m voicebench.cli diff demo/baseline_run.json demo/candidate_bargein.json
```

### Expected Output (Exits with code `1`)
```text
========================================================
VoiceBench Regression Comparison
========================================================
Baseline:  run-13615e43 (local_mock / baseline / virtual_simulation)
Candidate: run-3257d65d (local_mock / defect_bargein_lag / virtual_simulation)
Timing Modes: COMPATIBLE

Summary:
  Compared:    6
  Regressions: 1 (PASS -> FAIL)
  Fixes:       0 (FAIL -> PASS)
  Unchanged:   5 pass, 0 fail
  Shifts:      0 inconclusive
--------------------------------------------------------
[UNCHANGED_PASS]     SCEN-COORD-06   PASS -> PASS
[REGRESSION]         SCEN-INT-02     PASS -> FAIL
  [!] Regression Reason: Baseline: All deterministic assertions satisfied with verifiable evidence. -> Candidate: Interruption yield latency threshold exceeded: measured 1450.0ms, maximum allowed is 750.0ms.
  Category: FAIL-INT
     Metric yield_latency_ms: 320.0 -> 1450.0 (delta: +1130.0)
[UNCHANGED_PASS]     SCEN-REC-04     PASS -> PASS
[UNCHANGED_PASS]     SCEN-ROLE-03    PASS -> PASS
[UNCHANGED_PASS]     SCEN-ROUT-01    PASS -> PASS
[UNCHANGED_PASS]     SCEN-TOOL-05    PASS -> PASS
```

### How the Assertion Engine Caught the Regression:
1. `SCEN-INT-02` specifies `max_yield_latency_ms: 750.0`.
2. In `baseline_run.json`, the agent halted speech within `320.0ms` of the interrupt trigger $\rightarrow$ `PASS`.
3. In `candidate_bargein.json`, an introduced defect caused the agent to continue speaking for `1450.0ms` $\rightarrow$ `FAIL` (`FAIL-INT`).
4. The comparator automatically surfaced the $+1130.0\text{ ms}$ regression delta and classified the failure.

---

## ⏱️ Minute 2:15 – 2:45: Verified ElevenLabs Live Smoke Evaluation

VoiceBench has been verified against live ElevenLabs infrastructure using a real completed conversation (`conv_0501m5fpgz7jfnpbnwwn75nxwe7k`).

### Scenario Alignment Safeguard:
VoiceBench guarantees that an unrelated conversation is never evaluated against an mismatched scenario:
- **Canonical `SCEN-TOOL-05`**: Expects user to ask about ERCOT electricity spot prices. When tested with a transcript asking about conversation ID, VoiceBench's alignment engine rejected evaluation as `INCONCLUSIVE` (`Required scenario user input was not observed`).
- **Dedicated Smoke Scenario (`SCEN-ELEVEN-SMOKE-01`)**: Matches the actual live conversation:
  - *Observed User Input:* `"where can i find conversation id ?"`
  - *Observed Agent Tool:* Calls `knowledge_base` with keyword query.
  - *Observed Tool Result:* Clean `status: "no_matching_docs"`, `chunk_count: 0`.
  - *Structured Path Assertions:* Evaluated `required_tool_result_paths: ["chunk_count", "status"]` and verified `chunk_count == 0` without type coercion $\rightarrow$ `PASS`.

To run offline verification of the ElevenLabs adapter and live smoke scenario without spending API credits:
```bash
python -m pytest tests/test_elevenlabs_adapter.py tests/test_elevenlabs_smoke_scenario.py -v
```
All 37 targeted offline tests pass with zero network dependencies.

---

## ⏱️ Minute 2:45 – 3:00: Honest Disclosures & Remaining Boundaries

VoiceBench adheres to strict research honesty:
1. **No Conflated Timing:** Simulated virtual clocks (`virtual_simulation`) and provider call-relative offsets (`target_reported_call_relative`) are never conflated with client-observed wall-clock latency.
2. **No Fabricated Audio Interruption Latency:** ElevenLabs REST conversation details provide message-level timestamps, not sub-second acoustic energy cancellation timestamps. Real-time acoustic barge-in measurement requires live bidirectional audio WebSockets.
3. **Single-Agent Scope:** Live ElevenLabs ConvAI sessions are natively single-agent; multi-agent floor arbitration across multiple live agents is demonstrated via local fixtures.
4. **Offline Test Coverage:** 131 automated tests pass locally in ~12 seconds with zero live API calls required.
