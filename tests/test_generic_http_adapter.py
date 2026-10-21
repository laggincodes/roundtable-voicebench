"""Integration and error handling tests for GenericHttpAdapter."""

from pathlib import Path
import pytest

from voicebench.adapters.http import GenericHttpAdapter
from voicebench.evaluator import Evaluator
from voicebench.models import AdapterCapabilities, EventType, FailureCategory, TestStatus
from voicebench.parser import load_scenario_from_file
from voicebench.targets.reference_http_server import TestServerThread

SCENARIOS_DIR = Path(__file__).parent.parent / "scenarios" / "roundtable"


@pytest.fixture
def compliant_server():
    """Start local reference HTTP server in compliant mode."""
    server = TestServerThread(port=0, mode="compliant")
    server.start()
    yield server
    server.stop()


def test_http_adapter_compliant_execution(compliant_server):
    """Verify HTTP adapter successfully executes a scenario against the reference server."""
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROUT-01.yaml")
    adapter = GenericHttpAdapter(endpoint_url=compliant_server.url, timeout_seconds=5.0)

    trace = adapter.execute_scenario(scenario)
    assert trace.target_type == "generic_http"
    assert len(trace.events) >= 3

    # Verify that events are labeled as real (not simulated)
    final_transcript = trace.get_transcripts()[0]
    assert final_transcript.is_simulated is False
    assert final_transcript.speaker_id == "dr_aris"
    assert final_transcript.client_timestamp_ms is not None
    assert final_transcript.client_timestamp_ms > 0.0

    # Evaluate trace
    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace, target_type="generic_http", target_config="reference_http")
    assert result.status == TestStatus.PASS


def test_http_adapter_connection_failure():
    """Verify connecting to an unreachable port produces INCONCLUSIVE with ConnectionError."""
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROUT-01.yaml")
    # Port 59123 is closed
    adapter = GenericHttpAdapter(endpoint_url="http://127.0.0.1:59123/execute", timeout_seconds=1.0)

    trace = adapter.execute_scenario(scenario)
    transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
    assert len(transport_errors) == 1
    assert any(err in transport_errors[0].payload["error"] for err in ("ConnectionError", "TimeoutError"))

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value


def test_http_adapter_timeout_handling():
    """Verify server timeout produces INCONCLUSIVE with TimeoutError."""
    timeout_server = TestServerThread(port=0, mode="timeout")
    timeout_server.start()
    try:
        scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROUT-01.yaml")
        # Set low client timeout of 0.5s while server sleeps 3.0s
        adapter = GenericHttpAdapter(endpoint_url=timeout_server.url, timeout_seconds=0.5)

        trace = adapter.execute_scenario(scenario)
        transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
        assert len(transport_errors) == 1
        assert "TimeoutError" in transport_errors[0].payload["error"]

        evaluator = Evaluator()
        result = evaluator.evaluate(scenario, trace)
        assert result.status == TestStatus.INCONCLUSIVE
        assert result.failure_category == FailureCategory.FAIL_INFRA.value
        assert "timed out" in result.reason.lower()
    finally:
        timeout_server.stop()


def test_http_adapter_http_500_handling():
    """Verify HTTP 500 error produces INCONCLUSIVE with status code."""
    server = TestServerThread(port=0, mode="500")
    server.start()
    try:
        scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROUT-01.yaml")
        adapter = GenericHttpAdapter(endpoint_url=server.url, timeout_seconds=3.0)

        trace = adapter.execute_scenario(scenario)
        transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
        assert len(transport_errors) == 1
        assert "HTTP 500" in transport_errors[0].payload["error"]

        evaluator = Evaluator()
        result = evaluator.evaluate(scenario, trace)
        assert result.status == TestStatus.INCONCLUSIVE
        assert result.failure_category == FailureCategory.FAIL_INFRA.value
    finally:
        server.stop()


def test_http_adapter_malformed_json_handling():
    """Verify malformed JSON response body produces INCONCLUSIVE with MalformedResponseError."""
    server = TestServerThread(port=0, mode="malformed_json")
    server.start()
    try:
        scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROUT-01.yaml")
        adapter = GenericHttpAdapter(endpoint_url=server.url, timeout_seconds=3.0)

        trace = adapter.execute_scenario(scenario)
        transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
        assert len(transport_errors) == 1
        assert "MalformedResponseError" in transport_errors[0].payload["error"]

        evaluator = Evaluator()
        result = evaluator.evaluate(scenario, trace)
        assert result.status == TestStatus.INCONCLUSIVE
        assert result.failure_category == FailureCategory.FAIL_INFRA.value
    finally:
        server.stop()


def test_http_adapter_missing_events_field_handling():
    """Verify response lacking 'events' array produces INCONCLUSIVE."""
    server = TestServerThread(port=0, mode="missing_events")
    server.start()
    try:
        scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-ROUT-01.yaml")
        adapter = GenericHttpAdapter(endpoint_url=server.url, timeout_seconds=3.0)

        trace = adapter.execute_scenario(scenario)
        transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
        assert len(transport_errors) == 1
        assert "must contain an 'events' list" in transport_errors[0].payload["error"]

        evaluator = Evaluator()
        result = evaluator.evaluate(scenario, trace)
        assert result.status == TestStatus.INCONCLUSIVE
    finally:
        server.stop()


def test_http_adapter_unsupported_capability_rejection():
    """Verify adapter declaring supports_barge_in=False cleanly rejects SCEN-INT-02."""
    scenario = load_scenario_from_file(SCENARIOS_DIR / "SCEN-INT-02.yaml")

    limited_caps = AdapterCapabilities(
        supports_text_input=True,
        supports_audio_input=False,
        supports_barge_in=False,  # Explicitly disabled!
        supports_tool_calls=True,
        supports_multi_agent=True,
        timing_mode="observed_wall_clock",
    )
    adapter = GenericHttpAdapter(
        endpoint_url="http://localhost:8080/execute",
        capabilities=limited_caps,
    )

    trace = adapter.execute_scenario(scenario)
    transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
    assert len(transport_errors) == 1
    assert "UnsupportedCapabilityError" in transport_errors[0].payload["error"]

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.INCONCLUSIVE
    assert "UnsupportedCapabilityError" in result.reason
