"""Official ElevenLabs Agent Testing API Target Adapter for VoiceBench.

Integrates with ElevenLabs Conversational AI Agent Testing APIs:
  - Create Test: POST /v1/convai/agent-testing/create
  - Run Tests: POST /v1/convai/agents/{agent_id}/run-tests
  - Get Test Invocation: GET /v1/convai/test-invocations/{test_invocation_id}

Provides:
  - Typed request/response models for native test schemas:
      * CreateResponseUnitTestRequest (LLM next-reply unit tests)
      * CreateToolCallUnitTestRequest (Tool calling unit tests)
      * CreateSimulationTestRequest (Multi-turn conversation simulations)
  - Explicit safety controls: Default dry-run request previews and explicit confirmation gates.
  - Strict secret redaction across all logs, diagnostics, and errors.
  - Separation between provider-native judgments and VoiceBench deterministic evaluations.
  - Lifecycle state handling (pending, running, completed, failed, cancelled).
  - Loss-aware conversion between VoiceBench scenarios and native tests.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple, Union

from voicebench.adapters.base import TargetAdapter
from voicebench.evaluator import Evaluator
from voicebench.models import (
    AdapterCapabilities,
    ConversationTrace,
    EventType,
    FailureCategory,
    Scenario,
    ScenarioResult,
    TestStatus,
    TraceEvent,
)
from voicebench.trace import TraceCollector


class NativeTestType(str, Enum):
    """Supported ElevenLabs native test types."""
    LLM = "llm"                    # Response / next-reply unit test
    TOOL = "tool"                  # Tool call unit test
    SIMULATION = "simulation"      # Multi-turn conversation simulation


class TestRunStatus(str, Enum):
    """Lifecycle status of a native ElevenLabs test run."""
    __test__ = False
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Typed Request Models
# ---------------------------------------------------------------------------


@dataclass
class CreateResponseUnitTestRequest:
    """Request model for CreateResponseUnitTestRequest (type: 'llm')."""
    name: str
    type: str = "llm"
    chat_history: List[Dict[str, Any]] = field(default_factory=list)
    evaluation_criteria: Optional[str] = None
    expected_response: Optional[str] = None
    agent_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "chat_history": self.chat_history,
        }
        if self.evaluation_criteria is not None:
            d["evaluation_criteria"] = self.evaluation_criteria
        if self.expected_response is not None:
            d["expected_response"] = self.expected_response
        if self.agent_id is not None:
            d["agent_id"] = self.agent_id
        if self.metadata:
            d["metadata"] = self.metadata
        return d


@dataclass
class CreateToolCallUnitTestRequest:
    """Request model for CreateToolCallUnitTestRequest (type: 'tool')."""
    name: str
    tool_name: str
    type: str = "tool"
    chat_history: List[Dict[str, Any]] = field(default_factory=list)
    expected_tool_args: Optional[Dict[str, Any]] = None
    agent_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "tool_name": self.tool_name,
            "chat_history": self.chat_history,
        }
        if self.expected_tool_args is not None:
            d["expected_tool_args"] = self.expected_tool_args
        if self.agent_id is not None:
            d["agent_id"] = self.agent_id
        if self.metadata:
            d["metadata"] = self.metadata
        return d


@dataclass
class CreateSimulationTestRequest:
    """Request model for CreateSimulationTestRequest (type: 'simulation')."""
    name: str
    type: str = "simulation"
    scenario: Optional[str] = None
    user_prompt: Optional[str] = None
    success_criteria: List[str] = field(default_factory=list)
    max_turns: Optional[int] = None
    agent_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "type": self.type,
        }
        if self.scenario is not None:
            d["scenario"] = self.scenario
        if self.user_prompt is not None:
            d["user_prompt"] = self.user_prompt
        if self.success_criteria:
            d["success_criteria"] = self.success_criteria
        if self.max_turns is not None:
            d["max_turns"] = self.max_turns
        if self.agent_id is not None:
            d["agent_id"] = self.agent_id
        if self.metadata:
            d["metadata"] = self.metadata
        return d


@dataclass
class CreateTestResponse:
    """Response returned upon test creation: POST /v1/convai/agent-testing/create."""
    id: str


@dataclass
class RunTestsRequest:
    """Request model for POST /v1/convai/agents/{agent_id}/run-tests."""
    tests: List[Dict[str, str]]
    agent_id: str
    branch_id: Optional[str] = None
    agent_config_override: Optional[Dict[str, Any]] = None
    repeat_count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "tests": self.tests,
            "repeat_count": max(1, min(50, self.repeat_count)),
        }
        if self.branch_id is not None:
            d["branch_id"] = self.branch_id
        if self.agent_config_override is not None:
            d["agent_config_override"] = self.agent_config_override
        return d


# ---------------------------------------------------------------------------
# Typed Response & Result Models
# ---------------------------------------------------------------------------


@dataclass
class TestRunDetail:
    """Details of a single test run within a test invocation."""
    __test__ = False
    test_run_id: str
    test_invocation_id: str
    test_id: str
    agent_id: str
    status: str
    passed: Optional[bool] = None
    evaluation_result: Optional[Dict[str, Any]] = None
    chat_history: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TestInvocationResponse:
    """Response returned from GET /v1/convai/test-invocations/{test_invocation_id}."""
    __test__ = False
    id: str
    agent_id: Optional[str] = None
    branch_id: Optional[str] = None
    version_id: Optional[str] = None
    status: str = "pending"
    cancelled: bool = False
    ran_against_draft: bool = False
    repeat_count: int = 1
    bucketing_status: Optional[str] = None
    test_runs: List[TestRunDetail] = field(default_factory=list)
    raw_response: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NativeTestExecutionResult:
    """Composite evaluation result separating provider-native and VoiceBench deterministic outcomes."""
    test_invocation_id: str
    test_run_id: str
    agent_id: str
    provider_status: str
    provider_passed: Optional[bool]
    provider_feedback: Optional[Dict[str, Any]]
    voicebench_result: ScenarioResult
    trace: Optional[ConversationTrace] = None
    loss_notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_invocation_id": self.test_invocation_id,
            "test_run_id": self.test_run_id,
            "agent_id": self.agent_id,
            "provider_status": self.provider_status,
            "provider_passed": self.provider_passed,
            "provider_feedback": self.provider_feedback,
            "voicebench_status": self.voicebench_result.status.value if self.voicebench_result else None,
            "voicebench_reason": self.voicebench_result.reason if self.voicebench_result else None,
            "loss_notes": self.loss_notes,
        }


# ---------------------------------------------------------------------------
# Scenario to Native Test Conversion (Loss-Aware)
# ---------------------------------------------------------------------------


def scenario_to_native_test_request(
    scenario: Scenario,
    test_type: Union[NativeTestType, str] = NativeTestType.SIMULATION,
    agent_id: Optional[str] = None,
) -> Tuple[Union[CreateResponseUnitTestRequest, CreateToolCallUnitTestRequest, CreateSimulationTestRequest], List[str]]:
    """Convert a VoiceBench Scenario into a native ElevenLabs test creation request.

    Captures loss notes documenting features that cannot be represented in text-based
    single-agent ElevenLabs test schemas (such as multi-agent arbitration or sub-second barge-in).
    """
    loss_notes: List[str] = []
    t_type = NativeTestType(test_type) if isinstance(test_type, str) else test_type

    # Document lossy aspects
    exp = scenario.expectations
    if exp.max_cross_talk_overlap_ms is not None or (exp.expected_speaker_sequence and len(set(exp.expected_speaker_sequence)) > 1):
        loss_notes.append("Multi-agent speaker sequence/cross-talk arbitration cannot be evaluated natively in single-agent ElevenLabs ConvAI tests.")
    if exp.max_yield_latency_ms is not None or any(t.barge_in for t in scenario.turns):
        loss_notes.append("Sub-second acoustic barge-in yield timing cannot be represented in text-based simulation tests.")

    if t_type == NativeTestType.TOOL:
        if not exp.required_tool_call:
            raise ValueError(f"Scenario '{scenario.id}' does not specify required_tool_call, cannot create CreateToolCallUnitTestRequest.")

        chat_history: List[Dict[str, Any]] = []
        for t in scenario.turns:
            role = "user" if t.speaker in ("user", "human_user") else "assistant"
            chat_history.append({"role": role, "message": t.payload})

        req = CreateToolCallUnitTestRequest(
            name=f"VoiceBench_{scenario.id}_{scenario.name[:30]}",
            type="tool",
            tool_name=exp.required_tool_call,
            expected_tool_args=exp.expected_tool_args,
            chat_history=chat_history,
            agent_id=agent_id,
            metadata={"voicebench_scenario_id": scenario.id},
        )
        return req, loss_notes

    elif t_type == NativeTestType.LLM:
        chat_history = []
        for t in scenario.turns:
            role = "user" if t.speaker in ("user", "human_user") else "assistant"
            chat_history.append({"role": role, "message": t.payload})

        criteria_parts = []
        if exp.required_keywords:
            criteria_parts.append(f"Must mention: {', '.join(exp.required_keywords)}")
        if exp.forbidden_keywords:
            criteria_parts.append(f"Must NOT mention: {', '.join(exp.forbidden_keywords)}")
        if exp.expected_numeric_grounding:
            criteria_parts.append(f"Must ground value: {exp.expected_numeric_grounding}")

        req_llm = CreateResponseUnitTestRequest(
            name=f"VoiceBench_{scenario.id}_{scenario.name[:30]}",
            type="llm",
            chat_history=chat_history,
            evaluation_criteria="; ".join(criteria_parts) if criteria_parts else None,
            agent_id=agent_id,
            metadata={"voicebench_scenario_id": scenario.id},
        )
        return req_llm, loss_notes

    else:  # SIMULATION
        user_turns = [t for t in scenario.turns if t.speaker in ("user", "human_user")]
        first_user_prompt = user_turns[0].payload if user_turns else ""

        success_criteria: List[str] = []
        if exp.required_keywords:
            success_criteria.append(f"Agent includes keywords: {', '.join(exp.required_keywords)}")
        if exp.required_tool_call:
            success_criteria.append(f"Agent triggers tool: {exp.required_tool_call}")
        if exp.expected_numeric_grounding:
            success_criteria.append(f"Agent grounds numeric value: {exp.expected_numeric_grounding}")

        req_sim = CreateSimulationTestRequest(
            name=f"VoiceBench_{scenario.id}_{scenario.name[:30]}",
            type="simulation",
            scenario=scenario.description,
            user_prompt=first_user_prompt,
            success_criteria=success_criteria,
            max_turns=len(scenario.turns) + 2,
            agent_id=agent_id,
            metadata={"voicebench_scenario_id": scenario.id},
        )
        return req_sim, loss_notes


# ---------------------------------------------------------------------------
# ElevenLabs Agent Testing Adapter
# ---------------------------------------------------------------------------


class ElevenLabsAgentTestingAdapter(TargetAdapter):
    """Adapter for official ElevenLabs Agent Testing APIs (/v1/convai/agent-testing).

    Enforces:
      - Default dry-run request previews (no remote mutations unless explicitly confirmed).
      - Strict secret redaction in all outputs and error diagnostics.
      - Separation of provider-native verdicts and VoiceBench deterministic evaluations.
      - Pending test runs are never classified as PASS.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        agent_id: Optional[str] = None,
        test_invocation_id: Optional[str] = None,
        base_url: str = "https://api.elevenlabs.io",
        timeout_seconds: float = 15.0,
    ):
        super().__init__(target_type="elevenlabs_agent_testing", target_config=agent_id or "default")
        if (api_key is None and not os.environ.get("ELEVENLABS_API_KEY")) or (agent_id is None and not os.environ.get("ELEVENLABS_AGENT_ID")):
            try:
                from dotenv import load_dotenv
                load_dotenv()
            except ImportError:
                pass
        self._api_key = os.environ.get("ELEVENLABS_API_KEY", "") if api_key is None else api_key
        self.agent_id = os.environ.get("ELEVENLABS_AGENT_ID", "") if agent_id is None else agent_id
        self.test_invocation_id = test_invocation_id or os.environ.get("ELEVENLABS_TEST_INVOCATION_ID", "")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def __repr__(self) -> str:
        masked_key = f"...{self._api_key[-4:]}" if len(self._api_key) >= 4 else ("set" if self._api_key else "none")
        return (
            f"ElevenLabsAgentTestingAdapter(agent_id={self.agent_id or 'none'}, "
            f"test_invocation_id={self.test_invocation_id or 'none'}, "
            f"base_url={self.base_url}, api_key={masked_key})"
        )

    def _redact(self, text: str) -> str:
        """Sanitize secrets from error strings, logs, or payload representations."""
        if not self._api_key or len(self._api_key) < 4:
            return text
        return text.replace(self._api_key, "[REDACTED_ELEVENLABS_API_KEY]")

    def get_capabilities(self) -> AdapterCapabilities:
        """Declare capabilities of ElevenLabs Agent Testing API adapter."""
        return AdapterCapabilities(
            supports_text_input=True,
            supports_audio_input=False,
            supports_barge_in=False,
            supports_tool_calls=True,
            supports_multi_agent=False,
            timing_mode="target_reported_call_relative",
        )

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize adapter or override configuration."""
        if config:
            if "api_key" in config:
                self._api_key = str(config["api_key"])
            if "agent_id" in config:
                self.agent_id = str(config["agent_id"])
                self.target_config = self.agent_id
            if "test_invocation_id" in config:
                self.test_invocation_id = str(config["test_invocation_id"])
            if "base_url" in config:
                self.base_url = str(config["base_url"]).rstrip("/")
            if "timeout_seconds" in config:
                self.timeout_seconds = float(config["timeout_seconds"])

    def teardown(self) -> None:
        """Clean up resources."""
        pass

    # -----------------------------------------------------------------------
    # API Operations: Create Test, Run Tests, Get Invocation
    # -----------------------------------------------------------------------

    def create_test(
        self,
        request: Union[CreateResponseUnitTestRequest, CreateToolCallUnitTestRequest, CreateSimulationTestRequest],
        dry_run: bool = True,
        execute: bool = False,
    ) -> Dict[str, Any]:
        """Create a new agent test on ElevenLabs.

        Safety Gate:
          Defaults to dry_run=True. If execute is False, returns a request preview
          and does NOT make remote network calls or create remote test definitions.
        """
        payload = request.to_dict()
        endpoint = f"{self.base_url}/v1/convai/agent-testing/create"

        if dry_run or not execute:
            return {
                "dry_run": True,
                "action": "create_test",
                "method": "POST",
                "url": endpoint,
                "payload": payload,
                "confirmed": False,
                "message": "Dry-run mode: remote test creation was previewed but NOT executed. Set execute=True to commit.",
            }

        # Real execution (mutating operation)
        if not self._api_key:
            raise ValueError("ElevenLabs API key is required to execute remote test creation.")

        headers = {
            "Content-Type": "application/json",
            "xi-api-key": self._api_key,
            "User-Agent": "VoiceBench-AgentTesting-Adapter/0.3.0",
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {
                    "dry_run": False,
                    "action": "create_test",
                    "id": str(data.get("id", "")),
                    "status_code": resp.status,
                }
        except urllib.error.HTTPError as e:
            code = e.code
            err_body = self._redact(e.read().decode("utf-8", errors="replace"))
            if code in (401, 403):
                raise PermissionError(f"ElevenLabs authentication failed (HTTP {code}): Invalid API key.")
            elif code == 422:
                raise ValueError(f"ElevenLabs test creation schema error (HTTP 422): {err_body}")
            raise RuntimeError(f"ElevenLabs create test HTTP {code} error: {err_body}")
        except Exception as e:
            raise RuntimeError(f"ElevenLabs connection failure: {self._redact(str(e))}")

    def run_tests(
        self,
        request: RunTestsRequest,
        dry_run: bool = True,
        execute: bool = False,
    ) -> Dict[str, Any]:
        """Run designated tests on an agent: POST /v1/convai/agents/{agent_id}/run-tests.

        Safety Gate:
          Defaults to dry_run=True. If execute is False, returns a request preview
          and does NOT initiate remote test execution.
        """
        payload = request.to_dict()
        endpoint = f"{self.base_url}/v1/convai/agents/{request.agent_id}/run-tests"

        if dry_run or not execute:
            return {
                "dry_run": True,
                "action": "run_tests",
                "method": "POST",
                "url": endpoint,
                "agent_id": request.agent_id,
                "payload": payload,
                "confirmed": False,
                "message": "Dry-run mode: remote test run was previewed but NOT executed. Set execute=True to run tests.",
            }

        # Real execution (resource-consuming operation)
        if not self._api_key:
            raise ValueError("ElevenLabs API key is required to execute remote test runs.")

        headers = {
            "Content-Type": "application/json",
            "xi-api-key": self._api_key,
            "User-Agent": "VoiceBench-AgentTesting-Adapter/0.3.0",
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {
                    "dry_run": False,
                    "action": "run_tests",
                    "id": str(data.get("id", "")),
                    "test_runs": data.get("test_runs", []),
                    "status_code": resp.status,
                }
        except urllib.error.HTTPError as e:
            code = e.code
            err_body = self._redact(e.read().decode("utf-8", errors="replace"))
            if code in (401, 403):
                raise PermissionError(f"ElevenLabs authentication failed (HTTP {code}): Invalid API key.")
            elif code == 404:
                raise ValueError(f"ElevenLabs agent '{request.agent_id}' not found (HTTP 404).")
            raise RuntimeError(f"ElevenLabs run-tests HTTP {code} error: {err_body}")
        except Exception as e:
            raise RuntimeError(f"ElevenLabs connection failure: {self._redact(str(e))}")

    def get_test_invocation(self, test_invocation_id: str) -> TestInvocationResponse:
        """Fetch test execution results: GET /v1/convai/test-invocations/{test_invocation_id}.

        This is a strictly read-only inspection query.
        """
        if not test_invocation_id:
            raise ValueError("test_invocation_id is required.")
        if not self._api_key:
            raise ValueError("ElevenLabs API key is required to fetch test invocation.")

        endpoint = f"{self.base_url}/v1/convai/test-invocations/{test_invocation_id}"
        headers = {
            "xi-api-key": self._api_key,
            "User-Agent": "VoiceBench-AgentTesting-Adapter/0.3.0",
        }
        req = urllib.request.Request(endpoint, headers=headers, method="GET")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return self.parse_test_invocation_dict(data)
        except urllib.error.HTTPError as e:
            code = e.code
            err_body = self._redact(e.read().decode("utf-8", errors="replace"))
            if code in (401, 403):
                raise PermissionError(f"ElevenLabs authentication failed (HTTP {code}): Invalid API key.")
            elif code == 404:
                raise ValueError(f"Test invocation '{test_invocation_id}' not found (HTTP 404).")
            raise RuntimeError(f"ElevenLabs get test invocation HTTP {code} error: {err_body}")
        except Exception as e:
            raise RuntimeError(f"ElevenLabs connection failure: {self._redact(str(e))}")

    @staticmethod
    def parse_test_invocation_dict(data: Dict[str, Any]) -> TestInvocationResponse:
        """Parse raw JSON dictionary into a typed TestInvocationResponse."""
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict for test invocation response, got {type(data).__name__}")

        inv_id = str(data.get("id") or data.get("test_invocation_id") or "")
        test_runs_raw = data.get("test_runs", [])
        parsed_runs: List[TestRunDetail] = []

        for r in test_runs_raw:
            if not isinstance(r, dict):
                continue
            run_id = str(r.get("test_run_id") or r.get("id") or "")
            t_id = str(r.get("test_id") or "")
            ag_id = str(r.get("agent_id") or "")
            status = str(r.get("status", "pending")).lower()

            # Extract chat history from test_info or top-level
            test_info = r.get("test_info") if isinstance(r.get("test_info"), dict) else {}
            chat_hist = r.get("chat_history") or test_info.get("chat_history") or []
            tool_calls = r.get("tool_calls") or test_info.get("tool_calls") or []
            tool_results = r.get("tool_results") or test_info.get("tool_results") or []

            parsed_runs.append(
                TestRunDetail(
                    test_run_id=run_id,
                    test_invocation_id=inv_id,
                    test_id=t_id,
                    agent_id=ag_id,
                    status=status,
                    passed=r.get("passed"),
                    evaluation_result=r.get("evaluation_result"),
                    chat_history=chat_hist if isinstance(chat_hist, list) else [],
                    tool_calls=tool_calls if isinstance(tool_calls, list) else [],
                    tool_results=tool_results if isinstance(tool_results, list) else [],
                    error_message=r.get("error_message") or r.get("error"),
                    metadata=r.get("metadata", {}),
                )
            )

        # Invocation status is determined by runs if not explicitly on root
        overall_status = str(data.get("status") or (parsed_runs[0].status if parsed_runs else "pending")).lower()

        return TestInvocationResponse(
            id=inv_id,
            agent_id=data.get("agent_id"),
            branch_id=data.get("branch_id"),
            version_id=data.get("version_id"),
            status=overall_status,
            cancelled=bool(data.get("cancelled", False)),
            ran_against_draft=bool(data.get("ran_against_draft", False)),
            repeat_count=int(data.get("repeat_count", 1)),
            bucketing_status=data.get("bucketing_status"),
            test_runs=parsed_runs,
            raw_response=data,
        )

    # -----------------------------------------------------------------------
    # Trace Conversion & Evaluation Interoperability
    # -----------------------------------------------------------------------

    def convert_invocation_to_trace(
        self,
        invocation: TestInvocationResponse,
        scenario_id: str = "ELEVEN-NATIVE-TEST",
        test_run_index: int = 0,
    ) -> ConversationTrace:
        """Convert a completed native test run into a VoiceBench ConversationTrace.

        Enforces:
          - Event provenance is explicitly labeled as 'target_reported'.
          - Timing mode is 'target_reported_call_relative'.
          - Never fabricates wall-clock audio duration or barge-in interruption stamps.
        """
        if not invocation.test_runs:
            collector = TraceCollector(
                scenario_id=scenario_id,
                target_type=self.target_type,
                target_config=self.agent_id or "default",
                session_id=invocation.id,
                use_virtual_time=False,
                timing_mode="target_reported_call_relative",
            )
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                payload={"error": "TestInvocation contains no test_runs"},
                is_simulated=False,
                event_provenance="target_reported",
            )
            return collector.to_trace()

        if test_run_index < 0 or test_run_index >= len(invocation.test_runs):
            raise IndexError(f"test_run_index {test_run_index} out of range (total {len(invocation.test_runs)})")

        run = invocation.test_runs[test_run_index]
        collector = TraceCollector(
            scenario_id=scenario_id,
            target_type=self.target_type,
            target_config=run.agent_id or self.agent_id or "default",
            session_id=run.test_run_id or invocation.id,
            use_virtual_time=False,
            timing_mode="target_reported_call_relative",
        )

        turn_counter = 1
        for item in run.chat_history:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).lower()
            msg = item.get("message") or item.get("text") or item.get("content") or ""
            time_secs = item.get("time_in_call_secs")
            ts_ms = float(time_secs) * 1000.0 if time_secs is not None else float(turn_counter * 1000)

            if role in ("user", "human"):
                collector.record_event(
                    event_type=EventType.USER_INPUT,
                    speaker_id="user",
                    payload=msg,
                    turn_index=turn_counter,
                    timestamp_ms=ts_ms,
                    is_simulated=False,
                    event_provenance="target_reported",
                )
            else:
                collector.record_event(
                    event_type=EventType.TRANSCRIPT_FINAL,
                    speaker_id="agent",
                    payload=msg,
                    turn_index=turn_counter,
                    timestamp_ms=ts_ms,
                    is_simulated=False,
                    event_provenance="target_reported",
                )
                turn_counter += 1

        # Ingest tool calls
        for tc in run.tool_calls:
            if not isinstance(tc, dict):
                continue
            t_name = tc.get("tool_name") or tc.get("name") or tc.get("tool") or ""
            t_args = tc.get("params") or tc.get("parameters") or tc.get("args") or {}
            req_id = tc.get("request_id") or tc.get("id")
            collector.record_event(
                event_type=EventType.TOOL_CALL,
                speaker_id="agent",
                payload={"tool": t_name, "args": t_args, "request_id": req_id},
                turn_index=tc.get("turn_index") or 1,
                is_simulated=False,
                event_provenance="target_reported",
            )

        # Ingest tool results
        for tr in run.tool_results:
            if not isinstance(tr, dict):
                continue
            t_name = tr.get("tool_name") or tr.get("tool") or ""
            t_res = tr.get("result") if "result" in tr else tr.get("result_value")
            req_id = tr.get("request_id") or tr.get("id")
            is_err = bool(tr.get("is_error", False))
            collector.record_event(
                event_type=EventType.TOOL_RESULT,
                speaker_id="system",
                payload={
                    "tool": t_name,
                    "result": t_res,
                    "request_id": req_id,
                    "is_error": is_err,
                    "error": tr.get("error") or tr.get("raw_error_message"),
                },
                turn_index=tr.get("turn_index") or 1,
                is_simulated=False,
                event_provenance="target_reported",
            )

        return collector.to_trace()

    def evaluate_native_test(
        self,
        scenario: Scenario,
        invocation: TestInvocationResponse,
        test_run_index: int = 0,
    ) -> NativeTestExecutionResult:
        """Evaluate a native test invocation against a VoiceBench scenario.

        Separates provider-native evaluation from VoiceBench deterministic evaluation:
          - A provider-native PASS does NOT override VoiceBench deterministic checks.
          - Incomplete or pending states produce INCONCLUSIVE.
          - Cancelled runs produce INCONCLUSIVE.
        """
        if not invocation.test_runs:
            res = ScenarioResult(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                status=TestStatus.INCONCLUSIVE,
                reason=f"Test invocation '{invocation.id}' contains no test runs.",
                failure_category=FailureCategory.FAIL_INFRA.value,
                target_type=self.target_type,
                target_config=self.agent_id or "default",
            )
            return NativeTestExecutionResult(
                test_invocation_id=invocation.id,
                test_run_id="",
                agent_id="",
                provider_status=invocation.status,
                provider_passed=None,
                provider_feedback=None,
                voicebench_result=res,
            )

        if test_run_index >= len(invocation.test_runs):
            raise IndexError(f"test_run_index {test_run_index} out of range.")

        run = invocation.test_runs[test_run_index]

        # Verify agent ownership if configured
        if self.agent_id and run.agent_id and run.agent_id != self.agent_id:
            res = ScenarioResult(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                status=TestStatus.INCONCLUSIVE,
                reason=f"Agent ID mismatch: invocation was run against agent '{run.agent_id}', expected '{self.agent_id}'.",
                failure_category=FailureCategory.FAIL_INFRA.value,
                target_type=self.target_type,
                target_config=self.agent_id,
            )
            return NativeTestExecutionResult(
                test_invocation_id=invocation.id,
                test_run_id=run.test_run_id,
                agent_id=run.agent_id,
                provider_status=run.status,
                provider_passed=run.passed,
                provider_feedback=run.evaluation_result,
                voicebench_result=res,
            )

        # Check provider lifecycle status
        status_norm = run.status.lower()

        if status_norm in (TestRunStatus.PENDING.value, TestRunStatus.RUNNING.value):
            res = ScenarioResult(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                status=TestStatus.INCONCLUSIVE,
                reason=f"Test invocation '{invocation.id}' (run '{run.test_run_id}') is still {status_norm} on ElevenLabs infrastructure.",
                failure_category=FailureCategory.FAIL_INFRA.value,
                target_type=self.target_type,
                target_config=run.agent_id or self.agent_id,
            )
            return NativeTestExecutionResult(
                test_invocation_id=invocation.id,
                test_run_id=run.test_run_id,
                agent_id=run.agent_id,
                provider_status=run.status,
                provider_passed=None,
                provider_feedback=run.evaluation_result,
                voicebench_result=res,
            )

        if invocation.cancelled or status_norm == TestRunStatus.CANCELLED.value:
            res = ScenarioResult(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                status=TestStatus.INCONCLUSIVE,
                reason=f"Test invocation '{invocation.id}' was cancelled by provider.",
                failure_category=FailureCategory.FAIL_INFRA.value,
                target_type=self.target_type,
                target_config=run.agent_id or self.agent_id,
            )
            return NativeTestExecutionResult(
                test_invocation_id=invocation.id,
                test_run_id=run.test_run_id,
                agent_id=run.agent_id,
                provider_status=run.status,
                provider_passed=run.passed,
                provider_feedback=run.evaluation_result,
                voicebench_result=res,
            )

        if status_norm == TestRunStatus.FAILED.value and not run.chat_history:
            res = ScenarioResult(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                status=TestStatus.INCONCLUSIVE,
                reason=f"Provider test run failed with infrastructure/execution error: {run.error_message or 'No details'}",
                failure_category=FailureCategory.FAIL_INFRA.value,
                target_type=self.target_type,
                target_config=run.agent_id or self.agent_id,
            )
            return NativeTestExecutionResult(
                test_invocation_id=invocation.id,
                test_run_id=run.test_run_id,
                agent_id=run.agent_id,
                provider_status=run.status,
                provider_passed=run.passed,
                provider_feedback=run.evaluation_result,
                voicebench_result=res,
            )

        # Completed run: convert to trace and perform VoiceBench deterministic evaluation
        trace = self.convert_invocation_to_trace(invocation, scenario_id=scenario.id, test_run_index=test_run_index)
        evaluator = Evaluator()
        vb_result = evaluator.evaluate(scenario, trace)

        return NativeTestExecutionResult(
            test_invocation_id=invocation.id,
            test_run_id=run.test_run_id,
            agent_id=run.agent_id,
            provider_status=run.status,
            provider_passed=run.passed,
            provider_feedback=run.evaluation_result,
            voicebench_result=vb_result,
            trace=trace,
        )

    def execute_scenario(self, scenario: Scenario) -> ConversationTrace:
        """Execute scenario via TargetAdapter interface.

        Retrieves completed test invocation if test_invocation_id is configured.
        Refuses to invent synthetic test IDs or make unconfirmed remote mutations.
        """
        if not self.test_invocation_id:
            collector = TraceCollector(
                scenario_id=scenario.id,
                target_type=self.target_type,
                target_config=self.agent_id or "default",
                session_id="unconfigured_test_invocation",
                use_virtual_time=False,
                timing_mode="target_reported_call_relative",
            )
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                payload={
                    "error": "ConfigurationError: Missing ELEVENLABS_TEST_INVOCATION_ID. "
                             "Specify an existing test invocation ID to evaluate, or use 'voicebench agent-test' to inspect/run tests.",
                },
                is_simulated=False,
                event_provenance="client_observed",
            )
            return collector.to_trace()

        try:
            invocation = self.get_test_invocation(self.test_invocation_id)
            return self.convert_invocation_to_trace(invocation, scenario_id=scenario.id)
        except Exception as exc:
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                payload={"error": f"InvocationFetchError: {self._redact(str(exc))}"},
                is_simulated=False,
                event_provenance="client_observed",
            )
            return collector.to_trace()
