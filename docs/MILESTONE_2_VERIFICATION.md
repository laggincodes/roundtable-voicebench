# VoiceBench — Milestone 2 Verification Gate Report

**Date:** 2026-10-21  
**Target:** Milestone 2 (Integration Readiness and Regression Reliability)  
**Status:** Approved & Passed  
**Repository Working Directory:** `C:\Users\Yatha\.gemini\antigravity\scratch\voicebench`

---

## 1. Executive Summary & Verification Scope

Milestone 2 expands VoiceBench from an isolated offline mock evaluation engine into an **integration-ready, regression-reliable framework** for real-time multi-agent systems.

The verification gate confirms that:
1. The `TargetAdapter` contract explicitly enforces capability negotiation (`AdapterCapabilities`) and rejects incompatible scenarios prior to execution.
2. A completely independent, zero-external-dependency `GenericHttpAdapter` communicates over standard HTTP JSON protocols.
3. An offline `ReferenceRoundtableHandler` / `TestServerThread` allows full end-to-end testing of compliant and defective external server behaviors.
4. Measurement integrity is strictly maintained: monotonic client wall-clock timing is cleanly decoupled from target-reported internal timing, simulated timing is never conflated with live performance, unavailable metrics serialize as `null` (never `0.0`), and incompatible timing modes are blocked from comparative metric diffs.
5. A deterministic regression engine (`voicebench diff`) compares runs, detects regressions, reports category shifts and metric deltas, and sets appropriate exit codes for CI/CD integration.
6. All 45 automated tests pass with zero warnings and zero regressions against Milestone 1 functionality.

---

## 2. Automated Test Execution & Evidence

