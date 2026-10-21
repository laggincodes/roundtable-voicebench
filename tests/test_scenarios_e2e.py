"""End-to-End acceptance tests verifying all six scenarios against Baseline and Defective targets."""

import json
from pathlib import Path

from voicebench.adapters.mock import LocalMockAdapter
from voicebench.evaluator import Evaluator
from voicebench.models import FailureCategory, TestStatus
from voicebench.parser import load_scenarios_from_dir
from voicebench.runner import SuiteRunner


SCENARIOS_DIR = Path(__file__).parent.parent / "scenarios" / "roundtable"


def test_e2e_all_six_scenarios_pass_on_baseline():
    scenarios = load_scenarios_from_dir(SCENARIOS_DIR)
    assert len(scenarios) == 6

    adapter = LocalMockAdapter(target_config="baseline")
    runner = SuiteRunner(adapter=adapter)
    suite_result = runner.run_suite(scenarios)

    assert suite_result.total == 6
    assert suite_result.passed == 6
    assert suite_result.failed == 0
    assert suite_result.inconclusive == 0

    # Verify JSON serializability
    data = json.loads(suite_result.to_json())
    assert data["summary"]["pass_rate"] == 1.0


def test_e2e_scen_rout_01_detects_defect_routing_amnesia():
    scenarios = [s for s in load_scenarios_from_dir(SCENARIOS_DIR) if s.id == "SCEN-ROUT-01"]
    assert len(scenarios) == 1

    adapter = LocalMockAdapter(target_config="defect_routing_amnesia")
    runner = SuiteRunner(adapter=adapter)
    res = runner.run_suite(scenarios).results[0]

    assert res.status == TestStatus.FAIL
    assert res.failure_category == FailureCategory.FAIL_ROUT.value
    assert "elena_vance" in res.reason
    assert res.evidence is not None
    assert res.evidence.speaker_id == "elena_vance"


def test_e2e_scen_int_02_detects_defect_bargein_lag():
    scenarios = [s for s in load_scenarios_from_dir(SCENARIOS_DIR) if s.id == "SCEN-INT-02"]
    assert len(scenarios) == 1

    adapter = LocalMockAdapter(target_config="defect_bargein_lag")
    runner = SuiteRunner(adapter=adapter)
    res = runner.run_suite(scenarios).results[0]

    assert res.status == TestStatus.FAIL
    assert res.failure_category == FailureCategory.FAIL_INT.value
    assert "1450.0ms" in res.reason
    assert res.metrics.get("yield_latency_ms") == 1450.0


def test_e2e_scen_role_03_detects_defect_role_drift():
    scenarios = [s for s in load_scenarios_from_dir(SCENARIOS_DIR) if s.id == "SCEN-ROLE-03"]
    assert len(scenarios) == 1

    adapter = LocalMockAdapter(target_config="defect_role_drift")
    runner = SuiteRunner(adapter=adapter)
    res = runner.run_suite(scenarios).results[0]

    assert res.status == TestStatus.FAIL
    assert res.failure_category == FailureCategory.FAIL_ROLE.value
    assert "absolutely" in res.reason.lower()


def test_e2e_scen_rec_04_detects_defect_topic_amnesia():
    scenarios = [s for s in load_scenarios_from_dir(SCENARIOS_DIR) if s.id == "SCEN-REC-04"]
    assert len(scenarios) == 1

    adapter = LocalMockAdapter(target_config="defect_topic_amnesia")
    runner = SuiteRunner(adapter=adapter)
    res = runner.run_suite(scenarios).results[0]

    assert res.status == TestStatus.FAIL
    # Triggers on forbidden reset phrase or topic recovery
    assert res.status == TestStatus.FAIL
    assert res.evidence is not None


def test_e2e_scen_tool_05_detects_defect_tool_hallucination():
    scenarios = [s for s in load_scenarios_from_dir(SCENARIOS_DIR) if s.id == "SCEN-TOOL-05"]
    assert len(scenarios) == 1

    adapter = LocalMockAdapter(target_config="defect_tool_hallucination")
    runner = SuiteRunner(adapter=adapter)
    res = runner.run_suite(scenarios).results[0]

    assert res.status == TestStatus.FAIL
    assert res.failure_category == FailureCategory.FAIL_TOOL.value
    assert "fetch_market_price" in res.reason


def test_e2e_scen_coord_06_detects_defect_collision_chaos():
    scenarios = [s for s in load_scenarios_from_dir(SCENARIOS_DIR) if s.id == "SCEN-COORD-06"]
    assert len(scenarios) == 1

    adapter = LocalMockAdapter(target_config="defect_collision_chaos")
    runner = SuiteRunner(adapter=adapter)
    res = runner.run_suite(scenarios).results[0]

    assert res.status == TestStatus.FAIL
    assert res.failure_category == FailureCategory.FAIL_COORD.value
    assert "collision" in res.reason.lower()
    assert res.metrics.get("cross_talk_overlap_ms") == 650.0


def test_e2e_transport_drop_produces_inconclusive():
    scenarios = load_scenarios_from_dir(SCENARIOS_DIR)
    adapter = LocalMockAdapter(target_config="defect_transport_drop")
    runner = SuiteRunner(adapter=adapter)
    suite_res = runner.run_suite(scenarios)

    assert suite_res.total == 6
    assert suite_res.inconclusive == 6
    assert suite_res.passed == 0
    assert suite_res.failed == 0
    for res in suite_res.results:
        assert res.status == TestStatus.INCONCLUSIVE
        assert res.failure_category == FailureCategory.FAIL_INFRA.value
