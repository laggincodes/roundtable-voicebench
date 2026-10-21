"""Unit and integration tests for VoiceBench Regression Comparator."""

import json
from pathlib import Path
from voicebench.comparator import (
    compare_run_files,
    compare_runs,
    generate_markdown_diff_report,
    load_suite_result_from_file,
)
from voicebench.models import (
    ScenarioResult,
    StatusTransition,
    SuiteResult,
    TestStatus,
)


RESULTS_DIR = Path(__file__).parent.parent / "results"


def test_compare_baseline_vs_bargein_regression():
    """Verify comparing baseline and bargein defect catches the regression on SCEN-INT-02."""
    baseline_file = RESULTS_DIR / "run_baseline.json"
    candidate_file = RESULTS_DIR / "run_defect_bargein.json"

    assert baseline_file.is_file(), "Baseline results must exist"
    assert candidate_file.is_file(), "Candidate results must exist"

    report = compare_run_files(baseline_file, candidate_file)

    assert report.total_compared == 6
    assert report.regressions_count == 1
    assert report.fixes_count == 0
    assert report.timing_modes_compatible is True

    # Find SCEN-INT-02
    int_diff = next(d for d in report.scenario_diffs if d.scenario_id == "SCEN-INT-02")
    assert int_diff.transition == StatusTransition.REGRESSION
    assert int_diff.baseline_status == "PASS"
    assert int_diff.candidate_status == "FAIL"
    assert int_diff.candidate_failure_category == "FAIL-INT"

    # Verify metric delta
    yield_delta = next(m for m in int_diff.metric_deltas if m.metric_name == "yield_latency_ms")
    assert yield_delta.is_compatible is True
    assert yield_delta.baseline_value == 320.0
    assert yield_delta.candidate_value == 1450.0
    assert yield_delta.delta == 1130.0


def test_compare_baseline_vs_baseline_is_clean():
    """Comparing identical baseline runs reports 0 regressions."""
    baseline_file = RESULTS_DIR / "run_baseline.json"
    report = compare_run_files(baseline_file, baseline_file)

    assert report.regressions_count == 0
    assert report.fixes_count == 0
    assert report.unchanged_pass_count == 6


def test_compare_detects_fixed_transition():
    """Defect as baseline and healthy baseline as candidate reports FIXED."""
    candidate_file = RESULTS_DIR / "run_baseline.json"
    baseline_file = RESULTS_DIR / "run_defect_routing.json"

    report = compare_run_files(baseline_file, candidate_file)

    assert report.fixes_count == 1
    assert report.regressions_count == 0
    rout_diff = next(d for d in report.scenario_diffs if d.scenario_id == "SCEN-ROUT-01")
    assert rout_diff.transition == StatusTransition.FIXED


def test_incompatible_timing_modes_refuse_timing_metric_diffs():
    """Comparing virtual simulation with observed wall-clock must declare timing incompatible."""
    base = SuiteResult(
        run_id="run-virt",
        target_type="local_mock",
        target_config="baseline",
        timestamp_utc="2026-10-21T00:00:00Z",
        total=1,
        passed=1,
        failed=0,
        inconclusive=0,
        not_run=0,
        timing_mode="virtual_simulation",
        results=[
            ScenarioResult(
                scenario_id="SCEN-INT-02",
                scenario_name="Barge-In",
                status=TestStatus.PASS,
                reason="Pass",
                metrics={"yield_latency_ms": 320.0},
                timing_mode="virtual_simulation",
            )
        ],
    )

    cand = SuiteResult(
        run_id="run-real",
        target_type="generic_http",
        target_config="remote",
        timestamp_utc="2026-10-21T00:05:00Z",
        total=1,
        passed=1,
        failed=0,
        inconclusive=0,
        not_run=0,
        timing_mode="observed_wall_clock",
        results=[
            ScenarioResult(
                scenario_id="SCEN-INT-02",
                scenario_name="Barge-In",
                status=TestStatus.PASS,
                reason="Pass",
                metrics={"yield_latency_ms": 410.0},
                timing_mode="observed_wall_clock",
            )
        ],
    )

    report = compare_runs(base, cand)
    assert report.timing_modes_compatible is False
    assert "Incompatible timing modes" in report.timing_compatibility_note

    # Metric delta must be marked incompatible
    diff = report.scenario_diffs[0]
    yield_delta = next(m for m in diff.metric_deltas if m.metric_name == "yield_latency_ms")
    assert yield_delta.is_compatible is False
    assert yield_delta.delta is None


def test_generate_markdown_diff_report():
    """Markdown differential report contains expected sections and alerts."""
    baseline_file = RESULTS_DIR / "run_baseline.json"
    candidate_file = RESULTS_DIR / "run_defect_bargein.json"
    report = compare_run_files(baseline_file, candidate_file)

    md = generate_markdown_diff_report(report)
    assert "# VoiceBench — Regression Differential Report" in md
    assert "Regressions (PASS -> FAIL)" in md
    assert "SCEN-INT-02" in md
    assert "FAIL-INT" in md
