# VoiceBench — Milestone 3: ElevenLabs Integration Decision Note

**Date:** 2026-10-21  
**Author:** Senior Open-Source Hackathon Lead  
**Scope:** Milestone 3 (Gate 1 Deliverable)  
**Track:** FOSS FORGE 2.0 (ElevenLabs AI Roundtable Track — Problem 1)

---

## 1. Official ElevenLabs API Research & Documentation References

The official ElevenLabs API documentation was reviewed on **2026-10-21**:

1. **Conversational AI API Overview & Conversation Details:**
   - URL: `https://elevenlabs.io/docs/conversational-ai/api-reference`
   - Endpoint: `GET https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}`
   - Header: `xi-api-key: <api_key>`
   - Response Schema:
     - `agent_id`: string
     - `conversation_id`: string
     - `status`: enum (`initiated`, `in-progress`, `processing`, `done`, `failed`)
     - `metadata`: object containing `start_time_unix_secs`, `call_duration_secs`, `cost_fiat`
     - `analysis`: object containing `call_successful`, `transcript_summary`
     - `transcript`: array of objects, each containing:
       - `role`: string (`"user"` or `"agent"`)
       - `message`: string (spoken transcript content)
       - `time_in_call_secs`: number (timestamp offset relative to call start)

2. **Agent Configuration & Verification:**
   - Endpoint: `GET https://api.elevenlabs.io/v1/convai/agents/{agent_id}`
   - Header: `xi-api-key: <api_key>`
   - Response: Returns agent metadata, prompt configuration, voice ID, LLM parameters, and tool configurations.

3. **Agent Automated Testing API:**
   - URL: `https://elevenlabs.io/docs/eleven-agents/api-reference/tests/run-tests`
   - Endpoint: `POST https://api.elevenlabs.io/v1/convai/agents/{agent_id}/run-tests`
   - Purpose: Runs predefined ElevenLabs test simulations on the agent.

4. **Real-Time WebSocket Protocol:**
   - URL: `wss://api.elevenlabs.io/v1/convai/conversation?agent_id={agent_id}`
   - Streaming JSON frames with Base64 audio chunks and events.

---

## 2. Integration Scope & Architecture Decision

To adhere strictly to VoiceBench's core standards of **honest measurement, zero falsification, and verifiable evidence**:

### Dedicated Adapter: `ElevenLabsConvAIAdapter`
We implement `ElevenLabsConvAIAdapter` (`voicebench.adapters.elevenlabs`), subclassing `TargetAdapter`.

### Supported Capabilities:
- `supports_text_input = True`
- `supports_audio_input = False` (unless audio streaming pipeline is attached)
- `supports_barge_in = False` (REST conversation details do not provide sub-second audio energy cancellation timestamps)
- `supports_tool_calls = True` (tool executions tracked in session metadata)
- `supports_multi_agent = False` (native ElevenLabs ConvAI agents are single-agent; multi-agent roundtables require orchestrator routing)
- `timing_mode = "observed_wall_clock"` (or target-reported from `time_in_call_secs`)

### Observable Evidence vs. Unsupported Scenarios:
| Scenario / Feature | ElevenLabs REST Support | VoiceBench Handling | Rationale |
| :--- | :---: | :--- | :--- |
| **Expert Persona Consistency (`SCEN-ROLE-03`)** | **YES** | Can evaluate against agent transcript | Verifies domain boundaries and responses from agent transcript. |
| **Tool Call / Grounding (`SCEN-TOOL-05`)** | **YES** | Can evaluate tool calls in transcript | Verifies tool triggers and factual grounding. |
| **Targeted Routing (`SCEN-ROUT-01`)** | **CONDITIONAL** | Supported if multi-agent proxy or single expert addressed | Evaluates speaker attribution. |
| **Audio Barge-in Yield (`SCEN-INT-02`)** | **NO** | **REJECTED via `validate_scenario_compatibility`** | REST `time_in_call_secs` does not capture audio cancellation latency. Claiming barge-in measurement without audio WebSocket would be dishonest. |
| **Topic Recovery Detour (`SCEN-REC-04`)** | **YES** | Supported if prompt detour is in transcript | Verifies recovery keywords after detour. |
| **Multi-Speaker Floor Collision (`SCEN-COORD-06`)** | **NO** | **REJECTED via `validate_scenario_compatibility`** | Single-agent ConvAI has no second agent to collide with. |

---

## 3. Resilience, Redaction & Security Rules

1. **Strict Credential Isolation:**
   - Credentials read only from `ELEVENLABS_API_KEY` (or explicit parameter).
   - All exceptions, logs, and trace metadata must redact the API key (`xi-api-key: [REDACTED]`).
   - `.env` files are ignored in `.gitignore` and never committed.
2. **Deterministic Failure Mapping:**
   - 401 / 403 (Invalid / Missing API Key) -> `EventType.TRANSPORT_ERROR` -> `INCONCLUSIVE` (`FAIL_INFRA`).
   - 429 (Rate Limit / Quota Exceeded) -> `EventType.TRANSPORT_ERROR` -> `INCONCLUSIVE` (`FAIL_INFRA`).
   - 500 / 502 / 503 / 504 -> `EventType.TRANSPORT_ERROR` -> `INCONCLUSIVE` (`FAIL_INFRA`).
   - Network Timeout / Connection Drop -> `EventType.TRANSPORT_ERROR` -> `INCONCLUSIVE` (`FAIL_INFRA`).
   - Malformed JSON / Missing transcript array -> `EventType.TRANSPORT_ERROR` -> `INCONCLUSIVE` (`FAIL_INFRA`).
3. **Offline Zero-Dependency Guarantee:**
   - Built using standard Python `urllib.request` (zero new external runtime dependencies).
   - Full mock suite tests all network and parsing edge cases offline without live credentials.
