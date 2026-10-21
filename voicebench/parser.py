"""YAML Scenario Parser and Schema Validator for VoiceBench."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml

from voicebench.models import (
    AgentConfig,
    Preconditions,
    Scenario,
    ScenarioExpectations,
    TurnInput,
)


class ScenarioValidationError(Exception):
    """Raised when a scenario fails structural or semantic validation."""

    def __init__(self, message: str, errors: Optional[List[str]] = None):
        super().__init__(message)
        self.errors = errors or [message]


def validate_scenario_dict(data: Dict[str, Any], source_label: str = "<memory>") -> List[str]:
    """Validate a raw scenario dictionary and return a list of actionable error messages."""
    errors: List[str] = []

    if not isinstance(data, dict):
        return [f"{source_label}: Top-level YAML content must be a dictionary/mapping, got {type(data).__name__}"]

    # Required top-level fields
    required_str_fields = ["id", "name", "description"]
    for req in required_str_fields:
        val = data.get(req)
        if not val or not isinstance(val, str) or not val.strip():
            errors.append(f"{source_label}: Field '{req}' is required and must be a non-empty string.")

    # Validate id formatting
    scenario_id = data.get("id")
    if isinstance(scenario_id, str):
        if len(scenario_id.strip()) < 3:
            errors.append(f"{source_label}: 'id' must be at least 3 characters long (e.g. 'SCEN-ROUT-01').")

    # Validate timeout
    timeout = data.get("timeout_seconds")
    if timeout is not None:
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            errors.append(f"{source_label}: 'timeout_seconds' must be a positive number.")

    # Validate preconditions (optional, but if present must be structured)
    preconditions = data.get("preconditions")
    if preconditions is not None:
        if not isinstance(preconditions, dict):
            errors.append(f"{source_label}: 'preconditions' must be a dictionary/mapping.")
        else:
            agents = preconditions.get("active_agents")
            if agents is not None:
                if not isinstance(agents, list):
                    errors.append(f"{source_label}: 'preconditions.active_agents' must be a list of agents.")
                else:
                    for i, ag in enumerate(agents):
                        if not isinstance(ag, dict) or "id" not in ag or "role" not in ag:
                            errors.append(
                                f"{source_label}: 'preconditions.active_agents[{i}]' must have both 'id' and 'role' strings."
                            )

    # Validate turns (must be non-empty list)
    turns = data.get("turns")
    if not isinstance(turns, list) or len(turns) == 0:
        errors.append(f"{source_label}: 'turns' is required and must be a non-empty list of conversational turns.")
    else:
        for i, turn in enumerate(turns):
            if not isinstance(turn, dict):
                errors.append(f"{source_label}: Turn #{i+1} must be a dictionary mapping.")
                continue
            if "turn_index" not in turn or not isinstance(turn["turn_index"], int):
                errors.append(f"{source_label}: Turn #{i+1} is missing integer 'turn_index'.")
            if "speaker" not in turn or not isinstance(turn["speaker"], str) or not turn["speaker"].strip():
                errors.append(f"{source_label}: Turn #{i+1} must specify non-empty string 'speaker'.")
            if "payload" not in turn or not isinstance(turn["payload"], str):
                errors.append(f"{source_label}: Turn #{i+1} must specify string 'payload'.")

    # Validate expectations (must be present and have at least one assertion rule)
    expectations = data.get("expectations")
    if not isinstance(expectations, dict):
        errors.append(f"{source_label}: 'expectations' is required and must be a dictionary containing assertion rules.")
    else:
        assertable_keys = [
            "primary_speaker_id",
            "disallowed_speakers",
            "max_latency_ms",
            "max_yield_latency_ms",
            "required_keywords",
            "forbidden_keywords",
            "required_tool_call",
            "expected_tool_args",
            "expected_numeric_grounding",
            "expected_speaker_sequence",
            "max_cross_talk_overlap_ms",
            "max_dead_air_ms",
            "require_topic_recovery",
            "recovery_keywords",
        ]
        has_assertion = any(k in expectations and expectations[k] is not None for k in assertable_keys)
        if not has_assertion:
            errors.append(
                f"{source_label}: 'expectations' must define at least one valid assertion criteria from: {', '.join(assertable_keys[:7])}..."
            )

    return errors


def load_scenario_from_dict(data: Dict[str, Any], source_file: Optional[str] = None) -> Scenario:
    """Instantiate a Scenario object from a validated dictionary."""
    errors = validate_scenario_dict(data, source_label=source_file or "<memory>")
    if errors:
        raise ScenarioValidationError(
            f"Scenario validation failed with {len(errors)} error(s):\n" + "\n".join(f"  - {e}" for e in errors),
            errors=errors,
        )

    # Parse preconditions
    pre_raw = data.get("preconditions", {})
    active_agents: List[AgentConfig] = []
    for ag in pre_raw.get("active_agents", []):
        active_agents.append(
            AgentConfig(
                id=str(ag["id"]),
                role=str(ag.get("role", "")),
                description=str(ag.get("description", "")),
                domain_keywords=list(ag.get("domain_keywords", [])),
            )
        )
    preconditions = Preconditions(
        active_agents=active_agents,
        initial_topic=str(pre_raw.get("initial_topic", "")),
    )

    # Parse turns
    turns: List[TurnInput] = []
    for t in data.get("turns", []):
        turns.append(
            TurnInput(
                turn_index=int(t["turn_index"]),
                speaker=str(t["speaker"]),
                payload=str(t["payload"]),
                input_type=str(t.get("input_type", "text")),
                target_expert=str(t["target_expert"]) if t.get("target_expert") else None,
                inject_delay_ms=float(t.get("inject_delay_ms", 0.0)),
                barge_in=bool(t.get("barge_in", False)),
                barge_in_delay_ms=float(t["barge_in_delay_ms"]) if t.get("barge_in_delay_ms") is not None else None,
            )
        )

    # Parse expectations
    exp_raw = data.get("expectations", {})
    expectations = ScenarioExpectations(
        primary_speaker_id=str(exp_raw["primary_speaker_id"]) if exp_raw.get("primary_speaker_id") else None,
        disallowed_speakers=list(exp_raw.get("disallowed_speakers", [])),
        max_latency_ms=float(exp_raw["max_latency_ms"]) if exp_raw.get("max_latency_ms") is not None else None,
        max_yield_latency_ms=float(exp_raw["max_yield_latency_ms"]) if exp_raw.get("max_yield_latency_ms") is not None else None,
        required_keywords=list(exp_raw.get("required_keywords", [])),
        forbidden_keywords=list(exp_raw.get("forbidden_keywords", [])),
        required_tool_call=str(exp_raw["required_tool_call"]) if exp_raw.get("required_tool_call") else None,
        expected_tool_args=dict(exp_raw["expected_tool_args"]) if exp_raw.get("expected_tool_args") else None,
        expected_numeric_grounding=str(exp_raw["expected_numeric_grounding"]) if exp_raw.get("expected_numeric_grounding") else None,
        expected_speaker_sequence=list(exp_raw.get("expected_speaker_sequence", [])) if exp_raw.get("expected_speaker_sequence") else None,
        max_cross_talk_overlap_ms=float(exp_raw["max_cross_talk_overlap_ms"]) if exp_raw.get("max_cross_talk_overlap_ms") is not None else None,
        max_dead_air_ms=float(exp_raw["max_dead_air_ms"]) if exp_raw.get("max_dead_air_ms") is not None else None,
        require_topic_recovery=bool(exp_raw["require_topic_recovery"]) if exp_raw.get("require_topic_recovery") is not None else None,
        recovery_keywords=list(exp_raw.get("recovery_keywords", [])),
    )

    return Scenario(
        id=str(data["id"]),
        name=str(data["name"]),
        description=str(data["description"]),
        tags=list(data.get("tags", [])),
        timeout_seconds=float(data.get("timeout_seconds", 15.0)),
        preconditions=preconditions,
        turns=turns,
        expectations=expectations,
        source_file=source_file,
    )


def load_scenario_from_yaml(yaml_content: str, source_file: Optional[str] = None) -> Scenario:
    """Parse YAML string and return a validated Scenario."""
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        raise ScenarioValidationError(f"Invalid YAML syntax in {source_file or '<memory>'}: {e}") from e

    return load_scenario_from_dict(data, source_file=source_file)


def load_scenario_from_file(file_path: str | Path) -> Scenario:
    """Read a scenario YAML file and return a validated Scenario object."""
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"Scenario file not found: {p}")

    content = p.read_text(encoding="utf-8")
    return load_scenario_from_yaml(content, source_file=str(p))


def validate_scenario_file(file_path: str | Path) -> Tuple[bool, List[str]]:
    """Validate a file and return (is_valid, list_of_errors)."""
    p = Path(file_path)
    if not p.is_file():
        return False, [f"File not found: {p}"]

    try:
        content = p.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        errors = validate_scenario_dict(data, source_label=str(p))
        return (len(errors) == 0, errors)
    except Exception as e:
        return False, [f"Error reading {p}: {e}"]


def load_scenarios_from_dir(dir_path: str | Path) -> List[Scenario]:
    """Scan a directory for .yaml and .yml files and load all valid scenarios."""
    p = Path(dir_path)
    if not p.is_dir():
        raise NotADirectoryError(f"Scenarios directory not found: {p}")

    scenarios: List[Scenario] = []
    files = sorted(list(p.glob("*.yaml")) + list(p.glob("*.yml")))
    for f in files:
        scenarios.append(load_scenario_from_file(f))
    return scenarios
