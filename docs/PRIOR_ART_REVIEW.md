# VoiceBench — Prior Art Review & Originality Audit

**Author / Maintainer:** VoiceBench Core Team  
**Date of Audit:** October 21, 2026  
**Status:** Approved for Milestone 0 Planning  
**Compliance Standard:** Clean-Room Original Implementation (FOSS FORGE 2.0 / ElevenLabs Track)

---

## 1. Executive Summary & Audit Scope

This document provides a comprehensive prior-art review and originality audit for **VoiceBench**, an independent open-source evaluation framework for voice AI systems. VoiceBench is specifically designed to evaluate **Problem 1 (AI Roundtable: real-time multi-agent podcast where AI experts debate a topic and a human can interrupt and talk to individual agents)** within the ElevenLabs AI Roundtable track of FOSS FORGE 2.0.

In accordance with strict clean-room software engineering standards:
1. No source code, prompt templates, test scenarios, dataset files, or UI layouts were copied, forked, imported, or vendored from any researched repository.
2. All target architectures, scenario specifications, evaluation logic, metric definitions, and adapters documented herein are original designs developed from first principles.
3. Git commit history and author attribution will reflect solely genuine project contributors, with no external developer identities falsely claimed or imported.

---

## 2. Research Sources & Prior Art Audit

Each external project and reference cited in the track brief was investigated on October 21, 2026. The findings below explicitly contrast verified implementations against claims, document license constraints, and establish clear boundaries of differentiation.

| Project / Source | URL / Reference | License | Verified Implementation Details | Limitations & Unmet Needs | Overlap with VoiceBench |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **rand0wn/voice-eval** | `https://github.com/rand0wn/voice-eval` | Apache-2.0 | Multi-turn regression testing for single voice agents; tool-call validation; content grading; latency gates in CI/CD; per-turn audio artifact capture. | Strictly single-agent focus; assumes a 1-on-1 user-to-agent session. Does not support multi-agent floor arbitration, speaker-directed routing, cross-agent debate continuity, or post-interruption topic restoration. | Multi-turn turn simulation, latency measurements, and JSON results output. VoiceBench differs by handling multi-speaker arbitration, speaker attribution, and conversation recovery. |
| **ServiceNow/eva (EVA-Bench)** | `https://github.com/ServiceNow/eva` | MIT | Academic/enterprise benchmark suite for voice agents using bot-to-bot dialogue simulation in ITSM, CSM, and HR. Composite metrics: EVA-A (Accuracy) and EVA-X (Experience). | Heavyweight enterprise setup with massive dependencies; specialized in 1-on-1 transactional customer support; lacks lightweight local CLI execution; zero support for multi-expert roundtables, dynamic barge-in floor handoffs, or addressed-agent routing. | Bot-to-bot conversational simulation concept. VoiceBench differs by being lightweight, runnable offline without enterprise infra, and focused on multi-agent podcast dynamics. |
| **agentkitai/agenteval** | `https://github.com/agentkitai/agenteval` | MIT | YAML-based test runner for LLM agents; tool-call trajectory inspection; regex and JSON-schema assertion engine; LLM-as-a-judge scorers; CI/CD integration. | Purely text/API based. Has no concept of audio streaming, speech latency (time-to-first-audio), barge-in interruption timing, dead air, or voice agent coordination. | YAML scenario format and trajectory assertion philosophy. VoiceBench adopts structured YAML scenarios but adapts them to audio timestamps, speaker turns, and multi-agent coordination. |
| **saharmor/voice-lab** | `https://github.com/saharmor/voice-lab` | Apache-2.0 | Prompt testing harness for LLM voice backends; model migration benchmarking (e.g. OpenAI vs Anthropic); LLM-as-a-judge scoring on simulated user prompts. | Operates solely on the text-prompt layer; heavy reliance on subjective LLM judging without deterministic event assertions; no voice pipeline capture, no multi-agent podcast testing. | Benchmarking two configurations/models. VoiceBench differs by enforcing deterministic ground-truth checks over subjective LLM scoring and handling full multi-agent voice streaming. |
| **Bro-In-Finance/voice-agent-eval** | `https://github.com/Bro-In-Finance/voice-agent-eval` | Inaccessible / Unverified | Repository is either private, deleted, or a typo in the initial reference prompt. Not accessible as of October 21, 2026. | Cannot be reviewed or executed. | None. Verified as inaccessible. |
| **PranaySadavarte/voice-agent-qa** | `https://github.com/PranaySadavarte/voice-agent-qa` | Inaccessible / Unverified | Repository is either private, deleted, or a typo in the initial reference prompt (contrasts with unrelated general QA repos such as `superU-ai/voice-agent-QA`). Not accessible as of October 21, 2026. | Cannot be reviewed or executed. | None. Verified as inaccessible. |
| **fragres/agent-eval-suite** | `https://github.com/fragres/agent-eval-suite` | Inaccessible / Unverified | Repository is either private, deleted, or a typo in the initial reference prompt. Not accessible as of October 21, 2026. | Cannot be reviewed or executed. | None. Verified as inaccessible. |
| **ElevenLabs Agent Testing API** | `POST /v1/convai/agents/{agent_id}/run-tests` & Developer Docs | Proprietary Cloud Service | Cloud-hosted testing API for ElevenLabs Conversational AI agents; executes tool-call tests, knowledge retrieval verification, and prompt overrides; returns status buckets. | Single-agent architecture only; cloud-only (requires paid account and active network); cannot evaluate multi-agent roundtables; cannot detect speaker confusion or multi-party barge-in collisions; black-box evaluation. | VoiceBench provides an adapter to test ElevenLabs agents, but adds multi-agent coordination, local offline testing, deterministic trace assertions, and regression diffing. |

