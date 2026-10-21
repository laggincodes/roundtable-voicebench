"""Unit tests for VoiceBench scenario parser and schema validator."""

import pytest
from voicebench.parser import (
    ScenarioValidationError,
    load_scenario_from_yaml,
    validate_scenario_dict,
)


def test_valid_minimal_scenario_loads():
    yaml_content = """
    schema_version: "1.0.0"
    id: "SCEN-TEST-01"
    name: "Minimal Test Scenario"
    description: "Valid minimal scenario definition"
    timeout_seconds: 10
    turns:
      - turn_index: 1
        speaker: "user"
        payload: "Hello Dr. Aris"
        target_expert: "dr_aris"
    expectations:
      primary_speaker_id: "dr_aris"
    """
    scenario = load_scenario_from_yaml(yaml_content)
    assert scenario.id == "SCEN-TEST-01"
    assert scenario.name == "Minimal Test Scenario"
    assert len(scenario.turns) == 1
    assert scenario.expectations.primary_speaker_id == "dr_aris"


def test_missing_id_fails_validation():
    yaml_content = """
    name: "Missing ID"
    description: "Missing id field"
    turns:
      - turn_index: 1
        speaker: "user"
        payload: "Hi"
    expectations:
      primary_speaker_id: "dr_aris"
    """
    with pytest.raises(ScenarioValidationError) as exc_info:
        load_scenario_from_yaml(yaml_content)
    assert "Field 'id' is required" in str(exc_info.value)


def test_empty_turns_fails_validation():
    yaml_content = """
    id: "SCEN-TEST-02"
    name: "Empty Turns"
    description: "No turns defined"
    turns: []
    expectations:
      primary_speaker_id: "dr_aris"
    """
    with pytest.raises(ScenarioValidationError) as exc_info:
        load_scenario_from_yaml(yaml_content)
    assert "'turns' is required and must be a non-empty list" in str(exc_info.value)


def test_missing_expectations_fails_validation():
    yaml_content = """
    id: "SCEN-TEST-03"
    name: "No Assertions"
    description: "Empty expectations mapping"
    turns:
      - turn_index: 1
        speaker: "user"
        payload: "Hello"
    expectations: {}
    """
    with pytest.raises(ScenarioValidationError) as exc_info:
        load_scenario_from_yaml(yaml_content)
    assert "'expectations' must define at least one valid assertion" in str(exc_info.value)


def test_invalid_turn_structure_reports_actionable_error():
    yaml_content = """
    id: "SCEN-TEST-04"
    name: "Invalid Turn"
    description: "Turn missing payload"
    turns:
      - turn_index: 1
        speaker: "user"
    expectations:
      primary_speaker_id: "dr_aris"
    """
    errors = validate_scenario_dict(
        {"id": "SCEN-TEST-04", "name": "Invalid", "description": "desc", "turns": [{"turn_index": 1, "speaker": "user"}], "expectations": {"primary_speaker_id": "dr_aris"}}
    )
    assert len(errors) == 1
    assert "Turn #1 must specify string 'payload'" in errors[0]
