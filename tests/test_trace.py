"""Unit tests for conversation trace collection."""

from voicebench.models import EventType
from voicebench.trace import TraceCollector


def test_trace_collector_virtual_clock_monotonic():
    collector = TraceCollector(scenario_id="SCEN-TEST", use_virtual_time=True)
    assert collector.current_time_ms() == 0.0

    collector.record_event(event_type=EventType.SESSION_START, speaker_id="system")
    collector.advance_virtual_clock(250.0)
    ev2 = collector.record_event(
        event_type=EventType.USER_INPUT, speaker_id="user", payload="Test prompt"
    )

    assert ev2.timestamp_ms == 250.0
    assert collector.current_time_ms() == 250.0


def test_trace_serialization_distinguishes_none_from_zero():
    collector = TraceCollector(scenario_id="SCEN-TEST", use_virtual_time=True)
    collector.record_event(
        event_type=EventType.TRANSCRIPT_FINAL,
        speaker_id="dr_aris",
        payload="Sample utterance",
        audio_duration_ms=None,  # Missing information, NOT 0!
        turn_index=1,
    )
    trace = collector.to_trace()
    d = trace.to_dict()

    assert len(d["events"]) == 1
    assert d["events"][0]["audio_duration_ms"] is None
    assert d["events"][0]["is_simulated"] is True
