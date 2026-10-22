"""Deterministic unit and regression tests for real ElevenLabs conversation evaluation.

Verifies the 12 requirements from VoiceBench specification:
1. Valid conversation ID retrieves expected transcript.
2. Requested ID is passed unchanged to the retrieval endpoint.
3. Returned ID is checked against the requested ID.
4. Real transcript reaches the evaluation engine.
5. Actual transcript content appears in resulting evidence.
6. Tool calls and tool errors are preserved when present.
7. Invalid IDs and HTTP errors do not trigger fixture fallbacks.
8. Processing status does not produce a false completed evaluation.
9. Empty transcripts do not receive fabricated PASS results.
10. ElevenLabs agent mismatch is handled correctly.
11. Later evaluation cannot display previous conversation data.
12. Frontend API renders actual retrieval and evaluation errors.
13. Live integration procedure (authenticated when credentials available).
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from voicebench.adapters.elevenlabs import ElevenLabsConvAIAdapter
from voicebench.demo_app import DemoAppHandler, handle_evaluate_elevenlabs
from voicebench.evaluator import Evaluator
from voicebench.models import (
    ConversationTrace,
    EventType,
    FailureCategory,
    Scenario,
    ScenarioExpectations,
    TestStatus,
    TurnInput,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class MockConvAiHandler(BaseHTTPRequestHandler):
    """Configurable local mock HTTP server simulating official ElevenLabs ConvAI REST API."""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        server: MockConvAiServer = self.server  # type: ignore
        server.recorded_requests.append({
            "path": self.path,
            "headers": dict(self.headers),
        })

        api_key = self.headers.get("xi-api-key", "")
        if server.require_api_key and api_key != server.expected_api_key:
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail": {"status": "unauthorized", "message": "Invalid API key"}}')
            return

        # Route mode overrides
        mode = server.current_mode

        if mode == "404_not_found":
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail": {"status": "not_found", "message": "Conversation not found"}}')
            return

        if mode == "429_rate_limit":
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail": {"status": "quota_exceeded", "message": "Monthly usage limit reached"}}')
            return

        if mode == "500_server_error":
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail": {"status": "server_error", "message": "Internal gateway failure"}}')
            return

        if mode == "malformed_json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"incomplete": [')
            return

        if mode == "id_mismatch":
            body = {
                "agent_id": server.agent_id,
                "conversation_id": "conv_spoofed_or_different_999",
                "status": "done",
                "transcript": [
                    {"role": "user", "message": "Hi", "time_in_call_secs": 1.0},
                    {"role": "agent", "message": "Hello", "time_in_call_secs": 2.0},
                ],
            }
            resp_bytes = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        if mode == "processing":
            body = {
                "agent_id": server.agent_id,
                "conversation_id": server.active_conv_id,
                "status": "processing",
                "transcript": [],
            }
            resp_bytes = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        if mode == "empty_transcript":
            body = {
                "agent_id": server.agent_id,
                "conversation_id": server.active_conv_id,
                "status": "done",
                "transcript": [],
            }
            resp_bytes = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        if mode == "agent_mismatch":
            body = {
                "agent_id": "unexpected_rogue_agent_999",
                "conversation_id": server.active_conv_id,
                "status": "done",
                "transcript": [
                    {"role": "user", "message": "Hello", "time_in_call_secs": 1.0},
                    {"role": "agent", "message": "Greetings", "time_in_call_secs": 2.0},
                ],
            }
            resp_bytes = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        if mode == "tool_call_with_error":
            body = {
                "agent_id": server.agent_id,
                "conversation_id": server.active_conv_id,
                "status": "done",
                "metadata": {"call_duration_secs": 12},
                "transcript": [
                    {
                        "role": "user",
                        "message": "Lookup customer cust_789 status.",
                        "time_in_call_secs": 1.1,
                    },
                    {
                        "role": "agent",
                        "message": "Checking customer database now.",
                        "time_in_call_secs": 2.0,
                        "tool_calls": [
                            {
                                "request_id": "req_tool_789",
                                "tool_name": "lookup_customer",
                                "params_as_json": '{"customer_id": "cust_789"}',
                                "tool_has_been_called": True,
                            }
                        ],
                        "tool_results": [
                            {
                                "request_id": "req_tool_789",
                                "tool_name": "lookup_customer",
                                "result_value": None,
                                "is_error": True,
                                "raw_error_message": "RecordNotFound: customer cust_789 does not exist",
                            }
                        ],
                    },
                    {
                        "role": "agent",
                        "message": "I could not find a record for customer cust_789.",
                        "time_in_call_secs": 3.5,
                    },
                ],
            }
            resp_bytes = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        # Default compliant mode: return dynamic or stored payload matching path conversation ID
        req_conv_id = self.path.split("/v1/convai/conversations/")[-1].split("?")[0]
        body = {
            "agent_id": server.agent_id,
            "conversation_id": req_conv_id or server.active_conv_id,
            "status": "done",
            "metadata": {
                "call_duration_secs": 18,
                "start_time_unix_secs": 1729500000,
            },
            "transcript": server.custom_transcript or [
                {
                    "role": "user",
                    "message": "where can i find conversation id ?",
                    "time_in_call_secs": 1.2,
                },
                {
                    "role": "agent",
                    "message": "Checking knowledge base for conversation id documentation.",
                    "time_in_call_secs": 2.5,
                    "tool_calls": [
                        {
                            "request_id": "req_kb_001",
                            "tool_name": "knowledge_base",
                            "params_as_json": '{"query": "conversation id"}',
                            "tool_has_been_called": True,
                        }
                    ],
                    "tool_results": [
                        {
                            "request_id": "req_kb_001",
                            "tool_name": "knowledge_base",
                            "result_value": '{"doc": "Conversation ID is in URL parameters"}',
                            "is_error": False,
                        }
                    ],
                },
                {
                    "role": "agent",
                    "message": "You can find your conversation id in the ElevenLabs dashboard URL or API logs.",
                    "time_in_call_secs": 4.1,
                },
            ],
            "unrecognized_future_field_99": {"arbitrary": "data"},
        }
        resp_bytes = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)


class MockConvAiServer(ThreadingHTTPServer):
    """Mock server harness tracking state across test steps."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        super().__init__((host, port), MockConvAiHandler)
        self.recorded_requests: List[Dict[str, Any]] = []
        self.current_mode: str = "compliant"
        self.require_api_key: bool = True
        self.expected_api_key: str = "xi_mock_test_key_secret_777"
        self.agent_id: str = "agent_voicebench_main"
        self.active_conv_id: str = "conv_default_active_001"
        self.custom_transcript: Optional[List[Dict[str, Any]]] = None


