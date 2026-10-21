# VoiceBench (Roundtable VoiceBench)

An open-source evaluation framework for real-time multi-agent voice conversations, testing interruption handling, expert roles, conversation recovery, and behavioral regressions.

> **Status:** 🚀 **Milestone 3 Implemented (ElevenLabs Integration & Safe Publishing Readiness)**  
> *VoiceBench has implemented a dedicated ElevenLabs Conversational AI adapter (`ElevenLabsConvAIAdapter`), rigorous credential redaction, official REST endpoint integration, deterministic offline mock test suite, transparent capability disclosures, and safe GitHub publishing preparation.*

VoiceBench is an independent, open-source evaluation and regression-testing framework designed specifically for real-time multi-agent voice systems. 

Developed for **FOSS FORGE 2.0 (Build with ElevenLabs)**, VoiceBench targets **Problem 1: AI Roundtable**, providing developers with an automated, reproducible way to test whether multi-agent podcasts and panel discussions maintain conversational floor control, handle human barge-in interruptions, preserve expert personas, and recover context after detours.

---

## 1. Quickstart: Running VoiceBench in 2 Minutes

### 1.1 Prerequisites & Installation
VoiceBench is engineered to run completely offline with minimal dependencies:
- Python 3.10+ (tested on Python 3.13)
- Runtime dependencies: `pyyaml` (for scenario parsing)
- Developer dependencies: `pytest` (for automated test suite)

```bash
# Clone or navigate to the voicebench repository
cd voicebench

# Install required dependencies
pip install pyyaml pytest
```

### 1.2 Inspect & Validate Scenarios
```bash
# 1. List all available scenarios
python -m voicebench.cli scenarios list

# 2. Validate all scenario YAML files against the VoiceBench schema
python -m voicebench.cli scenarios validate scenarios/roundtable
```

