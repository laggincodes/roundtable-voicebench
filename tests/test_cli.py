"""Tests for the VoiceBench CLI interface."""

import json
from pathlib import Path
from voicebench.cli import main


def test_cli_scenarios_list(capsys):
    ret = main(["scenarios", "list", "--dir", "scenarios/roundtable"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Discovered 6 scenario(s)" in captured.out
    assert "SCEN-ROUT-01" in captured.out


def test_cli_scenarios_validate(capsys):
    ret = main(["scenarios", "validate", "scenarios/roundtable"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "ALL VALID" in captured.out


def test_cli_run_baseline_returns_zero(tmp_path, capsys):
    out_file = tmp_path / "results.json"
    ret = main([
        "run",
        "--suite", "scenarios/roundtable",
        "--target-config", "baseline",
        "--output", str(out_file),
    ])
    assert ret == 0
    captured = capsys.readouterr()
    assert "6 passed" in captured.out

    # Verify JSON file written
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["summary"]["passed"] == 6
    assert data["summary"]["failed"] == 0


def test_cli_run_defect_returns_nonzero(tmp_path, capsys):
    out_file = tmp_path / "defect_results.json"
    ret = main([
        "run",
        "--suite", "scenarios/roundtable",
        "--target-config", "defect_routing_amnesia",
        "--output", str(out_file),
    ])
    assert ret == 1
    captured = capsys.readouterr()
    assert "1 failed" in captured.out

    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["summary"]["failed"] == 1
