"""VoiceBench Command Line Interface (CLI).

Supports:
  - voicebench scenarios list
  - voicebench scenarios validate
  - voicebench run [--suite PATH] [--target {mock,http}] [--endpoint-url URL] [--output FILE]
  - voicebench diff BASELINE.json CANDIDATE.json [--output FILE] [--report FILE]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import List, Optional

from voicebench.adapters.base import TargetAdapter
from voicebench.adapters.http import GenericHttpAdapter
from voicebench.adapters.mock import LocalMockAdapter
from voicebench.comparator import (
    compare_run_files,
    compare_runs,
    generate_markdown_diff_report,
    load_suite_result_from_file,
)
from voicebench.evaluator import Evaluator
from voicebench.models import StatusTransition, SuiteResult, TestStatus
from voicebench.parser import (
    ScenarioValidationError,
    load_scenario_from_file,
    load_scenarios_from_dir,
    validate_scenario_file,
)
from voicebench.runner import SuiteRunner


def build_parser() -> argparse.ArgumentParser:
    """Construct argument parser for VoiceBench CLI."""
    parser = argparse.ArgumentParser(
        prog="voicebench",
        description="VoiceBench: Open-Source Evaluation Framework for Real-Time Multi-Agent Voice Systems",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # --- scenarios command ---
    scenarios_parser = subparsers.add_parser("scenarios", help="Manage and inspect scenarios")
    scenarios_sub = scenarios_parser.add_subparsers(dest="scenario_action", help="Scenario action")

    # scenarios list
    list_parser = scenarios_sub.add_parser("list", help="List scenarios in a directory")
    list_parser.add_argument(
        "--dir", "-d",
        default="scenarios/roundtable",
        help="Path to scenarios directory (default: scenarios/roundtable)",
    )

    # scenarios validate
    val_parser = scenarios_sub.add_parser("validate", help="Validate scenario YAML files")
    val_parser.add_argument(
        "files",
        nargs="*",
        default=["scenarios/roundtable"],
        help="File(s) or directory to validate",
    )

    # --- run command ---
    run_parser = subparsers.add_parser("run", help="Run scenario suite against a target adapter")
    run_parser.add_argument(
        "--suite", "-s",
        default="scenarios/roundtable",
        help="Path to scenario file or directory (default: scenarios/roundtable)",
    )
    run_parser.add_argument(
        "--target", "-t",
        default="mock",
        choices=["mock", "local_mock", "http", "generic_http"],
        help="Target adapter type (default: mock)",
    )
    run_parser.add_argument(
        "--endpoint-url",
        default="http://localhost:8080/api/v1/roundtable/execute",
        help="Target endpoint URL when using HTTP adapter (default: http://localhost:8080/api/v1/roundtable/execute)",
    )
    run_parser.add_argument(
        "--target-config", "-c",
        default="baseline",
        help="Target configuration name (e.g. baseline, defect_routing_amnesia, defect_bargein_lag, etc.)",
    )
    run_parser.add_argument(
        "--output", "-o",
        help="Path to write JSON results file",
    )
    run_parser.add_argument(
        "--scenario-id",
        help="Filter to run only a single scenario ID",
    )

    # --- diff command ---
    diff_parser = subparsers.add_parser("diff", help="Compare two test runs and generate regression report")
    diff_parser.add_argument("baseline", help="Path to baseline results JSON file")
    diff_parser.add_argument("candidate", help="Path to candidate results JSON file")
    diff_parser.add_argument("--output", "-o", help="Path to write machine-readable diff JSON")
    diff_parser.add_argument("--report", "-r", help="Path to write Markdown differential report")

    return parser


def cmd_scenarios_list(directory: str) -> int:
    """List all scenarios found in directory."""
    p = Path(directory)
    if not p.exists():
        print(f"Error: Directory not found: {directory}", file=sys.stderr)
        return 2

    try:
        scenarios = load_scenarios_from_dir(p)
    except Exception as e:
        print(f"Error loading scenarios: {e}", file=sys.stderr)
        return 2

    print(f"\nDiscovered {len(scenarios)} scenario(s) in {p.resolve()}:\n")
    print(f"{'ID':<16} {'NAME':<36} {'TURNS':<6} {'TAGS'}")
    print("-" * 75)
    for sc in scenarios:
        tags_str = ", ".join(sc.tags) if sc.tags else "-"
        print(f"{sc.id:<16} {sc.name[:34]:<36} {len(sc.turns):<6} {tags_str}")
    print()
    return 0


def cmd_scenarios_validate(targets: List[str]) -> int:
    """Validate one or more scenario files or directories."""
    all_valid = True
    validated_count = 0

    files_to_check: List[Path] = []
    for t in targets:
        p = Path(t)
        if p.is_dir():
            files_to_check.extend(sorted(list(p.glob("*.yaml")) + list(p.glob("*.yml"))))
        elif p.is_file():
            files_to_check.append(p)
        else:
            print(f"Error: Path does not exist: {t}", file=sys.stderr)
            return 2

    if not files_to_check:
        print("No YAML scenario files found to validate.", file=sys.stderr)
        return 2

    print(f"\nValidating {len(files_to_check)} scenario file(s)...\n")
    for f in files_to_check:
        is_valid, errors = validate_scenario_file(f)
        validated_count += 1
        if is_valid:
            print(f"  [VALID]   {f.name}")
        else:
            all_valid = False
            print(f"  [INVALID] {f.name}")
            for err in errors:
                print(f"            - {err}")

    print(f"\nValidation complete: {validated_count} checked, {'ALL VALID' if all_valid else 'FAILED'}.\n")
    return 0 if all_valid else 1


def cmd_run(
    suite_path: str,
    target_type: str = "mock",
    endpoint_url: str = "http://localhost:8080/api/v1/roundtable/execute",
    target_config: str = "baseline",
    output_file: Optional[str] = None,
    scenario_id_filter: Optional[str] = None,
) -> int:
    """Execute scenario suite and print summary."""
    p = Path(suite_path)
    if not p.exists():
        print(f"Error: Suite path not found: {suite_path}", file=sys.stderr)
        return 2

    try:
        if p.is_file():
            scenarios = [load_scenario_from_file(p)]
        else:
            scenarios = load_scenarios_from_dir(p)
    except ScenarioValidationError as e:
        print(f"Scenario validation error: {e}", file=sys.stderr)
        return 2

    if scenario_id_filter:
        scenarios = [s for s in scenarios if s.id == scenario_id_filter]
        if not scenarios:
            print(f"Error: No scenario matching ID '{scenario_id_filter}' found.", file=sys.stderr)
            return 2

    adapter: TargetAdapter
    if target_type in ("http", "generic_http"):
        adapter = GenericHttpAdapter(endpoint_url=endpoint_url, target_config=target_config)
    else:
        adapter = LocalMockAdapter(target_config=target_config)

    runner = SuiteRunner(adapter=adapter, evaluator=Evaluator())

    print(f"\nStarting VoiceBench test run:")
    print(f"  Target: {type(adapter).__name__} (Config: {target_config}, Timing: {adapter.get_capabilities().timing_mode})")
    print(f"  Scenarios: {len(scenarios)}")
    print("=" * 70)

    suite_result: SuiteResult = runner.run_suite(scenarios)

    # Print results table
    for res in suite_result.results:
        status_label = f"[{res.status.value}]"
        print(f"{status_label:<15} {res.scenario_id:<16} {res.scenario_name}")
        if res.status != TestStatus.PASS:
            print(f"                Reason: {res.reason}")
            if res.evidence and res.evidence.notes:
                print(f"                Evidence: {res.evidence.notes}")

    print("=" * 70)
    summary = suite_result.to_dict()["summary"]
    print(
        f"Summary: {summary['total']} total | "
        f"{summary['passed']} passed | "
        f"{summary['failed']} failed | "
        f"{summary['inconclusive']} inconclusive | "
        f"{summary['not_run']} not run "
        f"({summary['pass_rate'] * 100:.1f}% pass rate)\n"
    )

    if output_file:
        out_p = Path(output_file)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(suite_result.to_json(), encoding="utf-8")
        print(f"Machine-readable results written to: {out_p.resolve()}\n")

    # Exit code: 0 if all passed, 1 if any failed or inconclusive
    if suite_result.failed > 0 or suite_result.inconclusive > 0:
        return 1
    return 0


def cmd_diff(
    baseline_path: str,
    candidate_path: str,
    output_json: Optional[str] = None,
    report_md: Optional[str] = None,
) -> int:
    """Compare two test runs and display regression analysis."""
    try:
        baseline_res = load_suite_result_from_file(baseline_path)
    except Exception as e:
        print(f"Error loading baseline file '{baseline_path}': {e}", file=sys.stderr)
        return 2

    try:
        candidate_res = load_suite_result_from_file(candidate_path)
    except Exception as e:
        print(f"Error loading candidate file '{candidate_path}': {e}", file=sys.stderr)
        return 2

    report = compare_runs(baseline_res, candidate_res)

    print(f"\n========================================================")
    print(f"VoiceBench Regression Comparison")
    print(f"========================================================")
    print(f"Baseline:  {report.baseline_run_id} ({report.baseline_target} / {report.baseline_config} / {report.baseline_timing_mode})")
    print(f"Candidate: {report.candidate_run_id} ({report.candidate_target} / {report.candidate_config} / {report.candidate_timing_mode})")
    print(f"Timing Modes: {'COMPATIBLE' if report.timing_modes_compatible else 'INCOMPATIBLE'}")
    if not report.timing_modes_compatible:
        print(f"Note: {report.timing_compatibility_note}")

    print(f"\nSummary:")
    print(f"  Compared:    {report.total_compared}")
    print(f"  Regressions: {report.regressions_count} (PASS -> FAIL)")
    print(f"  Fixes:       {report.fixes_count} (FAIL -> PASS)")
    print(f"  Unchanged:   {report.unchanged_pass_count} pass, {report.unchanged_fail_count} fail")
    print(f"  Shifts:      {report.inconclusive_count} inconclusive")
    print("-" * 56)

    for d in report.scenario_diffs:
        transition_label = f"[{d.transition.value}]"
        print(f"{transition_label:<20} {d.scenario_id:<15} {d.baseline_status or 'N/A'} -> {d.candidate_status or 'N/A'}")
        if d.transition == StatusTransition.REGRESSION:
            print(f"  [!] Regression Reason: {d.reason_change}")
            if d.candidate_failure_category:
                print(f"  Category: {d.candidate_failure_category}")
        for md in d.metric_deltas:
            if md.is_compatible and md.delta is not None and abs(md.delta) > 0.0:
                print(f"     Metric {md.metric_name}: {md.baseline_value} -> {md.candidate_value} (delta: {md.delta:+.1f})")

    print()

    if output_json:
        out_p = Path(output_json)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(report.to_json(), encoding="utf-8")
        print(f"Machine-readable diff written to: {out_p.resolve()}")

    if report_md:
        rep_p = Path(report_md)
        rep_p.parent.mkdir(parents=True, exist_ok=True)
        md_text = generate_markdown_diff_report(report)
        rep_p.write_text(md_text, encoding="utf-8")
        print(f"Markdown differential report written to: {rep_p.resolve()}")

    # Return exit code 1 if any regressions exist
    return 1 if report.regressions_count > 0 else 0


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand is None:
        parser.print_help()
        return 0

    if args.subcommand == "scenarios":
        if args.scenario_action == "list":
            return cmd_scenarios_list(args.dir)
        elif args.scenario_action == "validate":
            return cmd_scenarios_validate(args.files)
        else:
            parser.parse_args(["scenarios", "--help"])
            return 0

    elif args.subcommand == "run":
        return cmd_run(
            suite_path=args.suite,
            target_type=args.target,
            endpoint_url=args.endpoint_url,
            target_config=args.target_config,
            output_file=args.output,
            scenario_id_filter=args.scenario_id,
        )

    elif args.subcommand == "diff":
        return cmd_diff(
            baseline_path=args.baseline,
            candidate_path=args.candidate,
            output_json=args.output,
            report_md=args.report,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
