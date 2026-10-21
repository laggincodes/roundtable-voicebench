# VoiceBench — Project Decision & Architecture Direction

**Document ID:** DECISION-2026-001  
**Target Track:** FOSS FORGE 2.0 · Build with ElevenLabs (Roundtable Track)  
**Target Problem Scope:** Problem 1 — AI Roundtable (Real-time Multi-Agent Podcast Evaluation)  
**Date:** October 21, 2026  
**Status:** Approved for Milestone 0 Planning  

---

## 1. Executive Summary & Recommended Direction

### 1.1 Scope Selection: Problem 1 (AI Roundtable)
The FOSS FORGE 2.0 participant brief allows selecting either **Problem 1 (AI Roundtable: real-time multi-agent podcast where AI experts debate a topic and a human can interrupt and talk to individual agents)** or **Problem 3 (Multimodal RAG Voice Assistant)**.

We decisively choose **Problem 1: AI Roundtable**.

**Rationale:**
1. **Unaddressed Gap in the Ecosystem:** As proven in `PRIOR_ART_REVIEW.md`, existing voice evaluation tools (such as `voice-eval`, `eva`, and `voice-lab`) exclusively test single-agent 1-on-1 conversations. There is currently no open-source testing framework tailored for multi-agent conversational floor arbitration, addressed-speaker routing, cross-agent debate continuity, or multi-party interruption recovery.
2. **Alignment with Track Theme:** The track is hosted under the ElevenLabs "AI Roundtable" track. Delivering an evaluation framework specifically engineered to test and validate AI Roundtables directly enhances the track's primary ecosystem goals.
3. **High Hackathon Impact & Feasibility:** Multi-agent podcasts suffer from noticeable and reproducible failure modes (agents talking over each other, wrong experts answering, context amnesia after barge-in). These failure modes can be deterministically captured, asserted, and benchmarked without requiring proprietary GPU clusters or enterprise RAG document stores.

---

## 2. Alignment with Judging Criteria

The hackathon scoring matrix awards points across six explicit dimensions. VoiceBench is architected to maximize points in every weighted area:

| Criterion | Weight | How VoiceBench Maximizes the Score |
| :--- | :---: | :--- |
| **Framework Design & Extensibility** | **25%** | • Decoupled core evaluation engine with modular `TargetAdapter` interface.<br>• Human-readable YAML scenario format supporting multi-agent turn definitions, barge-in timings, and assertions.<br>• Pluggable target adapters: Generic HTTP/WebSocket, ElevenLabs ConvAI, and a zero-dependency Local Mock target. |
| **Accuracy & Explainability of Evaluations** | **20%** | • Priority on **deterministic assertions** over flaky LLM judges.<br>• Every evaluation outcome (Pass, Fail, Inconclusive) is backed by concrete evidence: exact turn index, speaker ID, timestamp, and verbatim transcript/tool-call quote.<br>• Explicit `Inconclusive` state prevents false passes/fails on network hiccups or timeouts. |
| **Documentation** | **20%** | • Complete developer documentation: 10-minute Quickstart, Scenario Specification Reference, Metric Formulas, Adapter Implementation Guide, and Limitations Disclosure.<br>• No vague claims; all metrics and edge cases mathematically and behaviorally defined. |
| **Failure Analysis & Regression Comparison** | **15%** | • Turn-level failure attribution categorized into 6 distinct failure classes.<br>• Explicit separation between *Agent Failures* and *Test/Infrastructure Failures*.<br>• Built-in `voicebench diff` regression engine comparing Run A vs Run B (status flips, latency deltas, newly introduced failures). |
| **Results on Other Teams or Demo Agent** | **15%** | • Dual-path execution strategy: ready to test peer teams' HTTP/WebSocket endpoints without code modifications.<br>• Built-in controlled Reference Demo Agent featuring a healthy Baseline and a Deliberately Defective version to prove the framework catches real regressions out-of-the-box. |
| **Reproducibility & Open-Source Readiness** | **5%** | • 100% reproducible offline runs with zero required API keys using the Local Mock Adapter.<br>• Permissive open-source licensing (MIT/Apache-2.0), automated test suite for the evaluation engine itself, and clean CLI ergonomics. |

---

## 3. Problem Statement & Target Users

