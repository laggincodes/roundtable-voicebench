"""Data models for VoiceBench scenarios, traces, evaluation results, and metrics."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class TestStatus(str, Enum):
    __test__ = False
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_RUN = "NOT_RUN"


class FailureCategory(str, Enum):
    FAIL_ROUT = "FAIL-ROUT"       # Addressed-Agent Routing
    FAIL_INT = "FAIL-INT"         # Interruption & Barge-In
    FAIL_ROLE = "FAIL-ROLE"       # Role / Persona Drift
    FAIL_REC = "FAIL-REC"         # Debate Topic Recovery
    FAIL_TOOL = "FAIL-TOOL"       # Tool Call / Fact Grounding
    FAIL_COORD = "FAIL-COORD"     # Multi-Agent Coordination / Dead Air / Collision
    FAIL_INFRA = "FAIL-INFRA"     # Transport / Timeout / Infrastructure


class EventType(str, Enum):
    SESSION_START = "SESSION_START"
    USER_INPUT = "USER_INPUT"
    SPEECH_CHUNK = "SPEECH_CHUNK"
    TRANSCRIPT_FINAL = "TRANSCRIPT_FINAL"
    INTERRUPT_TRIGGER = "INTERRUPT_TRIGGER"
    INTERRUPT_ACK = "INTERRUPT_ACK"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    SESSION_END = "SESSION_END"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"


@dataclass
class AgentConfig:
    id: str
    role: str
    description: str = ""
    domain_keywords: List[str] = field(default_factory=list)


@dataclass
class Preconditions:
    active_agents: List[AgentConfig] = field(default_factory=list)
    initial_topic: str = ""


@dataclass
class TurnInput:
    turn_index: int
    speaker: str
    payload: str
    input_type: str = "text"                      # "text" or "audio"
    target_expert: Optional[str] = None          # Explicitly addressed expert
    inject_delay_ms: float = 0.0
    barge_in: bool = False
    barge_in_delay_ms: Optional[float] = None     # Time into agent speech when barge-in happens


@dataclass
class ScenarioExpectations:
    primary_speaker_id: Optional[str] = None
    disallowed_speakers: List[str] = field(default_factory=list)
    max_latency_ms: Optional[float] = None
    max_yield_latency_ms: Optional[float] = None
    required_keywords: List[str] = field(default_factory=list)
    forbidden_keywords: List[str] = field(default_factory=list)
    required_tool_call: Optional[str] = None
    expected_tool_args: Optional[Dict[str, Any]] = None
    expected_numeric_grounding: Optional[str] = None
    expected_speaker_sequence: Optional[List[str]] = None
    max_cross_talk_overlap_ms: Optional[float] = None
    max_dead_air_ms: Optional[float] = None
    require_topic_recovery: Optional[bool] = None
    recovery_keywords: List[str] = field(default_factory=list)


@dataclass
class Scenario:
    id: str
    name: str
    description: str
    tags: List[str] = field(default_factory=list)
    timeout_seconds: float = 15.0
    preconditions: Preconditions = field(default_factory=Preconditions)
    turns: List[TurnInput] = field(default_factory=list)
    expectations: ScenarioExpectations = field(default_factory=ScenarioExpectations)
    source_file: Optional[str] = None


@dataclass
class AdapterCapabilities:
    """Explicit declaration of capabilities supported by a target adapter."""
    supports_text_input: bool = True
    supports_audio_input: bool = False
    supports_barge_in: bool = False
    supports_tool_calls: bool = False
    supports_multi_agent: bool = True
    timing_mode: str = "virtual_simulation"  # "virtual_simulation", "observed_wall_clock", or "target_reported_call_relative"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TraceEvent:
    timestamp_ms: float
    event_type: EventType
    speaker_id: Optional[str] = None
    intended_recipient: Optional[str] = None
    payload: Any = None
    turn_index: Optional[int] = None
    audio_duration_ms: Optional[float] = None
    is_simulated: bool = True                     # Clearly labeled as simulated!
    source_timestamp_ms: Optional[float] = None   # Target-reported timestamp if available
    client_timestamp_ms: Optional[float] = None   # Monotonic client-observed timestamp
    event_provenance: str = "local_virtual"       # "local_virtual", "client_observed", "target_reported"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type.value if isinstance(self.event_type, EventType) else self.event_type
        return d


@dataclass
class ConversationTrace:
    scenario_id: str
    session_id: str
    target_type: str
    target_config: str
    events: List[TraceEvent] = field(default_factory=list)
    timing_mode: str = "virtual_simulation"

    def add_event(self, event: TraceEvent) -> None:
        self.events.append(event)

    def get_events_by_type(self, event_type: EventType) -> List[TraceEvent]:
        return [e for e in self.events if e.event_type == event_type]

    def get_transcripts(self) -> List[TraceEvent]:
        return [e for e in self.events if e.event_type == EventType.TRANSCRIPT_FINAL]

    def get_tool_calls(self) -> List[TraceEvent]:
        return [e for e in self.events if e.event_type == EventType.TOOL_CALL]

    def get_barge_in_events(self) -> List[TraceEvent]:
        return [e for e in self.events if e.event_type in (EventType.INTERRUPT_TRIGGER, EventType.INTERRUPT_ACK)]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "session_id": self.session_id,
            "target_type": self.target_type,
            "target_config": self.target_config,
            "timing_mode": self.timing_mode,
            "events": [e.to_dict() for e in self.events],
        }


@dataclass
class Evidence:
    turn_index: Optional[int] = None
    timestamp_ms: Optional[float] = None
    speaker_id: Optional[str] = None
    event_type: Optional[str] = None
    quote_or_payload: Optional[Any] = None
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScenarioResult:
    scenario_id: str
    scenario_name: str
    status: TestStatus
    reason: str
    evidence: Optional[Evidence] = None
    failure_category: Optional[str] = None
    metrics: Dict[str, Optional[float]] = field(default_factory=dict)
    metric_notes: Dict[str, str] = field(default_factory=dict)  # Explanations for unavailable/null metrics
    target_type: str = "local_mock"
    target_config: str = "baseline"
    run_id: str = ""
    timestamp_utc: str = ""
    software_version: str = "0.1.0"
    timing_mode: str = "virtual_simulation"

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "status": self.status.value if isinstance(self.status, TestStatus) else self.status,
            "reason": self.reason,
            "failure_category": self.failure_category,
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "metrics": self.metrics,
            "metric_notes": self.metric_notes,
            "target_type": self.target_type,
            "target_config": self.target_config,
            "run_id": self.run_id,
            "timestamp_utc": self.timestamp_utc,
            "software_version": self.software_version,
            "timing_mode": self.timing_mode,
        }
        return d


@dataclass
class SuiteResult:
    run_id: str
    target_type: str
    target_config: str
    timestamp_utc: str
    total: int
    passed: int
    failed: int
    inconclusive: int
    not_run: int
    results: List[ScenarioResult] = field(default_factory=list)
    software_version: str = "0.1.0"
    timing_mode: str = "virtual_simulation"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "target_type": self.target_type,
            "target_config": self.target_config,
            "timestamp_utc": self.timestamp_utc,
            "timing_mode": self.timing_mode,
            "summary": {
                "total": self.total,
                "passed": self.passed,
                "failed": self.failed,
                "inconclusive": self.inconclusive,
                "not_run": self.not_run,
                "pass_rate": (self.passed / self.total) if self.total > 0 else 0.0,
            },
            "results": [r.to_dict() for r in self.results],
            "software_version": self.software_version,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


class StatusTransition(str, Enum):
    REGRESSION = "REGRESSION"            # PASS -> FAIL
    FIXED = "FIXED"                      # FAIL -> PASS
    UNCHANGED_PASS = "UNCHANGED_PASS"    # PASS -> PASS
    UNCHANGED_FAIL = "UNCHANGED_FAIL"    # FAIL -> FAIL
    INCONCLUSIVE_SHIFT = "INCONCLUSIVE_SHIFT"  # Change to or from INCONCLUSIVE
    NEW_SCENARIO = "NEW_SCENARIO"        # Added in candidate run
    REMOVED_SCENARIO = "REMOVED_SCENARIO"# Missing in candidate run


@dataclass
class MetricDelta:
    metric_name: str
    baseline_value: Optional[float]
    candidate_value: Optional[float]
    delta: Optional[float]
    is_compatible: bool
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScenarioDiff:
    scenario_id: str
    scenario_name: str
    baseline_status: Optional[str]
    candidate_status: Optional[str]
    transition: StatusTransition
    baseline_failure_category: Optional[str] = None
    candidate_failure_category: Optional[str] = None
    reason_change: Optional[str] = None
    metric_deltas: List[MetricDelta] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["transition"] = self.transition.value if isinstance(self.transition, StatusTransition) else self.transition
        return d


@dataclass
class RegressionReport:
    baseline_run_id: str
    candidate_run_id: str
    baseline_target: str
    candidate_target: str
    baseline_config: str
    candidate_config: str
    baseline_timing_mode: str
    candidate_timing_mode: str
    timing_modes_compatible: bool
    timing_compatibility_note: str
    total_compared: int
    regressions_count: int
    fixes_count: int
    unchanged_pass_count: int
    unchanged_fail_count: int
    inconclusive_count: int
    scenario_diffs: List[ScenarioDiff] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["scenario_diffs"] = [s.to_dict() for s in self.scenario_diffs]
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
