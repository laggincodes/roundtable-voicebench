"""Deterministic Evaluation Engine for VoiceBench.

Evaluates scenario expectations against captured ConversationTrace evidence
without relying on non-deterministic LLM judging.
Supports PASS, FAIL, INCONCLUSIVE, and NOT_RUN.
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Dict, List, Optional, Tuple

from voicebench.models import (
    ConversationTrace,
    Evidence,
    EventType,
    FailureCategory,
    Scenario,
    ScenarioResult,
    TestStatus,
)


class Evaluator:
    """Evaluates scenario expectations against a conversation trace deterministically."""

    def __init__(self, software_version: str = "0.1.0"):
        self.software_version = software_version

    def evaluate(
        self,
        scenario: Scenario,
        trace: Optional[ConversationTrace],
        run_id: str = "",
        target_type: str = "local_mock",
        target_config: str = "baseline",
    ) -> ScenarioResult:
        """Evaluate a scenario against a captured trace."""
        timestamp_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # 1. Check if scenario was never run
        if trace is None:
            return ScenarioResult(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                status=TestStatus.NOT_RUN,
                reason="Scenario was not executed (trace is missing).",
                evidence=None,
                target_type=target_type,
                target_config=target_config,
                run_id=run_id,
                timestamp_utc=timestamp_utc,
                software_version=self.software_version,
            )

        # 2. Check for infrastructure/transport errors
        transport_errors = [e for e in trace.events if e.event_type == EventType.TRANSPORT_ERROR]
        if transport_errors:
            err_event = transport_errors[0]
            return ScenarioResult(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                status=TestStatus.INCONCLUSIVE,
                failure_category=FailureCategory.FAIL_INFRA.value,
                reason=f"Transport error occurred during scenario execution: {err_event.payload}",
                evidence=Evidence(
                    turn_index=err_event.turn_index,
                    timestamp_ms=err_event.timestamp_ms,
                    speaker_id=err_event.speaker_id,
                    event_type=err_event.event_type.value,
                    quote_or_payload=err_event.payload,
                    notes="Premature transport or socket disconnection.",
                ),
                metrics={},
                target_type=target_type,
                target_config=target_config,
                run_id=run_id,
                timestamp_utc=timestamp_utc,
                software_version=self.software_version,
            )

        # 3. Check for empty agent interactions
        transcripts = trace.get_transcripts()
        if not transcripts and not [e for e in trace.events if e.event_type == EventType.SPEECH_CHUNK]:
            return ScenarioResult(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                status=TestStatus.INCONCLUSIVE,
                failure_category=FailureCategory.FAIL_INFRA.value,
                reason="Target agent emitted no transcripts or speech events. Insufficient evidence to evaluate.",
                evidence=None,
                metrics={},
                target_type=target_type,
                target_config=target_config,
                run_id=run_id,
                timestamp_utc=timestamp_utc,
                software_version=self.software_version,
            )

        expectations = scenario.expectations
        metrics: Dict[str, Optional[float]] = {
            "turn_count": len(scenario.turns),
            "response_latency_ms": None,
            "yield_latency_ms": None,
            "cross_talk_overlap_ms": None,
        }

        # 4. Check Addressed-Agent / Primary Speaker Routing
        if expectations.primary_speaker_id:
            # Look for the agent transcript responding to the addressed turn
            # Find the first agent transcript that is not from the user
            agent_transcripts = [t for t in transcripts if t.speaker_id not in ("user", "system")]
            if not agent_transcripts:
                return ScenarioResult(
                    scenario_id=scenario.id,
                    scenario_name=scenario.name,
                    status=TestStatus.INCONCLUSIVE,
                    failure_category=FailureCategory.FAIL_INFRA.value,
                    reason="No agent transcript captured to verify addressed speaker.",
                    evidence=None,
                    metrics=metrics,
                    target_type=target_type,
                    target_config=target_config,
                    run_id=run_id,
                    timestamp_utc=timestamp_utc,
                    software_version=self.software_version,
                )

            first_resp = agent_transcripts[0]
            if first_resp.speaker_id != expectations.primary_speaker_id:
                return ScenarioResult(
                    scenario_id=scenario.id,
                    scenario_name=scenario.name,
                    status=TestStatus.FAIL,
                    failure_category=FailureCategory.FAIL_ROUT.value,
                    reason=(
                        f"Addressed agent routing failure: expected '{expectations.primary_speaker_id}' to respond, "
                        f"but received response from '{first_resp.speaker_id}'."
                    ),
                    evidence=Evidence(
                        turn_index=first_resp.turn_index,
                        timestamp_ms=first_resp.timestamp_ms,
                        speaker_id=first_resp.speaker_id,
                        event_type=first_resp.event_type.value,
                        quote_or_payload=first_resp.payload,
                        notes="Wrong expert answered user query.",
                    ),
                    metrics=metrics,
                    target_type=target_type,
                    target_config=target_config,
                    run_id=run_id,
                    timestamp_utc=timestamp_utc,
                    software_version=self.software_version,
                )

        # 5. Check Disallowed Speakers
        if expectations.disallowed_speakers:
            for t in transcripts:
                if t.speaker_id in expectations.disallowed_speakers:
                    return ScenarioResult(
                        scenario_id=scenario.id,
                        scenario_name=scenario.name,
                        status=TestStatus.FAIL,
                        failure_category=FailureCategory.FAIL_ROUT.value,
                        reason=f"Disallowed speaker '{t.speaker_id}' spoke during scenario execution.",
                        evidence=Evidence(
                            turn_index=t.turn_index,
                            timestamp_ms=t.timestamp_ms,
                            speaker_id=t.speaker_id,
                            event_type=t.event_type.value,
                            quote_or_payload=t.payload,
                            notes="Disallowed speaker took the conversational floor.",
                        ),
                        metrics=metrics,
                        target_type=target_type,
                        target_config=target_config,
                        run_id=run_id,
                        timestamp_utc=timestamp_utc,
                        software_version=self.software_version,
                    )

        # 6. Check Interruption / Barge-in Yield Latency
        if expectations.max_yield_latency_ms is not None:
            # Find interrupt trigger event
            interrupt_triggers = [e for e in trace.events if e.event_type == EventType.INTERRUPT_TRIGGER]
            interrupt_acks = [e for e in trace.events if e.event_type == EventType.INTERRUPT_ACK]

            if not interrupt_triggers:
                return ScenarioResult(
                    scenario_id=scenario.id,
                    scenario_name=scenario.name,
                    status=TestStatus.INCONCLUSIVE,
                    failure_category=FailureCategory.FAIL_INFRA.value,
                    reason="Scenario expected barge-in yield verification, but no INTERRUPT_TRIGGER event was recorded.",
                    evidence=None,
                    metrics=metrics,
                    target_type=target_type,
                    target_config=target_config,
                    run_id=run_id,
                    timestamp_utc=timestamp_utc,
                    software_version=self.software_version,
                )

            if not interrupt_acks:
                return ScenarioResult(
                    scenario_id=scenario.id,
                    scenario_name=scenario.name,
                    status=TestStatus.FAIL,
                    failure_category=FailureCategory.FAIL_INT.value,
                    reason="Target agent completely ignored human barge-in interruption (no yield acknowledged).",
                    evidence=Evidence(
                        turn_index=interrupt_triggers[0].turn_index,
                        timestamp_ms=interrupt_triggers[0].timestamp_ms,
                        speaker_id="user",
                        event_type="INTERRUPT_TRIGGER",
                        quote_or_payload=interrupt_triggers[0].payload,
                        notes="Barge-in emitted but agent did not yield silence.",
                    ),
                    metrics=metrics,
                    target_type=target_type,
                    target_config=target_config,
                    run_id=run_id,
                    timestamp_utc=timestamp_utc,
                    software_version=self.software_version,
                )

            # Compute yield latency
            trigger = interrupt_triggers[0]
            ack = interrupt_acks[0]
            yield_latency = ack.metadata.get("yield_latency_ms")
            if yield_latency is None:
                yield_latency = round(ack.timestamp_ms - trigger.timestamp_ms, 2)
            metrics["yield_latency_ms"] = yield_latency

            if yield_latency > expectations.max_yield_latency_ms:
                return ScenarioResult(
                    scenario_id=scenario.id,
                    scenario_name=scenario.name,
                    status=TestStatus.FAIL,
                    failure_category=FailureCategory.FAIL_INT.value,
                    reason=(
                        f"Interruption yield latency threshold exceeded: measured {yield_latency:.1f}ms, "
                        f"maximum allowed is {expectations.max_yield_latency_ms:.1f}ms."
                    ),
                    evidence=Evidence(
                        turn_index=ack.turn_index,
                        timestamp_ms=ack.timestamp_ms,
                        speaker_id=ack.speaker_id,
                        event_type=ack.event_type.value,
                        quote_or_payload=ack.payload,
                        notes=f"Audible yield lag: {yield_latency}ms.",
                    ),
                    metrics=metrics,
                    target_type=target_type,
                    target_config=target_config,
                    run_id=run_id,
                    timestamp_utc=timestamp_utc,
                    software_version=self.software_version,
                )

        # 7. Check Forbidden Keywords (Role Drift / Speculation)
        if expectations.forbidden_keywords:
            for t in transcripts:
                if t.speaker_id in ("user", "system"):
                    continue
                text = str(t.payload).lower()
                for kw in expectations.forbidden_keywords:
                    if re.search(r"\b" + re.escape(kw.lower()) + r"\b", text):
                        return ScenarioResult(
                            scenario_id=scenario.id,
                            scenario_name=scenario.name,
                            status=TestStatus.FAIL,
                            failure_category=FailureCategory.FAIL_ROLE.value,
                            reason=f"Forbidden keyword '{kw}' detected in speaker '{t.speaker_id}' utterance (role/stance drift).",
                            evidence=Evidence(
                                turn_index=t.turn_index,
                                timestamp_ms=t.timestamp_ms,
                                speaker_id=t.speaker_id,
                                event_type=t.event_type.value,
                                quote_or_payload=t.payload,
                                notes=f"Detected forbidden phrase: '{kw}'",
                            ),
                            metrics=metrics,
                            target_type=target_type,
                            target_config=target_config,
                            run_id=run_id,
                            timestamp_utc=timestamp_utc,
                            software_version=self.software_version,
                        )

        # 8. Check Multi-Speaker Coordination & Cross-Talk Collisions
        if expectations.max_cross_talk_overlap_ms is not None:
            # Check for overlaps flagged in metadata
            overlap_events = [
                e for e in trace.events
                if isinstance(e.metadata, dict) and e.metadata.get("overlap_ms", 0.0) > expectations.max_cross_talk_overlap_ms
            ]
            if overlap_events:
                ov = overlap_events[0]
                measured_overlap = ov.metadata["overlap_ms"]
                metrics["cross_talk_overlap_ms"] = measured_overlap
                return ScenarioResult(
                    scenario_id=scenario.id,
                    scenario_name=scenario.name,
                    status=TestStatus.FAIL,
                    failure_category=FailureCategory.FAIL_COORD.value,
                    reason=(
                        f"Speech cross-talk collision detected: measured {measured_overlap:.1f}ms overlap, "
                        f"maximum allowed is {expectations.max_cross_talk_overlap_ms:.1f}ms."
                    ),
                    evidence=Evidence(
                        turn_index=ov.turn_index,
                        timestamp_ms=ov.timestamp_ms,
                        speaker_id=ov.speaker_id,
                        event_type=ov.event_type.value,
                        quote_or_payload=ov.payload,
                        notes=f"Speakers collided for {measured_overlap}ms.",
                    ),
                    metrics=metrics,
                    target_type=target_type,
                    target_config=target_config,
                    run_id=run_id,
                    timestamp_utc=timestamp_utc,
                    software_version=self.software_version,
                )

        # 9. Check Speaker Sequence
        if expectations.expected_speaker_sequence:
            actual_speakers = [t.speaker_id for t in transcripts if t.speaker_id not in ("user", "system")]
            if actual_speakers != expectations.expected_speaker_sequence:
                return ScenarioResult(
                    scenario_id=scenario.id,
                    scenario_name=scenario.name,
                    status=TestStatus.FAIL,
                    failure_category=FailureCategory.FAIL_COORD.value,
                    reason=f"Speaker turn sequence mismatch: expected {expectations.expected_speaker_sequence}, got {actual_speakers}.",
                    evidence=Evidence(
                        turn_index=transcripts[0].turn_index if transcripts else None,
                        speaker_id=str(actual_speakers),
                        event_type="SPEAKER_SEQUENCE",
                        quote_or_payload=actual_speakers,
                        notes="Roundtable turn alternation failed.",
                    ),
                    metrics=metrics,
                    target_type=target_type,
                    target_config=target_config,
                    run_id=run_id,
                    timestamp_utc=timestamp_utc,
                    software_version=self.software_version,
                )

        # 10. Check Required Keywords (Domain / Recovery terminology)
        if expectations.required_keywords:
            all_agent_text = " ".join(
                str(t.payload).lower() for t in transcripts if t.speaker_id not in ("user", "system")
            )
            missing_keywords = [
                kw for kw in expectations.required_keywords if not re.search(r"\b" + re.escape(kw.lower()), all_agent_text)
            ]
            # Require at least half or all depending on configuration
            if len(missing_keywords) > len(expectations.required_keywords) // 2:
                last_turn = transcripts[-1] if transcripts else None
                return ScenarioResult(
                    scenario_id=scenario.id,
                    scenario_name=scenario.name,
                    status=TestStatus.FAIL,
                    failure_category=FailureCategory.FAIL_ROLE.value,
                    reason=f"Missing expected domain keywords: {missing_keywords}.",
                    evidence=Evidence(
                        turn_index=last_turn.turn_index if last_turn else None,
                        timestamp_ms=last_turn.timestamp_ms if last_turn else None,
                        speaker_id=last_turn.speaker_id if last_turn else None,
                        event_type="TRANSCRIPT_FINAL",
                        quote_or_payload=last_turn.payload if last_turn else None,
                        notes=f"Keywords {missing_keywords} not found in agent responses.",
                    ),
                    metrics=metrics,
                    target_type=target_type,
                    target_config=target_config,
                    run_id=run_id,
                    timestamp_utc=timestamp_utc,
                    software_version=self.software_version,
                )

        # 11. Check Topic Recovery After Interruption
        if expectations.require_topic_recovery and expectations.recovery_keywords:
            # Check the last turn
            final_turn = [t for t in transcripts if t.turn_index == scenario.turns[-1].turn_index]
            if not final_turn:
                return ScenarioResult(
                    scenario_id=scenario.id,
                    scenario_name=scenario.name,
                    status=TestStatus.INCONCLUSIVE,
                    failure_category=FailureCategory.FAIL_INFRA.value,
                    reason="Missing final turn transcript to verify topic recovery.",
                    evidence=None,
                    metrics=metrics,
                    target_type=target_type,
                    target_config=target_config,
                    run_id=run_id,
                    timestamp_utc=timestamp_utc,
                    software_version=self.software_version,
                )

            final_text = str(final_turn[0].payload).lower()
            recovered = any(
                re.search(r"\b" + re.escape(kw.lower()), final_text) for kw in expectations.recovery_keywords
            )
            if not recovered:
                return ScenarioResult(
                    scenario_id=scenario.id,
                    scenario_name=scenario.name,
                    status=TestStatus.FAIL,
                    failure_category=FailureCategory.FAIL_REC.value,
                    reason="Post-interruption debate topic amnesia: agents failed to resume prior debate topic.",
                    evidence=Evidence(
                        turn_index=final_turn[0].turn_index,
                        timestamp_ms=final_turn[0].timestamp_ms,
                        speaker_id=final_turn[0].speaker_id,
                        event_type=final_turn[0].event_type.value,
                        quote_or_payload=final_turn[0].payload,
                        notes=f"None of recovery keywords {expectations.recovery_keywords} found in resumed turn.",
                    ),
                    metrics=metrics,
                    target_type=target_type,
                    target_config=target_config,
                    run_id=run_id,
                    timestamp_utc=timestamp_utc,
                    software_version=self.software_version,
                )

        # 12. Check Tool-Call Correctness & Fact Grounding
        if expectations.required_tool_call:
            tool_calls = trace.get_tool_calls()
            matching_tool = [
                tc for tc in tool_calls
                if isinstance(tc.payload, dict) and tc.payload.get("tool") == expectations.required_tool_call
            ]
            if not matching_tool:
                return ScenarioResult(
                    scenario_id=scenario.id,
                    scenario_name=scenario.name,
                    status=TestStatus.FAIL,
                    failure_category=FailureCategory.FAIL_TOOL.value,
                    reason=f"Required tool '{expectations.required_tool_call}' was never called by the agent.",
                    evidence=Evidence(
                        turn_index=transcripts[0].turn_index if transcripts else None,
                        speaker_id=transcripts[0].speaker_id if transcripts else None,
                        event_type="TOOL_CALL_MISSING",
                        quote_or_payload="No tool call recorded in trace.",
                        notes="Agent answered without invoking required grounding tool.",
                    ),
                    metrics=metrics,
                    target_type=target_type,
                    target_config=target_config,
                    run_id=run_id,
                    timestamp_utc=timestamp_utc,
                    software_version=self.software_version,
                )

            # Check expected tool args
            if expectations.expected_tool_args:
                actual_args = matching_tool[0].payload.get("args", {})
                for k, expected_v in expectations.expected_tool_args.items():
                    if actual_args.get(k) != expected_v:
                        return ScenarioResult(
                            scenario_id=scenario.id,
                            scenario_name=scenario.name,
                            status=TestStatus.FAIL,
                            failure_category=FailureCategory.FAIL_TOOL.value,
                            reason=f"Tool call parameter mismatch for '{k}': expected '{expected_v}', got '{actual_args.get(k)}'.",
                            evidence=Evidence(
                                turn_index=matching_tool[0].turn_index,
                                timestamp_ms=matching_tool[0].timestamp_ms,
                                speaker_id=matching_tool[0].speaker_id,
                                event_type="TOOL_CALL",
                                quote_or_payload=matching_tool[0].payload,
                                notes="Tool parameter schema mismatch.",
                            ),
                            metrics=metrics,
                            target_type=target_type,
                            target_config=target_config,
                            run_id=run_id,
                            timestamp_utc=timestamp_utc,
                            software_version=self.software_version,
                        )

            # Check numeric grounding in spoken text
            if expectations.expected_numeric_grounding:
                spoken_text = " ".join(str(t.payload) for t in transcripts if t.speaker_id not in ("user", "system"))
                if expectations.expected_numeric_grounding not in spoken_text:
                    return ScenarioResult(
                        scenario_id=scenario.id,
                        scenario_name=scenario.name,
                        status=TestStatus.FAIL,
                        failure_category=FailureCategory.FAIL_TOOL.value,
                        reason=(
                            f"Numeric fact grounding failure: expected '{expectations.expected_numeric_grounding}' "
                            f"in spoken response, but was not present in transcript."
                        ),
                        evidence=Evidence(
                            turn_index=transcripts[-1].turn_index if transcripts else None,
                            timestamp_ms=transcripts[-1].timestamp_ms if transcripts else None,
                            speaker_id=transcripts[-1].speaker_id if transcripts else None,
                            event_type="TRANSCRIPT_FINAL",
                            quote_or_payload=spoken_text,
                            notes="Spoken answer contradicted or lacked tool-grounded fact.",
                        ),
                        metrics=metrics,
                        target_type=target_type,
                        target_config=target_config,
                        run_id=run_id,
                        timestamp_utc=timestamp_utc,
                        software_version=self.software_version,
                    )

        # All deterministic assertions passed!
        primary_evidence = transcripts[0] if transcripts else trace.events[0]
        return ScenarioResult(
            scenario_id=scenario.id,
            scenario_name=scenario.name,
            status=TestStatus.PASS,
            reason="All deterministic assertions satisfied with verifiable evidence.",
            evidence=Evidence(
                turn_index=primary_evidence.turn_index,
                timestamp_ms=primary_evidence.timestamp_ms,
                speaker_id=primary_evidence.speaker_id,
                event_type=primary_evidence.event_type.value,
                quote_or_payload=primary_evidence.payload,
                notes="Verified compliant behavior.",
            ),
            metrics=metrics,
            target_type=target_type,
            target_config=target_config,
            run_id=run_id,
            timestamp_utc=timestamp_utc,
            software_version=self.software_version,
        )
