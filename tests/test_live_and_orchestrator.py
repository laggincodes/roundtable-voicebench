"""Tests for Real-Time Audio Interruption, Multi-Agent Orchestration, and Native Testing API.

All tests execute completely offline using synthetic fixtures and mocks without remote API calls.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voicebench.adapters.elevenlabs_agent_testing import (
    CreateSimulationTestRequest,
    ElevenLabsAgentTestingAdapter,
)
from voicebench.demo_app import (
    handle_agent_test_create,
    handle_evaluate_live_audio,
    handle_roundtable_run,
    handle_roundtable_status,
)
from voicebench.evaluator import Evaluator
from voicebench.models import (
    ConversationTrace,
    EventType,
    FailureCategory,
    TestStatus,
    TraceEvent,
)
from voicebench.orchestrator import (
    MultiAgentConfig,
    MultiAgentRoundtableOrchestrator,
    RoundtableProtocol,
)
from voicebench.parser import load_scenario_from_file

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Gate 1: Live Voice Interruption Testing (Offline Tests)
# ---------------------------------------------------------------------------


def test_live_audio_interruption_passing():
    """Verify live audio interruption evaluation when playback cancel latency is within 750ms threshold."""
    payload = {
        "speech_onset_ms": 1000.0,
        "interruption_notice_ms": 1350.0,
        "playback_cancel_ms": 1420.0,  # delta: 420ms <= 750ms
        "agent_audio_duration_ms": 2500.0,
    }
    res = handle_evaluate_live_audio(payload)
    assert res["success"] is True
    assert res["verdict"] == "PASS"
    assert res["metrics"]["yield_latency_ms"] == 420.0
    assert res["metrics"]["speech_onset_to_interruption_notice_ms"] == 350.0
    assert res["metrics"]["acoustic_silence_latency_ms"] is None


def test_live_audio_interruption_exceeding_threshold():
    """Verify failure classification when playback cancellation exceeds 750ms threshold."""
    payload = {
        "speech_onset_ms": 1000.0,
        "interruption_notice_ms": 1850.0,
        "playback_cancel_ms": 1950.0,  # delta: 950ms > 750ms
        "agent_audio_duration_ms": 3000.0,
    }
    res = handle_evaluate_live_audio(payload)
    assert res["success"] is False
    assert res["verdict"] == "FAIL"
    assert res["metrics"]["yield_latency_ms"] == 950.0
    assert "exceeded" in res["reason"].lower()


def test_live_audio_missing_markers_inconclusive():
    """Verify inconclusive outcome when required interruption trigger is missing."""
    payload = {
        "speech_onset_ms": None,
        "interruption_notice_ms": None,
        "playback_cancel_ms": None,
    }
    res = handle_evaluate_live_audio(payload)
    assert res["verdict"] == "INCONCLUSIVE"


def test_live_audio_ignored_interruption_fail():
    """Verify failure classification when agent completely ignores interruption."""
    payload = {
        "speech_onset_ms": 1000.0,
        "interruption_notice_ms": None,
        "playback_cancel_ms": None,
    }
    res = handle_evaluate_live_audio(payload)
    assert res["verdict"] == "FAIL"
    assert "ignored" in res["reason"].lower()


# ---------------------------------------------------------------------------
# Gate 2: Multi-Agent Orchestration Tests
# ---------------------------------------------------------------------------


def test_multi_agent_config_validation():
    # Valid distinct configuration
    valid_cfg = MultiAgentConfig(
        moderator_id="agent_mod_1",
        expert_a_id="agent_exp_a",
        expert_b_id="agent_exp_b",
    )
    is_valid, reason = valid_cfg.validate_distinct()
    assert is_valid is True
    assert reason is None

    # Missing ID
    missing_cfg = MultiAgentConfig(
        moderator_id="agent_mod_1",
        expert_a_id="",
        expert_b_id="agent_exp_b",
    )
    is_valid, reason = missing_cfg.validate_distinct()
    assert is_valid is False
    assert "must be configured" in reason

    # Duplicate IDs
    duplicate_cfg = MultiAgentConfig(
        moderator_id="agent_same",
        expert_a_id="agent_same",
        expert_b_id="agent_exp_b",
    )
    is_valid, reason = duplicate_cfg.validate_distinct()
    assert is_valid is False
    assert "must be distinct" in reason


def test_multi_agent_orchestrator_readiness():
    orchestrator = MultiAgentRoundtableOrchestrator(
        config=MultiAgentConfig(moderator_id="mod", expert_a_id="exp_a", expert_b_id="exp_b")
    )
    res = orchestrator.check_readiness()
    assert res["ready"] is True
    assert res["status"] == "READY"

    # Blocked readiness
    blocked_orchestrator = MultiAgentRoundtableOrchestrator(
        config=MultiAgentConfig(moderator_id="mod", expert_a_id="mod", expert_b_id="mod")
    )
    blocked_res = blocked_orchestrator.check_readiness()
    assert blocked_res["ready"] is False
    assert blocked_res["status"] == "BLOCKED"
    assert "distinct" in blocked_res["reason"].lower()


def test_multi_agent_reference_simulation_evaluation():
    """Verify deterministic multi-agent simulation satisfies scenario assertions."""
    scenario_path = REPO_ROOT / "scenarios" / "multi_agent" / "SCEN-MULTI-ORCH-01.yaml"
    scenario = load_scenario_from_file(scenario_path)

    orchestrator = MultiAgentRoundtableOrchestrator()
    trace = orchestrator.execute_offline_simulation(scenario)

    assert trace.timing_mode == "virtual_simulation"
    assert len(trace.get_transcripts()) == 4

    evaluator = Evaluator()
    eval_result = evaluator.evaluate(scenario, trace)
    assert eval_result.status == TestStatus.PASS


def test_handle_roundtable_run_simulation():
    res = handle_roundtable_run({"mode": "simulation"})
    assert res["success"] is True
    assert res["verdict"] == "PASS"
    assert res["mode"] == "virtual_simulation"
    assert len(res["turns"]) == 4


# ---------------------------------------------------------------------------
# Gate 3: Native Agent Testing API Tests
# ---------------------------------------------------------------------------


def test_agent_test_create_dry_run_preview():
    """Verify dry_run mode generates preview without remote calls."""
    res = handle_agent_test_create({
        "type": "simulation",
        "name": "Unit Simulation Test",
        "execute": False,
    })
    assert res["dry_run"] is True
    assert res["action"] == "create_test"
    assert "previewed but NOT executed" in res["message"]


def test_agent_test_create_mocked_execution():
    """Verify execute=True dispatches request cleanly when API key is set."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"id": "test_mock_12345"}).encode("utf-8")
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        adapter = ElevenLabsAgentTestingAdapter(api_key="sk_dummy_key_1234")
        req = CreateSimulationTestRequest(name="Mock Simulation")
        res = adapter.create_test(req, dry_run=False, execute=True)

        assert res["dry_run"] is False
        assert res["id"] == "test_mock_12345"
