"""Deterministic offline tests for ElevenLabs Agent Testing API integration."""

import json
from unittest.mock import MagicMock, patch
import pytest

from voicebench.adapters.elevenlabs import ElevenLabsConvAIAdapter
from voicebench.adapters.elevenlabs_agent_testing import (
    CreateResponseUnitTestRequest,
    CreateSimulationTestRequest,
    CreateTestResponse,
    CreateToolCallUnitTestRequest,
    ElevenLabsAgentTestingAdapter,
    NativeTestType,
    RunTestsRequest,
    TestInvocationResponse,
    TestRunDetail,
    TestRunStatus,
    scenario_to_native_test_request,
)
from voicebench.cli import main
from voicebench.models import (
    EventType,
    FailureCategory,
    Scenario,
    ScenarioExpectations,
    TestStatus,
    TurnInput,
)


def _make_dummy_scenario(
    scenario_id: str = "SCEN-DUMMY",
    required_keywords: list = None,
    required_tool_call: str = None,
    expected_tool_args: dict = None,
    expected_tool_result_paths: dict = None,
    user_prompt: str = "Hello, what is the status?",
) -> Scenario:
    return Scenario(
        id=scenario_id,
        name="Dummy Agent Test Scenario",
        description="A scenario for testing native ElevenLabs agent testing integration",
        turns=[
            TurnInput(
                turn_index=1,
                speaker="user",
                payload=user_prompt,
            )
        ],
        expectations=ScenarioExpectations(
            required_keywords=required_keywords or [],
            required_tool_call=required_tool_call,
            expected_tool_args=expected_tool_args,
            expected_tool_result_paths=expected_tool_result_paths,
        ),
    )


# ---------------------------------------------------------------------------
# Gate 1 & 2: Request Models & Serialization
# ---------------------------------------------------------------------------


def test_create_test_request_models_serialization():
    """Verify serialization of all 3 native test request models."""
    # 1. LLM response unit test
    llm_req = CreateResponseUnitTestRequest(
        name="Test LLM",
        type="llm",
        chat_history=[{"role": "user", "message": "Hi"}],
        evaluation_criteria="Polite and concise",
        expected_response="Hello there",
        agent_id="agent_123",
    )
    llm_d = llm_req.to_dict()
    assert llm_d["name"] == "Test LLM"
    assert llm_d["type"] == "llm"
    assert llm_d["evaluation_criteria"] == "Polite and concise"
    assert llm_d["expected_response"] == "Hello there"
    assert llm_d["agent_id"] == "agent_123"

    # 2. Tool call unit test
    tool_req = CreateToolCallUnitTestRequest(
        name="Test Tool",
        type="tool",
        tool_name="fetch_weather",
        expected_tool_args={"city": "Austin"},
        chat_history=[{"role": "user", "message": "Weather in Austin?"}],
    )
    tool_d = tool_req.to_dict()
    assert tool_d["name"] == "Test Tool"
    assert tool_d["type"] == "tool"
    assert tool_d["tool_name"] == "fetch_weather"
    assert tool_d["expected_tool_args"] == {"city": "Austin"}

    # 3. Simulation test
    sim_req = CreateSimulationTestRequest(
        name="Test Sim",
        type="simulation",
        scenario="Customer asking about energy bill",
        user_prompt="Why is my bill high?",
        success_criteria=["Explains rate increase", "Offers payment plan"],
        max_turns=5,
    )
    sim_d = sim_req.to_dict()
    assert sim_d["name"] == "Test Sim"
    assert sim_d["type"] == "simulation"
    assert sim_d["user_prompt"] == "Why is my bill high?"
    assert len(sim_d["success_criteria"]) == 2
    assert sim_d["max_turns"] == 5


def test_run_tests_request_serialization():
    """Verify serialization of RunTestsRequest model."""
    run_req = RunTestsRequest(
        tests=[{"test_id": "test_001"}, {"test_id": "test_002"}],
        agent_id="agent_abc",
        branch_id="dev",
        repeat_count=3,
    )
    d = run_req.to_dict()
    assert len(d["tests"]) == 2
    assert d["branch_id"] == "dev"
    assert d["repeat_count"] == 3


