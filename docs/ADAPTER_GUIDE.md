# VoiceBench — Target Adapter & Integration Guide

This guide defines the integration contract and operational guidelines for connecting multi-agent voice systems (such as conversational roundtables) to the **VoiceBench** evaluation framework.

---

## 1. Architectural Philosophy: Black-Box Evaluation

VoiceBench evaluates voice agents as **black-box conversational systems**. An evaluation runner issues scenario turns (text or synthesized speech) and inspects the resulting trace of conversation events (agent utterances, turn yields, tool invocations, and timing metadata).

To maintain measurement integrity across different runtime environments:
1. **Never conflate timing modes:** Virtual clock simulation (`virtual_simulation`) and real wall-clock timing (`observed_wall_clock`) must never be compared directly.
2. **Explicit provenance:** Every recorded event must explicitly declare its provenance (`local_virtual`, `client_observed`, or `target_reported`).
3. **Transport failures are inconclusive:** If an adapter encounters a network timeout, socket drop, or HTTP 500, it records a `TRANSPORT_ERROR` event. The evaluator classifies this as `INCONCLUSIVE` (`FAIL_INFRA`), distinguishing infrastructure flakiness from behavioral model failure.

---

## 2. The `TargetAdapter` Contract

All adapters inherit from `voicebench.adapters.base.TargetAdapter` and implement two abstract methods:

```python
class TargetAdapter(ABC):
    @abstractmethod
    def get_capabilities(self) -> AdapterCapabilities:
        """Return explicit declaration of supported scenario features and timing mode."""
        pass

    @abstractmethod
    def execute_scenario(self, scenario: Scenario) -> ConversationTrace:
        """Execute scenario against target and return recorded ConversationTrace."""
        pass
```

### Capability Declarations (`AdapterCapabilities`)

Before running a scenario, the runner calls `adapter.validate_scenario_compatibility(scenario)`. If a scenario requires audio input or barge-in and the adapter lacks support, execution is rejected cleanly with an explanatory error before making target calls:

```python
@dataclass
class AdapterCapabilities:
    supports_text_input: bool = True
    supports_audio_input: bool = False
    supports_barge_in: bool = False
    supports_tool_calls: bool = False
    supports_multi_agent: bool = True
    timing_mode: str = "virtual_simulation"  # or "observed_wall_clock"
```

---

## 3. Generic HTTP Adapter Protocol

VoiceBench provides an out-of-the-box `GenericHttpAdapter` (`voicebench.adapters.http`) allowing external teams to connect any HTTP-accessible voice roundtable service without proprietary SDKs.

### Request Specification

- **Method:** `POST`
- **Header:** `Content-Type: application/json`
- **Timeout:** Configurable (default: 15.0 seconds)

#### Request JSON Schema:

```json
{
  "scenario_id": "SCEN-ROUT-01",
  "session_id": "8c5912a2-3f12-4e89-a212-000000000001",
  "preconditions": {
    "active_agents": [
      {
        "id": "dr_aris",
        "role": "Lead Architect",
        "domain_keywords": ["architecture", "scaling", "latency"]
      },
      {
        "id": "elena",
        "role": "Security Lead",
        "domain_keywords": ["auth", "rbac", "encryption"]
      }
    ],
    "initial_topic": "System architecture design"
  },
  "turns": [
    {
      "turn_index": 1,
      "speaker": "human_moderator",
      "payload": "Dr. Aris, what is our target service latency?",
      "input_type": "text",
      "target_expert": "dr_aris",
      "inject_delay_ms": 0.0,
      "barge_in": false,
      "barge_in_delay_ms": null
    }
  ]
}
```

### Response Specification

The target service must return HTTP 200 with a JSON payload containing the structured trace of events observed or produced by the roundtable:

```json
{
  "status": "success",
  "session_id": "8c5912a2-3f12-4e89-a212-000000000001",
  "error": null,
  "events": [
    {
      "event_type": "TRANSCRIPT_FINAL",
      "speaker_id": "dr_aris",
      "payload": "Our target service latency is strictly sub-100ms for p95 requests.",
      "turn_index": 1,
      "source_timestamp_ms": 320.0,
      "audio_duration_ms": 2800.0,
      "metadata": {
        "model": "eleven_multilingual_v2",
        "tokens_generated": 14
      }
    }
  ]
}
```

### Event Object Fields

