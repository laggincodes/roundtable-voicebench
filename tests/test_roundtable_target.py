"""Unit tests for the reference simulated roundtable target and local mock adapter."""

from voicebench.adapters.mock import LocalMockAdapter
from voicebench.parser import load_scenario_from_yaml
from voicebench.targets.roundtable import SimulatedRoundtableTarget


MINIMAL_SCENARIO_YAML = """
schema_version: "1.0.0"
id: "SCEN-ROUT-01"
name: "Targeted Expert Addressed-Routing"
description: "Verify that addressing Dr. Aris causes only Dr. Aris to respond"
timeout_seconds: 15.0
preconditions:
  active_agents:
    - id: "dr_aris"
      role: "Renewable Energy Scientist"
    - id: "elena_vance"
      role: "Energy Grid Economist"
  initial_topic: "Solar grid battery transitions"
turns:
  - turn_index: 1
    speaker: "user"
    input_type: "text"
    payload: "Dr. Aris, looking purely at battery storage physics, does the current grid support this?"
    target_expert: "dr_aris"
expectations:
  primary_speaker_id: "dr_aris"
"""


def test_baseline_target_reproducible_across_runs():
    scenario = load_scenario_from_yaml(MINIMAL_SCENARIO_YAML)

    adapter1 = LocalMockAdapter(target_config="baseline")
    trace1 = adapter1.execute_scenario(scenario)

    adapter2 = LocalMockAdapter(target_config="baseline")
    trace2 = adapter2.execute_scenario(scenario)

    # Event count, speaker IDs, and payloads must be 100% identical
    assert len(trace1.events) == len(trace2.events)
    for e1, e2 in zip(trace1.events, trace2.events):
        assert e1.event_type == e2.event_type
        assert e1.speaker_id == e2.speaker_id
        assert e1.payload == e2.payload
        assert e1.timestamp_ms == e2.timestamp_ms


def test_defective_target_routing_amnesia_produces_wrong_speaker():
    scenario = load_scenario_from_yaml(MINIMAL_SCENARIO_YAML)
    adapter = LocalMockAdapter(target_config="defect_routing_amnesia")
    trace = adapter.execute_scenario(scenario)

    transcripts = trace.get_transcripts()
    assert len(transcripts) == 1
    # Routing amnesia must route to elena_vance instead of dr_aris
    assert transcripts[0].speaker_id == "elena_vance"
