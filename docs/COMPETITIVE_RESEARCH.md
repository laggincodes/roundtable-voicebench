# VoiceBench — Competitive Research & Architectural Analysis

**Document Status:** Grounded Architectural Reference  
**Framework Version:** 0.4.2  
**Baseline Git Commit:** `8b286222c67bdc018531a327a1c95e2b774b90d1`  
**Primary Target:** FOSS FORGE 2.0 Problem 1 (AI Roundtable)  

---

## 1. Executive Summary

VoiceBench is an independent, open-source evaluation framework for voice agents and multi-agent conversational systems. In accordance with clean-room engineering practices, this document surveys leading public voice evaluation and orchestration projects to synthesize architectural lessons, document license boundaries, and explicitly ground how VoiceBench's implementation differs.

VoiceBench does not seek to clone external projects or wrap provider-specific testing features into a superficial dashboard. Its core architecture follows a strict, deterministic evaluation loop:

$$\text{Scenario (YAML)} \longrightarrow \text{Target Adapter} \longrightarrow \text{Captured Evidence} \longrightarrow \text{Deterministic Evaluator} \longrightarrow \text{Regression Comparator}$$

---

## 2. Comparative Matrix of Evaluated Projects

| Project / System | Repository / Documentation URL | License | Key Capabilities & Architecture | Valuable Architectural Lessons | How VoiceBench Differs (Grounded in Code) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **rand0wn/voice-eval** | `https://github.com/rand0wn/voice-eval` | Apache-2.0 | Provider-neutral adapters, multi-turn scenarios, per-turn grading, audio artifacts, latency percentiles, side-by-side adapter comparison, CI gates, JSON & Markdown reports, ElevenLabs WebSocket adapter. | Separation of scenario definition from adapter transport; capturing audio artifacts per turn; side-by-side provider diffing. | **Single-Agent vs. Multi-Agent**: `voice-eval` assumes a single 1-on-1 dialogue. VoiceBench natively evaluates multi-speaker floor arbitration, persona boundary adherence, and topic recovery under mid-turn barge-in (`SCEN-COORD-06`, `SCEN-INT-02`, `SCEN-REC-04`). VoiceBench also enforces strict scenario-to-transcript alignment and nested tool result assertions. |
| **ServiceNow/eva (EVA-Bench)** | `https://github.com/ServiceNow/eva` | MIT | Academic/enterprise voice benchmark; bot-to-bot dialogue simulation in customer service (ITSM/CSM/HR); separates task accuracy (EVA-A) from interaction experience (EVA-X); user-simulator goal validation. | Separating task correctness from conversational fluidity; requiring user simulator to satisfy goal preconditions before accepting test runs. | **Heavyweight vs. Lightweight**: `eva` requires substantial enterprise infrastructure and focuses on 1-on-1 customer service. VoiceBench runs locally via a lightweight standard library Python CLI/daemon, executes offline deterministically in < 1 second, and directly tests multi-party debate dynamics. |
| **Pipecat** | `https://github.com/pipecat-ai/pipecat` | BSD-2-Clause | Real-time conversational agent framework; pipeline architecture for audio/STT/LLM/TTS; automated end-to-end tests; saves conversations as artifacts for offline replay. | Saving full multi-turn conversation traces as immutable artifacts; ability to re-evaluate recorded traces offline without recurring cloud API calls. | **Framework vs. Evaluation Harness**: Pipecat is an agent construction framework. VoiceBench is an evaluation framework that treats the conversational pipeline as an external black box under test, evaluating it through pluggable target adapters (`LocalMockAdapter`, `ElevenLabsAdapter`, `AIRoundtableOrchestrator`, `GenericHttpAdapter`). |
| **LiveKit Agents** | `https://github.com/livekit/agents` | Apache-2.0 | Real-time WebRTC voice agent platform; native testing patterns for agent sessions, VAD event hooks, speech interruption handlers. | Real agent session lifecycle verification; handling WebRTC voice packet streams; intercepting user barge-in and agent speech cancellation. | **Platform vs. Objective Benchmark**: LiveKit's tests verify LiveKit's own worker protocols. VoiceBench provides an independent benchmark suite that can grade LiveKit, ElevenLabs, or local agents using identical YAML scenario criteria and latency thresholds. |
| **ElevenLabs Native Testing API & WebSockets** | `https://elevenlabs.io/docs/eleven-agents` | Proprietary Developer API | Cloud-hosted test runner for ElevenLabs Conversational AI (`/v1/convai/agents/{id}/run-tests`); simulation tests; tool call unit tests; response criteria; WebSockets for live conversation. | Official message schemas (`conversation_initiation_metadata`, `user_transcript`, `agent_response`, `interruption`); separation of simulation vs unit testing. | **Authoritative Evaluator vs. Cloud Black Box**: ElevenLabs Native Testing API only tests single ElevenLabs agents in the cloud. VoiceBench provides a local deterministic evaluation engine that separately evaluates VoiceBench assertions on ElevenLabs traces, validates multi-agent roundtables, and never accepts a provider PASS as a VoiceBench PASS without ground-truth verification. |
| **Google Gemini (SDK / API)** | `https://ai.google.dev/gemini-api/docs` | Developer API | Multimodal understanding, structured output, function calling, audio understanding. | Useful for developer-assisted scenario drafting and ambiguity detection. | **Developer Tooling Only vs. Core Runtime**: Per FOSS FORGE 2.0 instructions, components outside ElevenLabs must be open-source. VoiceBench strictly restricts Gemini to optional, developer-only scenario drafting; it **never** determines authoritative PASS/FAIL verdicts. All evaluations are 100% deterministic code assertions. |

