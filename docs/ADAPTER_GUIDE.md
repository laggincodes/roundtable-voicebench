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