@pytest.fixture
def mock_elevenlabs_service():
    """Start local mock server on free port."""
    server = MockConvAiServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    yield server, base_url
    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------------------
# Test 1: A valid conversation ID retrieves the expected transcript.
# ---------------------------------------------------------------------------
def test_01_valid_conversation_id_retrieves_expected_transcript(mock_elevenlabs_service):
    server, base_url = mock_elevenlabs_service
    test_conv_id = "conv_valid_req_001"
    server.active_conv_id = test_conv_id

    adapter = ElevenLabsConvAIAdapter(
        api_key=server.expected_api_key,
        agent_id=server.agent_id,
        conversation_id=test_conv_id,
        base_url=base_url,
    )
    trace = adapter.fetch_conversation_trace(test_conv_id)

    assert trace.session_id == test_conv_id
    assert trace.timing_mode == "target_reported_call_relative"

    transcripts = trace.get_transcripts()
    assert len(transcripts) >= 2
    assert "You can find your conversation id" in str(transcripts[-1].payload)


# ---------------------------------------------------------------------------
# Test 2: The requested ID is passed unchanged to the retrieval endpoint.
# ---------------------------------------------------------------------------
def test_02_requested_id_passed_unchanged_to_retrieval_endpoint(mock_elevenlabs_service):
    server, base_url = mock_elevenlabs_service
    requested_id = "conv_custom_uuid_8942_unaltered"

    adapter = ElevenLabsConvAIAdapter(
        api_key=server.expected_api_key,
        base_url=base_url,
    )
    adapter.fetch_conversation_trace(requested_id)

    assert len(server.recorded_requests) >= 1
    last_req = server.recorded_requests[-1]
    expected_path = f"/v1/convai/conversations/{requested_id}"
    assert last_req["path"] == expected_path
    headers_lower = {k.lower(): v for k, v in last_req["headers"].items()}
    assert headers_lower.get("xi-api-key") == server.expected_api_key


