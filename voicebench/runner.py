"""Suite Execution Runner for VoiceBench."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import List, Optional
import uuid

from voicebench.adapters.base import TargetAdapter
from voicebench.evaluator import Evaluator
from voicebench.models import Scenario, ScenarioResult, SuiteResult, TestStatus


class SuiteRunner:
    """Orchestrates scenario execution and evaluation against a target adapter."""

    def __init__(self, adapter: TargetAdapter, evaluator: Optional[Evaluator] = None):
        self.adapter = adapter
        self.evaluator = evaluator or Evaluator()

    def run_suite(
        self,
        scenarios: List[Scenario],
        run_id: Optional[str] = None,
    ) -> SuiteResult:
        """Execute a list of scenarios sequentially and compile the SuiteResult."""
        current_run_id = run_id or f"run-{uuid.uuid4().hex[:8]}"
        timestamp_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

        results: List[ScenarioResult] = []
        passed = 0
        failed = 0
        inconclusive = 0
        not_run = 0

        self.adapter.initialize()

        try:
            for scenario in scenarios:
                trace = self.adapter.execute_scenario(scenario)
                result = self.evaluator.evaluate(
                    scenario=scenario,
                    trace=trace,
                    run_id=current_run_id,
                    target_type=self.adapter.target_type,
                    target_config=self.adapter.target_config,
                )
                results.append(result)

                if result.status == TestStatus.PASS:
                    passed += 1
                elif result.status == TestStatus.FAIL:
                    failed += 1
                elif result.status == TestStatus.INCONCLUSIVE:
                    inconclusive += 1
                elif result.status == TestStatus.NOT_RUN:
                    not_run += 1
        finally:
            self.adapter.teardown()

        return SuiteResult(
            run_id=current_run_id,
            target_type=self.adapter.target_type,
            target_config=self.adapter.target_config,
            timestamp_utc=timestamp_utc,
            total=len(scenarios),
            passed=passed,
            failed=failed,
            inconclusive=inconclusive,
            not_run=not_run,
            results=results,
            software_version=self.evaluator.software_version,
        )