# ---------------------------------------------------------------------------
# Gate 4: Scenario Conversion & Loss-Awareness
# ---------------------------------------------------------------------------


def test_scenario_to_native_test_conversion():
    """Verify lossless and loss-aware conversions from Scenario to native requests."""
    # Simulation conversion with barge-in loss notes
    scen_sim = Scenario(
        id="SCEN-INT",
        name="Barge-in test",
        description="Testing interruption handling",
        turns=[
            TurnInput(turn_index=1, speaker="user", payload="Start speaking"),
            TurnInput(turn_index=2, speaker="user", payload="Stop!", barge_in=True),
        ],
        expectations=ScenarioExpectations(
            max_yield_latency_ms=400.0,
            required_keywords=["acknowledged"],
        ),
    )
    req_sim, loss_notes = scenario_to_native_test_request(scen_sim, test_type=NativeTestType.SIMULATION)
    assert isinstance(req_sim, CreateSimulationTestRequest)
    assert req_sim.user_prompt == "Start speaking"
    assert any("barge-in" in n.lower() for n in loss_notes)

    # Tool test conversion
    scen_tool = _make_dummy_scenario(
        required_tool_call="fetch_market_price",
        expected_tool_args={"asset": "electricity"},
    )
    req_tool, loss_tool = scenario_to_native_test_request(scen_tool, test_type=NativeTestType.TOOL)
    assert isinstance(req_tool, CreateToolCallUnitTestRequest)
    assert req_tool.tool_name == "fetch_market_price"
    assert req_tool.expected_tool_args == {"asset": "electricity"}

    # Missing tool call raises ValueError when targeting tool test
    scen_no_tool = _make_dummy_scenario()
    with pytest.raises(ValueError) as exc:
        scenario_to_native_test_request(scen_no_tool, test_type=NativeTestType.TOOL)
    assert "does not specify required_tool_call" in str(exc.value)


# ---------------------------------------------------------------------------
# Gate 3: Safety & Cost Controls (Dry-Run by Default)
# ---------------------------------------------------------------------------


def test_adapter_dry_run_create_test_default():
    """Verify create_test defaults to dry-run preview and makes NO remote calls."""
    adapter = ElevenLabsAgentTestingAdapter(api_key="sk_live_dummy_secret_12345")
    req = CreateSimulationTestRequest(name="Test 1", scenario="Mock scenario")

    # dry_run=True, execute=False
    res = adapter.create_test(req, dry_run=True, execute=False)
    assert res["dry_run"] is True
    assert res["confirmed"] is False
    assert res["method"] == "POST"
    assert "/v1/convai/agent-testing/create" in res["url"]
    assert "payload" in res


def test_adapter_dry_run_run_tests_default():
    """Verify run_tests defaults to dry-run preview and makes NO remote calls."""
    adapter = ElevenLabsAgentTestingAdapter(api_key="sk_live_dummy_secret_12345")
    req = RunTestsRequest(tests=[{"test_id": "test_1"}], agent_id="agent_123")

    res = adapter.run_tests(req, dry_run=True, execute=False)
    assert res["dry_run"] is True
    assert res["confirmed"] is False
    assert "/v1/convai/agents/agent_123/run-tests" in res["url"]


def test_adapter_execute_requires_api_key():
    """Verify execute=True without API key raises ValueError."""
    adapter = ElevenLabsAgentTestingAdapter(api_key="")
    req = CreateSimulationTestRequest(name="Test 1")
    with pytest.raises(ValueError) as exc:
        adapter.create_test(req, dry_run=False, execute=True)
    assert "ElevenLabs API key is required" in str(exc.value)


def test_adapter_redacts_api_key_in_errors_and_repr():
    """Verify secret API key is never exposed in adapter repr, errors, or logs."""
    secret = "sk_live_very_secret_key_9999"
    adapter = ElevenLabsAgentTestingAdapter(api_key=secret)

    # repr masking
    rep = repr(adapter)
    assert secret not in rep
    assert "...9999" in rep

    # _redact helper
    redacted = adapter._redact(f"Authorization failed with key {secret} on server")
    assert secret not in redacted
    assert "[REDACTED_ELEVENLABS_API_KEY]" in redacted