| Field | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `event_type` | `string` | **Yes** | One of `TRANSCRIPT_FINAL`, `SPEECH_CHUNK`, `INTERRUPT_ACK`, `TOOL_CALL`, `TOOL_RESULT` |
| `speaker_id` | `string` | Optional | Agent identifier (e.g. `dr_aris`, `elena`) |
| `payload` | `string` or `object` | **Yes** | Text transcript, tool call arguments, or event payload |
| `turn_index` | `integer` | Optional | Turn index that triggered or corresponds to this event |
| `source_timestamp_ms` | `float` | Optional | Internal target millisecond timestamp (relative to session start) |
| `audio_duration_ms` | `float` | Optional | Target-measured audio synthesis or speech playback duration |
| `metadata` | `object` | Optional | Additional diagnostic metadata (model ID, token counts, etc.) |

---

## 4. Measurement Integrity & Provenance Rules

VoiceBench maintains strict separation between client-measured wall-clock timing and target-reported internal timing:

1. **Client Monotonic Clock:**
   - The HTTP adapter records `client_timestamp_ms` using Python's `time.monotonic()` from the start of the HTTP request to the completion of the response stream.
   - Labeled with `event_provenance="client_observed"`.
2. **Target Internal Clock:**
   - If the remote server provides `source_timestamp_ms`, this is preserved in `TraceEvent.source_timestamp_ms` and labeled `event_provenance="target_reported"`.
   - VoiceBench **never** confuses client-observed network latency with server-internal model latency.
3. **Null vs Zero Metrics:**
   - If a metric (e.g., `yield_latency_ms`) is not measurable due to lack of barge-in events or instrumentation, it is recorded as `null` (`None`) with an explanatory reason in `metric_notes`. It is never recorded as `0.0`.

---

## 5. Reference HTTP Target Server

For offline development, testing, and continuous integration, VoiceBench includes a lightweight reference HTTP server that implements the protocol:

```bash
# Run reference server in compliant mode on port 8080
python -c "from voicebench.targets.reference_http_server import run_standalone_server; run_standalone_server(8080, 'compliant')"
```

### Testing Error Modes

The reference server supports simulating backend failure modes to verify pipeline resilience:

```python
from voicebench.targets.reference_http_server import TestServerThread

# Modes: 'compliant', 'timeout', '500', 'malformed_json', 'missing_events'
server = TestServerThread(port=0, mode="timeout")
server.start()
print("Test server running at:", server.url)
...
server.stop()
```

---

## 6. Integration Checklist for Peer Teams

When integrating an external roundtable implementation into VoiceBench:

- [ ] **Verify HTTP Response Headers:** Ensure endpoint responds with `Content-Type: application/json`.
- [ ] **Preserve Speaker IDs:** Ensure `speaker_id` in events matches the configured `id` in `active_agents`.
- [ ] **Accurate Turn Indexing:** Associate response events with the corresponding `turn_index` from the request.
- [ ] **Explicit Timing Separation:** If reporting internal latency, place it in `source_timestamp_ms`; do not synthesize wall-clock durations.
- [ ] **Handle Interruptions:** If implementing barge-in, emit an `INTERRUPT_ACK` event with the millisecond offset when the agent stopped generating audio.
- [ ] **Run Regression Diff:** Execute `voicebench diff` between consecutive model checkpoints to detect behavioral regressions.

---

## 7. ElevenLabs Conversational AI Adapter (`ElevenLabsConvAIAdapter`)

VoiceBench includes a dedicated adapter for evaluating agents built on the official **ElevenLabs Conversational AI** platform (`/v1/convai`).

### Supported Capabilities
```python
AdapterCapabilities(
    supports_text_input=True,
    supports_audio_input=False,
    supports_barge_in=False,        # REST conversation details do not provide sub-second audio energy cancellation timestamps
    supports_tool_calls=True,       # Tracked in session transcript tool_calls and tool_results
    supports_multi_agent=False,     # Native ConvAI sessions are single agent
    timing_mode="target_reported_call_relative",
)
```

### Official API Endpoints
- **Agent Verification:** `GET https://api.elevenlabs.io/v1/convai/agents/{agent_id}`
- **Conversation Details:** `GET https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}`
- **Header:** `xi-api-key: <api_key>`

### Timing Semantics & Provenance Integrity
VoiceBench enforces rigorous provenance tracking for all ElevenLabs ConvAI data:
1. **Target-Reported Call-Relative Timing (`target_reported_call_relative`):**
   - The ElevenLabs REST API reports `time_in_call_secs` for each transcript turn, representing elapsed seconds since the conversation session began on ElevenLabs infrastructure.
   - VoiceBench converts this to milliseconds (`source_timestamp_ms = time_in_call_secs * 1000.0`), tagged with `event_provenance="target_reported"` and `is_simulated=False`.
   - VoiceBench **never** presents target-reported call-relative offsets as client-observed wall-clock latency.