# ---------------------------------------------------------------------------
# Test 3: The returned ID is checked against the requested ID.
# ---------------------------------------------------------------------------
def test_03_returned_id_checked_against_requested_id(mock_elevenlabs_service):
    server, base_url = mock_elevenlabs_service
    server.current_mode = "id_mismatch"
    requested_id = "conv_real_target_123"

    adapter = ElevenLabsConvAIAdapter(
        api_key=server.expected_api_key,
        base_url=base_url,
    )
    trace = adapter.fetch_conversation_trace(requested_id)

    # Must record TRANSPORT_ERROR and halt
    transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
    assert len(transport_errors) == 1
    err_payload = transport_errors[0].payload
    assert "ConversationIdMismatchError" in str(err_payload)
    assert requested_id in str(err_payload)
    assert "conv_spoofed_or_different_999" in str(err_payload)


# ---------------------------------------------------------------------------
# Test 4: A real transcript reaches the evaluation engine.
# ---------------------------------------------------------------------------
def test_04_real_transcript_reaches_evaluation_engine(mock_elevenlabs_service):
    server, base_url = mock_elevenlabs_service
    target_conv_id = "conv_eval_engine_004"
    server.active_conv_id = target_conv_id

    adapter = ElevenLabsConvAIAdapter(
        api_key=server.expected_api_key,
        agent_id=server.agent_id,
        conversation_id=target_conv_id,
        base_url=base_url,
    )
    trace = adapter.fetch_conversation_trace(target_conv_id)

    scenario_path = REPO_ROOT / "scenarios" / "live" / "elevenlabs" / "SCEN-ELEVEN-SMOKE-01.yaml"
    from voicebench.parser import load_scenario_from_file
    scenario = load_scenario_from_file(scenario_path)

    evaluator = Evaluator()
    eval_result = evaluator.evaluate(scenario, trace)

    assert eval_result.status == TestStatus.PASS
    assert eval_result.target_type in ("elevenlabs_convai", "local_mock")
    assert eval_result.timing_mode == "target_reported_call_relative"


# ---------------------------------------------------------------------------
# Test 5: Actual transcript content appears in the resulting evidence.
# ---------------------------------------------------------------------------
def test_05_actual_transcript_content_appears_in_resulting_evidence(mock_elevenlabs_service):
    server, base_url = mock_elevenlabs_service
    conv_id = "conv_content_verify_005"
    unique_phrase = "Distinct verifiable signature phrase in agent response 98124."
    server.active_conv_id = conv_id
    server.custom_transcript = [
        {"role": "user", "message": "where can i find conversation id ?", "time_in_call_secs": 1.0},
        {
            "role": "agent",
            "message": f"{unique_phrase} Here is your conversation id documentation.",
            "time_in_call_secs": 2.0,
            "tool_calls": [{"request_id": "r1", "tool_name": "knowledge_base", "params_as_json": '{"query": "conversation id"}'}],
            "tool_results": [{"request_id": "r1", "tool_name": "knowledge_base", "result_value": "ok"}],
        },
    ]

    adapter = ElevenLabsConvAIAdapter(
        api_key=server.expected_api_key,
        agent_id=server.agent_id,
        base_url=base_url,
    )
    trace = adapter.fetch_conversation_trace(conv_id)

    scenario_path = REPO_ROOT / "scenarios" / "live" / "elevenlabs" / "SCEN-ELEVEN-SMOKE-01.yaml"
    from voicebench.parser import load_scenario_from_file
    scenario = load_scenario_from_file(scenario_path)

    eval_result = Evaluator().evaluate(scenario, trace)
    assert eval_result.status == TestStatus.PASS
    assert eval_result.evidence is not None
    assert unique_phrase in str(eval_result.evidence.quote_or_payload)


