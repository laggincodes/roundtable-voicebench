"""Abstract TargetAdapter interface for VoiceBench.

Defines the contract between the evaluation engine and target voice systems,
including capability declaration, scenario execution, and lifecycle management.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from voicebench.models import AdapterCapabilities, ConversationTrace, Scenario


class TargetAdapter(ABC):
    """Abstract base class that decouples the VoiceBench runner from target implementations."""

    def __init__(self, target_type: str, target_config: str = "default"):
        self.target_type = target_type
        self.target_config = target_config

    @abstractmethod
    def get_capabilities(self) -> AdapterCapabilities:
        """Return the declared capabilities and timing mode of this adapter."""
        pass

    @abstractmethod
    def initialize(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize adapter resources, establish connection, or spin up fixtures."""
        pass

    @abstractmethod
    def execute_scenario(self, scenario: Scenario) -> ConversationTrace:
        """Execute a full scenario against the target and return the captured ConversationTrace."""
        pass

    @abstractmethod
    def teardown(self) -> None:
        """Clean up sockets, terminate child processes, and release resources."""
        pass

    def validate_scenario_compatibility(self, scenario: Scenario) -> List[str]:
        """Verify whether the scenario's requirements are supported by this adapter.

        Returns a list of missing capability descriptions, or an empty list if compatible.
        """
        caps = self.get_capabilities()
        unsupported: List[str] = []

        # Check audio input requirement
        if any(turn.input_type == "audio" for turn in scenario.turns) and not caps.supports_audio_input:
            unsupported.append("Scenario requires audio input, but target adapter does not support audio input.")

        # Check barge-in requirement
        if any(turn.barge_in for turn in scenario.turns) and not caps.supports_barge_in:
            unsupported.append("Scenario requires barge-in interruption, but target adapter declared supports_barge_in=False.")

        # Check tool call expectations
        if scenario.expectations.required_tool_call and not caps.supports_tool_calls:
            unsupported.append(
                f"Scenario expects tool call '{scenario.expectations.required_tool_call}', "
                "but target adapter declared supports_tool_calls=False."
            )

        return unsupported