# ---------------------------------------------------------------------------
# Gate 2 & 5: Mocked API Operations
# ---------------------------------------------------------------------------


def test_mocked_create_test_execution():
    """Verify successful create_test when confirmed (mocked HTTP response)."""
    adapter = ElevenLabsAgentTestingAdapter(api_key="sk_test_1234")
    req = CreateSimulationTestRequest(name="Sim 1")

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = json.dumps({"id": "test_created_999"}).encode("utf-8")
    mock_resp.status = 200

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = adapter.create_test(req, dry_run=False, execute=True)
        assert res["dry_run"] is False
        assert res["id"] == "test_created_999"


def test_mocked_run_tests_execution():
    """Verify successful run_tests when confirmed (mocked HTTP response)."""
    adapter = ElevenLabsAgentTestingAdapter(api_key="sk_test_1234")
    req = RunTestsRequest(tests=[{"test_id": "test_001"}], agent_id="agent_xyz")

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = json.dumps({
        "id": "inv_run_555",
        "agent_id": "agent_xyz",
        "test_runs": [{"test_run_id": "run_01", "status": "pending"}],
    }).encode("utf-8")
    mock_resp.status = 200

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = adapter.run_tests(req, dry_run=False, execute=True)
        assert res["dry_run"] is False
        assert res["id"] == "inv_run_555"


# ---------------------------------------------------------------------------
# Gate 2 & 4: Lifecycle Status Handling & VoiceBench Separation
# ---------------------------------------------------------------------------


def test_pending_test_run_returns_inconclusive():
    """Verify pending or running test invocation returns INCONCLUSIVE (never treated as PASS)."""
    scenario = _make_dummy_scenario(required_keywords=["success"])
    inv = TestInvocationResponse(
        id="inv_pending_01",
        status="pending",
        test_runs=[
            TestRunDetail(
                test_run_id="run_01",
                test_invocation_id="inv_pending_01",
                test_id="test_01",
                agent_id="agent_123",
                status="pending",
                passed=None,
            )
        ],
    )

    adapter = ElevenLabsAgentTestingAdapter(api_key="sk_test_1234", agent_id="agent_123")
    eval_res = adapter.evaluate_native_test(scenario, inv)
    assert eval_res.voicebench_result.status == TestStatus.INCONCLUSIVE
    assert eval_res.voicebench_result.failure_category == FailureCategory.FAIL_INFRA.value
    assert "still pending" in eval_res.voicebench_result.reason


def test_cancelled_test_run_returns_inconclusive():
    """Verify cancelled test invocation returns INCONCLUSIVE."""
    scenario = _make_dummy_scenario()
    inv = TestInvocationResponse(
        id="inv_cancel_01",
        status="cancelled",
        cancelled=True,
        test_runs=[
            TestRunDetail(
                test_run_id="run_01",
                test_invocation_id="inv_cancel_01",
                test_id="test_01",
                agent_id="agent_123",
                status="cancelled",
            )
        ],
    )

    adapter = ElevenLabsAgentTestingAdapter(api_key="sk_test_1234", agent_id="agent_123")
    eval_res = adapter.evaluate_native_test(scenario, inv)
    assert eval_res.voicebench_result.status == TestStatus.INCONCLUSIVE
    assert "cancelled by provider" in eval_res.voicebench_result.reason


def test_agent_id_mismatch_returns_inconclusive():
    """Verify invocation belonging to different agent yields INCONCLUSIVE."""
    scenario = _make_dummy_scenario()
    inv = TestInvocationResponse(
        id="inv_mismatch_01",
        status="completed",
        test_runs=[
            TestRunDetail(
                test_run_id="run_01",
                test_invocation_id="inv_mismatch_01",
                test_id="test_01",
                agent_id="agent_OTHER",
                status="completed",
            )
        ],
    )

    adapter = ElevenLabsAgentTestingAdapter(api_key="sk_test_1234", agent_id="agent_EXPECTED")
    eval_res = adapter.evaluate_native_test(scenario, inv)
    assert eval_res.voicebench_result.status == TestStatus.INCONCLUSIVE
    assert "Agent ID mismatch" in eval_res.voicebench_result.reason


