# VoiceBench — Regression Differential Report

**Baseline Run:** `run-5bb32b0f` (local_mock / `baseline` / `virtual_simulation`)
**Candidate Run:** `run-95723d6b` (local_mock / `defect_bargein_lag` / `virtual_simulation`)

### Summary
- **Total Scenarios Compared:** 6
- **Regressions (PASS -> FAIL):** 1
- **Fixes (FAIL -> PASS):** 0
- **Unchanged Passing:** 5
- **Unchanged Failing:** 0
- **Inconclusive / Status Shifts:** 0
- **Timing Mode Compatibility:** Compatible

### Scenario Comparison Matrix

| Scenario ID | Name | Baseline | Candidate | Transition | Category Shift |
| :--- | :--- | :---: | :---: | :---: | :--- |
| `SCEN-COORD-06` | Multi-Speaker Floor Arbitration & Dead Air Detection | PASS | PASS | **UNCHANGED_PASS** | - |
| `SCEN-INT-02` | Human Barge-In & Yield Latency | PASS | FAIL | **REGRESSION** | None -> FAIL-INT |
| `SCEN-REC-04` | Post-Interruption Debate Topic Recovery | PASS | PASS | **UNCHANGED_PASS** | - |
| `SCEN-ROLE-03` | Persona & Stance Consistency Under Adversarial Prompt | PASS | PASS | **UNCHANGED_PASS** | - |
| `SCEN-ROUT-01` | Targeted Expert Addressed-Routing | PASS | PASS | **UNCHANGED_PASS** | - |
| `SCEN-TOOL-05` | Tool-Call Correctness & Fact Grounding | PASS | PASS | **UNCHANGED_PASS** | - |

### Detected Regressions (Action Required)

#### ❌ `SCEN-INT-02`: Human Barge-In & Yield Latency
- **Reason Change:** Baseline: All deterministic assertions satisfied with verifiable evidence. -> Candidate: Interruption yield latency threshold exceeded: measured 1450.0ms, maximum allowed is 750.0ms.
- **Failure Category:** `FAIL-INT`
- **Metric `turn_count`:** 1 -> 1 (Δ +0.0)
- **Metric `yield_latency_ms`:** 320.0 -> 1450.0 (Δ +1130.0)
