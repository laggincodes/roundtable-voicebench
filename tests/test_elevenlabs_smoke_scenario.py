"""Deterministic offline tests for the ElevenLabs Live Knowledge-Base Smoke Scenario (SCEN-ELEVEN-SMOKE-01)."""

from pathlib import Path
import pytest

from voicebench.evaluator import Evaluator
from voicebench.models import (
    ConversationTrace,
    EventType,
    FailureCategory,
    TestStatus,
    TraceEvent,
)
from voicebench.parser import load_scenario_from_file

SMOKE_SCENARIO_PATH = (
    Path(__file__).parent.parent / "scenarios" / "live" / "elevenlabs" / "SCEN-ELEVEN-SMOKE-01.yaml"
)


def test_smoke_scenario_parses_and_validates():
    """Verify SCEN-ELEVEN-SMOKE-01 parses and satisfies schema validation."""
    scenario = load_scenario_from_file(SMOKE_SCENARIO_PATH)
    assert scenario.id == "SCEN-ELEVEN-SMOKE-01"
    assert len(scenario.turns) == 1
    assert scenario.turns[0].payload == "where can i find conversation id ?"
    assert scenario.expectations.required_tool_call == "knowledge_base"
    assert scenario.expectations.expected_tool_args == {"query": "conversation id"}
    assert "conversation id" in scenario.expectations.required_keywords


def test_smoke_scenario_matching_transcript_passes():
    """Verify synthetic trace matching the observed ElevenLabs interaction passes evaluation."""
    scenario = load_scenario_from_file(SMOKE_SCENARIO_PATH)
    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="conv_smoke_match_001",
        target_type="elevenlabs_convai",
        target_config="agent_voicebench",
        events=[
            TraceEvent(
                timestamp_ms=0.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent_voicebench",
                payload="Hello! How can I help you today?",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=0.0,
                event_type=EventType.USER_INPUT,
                speaker_id="human_user",
                payload="where can i find conversation id ?",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=1000.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent_voicebench",
                payload={
                    "tool": "knowledge_base",
                    "args": {"strategy": "keyword", "query": "conversation id"},
                    "request_id": "req_kb_001",
                },
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=1000.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="agent_voicebench",
                payload={
                    "tool": "knowledge_base",
                    "result": {
                        "result_type": "knowledge_base_success",
                        "status": "no_matching_docs",
                    },
                    "is_error": False,
                    "request_id": "req_kb_001",
                },
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=1000.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent_voicebench",
                payload="Where you can find a conversation ID depends on the platform or service you are using.",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
        ],
        timing_mode="target_reported_call_relative",
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace, target_type="elevenlabs_convai", target_config="agent_voicebench")
    assert result.status == TestStatus.PASS
    assert result.failure_category is None
    assert "All deterministic assertions satisfied" in result.reason


def test_smoke_scenario_mismatched_transcript_returns_inconclusive():
    """Verify unrelated prompt returns INCONCLUSIVE with SCENARIO_INPUT_NOT_OBSERVED safeguard."""
    scenario = load_scenario_from_file(SMOKE_SCENARIO_PATH)
    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="conv_mismatched_001",
        target_type="elevenlabs_convai",
        target_config="agent_voicebench",
        events=[
            TraceEvent(
                timestamp_ms=0.0,
                event_type=EventType.USER_INPUT,
                speaker_id="human_user",
                payload="Elena, what is the current wholesale electricity spot price in ERCOT right now?",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=1000.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent_voicebench",
                payload="I am not Elena and I do not have energy market prices.",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
        ],
        timing_mode="target_reported_call_relative",
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace, target_type="elevenlabs_convai", target_config="agent_voicebench")
    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value
    assert "SCENARIO_INPUT_NOT_OBSERVED" in result.reason