### 1.3 Run Scenarios Against Target Adapters
```bash
# 3. Run the complete 6-scenario suite against the compliant Baseline mock target
python -m voicebench.cli run \
  --suite scenarios/roundtable \
  --target mock \
  --target-config baseline \
  --output results/baseline.json

# 4. Run against a candidate configuration with an introduced defect
python -m voicebench.cli run \
  --suite scenarios/roundtable \
  --target mock \
  --target-config defect_bargein_lag \
  --output results/candidate_bargein.json

# 5. Run scenarios against an external roundtable service via Generic HTTP Adapter
python -m voicebench.cli run \
  --suite scenarios/roundtable \
  --target http \
  --endpoint-url http://localhost:8080/api/v1/roundtable/execute \
  --output results/http_run.json

# 6. Run scenario against ElevenLabs Conversational AI
python -m voicebench.cli run \
  --suite scenarios/roundtable/SCEN-ROLE-03.yaml \
  --target elevenlabs \
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

## 4. ElevenLabs Conversational AI Integration & Honest Capability Disclosures

VoiceBench includes a dedicated adapter (`ElevenLabsConvAIAdapter`) connecting directly to official ElevenLabs Conversational AI REST APIs (`/v1/convai/agents/{agent_id}` and `/v1/convai/conversations/{conversation_id}`).

### Capability Support & Honest Boundaries:
- **Supported:**
  - Expert persona and boundary verification (`SCEN-ROLE-03`)
  - Tool invocation schema validation, execution error checking, and fact grounding (`SCEN-TOOL-05`)
  - Post-detour debate topic recovery (`SCEN-REC-04`)
- **Timing Mode & Provenance (`target_reported_call_relative`):**
  - Transcript turns carry target-reported, call-relative timing (`time_in_call_secs`), never conflated with client-observed wall-clock latency.
  - Client REST fetch latency (`fetch_latency_ms`) is tracked separately via monotonic clock and tagged `client_observed`.
- **Explicitly Rejected with Inconclusive Verdict:**
  - Sub-second audio energy barge-in yield measurement (`SCEN-INT-02`): REST conversation details provide call-relative message timestamps, not sub-second audio energy cancellation timestamps. Claiming barge-in yield measurement without live bidirectional audio WebSocket instrumentation would be dishonest.
  - Multi-speaker floor collision measurement (`SCEN-COORD-06`): ElevenLabs ConvAI sessions are natively single-agent.

### Configuration on Windows PowerShell:
```powershell
$env:ELEVENLABS_API_KEY = "sk_..."
$env:ELEVENLABS_AGENT_ID = "agent_..."
```

---

## 5. Current Test Scenarios

The suite implements six core scenarios targeting six distinct multi-agent failure categories:

| Scenario ID | Name | Failure Category | Target Behavior Tested |
| :--- | :--- | :--- | :--- |
| `SCEN-ROUT-01` | **Targeted Expert Addressed-Routing** | `FAIL-ROUT` | Addressing Dr. Aris causes only Dr. Aris to respond. |
| `SCEN-INT-02` | **Human Barge-In & Yield Latency** | `FAIL-INT` | Speaker halts speech $\le 750\text{ ms}$ upon human interruption. |
| `SCEN-ROLE-03` | **Persona & Stance Consistency** | `FAIL-ROLE` | Dr. Aris rejects ungrounded financial speculation under adversarial prompt. |
| `SCEN-REC-04` | **Post-Interruption Topic Recovery** | `FAIL-REC` | Panel resumes technical geothermal debate after procedural interruption. |
| `SCEN-TOOL-05` | **Tool-Call Correctness & Grounding**| `FAIL-TOOL` | Agent calls `fetch_market_price` with `ERCOT` and speaks `$34.50`. |
| `SCEN-COORD-06`| **Multi-Speaker Floor Arbitration** | `FAIL-COORD` | Panelists alternate speech without overlap ($>200\text{ ms}$) or dead air. |

---

## 6. Automated Test Suite

VoiceBench is backed by a comprehensive automated test suite verifying scenario validation, synthetic traces, mock target adapters, HTTP targets, ElevenLabs integration, error classification, timing integrity, and regression diffing:

```bash
python -m pytest tests/ -v
```

**Results:** `63 passed in ~10.7s` (100% pass rate, 0 warnings).

---

## 7. Documentation Suite

- **[PRIOR_ART_REVIEW.md](docs/PRIOR_ART_REVIEW.md):** Research sources, prior-art audit, and gap analysis.
- **[PROJECT_DECISION.md](docs/PROJECT_DECISION.md):** Track scope selection (Problem 1 AI Roundtable) and judging criteria alignment.
- **[EVALUATION_PLAN.md](docs/EVALUATION_PLAN.md):** Detailed scenario specifications and failure taxonomies.
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md):** System architecture, interfaces, and diff specifications.
- **[ADAPTER_GUIDE.md](docs/ADAPTER_GUIDE.md):** Target adapter contract, HTTP JSON schemas, ElevenLabs adapter guide, timing rules, and reference server.
- **[ELEVENLABS_INTEGRATION_DECISION.md](docs/ELEVENLABS_INTEGRATION_DECISION.md):** Official API references, integration scope, and capability decisions.
- **[MILESTONE_PLAN.md](docs/MILESTONE_PLAN.md):** Implementation roadmap for the hackathon.
- **[MILESTONE_1_VERIFICATION.md](docs/MILESTONE_1_VERIFICATION.md):** Milestone 1 verification gate audit.
- **[MILESTONE_2_VERIFICATION.md](docs/MILESTONE_2_VERIFICATION.md):** Milestone 2 verification gate audit.
- **[MILESTONE_3_VERIFICATION.md](docs/MILESTONE_3_VERIFICATION.md):** Milestone 3 verification gate audit and safe publishing readiness.

---

## 8. Clean-Room Implementation & Originality Standard

- All code and documentation written from scratch.
- Zero code copied, forked, or vendored from external projects.
- Authorship accurately reflects true local developers.
- Work is local and unpublished; no remote Git repository has been pushed.
