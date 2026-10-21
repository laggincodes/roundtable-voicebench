# VoiceBench (Roundtable VoiceBench)

An open-source evaluation framework for real-time multi-agent voice conversations, testing interruption handling, expert roles, conversation recovery, and behavioral regressions.

> **Status:** 🚀 **Submission Ready (Verified Clean-Room Framework & Live Smoke Validated)**
> *VoiceBench delivers an automated, reproducible evaluation framework for conversational voice systems, featuring deterministic assertion checking, structured nested tool-result verification, scenario alignment validation, correlation integrity, a Generic HTTP adapter, and a dedicated ElevenLabs completed-conversation adapter with rigorous credential redaction and timing provenance separation.*

VoiceBench is an independent, open-source evaluation and regression-testing framework designed specifically for real-time conversational voice systems.

Developed for **FOSS FORGE 2.0 (Build with ElevenLabs)**, VoiceBench targets **Problem 1: AI Roundtable**, providing developers with an automated, reproducible way to test whether conversational voice agents maintain expert personas, execute tool calls accurately, produce structured tool results, handle interruptions cleanly, and recover context after detours.

---

## 1. Quickstart: Running VoiceBench in 2 Minutes

### 1.1 Prerequisites & Installation
VoiceBench is engineered to run offline with minimal, transparent dependencies:
- Python 3.10+ (tested on Python 3.13)
- Runtime dependencies: `pyyaml` (for scenario parsing), `elevenlabs` (official SDK), `python-dotenv` (for loading `.env` credentials)
- Developer dependencies: `pytest` (for automated test suite)

```bash
# Clone or navigate to the voicebench repository
cd voicebench

# Install required dependencies
pip install -r requirements.txt
```

### 1.2 Inspect & Validate Scenarios
```bash
# 1. List all available scenarios
python -m voicebench.cli scenarios list

# 2. Validate canonical roundtable scenarios against schema
python -m voicebench.cli scenarios validate scenarios/roundtable

# 3. Validate dedicated ElevenLabs live smoke scenario
python -m voicebench.cli scenarios validate scenarios/live/elevenlabs
```

### 1.3 Run Scenarios Against Target Adapters
```bash
# 4. Run the complete 6-scenario suite against the compliant Baseline mock target
python -m voicebench.cli run \
  --suite scenarios/roundtable \
  --target mock \
  --target-config baseline \
  --output results/baseline.json

# 5. Run against a candidate configuration with an introduced defect
python -m voicebench.cli run \
  --suite scenarios/roundtable \
  --target mock \
  --target-config defect_bargein_lag \
  --output results/candidate_bargein.json

# 6. Run scenarios against an external roundtable service via Generic HTTP Adapter
python -m voicebench.cli run \
  --suite scenarios/roundtable \
  --target http \
  --endpoint-url http://localhost:8080/api/v1/roundtable/execute \
  --output results/http_run.json

# 7. Run dedicated smoke scenario against a real completed ElevenLabs conversation
python -m voicebench.cli run \
  --suite scenarios/live/elevenlabs/SCEN-ELEVEN-SMOKE-01.yaml \
  --target elevenlabs \
  --conversation-id "conv_0501m5fpgz7jfnpbnwwn75nxwe7k" \
  --output results/elevenlabs_run.json
```

---

## 2. Regression Differencing: `voicebench diff`

VoiceBench provides first-class regression tracking between consecutive model versions or prompt updates. It compares baseline and candidate runs, classifies transitions, and flags regressions:

```bash
python -m voicebench.cli diff results/baseline.json results/candidate_bargein.json \
  --output results/diff.json \
  --report results/diff_report.md
```

### Example Console Output:
```text
========================================================
VoiceBench Regression Comparison
========================================================
Baseline:  run-dc4020ab (local_mock / baseline / virtual_simulation)
Candidate: run-edf3ccc3 (local_mock / defect_bargein_lag / virtual_simulation)
Timing Modes: COMPATIBLE

Summary:
  Compared:    6
  Regressions: 1 (PASS -> FAIL)
  Fixes:       0 (FAIL -> PASS)
  Unchanged:   5 pass, 0 fail
  Shifts:      0 inconclusive
--------------------------------------------------------
[UNCHANGED_PASS]     SCEN-COORD-06   PASS -> PASS
[REGRESSION]         SCEN-INT-02     PASS -> FAIL
  [!] Regression Reason: Baseline: All deterministic assertions satisfied with verifiable evidence. -> Candidate: Interruption yield latency threshold exceeded: measured 1450.0ms, maximum allowed is 750.0ms.
  Category: FAIL-INT
     Metric yield_latency_ms: 320.0 -> 1450.0 (delta: +1130.0)
[UNCHANGED_PASS]     SCEN-REC-04     PASS -> PASS
[UNCHANGED_PASS]     SCEN-ROLE-03    PASS -> PASS
[UNCHANGED_PASS]     SCEN-ROUT-01    PASS -> PASS
[UNCHANGED_PASS]     SCEN-TOOL-05    PASS -> PASS

Machine-readable diff written to: results/diff.json
Markdown differential report written to: results/diff_report.md
```

