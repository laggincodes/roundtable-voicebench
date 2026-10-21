"""Deterministic offline tests for structured tool-result assertions in VoiceBench."""

import pytest

from voicebench.evaluator import Evaluator
from voicebench.models import (
    ConversationTrace,
    EventType,
    FailureCategory,
    Scenario,
    ScenarioExpectations,
    TestStatus,
    TraceEvent,
    TurnInput,
)
from voicebench.parser import ScenarioValidationError, load_scenario_from_dict


def _make_base_scenario(expectations: ScenarioExpectations) -> Scenario:
    """Helper to construct a simple valid scenario with given expectations."""
    return Scenario(
        id="SCEN-TEST-TOOL-RES",
        name="Tool Result Test Scenario",
        description="Scenario for verifying tool result assertions",
        turns=[
            TurnInput(
                turn_index=1,
                speaker="user",
                payload="What is the current market price?",
            )
        ],
        expectations=expectations,
    )


def test_exact_expected_field_matches():
    """Verify tool result matching expected field values passes evaluation."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="fetch_market_price",
            expected_tool_result={"price_mwh": 34.50, "currency": "USD"},
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_001",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "fetch_market_price", "args": {"asset": "electricity"}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={"tool": "fetch_market_price", "result": {"price_mwh": 34.50, "currency": "USD"}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="The market price is 34.50 USD.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.PASS


def test_unexpected_field_values_fail():
    """Verify unexpected tool result field value produces FAIL with FAIL_TOOL."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="fetch_market_price",
            expected_tool_result={"price_mwh": 34.50, "currency": "USD"},
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_002",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "fetch_market_price", "args": {}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={"tool": "fetch_market_price", "result": {"price_mwh": 85.00, "currency": "USD"}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="The market price is 85.00 USD.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.FAIL
    assert result.failure_category == FailureCategory.FAIL_TOOL.value
    assert "Tool result field mismatch for 'price_mwh'" in result.reason
    assert "expected '34.5', got '85.0'" in result.reason
    assert result.evidence.event_type == "TOOL_RESULT_MISMATCH"


def test_missing_required_fields_fail():
    """Verify missing required field produces FAIL with FAIL_TOOL."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="knowledge_base",
            required_tool_result_fields=["status", "chunk_count", "document_id"],
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_003",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "knowledge_base", "args": {"query": "pricing"}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={
                    "tool": "knowledge_base",
                    "result": {"status": "no_matching_documents", "chunk_count": 0},
                },
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="No documents found.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.FAIL
    assert result.failure_category == FailureCategory.FAIL_TOOL.value
    assert "Tool result missing required field 'document_id'" in result.reason
    assert result.evidence.event_type == "TOOL_RESULT_SCHEMA_MISMATCH"


def test_explicit_nonempty_result_requirement_fails_on_empty():
    """Verify require_nonempty_tool_result=True produces FAIL when result is empty."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="knowledge_base",
            require_nonempty_tool_result=True,
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_004",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "knowledge_base", "args": {"query": "pricing"}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={
                    "tool": "knowledge_base",
                    "result": {
                        "result_type": "knowledge_base_success",
                        "status": "no_matching_documents",
                        "chunk_count": 0,
                    },
                    "is_error": False,
                },
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="No documents found.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.FAIL
    assert result.failure_category == FailureCategory.FAIL_TOOL.value
    assert "was empty, but scenario explicitly required a non-empty result" in result.reason
    assert result.evidence.event_type == "TOOL_RESULT_EMPTY"


def test_explicit_nonempty_result_requirement_passes_on_populated():
    """Verify require_nonempty_tool_result=True passes when populated data is returned."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="knowledge_base",
            require_nonempty_tool_result=True,
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_005",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "knowledge_base", "args": {"query": "pricing"}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={
                    "tool": "knowledge_base",
                    "result": {
                        "chunk_count": 2,
                        "documents": [{"id": "doc_1", "text": "ERCOT pricing is $34.50"}],
                    },
                    "is_error": False,
                },
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="Found pricing documents in knowledge base.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.PASS


def test_valid_empty_result_when_emptiness_allowed():
    """Verify empty result is accepted when require_nonempty_tool_result is not set."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="knowledge_base",
            expected_tool_result={"status": "no_matching_documents"},
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_006",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "knowledge_base", "args": {"query": "pricing"}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={
                    "tool": "knowledge_base",
                    "result": {
                        "result_type": "knowledge_base_success",
                        "status": "no_matching_documents",
                        "chunk_count": 0,
                    },
                    "is_error": False,
                },
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="No documents found.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.PASS


