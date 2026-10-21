"""Abstract TargetAdapter interface for VoiceBench."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from voicebench.models import ConversationTrace, Scenario


class TargetAdapter(ABC):
    """Abstract base class that decouples the VoiceBench runner from target implementations."""

    def __init__(self, target_type: str, target_config: str = "default"):
        self.target_type = target_type
        self.target_config = target_config

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
