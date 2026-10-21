"""Unit tests for deterministic evaluator engine."""

from voicebench.evaluator import Evaluator
from voicebench.models import (
    ConversationTrace,
    EventType,
    FailureCategory,
    ScenarioExpectations,
    TestStatus,
    TraceEvent,
)
from voicebench.parser import load_scenario_from_yaml


SAMPLE_SCENARIO_YAML = """
schema_version: "1.0.0"
id: "SCEN-EVAL-01"
name: "Evaluator Test Scenario"
description: "Scenario for evaluator unit tests"
timeout_seconds: 10.0
turns:
  - turn_index: 1
    speaker: "user"
    payload: "Evaluate me"
expectations:
  primary_speaker_id: "dr_aris"
  required_keywords: ["physics", "storage"]
"""


def test_evaluator_unexecuted_scenario_returns_not_run():
    scenario = load_scenario_from_yaml(SAMPLE_SCENARIO_YAML)
    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace=None)

    assert result.status == TestStatus.NOT_RUN
    assert "not executed" in result.reason.lower()
    assert result.evidence is None


def test_evaluator_transport_error_returns_inconclusive():
    scenario = load_scenario_from_yaml(SAMPLE_SCENARIO_YAML)
    trace = ConversationTrace(
        scenario_id="SCEN-EVAL-01",
        session_id="sess-1",
        target_type="mock",
        target_config="broken",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.TRANSPORT_ERROR,
                payload="Connection reset by peer",
            )
        ],
    )
    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)

    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value
    assert "Transport error occurred" in result.reason
    assert result.evidence is not None
    assert result.evidence.quote_or_payload == "Connection reset by peer"


def test_evaluator_missing_evidence_returns_inconclusive():
    scenario = load_scenario_from_yaml(SAMPLE_SCENARIO_YAML)
    # Empty trace with only session start
    trace = ConversationTrace(
        scenario_id="SCEN-EVAL-01",
        session_id="sess-2",
        target_type="mock",
        target_config="silent",
        events=[
            TraceEvent(
                timestamp_ms=0.0,
                event_type=EventType.SESSION_START,
                speaker_id="system",
            )
        ],
    )
    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)

    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value
    assert "insufficient evidence" in result.reason.lower()


def test_evaluator_routing_mismatch_returns_fail_with_evidence():
    scenario = load_scenario_from_yaml(SAMPLE_SCENARIO_YAML)
    trace = ConversationTrace(
        scenario_id="SCEN-EVAL-01",
        session_id="sess-3",
        target_type="mock",
        target_config="wrong_speaker",
        events=[
            TraceEvent(
                timestamp_ms=250.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",  # Wrong speaker!
                payload="I am discussing physics and storage.",
                turn_index=1,
            )
        ],
    )
    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)

    assert result.status == TestStatus.FAIL
    assert result.failure_category == FailureCategory.FAIL_ROUT.value
    assert "Addressed agent routing failure" in result.reason
    assert result.evidence is not None
    assert result.evidence.speaker_id == "elena_vance"
    assert result.evidence.turn_index == 1


def test_evaluator_compliant_run_returns_pass():
    scenario = load_scenario_from_yaml(SAMPLE_SCENARIO_YAML)
    trace = ConversationTrace(
        scenario_id="SCEN-EVAL-01",
        session_id="sess-4",
        target_type="mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=250.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="dr_aris",
                payload="Our physics research confirms battery storage limits.",
                turn_index=1,
            )
        ],
    )
    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)

    assert result.status == TestStatus.PASS
    assert result.failure_category is None
    assert "All deterministic assertions satisfied" in result.reason
