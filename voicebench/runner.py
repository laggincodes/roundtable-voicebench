"""Suite Execution Runner for VoiceBench."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import List, Optional, Set
import uuid

from voicebench.adapters.base import TargetAdapter
from voicebench.evaluator import Evaluator
from voicebench.models import (
    FailureCategory,
    Scenario,
    ScenarioResult,
    SuiteResult,
    TestStatus,
    redact_secrets,
)


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
        executed_scenario_ids: Set[str] = set()

        try:
            self.adapter.initialize()
        except Exception as init_exc:
            # Initialization failure prevents running, mark all as INCONCLUSIVE
            sanitized_init = redact_secrets(str(init_exc))
            for sc in scenarios:
                results.append(
                    ScenarioResult(
                        scenario_id=sc.id,
                        scenario_name=sc.name,
                        status=TestStatus.INCONCLUSIVE,
                        failure_category=FailureCategory.FAIL_INFRA.value,
                        reason=f"Adapter initialization failed: {sanitized_init}",
                        diagnostic_details={"error_type": type(init_exc).__name__, "details": sanitized_init},
                        target_type=self.adapter.target_type,
                        target_config=self.adapter.target_config,
                        run_id=current_run_id,
                        timestamp_utc=timestamp_utc,
                        software_version=self.evaluator.software_version,
                        timing_mode=self.adapter.get_capabilities().timing_mode,
                    )
                )
                inconclusive += 1
            return SuiteResult(
                run_id=current_run_id,
                target_type=self.adapter.target_type,
                target_config=self.adapter.target_config,
                timestamp_utc=timestamp_utc,
                total=len(scenarios),
                passed=0,
                failed=0,
                inconclusive=inconclusive,
                not_run=0,
                results=results,
                software_version=self.evaluator.software_version,
                timing_mode=self.adapter.get_capabilities().timing_mode,
            )

        try:
            for scenario in scenarios:
                # 1. Check for pre-existing scenario definition or schema error
                if getattr(scenario, "_validation_error", None):
                    err_detail = redact_secrets(str(scenario._validation_error))
                    res = ScenarioResult(
                        scenario_id=scenario.id,
                        scenario_name=scenario.name,
                        status=TestStatus.FAIL,
                        failure_category=FailureCategory.FAIL_SCHEMA.value,
                        reason=f"Scenario schema validation error: {err_detail}",
                        diagnostic_details={"error_type": "ScenarioValidationError", "details": err_detail},
                        target_type=self.adapter.target_type,
                        target_config=self.adapter.target_config,
                        run_id=current_run_id,
                        timestamp_utc=timestamp_utc,
                        software_version=self.evaluator.software_version,
                        timing_mode=self.adapter.get_capabilities().timing_mode,
                    )
                    results.append(res)
                    failed += 1
                    executed_scenario_ids.add(scenario.id)
                    continue

                # 2. Execute scenario via target adapter
                trace = None
                try:
                    trace = self.adapter.execute_scenario(scenario)
                except Exception as adapter_exc:
                    sanitized_exc = redact_secrets(str(adapter_exc))
                    is_infra = any(
                        term in sanitized_exc.lower()
                        for term in ["timeout", "connection", "http", "timed out", "unreachable", "refused", "reset"]
                    )
                    cat = FailureCategory.FAIL_INFRA.value if is_infra else FailureCategory.FAIL_ADAPTER.value
                    res = ScenarioResult(
                        scenario_id=scenario.id,
                        scenario_name=scenario.name,
                        status=TestStatus.INCONCLUSIVE,
                        failure_category=cat,
                        reason=f"Adapter execution error: {sanitized_exc}",
                        diagnostic_details={"error_type": type(adapter_exc).__name__, "details": sanitized_exc},
                        target_type=self.adapter.target_type,
                        target_config=self.adapter.target_config,
                        run_id=current_run_id,
                        timestamp_utc=timestamp_utc,
                        software_version=self.evaluator.software_version,
                        timing_mode=self.adapter.get_capabilities().timing_mode,
                    )
                    results.append(res)
                    inconclusive += 1
                    executed_scenario_ids.add(scenario.id)
                    continue

                # 3. Evaluate trace against scenario expectations
                try:
                    result = self.evaluator.evaluate(
                        scenario=scenario,
                        trace=trace,
                        run_id=current_run_id,
                        target_type=self.adapter.target_type,
                        target_config=self.adapter.target_config,
                        timing_mode=self.adapter.get_capabilities().timing_mode,
                    )
                except Exception as eval_exc:
                    sanitized_exc = redact_secrets(str(eval_exc))
                    result = ScenarioResult(
                        scenario_id=scenario.id,
                        scenario_name=scenario.name,
                        status=TestStatus.INCONCLUSIVE,
                        failure_category=FailureCategory.FAIL_HARNESS.value,
                        reason=f"Internal evaluator harness error: {sanitized_exc}",
                        diagnostic_details={"error_type": type(eval_exc).__name__, "details": sanitized_exc},
                        target_type=self.adapter.target_type,
                        target_config=self.adapter.target_config,
                        run_id=current_run_id,
                        timestamp_utc=timestamp_utc,
                        software_version=self.evaluator.software_version,
                        timing_mode=self.adapter.get_capabilities().timing_mode,
                    )

                results.append(result)
                executed_scenario_ids.add(scenario.id)

                if result.status == TestStatus.PASS:
                    passed += 1
                elif result.status == TestStatus.FAIL:
                    failed += 1
                elif result.status == TestStatus.INCONCLUSIVE:
                    inconclusive += 1
                elif result.status == TestStatus.NOT_RUN:
                    not_run += 1

        except KeyboardInterrupt:
            # Preserve partial execution if interrupted
            pass
        finally:
            try:
                self.adapter.teardown()
            except Exception:
                pass

        # Identify any scenarios that were never executed
        for sc in scenarios:
            if sc.id not in executed_scenario_ids:
                results.append(
                    ScenarioResult(
                        scenario_id=sc.id,
                        scenario_name=sc.name,
                        status=TestStatus.NOT_RUN,
                        reason="Scenario was not executed (suite stopped early).",
                        target_type=self.adapter.target_type,
                        target_config=self.adapter.target_config,
                        run_id=current_run_id,
                        timestamp_utc=timestamp_utc,
                        software_version=self.evaluator.software_version,
                        timing_mode=self.adapter.get_capabilities().timing_mode,
                    )
                )
                not_run += 1

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
            timing_mode=self.adapter.get_capabilities().timing_mode,
        )