### 3.1 The Problem
Building real-time multi-agent podcasts with voice AI is inherently chaotic. When multiple autonomous AI experts debate a topic and a human listener jumps in:
- **Floor Collisions:** Multiple agents start speaking at the same time or cause awkward dead air.
- **Routing Confusion:** A user says *"Marcus, what is the counterargument?"*, but Elena answers instead.
- **Barge-in Failures:** The user interrupts to correct a fact, but the speaking agent ignores the barge-in and completes a 30-second monologue.
- **Debate Amnesia:** After the human asks a 1-turn side question, the podcast agents forget what they were debating and default to generic greetings.

Developers currently test these systems by manually listening to dozens of minutes of audio, which is unscalable, subjective, and prone to undetected regressions during code updates.

### 3.2 Target Users
1. **Roundtable / Podcast AI Developers:** Engineers designing multi-agent panel systems who need automated CI/CD regression gates before deploying new prompts or voice models.
2. **Hackathon Participants / Evaluators:** Peer teams in the ElevenLabs track who want automated reliability reports to prove their podcast agents work as advertised.
3. **Voice AI Researchers:** Teams benchmarking turn-taking algorithms, conversational floor arbitration, and multi-agent persona consistency.

---

## 4. Minimum Viable Product (MVP) Scope

### 4.1 In Scope for VoiceBench MVP
1. **Scenario Specification Suite:** YAML-based scenario format supporting multi-agent turn simulation, targeted user queries, timed barge-in interrupts, and deterministic expectations.
2. **Execution Engine:** Asynchronous turn runner that orchestrates simulated conversations, handles timeouts, and captures structured traces (`ConversationTrace`).
3. **Modular Adapter Layer:**
   - `LocalMockAdapter`: Replays scripted/simulated multi-agent audio-transcript streams with toggleable defects (100% offline, credential-free).
   - `GenericHttpAdapter`: Connects to external team podcast servers via REST / JSON stream.
   - `ElevenLabsAdapter`: Interfaces with ElevenLabs conversational endpoints.
4. **Deterministic Assertion Engine:** Evaluates:
   - Addressed-agent speaker ID correctness.
   - Interruption yield latency (time from barge-in trigger to agent silence).
   - Critical fact presence / keyword avoidance.
   - Tool-call execution and parameter schemas.
   - Conversation topic recovery keywords in post-interruption turns.
5. **Failure Analysis & Categorization:** Automatically classifies failures into standardized buckets with exact turn and speaker attribution.
6. **Regression Comparison (`voicebench diff`):** Diffs two test run JSON artifacts, reporting test outcome changes, latency shifts, and new failure modes.
7. **Reporting:** Outputs human-readable Markdown/HTML reports and standardized JSON artifacts.
8. **Controlled Reference Targets:** Bundles both a healthy baseline podcast agent and a defective variant to guarantee reproducibility during demonstrations.

### 4.2 Explicit Exclusions (Out of Scope for Hackathon)
- **Heavyweight Video / Computer Vision:** Focused purely on audio/speech transcripts and multi-agent conversational mechanics.
- **Custom TTS Engine Training:** VoiceBench is an evaluation tool, not a voice synthesizer. It relies on standard voice outputs.
- **Complex Full-Stack Dashboard / Web Server:** In compliance with hackathon rules, engineering resources are focused on robust evaluation, deterministic assertions, CLI ergonomics, and reproducible evidence rather than a cosmetic web dashboard.

---

## 5. Technical Risks & Mitigation Strategies

| Risk | Severity | Probability | Concrete Mitigation Strategy |
| :--- | :---: | :---: | :--- |
| **1. Peer teams are uncooperative or unavailable to test** | Medium | Medium | Provide a fully functional, self-contained reference demo agent featuring both a **Baseline** and a **Defective** version. Judges can evaluate the framework completely independently without external dependencies. |
| **2. Flaky network or rate limits during live evaluation** | High | Medium | Implement strict timeout handling and the explicit `Inconclusive` test status. Network failures will be classified as `InfrastructureFailure`, never as false agent passes or fails. |
| **3. Audio stream timing inaccuracies in simulated tests** | Medium | Low | Capture timestamps at both the packet/turn injection point and response receipt point. Support simulated millisecond timestamps in the `LocalMockAdapter` for 100% reproducible timing assertions in CI. |
| **4. External LLM judge unreliability or cost** | High | Low | Prioritize deterministic assertions for 90%+ of all checks. If an AI judge is invoked for semantic evaluation, it must use an open-weight model or local deterministic heuristics, citing verbatim quotes and turn numbers. |
| **5. Scope creep during hackathon crunch** | High | Medium | Enforce strict phase gates (Milestone 0 -> Milestone 1 -> Milestone 2). Application code will only be written once architectural contracts and test specifications are approved. |
