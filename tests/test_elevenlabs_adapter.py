"""Deterministic offline tests for ElevenLabsConvAIAdapter."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import pytest

from voicebench.adapters.elevenlabs import ElevenLabsConvAIAdapter
from voicebench.comparator import compare_runs
from voicebench.evaluator import Evaluator
from voicebench.models import (
    EventType,
    FailureCategory,
    ScenarioExpectations,
    SuiteResult,
    TestStatus,
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

        # 2. Mode simulation
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
                        "message": "Dr. Aris, can you explain the physics behind deep borehole stability?",
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
    """Verify capabilities accurately declare supported and unsupported capabilities."""
    adapter = ElevenLabsConvAIAdapter(api_key="test_key", agent_id="agent_123")
    caps = adapter.get_capabilities()

    assert caps.supports_text_input is True
    assert caps.supports_audio_input is False
    assert caps.supports_barge_in is False
    assert caps.supports_multi_agent is False
    assert caps.supports_tool_calls is True
    assert caps.timing_mode == "observed_wall_clock"


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
    # Adapt expected speaker for single-agent evaluation
    scenario.expectations.primary_speaker_id = "test_agent_dr_aris"
    scenario.expectations.required_keywords = ["borehole", "pressure"]

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace, target_type="elevenlabs_convai", target_config="dr_aris")
    assert result.status == TestStatus.PASS


def test_adapter_authentication_failure(mock_elevenlabs_server):
    """Verify HTTP 401 produces INCONCLUSIVE with FAIL_INFRA."""
    # Pass invalid key
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
    # Manually ensure no env vars
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
