"""Independent synthetic trace tests for VoiceBench Evaluator.

These tests construct raw ConversationTrace objects directly, completely bypassing
SimulatedRoundtableTarget, to prove that the Evaluator logic independently evaluates
recorded evidence without shared assumptions.
"""

from voicebench.evaluator import Evaluator
from voicebench.models import (
    ConversationTrace,
    EventType,
    FailureCategory,
    ScenarioExpectations,
    TestStatus,
    TraceEvent,
)
from voicebench.parser import load_scenario_from_file
from pathlib import Path

SCENARIOS_DIR = Path(__file__).parent.parent / "scenarios" / "roundtable"


def test_synthetic_routing_failure_independent():
    """Verify SCEN-ROUT-01 fails when an external unscripted speaker responds."""
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROUT-01.yaml")
    evaluator = Evaluator()

    # Raw synthetic trace: User spoke, but an unknown agent 'bot_charlie' responded
    synthetic_trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="synth-01",
        target_type="third_party_engine",
        target_config="custom",
        events=[
            TraceEvent(timestamp_ms=0.0, event_type=EventType.SESSION_START, speaker_id="system"),
            TraceEvent(timestamp_ms=100.0, event_type=EventType.USER_INPUT, speaker_id="user", payload="Dr. Aris..."),
            TraceEvent(
                timestamp_ms=500.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="bot_charlie",  # Not dr_aris!
                payload="I am answering on battery physics.",
                turn_index=1,
            ),
        ],
    )

    result = evaluator.evaluate(scenario, synthetic_trace)
    assert result.status == TestStatus.FAIL
    assert result.failure_category == FailureCategory.FAIL_ROUT.value
    assert "bot_charlie" in result.reason
    assert result.evidence.speaker_id == "bot_charlie"


def test_synthetic_bargein_lag_independent():
    """Verify SCEN-INT-02 fails when interrupt ack arrives 1100ms after trigger."""
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-INT-02.yaml")
    evaluator = Evaluator()

    # Raw synthetic trace: Barge-in at 2000ms, ack at 3100ms (1100ms yield latency)
    synthetic_trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="synth-02",
        target_type="third_party_engine",
        target_config="custom",
        events=[
            TraceEvent(timestamp_ms=0.0, event_type=EventType.SESSION_START, speaker_id="system"),
            TraceEvent(
                timestamp_ms=2000.0,
                event_type=EventType.INTERRUPT_TRIGGER,
                speaker_id="user",
                payload="Hold on Elena...",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=3100.0,
                event_type=EventType.INTERRUPT_ACK,
                speaker_id="elena_vance",
                payload="Halting speech.",
                turn_index=1,
                metadata={"yield_latency_ms": 1100.0},
            ),
            TraceEvent(
                timestamp_ms=3300.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",
                payload="Let's adjust the repealed tariff numbers.",
                turn_index=1,
            ),
        ],
    )

    result = evaluator.evaluate(scenario, synthetic_trace)
    assert result.status == TestStatus.FAIL
    assert result.failure_category == FailureCategory.FAIL_INT.value
    assert "1100.0ms" in result.reason
    assert result.metrics["yield_latency_ms"] == 1100.0


def test_synthetic_topic_amnesia_without_forbidden_words():
    """Verify SCEN-REC-04 fails on pure topic amnesia even when no forbidden phrases are spoken."""
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-REC-04.yaml")
    evaluator = Evaluator()

    # Raw synthetic trace: Turn 2 agent talks about something completely off-topic (e.g. coffee)
    # without using any forbidden phrases like 'welcome to the podcast'
    synthetic_trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="synth-04",
        target_type="third_party_engine",
        target_config="custom",
        events=[
            TraceEvent(timestamp_ms=0.0, event_type=EventType.SESSION_START, speaker_id="system"),
            TraceEvent(timestamp_ms=100.0, event_type=EventType.USER_INPUT, speaker_id="user", payload="What time do we end?"),
            TraceEvent(timestamp_ms=500.0, event_type=EventType.TRANSCRIPT_FINAL, speaker_id="elena_vance", payload="At 3:00 PM.", turn_index=1),
            TraceEvent(timestamp_ms=700.0, event_type=EventType.USER_INPUT, speaker_id="user", payload="Great, please continue."),
            TraceEvent(
                timestamp_ms=1200.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="dr_aris",
                payload="I really enjoy drinking black coffee in the morning while reading the news.",  # Off-topic!
                turn_index=2,
            ),
        ],
    )

    result = evaluator.evaluate(scenario, synthetic_trace)
    assert result.status == TestStatus.FAIL
    assert result.failure_category == FailureCategory.FAIL_REC.value
    assert "topic amnesia" in result.reason.lower()


def test_synthetic_tool_argument_mismatch_independent():
    """Verify SCEN-TOOL-05 fails when tool is called with wrong arguments."""
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-TOOL-05.yaml")
    evaluator = Evaluator()

    # Raw synthetic trace: tool called with region="CAISO" instead of "ERCOT"
    synthetic_trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="synth-05",
        target_type="third_party_engine",
        target_config="custom",
        events=[
            TraceEvent(timestamp_ms=0.0, event_type=EventType.SESSION_START, speaker_id="system"),
            TraceEvent(timestamp_ms=100.0, event_type=EventType.USER_INPUT, speaker_id="user", payload="Elena, wholesale price in ERCOT?"),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="elena_vance",
                payload={"tool": "fetch_market_price", "args": {"asset": "electricity", "region": "CAISO"}},  # Wrong region!
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=500.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",
                payload="The spot price in ERCOT is 34.50.",
                turn_index=1,
            ),
        ],
    )

    result = evaluator.evaluate(scenario, synthetic_trace)
    assert result.status == TestStatus.FAIL
    assert result.failure_category == FailureCategory.FAIL_TOOL.value
    assert "parameter mismatch" in result.reason.lower()
    assert "CAISO" in result.reason
