"""Reference Simulated Multi-Agent Roundtable Target for VoiceBench.

Implements both a compliant Baseline and documented Defective variants
specifically designed to validate the VoiceBench evaluation engine.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from voicebench.models import EventType, Scenario, TurnInput
from voicebench.trace import TraceCollector


class SimulatedRoundtableTarget:
    """Deterministic simulated multi-agent roundtable podcast system.

    Identifiable Expert Personas:
      - 'dr_aris': Renewable Energy Scientist (empirical, cautious, physics-grounded)
      - 'elena_vance': Energy Grid Economist (market dynamics, financing, subsidies)
    """

    AVAILABLE_CONFIGS = [
        "baseline",
        "defect_routing_amnesia",
        "defect_bargein_lag",
        "defect_role_drift",
        "defect_topic_amnesia",
        "defect_tool_hallucination",
        "defect_collision_chaos",
        "defect_transport_drop",
    ]

    def __init__(self, config_name: str = "baseline"):
        if config_name not in self.AVAILABLE_CONFIGS:
            raise ValueError(
                f"Unknown target configuration '{config_name}'. Available: {self.AVAILABLE_CONFIGS}"
            )
        self.config_name = config_name
        self._current_topic: str = ""
        self._prior_debate_context: Optional[str] = None

    def execute_scenario(self, scenario: Scenario) -> TraceCollector:
        """Run the scenario through the simulated roundtable and return captured trace."""
        collector = TraceCollector(
            scenario_id=scenario.id,
            target_type="local_mock_roundtable",
            target_config=self.config_name,
            use_virtual_time=True,
        )

        # 1. Session start
        collector.record_event(
            event_type=EventType.SESSION_START,
            speaker_id="system",
            payload={"status": "roundtable_active", "topic": scenario.preconditions.initial_topic},
            metadata={"simulated_target": "SimulatedRoundtableTarget", "config": self.config_name},
        )
        self._current_topic = scenario.preconditions.initial_topic
        self._prior_debate_context = scenario.preconditions.initial_topic

        # If transport drop defect is enabled, simulate premature socket failure
        if self.config_name == "defect_transport_drop":
            collector.advance_virtual_clock(250.0)
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                speaker_id="system",
                payload={"error": "ConnectionResetError: WebSocket socket closed unexpectedly"},
            )
            return collector

        # 2. Execute turns
        for turn in scenario.turns:
            self._process_turn(turn, scenario, collector)

        # 3. Session end
        collector.advance_virtual_clock(100.0)
        collector.record_event(
            event_type=EventType.SESSION_END,
            speaker_id="system",
            payload={"status": "session_completed"},
        )

        return collector

    def _process_turn(self, turn: TurnInput, scenario: Scenario, collector: TraceCollector) -> None:
        """Process an individual scenario turn deterministically."""
        # Record user input injection
        collector.advance_virtual_clock(turn.inject_delay_ms)
        collector.record_event(
            event_type=EventType.USER_INPUT,
            speaker_id=turn.speaker,
            intended_recipient=turn.target_expert,
            payload=turn.payload,
            turn_index=turn.turn_index,
        )

        # Handle specific scenario patterns deterministically based on config
        scenario_id = scenario.id

        if scenario_id == "SCEN-ROUT-01":
            self._handle_routing_turn(turn, collector)
        elif scenario_id == "SCEN-INT-02":
            self._handle_bargein_turn(turn, collector)
        elif scenario_id == "SCEN-ROLE-03":
            self._handle_role_consistency_turn(turn, collector)
        elif scenario_id == "SCEN-REC-04":
            self._handle_recovery_turn(turn, collector)
        elif scenario_id == "SCEN-TOOL-05":
            self._handle_tool_turn(turn, collector)
        elif scenario_id == "SCEN-COORD-06":
            self._handle_coordination_turn(turn, collector)
        else:
            # Generic fallback handler for user-defined scenarios
            self._handle_generic_turn(turn, scenario, collector)

    # -------------------------------------------------------------------------
    # Scenario 1 Handler: Addressed Routing
    # -------------------------------------------------------------------------
    def _handle_routing_turn(self, turn: TurnInput, collector: TraceCollector) -> None:
        collector.advance_virtual_clock(450.0)

        if self.config_name == "defect_routing_amnesia":
            # Defect: Always route to elena_vance even when dr_aris was addressed!
            speaker = "elena_vance"
            text = "From an economic investment perspective, battery storage infrastructure yields solid returns."
        else:
            # Baseline: Correctly routes to dr_aris
            speaker = "dr_aris"
            text = "From a physics perspective, current lithium-ion battery chemistry degradation and storage capacity limit seasonal grid transition."

        collector.record_event(
            event_type=EventType.TRANSCRIPT_FINAL,
            speaker_id=speaker,
            payload=text,
            turn_index=turn.turn_index,
            audio_duration_ms=3200.0,
            is_simulated=True,
        )

    # -------------------------------------------------------------------------
    # Scenario 2 Handler: Barge-In & Yield Latency
    # -------------------------------------------------------------------------
    def _handle_bargein_turn(self, turn: TurnInput, collector: TraceCollector) -> None:
        # Elena begins speaking monologue
        collector.advance_virtual_clock(100.0)
        collector.record_event(
            event_type=EventType.SPEECH_CHUNK,
            speaker_id="elena_vance",
            payload="The feed-in tariff numbers demonstrate that over the next seven quarters...",
            turn_index=turn.turn_index,
            is_simulated=True,
        )

        # User barge-in interrupt arrives at 3500ms
        barge_in_timestamp = collector.current_time_ms() + 3500.0
        collector.advance_virtual_clock(3500.0)

        collector.record_event(
            event_type=EventType.INTERRUPT_TRIGGER,
            speaker_id="user",
            payload=turn.payload,
            turn_index=turn.turn_index,
            timestamp_ms=barge_in_timestamp,
        )

        if self.config_name == "defect_bargein_lag":
            # Defect: Agent takes 1450ms to yield silence (exceeding 750ms limit!)
            yield_lag_ms = 1450.0
            collector.advance_virtual_clock(yield_lag_ms)
            silence_timestamp = barge_in_timestamp + yield_lag_ms
            collector.record_event(
                event_type=EventType.INTERRUPT_ACK,
                speaker_id="elena_vance",
                payload="Agent yielded after audible delay.",
                turn_index=turn.turn_index,
                timestamp_ms=silence_timestamp,
                metadata={"yield_latency_ms": yield_lag_ms},
            )
            collector.record_event(
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",
                payload="...quarterly metrics remain solid. Oh, hold on.",
                turn_index=turn.turn_index,
            )
        else:
            # Baseline: Yields cleanly in 320ms
            yield_lag_ms = 320.0
            collector.advance_virtual_clock(yield_lag_ms)
            silence_timestamp = barge_in_timestamp + yield_lag_ms
            collector.record_event(
                event_type=EventType.INTERRUPT_ACK,
                speaker_id="elena_vance",
                payload="Agent halted speech cleanly upon barge-in.",
                turn_index=turn.turn_index,
                timestamp_ms=silence_timestamp,
                metadata={"yield_latency_ms": yield_lag_ms},
            )
            collector.advance_virtual_clock(250.0)
            collector.record_event(
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",
                payload="You are correct, let's look at the repealed tariff numbers and adjust.",
                turn_index=turn.turn_index,
            )

    # -------------------------------------------------------------------------
    # Scenario 3 Handler: Persona & Stance Consistency
    # -------------------------------------------------------------------------
    def _handle_role_consistency_turn(self, turn: TurnInput, collector: TraceCollector) -> None:
        collector.advance_virtual_clock(500.0)

        if self.config_name == "defect_role_drift":
            # Defect: Dr. Aris abandons scientific rigor and promotes speculative tokens
            speaker = "dr_aris"
            text = "Absolutely! For $10 billion, I would go all in on cold fusion crypto tokens. It is a great investment!"
        else:
            # Baseline: Dr. Aris maintains cautious skepticism and empirical standards
            speaker = "dr_aris"
            text = "As a scientist, I remain deeply skeptical. Without peer-reviewed empirical evidence and published laboratory data, unproven cold fusion claims cannot justify capital."

        collector.record_event(
            event_type=EventType.TRANSCRIPT_FINAL,
            speaker_id=speaker,
            payload=text,
            turn_index=turn.turn_index,
            is_simulated=True,
        )

    # -------------------------------------------------------------------------
    # Scenario 4 Handler: Post-Interruption Topic Recovery
    # -------------------------------------------------------------------------
    def _handle_recovery_turn(self, turn: TurnInput, collector: TraceCollector) -> None:
        collector.advance_virtual_clock(400.0)

        if turn.turn_index == 1:
            # User asks procedural question: "what time is our live podcast scheduled to end?"
            collector.record_event(
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",
                payload="We are scheduled to conclude promptly at 3:00 PM.",
                turn_index=turn.turn_index,
            )
        elif turn.turn_index == 2:
            # User says: "Great, thanks. Please continue."
            if self.config_name == "defect_topic_amnesia":
                # Defect: Topic amnesia, generic reboot
                text = "Welcome to the podcast everyone. Today we are starting a brand new conversation."
            else:
                # Baseline: Seamlessly resumes the geothermal drill bit topic
                text = "Returning to our debate on geothermal drill bit longevity versus micro-reactors, the deep rock formation data shows notable wear."

            collector.record_event(
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="dr_aris",
                payload=text,
                turn_index=turn.turn_index,
            )

    # -------------------------------------------------------------------------
    # Scenario 5 Handler: Tool-Call Correctness & Fact Grounding
    # -------------------------------------------------------------------------
    def _handle_tool_turn(self, turn: TurnInput, collector: TraceCollector) -> None:
        collector.advance_virtual_clock(250.0)

        if self.config_name == "defect_tool_hallucination":
            # Defect: Does not call tool, hallucinating random number
            collector.advance_virtual_clock(300.0)
            collector.record_event(
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",
                payload="The wholesale electricity spot price is currently eighty-five dollars per megawatt hour.",
                turn_index=turn.turn_index,
            )
        else:
            # Baseline: Emits tool call, receives result, speaks exact value
            collector.record_event(
                event_type=EventType.TOOL_CALL,
                speaker_id="elena_vance",
                payload={"tool": "fetch_market_price", "args": {"asset": "electricity", "region": "ERCOT"}},
                turn_index=turn.turn_index,
            )
            collector.advance_virtual_clock(120.0)
            collector.record_event(
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={"price_mwh": 34.50, "currency": "USD"},
                turn_index=turn.turn_index,
            )
            collector.advance_virtual_clock(250.0)
            collector.record_event(
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",
                payload="According to market data, the wholesale spot price in ERCOT is 34.50 USD per megawatt-hour.",
                turn_index=turn.turn_index,
            )

    # -------------------------------------------------------------------------
    # Scenario 6 Handler: Multi-Speaker Coordination & Dead Air
    # -------------------------------------------------------------------------
    def _handle_coordination_turn(self, turn: TurnInput, collector: TraceCollector) -> None:
        collector.advance_virtual_clock(300.0)

        if self.config_name == "defect_collision_chaos":
            # Defect: Both agents talk simultaneously with 650ms overlap
            collector.record_event(
                event_type=EventType.SPEECH_CHUNK,
                speaker_id="elena_vance",
                payload="Carbon capture incentives are essential for private investment.",
                turn_index=1,
                metadata={"start_ms": 300.0, "end_ms": 2500.0},
            )
            # Elena and Aris collide between 2000ms and 2650ms (650ms overlap!)
            collector.record_event(
                event_type=EventType.SPEECH_CHUNK,
                speaker_id="dr_aris",
                payload="I strongly disagree on the capture efficiency thermodynamic limits.",
                turn_index=2,
                metadata={"start_ms": 2000.0, "end_ms": 4200.0, "overlap_ms": 650.0},
            )
            collector.record_event(
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="dr_aris",
                payload="Both speakers talked over each other in collision.",
                turn_index=2,
            )
        else:
            # Baseline: Clean alternating sequence with smooth 450ms gaps
            # Turn 1: Elena
            collector.record_event(
                event_type=EventType.SPEECH_CHUNK,
                speaker_id="elena_vance",
                payload="Carbon capture subsidies stimulate early market infrastructure.",
                turn_index=1,
                metadata={"start_ms": 300.0, "end_ms": 2200.0},
            )
            collector.record_event(
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",
                payload="Carbon capture subsidies stimulate early market infrastructure.",
                turn_index=1,
            )
            collector.advance_virtual_clock(2350.0)  # Gap of 450ms

            # Turn 2: Dr. Aris
            collector.record_event(
                event_type=EventType.SPEECH_CHUNK,
                speaker_id="dr_aris",
                payload="However, thermodynamic efficiency in direct air capture remains a major hurdle.",
                turn_index=2,
                metadata={"start_ms": 2650.0, "end_ms": 4600.0},
            )
            collector.record_event(
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="dr_aris",
                payload="However, thermodynamic efficiency in direct air capture remains a major hurdle.",
                turn_index=2,
            )
            collector.advance_virtual_clock(2400.0)  # Gap of 450ms

            # Turn 3: Elena
            collector.record_event(
                event_type=EventType.SPEECH_CHUNK,
                speaker_id="elena_vance",
                payload="Which is precisely why tax incentives are required to bridge R&D scale.",
                turn_index=3,
                metadata={"start_ms": 5050.0, "end_ms": 7100.0},
            )
            collector.record_event(
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="elena_vance",
                payload="Which is precisely why tax incentives are required to bridge R&D scale.",
                turn_index=3,
            )

    # -------------------------------------------------------------------------
    # Generic Turn Fallback
    # -------------------------------------------------------------------------
    def _handle_generic_turn(self, turn: TurnInput, scenario: Scenario, collector: TraceCollector) -> None:
        collector.advance_virtual_clock(400.0)
        speaker = turn.target_expert or (
            scenario.expectations.primary_speaker_id or "dr_aris"
        )
        collector.record_event(
            event_type=EventType.TRANSCRIPT_FINAL,
            speaker_id=speaker,
            payload=f"Response to: {turn.payload}",
            turn_index=turn.turn_index,
        )
