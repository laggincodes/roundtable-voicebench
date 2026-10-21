"""Regression Comparison Engine for VoiceBench.

Compares two test runs (Baseline vs Candidate) and produces structured,
honest differential analysis without inventing arbitrary composite scores.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from voicebench.models import (
    MetricDelta,
    RegressionReport,
    ScenarioDiff,
    ScenarioResult,
    StatusTransition,
    SuiteResult,
    TestStatus,
)


def compare_run_files(baseline_file: str | Path, candidate_file: str | Path) -> RegressionReport:
    """Load and compare two SuiteResult JSON files."""
    b_suite = load_suite_result_from_file(baseline_file)
    c_suite = load_suite_result_from_file(candidate_file)
    return compare_runs(b_suite, c_suite)


def load_suite_result_from_file(file_path: str | Path) -> SuiteResult:
    """Load and parse SuiteResult from a JSON file."""
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"Result file not found: {p}")

    data = json.loads(p.read_text(encoding="utf-8"))

    results: List[ScenarioResult] = []
    for r in data.get("results", []):
        results.append(
            ScenarioResult(
                scenario_id=r["scenario_id"],
                scenario_name=r.get("scenario_name", r["scenario_id"]),
                status=TestStatus(r["status"]),
                reason=r.get("reason", ""),
                failure_category=r.get("failure_category"),
                metrics=dict(r.get("metrics", {})),
                metric_notes=dict(r.get("metric_notes", {})),
                target_type=r.get("target_type", "unknown"),
                target_config=r.get("target_config", "unknown"),
                run_id=r.get("run_id", ""),
                timestamp_utc=r.get("timestamp_utc", ""),
                software_version=r.get("software_version", "0.1.0"),
                timing_mode=r.get("timing_mode", "virtual_simulation"),
            )
        )

    summary = data.get("summary", {})
    return SuiteResult(
        run_id=data.get("run_id", ""),
        target_type=data.get("target_type", "unknown"),
        target_config=data.get("target_config", "unknown"),
        timestamp_utc=data.get("timestamp_utc", ""),
        total=summary.get("total", len(results)),
        passed=summary.get("passed", 0),
        failed=summary.get("failed", 0),
        inconclusive=summary.get("inconclusive", 0),
        not_run=summary.get("not_run", 0),
        results=results,
        software_version=data.get("software_version", "0.1.0"),
        timing_mode=data.get("timing_mode", "virtual_simulation"),
    )


def compare_runs(baseline: SuiteResult, candidate: SuiteResult) -> RegressionReport:
    """Compare two SuiteResult objects and generate a RegressionReport."""
    baseline_map: Dict[str, ScenarioResult] = {r.scenario_id: r for r in baseline.results}
    candidate_map: Dict[str, ScenarioResult] = {r.scenario_id: r for r in candidate.results}

    all_ids = sorted(list(set(baseline_map.keys()) | set(candidate_map.keys())))

    # Determine timing mode compatibility
    base_timing = getattr(baseline, "timing_mode", "virtual_simulation")
    cand_timing = getattr(candidate, "timing_mode", "virtual_simulation")
    timing_compatible = (base_timing == cand_timing)

    timing_note = (
        "Timing modes are compatible."
        if timing_compatible
        else f"Incompatible timing modes: baseline uses '{base_timing}' while candidate uses '{cand_timing}'. "
        "Timing metrics will not be directly diffed."
    )

    scenario_diffs: List[ScenarioDiff] = []
    regressions_count = 0
    fixes_count = 0
    unchanged_pass_count = 0
    unchanged_fail_count = 0
    inconclusive_count = 0

    for sc_id in all_ids:
        b_res = baseline_map.get(sc_id)
        c_res = candidate_map.get(sc_id)

        if b_res is None and c_res is not None:
            transition = StatusTransition.NEW_SCENARIO
            b_status = None
            c_status = c_res.status.value
            sc_name = c_res.scenario_name
        elif b_res is not None and c_res is None:
            transition = StatusTransition.REMOVED_SCENARIO
            b_status = b_res.status.value
            c_status = None
            sc_name = b_res.scenario_name
        else:
            assert b_res is not None and c_res is not None
            b_status = b_res.status.value
            c_status = c_res.status.value
            sc_name = c_res.scenario_name

            if b_status == "PASS" and c_status == "FAIL":
                transition = StatusTransition.REGRESSION
                regressions_count += 1
            elif b_status == "FAIL" and c_status == "PASS":
                transition = StatusTransition.FIXED
                fixes_count += 1
            elif b_status == "PASS" and c_status == "PASS":
                transition = StatusTransition.UNCHANGED_PASS
                unchanged_pass_count += 1
            elif b_status == "FAIL" and c_status == "FAIL":
                transition = StatusTransition.UNCHANGED_FAIL
                unchanged_fail_count += 1
            else:
                transition = StatusTransition.INCONCLUSIVE_SHIFT
                inconclusive_count += 1

        # Calculate metric deltas if both results exist
        metric_deltas: List[MetricDelta] = []
        if b_res and c_res:
            all_metric_keys = sorted(list(set(b_res.metrics.keys()) | set(c_res.metrics.keys())))
            for m_key in all_metric_keys:
                b_val = b_res.metrics.get(m_key)
                c_val = c_res.metrics.get(m_key)

                if b_val is None or c_val is None:
                    metric_deltas.append(
                        MetricDelta(
                            metric_name=m_key,
                            baseline_value=b_val,
                            candidate_value=c_val,
                            delta=None,
                            is_compatible=False,
                            notes="Metric unavailable in one of the evaluated runs.",
                        )
                    )
                elif not timing_compatible and "latency" in m_key.lower():
                    metric_deltas.append(
                        MetricDelta(
                            metric_name=m_key,
                            baseline_value=b_val,
                            candidate_value=c_val,
                            delta=None,
                            is_compatible=False,
                            notes=f"Cannot diff timing: {base_timing} vs {cand_timing}.",
                        )
                    )
                else:
                    delta = round(c_val - b_val, 2)
                    metric_deltas.append(
                        MetricDelta(
                            metric_name=m_key,
                            baseline_value=b_val,
                            candidate_value=c_val,
                            delta=delta,
                            is_compatible=True,
                            notes=f"Change: {delta:+.1f}",
                        )
                    )

        reason_change = None
        if b_res and c_res and b_res.reason != c_res.reason:
            reason_change = f"Baseline: {b_res.reason} -> Candidate: {c_res.reason}"

        scenario_diffs.append(
            ScenarioDiff(
                scenario_id=sc_id,
                scenario_name=sc_name,
                baseline_status=b_status,
                candidate_status=c_status,
                transition=transition,
                baseline_failure_category=b_res.failure_category if b_res else None,
                candidate_failure_category=c_res.failure_category if c_res else None,
                reason_change=reason_change,
                metric_deltas=metric_deltas,
            )
        )

    return RegressionReport(
        baseline_run_id=baseline.run_id,
        candidate_run_id=candidate.run_id,
        baseline_target=baseline.target_type,
        candidate_target=candidate.target_type,
        baseline_config=baseline.target_config,
        candidate_config=candidate.target_config,
        baseline_timing_mode=base_timing,
        candidate_timing_mode=cand_timing,
        timing_modes_compatible=timing_compatible,
        timing_compatibility_note=timing_note,
        total_compared=len(all_ids),
        regressions_count=regressions_count,
        fixes_count=fixes_count,
        unchanged_pass_count=unchanged_pass_count,
        unchanged_fail_count=unchanged_fail_count,
        inconclusive_count=inconclusive_count,
        scenario_diffs=scenario_diffs,
    )


def generate_markdown_diff_report(report: RegressionReport) -> str:
    """Generate a clean GitHub-flavored markdown report from a RegressionReport."""
    lines = [
        f"# VoiceBench — Regression Differential Report",
        f"",
        f"**Baseline Run:** `{report.baseline_run_id}` ({report.baseline_target} / `{report.baseline_config}` / `{report.baseline_timing_mode}`)",
        f"**Candidate Run:** `{report.candidate_run_id}` ({report.candidate_target} / `{report.candidate_config}` / `{report.candidate_timing_mode}`)",
        f"",
        f"### Summary",
        f"- **Total Scenarios Compared:** {report.total_compared}",
        f"- **Regressions (PASS -> FAIL):** {report.regressions_count}",
        f"- **Fixes (FAIL -> PASS):** {report.fixes_count}",
        f"- **Unchanged Passing:** {report.unchanged_pass_count}",
        f"- **Unchanged Failing:** {report.unchanged_fail_count}",
        f"- **Inconclusive / Status Shifts:** {report.inconclusive_count}",
        f"- **Timing Mode Compatibility:** {'Compatible' if report.timing_modes_compatible else 'Incompatible'}",
        f"",
    ]

    if not report.timing_modes_compatible:
        lines.extend([
            f"> [!WARNING]",
            f"> {report.timing_compatibility_note}",
            f"",
        ])

    lines.extend([
        f"### Scenario Comparison Matrix",
        f"",
        f"| Scenario ID | Name | Baseline | Candidate | Transition | Category Shift |",
        f"| :--- | :--- | :---: | :---: | :---: | :--- |",
    ])

    for d in report.scenario_diffs:
        cat_shift = "-"
        if d.baseline_failure_category or d.candidate_failure_category:
            cat_shift = f"{d.baseline_failure_category or 'None'} -> {d.candidate_failure_category or 'None'}"

        b_stat = d.baseline_status or "N/A"
        c_stat = d.candidate_status or "N/A"
        lines.append(
            f"| `{d.scenario_id}` | {d.scenario_name} | {b_stat} | {c_stat} | **{d.transition.value}** | {cat_shift} |"
        )

    # Detailed Regressions
    regressions = [d for d in report.scenario_diffs if d.transition == StatusTransition.REGRESSION]
    if regressions:
        lines.extend([
            f"",
            f"### Detected Regressions (Action Required)",
            f"",
        ])
        for r in regressions:
            lines.append(f"#### ❌ `{r.scenario_id}`: {r.scenario_name}")
            if r.reason_change:
                lines.append(f"- **Reason Change:** {r.reason_change}")
            if r.candidate_failure_category:
                lines.append(f"- **Failure Category:** `{r.candidate_failure_category}`")
            for md in r.metric_deltas:
                if md.delta is not None:
                    lines.append(f"- **Metric `{md.metric_name}`:** {md.baseline_value} -> {md.candidate_value} (Δ {md.delta:+.1f})")
            lines.append("")

    return "\n".join(lines)