def test_tool_execution_error_fails():
    """Verify reported execution error (is_error=True) produces FAIL with FAIL_TOOL."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="fetch_market_price",
            expected_tool_result={"price_mwh": 34.50},
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_007",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "fetch_market_price", "args": {}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={
                    "tool": "fetch_market_price",
                    "is_error": True,
                    "error": "Database connection timed out",
                },
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="Price lookup failed.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.FAIL
    assert result.failure_category == FailureCategory.FAIL_TOOL.value
    assert "Tool execution returned an error" in result.reason
    assert "Database connection timed out" in result.reason


def test_missing_result_event_returns_inconclusive():
    """Verify tool call without corresponding tool result returns INCONCLUSIVE when result assertion is specified."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="fetch_market_price",
            expected_tool_result={"price_mwh": 34.50},
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_008",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "fetch_market_price", "args": {}},
                turn_index=1,
            ),
            # Missing TOOL_RESULT event completely
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="The market price is unknown.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value
    assert "Missing tool result evidence" in result.reason
    assert result.evidence.event_type == "TOOL_RESULT_MISSING"


def test_unparseable_result_event_returns_inconclusive():
    """Verify malformed unparseable result string returns INCONCLUSIVE when result assertion is specified."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="fetch_market_price",
            expected_tool_result={"price_mwh": 34.50},
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_009",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "fetch_market_price", "args": {}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={
                    "tool": "fetch_market_price",
                    "result": "{malformed_json: true, unquoted",
                },
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="The market price is unknown.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value
    assert "Unparseable tool result evidence" in result.reason
    assert result.evidence.event_type == "TOOL_RESULT_UNPARSEABLE"


def test_backward_compatibility_when_no_result_assertions_defined():
    """Verify existing scenarios without result assertions behave identically to before."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="fetch_market_price",
            expected_tool_args={"asset": "electricity"},
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_010",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "fetch_market_price", "args": {"asset": "electricity"}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={"price_mwh": 34.50, "currency": "USD"},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="The market price is 34.50 USD.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.PASS


def test_parser_validation_rejects_invalid_tool_result_types():
    """Verify schema validator catches malformed tool-result assertion types."""
    base_dict = {
        "id": "SCEN-INVALID-TYPES",
        "name": "Invalid Types Scenario",
        "description": "Testing schema validation",
        "turns": [{"turn_index": 1, "speaker": "user", "payload": "Hello"}],
    }

    # 1. expected_tool_result must be dict
    invalid_res = dict(base_dict, expectations={"expected_tool_result": "not_a_dict"})
    with pytest.raises(ScenarioValidationError) as exc:
        load_scenario_from_dict(invalid_res)
    assert "expected_tool_result' must be a dictionary/mapping" in str(exc.value)

    # 2. required_tool_result_fields must be list of strings
    invalid_fields = dict(base_dict, expectations={"required_tool_result_fields": "not_a_list"})
    with pytest.raises(ScenarioValidationError) as exc:
        load_scenario_from_dict(invalid_fields)
    assert "required_tool_result_fields' must be a list of non-empty field name strings" in str(exc.value)

    # 3. require_nonempty_tool_result must be bool
    invalid_nonempty = dict(base_dict, expectations={"require_nonempty_tool_result": "yes"})
    with pytest.raises(ScenarioValidationError) as exc:
        load_scenario_from_dict(invalid_nonempty)
    assert "require_nonempty_tool_result' must be a boolean" in str(exc.value)


# ---------------------------------------------------------------------------
# Nested Tool-Result Assertions and Correlation Integrity Tests
# ---------------------------------------------------------------------------