2. **Client-Observed Fetch Latency (`fetch_latency_ms`):**
   - Measured locally via Python's `time.monotonic()` strictly around the HTTPS GET request to the `/v1/convai/conversations/{conversation_id}` endpoint.
   - Recorded in `SESSION_END.payload["fetch_latency_ms"]` with `event_provenance="client_observed"`.
   - Annotated with diagnostic notes explaining that this measures API payload retrieval time, NOT conversational turnaround or audio playback latency.
3. **Cross-Timing-Mode Incompatibility Refusal:**
   - In the regression comparator, `target_reported_call_relative` runs cannot be compared against `virtual_simulation` or `observed_wall_clock` runs for timing metrics.
   - The comparator sets `timing_modes_compatible = False`, suppresses misleading numerical timing deltas (`delta = None`), and attaches an explicit diagnostic note. Non-timing metrics (e.g. `turn_count`) continue to compare normally.

### Tool-Call Schema & Evaluation Support
The ElevenLabs adapter ingests and parses native tool call structures from `/v1/convai/conversations/{conversation_id}`:
- **Tool Invocations (`transcript[].tool_calls`):**
  - Extracts `tool_name` and decodes `params_as_json`. Emits `EventType.TOOL_CALL`.
  - Malformed JSON strings are captured under `payload["args"]["_raw"]`, enabling schema mismatch detection.
- **Tool Results (`transcript[].tool_results`):**
  - Extracts `result_value`, `is_error`, `error_type`, and `raw_error_message`. Emits `EventType.TOOL_RESULT`.
  - If a tool result has `is_error: True`, the VoiceBench evaluator flags a definitive `FAIL_TOOL` defect with the corresponding error evidence.

### Configuration on Windows PowerShell
Set environment variables securely in your terminal session (never commit keys to Git):
```powershell
$env:ELEVENLABS_API_KEY = "sk_..."
$env:ELEVENLABS_AGENT_ID = "agent_..."
$env:ELEVENLABS_CONVERSATION_ID = "conv_..."
```

### Running Evaluation Against ElevenLabs
```bash
# Evaluate a real completed conversation for role consistency
python -m voicebench.cli run \
  --suite scenarios/roundtable/SCEN-ROLE-03.yaml \
  --target elevenlabs \
  --conversation-id "conv_your_conversation_id"

# Pass credentials, agent ID, and conversation ID explicitly via CLI
python -m voicebench.cli run \
  --suite scenarios/roundtable/SCEN-TOOL-05.yaml \
  --target elevenlabs \
  --api-key "sk_..." \
  --agent-id "agent_..." \
  --conversation-id "conv_your_conversation_id"
```

> **Safety & Integrity Guarantees:**
> - **Read-Only API Calls:** VoiceBench queries `GET /v1/convai/conversations/{conversation_id}`. It never triggers outbound calls or generates billable conversational turns.
> - **Zero Fabricated IDs:** If `--conversation-id` is omitted, VoiceBench immediately records `ConfigurationError: Missing ELEVENLABS_CONVERSATION_ID` (verdict: `INCONCLUSIVE`), refusing to invent fictional session IDs.
> - **No Multi-Scenario Reuse:** A single conversation cannot be evaluated across multiple scenarios. The CLI and adapter reject running multi-scenario suites against a single conversation ID.
> - **Agent Ownership Verification:** When an agent ID is configured, VoiceBench validates that the conversation record actually originated from that agent, flagging discrepancies as `AgentMismatchError`.

### Capability Matrix & Honest Disclosures
| Scenario | Capability Required | Supported by ElevenLabs REST? | VoiceBench Behavior |
| :--- | :--- | :---: | :--- |
| `SCEN-ROLE-03` | Persona consistency & boundaries | **YES** | Evaluates response text against grounded boundaries. |
| `SCEN-TOOL-05` | Tool invocation & numeric grounding | **YES** | Evaluates tool triggering, arguments, and result errors. |
| `SCEN-INT-02` | Sub-second audio barge-in yield | **NO** | Rejects with `UnsupportedCapabilityError` (produces `INCONCLUSIVE`). |
| `SCEN-COORD-06`| Multi-agent floor coordination | **NO** | Rejects with `UnsupportedCapabilityError` (produces `INCONCLUSIVE`). |

### Offline Verification (Zero Credentials Required)
Developers without paid ElevenLabs accounts can verify the adapter logic locally using the built-in mock server:
```bash
python -m pytest tests/test_elevenlabs_adapter.py -v
```
All 16 tests execute completely offline without network requests, external dependencies, or API costs.