def test_separation_of_native_pass_and_deterministic_fail():
    """Verify provider native PASS does NOT become VoiceBench PASS when assertions fail.

    CRITICAL REQUIREMENT:
      The provider's native LLM judge marked passed=True, but the agent's utterance
      in chat_history failed VoiceBench's deterministic required_keywords rule.
      VoiceBench must assign FAIL.
    """
    scenario = _make_dummy_scenario(required_keywords=["critical_grounding_keyword"])
    inv = TestInvocationResponse(
        id="inv_comp_01",
        status="completed",
        test_runs=[
            TestRunDetail(
                test_run_id="run_01",
                test_invocation_id="inv_comp_01",
                test_id="test_01",
                agent_id="agent_123",
                status="completed",
                passed=True,  # Provider native evaluation judged PASS
                evaluation_result={"summary": "Agent responded politely"},
                chat_history=[
                    {"role": "user", "message": "Hello, what is the status?", "time_in_call_secs": 1.0},
                    {"role": "assistant", "message": "Everything is running smoothly.", "time_in_call_secs": 2.5},
                ],
            )
        ],
    )

    adapter = ElevenLabsAgentTestingAdapter(api_key="sk_test_1234", agent_id="agent_123")
    eval_res = adapter.evaluate_native_test(scenario, inv)

    # Provider reported PASS
    assert eval_res.provider_passed is True
    # But VoiceBench deterministic evaluator correctly caught missing keyword!
    assert eval_res.voicebench_result.status == TestStatus.FAIL
    assert "critical_grounding_keyword" in eval_res.voicebench_result.reason


def test_completed_native_pass_and_deterministic_pass():
    """Verify scenario passes when both provider and VoiceBench deterministic rules pass."""
    scenario = _make_dummy_scenario(required_keywords=["smoothly"])
    inv = TestInvocationResponse(
        id="inv_comp_02",
        status="completed",
        test_runs=[
            TestRunDetail(
                test_run_id="run_02",
                test_invocation_id="inv_comp_02",
                test_id="test_01",
                agent_id="agent_123",
                status="completed",
                passed=True,
                chat_history=[
                    {"role": "user", "message": "Hello, what is the status?", "time_in_call_secs": 1.0},
                    {"role": "assistant", "message": "Everything is running smoothly.", "time_in_call_secs": 2.5},
                ],
            )
        ],
    )

    adapter = ElevenLabsAgentTestingAdapter(api_key="sk_test_1234", agent_id="agent_123")
    eval_res = adapter.evaluate_native_test(scenario, inv)
    assert eval_res.provider_passed is True
    assert eval_res.voicebench_result.status == TestStatus.PASS


def test_nested_tool_result_assertions_on_native_test_trace():
    """Verify structured nested tool-result assertions apply to native test traces."""
    scenario = _make_dummy_scenario(
        required_tool_call="get_server_status",
        expected_tool_result_paths={
            "metrics.health": "green",
            "cluster.nodes[0].ip": "10.0.0.1",
        },
    )

    inv = TestInvocationResponse(
        id="inv_tool_01",
        status="completed",
        test_runs=[
            TestRunDetail(
                test_run_id="run_tool_01",
                test_invocation_id="inv_tool_01",
                test_id="test_tool",
                agent_id="agent_123",
                status="completed",
                passed=True,
                chat_history=[
                    {"role": "user", "message": "Hello, what is the status?", "time_in_call_secs": 1.0},
                    {"role": "assistant", "message": "Checking server status.", "time_in_call_secs": 2.0},
                ],
                tool_calls=[
                    {"tool": "get_server_status", "args": {}, "request_id": "req_1"}
                ],
                tool_results=[
                    {
                        "tool": "get_server_status",
                        "request_id": "req_1",
                        "result": {
                            "metrics": {"health": "green"},
                            "cluster": {"nodes": [{"ip": "10.0.0.1"}]},
                        },
                    }
                ],
            )
        ],
    )

    adapter = ElevenLabsAgentTestingAdapter(api_key="sk_test_1234", agent_id="agent_123")
    eval_res = adapter.evaluate_native_test(scenario, inv)
    assert eval_res.voicebench_result.status == TestStatus.PASS