### Test Suite Output
```text
============================= test session starts =============================
platform win32 -- Python 3.13.15, pytest-9.1.1, pluggy-1.7.0 -- C:\Users\Yatha\AppData\Local\Programs\Python\Python313\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\Yatha\.gemini\antigravity\scratch\voicebench
collecting ... collected 45 items

tests/test_cli.py::test_cli_scenarios_list PASSED                        [  2%]
tests/test_cli.py::test_cli_scenarios_validate PASSED                    [  4%]
tests/test_cli.py::test_cli_run_baseline_returns_zero PASSED             [  6%]
tests/test_cli.py::test_cli_run_defect_returns_nonzero PASSED            [  8%]
tests/test_comparator.py::test_compare_baseline_vs_bargein_regression PASSED [ 11%]
tests/test_comparator.py::test_compare_baseline_vs_baseline_is_clean PASSED [ 13%]
tests/test_comparator.py::test_compare_detects_fixed_transition PASSED   [ 15%]
tests/test_comparator.py::test_incompatible_timing_modes_refuse_timing_metric_diffs PASSED [ 17%]
tests/test_comparator.py::test_generate_markdown_diff_report PASSED      [ 20%]
tests/test_evaluator.py::test_evaluator_unexecuted_scenario_returns_not_run PASSED [ 22%]
tests/test_evaluator.py::test_evaluator_transport_error_returns_inconclusive PASSED [ 24%]
tests/test_evaluator.py::test_evaluator_missing_evidence_returns_inconclusive PASSED [ 26%]
tests/test_evaluator.py::test_evaluator_routing_mismatch_returns_fail_with_evidence PASSED [ 28%]
tests/test_evaluator.py::test_evaluator_compliant_run_returns_pass PASSED [ 31%]
tests/test_generic_http_adapter.py::test_http_adapter_compliant_execution PASSED [ 33%]
tests/test_generic_http_adapter.py::test_http_adapter_connection_failure PASSED [ 35%]
tests/test_generic_http_adapter.py::test_http_adapter_timeout_handling PASSED [ 37%]
tests/test_generic_http_adapter.py::test_http_adapter_http_500_handling PASSED [ 40%]
tests/test_generic_http_adapter.py::test_http_adapter_malformed_json_handling PASSED [ 42%]
tests/test_generic_http_adapter.py::test_http_adapter_missing_events_field_handling PASSED [ 44%]
tests/test_generic_http_adapter.py::test_http_adapter_unsupported_capability_rejection PASSED [ 46%]
tests/test_independent_synthetic_traces.py::test_synthetic_routing_failure_independent PASSED [ 48%]
tests/test_independent_synthetic_traces.py::test_synthetic_bargein_lag_independent PASSED [ 51%]
tests/test_independent_synthetic_traces.py::test_synthetic_topic_amnesia_without_forbidden_words PASSED [ 53%]
tests/test_independent_synthetic_traces.py::test_synthetic_tool_argument_mismatch_independent PASSED [ 55%]
tests/test_parser.py::test_valid_minimal_scenario_loads PASSED           [ 57%]
tests/test_parser.py::test_missing_id_fails_validation PASSED            [ 60%]
tests/test_parser.py::test_empty_turns_fails_validation PASSED           [ 62%]
tests/test_parser.py::test_missing_expectations_fails_validation PASSED  [ 64%]
tests/test_parser.py::test_invalid_turn_structure_reports_actionable_error PASSED [ 66%]
tests/test_roundtable_target.py::test_baseline_target_reproducible_across_runs PASSED [ 68%]
tests/test_roundtable_target.py::test_defective_target_routing_amnesia_produces_wrong_speaker PASSED [ 71%]
tests/test_scenarios_e2e.py::test_e2e_all_six_scenarios_pass_on_baseline PASSED [ 73%]
tests/test_scenarios_e2e.py::test_e2e_scen_rout_01_detects_defect_routing_amnesia PASSED [ 75%]
tests/test_scenarios_e2e.py::test_e2e_scen_int_02_detects_defect_bargein_lag PASSED [ 77%]
tests/test_scenarios_e2e.py::test_e2e_scen_role_03_detects_defect_role_drift PASSED [ 80%]
tests/test_scenarios_e2e.py::test_e2e_scen_rec_04_detects_defect_topic_amnesia PASSED [ 82%]
tests/test_scenarios_e2e.py::test_e2e_scen_tool_05_detects_defect_tool_hallucination PASSED [ 84%]
tests/test_scenarios_e2e.py::test_e2e_scen_coord_06_detects_defect_collision_chaos PASSED [ 86%]
tests/test_scenarios_e2e.py::test_e2e_transport_drop_produces_inconclusive PASSED [ 88%]
tests/test_timing_integrity.py::test_simulated_events_explicitly_tagged PASSED [ 91%]
tests/test_timing_integrity.py::test_unavailable_metrics_are_none_not_zero PASSED [ 93%]
tests/test_timing_integrity.py::test_source_timestamps_preserved_separately PASSED [ 95%]
tests/test_trace.py::test_trace_collector_virtual_clock_monotonic PASSED [ 97%]
tests/test_trace.py::test_trace_serialization_distinguishes_none_from_zero PASSED [100%]

============================= 45 passed in 4.49s ==============================
```

---

## 3. Generic Adapter Contract & Failure Audits

### 3.1 Capability Validation
The framework verifies scenario preconditions against `AdapterCapabilities`. If a scenario requires an unadvertised feature (such as `supports_audio_input=False` when testing an audio-only scenario), execution fails fast with an explicit `ValueError` before opening network sockets or calling targets.
- **Automated Verification:** `tests/test_generic_http_adapter.py::test_http_adapter_unsupported_capability_rejection`

### 3.2 Network & Transport Failure Handling
Real-world network integrations encounter edge cases that must not be falsely scored as prompt or model logic failures. The `GenericHttpAdapter` catches network anomalies, converts them to `EventType.TRANSPORT_ERROR`, and the evaluator maps them to `INCONCLUSIVE` (`FAIL_INFRA`):