def test_nested_object_path_match():
    """Verify dot-separated paths in nested objects match and pass."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="system_health",
            expected_tool_result_paths={
                "metadata.status": "healthy",
                "metadata.code": 200,
                "server.region.az": "us-east-1a",
            },
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_nested_01",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "system_health", "args": {}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={
                    "tool": "system_health",
                    "result": {
                        "metadata": {"status": "healthy", "code": 200},
                        "server": {"region": {"az": "us-east-1a"}},
                    },
                },
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="System is healthy.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.PASS


def test_nested_object_path_mismatch():
    """Verify dot-separated path value mismatch produces deterministic FAIL."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="system_health",
            expected_tool_result_paths={
                "metadata.status": "healthy",
            },
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_nested_02",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "system_health", "args": {}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={
                    "tool": "system_health",
                    "result": {
                        "metadata": {"status": "degraded"},
                    },
                },
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="System is degraded.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.FAIL
    assert result.failure_category == FailureCategory.FAIL_TOOL.value
    assert "Tool result value mismatch at path 'metadata.status'" in result.reason
    assert "expected 'healthy'" in result.reason
    assert result.evidence.event_type == "TOOL_RESULT_MISMATCH"


def test_array_index_path_match():
    """Verify array indexing syntax ([0], documents[0].id) matches and passes."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="knowledge_base",
            expected_tool_result_paths={
                "documents[0].id": "doc_101",
                "documents[0].title": "Quickstart Guide",
                "tags[1]": "release",
            },
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_nested_03",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "knowledge_base", "args": {}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={
                    "tool": "knowledge_base",
                    "result": {
                        "documents": [
                            {"id": "doc_101", "title": "Quickstart Guide"},
                            {"id": "doc_102", "title": "Advanced Usage"},
                        ],
                        "tags": ["draft", "release"],
                    },
                },
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="Found quickstart guide.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.PASS


def test_array_index_path_mismatch():
    """Verify array index value mismatch produces deterministic FAIL."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="knowledge_base",
            expected_tool_result_paths={
                "documents[0].id": "doc_101",
            },
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_nested_04",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "knowledge_base", "args": {}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={
                    "tool": "knowledge_base",
                    "result": {
                        "documents": [
                            {"id": "doc_999"},
                        ]
                    },
                },
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="Document found.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.FAIL
    assert result.failure_category == FailureCategory.FAIL_TOOL.value
    assert "Tool result value mismatch at path 'documents[0].id'" in result.reason
    assert "expected 'doc_101'" in result.reason


def test_array_index_out_of_range_fails():
    """Verify array index exceeding array length fails with path not found."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="knowledge_base",
            expected_tool_result_paths={
                "documents[5].id": "doc_101",
            },
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_nested_05",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "knowledge_base", "args": {}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={
                    "tool": "knowledge_base",
                    "result": {
                        "documents": [
                            {"id": "doc_101"},
                        ]
                    },
                },
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="Finished search.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.FAIL
    assert result.failure_category == FailureCategory.FAIL_TOOL.value
    assert "Tool result path 'documents[5].id' was not found" in result.reason


def test_required_nested_path_present():
    """Verify required_tool_result_paths passes when all paths resolve."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="get_config",
            required_tool_result_paths=[
                "cluster.nodes[0].ip",
                "settings.security.tls_enabled",
            ],
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_nested_06",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "get_config", "args": {}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={
                    "cluster": {"nodes": [{"ip": "10.0.0.1"}]},
                    "settings": {"security": {"tls_enabled": True}},
                },
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="Configuration retrieved.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.PASS


def test_required_nested_path_missing():
    """Verify required_tool_result_paths fails when any path is absent."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="get_config",
            required_tool_result_paths=[
                "cluster.nodes[0].missing_key",
            ],
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_nested_07",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "get_config", "args": {}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={
                    "cluster": {"nodes": [{"ip": "10.0.0.1"}]},
                },
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="Config loaded.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.FAIL
    assert result.failure_category == FailureCategory.FAIL_TOOL.value
    assert "Tool result missing required path 'cluster.nodes[0].missing_key'" in result.reason
    assert result.evidence.event_type == "TOOL_RESULT_SCHEMA_MISMATCH"


def test_required_nested_path_distinguishes_explicit_none():
    """Verify an explicit null value passes required path check, unlike a missing key."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="fetch_item",
            required_tool_result_paths=[
                "response.error",
                "response.deleted_at",
            ],
            expected_tool_result_paths={
                "response.error": None,
            },
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_nested_08",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "fetch_item", "args": {}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={
                    "response": {
                        "error": None,
                        "deleted_at": None,
                        "item_id": 123,
                    }
                },
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="Item fetched successfully.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.PASS