def test_missing_tool_result_in_native_test_trace_yields_inconclusive():
    """Verify tool call without tool result yields INCONCLUSIVE."""
    scenario = _make_dummy_scenario(
        required_tool_call="get_server_status",
        expected_tool_result_paths={"health": "ok"},
    )

    inv = TestInvocationResponse(
        id="inv_tool_missing",
        status="completed",
        test_runs=[
            TestRunDetail(
                test_run_id="run_tool_02",
                test_invocation_id="inv_tool_missing",
                test_id="test_tool",
                agent_id="agent_123",
                status="completed",
                chat_history=[
                    {"role": "user", "message": "Hello, what is the status?", "time_in_call_secs": 1.0},
                    {"role": "assistant", "message": "Checking server status.", "time_in_call_secs": 2.0},
                ],
                tool_calls=[{"tool": "get_server_status", "args": {}}],
                tool_results=[],  # No tool results captured!
            )
        ],
    )

    adapter = ElevenLabsAgentTestingAdapter(api_key="sk_test_1234", agent_id="agent_123")
    eval_res = adapter.evaluate_native_test(scenario, inv)
    assert eval_res.voicebench_result.status == TestStatus.INCONCLUSIVE
    assert eval_res.voicebench_result.evidence.event_type == "TOOL_RESULT_MISSING"


# ---------------------------------------------------------------------------
# CLI Command Tests: agent-test create, run, get
# ---------------------------------------------------------------------------


def test_cli_agent_test_create_dry_run(capsys):
    """Verify CLI agent-test create defaults to dry-run preview and exits 0."""
    ret = main([
        "agent-test", "create",
        "--scenario", "scenarios/roundtable/SCEN-TOOL-05.yaml",
        "--type", "tool",
        "--agent-id", "agent_mock_123",
    ])
    assert ret == 0
    captured = capsys.readouterr()
    assert "ElevenLabs Native Test Creation (DRY-RUN PREVIEW)" in captured.out
    assert '"dry_run": true' in captured.out
    assert '"tool_name": "fetch_market_price"' in captured.out


def test_cli_agent_test_run_dry_run(capsys):
    """Verify CLI agent-test run defaults to dry-run preview and exits 0."""
    ret = main([
        "agent-test", "run",
        "--test-id", "test_abc_1",
        "--test-id", "test_abc_2",
        "--agent-id", "agent_mock_123",
        "--repeat-count", "2",
    ])
    assert ret == 0
    captured = capsys.readouterr()
    assert "ElevenLabs Run Tests (DRY-RUN PREVIEW)" in captured.out
    assert '"dry_run": true' in captured.out
    assert '"repeat_count": 2' in captured.out


def test_cli_agent_test_run_missing_agent_id_fails(capsys, monkeypatch):
    """Verify CLI agent-test run without agent-id exits with error code 2."""
    monkeypatch.delenv("ELEVENLABS_AGENT_ID", raising=False)
    ret = main([
        "agent-test", "run",
        "--test-id", "test_abc",
    ])
    assert ret == 2
    captured = capsys.readouterr()
    assert "agent_id is required" in captured.err


def test_cli_run_with_target_elevenlabs_agent_testing_unconfigured(capsys, monkeypatch):
    """Verify running --target elevenlabs_agent_testing without invocation ID exits with INCONCLUSIVE."""
    monkeypatch.delenv("ELEVENLABS_TEST_INVOCATION_ID", raising=False)
    ret = main([
        "run",
        "--suite", "scenarios/roundtable/SCEN-TOOL-05.yaml",
        "--target", "elevenlabs_agent_testing",
    ])
    # Inconclusive run produces non-zero return code
    assert ret == 1
    captured = capsys.readouterr()
    assert "Missing ELEVENLABS_TEST_INVOCATION_ID" in captured.out


def test_backward_compatibility_convai_adapter():
    """Verify existing ElevenLabsConvAIAdapter capabilities remain intact."""
    conv_adapter = ElevenLabsConvAIAdapter()
    caps = conv_adapter.get_capabilities()
    assert caps.supports_text_input is True
    assert caps.supports_audio_input is False
    assert caps.supports_barge_in is False
    assert caps.supports_tool_calls is True
    assert caps.supports_multi_agent is False
    assert caps.timing_mode == "target_reported_call_relative"