The CLI exits with code `1` if any regression is detected, integrating directly into CI/CD quality gates.

---

## 3. Generic HTTP Adapter & Integration Protocol

Peer teams building real-time multi-agent systems can integrate with VoiceBench using standard HTTP JSON endpoints—without requiring custom SDK installations or code alterations to VoiceBench.

- **Request Schema:** Sends full scenario context (`scenario_id`, `preconditions`, `active_agents`, and conversational `turns`).
- **Response Schema:** Receives JSON event log containing `events` (`TRANSCRIPT_FINAL`, `SPEECH_CHUNK`, `INTERRUPT_ACK`, `TOOL_CALL`).
- **Measurement Integrity:**
  - Client observed latency is tracked monotonically and labeled `event_provenance="client_observed"`.
  - Target internal latency is preserved separately in `source_timestamp_ms` (`event_provenance="target_reported"`).
  - Incompatible timing modes (`virtual_simulation`, `observed_wall_clock`, `target_reported_call_relative`) are rejected from metric deltas in the regression comparator to prevent dishonest comparisons.
- **Resilience:** Unreachable ports, connection timeouts, HTTP 500, and malformed JSON are cleanly caught as `TRANSPORT_ERROR` events and evaluated as `INCONCLUSIVE` (`FAIL_INFRA`).

See [docs/ADAPTER_GUIDE.md](docs/ADAPTER_GUIDE.md) for full endpoint specifications, payload schemas, and integration checklists.

---

## 4. Supported Scenario Assertions & ElevenLabs Integration

VoiceBench includes a dedicated adapter (`ElevenLabsConvAIAdapter`) connecting directly to official ElevenLabs Conversational AI REST APIs (`/v1/convai/agents/{agent_id}` and `/v1/convai/conversations/{conversation_id}`).

### 4.1 Supported Scenario Assertions
VoiceBench evaluates conversational evidence through deterministic, reproducible rules—without relying on non-deterministic LLM judging:
- **Persona & Stance Consistency:** Verifies primary speaker identity (`primary_speaker_id`), forbids out-of-character participants (`disallowed_speakers`), and enforces boundary constraints (`forbidden_keywords`).
- **Keyword & Numeric Grounding:** Checks presence of required terminology (`required_keywords`) and validates exact numerical values (`expected_numeric_grounding`).
- **Tool-Call Correctness:** Validates tool invocation names (`required_tool_call`) and exact argument dictionaries (`expected_tool_args`).
- **Structured Tool-Result Assertions:** Checks field values (`expected_tool_result`), enforces mandatory field presence (`required_tool_result_fields`), and validates non-emptiness (`require_nonempty_tool_result`).
- **Nested Path Verification:** Deterministically checks nested objects and arrays using dot-separated and indexed paths (`expected_tool_result_paths`, `required_tool_result_paths`, e.g. `metadata.status`, `documents[0].id`) with strict type equality (no bool/int or str/int coercion) and explicit null distinction.
- **Scenario Alignment Verification:** Automatically verifies that required user prompts appear in the transcript in proper order before evaluating agent responses.
- **Correlation Integrity:** Correlates tool calls and tool results strictly by request ID or turn index, returning `INCONCLUSIVE` (`TOOL_RESULT_AMBIGUOUS`) rather than silently guessing among ambiguous results.

### 4.2 Transparent Measurement Disclosures & Honest Boundaries
To maintain rigorous scientific integrity, VoiceBench explicitly partitions timing and provenance:
- **Virtual Simulation (`timing_mode="virtual_simulation"`):** Used for local offline mocks with zero conflation with real wall-clock latency.
- **Provider-Reported Transcript Offsets (`timing_mode="target_reported_call_relative"`, `event_provenance="target_reported"`):** Call-relative offsets reported by ElevenLabs (`time_in_call_secs`). VoiceBench **never** presents these as client-observed wall-clock latency.
- **Client-Observed REST Latency (`event_provenance="client_observed"`):** Monotonically measured fetch duration of REST payloads (`fetch_latency_ms`).
- **Honest Limitations:**
  - *No Fabricated Audio Interruption Measurements:* REST conversation details provide call-relative message timestamps, not sub-second acoustic cancellation timestamps. Claiming audio barge-in yield measurement without live bidirectional audio WebSocket instrumentation would be dishonest; VoiceBench explicitly rejects this scenario with `UnsupportedCapabilityError` (`INCONCLUSIVE`).
  - *Single-Agent vs. Multi-Agent Scope:* ElevenLabs ConvAI sessions are single-agent. Genuine multi-agent floor arbitration across multiple live agents is not claimed.
  - *Provider-Native Testing API Scope:* Native ElevenLabs Agent Testing API endpoints (`/v1/convai/agent-testing`) are maintained in modular dry-run preview mode and are not claimed as verified for this submission.

