"""Local Mock Target Adapter for VoiceBench."""

from __future__ import annotations

from typing import Any, Dict, Optional

from voicebench.adapters.base import TargetAdapter
from voicebench.models import ConversationTrace, Scenario
from voicebench.targets.roundtable import SimulatedRoundtableTarget


class LocalMockAdapter(TargetAdapter):
    """Adapter that executes scenarios against the offline SimulatedRoundtableTarget.

    Guarantees deterministic, reproducible results without network access, credentials,
    or external API costs. Clearly labeled as a local simulation.
    """

    def __init__(self, target_config: str = "baseline"):
        super().__init__(target_type="local_mock", target_config=target_config)
        self._target: Optional[SimulatedRoundtableTarget] = None

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the simulated roundtable target with the selected configuration."""
        cfg_name = self.target_config
        if config and "target_config" in config:
            cfg_name = config["target_config"]
            self.target_config = cfg_name
        self._target = SimulatedRoundtableTarget(config_name=cfg_name)

    def execute_scenario(self, scenario: Scenario) -> ConversationTrace:
        """Execute the scenario deterministically and return the conversation trace."""
        if self._target is None:
            self.initialize()
        assert self._target is not None

        trace_collector = self._target.execute_scenario(scenario)
        return trace_collector.to_trace()

    def teardown(self) -> None:
        """Release target resources."""
        self._target = None