def test_strict_type_comparison_without_coercion():
    """Verify strict type comparison prevents bool/int coercion (1 != True) and string/int coercion."""
    evaluator = Evaluator()

    # Case 1: 1 vs True
    scenario_bool = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="test_tool",
            expected_tool_result_paths={"flag": True},
        )
    )
    trace_int = ConversationTrace(
        scenario_id=scenario_bool.id,
        session_id="sess_type_01",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(timestamp_ms=100.0, event_type=EventType.USER_INPUT, speaker_id="user", payload="What is the current market price?", turn_index=1),
            TraceEvent(timestamp_ms=200.0, event_type=EventType.TOOL_CALL, speaker_id="agent", payload={"tool": "test_tool", "args": {}}, turn_index=1),
            TraceEvent(timestamp_ms=300.0, event_type=EventType.TOOL_RESULT, speaker_id="system", payload={"flag": 1}, turn_index=1),
            TraceEvent(timestamp_ms=400.0, event_type=EventType.TRANSCRIPT_FINAL, speaker_id="agent", payload="Result here.", turn_index=1),
        ],
    )
    res_1 = evaluator.evaluate(scenario_bool, trace_int)
    assert res_1.status == TestStatus.FAIL
    assert "Tool result value mismatch at path 'flag'" in res_1.reason

    # Case 2: "123" vs 123
    scenario_str = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="test_tool",
            expected_tool_result_paths={"code": "123"},
        )
    )
    trace_int2 = ConversationTrace(
        scenario_id=scenario_str.id,
        session_id="sess_type_02",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(timestamp_ms=100.0, event_type=EventType.USER_INPUT, speaker_id="user", payload="What is the current market price?", turn_index=1),
            TraceEvent(timestamp_ms=200.0, event_type=EventType.TOOL_CALL, speaker_id="agent", payload={"tool": "test_tool", "args": {}}, turn_index=1),
            TraceEvent(timestamp_ms=300.0, event_type=EventType.TOOL_RESULT, speaker_id="system", payload={"code": 123}, turn_index=1),
            TraceEvent(timestamp_ms=400.0, event_type=EventType.TRANSCRIPT_FINAL, speaker_id="agent", payload="Result here.", turn_index=1),
        ],
    )
    res_2 = evaluator.evaluate(scenario_str, trace_int2)
    assert res_2.status == TestStatus.FAIL
    assert "Tool result value mismatch at path 'code'" in res_2.reason


def test_parser_validation_rejects_malformed_paths():
    """Verify scenario parser rejects invalid path syntax during schema validation."""
    base_dict = {
        "id": "SCEN-INVALID-PATH",
        "name": "Invalid Path Scenario",
        "description": "Testing schema validation for paths",
        "turns": [{"turn_index": 1, "speaker": "user", "payload": "Hello"}],
    }

    invalid_paths = [
        ("foo[0", "Unclosed bracket"),
        ("foo[]", "Empty index"),
        ("foo[abc]", "Array index must be a non-negative integer"),
        (".foo", "Path cannot start with a dot"),
        ("foo.", "Path cannot end with a dot"),
        ("foo..bar", "Consecutive dots"),
        ("foo bar", "Whitespace is not permitted"),
        ("foo@bar", "Invalid character"),
    ]

    for bad_path, expected_err in invalid_paths:
        bad_scenario_dict = dict(
            base_dict,
            expectations={"expected_tool_result_paths": {bad_path: "val"}},
        )
        with pytest.raises(ScenarioValidationError) as exc:
            load_scenario_from_dict(bad_scenario_dict)
        assert expected_err in str(exc.value)

        # Also test required_tool_result_paths
        bad_req_dict = dict(
            base_dict,
            expectations={"required_tool_result_paths": [bad_path]},
        )
        with pytest.raises(ScenarioValidationError) as exc_req:
            load_scenario_from_dict(bad_req_dict)
        assert expected_err in str(exc_req.value)


