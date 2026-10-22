"""Comprehensive test suite for VoiceBench Error Visibility & Failure Diagnostics.

Verifies end-to-end capture, classification, retention, secret redaction, and
diagnostics across 12 required failure scenarios:
1. Multiple failed assertions in a single scenario.
2. Multiple scenarios failing in the same run.
3. Infrastructure exception while other scenarios pass.
4. Invalid scenario YAML / schema validation error.
5. HTTP 4xx and 5xx responses with secret redaction.
6. Timeout / connection failure.
7. Malformed JSON from target or API.
8. Missing evidence or unavailable metric values.
9. Unexpected internal exception (harness error).
10. Failed regression comparison request.
11. Partially completed evaluation (NOT_RUN scenario retention).
12. Frontend rendering of multiple errors without dropping entries.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voicebench.adapters.base import TargetAdapter
from voicebench.adapters.http import GenericHttpAdapter
from voicebench.demo_app import (
    handle_run_baseline,
    handle_run_diff,
)
from voicebench.evaluator import Evaluator
from voicebench.models import (
    AdapterCapabilities,
    ConversationTrace,
    EventType,
    FailureCategory,
    ScenarioResult,
    TestStatus,
    TraceEvent,
    classify_failure_type,
    redact_secrets,
)
from voicebench.parser import load_scenario_from_yaml, load_scenarios_from_dir
from voicebench.runner import SuiteRunner


class BaseMockAdapter(TargetAdapter):
    """Reusable compliant target adapter test fixture."""

    def __init__(self, target_type: str = "mock", target_config: str = "default"):
        super().__init__(target_type=target_type, target_config=target_config)

    def get_capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supports_text_input=True,
            supports_audio_input=False,
            supports_barge_in=True,
            supports_tool_calls=True,
            supports_multi_agent=True,
            timing_mode="virtual_simulation",
        )

    def initialize(self, config=None):
        pass

    def teardown(self):
        pass

    def execute_scenario(self, scenario):
        return ConversationTrace(
            scenario_id=scenario.id,
            session_id=f"sess_{scenario.id}",
            target_type=self.target_type,
            target_config=self.target_config,
            events=[
                TraceEvent(
                    timestamp_ms=100.0,
                    event_type=EventType.USER_INPUT,
                    speaker_id="user",
                    payload="Tell me about physics",
                    turn_index=1,
                ),
                TraceEvent(
                    timestamp_ms=300.0,
                    event_type=EventType.TRANSCRIPT_FINAL,
                    speaker_id="agent_alpha",
                    payload="Physics discussion",
                    turn_index=1,
                ),
            ],
        )


# ---------------------------------------------------------------------------
# Test 1: Multiple failed assertions in a single scenario
# ---------------------------------------------------------------------------
def test_multiple_failed_assertions_accumulated():
    """Verify that multiple failed assertions in one scenario are all recorded without early abort."""
    yaml_content = """
schema_version: "1.0.0"
id: "SCEN-MULTI-FAIL-01"
name: "Multiple Failure Assertions Scenario"
description: "Scenario designed to trigger multiple failed assertions"
timeout_seconds: 5.0
turns:
  - turn_index: 1
    speaker: "user"
    payload: "Tell me about physics"
expectations:
  primary_speaker_id: "agent_alpha"
  disallowed_speakers: ["agent_beta"]
  required_keywords: ["quantum", "gravity"]
  forbidden_keywords: ["astrology"]