| Failure Injected | Injected Mode | Captured Exception | Result Status | Failure Category | Test Method |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Unreachable port | Closed port `59123` | `ConnectionError` / `TimeoutError` | `INCONCLUSIVE` | `FAIL_INFRA` | `test_http_adapter_connection_failure` |
| Server delay | Mode `timeout` (3s) | `TimeoutError` (client 0.5s) | `INCONCLUSIVE` | `FAIL_INFRA` | `test_http_adapter_timeout_handling` |
| HTTP 500 error | Mode `500` | `HTTP 500: Internal Server Error` | `INCONCLUSIVE` | `FAIL_INFRA` | `test_http_adapter_http_500_handling` |
| Corrupt JSON | Mode `malformed_json` | `MalformedResponseError` | `INCONCLUSIVE` | `FAIL_INFRA` | `test_http_adapter_malformed_json_handling` |
| Missing fields | Mode `missing_events` | `KeyError: 'events'` | `INCONCLUSIVE` | `FAIL_INFRA` | `test_http_adapter_missing_events_field_handling` |

---

## 4. Timing Mode Preservation & Measurement Integrity Audit

1. **Separation of Timing Modes:**
   - `LocalMockAdapter` declares `timing_mode="virtual_simulation"`. All events carry `is_simulated=True` and `event_provenance="local_virtual"`.
   - `GenericHttpAdapter` declares `timing_mode="observed_wall_clock"`. All events carry `is_simulated=False` and `event_provenance="client_observed"`.
2. **Provenance Preservation:**
   - When an external server sends internal event timestamps, they are placed in `TraceEvent.source_timestamp_ms` and labeled `target_reported`.
   - Client response latency is captured via Python's monotonic clock (`time.monotonic()`) in `TraceEvent.client_timestamp_ms` (`client_observed`).
3. **Null vs Zero:**
   - In scenarios without barge-in or where a metric is unmeasurable, `yield_latency_ms` is serialized as `null` in JSON. Automated test `tests/test_timing_integrity.py::test_unavailable_metrics_are_none_not_zero` asserts that deserialized JSON preserves `None` and does not default to `0.0`.
4. **Mode Incompatibility Guard:**
   - `voicebench.comparator.compare_runs` rejects calculating metric deltas for timing metrics if the baseline run was `virtual_simulation` and the candidate run was `observed_wall_clock`. An explicit warning note is recorded in `metric_notes`.
   - Verified in `tests/test_comparator.py::test_incompatible_timing_modes_refuse_timing_metric_diffs`.

---

## 5. Regression Comparator (`voicebench diff`) Audit

The `voicebench diff` command was tested using actual baseline and defective runs:
- **Baseline Run:** 6/6 PASS on `baseline` target.
- **Candidate Run:** 5/6 PASS, 1 FAIL (`SCEN-INT-02`) on `defect_bargein_lag`.

### Differential Report Verification:
- Correctly classified `SCEN-INT-02` as `StatusTransition.REGRESSION` (`PASS -> FAIL`).
- Correctly reported failure category shift: `None -> FAIL-INT`.
- Correctly computed numerical metric delta: `yield_latency_ms: 320.0 -> 1450.0 (delta: +1130.0)`.
- Generated both machine-readable JSON diff (`diff.json`) and formatted Markdown report (`diff_report.md`).
- Set process exit code to `1` due to presence of regressions.
- Verified in `tests/test_comparator.py::test_compare_baseline_vs_bargein_regression`.

---

## 6. Repository Integrity & Clean-Room Standard Audit

```bash
git status
```
- No foreign repository commits or Git history.
- Local commits created with proper clean author identity.
- Clean working directory following verification.

```bash
git remote -v
```
- Returns zero configured remotes (confirming no remote repositories exist and nothing has been pushed or published).

---

## 7. Gate Conclusion

Milestone 2 fulfills all requirements specified in `docs/MILESTONE_PLAN.md` and `docs/ARCHITECTURE.md`.
The framework is fully integration-ready, regression-reliable, and deterministic.

**Milestone 2 Sign-Off:** COMPLETE.