def test_smoke_scenario_observed_no_matching_docs_tool_result_handled_cleanly():
    """Verify tool execution returning status='no_matching_docs' with is_error=False is evaluated cleanly without false grounding claim."""
    scenario = load_scenario_from_file(SMOKE_SCENARIO_PATH)
    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="conv_no_docs_001",
        target_type="elevenlabs_convai",
        target_config="agent_voicebench",
        events=[
            TraceEvent(
                timestamp_ms=0.0,
                event_type=EventType.USER_INPUT,
                speaker_id="human_user",
                payload="where can i find conversation id ?",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=1000.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent_voicebench",
                payload={
                    "tool": "knowledge_base",
                    "args": {"query": "conversation id"},
                    "request_id": "req_kb_no_docs",
                },
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=1200.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="agent_voicebench",
                payload={
                    "tool": "knowledge_base",
                    "result": {"status": "no_matching_docs"},
                    "is_error": False,
                    "request_id": "req_kb_no_docs",
                },
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=2000.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent_voicebench",
                payload="I searched my knowledge base for conversation id, but found no matching documents.",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
        ],
        timing_mode="target_reported_call_relative",
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace, target_type="elevenlabs_convai", target_config="agent_voicebench")
    # Non-errored tool execution satisfies required_tool_call & expected_tool_args
    assert result.status == TestStatus.PASS
    # Verify no numeric grounding was hallucinated in metrics or results
    assert scenario.expectations.expected_numeric_grounding is None


def test_smoke_scenario_detects_tool_execution_error():
    """Verify tool execution error (is_error=True) correctly fails with FAIL_TOOL."""
    scenario = load_scenario_from_file(SMOKE_SCENARIO_PATH)
    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="conv_tool_err_001",
        target_type="elevenlabs_convai",
        target_config="agent_voicebench",
        events=[
            TraceEvent(
                timestamp_ms=0.0,
                event_type=EventType.USER_INPUT,
                speaker_id="human_user",
                payload="where can i find conversation id ?",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=1000.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent_voicebench",
                payload={
                    "tool": "knowledge_base",
                    "args": {"query": "conversation id"},
                    "request_id": "req_kb_err",
                },
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=1200.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="agent_voicebench",
                payload={
                    "tool": "knowledge_base",
                    "is_error": True,
                    "error": "InternalServerError: Knowledge base index unavailable",
                    "request_id": "req_kb_err",
                },
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=2000.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent_voicebench",
                payload="Sorry, my conversation id lookup service failed.",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
        ],
        timing_mode="target_reported_call_relative",
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace, target_type="elevenlabs_convai", target_config="agent_voicebench")
    assert result.status == TestStatus.FAIL
    assert result.failure_category == FailureCategory.FAIL_TOOL.value
    assert "Tool execution returned an error" in result.reason


def test_smoke_scenario_detects_missing_tool_call():
    """Verify answering without calling knowledge_base fails with FAIL_TOOL."""
    scenario = load_scenario_from_file(SMOKE_SCENARIO_PATH)
    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="conv_no_tool_001",
        target_type="elevenlabs_convai",
        target_config="agent_voicebench",
        events=[
            TraceEvent(
                timestamp_ms=0.0,
                event_type=EventType.USER_INPUT,
                speaker_id="human_user",
                payload="where can i find conversation id ?",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=1000.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent_voicebench",
                payload="You can find conversation id in the URL bar.",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
        ],
        timing_mode="target_reported_call_relative",
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace, target_type="elevenlabs_convai", target_config="agent_voicebench")
    assert result.status == TestStatus.FAIL
    assert result.failure_category == FailureCategory.FAIL_TOOL.value
    assert "was never called" in result.reason


def test_smoke_scenario_detects_tool_argument_mismatch():
    """Verify tool call with wrong argument fails with FAIL_TOOL."""
    scenario = load_scenario_from_file(SMOKE_SCENARIO_PATH)
    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="conv_bad_args_001",
        target_type="elevenlabs_convai",
        target_config="agent_voicebench",
        events=[
            TraceEvent(
                timestamp_ms=0.0,
                event_type=EventType.USER_INPUT,
                speaker_id="human_user",
                payload="where can i find conversation id ?",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=1000.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent_voicebench",
                payload={
                    "tool": "knowledge_base",
                    "args": {"query": "weather forecast"},
                    "request_id": "req_kb_bad_arg",
                },
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=1200.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="agent_voicebench",
                payload={
                    "tool": "knowledge_base",
                    "result": {"status": "no_matching_docs"},
                    "is_error": False,
                    "request_id": "req_kb_bad_arg",
                },
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=2000.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent_voicebench",
                payload="The conversation id was not found.",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
        ],
        timing_mode="target_reported_call_relative",
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace, target_type="elevenlabs_convai", target_config="agent_voicebench")
    assert result.status == TestStatus.FAIL
    assert result.failure_category == FailureCategory.FAIL_TOOL.value
    assert "Tool call parameter mismatch for 'query'" in result.reason