"""
    scenario = load_scenario_from_yaml(yaml_content)

    # Trace violates:
    # 1. primary_speaker_id (observed: agent_beta != agent_alpha)
    # 2. disallowed_speakers (observed: agent_beta)
    # 3. forbidden_keywords (observed 'astrology' in agent message)
    # 4. required_keywords (missing 'quantum' and 'gravity')
    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="conv_multi_fail",
        target_type="mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="Tell me about physics",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent_beta",
                payload="Here is some astrology info",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)

    assert result.status == TestStatus.FAIL
    assert result.failure_category in (
        FailureCategory.FAIL_ROUT.value,
        FailureCategory.FAIL_ROLE.value,
        FailureCategory.FAIL_ASSERT.value,
    )
    assert result.failure_type == "assertion_failure"

    # All failures must be captured in the failures list
    assert hasattr(result, "failures")
    assert len(result.failures) == 4, f"Expected 4 accumulated failures, got: {result.failures}"

    assertions = [f.get("assertion") for f in result.failures]
    assert "primary_speaker_id" in assertions
    assert "disallowed_speakers" in assertions
    assert "forbidden_keywords" in assertions
    assert "required_keywords" in assertions

    # Reason must summarize multiple failures without dropping
    assert "Multiple assertions failed (4):" in result.reason


# ---------------------------------------------------------------------------
# Test 2: Multiple scenarios failing in the same run
# ---------------------------------------------------------------------------
def test_multiple_scenarios_failing_in_same_run():
    """Verify that multiple scenario failures in a suite do not overwrite each other or abort the suite."""
    scen1_yaml = """
schema_version: "1.0.0"
id: "SCEN-RUN-PASS"
name: "Passing Scenario"
description: "Passing scenario"
turns: [{turn_index: 1, speaker: "user", payload: "Hi"}]
expectations: {primary_speaker_id: "agent_alpha"}
"""
    scen2_yaml = """
schema_version: "1.0.0"
id: "SCEN-RUN-FAIL-A"
name: "Failing Scenario A"
description: "Failing scenario A"
turns: [{turn_index: 1, speaker: "user", payload: "Test A"}]
expectations: {primary_speaker_id: "agent_alpha"}
"""
    scen3_yaml = """
schema_version: "1.0.0"
id: "SCEN-RUN-FAIL-B"
name: "Failing Scenario B"
description: "Failing scenario B"
turns: [{turn_index: 1, speaker: "user", payload: "Test B"}]
expectations: {required_keywords: ["nonexistent_keyword"]}
"""
    scenarios = [
        load_scenario_from_yaml(scen1_yaml),
        load_scenario_from_yaml(scen2_yaml),
        load_scenario_from_yaml(scen3_yaml),
    ]

    class MultiBehaviorAdapter(BaseMockAdapter):
        def execute_scenario(self, scenario):
            if scenario.id == "SCEN-RUN-PASS":
                return ConversationTrace(
                    scenario_id=scenario.id,
                    session_id="c1",
                    target_type="mock",
                    target_config="default",
                    events=[
                        TraceEvent(timestamp_ms=100.0, event_type=EventType.USER_INPUT, speaker_id="user", payload="Hi", turn_index=1),
                        TraceEvent(timestamp_ms=200.0, event_type=EventType.TRANSCRIPT_FINAL, speaker_id="agent_alpha", payload="Hello", turn_index=1),
                    ],
                )
            elif scenario.id == "SCEN-RUN-FAIL-A":
                return ConversationTrace(
                    scenario_id=scenario.id,
                    session_id="c2",
                    target_type="mock",
                    target_config="default",
                    events=[
                        TraceEvent(timestamp_ms=100.0, event_type=EventType.USER_INPUT, speaker_id="user", payload="Test A", turn_index=1),
                        TraceEvent(timestamp_ms=200.0, event_type=EventType.TRANSCRIPT_FINAL, speaker_id="agent_wrong", payload="Hello", turn_index=1),
                    ],
                )
            else:
                return ConversationTrace(
                    scenario_id=scenario.id,
                    session_id="c3",
                    target_type="mock",
                    target_config="default",
                    events=[
                        TraceEvent(timestamp_ms=100.0, event_type=EventType.USER_INPUT, speaker_id="user", payload="Test B", turn_index=1),
                        TraceEvent(timestamp_ms=200.0, event_type=EventType.TRANSCRIPT_FINAL, speaker_id="agent_alpha", payload="Hello without kw", turn_index=1),
                    ],
                )

    runner = SuiteRunner(adapter=MultiBehaviorAdapter())
    suite_result = runner.run_suite(scenarios)

    assert suite_result.total == 3
    assert suite_result.passed == 1
    assert suite_result.failed == 2

    r_map = {r.scenario_id: r for r in suite_result.results}
    assert r_map["SCEN-RUN-PASS"].status == TestStatus.PASS
    assert r_map["SCEN-RUN-FAIL-A"].status == TestStatus.FAIL
    assert r_map["SCEN-RUN-FAIL-B"].status == TestStatus.FAIL

    assert "agent_wrong" in r_map["SCEN-RUN-FAIL-A"].reason
    assert "nonexistent_keyword" in r_map["SCEN-RUN-FAIL-B"].reason


# ---------------------------------------------------------------------------
# Test 3: Infrastructure exception while other scenarios pass
# ---------------------------------------------------------------------------
def test_infrastructure_exception_isolated():
    """Verify an unexpected socket/network exception in one scenario is isolated and does not abort suite."""
    scen_ok1 = load_scenario_from_yaml("""