---

## 3. Detailed Technical Analysis of Prior Art Gaps

### 3.1 The "Single-Agent Paradigm" in Surveyed Voice Evaluation Tools
While multi-agent conversational benchmarks exist in text-only NLP research (e.g., multi-agent debate, consensus dynamics, and text role-playing harnesses), every voice-specific evaluation framework surveyed in this audit (`rand0wn/voice-eval`, `ServiceNow/eva`, `saharmor/voice-lab`, and ElevenLabs native test endpoints) is architected around a **1-on-1 client-to-single-agent paradigm**: one human caller interacts with exactly one voice agent.

In contrast, **AI Roundtable (Problem 1)** introduces real-time audio multi-party dynamics:
- Multiple AI agents sharing an audio channel / conversational space.
- Dynamic conversational floor handoff between agents.
- Human user barge-in that can either address the entire panel or target a specific individual expert.

None of the existing tools provide primitives to measure:
1. **Addressed-Agent Routing:** When a user states *"Dr. Aris, do you agree with Elena?"*, does Dr. Aris respond, or does Elena continue speaking?
2. **Interruption Floor Yield:** When a human interrupts, do *all* agents immediately fall silent, or does one agent yield while another continues rambling?
3. **Role & Voice Consistency:** Over a 10-turn debate, do agents preserve their distinct viewpoints and assigned voices without voice bleed or personality drift?
4. **Debate Thread Restoration:** Once a user detour is completed, does the panel moderator or speaking agent acknowledge and resume the original discussion topic, or is context permanently lost?

### 3.2 The Subjective "LLM-as-a-Judge" Pitfall
Tools like `voice-lab` rely almost exclusively on qualitative LLM-as-a-judge scoring. In a competitive benchmark or production CI/CD pipeline, LLM judging suffers from:
- **Non-determinism:** Scores vary across repeated evaluations of identical transcripts.
- **Explainability gaps:** A qualitative score (e.g. `7/10`) does not pinpoint which exact millisecond or turn triggered a failure.
- **Cost and latency:** Slows down CI pipelines and requires external API keys.

**VoiceBench Position:** Deterministic assertions (speaker identity matching, regex/keyword grounding, tool-call schema validation, latency thresholds, barge-in timing bounds) must serve as the primary pass/fail ground truth. AI-assisted judges are reserved strictly for nuanced semantic checks (e.g. tone or qualitative topic alignment) and must cite specific turn indices and quote verbatim evidence to support any conclusion.

### 3.3 The Offline & Credential Barrier
ElevenLabs native testing and many cloud-based tools require active API keys, paid credits, and internet access. If network hiccups occur or rate limits are hit, test runs fail ambiguously.
**VoiceBench Position:** Core testing must execute locally without credentials using mock/fixture target adapters, allowing developers to run full regression test suites in air-gapped CI environments or during local development for zero cost.

---

## 4. Feature-Overlap & Originality Matrix

| Capability | rand0wn/voice-eval | ServiceNow EVA | saharmor/voice-lab | ElevenLabs Native Tests | **VoiceBench (Proposed)** |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Multi-Turn Scripting** | Yes (YAML) | Yes (Bot-to-Bot) | Yes (JSON) | Yes (Dashboard/API) | **Yes (Original YAML schema)** |
| **Multi-Agent Roundtable Support** | No | No | No | No | **Yes (Native multi-speaker tracking)** |
| **Addressed-Agent Routing Check** | No | No | No | No | **Yes (Deterministic speaker matching)** |
| **Barge-In / Interruption Latency** | Partial | Partial | No | No | **Yes (Timestamped yield delta)** |
| **Post-Interruption Topic Recovery** | No | No | No | No | **Yes (Context return assertion)** |
| **Turn-Level Root Cause Attribution** | Partial | No (Aggregate) | No | Partial (Buckets) | **Yes (Turn/Speaker/Tool failure linking)** |
| **Regression Diffing (Run A vs Run B)**| Yes | No | No | Partial | **Yes (Categorical regression analysis)** |
| **Zero-Credential Local Mocking** | No | No | No | No | **Yes (Fixture-based adapter included)** |
| **Open-Source & Extensible Adapters** | Apache-2.0 | MIT | Apache-2.0 | Proprietary | **Yes (Clean-room MIT/Apache-2.0)** |

---

## 5. Clean-Room Implementation & Legal Hygiene

To ensure total legal and ethical integrity:
1. **No Code Provenance Infiltration:** No lines of code, test cases, or schemas will be copied from `rand0wn/voice-eval`, `ServiceNow/eva`, `agentkitai/agenteval`, or `saharmor/voice-lab`.
2. **Distinct Architectural Design:** VoiceBench's engine is designed specifically around an event-driven `ConversationTrace` model tracking simultaneous participant streams, unlike single-agent request/response loops.
3. **Third-Party Dependency Discipline:** Any third-party Python library adopted in future milestones (e.g. `pydantic`, `pytest`, `pyyaml`, `rich`) will be standard, permissive open-source packages (MIT/Apache-2.0/BSD) and will be vetted for license compatibility.
4. **No Plagiarism of Documentation:** All documentation, diagrams, scenario specifications, and failure taxonomies in VoiceBench are original creative works.
