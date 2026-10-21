"""Generic HTTP Target Adapter for VoiceBench.

Connects to external AI Roundtable endpoints over HTTP using a documented JSON protocol.
Records monotonic wall-clock client-observed durations, preserves target-reported source
timestamps, and provides robust error handling for connection drops, timeouts, and malformed payloads.
"""

from __future__ import annotations

import json
from pathlib import Path
import socket
import time
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request
import uuid

from voicebench.adapters.base import TargetAdapter
from voicebench.models import (
    AdapterCapabilities,
    ConversationTrace,
    EventType,
    Scenario,
    TraceEvent,
)
from voicebench.trace import TraceCollector


class GenericHttpAdapter(TargetAdapter):
    """Adapter that executes scenarios against an external HTTP server.

    Protocol Specification:
      - HTTP Method: POST
      - Content-Type: application/json
      - Request payload includes scenario metadata, preconditions, and turns.
      - Response payload must be a JSON object containing an 'events' list.
    """

    def __init__(
        self,
        endpoint_url: str,
        timeout_seconds: float = 10.0,
        capabilities: Optional[AdapterCapabilities] = None,
        target_config: str = "external_http",
    ):
        super().__init__(target_type="generic_http", target_config=target_config)
        self.endpoint_url = endpoint_url
        self.timeout_seconds = timeout_seconds
        self._capabilities = capabilities or AdapterCapabilities(
            supports_text_input=True,
            supports_audio_input=False,
            supports_barge_in=True,
            supports_tool_calls=True,
            supports_multi_agent=True,
            timing_mode="observed_wall_clock",
        )

    def get_capabilities(self) -> AdapterCapabilities:
        """Return the declared capabilities of this HTTP target adapter."""
        return self._capabilities

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Optionally override endpoint URL or timeout from configuration."""
        if config:
            if "endpoint_url" in config:
                self.endpoint_url = str(config["endpoint_url"])
            if "timeout_seconds" in config:
                self.timeout_seconds = float(config["timeout_seconds"])

    def execute_scenario(self, scenario: Scenario) -> ConversationTrace:
        """Transmit scenario to target HTTP endpoint and capture conversation trace."""
        session_id = f"http-sess-{uuid.uuid4().hex[:8]}"
        collector = TraceCollector(
            scenario_id=scenario.id,
            target_type=self.target_type,
            target_config=self.target_config,
            session_id=session_id,
            use_virtual_time=False,  # Use real wall-clock timing!
        )

        # 1. Validate scenario compatibility against declared capabilities
        unsupported = self.validate_scenario_compatibility(scenario)
        if unsupported:
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                speaker_id="system",
                payload={
                    "error": "UnsupportedCapabilityError",
                    "details": unsupported,
                },
                is_simulated=False,
                metadata={"event_provenance": "client_observed"},
            )
            return collector.to_trace()

        # 2. Build the documented request payload
        request_payload = {
            "scenario_id": scenario.id,
            "session_id": session_id,
            "timeout_seconds": scenario.timeout_seconds,
            "preconditions": {
                "initial_topic": scenario.preconditions.initial_topic,
                "active_agents": [
                    {"id": ag.id, "role": ag.role, "domain_keywords": ag.domain_keywords}
                    for ag in scenario.preconditions.active_agents
                ],
            },
            "turns": [
                {
                    "turn_index": t.turn_index,
                    "speaker": t.speaker,
                    "input_type": t.input_type,
                    "payload": t.payload,
                    "target_expert": t.target_expert,
                    "inject_delay_ms": t.inject_delay_ms,
                    "barge_in": t.barge_in,
                    "barge_in_delay_ms": t.barge_in_delay_ms,
                }
                for t in scenario.turns
            ],
        }

        # 3. Transmit request and record monotonic boundaries
        collector.record_event(
            event_type=EventType.SESSION_START,
            speaker_id="system",
            payload={"endpoint_url": self.endpoint_url, "timing_mode": "observed_wall_clock"},
            is_simulated=False,
            metadata={"event_provenance": "client_observed"},
        )

        body_bytes = json.dumps(request_payload).encode("utf-8")
        req = urllib.request.Request(
            url=self.endpoint_url,
            data=body_bytes,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "VoiceBench/0.1.0",
            },
            method="POST",
        )

        t_start_monotonic = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                resp_bytes = resp.read()
                t_end_monotonic = time.monotonic()
                observed_latency_ms = round((t_end_monotonic - t_start_monotonic) * 1000.0, 2)
        except urllib.error.HTTPError as e:
            t_end_monotonic = time.monotonic()
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                speaker_id="system",
                payload={"error": f"HttpError: HTTP {e.code}: {e.reason}", "status_code": e.code},
                is_simulated=False,
                metadata={
                    "event_provenance": "client_observed",
                    "client_observed_latency_ms": round((t_end_monotonic - t_start_monotonic) * 1000.0, 2),
                },
            )
            return collector.to_trace()
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            t_end_monotonic = time.monotonic()
            reason_str = str(getattr(e, "reason", e))
            err_type = "TimeoutError" if "timed out" in reason_str.lower() or isinstance(e, TimeoutError) else "ConnectionError"
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                speaker_id="system",
                payload={"error": f"{err_type}: {reason_str}", "endpoint_url": self.endpoint_url},
                is_simulated=False,
                metadata={
                    "event_provenance": "client_observed",
                    "client_observed_latency_ms": round((t_end_monotonic - t_start_monotonic) * 1000.0, 2),
                },
            )
            return collector.to_trace()
        except Exception as e:
            t_end_monotonic = time.monotonic()
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                speaker_id="system",
                payload={"error": f"TransportException: {type(e).__name__}: {str(e)}"},
                is_simulated=False,
                metadata={"event_provenance": "client_observed"},
            )
            return collector.to_trace()

        # 4. Parse response JSON
        try:
            resp_data = json.loads(resp_bytes.decode("utf-8"))
        except Exception as e:
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                speaker_id="system",
                payload={"error": f"MalformedResponseError: Invalid JSON body: {str(e)}", "raw_body": resp_bytes[:200].decode("utf-8", errors="replace")},
                is_simulated=False,
                metadata={"event_provenance": "client_observed"},
            )
            return collector.to_trace()

        if not isinstance(resp_data, dict) or "events" not in resp_data or not isinstance(resp_data["events"], list):
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                speaker_id="system",
                payload={"error": "MalformedResponseError: Target response must contain an 'events' list."},
                is_simulated=False,
                metadata={"event_provenance": "client_observed"},
            )
            return collector.to_trace()

        # 5. Record user turns and target events into trace
        # First record user inputs that were injected
        for t in scenario.turns:
            collector.record_event(
                event_type=EventType.USER_INPUT,
                speaker_id=t.speaker,
                intended_recipient=t.target_expert,
                payload=t.payload,
                turn_index=t.turn_index,
                is_simulated=False,
                metadata={"event_provenance": "client_observed"},
            )
            if t.barge_in:
                collector.record_event(
                    event_type=EventType.INTERRUPT_TRIGGER,
                    speaker_id="user",
                    payload=t.payload,
                    turn_index=t.turn_index,
                    is_simulated=False,
                    metadata={"event_provenance": "client_observed"},
                )

        # Ingest target-reported events
        for i, ev_raw in enumerate(resp_data["events"]):
            if not isinstance(ev_raw, dict):
                continue

            raw_type = ev_raw.get("event_type", "TRANSCRIPT_FINAL")
            try:
                ev_type = EventType(raw_type)
            except ValueError:
                ev_type = EventType.TRANSCRIPT_FINAL

            source_ts = float(ev_raw.get("source_timestamp_ms")) if ev_raw.get("source_timestamp_ms") is not None else None
            audio_dur = float(ev_raw.get("audio_duration_ms")) if ev_raw.get("audio_duration_ms") is not None else None

            # Timestamp: client observed offset or source reported offset
            ev_meta = dict(ev_raw.get("metadata", {}))
            ev_meta["event_provenance"] = "target_reported" if source_ts is not None else "client_observed"
            ev_meta["client_observed_latency_ms"] = observed_latency_ms

            collector.record_event(
                event_type=ev_type,
                speaker_id=ev_raw.get("speaker_id"),
                intended_recipient=ev_raw.get("intended_recipient"),
                payload=ev_raw.get("payload"),
                turn_index=int(ev_raw["turn_index"]) if ev_raw.get("turn_index") is not None else None,
                audio_duration_ms=audio_dur,
                is_simulated=False,  # Real observed HTTP network response!
                source_timestamp_ms=source_ts,
                client_timestamp_ms=observed_latency_ms,
                metadata=ev_meta,
            )

        collector.record_event(
            event_type=EventType.SESSION_END,
            speaker_id="system",
            payload={"status": "http_session_complete", "client_observed_latency_ms": observed_latency_ms},
            is_simulated=False,
            metadata={"event_provenance": "client_observed"},
        )

        return collector.to_trace()

    def teardown(self) -> None:
        """HTTP connection cleanup."""
        pass