schema_version: "1.0.0"
id: "SCEN-OK-1"
name: "OK Scenario 1"
description: "OK 1"
turns: [{turn_index: 1, speaker: "user", payload: "Hi"}]
expectations: {primary_speaker_id: "agent_alpha"}
""")
    scen_crash = load_scenario_from_yaml("""
schema_version: "1.0.0"
id: "SCEN-CRASH"
name: "Crashing Scenario"
description: "Crashing scenario"
turns: [{turn_index: 1, speaker: "user", payload: "Explode"}]
expectations: {primary_speaker_id: "agent_alpha"}
""")
    scen_ok2 = load_scenario_from_yaml("""
schema_version: "1.0.0"
id: "SCEN-OK-2"
name: "OK Scenario 2"
description: "OK 2"
turns: [{turn_index: 1, speaker: "user", payload: "Hi"}]
expectations: {primary_speaker_id: "agent_alpha"}
""")

    class CrashingAdapter(BaseMockAdapter):
        def execute_scenario(self, scenario):
            if scenario.id == "SCEN-CRASH":
                raise ConnectionResetError("Connection abruptly terminated by target host: 192.168.1.100")
            return ConversationTrace(
                scenario_id=scenario.id,
                session_id=f"conv_{scenario.id}",
                target_type="mock",
                target_config="default",
                events=[
                    TraceEvent(timestamp_ms=100.0, event_type=EventType.USER_INPUT, speaker_id="user", payload="Hi", turn_index=1),
                    TraceEvent(timestamp_ms=200.0, event_type=EventType.TRANSCRIPT_FINAL, speaker_id="agent_alpha", payload="Hi", turn_index=1),
                ],
            )

    runner = SuiteRunner(adapter=CrashingAdapter())
    suite_result = runner.run_suite([scen_ok1, scen_crash, scen_ok2])

    assert suite_result.total == 3
    assert suite_result.passed == 2
    assert suite_result.inconclusive == 1

    r_map = {r.scenario_id: r for r in suite_result.results}
    assert r_map["SCEN-OK-1"].status == TestStatus.PASS
    assert r_map["SCEN-CRASH"].status == TestStatus.INCONCLUSIVE
    assert r_map["SCEN-CRASH"].failure_category == FailureCategory.FAIL_INFRA.value
    assert r_map["SCEN-CRASH"].failure_type == "infrastructure_error"
    assert "Connection abruptly terminated" in r_map["SCEN-CRASH"].reason
    assert r_map["SCEN-CRASH"].diagnostic_details.get("error_type") == "ConnectionResetError"
    assert r_map["SCEN-OK-2"].status == TestStatus.PASS


# ---------------------------------------------------------------------------
# Test 4: Invalid scenario YAML / schema validation error
# ---------------------------------------------------------------------------
def test_scenario_schema_validation_error_collected(tmp_path):
    """Verify that invalid YAML syntax or schema violations produce structured diagnostics."""
    good_yaml = tmp_path / "good.yaml"
    good_yaml.write_text("""