# ---------------------------------------------------------------------------
# Test 6: Tool calls and tool errors are preserved when present.
# ---------------------------------------------------------------------------
def test_06_tool_calls_and_tool_errors_preserved_when_present(mock_elevenlabs_service):
    server, base_url = mock_elevenlabs_service
    conv_id = "conv_tool_error_006"
    server.active_conv_id = conv_id
    server.current_mode = "tool_call_with_error"

    adapter = ElevenLabsConvAIAdapter(
        api_key=server.expected_api_key,
        agent_id=server.agent_id,
        base_url=base_url,
    )
    trace = adapter.fetch_conversation_trace(conv_id)

    tool_calls = trace.get_tool_calls()
    assert len(tool_calls) == 1
    assert tool_calls[0].payload["tool"] == "lookup_customer"
    assert tool_calls[0].payload["args"] == {"customer_id": "cust_789"}

    tool_results = [e for e in trace.events if e.event_type == EventType.TOOL_RESULT]
    assert len(tool_results) == 1
    tr = tool_results[0].payload
    assert tr["is_error"] is True
    assert "RecordNotFound" in tr["error"]


# ---------------------------------------------------------------------------
# Test 7: Invalid IDs and HTTP errors do not trigger fixture fallbacks.
# ---------------------------------------------------------------------------
def test_07_invalid_ids_and_http_errors_do_not_trigger_fixture_fallbacks(mock_elevenlabs_service):
    server, base_url = mock_elevenlabs_service

    # Case A: Invalid format
    res_inv = handle_evaluate_elevenlabs("invalid!chars*")
    assert res_inv["success"] is False
    assert res_inv["verdict"] == "ERROR"
    assert res_inv["error_type"] == "InvalidConversationIdError"
    assert res_inv["total"] == 1
    assert res_inv["passed_count"] == 0
    assert "diff.json" not in str(res_inv)

    # Case B: HTTP 404
    server.current_mode = "404_not_found"
    with patch.dict(os.environ, {"ELEVENLABS_API_KEY": server.expected_api_key}):
        res_404 = handle_evaluate_elevenlabs("conv_missing_404_test", base_url=base_url)
        assert res_404["success"] is False
        assert res_404["error_type"] == "NotFoundError"
        assert res_404["total"] == 1
        assert res_404["passed_count"] == 0

    # Case C: HTTP 429 Rate Limit
    server.current_mode = "429_rate_limit"
    with patch.dict(os.environ, {"ELEVENLABS_API_KEY": server.expected_api_key}):
        res_429 = handle_evaluate_elevenlabs("conv_rate_limited_test", base_url=base_url)
        assert res_429["success"] is False
        assert res_429["error_type"] == "RateLimitError"