---

## 8. ElevenLabs Agent Testing API Adapter (`ElevenLabsAgentTestingAdapter`)

VoiceBench provides dedicated integration with the official **ElevenLabs Agent Testing API** (`/v1/convai/agent-testing` and `/v1/convai/agents/{agent_id}/run-tests`).

### Distinction: Recorded Conversations vs. Native Agent Tests
| Feature | Completed-Conversation Evaluation (`ElevenLabsConvAIAdapter`) | Native Agent Testing (`ElevenLabsAgentTestingAdapter`) |
| :--- | :--- | :--- |
| **API Endpoints** | `GET /v1/convai/conversations/{id}` | `POST /v1/convai/agent-testing/create`<br>`POST /v1/convai/agents/{agent_id}/run-tests`<br>`GET /v1/convai/test-invocations/{id}` |
| **Workflow** | Evaluates transcripts from completed live calls | Programmatically registers unit/simulation tests and triggers remote executions |
| **Test Scope** | Retroactive analysis of real human/agent sessions | Controlled, repeatable scenario and tool-calling validation |
| **CLI Command** | `voicebench run --target elevenlabs --conversation-id <id>` | `voicebench agent-test [create|run|get]`<br>`voicebench run --target elevenlabs_agent_testing` |

### Supported Native Test Types
1. **Simulation Tests (`CreateSimulationTestRequest`, `type: "simulation"`):** Multi-turn conversational simulations driven by scenario goals and success criteria.
2. **Response Unit Tests (`CreateResponseUnitTestRequest`, `type: "llm"`):** Single-turn next-reply validation checking specific phrasing or evaluation criteria.
3. **Tool Call Unit Tests (`CreateToolCallUnitTestRequest`, `type: "tool"`):** Verification that the agent triggers expected tool names with designated parameters.

### Safety & Cost Controls
Creating remote tests or triggering simulated conversations alters account state or consumes credits. VoiceBench enforces:
- **Dry-Run Preview by Default:** CLI commands (`voicebench agent-test create`, `voicebench agent-test run`) generate a structured request preview and exit without making network requests.
- **Explicit Confirmation Flag (`--execute`):** Remote mutations and executions only occur when `--execute` is supplied.
- **Read-Only Inspection (`voicebench agent-test get`):** Inspecting invocations and evaluating traces is strictly read-only.
- **Strict Secret Redaction:** API keys are never printed in previews, logs, or error diagnostics.

### Result Mapping & Evaluation Separation
VoiceBench strictly distinguishes provider-native evaluation from VoiceBench deterministic evaluation:
- **Provider-Native Verdict:** Captures ElevenLabs' internal judge verdict (`passed: true/false`, feedback summary).
- **VoiceBench Deterministic Verdict:** Evaluates the resulting conversation trace against scenario expectations:
  - `PASS`: All deterministic rules (keywords, tool calls, nested tool-result paths) satisfied.
  - `FAIL` (`FAIL_TOOL` / `FAIL_ROLE`): Mismatched arguments, missing keywords, or tool result defects. A provider-native PASS **never** overrides a VoiceBench deterministic failure.
  - `INCONCLUSIVE` (`FAIL_INFRA`): Test run is still `pending` or `running` (never treated as pass), cancelled by provider, or missing tool telemetry.

### Example CLI Usage
```bash
# 1. Preview creating a native simulation test from a scenario (Dry-Run by default)
python -m voicebench.cli agent-test create \
  --scenario scenarios/roundtable/SCEN-TOOL-05.yaml \
  --type tool

# 2. Commit test creation to ElevenLabs (requires explicit --execute)
python -m voicebench.cli agent-test create \
  --scenario scenarios/roundtable/SCEN-TOOL-05.yaml \
  --type tool \
  --agent-id "agent_your_agent_id" \
  --execute

# 3. Preview running tests on an agent
python -m voicebench.cli agent-test run \
  --test-id "test_your_test_id" \
  --agent-id "agent_your_agent_id" \
  --repeat-count 1

# 4. Fetch test invocation and run deterministic VoiceBench evaluation (Read-Only)
python -m voicebench.cli agent-test get \
  --invocation-id "inv_your_invocation_id" \
  --scenario scenarios/roundtable/SCEN-TOOL-05.yaml
```

### Offline Test Verification
Run the complete offline test suite for the Agent Testing API adapter:
```bash
python -m pytest tests/test_elevenlabs_agent_testing.py -v
```
All 21 tests execute completely offline using synthetic fixtures with zero remote API requests.