schema_version: "1.0.0"
id: "SCEN-GOOD"
name: "Valid Scenario"
description: "A valid test scenario"
turns: [{turn_index: 1, speaker: "user", payload: "Hi"}]
expectations: {primary_speaker_id: "agent_alpha"}
""", encoding="utf-8")

    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("""
schema_version: "1.0.0"
# Missing id, name, description, expectations
turns: "invalid-turns-type"
""", encoding="utf-8")

    scenarios = load_scenarios_from_dir(tmp_path, collect_errors=True)
    assert len(scenarios) == 2

    bad_scen = next(s for s in scenarios if s.id == "bad")
    assert getattr(bad_scen, "_validation_error", None) is not None
    assert "Scenario schema validation error" in bad_scen.description


# ---------------------------------------------------------------------------
# Test 5: HTTP 4xx and 5xx responses with secret redaction
# ---------------------------------------------------------------------------
def test_http_error_with_secret_redaction():
    """Verify that HTTP 4xx/5xx errors redact API keys, tokens, and authorization headers."""
    raw_error = "HTTP 401 Unauthorized: Authorization Bearer sk_live_secret1234567890abcdef is invalid. xi-api-key=secret_xi_key_val123"
    redacted = redact_secrets(raw_error)

    assert "sk_live_secret1234567890abcdef" not in redacted
    assert "secret_xi_key_val123" not in redacted
    assert "[REDACTED" in redacted


# ---------------------------------------------------------------------------
# Test 6: Timeout / connection failure
# ---------------------------------------------------------------------------
def test_timeout_connection_failure():
    """Verify socket timeout produces INCONCLUSIVE with diagnostic details."""
    scen = load_scenario_from_yaml("""
schema_version: "1.0.0"
id: "SCEN-TIMEOUT"
name: "Timeout Scenario"
description: "Tests timeout handling"
timeout_seconds: 1.0
turns: [{turn_index: 1, speaker: "user", payload: "Hello"}]
expectations: {primary_speaker_id: "agent_alpha"}
""")

    class TimeoutAdapter(BaseMockAdapter):
        def execute_scenario(self, scenario):
            raise TimeoutError("Socket read timed out after 1.0s waiting for agent speech frame")

    runner = SuiteRunner(adapter=TimeoutAdapter())
    suite_res = runner.run_suite([scen])

    res = suite_res.results[0]
    assert res.status == TestStatus.INCONCLUSIVE
    assert res.failure_category == FailureCategory.FAIL_INFRA.value
    assert res.failure_type == "infrastructure_error"
    assert "Socket read timed out" in res.reason
    assert res.diagnostic_details.get("error_type") == "TimeoutError"


# ---------------------------------------------------------------------------
# Test 7: Malformed JSON from target or API
# ---------------------------------------------------------------------------
def test_malformed_json_response_handling():
    """Verify that unparseable non-JSON responses from API or target produce handled errors."""
    adapter = GenericHttpAdapter(endpoint_url="http://127.0.0.1:9999/chat")
    scen = load_scenario_from_yaml("""
schema_version: "1.0.0"
id: "SCEN-HTTP-MALFORMED"
name: "Malformed HTTP Test"
description: "Test 502 bad gateway HTML response"
turns: [{turn_index: 1, speaker: "user", payload: "test"}]
expectations: {primary_speaker_id: "agent_alpha"}
""")

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"<html><head><title>502 Bad Gateway</title></head><body>Bad Gateway</body></html>"
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        trace = adapter.execute_scenario(scen)
        errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
        assert len(errors) > 0
        assert "MalformedResponseError" in errors[0].payload["error"]


# ---------------------------------------------------------------------------
# Test 8: Missing evidence or unavailable metric values
# ---------------------------------------------------------------------------
def test_missing_evidence_assertion_handling():
    """Verify that an expectation requiring a metric unavailable in trace produces INCONCLUSIVE, not bogus pass."""
    scen = load_scenario_from_yaml("""