### 4.3 Configuration:
```bash
# Set environment variables or place in .env:
ELEVENLABS_API_KEY="sk_..."
ELEVENLABS_AGENT_ID="agent_..."
ELEVENLABS_CONVERSATION_ID="conv_..."
```

> **Evaluation Workflow:** To evaluate a live ElevenLabs conversation, specify a real completed conversation ID using `--conversation-id` or `$env:ELEVENLABS_CONVERSATION_ID`. VoiceBench queries `GET /v1/convai/conversations/{conversation_id}` strictly in read-only mode — it never initiates outbound calls or creates billable resources. If no conversation ID is provided, VoiceBench reports a transparent `INCONCLUSIVE` configuration error rather than sending fabricated identifiers. Accidental reuse of a single conversation across a multi-scenario suite is strictly prevented.

### 4.4 Official ElevenLabs Python SDK Client (`voicebench.elevenlabs_client`)
VoiceBench includes an official ElevenLabs SDK client wrapper (`voicebench/elevenlabs_client.py`). It securely loads credentials from `.env` using `python-dotenv` without hardcoding keys:
```python
from voicebench.elevenlabs_client import client

# Safe, read-only authentication check (does not consume generation credits):
user = client.user.get()
print("Authenticated user tier:", user.subscription.tier)
```

---

## 5. Current Scenarios

VoiceBench provides six canonical roundtable scenarios targeting core multi-agent conversational challenges, plus an aligned live smoke scenario:

### Canonical Roundtable Suite (`scenarios/roundtable/`)
| Scenario ID | Name | Failure Category | Target Behavior Tested |
| :--- | :--- | :--- | :--- |
| `SCEN-ROUT-01` | **Targeted Expert Addressed-Routing** | `FAIL-ROUT` | Addressing Dr. Aris causes only Dr. Aris to respond. |
| `SCEN-INT-02` | **Human Barge-In & Yield Latency** | `FAIL-INT` | Speaker halts speech $\le 750\text{ ms}$ upon human interruption. |
| `SCEN-ROLE-03` | **Persona & Stance Consistency** | `FAIL-ROLE` | Dr. Aris rejects ungrounded financial speculation under adversarial prompt. |
| `SCEN-REC-04` | **Post-Interruption Topic Recovery** | `FAIL-REC` | Panel resumes technical geothermal debate after procedural interruption. |
| `SCEN-TOOL-05` | **Tool-Call Correctness & Grounding**| `FAIL-TOOL` | Agent calls `fetch_market_price` with `ERCOT` and speaks `$34.50`. |
| `SCEN-COORD-06`| **Multi-Speaker Floor Arbitration** | `FAIL-COORD` | Panelists alternate speech without overlap ($>200\text{ ms}$) or dead air. |

### Aligned Live Smoke Scenario (`scenarios/live/elevenlabs/`)
| Scenario ID | Name | Failure Category | Target Behavior Tested |
| :--- | :--- | :--- | :--- |
| `SCEN-ELEVEN-SMOKE-01` | **ElevenLabs Live Smoke Test** | `FAIL-TOOL` | Validates live agent knowledge base query on conversation ID retrieval with clean handling of empty result states. |

---

## 6. Automated Test Suite

VoiceBench is backed by a comprehensive automated test suite verifying scenario validation, synthetic traces, mock target adapters, Generic HTTP targets, ElevenLabs integration, structured tool-result assertions, nested paths, correlation integrity, error classification, timing integrity, and regression diffing:

```bash
python -m pytest tests/ -v
```

**Results:** `131 passed in ~12.4s` (100% pass rate, 0 warnings).

---

## 7. Documentation Suite

- **[DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md):** 3-minute reproducible judging demonstration script.
- **[SUBMISSION_STATUS.md](docs/SUBMISSION_STATUS.md):** Submission readiness audit, verified capabilities, and honesty disclosures.
- **[PRESENTATION.md](docs/PRESENTATION.md):** Technical presentation and architecture walkthrough.
- **[ADAPTER_GUIDE.md](docs/ADAPTER_GUIDE.md):** Target adapter contract, HTTP JSON schemas, ElevenLabs adapter guide, timing rules, and reference server.
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md):** System architecture, interfaces, and diff specifications.
- **[PRIOR_ART_REVIEW.md](docs/PRIOR_ART_REVIEW.md):** Research sources, prior-art audit, and gap analysis.
- **[EVALUATION_PLAN.md](docs/EVALUATION_PLAN.md):** Detailed scenario specifications and failure taxonomies.

---

## 8. Clean-Room Implementation & Originality Standard

- All code and documentation written from scratch.
- Zero code copied, forked, or vendored from external projects.
- Authorship accurately reflects true local developers.
- Repository published and verified on GitHub under the MIT License.