def test_correlation_by_request_id():
    """Verify tool calls with explicit request_id correlate strictly with matching tool result."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="query_api",
            expected_tool_result_paths={"status": "done"},
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_corr_01",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            # Tool call 1
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "query_api", "request_id": "call_1"},
                turn_index=1,
            ),
            # Tool call 2
            TraceEvent(
                timestamp_ms=250.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "query_api", "request_id": "call_2"},
                turn_index=1,
            ),
            # Result for call 2
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={"tool": "query_api", "request_id": "call_2", "result": {"status": "done"}},
                turn_index=1,
            ),
            # Result for call 1
            TraceEvent(
                timestamp_ms=350.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={"tool": "query_api", "request_id": "call_1", "result": {"status": "pending"}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="Query completed.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    # The first tool call in trace is call_1 (status pending)
    # If evaluating against call_1, it will correlate with call_1's result
    result = evaluator.evaluate(scenario, trace)
    # call_1 correlated with status: pending != done -> FAIL
    assert result.status == TestStatus.FAIL
    assert "expected 'done'" in result.reason
    assert "got 'pending'" in result.reason


def test_correlation_ambiguity_returns_inconclusive():
    """Verify multiple matching tool results without correlation identifiers yield INCONCLUSIVE."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="query_api",
            expected_tool_result={"status": "done"},
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_corr_02",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "query_api"},
                turn_index=1,
            ),
            # Multiple results on the same turn without request_ids
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={"tool": "query_api", "status": "done"},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=350.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={"tool": "query_api", "status": "failed"},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="Query done.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value
    assert "Ambiguous tool result correlation" in result.reason
    assert result.evidence.event_type == "TOOL_RESULT_AMBIGUOUS"


def test_correlation_conflicting_request_ids_returns_inconclusive():
    """Verify conflicting request_id correlation yields INCONCLUSIVE."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="query_api",
            expected_tool_result={"status": "done"},
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_corr_03",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "query_api", "request_id": "call_AAA"},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={"tool": "query_api", "request_id": "call_BBB", "status": "done"},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="Query done.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value
    assert "Conflicting tool correlation" in result.reason
    assert result.evidence.event_type == "TOOL_RESULT_AMBIGUOUS"


def test_missing_tool_result_telemetry_returns_inconclusive():
    """Verify missing tool result event produces INCONCLUSIVE with TOOL_RESULT_MISSING."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="fetch_market_price",
            expected_tool_result={"price_mwh": 34.50},
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_missing_01",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "fetch_market_price", "args": {}},
                turn_index=1,
            ),
            # NO TOOL_RESULT event present!
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="The market price is 34.50 USD.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value
    assert "no corresponding tool result was captured" in result.reason
    assert result.evidence.event_type == "TOOL_RESULT_MISSING"


def test_unparseable_tool_result_returns_inconclusive():
    """Verify malformed JSON tool result payload produces INCONCLUSIVE with TOOL_RESULT_UNPARSEABLE."""
    scenario = _make_base_scenario(
        ScenarioExpectations(
            required_tool_call="fetch_market_price",
            expected_tool_result={"price_mwh": 34.50},
        )
    )

    trace = ConversationTrace(
        scenario_id=scenario.id,
        session_id="sess_unparseable_01",
        target_type="local_mock",
        target_config="baseline",
        events=[
            TraceEvent(
                timestamp_ms=100.0,
                event_type=EventType.USER_INPUT,
                speaker_id="user",
                payload="What is the current market price?",
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=200.0,
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": "fetch_market_price", "args": {}},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=300.0,
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={"tool": "fetch_market_price", "result": '{"unclosed json: ...'},
                turn_index=1,
            ),
            TraceEvent(
                timestamp_ms=400.0,
                event_type=EventType.TRANSCRIPT_FINAL,
                speaker_id="agent",
                payload="The price is available.",
                turn_index=1,
            ),
        ],
    )

    evaluator = Evaluator()
    result = evaluator.evaluate(scenario, trace)
    assert result.status == TestStatus.INCONCLUSIVE
    assert result.failure_category == FailureCategory.FAIL_INFRA.value
    assert "payload string is not valid JSON" in result.reason
    assert result.evidence.event_type == "TOOL_RESULT_UNPARSEABLE"