# ---------------------------------------------------------------------------
# Test 8: Processing status does not produce a false completed evaluation.
# ---------------------------------------------------------------------------
def test_08_processing_status_does_not_produce_false_completed_evaluation(mock_elevenlabs_service):
    server, base_url = mock_elevenlabs_service
    conv_id = "conv_in_progress_008"
    server.active_conv_id = conv_id
    server.current_mode = "processing"

    adapter = ElevenLabsConvAIAdapter(api_key=server.expected_api_key, base_url=base_url)
    trace = adapter.fetch_conversation_trace(conv_id)

    transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
    assert len(transport_errors) == 1
    assert "ConversationProcessingError" in str(transport_errors[0].payload)
    assert "still in status 'processing'" in str(transport_errors[0].payload)

    with patch.dict(os.environ, {"ELEVENLABS_API_KEY": server.expected_api_key}):
        with patch.object(ElevenLabsConvAIAdapter, "fetch_conversation_trace", return_value=trace):
            res = handle_evaluate_elevenlabs(conv_id)
            assert res["success"] is False
            assert res["verdict"] == "INCONCLUSIVE"
            assert res["error_type"] == "ConversationProcessingError"
            assert res["passed_count"] == 0


# ---------------------------------------------------------------------------
# Test 9: Empty transcripts do not receive fabricated PASS results.
# ---------------------------------------------------------------------------
def test_09_empty_transcripts_do_not_receive_fabricated_pass_results(mock_elevenlabs_service):
    server, base_url = mock_elevenlabs_service
    conv_id = "conv_empty_done_009"
    server.active_conv_id = conv_id
    server.current_mode = "empty_transcript"

    adapter = ElevenLabsConvAIAdapter(api_key=server.expected_api_key, base_url=base_url)
    trace = adapter.fetch_conversation_trace(conv_id)

    transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
    assert len(transport_errors) == 1
    assert "EmptyTranscriptError" in str(transport_errors[0].payload)

    with patch.dict(os.environ, {"ELEVENLABS_API_KEY": server.expected_api_key}):
        with patch.object(ElevenLabsConvAIAdapter, "fetch_conversation_trace", return_value=trace):
            res = handle_evaluate_elevenlabs(conv_id)
            assert res["success"] is False
            assert res["verdict"] != "PASS"
            assert res["error_type"] == "EmptyTranscriptError"
            assert res["passed_count"] == 0


# ---------------------------------------------------------------------------
# Test 10: An ElevenLabs agent mismatch is handled correctly.
# ---------------------------------------------------------------------------
def test_10_agent_mismatch_handled_correctly(mock_elevenlabs_service):
    server, base_url = mock_elevenlabs_service
    conv_id = "conv_mismatched_010"
    server.active_conv_id = conv_id
    server.current_mode = "agent_mismatch"

    adapter = ElevenLabsConvAIAdapter(
        api_key=server.expected_api_key,
        agent_id="expected_agent_alpha",
        base_url=base_url,
    )
    trace = adapter.fetch_conversation_trace(conv_id)

    transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
    assert len(transport_errors) == 1
    assert "AgentMismatchError" in str(transport_errors[0].payload)
    assert "unexpected_rogue_agent_999" in str(transport_errors[0].payload)
    assert "expected_agent_alpha" in str(transport_errors[0].payload)


