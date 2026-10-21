"""Trace Collector for capturing structured conversation events."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from voicebench.models import ConversationTrace, EventType, TraceEvent


class TraceCollector:
    """Manages the recording and indexing of conversation events for a single scenario session."""

    def __init__(
        self,
        scenario_id: str,
        target_type: str = "local_mock",
        target_config: str = "baseline",
        session_id: Optional[str] = None,
        use_virtual_time: bool = True,
    ):
        self.scenario_id = scenario_id
        self.target_type = target_type
        self.target_config = target_config
        self.session_id = session_id or str(uuid.uuid4())
        self.use_virtual_time = use_virtual_time
        self._start_time_monotonic = time.monotonic()
        self._virtual_clock_ms = 0.0
        self._events: List[TraceEvent] = []

    def current_time_ms(self) -> float:
        """Return the current timestamp in milliseconds, either virtual or wall-clock."""
        if self.use_virtual_time:
            return round(self._virtual_clock_ms, 2)
        return round((time.monotonic() - self._start_time_monotonic) * 1000.0, 2)

    def advance_virtual_clock(self, delta_ms: float) -> None:
        """Advance the deterministic virtual clock by a delta in milliseconds."""
        if delta_ms < 0:
            raise ValueError(f"Cannot advance virtual clock backwards: {delta_ms}")
        self._virtual_clock_ms += delta_ms

    def record_event(
        self,
        event_type: EventType,
        speaker_id: Optional[str] = None,
        intended_recipient: Optional[str] = None,
        payload: Any = None,
        turn_index: Optional[int] = None,
        audio_duration_ms: Optional[float] = None,
        is_simulated: bool = True,
        timestamp_ms: Optional[float] = None,
        source_timestamp_ms: Optional[float] = None,
        client_timestamp_ms: Optional[float] = None,
        event_provenance: str = "local_virtual",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TraceEvent:
        """Record an event with verified timestamp, speaker, and payload."""
        ts = timestamp_ms if timestamp_ms is not None else self.current_time_ms()
        event = TraceEvent(
            timestamp_ms=ts,
            event_type=event_type,
            speaker_id=speaker_id,
            intended_recipient=intended_recipient,
            payload=payload,
            turn_index=turn_index,
            audio_duration_ms=audio_duration_ms,
            is_simulated=is_simulated,
            source_timestamp_ms=source_timestamp_ms,
            client_timestamp_ms=client_timestamp_ms,
            event_provenance=event_provenance,
            metadata=metadata or {},
        )
        self._events.append(event)
        return event

    def to_trace(self) -> ConversationTrace:
        """Export all recorded events into an immutable ConversationTrace."""
        return ConversationTrace(
            scenario_id=self.scenario_id,
            session_id=self.session_id,
            target_type=self.target_type,
            target_config=self.target_config,
            events=list(self._events),
        )
