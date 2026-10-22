"""Deterministic Evaluation Engine for VoiceBench.

Evaluates scenario expectations against captured ConversationTrace evidence
without relying on non-deterministic LLM judging.
Supports PASS, FAIL, INCONCLUSIVE, and NOT_RUN.
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from voicebench.models import (
    ConversationTrace,
    Evidence,
    EventType,
    FailureCategory,
    Scenario,
    ScenarioResult,
    TestStatus,
    redact_secrets,
)


def normalize_transcript_text(text: Optional[str]) -> str:
    """Normalize text for deterministic scenario alignment comparison.

    Performs explicit, documented normalization:
      - Strips leading and trailing whitespace
      - Converts to lower-case
      - Replaces punctuation characters with spaces
      - Collapses consecutive whitespace characters into a single space
    """
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def validate_scenario_input_alignment(
    scenario: Scenario,
    trace: ConversationTrace,
) -> Tuple[bool, Optional[str], Optional[int], List[str]]:
    """Validate that required scenario user turns are observed in order within the transcript.

    Returns:
        (is_aligned, failure_reason, failed_turn_index, observed_user_texts)
    """
    required_user_turns = [t for t in scenario.turns if t.speaker in ("user", "human_user")]
    if not required_user_turns:
        return True, None, None, []

    user_events = [
        e for e in trace.events
        if e.event_type == EventType.USER_INPUT or e.speaker_id in ("user", "human_user")
    ]
    observed_user_texts = [str(e.payload) if e.payload is not None else "" for e in user_events]

    if not user_events:
        return (
            False,
            f"No user input turns observed in conversation transcript (required {len(required_user_turns)} turn(s)).",
            required_user_turns[0].turn_index,
            observed_user_texts,
        )

    norm_observed = [normalize_transcript_text(t) for t in observed_user_texts]
    current_idx = 0

    for req_turn in required_user_turns:
        norm_req = normalize_transcript_text(req_turn.payload)
        match_idx = None
        for j in range(current_idx, len(norm_observed)):
            if norm_observed[j] == norm_req:
                match_idx = j
                break

        if match_idx is not None:
            current_idx = match_idx + 1
        else:
            earlier_match = any(norm_observed[k] == norm_req for k in range(0, current_idx))
            if earlier_match:
                return (
                    False,
                    f"Required scenario user input (turn {req_turn.turn_index}: '{req_turn.payload}') "
                    "appeared out of order in conversation transcript.",
                    req_turn.turn_index,
                    observed_user_texts,
                )
            else:
                return (
                    False,
                    f"Required scenario user input (turn {req_turn.turn_index}: '{req_turn.payload}') "
                    "was not observed in conversation transcript.",
                    req_turn.turn_index,
                    observed_user_texts,
                )

    return True, None, None, observed_user_texts


def is_tool_result_empty(result_data: Any) -> bool:
    """Determine whether a tool result payload is considered empty.

    Recognizes standard empty constructs:
      - None
      - Empty strings, lists, tuples, sets, or dicts
      - Tool result payloads with zero chunk count or explicit empty status
      - Explicit empty item/document lists
    """
    if result_data is None:
        return True
    if isinstance(result_data, (str, list, tuple, set)) and len(result_data) == 0:
        return True
    if isinstance(result_data, dict):
        if len(result_data) == 0:
            return True
        if result_data.get("chunk_count") == 0:
            return True
        status_val = str(result_data.get("status", "")).strip().lower()
        if status_val in ("no_matching_documents", "no_matching_docs", "empty", "not_found"):
            return True
        for list_key in ("items", "results", "documents", "data"):
            if list_key in result_data and isinstance(result_data[list_key], list) and len(result_data[list_key]) == 0:
                return True
        return False
    return False


def extract_tool_result_payload(tr: TraceEvent) -> Tuple[Any, bool, Optional[str], bool]:
    """Extract result data, error status, error message, and unparseable flag from a TOOL_RESULT event.

    Returns:
        (result_data, is_error, error_message, is_unparseable)
    """
    if not isinstance(tr.payload, dict):
        return tr.payload, False, None, False

    is_error = bool(tr.payload.get("is_error", False))
    raw_err = tr.payload.get("error") or tr.payload.get("raw_error_message")

    res_raw = tr.payload.get("result") if "result" in tr.payload else tr.payload

    if isinstance(res_raw, str) and res_raw.strip().startswith(("{", "[")):
        try:
            parsed = json.loads(res_raw)
            return parsed, is_error, raw_err, False
        except Exception:
            return res_raw, is_error, raw_err, True

    return res_raw, is_error, raw_err, False


def parse_and_validate_path(path: str) -> Tuple[bool, Optional[str], List[Union[str, int]]]:
    """Parse and validate a nested tool result path expression.

    Grammar:
      path ::= segment ('.' segment | '[' index ']')*
      segment ::= identifier | '[' index ']'
      identifier ::= [a-zA-Z0-9_-]+
      index ::= [0-9]+

    Returns:
        (is_valid, error_message, tokens)
    """
    if not isinstance(path, str):
        return False, f"Path must be a string, got {type(path).__name__}", []
    p = path.strip()
    if not p:
        return False, "Path expression cannot be empty", []
    if " " in p or "\t" in p or "\n" in p:
        return False, f"Whitespace is not permitted in path expression: '{path}'", []
    if p.startswith("."):
        return False, f"Path cannot start with a dot: '{path}'", []
    if p.endswith("."):
        return False, f"Path cannot end with a dot: '{path}'", []
    if ".." in p:
        return False, f"Consecutive dots are not allowed: '{path}'", []

    tokens: List[Union[str, int]] = []
    i = 0
    n = len(p)
    while i < n:
        if p[i] == ".":
            i += 1
            if i >= n or p[i] == ".":
                return False, f"Invalid dot placement in path: '{path}'", []
            continue

        if p[i] == "[":
            close_idx = p.find("]", i)
            if close_idx == -1:
                return False, f"Unclosed bracket '[' in path: '{path}'", []
            idx_str = p[i + 1 : close_idx]
            if not idx_str:
                return False, f"Empty index '[]' in path: '{path}'", []
            if not idx_str.isdigit():
                return False, f"Array index must be a non-negative integer, got '{idx_str}' in path: '{path}'", []
            tokens.append(int(idx_str))
            i = close_idx + 1
            if i < n and p[i] not in (".", "["):
                return False, f"Missing dot separator after bracket index at '{p[i:]}' in path: '{path}'", []
            continue

        if p[i] == "]":
            return False, f"Unexpected closing bracket ']' in path: '{path}'", []

        start = i
        while i < n and p[i] not in (".", "[", "]"):
            if not (p[i].isalnum() or p[i] in ("_", "-")):
                return False, f"Invalid character '{p[i]}' in path: '{path}'", []
            i += 1
        key = p[start:i]
        if not key:
            return False, f"Invalid empty segment in path: '{path}'", []
        tokens.append(key)

    if not tokens:
        return False, f"No valid tokens parsed from path: '{path}'", []

    return True, None, tokens


def resolve_path_tokens(data: Any, tokens: List[Union[str, int]]) -> Tuple[bool, Any]:
    """Resolve parsed tokens against data without coercing types.

    Returns:
        (exists: bool, value: Any)
    """
    curr = data
    for token in tokens:
        if isinstance(token, str):
            if not isinstance(curr, dict) or token not in curr:
                return False, None
            curr = curr[token]
        elif isinstance(token, int):
            if not isinstance(curr, list) or token < 0 or token >= len(curr):
                return False, None
            curr = curr[token]
        else:
            return False, None
    return True, curr


def values_are_equal_strict(actual: Any, expected: Any) -> bool:
    """Compare two values deterministically without coercing incompatible types.

    Distinguishes:
      - bool from int/float (e.g. True != 1, False != 0)
      - string from number (e.g. "34.5" != 34.5)
      - null/None from non-null
    Allows int/float numerical equivalence if neither is a bool (e.g. 34 == 34.0).
    """
    if expected is None:
        return actual is None
    if actual is None:
        return False

    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(actual) is type(expected) and actual == expected

    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return actual == expected

    if type(actual) is not type(expected):
        return False

    if isinstance(expected, dict):
        if len(actual) != len(expected):
            return False
        return all(k in actual and values_are_equal_strict(actual[k], v) for k, v in expected.items())

    if isinstance(expected, list):
        if len(actual) != len(expected):
            return False
        return all(values_are_equal_strict(a, e) for a, e in zip(actual, expected))

    return actual == expected


def correlate_tool_result(
    trace: ConversationTrace,
    tool_call: TraceEvent,
    required_tool: str,
) -> Tuple[Optional[TraceEvent], Optional[str]]:
    """Harden correlation between a tool call and its corresponding TOOL_RESULT event.

    Matching hierarchy:
      1. Explicit request_id correlation:
         - If tool_call has request_id, find results matching that request_id.
         - Validates that tool name (if present) agrees; rejects conflicting tools.
         - Rejects multiple results claiming the same request_id as ambiguous.
         - Does not fall back to other results with conflicting request_ids.
      2. Tool name and turn_index correlation (when request_id is not present on tool call):
         - Excludes results with explicit request_ids belonging to other calls.
         - Excludes results explicitly belonging to a different tool.
         - Correlates by turn_index if unambiguous.
      3. Single compatible result fallback (unambiguous trace).
      4. Ambiguity / Conflict detection:
         - Returns (None, error_reason) if multiple candidate results are equally plausible or if identifiers conflict.

    Returns:
        (matched_result, ambiguity_or_conflict_error_message)
    """
    candidate_results = [e for e in trace.events if e.event_type == EventType.TOOL_RESULT]
    if not candidate_results:
        return None, None

    tc_payload = tool_call.payload if isinstance(tool_call.payload, dict) else {}
    tc_req_id = tc_payload.get("request_id")

    # Case 1: Tool call has an explicit request_id
    if tc_req_id:
        req_id_matches = [
            tr for tr in candidate_results
            if isinstance(tr.payload, dict) and tr.payload.get("request_id") == tc_req_id
        ]
        if len(req_id_matches) > 1:
            return None, (
                f"Ambiguous tool result correlation: multiple tool results ({len(req_id_matches)}) "
                f"found sharing request_id '{tc_req_id}'."
            )
        if len(req_id_matches) == 1:
            res_event = req_id_matches[0]
            if isinstance(res_event.payload, dict):
                res_tool = res_event.payload.get("tool")
                if res_tool and res_tool != required_tool:
                    return None, (
                        f"Conflicting tool correlation: result with request_id '{tc_req_id}' "
                        f"belongs to tool '{res_tool}', but expected tool '{required_tool}'."
                    )
            return res_event, None

        # 0 matches with this request_id: check for conflicts
        conflicting_results = [
            tr for tr in candidate_results
            if isinstance(tr.payload, dict) and tr.payload.get("request_id") and tr.payload.get("request_id") != tc_req_id
        ]
        if conflicting_results:
            conflicting_ids = [r.payload.get("request_id") for r in conflicting_results]
            return None, (
                f"Conflicting tool correlation: tool call specified request_id '{tc_req_id}', "
                f"but available tool results have non-matching request_id identifiers ({conflicting_ids})."
            )
        return None, None

    # Case 2: Tool call does not specify request_id
    compatible: List[TraceEvent] = []
    for tr in candidate_results:
        if isinstance(tr.payload, dict):
            res_tool = tr.payload.get("tool")
            if res_tool and res_tool != required_tool:
                continue
            r_id = tr.payload.get("request_id")
            if r_id:
                other_calls = [
                    c for c in trace.get_tool_calls()
                    if c != tool_call and isinstance(c.payload, dict) and c.payload.get("request_id") == r_id
                ]
                if other_calls:
                    continue
        compatible.append(tr)

    if not compatible:
        return None, None

    # Try correlation by turn_index
    if tool_call.turn_index is not None:
        turn_matches = [tr for tr in compatible if tr.turn_index == tool_call.turn_index]
        if len(turn_matches) == 1:
            return turn_matches[0], None
        if len(turn_matches) > 1:
            return None, (
                f"Ambiguous tool result correlation: multiple tool results ({len(turn_matches)}) "
                f"found for tool '{required_tool}' at turn {tool_call.turn_index} without request_id correlation."
            )

    # Fallback to single compatible result if unambiguous
    if len(compatible) == 1:
        return compatible[0], None

    return None, (
        f"Ambiguous tool result correlation: {len(compatible)} candidate tool results found for "
        f"tool '{required_tool}' without distinct correlation identifiers."
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
        timing_mode: Optional[str] = None,
        validate_input_alignment: Optional[bool] = None,
    ) -> ScenarioResult:
        """Evaluate a scenario against a captured trace and preserve resolved timing mode."""
        res = self._evaluate_internal(
            scenario=scenario,
            trace=trace,
            run_id=run_id,
            target_type=target_type,
            target_config=target_config,
            validate_input_alignment=validate_input_alignment,
        )
        resolved_timing = timing_mode or (trace.timing_mode if trace else "virtual_simulation")
        res.timing_mode = resolved_timing
        return res

    def _evaluate_internal(
        self,
        scenario: Scenario,
        trace: Optional[ConversationTrace],
        run_id: str = "",
        target_type: str = "local_mock",
        target_config: str = "baseline",
        validate_input_alignment: Optional[bool] = None,
    ) -> ScenarioResult:
        """Internal deterministic evaluation logic."""
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
            sanitized_payload = redact_secrets(str(err_event.payload))
            return ScenarioResult(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                status=TestStatus.INCONCLUSIVE,
                failure_category=FailureCategory.FAIL_INFRA.value,
                reason=f"Transport error occurred during scenario execution: {sanitized_payload}",
                evidence=Evidence(
                    turn_index=err_event.turn_index,
                    timestamp_ms=err_event.timestamp_ms,
                    speaker_id=err_event.speaker_id,
                    event_type=err_event.event_type.value,
                    quote_or_payload=err_event.payload,
                    notes="Premature transport or socket disconnection.",
                ),
                metrics={},
                diagnostic_details={
                    "error_type": "TransportError",
                    "payload": sanitized_payload,
                },
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
                diagnostic_details={"error_type": "NoAgentInteractionError"},
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

        # 3b. Scenario-to-Transcript Input Alignment Preflight Check
        effective_target_type = trace.target_type if trace and trace.target_type else target_type
        should_validate_alignment = (
            validate_input_alignment is True
            or (
                validate_input_alignment is not False
                and effective_target_type in ("elevenlabs", "elevenlabs_convai")
            )
        )
        if should_validate_alignment:
            is_aligned, failure_reason, failed_turn_idx, observed_user_texts = validate_scenario_input_alignment(
                scenario, trace
            )
            if not is_aligned:
                return ScenarioResult(
                    scenario_id=scenario.id,
                    scenario_name=scenario.name,
                    status=TestStatus.INCONCLUSIVE,
                    failure_category=FailureCategory.FAIL_INFRA.value,
                    reason=f"SCENARIO_INPUT_NOT_OBSERVED: {failure_reason}",
                    evidence=Evidence(
                        turn_index=failed_turn_idx,
                        timestamp_ms=None,
                        speaker_id="user",
                        event_type=EventType.USER_INPUT.value,
                        quote_or_payload={"observed_user_inputs": observed_user_texts},
                        notes="Conversation transcript does not contain the required scenario user inputs in expected order.",
                    ),
                    metrics=metrics,
                    diagnostic_details={"error_type": "ScenarioInputAlignmentError", "reason": failure_reason},
                    target_type=target_type,
                    target_config=target_config,
                    run_id=run_id,
                    timestamp_utc=timestamp_utc,
                    software_version=self.software_version,
                )

        failures: List[Dict[str, Any]] = []
        evidences: List[Evidence] = []

        # 4. Check Addressed-Agent / Primary Speaker Routing
        if expectations.primary_speaker_id:
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
                    diagnostic_details={"error_type": "MissingAgentTranscript"},
                    target_type=target_type,
                    target_config=target_config,
                    run_id=run_id,
                    timestamp_utc=timestamp_utc,
                    software_version=self.software_version,
                )

            first_resp = agent_transcripts[0]
            if first_resp.speaker_id != expectations.primary_speaker_id:
                ev = Evidence(
                    turn_index=first_resp.turn_index,
                    timestamp_ms=first_resp.timestamp_ms,
                    speaker_id=first_resp.speaker_id,
                    event_type=first_resp.event_type.value,
                    quote_or_payload=first_resp.payload,
                    notes="Wrong expert answered user query.",
                )
                failures.append({
                    "assertion": "primary_speaker_id",
                    "failure_category": FailureCategory.FAIL_ROUT.value,
                    "reason": (
                        f"Addressed agent routing failure: expected '{expectations.primary_speaker_id}' to respond, "
                        f"but received response from '{first_resp.speaker_id}'."
                    ),
                    "expected": expectations.primary_speaker_id,
                    "actual": first_resp.speaker_id,
                    "evidence": ev.to_dict(),
                })
                evidences.append(ev)

        # 5. Check Disallowed Speakers
        if expectations.disallowed_speakers:
            for t in transcripts:
                if t.speaker_id in expectations.disallowed_speakers:
                    ev = Evidence(
                        turn_index=t.turn_index,
                        timestamp_ms=t.timestamp_ms,
                        speaker_id=t.speaker_id,
                        event_type=t.event_type.value,
                        quote_or_payload=t.payload,
                        notes="Disallowed speaker took the conversational floor.",
                    )
                    failures.append({
                        "assertion": "disallowed_speakers",
                        "failure_category": FailureCategory.FAIL_ROUT.value,
                        "reason": f"Disallowed speaker '{t.speaker_id}' spoke during scenario execution.",
                        "expected": f"None of {expectations.disallowed_speakers}",
                        "actual": t.speaker_id,
                        "evidence": ev.to_dict(),
                    })
                    evidences.append(ev)

        # 6. Check Interruption / Barge-in Yield Latency
        if expectations.max_yield_latency_ms is not None:
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
                    diagnostic_details={"error_type": "MissingInterruptTrigger"},
                    target_type=target_type,
                    target_config=target_config,
                    run_id=run_id,
                    timestamp_utc=timestamp_utc,
                    software_version=self.software_version,
                )

            if not interrupt_acks:
                ev = Evidence(
                    turn_index=interrupt_triggers[0].turn_index,
                    timestamp_ms=interrupt_triggers[0].timestamp_ms,
                    speaker_id="user",
                    event_type="INTERRUPT_TRIGGER",
                    quote_or_payload=interrupt_triggers[0].payload,
                    notes="Barge-in emitted but agent did not yield silence.",
                )
                failures.append({
                    "assertion": "max_yield_latency_ms",
                    "failure_category": FailureCategory.FAIL_INT.value,
                    "reason": "Target agent completely ignored human barge-in interruption (no yield acknowledged).",
                    "expected": f"<= {expectations.max_yield_latency_ms:.1f}ms yield acknowledgment",
                    "actual": "No yield acknowledged",
                    "evidence": ev.to_dict(),
                })
                evidences.append(ev)
            else:
                trigger = interrupt_triggers[0]
                ack = interrupt_acks[0]
                yield_latency = ack.metadata.get("yield_latency_ms")
                if yield_latency is None:
                    yield_latency = round(ack.timestamp_ms - trigger.timestamp_ms, 2)
                metrics["yield_latency_ms"] = yield_latency

                if yield_latency > expectations.max_yield_latency_ms:
                    ev = Evidence(
                        turn_index=ack.turn_index,
                        timestamp_ms=ack.timestamp_ms,
                        speaker_id=ack.speaker_id,
                        event_type=ack.event_type.value,
                        quote_or_payload=ack.payload,
                        notes=f"Audible yield lag: {yield_latency}ms.",
                    )
                    failures.append({
                        "assertion": "max_yield_latency_ms",
                        "failure_category": FailureCategory.FAIL_INT.value,
                        "reason": (
                            f"Interruption yield latency threshold exceeded: measured {yield_latency:.1f}ms, "
                            f"maximum allowed is {expectations.max_yield_latency_ms:.1f}ms."
                        ),
                        "expected": f"<= {expectations.max_yield_latency_ms:.1f}ms",
                        "actual": f"{yield_latency:.1f}ms",
                        "evidence": ev.to_dict(),
                    })
                    evidences.append(ev)

        # 7. Check Forbidden Keywords (Role Drift / Speculation)
        if expectations.forbidden_keywords:
            for t in transcripts:
                if t.speaker_id in ("user", "system"):
                    continue
                text = str(t.payload).lower()
                for kw in expectations.forbidden_keywords:
                    if re.search(r"\b" + re.escape(kw.lower()) + r"\b", text):
                        ev = Evidence(
                            turn_index=t.turn_index,
                            timestamp_ms=t.timestamp_ms,
                            speaker_id=t.speaker_id,
                            event_type=t.event_type.value,
                            quote_or_payload=t.payload,
                            notes=f"Detected forbidden phrase: '{kw}'",
                        )
                        failures.append({
                            "assertion": "forbidden_keywords",
                            "failure_category": FailureCategory.FAIL_ROLE.value,
                            "reason": f"Forbidden keyword '{kw}' detected in speaker '{t.speaker_id}' utterance (role/stance drift).",
                            "expected": f"Utterance free of '{kw}'",
                            "actual": f"Spoke '{kw}' in '{t.payload}'",
                            "evidence": ev.to_dict(),
                        })
                        evidences.append(ev)

        # 8. Check Multi-Speaker Coordination & Cross-Talk Collisions
        if expectations.max_cross_talk_overlap_ms is not None:
            overlap_events = [
                e for e in trace.events
                if isinstance(e.metadata, dict) and e.metadata.get("overlap_ms", 0.0) > expectations.max_cross_talk_overlap_ms
            ]
            if overlap_events:
                ov = overlap_events[0]
                measured_overlap = ov.metadata["overlap_ms"]
                metrics["cross_talk_overlap_ms"] = measured_overlap
                ev = Evidence(
                    turn_index=ov.turn_index,
                    timestamp_ms=ov.timestamp_ms,
                    speaker_id=ov.speaker_id,
                    event_type=ov.event_type.value,
                    quote_or_payload=ov.payload,
                    notes=f"Speakers collided for {measured_overlap}ms.",
                )
                failures.append({
                    "assertion": "max_cross_talk_overlap_ms",
                    "failure_category": FailureCategory.FAIL_COORD.value,
                    "reason": (
                        f"Speech cross-talk collision detected: measured {measured_overlap:.1f}ms overlap, "
                        f"maximum allowed is {expectations.max_cross_talk_overlap_ms:.1f}ms."
                    ),
                    "expected": f"<= {expectations.max_cross_talk_overlap_ms:.1f}ms",
                    "actual": f"{measured_overlap:.1f}ms",
                    "evidence": ev.to_dict(),
                })
                evidences.append(ev)

        # 9. Check Speaker Sequence
        if expectations.expected_speaker_sequence:
            actual_speakers = [t.speaker_id for t in transcripts if t.speaker_id not in ("user", "system")]
            if actual_speakers != expectations.expected_speaker_sequence:
                ev = Evidence(
                    turn_index=transcripts[0].turn_index if transcripts else None,
                    speaker_id=str(actual_speakers),
                    event_type="SPEAKER_SEQUENCE",
                    quote_or_payload=actual_speakers,
                    notes="Roundtable turn alternation failed.",
                )
                failures.append({
                    "assertion": "expected_speaker_sequence",
                    "failure_category": FailureCategory.FAIL_COORD.value,
                    "reason": f"Speaker turn sequence mismatch: expected {expectations.expected_speaker_sequence}, got {actual_speakers}.",
                    "expected": expectations.expected_speaker_sequence,
                    "actual": actual_speakers,
                    "evidence": ev.to_dict(),
                })
                evidences.append(ev)

        # 10. Check Required Keywords (Domain / Recovery terminology)
        if expectations.required_keywords:
            all_agent_text = " ".join(
                str(t.payload).lower() for t in transcripts if t.speaker_id not in ("user", "system")
            )
            missing_keywords = [
                kw for kw in expectations.required_keywords if not re.search(r"\b" + re.escape(kw.lower()), all_agent_text)
            ]
            if len(missing_keywords) > len(expectations.required_keywords) // 2:
                last_turn = transcripts[-1] if transcripts else None
                ev = Evidence(
                    turn_index=last_turn.turn_index if last_turn else None,
                    timestamp_ms=last_turn.timestamp_ms if last_turn else None,
                    speaker_id=last_turn.speaker_id if last_turn else None,
                    event_type="TRANSCRIPT_FINAL",
                    quote_or_payload=last_turn.payload if last_turn else None,
                    notes=f"Keywords {missing_keywords} not found in agent responses.",
                )
                failures.append({
                    "assertion": "required_keywords",
                    "failure_category": FailureCategory.FAIL_ROLE.value,
                    "reason": f"Missing expected domain keywords: {missing_keywords}.",
                    "expected": expectations.required_keywords,
                    "actual": f"Missing: {missing_keywords}",
                    "evidence": ev.to_dict(),
                })
                evidences.append(ev)

        # 11. Check Topic Recovery After Interruption
        if expectations.require_topic_recovery and expectations.recovery_keywords:
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
                    diagnostic_details={"error_type": "MissingFinalTurnTranscript"},
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
                ev = Evidence(
                    turn_index=final_turn[0].turn_index,
                    timestamp_ms=final_turn[0].timestamp_ms,
                    speaker_id=final_turn[0].speaker_id,
                    event_type=final_turn[0].event_type.value,
                    quote_or_payload=final_turn[0].payload,
                    notes=f"None of recovery keywords {expectations.recovery_keywords} found in resumed turn.",
                )
                failures.append({
                    "assertion": "require_topic_recovery",
                    "failure_category": FailureCategory.FAIL_REC.value,
                    "reason": "Post-interruption debate topic amnesia: agents failed to resume prior debate topic.",
                    "expected": f"At least one of {expectations.recovery_keywords}",
                    "actual": "None observed in final turn",
                    "evidence": ev.to_dict(),
                })
                evidences.append(ev)

        # 12. Check Tool-Call Correctness & Fact Grounding
        if expectations.required_tool_call:
            tool_calls = trace.get_tool_calls()
            matching_tool = [
                tc for tc in tool_calls
                if isinstance(tc.payload, dict) and tc.payload.get("tool") == expectations.required_tool_call
            ]
            if not matching_tool:
                ev = Evidence(
                    turn_index=transcripts[0].turn_index if transcripts else None,
                    speaker_id=transcripts[0].speaker_id if transcripts else None,
                    event_type="TOOL_CALL_MISSING",
                    quote_or_payload="No tool call recorded in trace.",
                    notes="Agent answered without invoking required grounding tool.",
                )
                failures.append({
                    "assertion": "required_tool_call",
                    "failure_category": FailureCategory.FAIL_TOOL.value,
                    "reason": f"Required tool '{expectations.required_tool_call}' was never called by the agent.",
                    "expected": expectations.required_tool_call,
                    "actual": "No tool call recorded in trace",
                    "evidence": ev.to_dict(),
                })
                evidences.append(ev)
            else:
                primary_tc = matching_tool[0]

                # Check expected tool args
                if expectations.expected_tool_args:
                    actual_args = primary_tc.payload.get("args", {})
                    for k, expected_v in expectations.expected_tool_args.items():
                        if actual_args.get(k) != expected_v:
                            ev = Evidence(
                                turn_index=primary_tc.turn_index,
                                timestamp_ms=primary_tc.timestamp_ms,
                                speaker_id=primary_tc.speaker_id,
                                event_type="TOOL_CALL",
                                quote_or_payload=primary_tc.payload,
                                notes="Tool parameter schema mismatch.",
                            )
                            failures.append({
                                "assertion": f"expected_tool_args.{k}",
                                "failure_category": FailureCategory.FAIL_TOOL.value,
                                "reason": f"Tool call parameter mismatch for '{k}': expected '{expected_v}', got '{actual_args.get(k)}'.",
                                "expected": expected_v,
                                "actual": actual_args.get(k),
                                "evidence": ev.to_dict(),
                            })
                            evidences.append(ev)

                # Correlate and validate tool result evidence
                has_result_assertions = (
                    expectations.expected_tool_result is not None
                    or expectations.required_tool_result_fields is not None
                    or expectations.require_nonempty_tool_result is not None
                    or expectations.expected_tool_result_paths is not None
                    or expectations.required_tool_result_paths is not None
                )

                matched_tr, correlation_err = correlate_tool_result(
                    trace=trace,
                    tool_call=primary_tc,
                    required_tool=expectations.required_tool_call,
                )

                if correlation_err is not None:
                    return ScenarioResult(
                        scenario_id=scenario.id,
                        scenario_name=scenario.name,
                        status=TestStatus.INCONCLUSIVE,
                        failure_category=FailureCategory.FAIL_INFRA.value,
                        reason=correlation_err,
                        evidence=Evidence(
                            turn_index=primary_tc.turn_index,
                            timestamp_ms=primary_tc.timestamp_ms,
                            speaker_id=primary_tc.speaker_id,
                            event_type="TOOL_RESULT_AMBIGUOUS",
                            quote_or_payload=None,
                            notes=correlation_err,
                        ),
                        metrics=metrics,
                        diagnostic_details={"error_type": "ToolCorrelationError", "reason": correlation_err},
                        target_type=target_type,
                        target_config=target_config,
                        run_id=run_id,
                        timestamp_utc=timestamp_utc,
                        software_version=self.software_version,
                    )

                if matched_tr is None:
                    if has_result_assertions:
                        return ScenarioResult(
                            scenario_id=scenario.id,
                            scenario_name=scenario.name,
                            status=TestStatus.INCONCLUSIVE,
                            failure_category=FailureCategory.FAIL_INFRA.value,
                            reason=(
                                f"Missing tool result evidence: required tool '{expectations.required_tool_call}' was called, "
                                "but no corresponding tool result was captured in the conversation trace."
                            ),
                            evidence=Evidence(
                                turn_index=primary_tc.turn_index,
                                timestamp_ms=primary_tc.timestamp_ms,
                                speaker_id=primary_tc.speaker_id,
                                event_type="TOOL_RESULT_MISSING",
                                quote_or_payload=None,
                                notes="Tool result assertion specified but no result event found in trace.",
                            ),
                            metrics=metrics,
                            diagnostic_details={"error_type": "MissingToolResultEvidence"},
                            target_type=target_type,
                            target_config=target_config,
                            run_id=run_id,
                            timestamp_utc=timestamp_utc,
                            software_version=self.software_version,
                        )
                else:
                    res_data, is_err, err_detail, is_unparseable = extract_tool_result_payload(matched_tr)

                    if is_err:
                        err_msg = err_detail or res_data or "Tool execution error"
                        ev = Evidence(
                            turn_index=matched_tr.turn_index,
                            timestamp_ms=matched_tr.timestamp_ms,
                            speaker_id=matched_tr.speaker_id,
                            event_type="TOOL_RESULT_ERROR",
                            quote_or_payload=matched_tr.payload,
                            notes="Tool invocation failed execution during conversation.",
                        )
                        failures.append({
                            "assertion": "tool_execution_error",
                            "failure_category": FailureCategory.FAIL_TOOL.value,
                            "reason": f"Tool execution returned an error for '{expectations.required_tool_call}': {err_msg}",
                            "expected": "Successful execution",
                            "actual": str(err_msg),
                            "evidence": ev.to_dict(),
                        })
                        evidences.append(ev)

                    if is_unparseable and has_result_assertions:
                        return ScenarioResult(
                            scenario_id=scenario.id,
                            scenario_name=scenario.name,
                            status=TestStatus.INCONCLUSIVE,
                            failure_category=FailureCategory.FAIL_INFRA.value,
                            reason=f"Unparseable tool result evidence for tool '{expectations.required_tool_call}': payload string is not valid JSON.",
                            evidence=Evidence(
                                turn_index=matched_tr.turn_index,
                                timestamp_ms=matched_tr.timestamp_ms,
                                speaker_id=matched_tr.speaker_id,
                                event_type="TOOL_RESULT_UNPARSEABLE",
                                quote_or_payload=matched_tr.payload,
                                notes="Tool result content could not be parsed for structured evaluation.",
                            ),
                            metrics=metrics,
                            diagnostic_details={"error_type": "UnparseableToolResultJSON"},
                            target_type=target_type,
                            target_config=target_config,
                            run_id=run_id,
                            timestamp_utc=timestamp_utc,
                            software_version=self.software_version,
                        )

                    if expectations.require_nonempty_tool_result is True and is_tool_result_empty(res_data):
                        ev = Evidence(
                            turn_index=matched_tr.turn_index,
                            timestamp_ms=matched_tr.timestamp_ms,
                            speaker_id=matched_tr.speaker_id,
                            event_type="TOOL_RESULT_EMPTY",
                            quote_or_payload=res_data,
                            notes="Expected non-empty result payload.",
                        )
                        failures.append({
                            "assertion": "require_nonempty_tool_result",
                            "failure_category": FailureCategory.FAIL_TOOL.value,
                            "reason": f"Tool result for '{expectations.required_tool_call}' was empty, but scenario explicitly required a non-empty result.",
                            "expected": "Non-empty tool result payload",
                            "actual": res_data,
                            "evidence": ev.to_dict(),
                        })
                        evidences.append(ev)

                    if expectations.required_tool_result_fields:
                        if not isinstance(res_data, dict):
                            ev = Evidence(
                                turn_index=matched_tr.turn_index,
                                timestamp_ms=matched_tr.timestamp_ms,
                                speaker_id=matched_tr.speaker_id,
                                event_type="TOOL_RESULT_SCHEMA_MISMATCH",
                                quote_or_payload=res_data,
                                notes="Result payload must be a mapping to verify required fields.",
                            )
                            failures.append({
                                "assertion": "required_tool_result_fields",
                                "failure_category": FailureCategory.FAIL_TOOL.value,
                                "reason": f"Tool result is not a dictionary; cannot verify required fields {expectations.required_tool_result_fields}.",
                                "expected": f"Dict with fields {expectations.required_tool_result_fields}",
                                "actual": type(res_data).__name__,
                                "evidence": ev.to_dict(),
                            })
                            evidences.append(ev)
                        else:
                            for field_name in expectations.required_tool_result_fields:
                                if field_name not in res_data or res_data[field_name] is None:
                                    ev = Evidence(
                                        turn_index=matched_tr.turn_index,
                                        timestamp_ms=matched_tr.timestamp_ms,
                                        speaker_id=matched_tr.speaker_id,
                                        event_type="TOOL_RESULT_SCHEMA_MISMATCH",
                                        quote_or_payload=res_data,
                                        notes=f"Required field '{field_name}' not found or null in result.",
                                    )
                                    failures.append({
                                        "assertion": f"required_tool_result_fields.{field_name}",
                                        "failure_category": FailureCategory.FAIL_TOOL.value,
                                        "reason": f"Tool result missing required field '{field_name}' for tool '{expectations.required_tool_call}'.",
                                        "expected": f"Field '{field_name}' present",
                                        "actual": "Missing or null",
                                        "evidence": ev.to_dict(),
                                    })
                                    evidences.append(ev)

                    if expectations.expected_tool_result:
                        if not isinstance(res_data, dict):
                            ev = Evidence(
                                turn_index=matched_tr.turn_index,
                                timestamp_ms=matched_tr.timestamp_ms,
                                speaker_id=matched_tr.speaker_id,
                                event_type="TOOL_RESULT_MISMATCH",
                                quote_or_payload=res_data,
                                notes="Result payload must be a mapping to match expected fields.",
                            )
                            failures.append({
                                "assertion": "expected_tool_result",
                                "failure_category": FailureCategory.FAIL_TOOL.value,
                                "reason": f"Tool result is not a dictionary; cannot match expected fields: {expectations.expected_tool_result}.",
                                "expected": expectations.expected_tool_result,
                                "actual": type(res_data).__name__,
                                "evidence": ev.to_dict(),
                            })
                            evidences.append(ev)
                        else:
                            for k, expected_v in expectations.expected_tool_result.items():
                                actual_v = res_data.get(k)
                                if not values_are_equal_strict(actual_v, expected_v):
                                    ev = Evidence(
                                        turn_index=matched_tr.turn_index,
                                        timestamp_ms=matched_tr.timestamp_ms,
                                        speaker_id=matched_tr.speaker_id,
                                        event_type="TOOL_RESULT_MISMATCH",
                                        quote_or_payload=res_data,
                                        notes=f"Expected result field '{k}' to equal '{expected_v}', but got '{actual_v}'.",
                                    )
                                    failures.append({
                                        "assertion": f"expected_tool_result.{k}",
                                        "failure_category": FailureCategory.FAIL_TOOL.value,
                                        "reason": f"Tool result field mismatch for '{k}': expected '{expected_v}', got '{actual_v}'.",
                                        "expected": expected_v,
                                        "actual": actual_v,
                                        "evidence": ev.to_dict(),
                                    })
                                    evidences.append(ev)

                    if expectations.required_tool_result_paths:
                        for path_expr in expectations.required_tool_result_paths:
                            ok, err_msg, tokens = parse_and_validate_path(path_expr)
                            if not ok:
                                return ScenarioResult(
                                    scenario_id=scenario.id,
                                    scenario_name=scenario.name,
                                    status=TestStatus.INCONCLUSIVE,
                                    failure_category=FailureCategory.FAIL_INFRA.value,
                                    reason=f"Invalid path expression in required_tool_result_paths: {err_msg}",
                                    evidence=Evidence(
                                        turn_index=matched_tr.turn_index,
                                        timestamp_ms=matched_tr.timestamp_ms,
                                        speaker_id=matched_tr.speaker_id,
                                        event_type="TOOL_RESULT_SCHEMA_MISMATCH",
                                        quote_or_payload=path_expr,
                                        notes=f"Syntax error in path expression: {err_msg}",
                                    ),
                                    metrics=metrics,
                                    diagnostic_details={"error_type": "InvalidPathExpression", "path": path_expr, "reason": err_msg},
                                    target_type=target_type,
                                    target_config=target_config,
                                    run_id=run_id,
                                    timestamp_utc=timestamp_utc,
                                    software_version=self.software_version,
                                )
                            exists, val = resolve_path_tokens(res_data, tokens)
                            if not exists:
                                ev = Evidence(
                                    turn_index=matched_tr.turn_index,
                                    timestamp_ms=matched_tr.timestamp_ms,
                                    speaker_id=matched_tr.speaker_id,
                                    event_type="TOOL_RESULT_SCHEMA_MISMATCH",
                                    quote_or_payload=res_data,
                                    notes=f"Required path '{path_expr}' was not found in result payload.",
                                )
                                failures.append({
                                    "assertion": f"required_tool_result_paths.{path_expr}",
                                    "failure_category": FailureCategory.FAIL_TOOL.value,
                                    "reason": f"Tool result missing required path '{path_expr}' for tool '{expectations.required_tool_call}'.",
                                    "expected": f"Path '{path_expr}' exists",
                                    "actual": "Path not found",
                                    "evidence": ev.to_dict(),
                                })
                                evidences.append(ev)

                    if expectations.expected_tool_result_paths:
                        for path_expr, expected_v in expectations.expected_tool_result_paths.items():
                            ok, err_msg, tokens = parse_and_validate_path(path_expr)
                            if not ok:
                                return ScenarioResult(
                                    scenario_id=scenario.id,
                                    scenario_name=scenario.name,
                                    status=TestStatus.INCONCLUSIVE,
                                    failure_category=FailureCategory.FAIL_INFRA.value,
                                    reason=f"Invalid path expression in expected_tool_result_paths: {err_msg}",
                                    evidence=Evidence(
                                        turn_index=matched_tr.turn_index,
                                        timestamp_ms=matched_tr.timestamp_ms,
                                        speaker_id=matched_tr.speaker_id,
                                        event_type="TOOL_RESULT_MISMATCH",
                                        quote_or_payload=path_expr,
                                        notes=f"Syntax error in path expression: {err_msg}",
                                    ),
                                    metrics=metrics,
                                    diagnostic_details={"error_type": "InvalidPathExpression", "path": path_expr, "reason": err_msg},
                                    target_type=target_type,
                                    target_config=target_config,
                                    run_id=run_id,
                                    timestamp_utc=timestamp_utc,
                                    software_version=self.software_version,
                                )
                            exists, actual_v = resolve_path_tokens(res_data, tokens)
                            if not exists:
                                ev = Evidence(
                                    turn_index=matched_tr.turn_index,
                                    timestamp_ms=matched_tr.timestamp_ms,
                                    speaker_id=matched_tr.speaker_id,
                                    event_type="TOOL_RESULT_MISMATCH",
                                    quote_or_payload=res_data,
                                    notes=f"Path '{path_expr}' could not be resolved in result payload.",
                                )
                                failures.append({
                                    "assertion": f"expected_tool_result_paths.{path_expr}",
                                    "failure_category": FailureCategory.FAIL_TOOL.value,
                                    "reason": f"Tool result path '{path_expr}' was not found in result payload for tool '{expectations.required_tool_call}'.",
                                    "expected": f"Path '{path_expr}' exists",
                                    "actual": "Path not found",
                                    "evidence": ev.to_dict(),
                                })
                                evidences.append(ev)
                            elif not values_are_equal_strict(actual_v, expected_v):
                                ev = Evidence(
                                    turn_index=matched_tr.turn_index,
                                    timestamp_ms=matched_tr.timestamp_ms,
                                    speaker_id=matched_tr.speaker_id,
                                    event_type="TOOL_RESULT_MISMATCH",
                                    quote_or_payload=res_data,
                                    notes=f"Expected value at '{path_expr}' was '{expected_v}', but observed '{actual_v}'.",
                                )
                                failures.append({
                                    "assertion": f"expected_tool_result_paths.{path_expr}",
                                    "failure_category": FailureCategory.FAIL_TOOL.value,
                                    "reason": (
                                        f"Tool result value mismatch at path '{path_expr}': "
                                        f"expected '{expected_v}' ({type(expected_v).__name__}), "
                                        f"got '{actual_v}' ({type(actual_v).__name__})."
                                    ),
                                    "expected": expected_v,
                                    "actual": actual_v,
                                    "evidence": ev.to_dict(),
                                })
                                evidences.append(ev)

                # Check numeric grounding in spoken text
                if expectations.expected_numeric_grounding:
                    spoken_text = " ".join(str(t.payload) for t in transcripts if t.speaker_id not in ("user", "system"))
                    if expectations.expected_numeric_grounding not in spoken_text:
                        ev = Evidence(
                            turn_index=transcripts[-1].turn_index if transcripts else None,
                            timestamp_ms=transcripts[-1].timestamp_ms if transcripts else None,
                            speaker_id=transcripts[-1].speaker_id if transcripts else None,
                            event_type="TRANSCRIPT_FINAL",
                            quote_or_payload=spoken_text,
                            notes="Spoken answer contradicted or lacked tool-grounded fact.",
                        )
                        failures.append({
                            "assertion": "expected_numeric_grounding",
                            "failure_category": FailureCategory.FAIL_TOOL.value,
                            "reason": (
                                f"Numeric fact grounding failure: expected '{expectations.expected_numeric_grounding}' "
                                f"in spoken response, but was not present in transcript."
                            ),
                            "expected": expectations.expected_numeric_grounding,
                            "actual": spoken_text,
                            "evidence": ev.to_dict(),
                        })
                        evidences.append(ev)

        # Final reconciliation: did any assertion fail?
        if failures:
            primary_failure = failures[0]
            if len(failures) == 1:
                combined_reason = primary_failure["reason"]
            else:
                combined_reason = (
                    f"Multiple assertions failed ({len(failures)}): "
                    + "; ".join(f["reason"] for f in failures)
                )

            return ScenarioResult(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                status=TestStatus.FAIL,
                failure_category=primary_failure["failure_category"],
                reason=combined_reason,
                evidence=evidences[0] if evidences else None,
                metrics=metrics,
                failures=failures,
                expected_vs_actual={
                    f["assertion"]: {"expected": f.get("expected"), "actual": f.get("actual")}
                    for f in failures
                },
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
            failures=[],
            target_type=target_type,
            target_config=target_config,
            run_id=run_id,
            timestamp_utc=timestamp_utc,
            software_version=self.software_version,
        )