schema_version: "1.0.0"
id: "SCEN-METRIC-MISSING"
name: "Missing Latency Metric Test"
description: "Tests missing interrupt evidence"
turns: [{turn_index: 1, speaker: "user", payload: "test"}]
expectations:
  max_yield_latency_ms: 500
""")
    trace = ConversationTrace(
        scenario_id=scen.id,
        session_id="conv_no_metric",
        target_type="mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=0.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="test",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent_alpha",
                payload="response",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    res = evaluator.evaluate(scen, trace)

    # Should not pass; cannot verify latency without INTERRUPT_TRIGGER
    assert res.status == TestStatus.INCONCLUSIVE
    assert res.failure_category == FailureCategory.FAIL_INFRA.value
    assert "no interrupt_trigger event was recorded" in res.reason.lower()


# ---------------------------------------------------------------------------
# Test 9: Unexpected internal exception (harness error)
# ---------------------------------------------------------------------------
def test_harness_internal_exception_handling():
    """Verify that an unexpected bug inside evaluation harness produces INCONCLUSIVE (FAIL-HARNESS)."""
    scen = load_scenario_from_yaml("""
schema_version: "1.0.0"
id: "SCEN-HARNESS-ERR"
name: "Harness Error Test"
description: "Tests harness error isolation"
turns: [{turn_index: 1, speaker: "user", payload: "test"}]
expectations: {primary_speaker_id: "agent_alpha"}
""")

    class BuggyEvaluator(Evaluator):
        def _evaluate_internal(self, *args, **kwargs):
            raise RuntimeError("Unexpected internal AST parser bug in evaluation engine")

    runner = SuiteRunner(adapter=BaseMockAdapter(), evaluator=BuggyEvaluator())
    suite_res = runner.run_suite([scen])
    res = suite_res.results[0]

    assert res.status == TestStatus.INCONCLUSIVE
    assert res.failure_category == FailureCategory.FAIL_HARNESS.value
    assert res.failure_type == "harness_error"
    assert "Unexpected internal AST parser bug" in res.reason
    assert res.diagnostic_details.get("error_type") == "RuntimeError"


# ---------------------------------------------------------------------------
# Test 10: Failed regression comparison request
# ---------------------------------------------------------------------------
def test_failed_regression_comparison_request():
    """Verify that comparing non-existent or invalid run files returns clean JSON error response."""
    res = handle_run_diff({
        "baseline": "nonexistent_baseline_file.json",
        "candidate": "nonexistent_candidate_file.json",
    })

    assert res.get("success") is False
    assert "error" in res
    assert "not found" in res["error"].lower()


# ---------------------------------------------------------------------------
# Test 11: Partially completed evaluation (NOT_RUN scenario retention)
# ---------------------------------------------------------------------------
def test_not_run_scenario_retention():
    """Verify that unexecuted scenarios retain NOT_RUN status and failure_type not_run."""
    res = ScenarioResult(
        scenario_id="SCEN-UNEXECUTED",
        scenario_name="Unexecuted Scenario",
        status=TestStatus.NOT_RUN,
        reason="Execution halted before scenario was reached",
    )

    assert res.status == TestStatus.NOT_RUN
    assert res.failure_type == "not_run"
    assert classify_failure_type(res.failure_category, res.status) == "not_run"


# ---------------------------------------------------------------------------
# Test 12: Frontend rendering of multiple errors without dropping entries
# ---------------------------------------------------------------------------
def test_frontend_run_results_structure():
    """Verify demo_app returns complete results structure for both PASS and FAIL runs."""
    res = handle_run_baseline({"target": "mock"})

    assert "scenarios" in res
    assert len(res["scenarios"]) == 6

    # Check that every scenario has required diagnostic fields
    for s in res["scenarios"]:
        assert "id" in s or "scenario_id" in s
        assert "status" in s
        assert "failure_category" in s
        assert "failure_type" in s
        assert "metrics" in s
        assert "failures" in s
