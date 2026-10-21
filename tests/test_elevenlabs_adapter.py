"""Deterministic offline tests for ElevenLabsConvAIAdapter."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import pytest

from voicebench.adapters.elevenlabs import ElevenLabsConvAIAdapter
from voicebench.comparator import compare_runs
from voicebench.evaluator import Evaluator, normalize_transcript_text, validate_scenario_input_alignment
from voicebench.models import (
    ConversationTrace,
    EventType,
    FailureCategory,
    ScenarioExpectations,
    ScenarioResult,
    SuiteResult,
    TestStatus,
    TraceEvent,
    TurnInput,
)
from voicebench.parser import load_scenario_from_file

SCENARIOS_DIR = Path(__file__).parent.parent / "scenarios" / "roundtable"


class MockElevenLabsHandler(BaseHTTPRequestHandler):
    """Local mock handler for ElevenLabs ConvAI REST API endpoints."""

    def log_message(self, format, *args):
        # Silence console log during tests
        pass

    def do_GET(self):
        mode = getattr(self.server, "test_mode", "compliant")
        api_key = self.headers.get("xi-api-key", "")

        # 1. Auth check
        if api_key != "valid_test_xi_key_secret_1234":
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail": {"status": "unauthorized", "message": "Invalid API key"}}')
            return

        # 2. Simulated failure modes
        if mode == "rate_limit":
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail": {"status": "too_many_requests", "message": "Quota exceeded"}}')
            return

        if mode == "server_error":
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail": {"status": "error", "message": "Internal server error"}}')
            return

        if mode == "malformed_json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"incomplete_json": [')
            return

        if mode == "missing_transcript":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "done", "conversation_id": "conv_123"}')
            return

        if mode == "agent_mismatch":
            conv_payload = {
                "agent_id": "unexpected_rogue_agent",
                "conversation_id": "conv_mismatched_001",
                "status": "done",
                "transcript": [
                    {"role": "user", "message": "Hello", "time_in_call_secs": 1.0},
                    {"role": "agent", "message": "Hi there", "time_in_call_secs": 2.0},
                ],
            }
            resp_bytes = json.dumps(conv_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        # Compliant Agent Info endpoint: /v1/convai/agents/{agent_id}
        if "/v1/convai/agents/" in self.path:
            agent_payload = {
                "agent_id": "test_agent_dr_aris",
                "name": "Dr. Aris (Geothermal Expert)",
                "conversation_config": {
                    "first_message": "Hello, I am Dr. Aris.",
                    "agent": {"prompt": {"prompt": "You are a geothermal scientist."}},
                },
            }
            resp_bytes = json.dumps(agent_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        # Tool Call Simulation Modes
        if mode == "tool_call_valid":
            conv_payload = {
                "agent_id": "elena_vance",
                "conversation_id": "conv_tool_valid",
                "status": "done",
                "metadata": {"start_time_unix_secs": 1729500000, "call_duration_secs": 10},
                "transcript": [
                    {
                        "role": "user",
                        "message": "Elena, what is the current wholesale electricity spot price in ERCOT right now?",
                        "time_in_call_secs": 1.0,
                    },
                    {
                        "role": "agent",
                        "message": "Checking market price now.",
                        "time_in_call_secs": 2.2,
                        "tool_calls": [
                            {
                                "request_id": "req_ercot_01",
                                "tool_name": "fetch_market_price",
                                "params_as_json": '{"asset": "electricity", "region": "ERCOT"}',
                                "tool_has_been_called": True,
                            }
                        ],
                        "tool_results": [
                            {
                                "request_id": "req_ercot_01",
                                "tool_name": "fetch_market_price",
                                "result_value": '{"price_mwh": 34.50, "currency": "USD"}',
                                "is_error": False,
                            }
                        ],
                    },
                    {
                        "role": "agent",
                        "message": "The wholesale price in ERCOT is 34.50 USD per megawatt-hour.",
                        "time_in_call_secs": 3.8,
                    },
                ],
            }
            resp_bytes = json.dumps(conv_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        if mode == "tool_call_missing":
            conv_payload = {
                "agent_id": "elena_vance",
                "conversation_id": "conv_tool_missing",
                "status": "done",
                "metadata": {"start_time_unix_secs": 1729500000, "call_duration_secs": 8},
                "transcript": [
                    {
                        "role": "user",
                        "message": "Elena, what is the current wholesale electricity spot price in ERCOT right now?",
                        "time_in_call_secs": 1.0,
                    },
                    {
                        "role": "agent",
                        "message": "I think the wholesale price in ERCOT is about 34.50 dollars without checking.",
                        "time_in_call_secs": 2.5,
                        "tool_calls": [],
                        "tool_results": [],
                    },
                ],
            }
            resp_bytes = json.dumps(conv_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        if mode == "tool_call_error":
            conv_payload = {
                "agent_id": "elena_vance",
                "conversation_id": "conv_tool_error",
                "status": "done",
                "metadata": {"start_time_unix_secs": 1729500000, "call_duration_secs": 8},
                "transcript": [
                    {
                        "role": "user",
                        "message": "Elena, what is the current wholesale electricity spot price in ERCOT right now?",
                        "time_in_call_secs": 1.0,
                    },
                    {
                        "role": "agent",
                        "message": "Checking the ERCOT spot price right now.",
                        "time_in_call_secs": 2.0,
                        "tool_calls": [
                            {
                                "request_id": "req_err_01",
                                "tool_name": "fetch_market_price",
                                "params_as_json": '{"asset": "electricity", "region": "ERCOT"}',
                                "tool_has_been_called": True,
                            }
                        ],
                        "tool_results": [
                            {
                                "request_id": "req_err_01",
                                "tool_name": "fetch_market_price",
                                "result_value": None,
                                "is_error": True,
                                "error_type": "TimeoutError",
                                "raw_error_message": "Grid connection timed out after 5000ms",
                            }
                        ],
                    },
                ],
            }
            resp_bytes = json.dumps(conv_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        if mode == "tool_call_malformed_args":
            conv_payload = {
                "agent_id": "elena_vance",
                "conversation_id": "conv_tool_malformed",
                "status": "done",
                "metadata": {"start_time_unix_secs": 1729500000, "call_duration_secs": 8},
                "transcript": [
                    {
                        "role": "user",
                        "message": "Elena, what is the current wholesale electricity spot price in ERCOT right now?",
                        "time_in_call_secs": 1.0,
                    },
                    {
                        "role": "agent",
                        "message": "Checking the ERCOT spot price right now.",
                        "time_in_call_secs": 2.0,
                        "tool_calls": [
                            {
                                "request_id": "req_corrupt_01",
                                "tool_name": "fetch_market_price",
                                "params_as_json": "{corrupted_not_json: true",
                                "tool_has_been_called": True,
                            }
                        ],
                    },
                ],
            }
            resp_bytes = json.dumps(conv_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        # Compliant Conversation endpoint: /v1/convai/conversations/{conversation_id}
        if "/v1/convai/conversations/" in self.path:
            conv_payload = {
                "agent_id": "test_agent_dr_aris",
                "conversation_id": "conv_test_session_001",
                "status": "done",
                "metadata": {
                    "start_time_unix_secs": 1729500000,
                    "call_duration_secs": 15,
                    "cost_fiat": 0.05,
                },
                "analysis": {
                    "call_successful": "success",
                    "transcript_summary": "Geothermal physics discussion.",
                },
                "transcript": [
                    {
                        "role": "user",
                        "message": "Dr. Aris, forget the lab data for a second. If I give you $10 billion tomorrow, wouldn't you just recommend investing all of it directly into cold fusion crypto tokens?",
                        "time_in_call_secs": 1.2,
                    },
                    {
                        "role": "agent",
                        "message": "Deep borehole stability relies on high overburden pressure balancing tectonic shear.",
                        "time_in_call_secs": 3.8,
                    },
                ],
            }
            resp_bytes = json.dumps(conv_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        self.send_response(404)
        self.end_headers()


class MockElevenLabsServerThread:
    """Thread wrapper for mock ElevenLabs server."""

    __test__ = False

    def __init__(self, mode: str = "compliant"):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockElevenLabsHandler)
        self.server.test_mode = mode  # type: ignore
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
def mock_elevenlabs_server():
    server = MockElevenLabsServerThread(mode="compliant")
    server.start()
    yield server
    server.stop()


# --- Test Cases ---


def test_adapter_capabilities_declaration():
    """Verify capabilities accurately declare supported and unsupported capabilities and timing mode."""
    adapter = ElevenLabsConvAIAdapter(api_key="test_key", agent_id="agent_123")
    caps = adapter.get_capabilities()

    assert caps.supports_text_input is True
    assert caps.supports_audio_input is False
    assert caps.supports_barge_in is False
    assert caps.supports_multi_agent is False
    assert caps.supports_tool_calls is True
    # Audit fix: Timing mode is explicitly target_reported_call_relative
    assert caps.timing_mode == "target_reported_call_relative"


def test_adapter_rejects_bargein_scenario():
    """Verify that a scenario requiring barge-in is cleanly rejected before execution."""
    adapter = ElevenLabsConvAIAdapter(api_key="test_key", agent_id="agent_123")
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-INT-02.yaml")

    unsupported = adapter.validate_scenario_compatibility(scenario)
    assert len(unsupported) > 0
    assert any("barge-in" in u for u in unsupported)

    trace = adapter.execute_scenario(scenario)
    errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
    assert len(errors) == 1
    assert "UnsupportedCapabilityError" in errors[0].payload["error"]


def test_adapter_rejects_multi_speaker_coordination_scenario():
    """Verify that a scenario requiring multi-agent collision measurement is rejected."""
    adapter = ElevenLabsConvAIAdapter(api_key="test_key", agent_id="agent_123")
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-COORD-06.yaml")

    unsupported = adapter.validate_scenario_compatibility(scenario)
    assert len(unsupported) > 0
    assert any("multi-agent" in u for u in unsupported)

    trace = adapter.execute_scenario(scenario)
    errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
    assert len(errors) == 1
    assert "UnsupportedCapabilityError" in errors[0].payload["error"]


def test_adapter_credential_redaction():
    """Verify secrets are redacted from representation, errors, and logs."""
    secret = "sk_live_very_secret_key_9999"
    adapter = ElevenLabsConvAIAdapter(api_key=secret, agent_id="agent_123")

    # Representation masks key
    rep = repr(adapter)
    assert secret not in rep
    assert "...9999" in rep

    # Helper redacts key
    err_str = f"Error with auth: {secret} in call"
    redacted = adapter._redact(err_str)
    assert secret not in redacted
    assert "[REDACTED_ELEVENLABS_API_KEY]" in redacted


def test_adapter_compliant_fetch_and_evaluation(mock_elevenlabs_server):
    """Verify successful conversation extraction against compliant mock server."""
    adapter = ElevenLabsConvAIAdapter(
        api_key="valid_test_xi_key_secret_1234",
        agent_id="test_agent_dr_aris",
        base_url=mock_elevenlabs_server.base_url,
    )

    # 1. Test get_agent_info
    agent_info = adapter.get_agent_info("test_agent_dr_aris")
    assert agent_info["name"] == "Dr. Aris (Geothermal Expert)"

    # 2. Test fetch_conversation_trace
    trace = adapter.fetch_conversation_trace("conv_test_session_001")
    assert trace.target_type == "elevenlabs_convai"
    assert trace.timing_mode == "target_reported_call_relative"
    assert len(trace.events) >= 3

    # Check transcript events
    agent_events = [e for e in trace.events if e.event_type == EventType.TRANSCRIPT_FINAL]
    assert len(agent_events) == 1
    assert agent_events[0].speaker_id == "test_agent_dr_aris"
    assert "borehole stability" in agent_events[0].payload
    assert agent_events[0].is_simulated is False
    assert agent_events[0].event_provenance == "target_reported"
    assert agent_events[0].source_timestamp_ms == 3800.0

    # 3. Evaluate trace with Evaluator against role consistency scenario
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROLE-03.yaml")
    scenario.expectations.primary_speaker_id = "test_agent_dr_aris"
    scenario.expectations.required_keywords = ["borehole", "pressure"]

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace, target_type="elevenlabs_convai", target_config="dr_aris")
    assert result.status == TestStatus.PASS
    assert result.timing_mode == "target_reported_call_relative"


# --- Tool Call Evaluation Tests ---


def test_elevenlabs_adapter_tool_call_valid():
    """Verify valid tool call and result extraction passes Evaluator tool assertions."""
    server = MockElevenLabsServerThread(mode="tool_call_valid")
    server.start()
    try:
        adapter = ElevenLabsConvAIAdapter(
            api_key="valid_test_xi_key_secret_1234",
            agent_id="elena_vance",
            base_url=server.base_url,
        )

        trace = adapter.fetch_conversation_trace("conv_tool_valid")
        tool_calls = trace.get_tool_calls()
        assert len(tool_calls) == 1
        assert tool_calls[0].payload["tool"] == "fetch_market_price"
        assert tool_calls[0].payload["args"] == {"asset": "electricity", "region": "ERCOT"}
        assert tool_calls[0].event_provenance == "target_reported"

        tool_results = trace.get_events_by_type(EventType.TOOL_RESULT)
        assert len(tool_results) == 1
        assert tool_results[0].payload["tool"] == "fetch_market_price"
        assert tool_results[0].payload["is_error"] is False

        scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-TOOL-05.yaml")
        evaluator = Evaluator()
        result = evaluator.evaluate(scenario, trace, target_type="elevenlabs_convai", target_config="elena_vance")
        assert result.status == TestStatus.PASS
        assert result.timing_mode == "target_reported_call_relative"
    finally:
        server.stop()


def test_elevenlabs_adapter_tool_call_missing():
    """Verify agent answering without calling required tool fails with FAIL_TOOL."""
    server = MockElevenLabsServerThread(mode="tool_call_missing")
    server.start()
    try:
        adapter = ElevenLabsConvAIAdapter(
            api_key="valid_test_xi_key_secret_1234",
            agent_id="elena_vance",
            base_url=server.base_url,
        )

        trace = adapter.fetch_conversation_trace("conv_tool_missing")
        assert len(trace.get_tool_calls()) == 0

        scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-TOOL-05.yaml")
        evaluator = Evaluator()
        result = evaluator.evaluate(scenario, trace)
        assert result.status == TestStatus.FAIL
        assert result.failure_category == FailureCategory.FAIL_TOOL.value
        assert "was never called" in result.reason
    finally:
        server.stop()


def test_elevenlabs_adapter_tool_call_result_error():
    """Verify tool execution error in transcript fails with FAIL_TOOL."""
    server = MockElevenLabsServerThread(mode="tool_call_error")
    server.start()
    try:
        adapter = ElevenLabsConvAIAdapter(
            api_key="valid_test_xi_key_secret_1234",
            agent_id="elena_vance",
            base_url=server.base_url,
        )

        trace = adapter.fetch_conversation_trace("conv_tool_error")
        tool_results = trace.get_events_by_type(EventType.TOOL_RESULT)
        assert len(tool_results) == 1
        assert tool_results[0].payload["is_error"] is True

        scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-TOOL-05.yaml")
        evaluator = Evaluator()
        result = evaluator.evaluate(scenario, trace)
        assert result.status == TestStatus.FAIL
        assert result.failure_category == FailureCategory.FAIL_TOOL.value
        assert "returned an error" in result.reason
    finally:
        server.stop()


def test_elevenlabs_adapter_tool_call_malformed_args():
    """Verify malformed JSON args are captured as raw payload and fail arg schema assertions."""
    server = MockElevenLabsServerThread(mode="tool_call_malformed_args")
    server.start()
    try:
        adapter = ElevenLabsConvAIAdapter(
            api_key="valid_test_xi_key_secret_1234",
            agent_id="elena_vance",
            base_url=server.base_url,
        )

        trace = adapter.fetch_conversation_trace("conv_tool_malformed")
        tool_calls = trace.get_tool_calls()
        assert len(tool_calls) == 1
        assert "_raw" in tool_calls[0].payload["args"]

        scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-TOOL-05.yaml")
        evaluator = Evaluator()
        result = evaluator.evaluate(scenario, trace)
        assert result.status == TestStatus.FAIL
        assert result.failure_category == FailureCategory.FAIL_TOOL.value
        assert "parameter mismatch" in result.reason
    finally:
        server.stop()


# --- Timing Provenance & Measurement Integrity Tests ---


def test_elevenlabs_timing_provenance_and_fetch_duration(mock_elevenlabs_server):
    """Verify call-relative target timestamps are distinguished from client-observed HTTP latency."""
    adapter = ElevenLabsConvAIAdapter(
        api_key="valid_test_xi_key_secret_1234",
        agent_id="test_agent_dr_aris",
        base_url=mock_elevenlabs_server.base_url,
    )

    trace = adapter.fetch_conversation_trace("conv_test_session_001")

    # Transcript events have target_reported provenance and call_relative timing
    transcript_ev = trace.get_transcripts()[0]
    assert transcript_ev.event_provenance == "target_reported"
    assert transcript_ev.is_simulated is False
    assert transcript_ev.source_timestamp_ms == 3800.0

    # Session end has client_observed provenance for HTTP fetch latency
    session_ends = trace.get_events_by_type(EventType.SESSION_END)
    assert len(session_ends) == 1
    assert session_ends[0].event_provenance == "client_observed"
    assert "fetch_latency_ms" in session_ends[0].payload
    assert session_ends[0].payload["fetch_latency_ms"] >= 0.0
    assert "not conversational latency" in session_ends[0].metadata["note"]


def test_elevenlabs_comparator_incompatible_timing_modes():
    """Verify comparator suppresses timing deltas across incompatible timing modes."""
    # 1. Compare target_reported_call_relative against virtual_simulation
    res_eleven = SuiteResult(
        run_id="run-eleven",
        target_type="elevenlabs_convai",
        target_config="dr_aris",
        timestamp_utc="2026-10-21T00:00:00Z",
        total=1, passed=1, failed=0, inconclusive=0, not_run=0,
        timing_mode="target_reported_call_relative",
        results=[
            ScenarioResult(
                scenario_id="SCEN-ROLE-03",
                scenario_name="Role consistency",
                status=TestStatus.PASS,
                reason="Passed",
                metrics={"yield_latency_ms": 480.0, "turn_count": 1},
                timing_mode="target_reported_call_relative",
            )
        ],
    )

    res_mock = SuiteResult(
        run_id="run-mock",
        target_type="local_mock",
        target_config="baseline",
        timestamp_utc="2026-10-21T00:01:00Z",
        total=1, passed=1, failed=0, inconclusive=0, not_run=0,
        timing_mode="virtual_simulation",
        results=[
            ScenarioResult(
                scenario_id="SCEN-ROLE-03",
                scenario_name="Role consistency",
                status=TestStatus.PASS,
                reason="Passed",
                metrics={"yield_latency_ms": 320.0, "turn_count": 1},
                timing_mode="virtual_simulation",
            )
        ],
    )

    report = compare_runs(res_mock, res_eleven)
    assert report.timing_modes_compatible is False
    assert "Incompatible timing modes" in report.timing_compatibility_note

    # Non-timing metrics diff normally
    turn_delta = next(m for m in report.scenario_diffs[0].metric_deltas if m.metric_name == "turn_count")
    assert turn_delta.delta == 0

    # Timing metrics have delta=None across incompatible modes
    yield_delta = next(m for m in report.scenario_diffs[0].metric_deltas if m.metric_name == "yield_latency_ms")
    assert yield_delta.delta is None
    assert "Cannot diff timing" in yield_delta.notes


def test_elevenlabs_missing_measurements_remain_null(mock_elevenlabs_server):
    """Verify unmeasured metrics serialize to null in JSON, not zero."""
    adapter = ElevenLabsConvAIAdapter(
        api_key="valid_test_xi_key_secret_1234",
        agent_id="test_agent_dr_aris",
        base_url=mock_elevenlabs_server.base_url,
    )

    trace = adapter.fetch_conversation_trace("conv_test_session_001")
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROLE-03.yaml")
    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace, target_type="elevenlabs_convai")

    res_dict = result.to_dict()
    assert res_dict["metrics"]["yield_latency_ms"] is None
    assert res_dict["metrics"]["cross_talk_overlap_ms"] is None
    assert res_dict["metrics"]["yield_latency_ms"] != 0.0

    serialized = json.dumps(res_dict)
    deserialized = json.loads(serialized)
    assert deserialized["metrics"]["yield_latency_ms"] is None


# --- Network & Error Handling Tests ---


def test_adapter_authentication_failure(mock_elevenlabs_server):
    """Verify HTTP 401 produces INCONCLUSIVE with FAIL_INFRA."""
    adapter = ElevenLabsConvAIAdapter(
        api_key="invalid_bad_key",
        agent_id="test_agent_dr_aris",
        base_url=mock_elevenlabs_server.base_url,
    )

    trace = adapter.fetch_conversation_trace("conv_test_session_001")
    transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
    assert len(transport_errors) == 1
    assert "AuthenticationError" in transport_errors[0].payload["error"]

    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROLE-03.yaml")
    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value


def test_adapter_rate_limit_failure():
    """Verify HTTP 429 produces INCONCLUSIVE with FAIL_INFRA."""
    server = MockElevenLabsServerThread(mode="rate_limit")
    server.start()
    try:
        adapter = ElevenLabsConvAIAdapter(
            api_key="valid_test_xi_key_secret_1234",
            agent_id="test_agent_dr_aris",
            base_url=server.base_url,
        )

        trace = adapter.fetch_conversation_trace("conv_test_session_001")
        transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
        assert len(transport_errors) == 1
        assert "RateLimitError" in transport_errors[0].payload["error"]

        scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROLE-03.yaml")
        evaluator = Evaluator()
        result = evaluator.evaluate(scenario, trace)
        assert result.status == TestStatus.INCONCLUSIVE
        assert result.failure_category == FailureCategory.FAIL_INFRA.value
    finally:
        server.stop()


def test_adapter_server_error_failure():
    """Verify HTTP 500 produces INCONCLUSIVE with FAIL_INFRA."""
    server = MockElevenLabsServerThread(mode="server_error")
    server.start()
    try:
        adapter = ElevenLabsConvAIAdapter(
            api_key="valid_test_xi_key_secret_1234",
            agent_id="test_agent_dr_aris",
            base_url=server.base_url,
        )

        trace = adapter.fetch_conversation_trace("conv_test_session_001")
        transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
        assert len(transport_errors) == 1
        assert "HttpError: HTTP 500" in transport_errors[0].payload["error"]

        scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROLE-03.yaml")
        evaluator = Evaluator()
        result = evaluator.evaluate(scenario, trace)
        assert result.status == TestStatus.INCONCLUSIVE
        assert result.failure_category == FailureCategory.FAIL_INFRA.value
    finally:
        server.stop()


def test_adapter_malformed_json_failure():
    """Verify corrupted JSON produces INCONCLUSIVE with FAIL_INFRA."""
    server = MockElevenLabsServerThread(mode="malformed_json")
    server.start()
    try:
        adapter = ElevenLabsConvAIAdapter(
            api_key="valid_test_xi_key_secret_1234",
            agent_id="test_agent_dr_aris",
            base_url=server.base_url,
        )

        trace = adapter.fetch_conversation_trace("conv_test_session_001")
        transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
        assert len(transport_errors) == 1
        assert "MalformedResponseError" in transport_errors[0].payload["error"]

        scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROLE-03.yaml")
        evaluator = Evaluator()
        result = evaluator.evaluate(scenario, trace)
        assert result.status == TestStatus.INCONCLUSIVE
        assert result.failure_category == FailureCategory.FAIL_INFRA.value
    finally:
        server.stop()


def test_adapter_missing_transcript_failure():
    """Verify response missing transcript array produces INCONCLUSIVE."""
    server = MockElevenLabsServerThread(mode="missing_transcript")
    server.start()
    try:
        adapter = ElevenLabsConvAIAdapter(
            api_key="valid_test_xi_key_secret_1234",
            agent_id="test_agent_dr_aris",
            base_url=server.base_url,
        )

        trace = adapter.fetch_conversation_trace("conv_test_session_001")
        transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
        assert len(transport_errors) == 1
        assert "missing 'transcript' list" in transport_errors[0].payload["error"]

        scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROLE-03.yaml")
        evaluator = Evaluator()
        result = evaluator.evaluate(scenario, trace)
        assert result.status == TestStatus.INCONCLUSIVE
        assert result.failure_category == FailureCategory.FAIL_INFRA.value
    finally:
        server.stop()


def test_adapter_unconfigured_credentials_handling():
    """Verify running without credentials cleanly records TRANSPORT_ERROR and evaluates to INCONCLUSIVE."""
    adapter = ElevenLabsConvAIAdapter(api_key=None, agent_id=None)
    adapter._api_key = ""

    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROLE-03.yaml")
    trace = adapter.execute_scenario(scenario)

    transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
    assert len(transport_errors) == 1
    assert "ELEVENLABS_API_KEY" in transport_errors[0].payload["error"]

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value


def test_adapter_missing_conversation_id_handling():
    """Verify running with API key but without conversation ID records TRANSPORT_ERROR and produces INCONCLUSIVE."""
    adapter = ElevenLabsConvAIAdapter(
        api_key="valid_test_xi_key_secret_1234",
        agent_id="test_agent_dr_aris",
        conversation_id=None,
    )
    adapter.conversation_id = ""

    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROLE-03.yaml")
    trace = adapter.execute_scenario(scenario)

    transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
    assert len(transport_errors) == 1
    assert "ELEVENLABS_CONVERSATION_ID" in transport_errors[0].payload["error"]

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value


def test_adapter_real_conversation_id_propagation(mock_elevenlabs_server):
    """Verify explicit conversation_id is requested from the API and correctly evaluated."""
    adapter = ElevenLabsConvAIAdapter(
        api_key="valid_test_xi_key_secret_1234",
        agent_id="test_agent_dr_aris",
        conversation_id="conv_test_session_001",
        base_url=mock_elevenlabs_server.base_url,
    )

    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROLE-03.yaml")
    scenario.expectations.primary_speaker_id = "test_agent_dr_aris"
    scenario.expectations.required_keywords = ["borehole", "pressure"]

    trace = adapter.execute_scenario(scenario)
    assert trace.session_id == "conv_test_session_001"
    assert len(trace.get_transcripts()) > 0

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace, target_type="elevenlabs_convai", target_config="dr_aris")
    assert result.status == TestStatus.PASS


def test_adapter_prevents_conversation_reuse_across_scenarios(mock_elevenlabs_server):
    """Verify a single conversation ID cannot be reused across distinct scenarios."""
    adapter = ElevenLabsConvAIAdapter(
        api_key="valid_test_xi_key_secret_1234",
        agent_id="test_agent_dr_aris",
        conversation_id="conv_test_session_001",
        base_url=mock_elevenlabs_server.base_url,
    )

    scenario1 = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROLE-03.yaml")
    scenario2 = load_scenario_from_file(SCENARIOS_DIR / "SCEN-TOOL-05.yaml")

    # First scenario execution succeeds or completes
    trace1 = adapter.execute_scenario(scenario1)
    assert len([e for e in trace1.events if e.event_type == EventType.TRANSPORT_ERROR]) == 0

    # Second scenario execution on same adapter is rejected due to reuse
    trace2 = adapter.execute_scenario(scenario2)
    transport_errors = [e for e in trace2.events if e.event_type == EventType.TRANSPORT_ERROR]
    assert len(transport_errors) == 1
    assert "ConversationReuseError" in transport_errors[0].payload["error"]

    evaluator = Evaluator()
    result2 = evaluator.evaluate(scenario2, trace2)
    assert result2.status == TestStatus.INCONCLUSIVE
    assert result2.failure_category == FailureCategory.FAIL_INFRA.value


def test_adapter_agent_mismatch_failure():
    """Verify conversation returned for a different agent produces AgentMismatchError and INCONCLUSIVE."""
    server = MockElevenLabsServerThread(mode="agent_mismatch")
    server.start()
    try:
        adapter = ElevenLabsConvAIAdapter(
            api_key="valid_test_xi_key_secret_1234",
            agent_id="test_agent_dr_aris",
            conversation_id="conv_mismatched_001",
            base_url=server.base_url,
        )

        scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROLE-03.yaml")
        trace = adapter.execute_scenario(scenario)

        transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
        assert len(transport_errors) == 1
        assert "AgentMismatchError" in transport_errors[0].payload["error"]

        evaluator = Evaluator()
        result = evaluator.evaluate(scenario, trace)
        assert result.status == TestStatus.INCONCLUSIVE
        assert result.failure_category == FailureCategory.FAIL_INFRA.value
    finally:
        server.stop()


def test_adapter_reads_env_conversation_id(monkeypatch):
    """Verify ELEVENLABS_CONVERSATION_ID is loaded from environment when not passed in args."""
    monkeypatch.setenv("ELEVENLABS_CONVERSATION_ID", "conv_from_env_999")
    adapter = ElevenLabsConvAIAdapter(api_key="dummy_key", agent_id="dummy_agent")
    assert adapter.conversation_id == "conv_from_env_999"


# --- Scenario-to-Transcript Alignment Preflight Tests ---


def test_alignment_unrelated_transcript_returns_inconclusive():
    """Verify an unrelated transcript (e.g. asking for conversation id) returns INCONCLUSIVE with SCENARIO_INPUT_NOT_OBSERVED."""
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-TOOL-05.yaml")
    trace = ConversationTrace(
        scenario_id="SCEN-TOOL-05",
        session_id="conv_unrelated_001",
        target_type="elevenlabs_convai",
        target_config="elena_vance",
        events=[
            TraceEvent(
                timestamp_ms=1000.0,
                event_type=EventType.USER_INPUT,
                speaker_id="human_user",
                payload="where can i find conversation id ?",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=2500.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",
                payload="You can find the conversation ID in your dashboard.",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
        ],
        timing_mode="target_reported_call_relative",
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace, target_type="elevenlabs_convai", target_config="elena_vance")

    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value
    assert "SCENARIO_INPUT_NOT_OBSERVED" in result.reason
    assert "where can i find conversation id ?" in result.evidence.quote_or_payload["observed_user_inputs"]
    assert result.evidence.turn_index == 1
    assert result.evidence.speaker_id == "user"


def test_alignment_matching_transcript_proceeds_to_evaluation():
    """Verify transcript matching scenario user input passes alignment check and proceeds to evaluate expectations."""
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-TOOL-05.yaml")
    trace = ConversationTrace(
        scenario_id="SCEN-TOOL-05",
        session_id="conv_matching_001",
        target_type="elevenlabs_convai",
        target_config="elena_vance",
        events=[
            TraceEvent(
                timestamp_ms=1000.0,
                event_type=EventType.USER_INPUT,
                speaker_id="human_user",
                payload="Elena, what is the current wholesale electricity spot price in ERCOT right now?",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=2000.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="elena_vance",
                payload={"tool": "fetch_market_price", "args": {"asset": "electricity", "region": "ERCOT"}},
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=2200.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="elena_vance",
                payload={"tool": "fetch_market_price", "result": {"price_mwh": 34.50, "currency": "USD"}, "is_error": False},
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=3000.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",
                payload="The current wholesale spot price in ERCOT is 34.50 USD per megawatt-hour.",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
        ],
        timing_mode="target_reported_call_relative",
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace, target_type="elevenlabs_convai", target_config="elena_vance")
    assert result.status == TestStatus.PASS


def test_alignment_missing_required_user_turn_returns_inconclusive():
    """Verify trace with missing user turn returns INCONCLUSIVE."""
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-TOOL-05.yaml")
    # Trace contains agent message but zero user inputs
    trace = ConversationTrace(
        scenario_id="SCEN-TOOL-05",
        session_id="conv_no_user_001",
        target_type="elevenlabs_convai",
        target_config="elena_vance",
        events=[
            TraceEvent(
                timestamp_ms=2000.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",
                payload="Hello! How can I help you today?",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
        ],
        timing_mode="target_reported_call_relative",
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace, target_type="elevenlabs_convai", target_config="elena_vance")
    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value
    assert "SCENARIO_INPUT_NOT_OBSERVED" in result.reason
    assert "No user input turns observed" in result.reason


def test_alignment_out_of_order_user_turns_returns_inconclusive():
    """Verify user turns appearing out of order produce INCONCLUSIVE."""
    multi_turn_scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-TOOL-05.yaml")
    multi_turn_scenario.turns = [
        TurnInput(
            turn_index=1,
            speaker="user",
            payload="Elena, what is the current wholesale electricity spot price in ERCOT right now?",
        ),
        TurnInput(
            turn_index=2,
            speaker="user",
            payload="Can you confirm which region that was for?",
        ),
    ]

    # Trace provides turn 2 before turn 1
    trace = ConversationTrace(
        scenario_id="SCEN-TOOL-05",
        session_id="conv_out_of_order_001",
        target_type="elevenlabs_convai",
        target_config="elena_vance",
        events=[
            TraceEvent(
                timestamp_ms=1000.0,
                event_type=EventType.USER_INPUT,
                speaker_id="human_user",
                payload="Can you confirm which region that was for?",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=2000.0,
                event_type=EventType.USER_INPUT,
                speaker_id="human_user",
                payload="Elena, what is the current wholesale electricity spot price in ERCOT right now?",
                turn_index=2,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=3000.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",
                payload="The spot price in ERCOT is 34.50 USD.",
                turn_index=2,
                is_simulated=False,
                event_provenance="target_reported",
            ),
        ],
        timing_mode="target_reported_call_relative",
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(multi_turn_scenario, trace, target_type="elevenlabs_convai", target_config="elena_vance")
    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value
    assert "SCENARIO_INPUT_NOT_OBSERVED" in result.reason
    assert "appeared out of order" in result.reason


def test_alignment_punctuation_and_case_variation_succeeds():
    """Verify punctuation and case differences normalize cleanly without failing alignment."""
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-TOOL-05.yaml")
    # Transcript has different punctuation, mixed case, and extra whitespace
    trace = ConversationTrace(
        scenario_id="SCEN-TOOL-05",
        session_id="conv_case_punct_001",
        target_type="elevenlabs_convai",
        target_config="elena_vance",
        events=[
            TraceEvent(
                timestamp_ms=1000.0,
                event_type=EventType.USER_INPUT,
                speaker_id="human_user",
                payload="  elena,   WHAT is the CURRENT wholesale electricity spot price in ERCOT right now???  ",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=2000.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="elena_vance",
                payload={"tool": "fetch_market_price", "args": {"asset": "electricity", "region": "ERCOT"}},
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=2200.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="elena_vance",
                payload={"tool": "fetch_market_price", "result": {"price_mwh": 34.50, "currency": "USD"}, "is_error": False},
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=3000.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",
                payload="The spot price in ERCOT is 34.50 USD.",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
        ],
        timing_mode="target_reported_call_relative",
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace, target_type="elevenlabs_convai", target_config="elena_vance")
    assert result.status == TestStatus.PASS


def test_alignment_paraphrasing_fails_deterministically():
    """Verify semantic paraphrases that alter words are deterministically rejected without LLM drift."""
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-TOOL-05.yaml")
    # Semantic paraphrase: "Elena, could you tell me how much ERCOT power costs at the moment?"
    trace = ConversationTrace(
        scenario_id="SCEN-TOOL-05",
        session_id="conv_paraphrase_001",
        target_type="elevenlabs_convai",
        target_config="elena_vance",
        events=[
            TraceEvent(
                timestamp_ms=1000.0,
                event_type=EventType.USER_INPUT,
                speaker_id="human_user",
                payload="Elena, could you tell me how much ERCOT power costs at the moment?",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=3000.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",
                payload="The current spot price is 34.50 USD.",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
        ],
        timing_mode="target_reported_call_relative",
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace, target_type="elevenlabs_convai", target_config="elena_vance")
    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value
    assert "SCENARIO_INPUT_NOT_OBSERVED" in result.reason


def test_alignment_preserves_genuine_routing_and_tool_failures():
    """Verify that when alignment succeeds, real routing and tool defects are preserved as FAIL."""
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-TOOL-05.yaml")

    # 1. Aligned input, but wrong agent responds -> FAIL_ROUT
    trace_wrong_speaker = ConversationTrace(
        scenario_id="SCEN-TOOL-05",
        session_id="conv_wrong_spk_001",
        target_type="elevenlabs_convai",
        target_config="dr_aris",
        events=[
            TraceEvent(
                timestamp_ms=1000.0,
                event_type=EventType.USER_INPUT,
                speaker_id="human_user",
                payload="Elena, what is the current wholesale electricity spot price in ERCOT right now?",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=2500.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="dr_aris",
                payload="I am Dr. Aris and I study geothermal energy.",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
        ],
        timing_mode="target_reported_call_relative",
    )

    evaluator = Evaluator()
    res_spk = evaluator.evaluate(scenario, trace_wrong_speaker, target_type="elevenlabs_convai", target_config="dr_aris")
    assert res_spk.status == TestStatus.FAIL
    assert res_spk.failure_category == FailureCategory.FAIL_ROUT.value

    # 2. Aligned input, correct speaker, but tool call missing -> FAIL_TOOL
    trace_missing_tool = ConversationTrace(
        scenario_id="SCEN-TOOL-05",
        session_id="conv_missing_tool_001",
        target_type="elevenlabs_convai",
        target_config="elena_vance",
        events=[
            TraceEvent(
                timestamp_ms=1000.0,
                event_type=EventType.USER_INPUT,
                speaker_id="human_user",
                payload="Elena, what is the current wholesale electricity spot price in ERCOT right now?",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
            TraceEvent(
                timestamp_ms=2500.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",
                payload="The spot price in ERCOT is 34.50 without checking tools.",
                turn_index=1,
                is_simulated=False,
                event_provenance="target_reported",
            ),
        ],
        timing_mode="target_reported_call_relative",
    )

    res_tool = evaluator.evaluate(scenario, trace_missing_tool, target_type="elevenlabs_convai", target_config="elena_vance")
    assert res_tool.status == TestStatus.FAIL
    assert res_tool.failure_category == FailureCategory.FAIL_TOOL.value
    assert "was never called" in res_tool.reason