---

## 3. Core Architectural Differentiators in VoiceBench

### 3.1 Strict Scenario-to-Transcript Alignment
When evaluating pre-recorded or completed conversations (e.g., via `ElevenLabsConvAIAdapter`), VoiceBench verifies that the recorded trace actually executed the required scenario before evaluating agent expectations:
* Verifies scenario preconditions: correct target `agent_id`, required speaker roles.
* Matches all required user turns in sequence.
* Returns `INCONCLUSIVE` (with an explicit attribution notice) if the recorded conversation was aborted, addressed to the wrong agent, or contains unrelated prompts.

### 3.2 Four-State Deterministic Assertion Engine
Unlike binary test runners, VoiceBench categorizes outcomes into four explicit statuses:
1. `PASS`: All scenario expectations, turn sequences, keyword grounding, and tool assertions met.
2. `FAIL`: Agent violated scenario criteria (e.g., failed to yield floor, hallucinated facts, dropped topic).
3. `INCONCLUSIVE`: Environmental or conversational prerequisites were missing (e.g., missing user turns, audio silence packets unavailable).
4. `NOT_RUN / BLOCKED`: Prerequisites unconfigured (e.g., missing provider credentials or network offline).

### 3.3 Strict Structured & Nested Tool-Result Assertions
VoiceBench inspects not only tool names and input arguments, but also deeply nested JSON tool execution results:
* Strict type equality (rejecting stringified floats or loose type coercion).
* Keypath extraction (e.g., `expected_tool_result_paths={"data.carbon_price": 42.5}`).
* Disambiguated correlation: If multiple tool calls occur in a turn, VoiceBench requires unambiguous pairing; ambiguous correlations yield `INCONCLUSIVE` rather than guessing.

### 3.4 Multi-Tier Evidence Provenance
VoiceBench strictly rejects representing mock timing as physical acoustic latency:
* `virtual_simulation`: Local deterministic simulated clock (e.g., `LocalMockAdapter`).
* `target_reported_call_relative`: Cloud provider reported timestamps (e.g., `ElevenLabsConvAIAdapter`).
* `client_observed`: Monotonic wall-clock timestamps recorded by the local harness runner.
* `acoustically_observed_loopback`: Verified microphone-to-speaker acoustic measurement (reported as `null` unless physical audio loopback hardware is active).

### 3.5 Explainable Regression Comparator
VoiceBench's `RegressionComparator` performs scenario-by-scenario differential analysis across test runs:
* Rejects comparing runs with incompatible timing modes (e.g., comparing simulated mock time against wall-clock audio).
* Explicitly highlights:
  * Regressions: Previously passing scenarios that now fail (`PASS ➔ FAIL`).
  * Improvements: Previously failing scenarios that now pass (`FAIL ➔ PASS`).
  * Stable: Unchanged passes or failures.
* Outputs both human-readable Markdown diff reports and machine-readable JSON.

---

## 4. Architectural Boundaries and Principles

1. **Clean-Room Integrity**: No code or copyrighted scenario text is vendored from external projects.
2. **Deterministic Priority**: LLM-as-a-judge is excluded from core test evaluation; all pass/fail gates use code-level assertions.
3. **Zero Exfiltration**: Local `.env` tokens and provider credentials are redacted from all dashboard APIs, logs, and JSON exports.
