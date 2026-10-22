"""Multi-Agent Roundtable Orchestrator for VoiceBench.

Manages sequential and addressed roundtables across distinct agent configurations:
  - Moderator (Host / Synthesizer)
  - Expert A (Domain Specialist)
  - Expert B (Critical Inquirer / Cross-Domain Specialist)

Enforces:
  - Distinct agent configuration checks (fails/blocks with clear instructions if identical or missing IDs)
  - Strict turn-taking policies and context handoffs
  - Independent session tracking per agent
  - Clean error, timeout, and transport fault recording without inventing dialogue
  - Offline reference simulation target for reproducible automated testing
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from voicebench.models import (
    AdapterCapabilities,
    AgentConfig,
    ConversationTrace,
    EventType,
    Scenario,
    TraceEvent,
)


class RoundtableProtocol(str, Enum):
    """Supported turn-taking protocols for multi-agent roundtables."""
    SEQUENTIAL_DEBATE = "sequential_debate"        # Mod -> Expert A -> Expert B -> Mod
    MODERATED_INQUIRY = "moderated_inquiry"        # Mod -> Expert A -> Mod -> Expert B -> Mod
    CROSS_EXAMINATION = "cross_examination"        # Expert A <-> Expert B under Mod arbitration


@dataclass
class MultiAgentConfig:
    """Configured agent identities for roundtable roles."""
    moderator_id: str
    expert_a_id: str
    expert_b_id: str

    def validate_distinct(self) -> Tuple[bool, Optional[str]]:
        """Verify that all three agent IDs are non-empty and mutually distinct."""
        ids = [self.moderator_id.strip(), self.expert_a_id.strip(), self.expert_b_id.strip()]
        if any(not x for x in ids):
            return False, "All three agent roles (moderator, expert_a, expert_b) must be configured."
        if len(set(ids)) < 3:
            return False, "Agent IDs must be distinct for each role. Cannot reuse the same agent ID across roles."
        return True, None


@dataclass
class RoundtableTurn:
    """Recorded turn from a participant during an orchestrated roundtable."""
    turn_index: int
    speaker_role: str
    agent_id: str
    payload: str
    timestamp_ms: float
    addressed_to: Optional[str] = None
    context_tokens: int = 0
    duration_ms: Optional[float] = None
    is_simulated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MultiAgentRoundtableOrchestrator:
    """Orchestrates multi-turn roundtables across independent agent identities."""

    def __init__(
        self,
        config: Optional[MultiAgentConfig] = None,
        api_key: Optional[str] = None,
        base_url: str = "https://api.elevenlabs.io",
        timeout_seconds: float = 20.0,
    ):
        self.config = config or self._load_config_from_env()
        self._api_key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @classmethod
    def _load_config_from_env(cls) -> MultiAgentConfig:
        return MultiAgentConfig(
            moderator_id=os.environ.get("ELEVENLABS_AGENT_ID_MODERATOR", ""),
            expert_a_id=os.environ.get("ELEVENLABS_AGENT_ID_EXPERT_A", ""),
            expert_b_id=os.environ.get("ELEVENLABS_AGENT_ID_EXPERT_B", ""),
        )

    def check_readiness(self) -> Dict[str, Any]:
        """Check whether the environment is ready for live multi-agent execution."""
        valid, reason = self.config.validate_distinct()
        return {
            "ready": valid,
            "status": "READY" if valid else "BLOCKED",
            "reason": reason or "All distinct agent identities configured.",
            "configured_roles": {
                "moderator": self.config.moderator_id or "[MISSING]",
                "expert_a": self.config.expert_a_id or "[MISSING]",
                "expert_b": self.config.expert_b_id or "[MISSING]",
            },
            "instructions": (
                "To enable live multi-agent roundtables, set distinct agent IDs in your .env:\n"
                "  ELEVENLABS_AGENT_ID_MODERATOR=agent_...\n"
                "  ELEVENLABS_AGENT_ID_EXPERT_A=agent_...\n"
                "  ELEVENLABS_AGENT_ID_EXPERT_B=agent_...\n"
                "Each role requires a separate agent configuration with specialized instructions."
            ) if not valid else None,
        }

    def execute_offline_simulation(
        self,
        scenario: Scenario,
        session_id: str = "sim-multi-001",
    ) -> ConversationTrace:
        """Execute a deterministic reference simulation across three simulated roles.

        Clearly labeled with timing_mode="virtual_simulation" and is_simulated=True.
        """
        trace = ConversationTrace(
            scenario_id=scenario.id,
            session_id=session_id,
            target_type="multi_agent_roundtable_simulation",
            target_config="sequential_debate",
            timing_mode="virtual_simulation",
        )

        trace.add_event(TraceEvent(
            timestamp_ms=0.0,
            event_type=EventType.SESSION_START,
            payload={"topic": scenario.preconditions.initial_topic, "protocol": "sequential_debate"},
            is_simulated=True,
            event_provenance="local_virtual",
        ))

        current_ms = 100.0
        for turn in scenario.turns:
            # Simulate turn execution
            trace.add_event(TraceEvent(
                timestamp_ms=current_ms,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id=turn.speaker,
                intended_recipient=turn.target_expert,
                payload=turn.payload,
                turn_index=turn.turn_index,
                is_simulated=True,
                event_provenance="local_virtual",
                metadata={"role": turn.speaker, "addressed_to": turn.target_expert},
            ))
            current_ms += 1200.0

        trace.add_event(TraceEvent(
            timestamp_ms=current_ms,
            event_type=EventType.SESSION_END,
            payload={"status": "completed", "total_turns": len(scenario.turns)},
            is_simulated=True,
            event_provenance="local_virtual",
        ))

        return trace