# ---------------------------------------------------------------------------
# Test 11: A later evaluation cannot accidentally display previous conversation data.
# ---------------------------------------------------------------------------
def test_11_later_evaluation_cannot_display_previous_conversation_data(mock_elevenlabs_service):
    server, base_url = mock_elevenlabs_service

    # First conversation
    conv_1 = "conv_first_call_111"
    server.active_conv_id = conv_1
    server.custom_transcript = [
        {"role": "user", "message": "where can i find conversation id ?", "time_in_call_secs": 1.0},
        {"role": "agent", "message": "First call content", "time_in_call_secs": 2.0, "tool_calls": [{"request_id": "r1", "tool_name": "knowledge_base", "params_as_json": '{"query": "conversation id"}'}]},
    ]
    adapter1 = ElevenLabsConvAIAdapter(api_key=server.expected_api_key, agent_id=server.agent_id, base_url=base_url)
    trace1 = adapter1.fetch_conversation_trace(conv_1)

    # Second conversation
    conv_2 = "conv_second_call_222"
    server.active_conv_id = conv_2
    server.custom_transcript = [
        {"role": "user", "message": "where can i find conversation id ?", "time_in_call_secs": 1.0},
        {"role": "agent", "message": "Second call completely different content", "time_in_call_secs": 2.0, "tool_calls": [{"request_id": "r2", "tool_name": "knowledge_base", "params_as_json": '{"query": "conversation id"}'}]},
    ]
    adapter2 = ElevenLabsConvAIAdapter(api_key=server.expected_api_key, agent_id=server.agent_id, base_url=base_url)
    trace2 = adapter2.fetch_conversation_trace(conv_2)

    assert trace1.session_id == conv_1
    assert trace2.session_id == conv_2
    assert "First call content" not in [e.payload for e in trace2.events]
    assert "Second call completely different content" in [e.payload for e in trace2.events]

    # Verify adapter conversation reuse prevention
    adapter_reuse = ElevenLabsConvAIAdapter(api_key=server.expected_api_key, conversation_id=conv_1, base_url=base_url)
    scen1 = Scenario(id="SCEN-A", name="A", description="A")
    scen2 = Scenario(id="SCEN-B", name="B", description="B")
    t1 = adapter_reuse.execute_scenario(scen1)
    t2 = adapter_reuse.execute_scenario(scen2)
    t2_errors = [e for e in t2.events if e.event_type == EventType.TRANSPORT_ERROR]
    assert len(t2_errors) == 1
    assert "ConversationReuseError" in str(t2_errors[0].payload)


# ---------------------------------------------------------------------------
# Test 12: The frontend renders actual retrieval and evaluation errors.
# ---------------------------------------------------------------------------
def test_12_frontend_renders_actual_retrieval_and_evaluation_errors(mock_elevenlabs_service):
    server, base_url = mock_elevenlabs_service
    conv_id = "conv_fe_error_012"
    server.active_conv_id = conv_id
    server.current_mode = "404_not_found"

    with patch.dict(os.environ, {"ELEVENLABS_API_KEY": server.expected_api_key}):
        res = handle_evaluate_elevenlabs(conv_id, base_url=base_url)

        assert res["action"] == "evaluate_elevenlabs"
        assert res["success"] is False
        assert res["verdict"] == "INCONCLUSIVE"
        assert res["conversation_id"] == conv_id
        assert "NotFoundError" in res["error"] or res["error_type"] == "NotFoundError"
        assert len(res["scenarios"]) == 1
        assert res["scenarios"][0]["status"] == "INCONCLUSIVE"
        assert res["data_source"] == "ElevenLabs conversation (RETRIEVAL_FAILED)"


# ---------------------------------------------------------------------------
# Integration Procedure: Real Conversation ID (Executed when credentials set)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not os.environ.get("ELEVENLABS_API_KEY") or not os.environ.get("ELEVENLABS_CONVERSATION_ID"),
    reason="Live integration requires ELEVENLABS_API_KEY and ELEVENLABS_CONVERSATION_ID in environment",
)
def test_13_live_integration_real_conversation_id():
    api_key = os.environ["ELEVENLABS_API_KEY"]
    conv_id = os.environ["ELEVENLABS_CONVERSATION_ID"]

    adapter = ElevenLabsConvAIAdapter(api_key=api_key, conversation_id=conv_id)
    trace = adapter.fetch_conversation_trace(conv_id)

    # Check that identity matches
    session_start = [e for e in trace.events if e.event_type == EventType.SESSION_START]
    assert len(session_start) == 1
    assert session_start[0].payload.get("conversation_id") == conv_id

    # Confirm no transport errors
    transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
    assert len(transport_errors) == 0, f"Transport errors in live evaluation: {transport_errors}"

    # Confirm transcript events present
    assert len(trace.events) > 2
