# VoiceBench — Final Publication Safety Audit

**Audit Date:** 2026-10-21
**Auditor:** Senior Open-Source Hackathon Lead
**Working Directory:** `C:\Users\Yatha\.gemini\antigravity\scratch\voicebench`
**GitHub Repository:** `https://github.com/laggincodes/roundtable-voicebench.git`
**Candidate Branch:** `reconcile/github-main`
**Candidate HEAD Commit:** `11106e23652b9485f30ae46b2f9150ffedd4f76d`
**Remote main SHA:** `cba7f21b1acf00d180b8d191ac5692d8bc438334`
**Document Status:** Uncommitted working audit document
**Publication Status:** **GATED — NO PUSH EXECUTED — AWAITING USER APPROVAL**

---

## 1. Repository State Confirmation

- **Active Branch:** `reconcile/github-main` (checked via `git branch --show-current`)
- **HEAD Commit SHA:** `11106e23652b9485f30ae46b2f9150ffedd4f76d` (checked via `git rev-parse HEAD`)
- **Remote `origin/main` SHA:** `cba7f21b1acf00d180b8d191ac5692d8bc438334` (checked via `git rev-parse origin/main`)
- **Remote URL:** `origin https://github.com/laggincodes/roundtable-voicebench.git` (checked via `git remote -v`)
- **Untracked Documents:**
  - `docs/GITHUB_PUBLISHING_READINESS.md` (audit & reconciliation plan)
  - `docs/PUBLICATION_AUDIT.md` (this audit document)
- **Staged / Modified Working Tree:** Clean (zero unstaged code modifications).

---

## 2. Public Repository Security & Privacy Audit

Every tracked file and the full Git history DAG were audited using safe, local inspection scripts without external network scanning.

### 2.1 Secrets, Credentials & Tokens
- **Real API Keys / Tokens / Secrets:** **ZERO** found.
- **Git History Scan:** Audited all commit diffs (`git log -p HEAD`). Zero live credentials or secret tokens were ever committed.
- **Credential Redaction Implementation:** `ElevenLabsConvAIAdapter` rigorously masks keys in `__repr__`, error payloads, and exceptions (`[REDACTED]` or `...<suffix>`).
- **Test Fixtures:** The mock test `test_adapter_credential_redaction` in `tests/test_elevenlabs_adapter.py` contains a synthetic dummy key (`"sk_live_very_secret_key_9999"`), which is specifically used to verify that the redaction engine masks credentials.

### 2.2 Environment Files & Local Configuration
- **`.env` files:** Zero `.env` files exist in the repository.
- **`.gitignore` Coverage:** Explicitly ignores `.env`, `__pycache__/`, `.pytest_cache/`, `dist/`, `build/`, `.venv/`, and `*.pyc`.
- **`.env.example` Status:** Does not currently exist. A template defining placeholder variables (`ELEVENLABS_API_KEY=`, `ELEVENLABS_AGENT_ID=`) is recommended before open-source community onboarding.

### 2.3 Personal Data & Machine-Specific Paths (Privacy Finding)
- **Source Code (`voicebench/`):** Clean. Zero machine paths or personal identifiers.
- **Tests & Scenarios (`tests/`, `scenarios/`):** Clean. All paths use `pathlib.Path` relative anchors.
- **Documentation (`README.md`):**
  - **Issue Category:** Local machine path & non-portable URI links.
  - **Location:** Lines 127 and 184–193 in `README.md` contain absolute URI links formatted as `file:///C:/Users/Yatha/...`.
  - **Impact:** On GitHub's web interface, `file:///C:/Users/...` links cannot be resolved by external readers and expose the local Windows username (`Yatha`).
  - **Recommendation:** Normalize markdown links to standard relative paths (e.g. `[docs/ADAPTER_GUIDE.md](docs/ADAPTER_GUIDE.md)`).
- **Historical Verification Documents:** `docs/MILESTONE_1_VERIFICATION.md`, `docs/MILESTONE_2_VERIFICATION.md`, and `docs/MILESTONE_3_VERIFICATION.md` record the local pytest execution header containing the local working directory. (Acceptable for historical engineering audit records).

### 2.4 Licensing & Attribution Review
- **Current State:** No `LICENSE` or `LICENSE.md` file currently exists in the local workspace or in the remote repository.
- **Open-Source License Options:**
  1. **Apache License 2.0 (Recommended):** Permissive open-source license with an explicit patent grant and contributor attribution terms. Preferred for AI/ML evaluation and benchmarking toolkits.
  2. **MIT License:** Ultra-permissive, minimal legal text, standard for hackathon entries.
- **Governance:** In accordance with instructions, no license file has been created without user authorization.

### 2.5 Dependencies & Security Claims
- **Dependency Surface:** Exceptionally minimal. Uses Python standard library (`urllib.request`, `json`, `time`, `re`, `pathlib`, `http.server`). Only 2 dependencies: `pyyaml` (data parsing) and `pytest` (test execution).
- **No Unsafe Dependencies:** Zero binary wheels or untrusted native extensions.

---

## 3. Documentation & Implementation Integrity Review

1. **Capability Boundaries Honesty:**
   - Single-agent personas (`SCEN-ROLE-03`), tool call schema validation / result error checking (`SCEN-TOOL-05`), and detour recovery (`SCEN-REC-04`) are accurately reported as supported.
   - Sub-second audio energy barge-in (`SCEN-INT-02`) and multi-speaker floor arbitration (`SCEN-COORD-06`) are explicitly declared unsupported over REST and cleanly rejected (`INCONCLUSIVE`).
2. **Live Integration Claim Accuracy:**
   - Both `README.md` and `docs/MILESTONE_3_VERIFICATION.md` honestly state that live ElevenLabs testing requires user-provided credentials and was **not** claimed as live-tested in the local offline environment (status: `OFFLINE_TESTS_PASS_LIVE_TEST_BLOCKED`).
3. **Measurement Integrity & Timing Provenance:**
   - Transcript turn timestamps are strictly documented and serialized as `target_reported_call_relative` with `event_provenance="target_reported"`.
   - Client HTTP request elapsed duration is isolated as `fetch_latency_ms` with `event_provenance="client_observed"` and clearly documented as transport latency.
   - Unsupported metrics (`yield_latency_ms`) remain `null`, never `0.0`.

---

## 4. Re-Verification Gate Results

| Verification Gate | Command | Exit Code | Result | Details |
| :--- | :--- | :---: | :---: | :--- |
| **Full Automated Test Suite** | `python -m pytest tests/ -v` | **0** | **PASS** | 63 passed, 0 failed, 0 warnings in 10.79s |
| **Scenario Schema Validation** | `python -m voicebench.cli scenarios validate scenarios/roundtable` | **0** | **PASS** | 6 checked, ALL VALID |
| **Git Whitespace & Linter** | `git diff --check` | **0** | **PASS** | Clean working copy, zero whitespace issues |

---

## 5. Exact Files Intended for Publication (Tracked in Commit `11106e2`)

```text
.gitignore
README.md
docs/ADAPTER_GUIDE.md
docs/ARCHITECTURE.md
docs/ELEVENLABS_INTEGRATION_DECISION.md
docs/EVALUATION_PLAN.md
docs/MILESTONE_1_VERIFICATION.md
docs/MILESTONE_2_FINAL_AUDIT.md
docs/MILESTONE_2_VERIFICATION.md
docs/MILESTONE_3_VERIFICATION.md
docs/MILESTONE_PLAN.md
docs/PRIOR_ART_REVIEW.md
docs/PROJECT_DECISION.md
results/diff_baseline_vs_bargein.json
results/diff_baseline_vs_bargein.md
results/run_baseline.json
results/run_defect_bargein.json
results/run_defect_bargein_single.json
results/run_defect_collision.json
results/run_defect_collision_single.json
results/run_defect_role.json
results/run_defect_role_single.json
results/run_defect_routing.json
results/run_defect_routing_single.json
results/run_defect_tool.json
results/run_defect_tool_single.json
results/run_defect_topic.json
results/run_defect_topic_single.json
results/run_defect_transport.json
scenarios/roundtable/SCEN-COORD-06.yaml
scenarios/roundtable/SCEN-INT-02.yaml
scenarios/roundtable/SCEN-REC-04.yaml
scenarios/roundtable/SCEN-ROLE-03.yaml
scenarios/roundtable/SCEN-ROUT-01.yaml
scenarios/roundtable/SCEN-TOOL-05.yaml
tests/test_cli.py
tests/test_comparator.py
tests/test_elevenlabs_adapter.py
tests/test_evaluator.py
tests/test_generic_http_adapter.py
tests/test_independent_synthetic_traces.py
tests/test_parser.py
tests/test_roundtable_target.py
tests/test_scenarios_e2e.py
tests/test_timing_integrity.py
tests/test_trace.py
voicebench/__init__.py
voicebench/adapters/__init__.py
voicebench/adapters/base.py
voicebench/adapters/elevenlabs.py
voicebench/adapters/http.py
voicebench/adapters/mock.py
voicebench/cli.py
voicebench/comparator.py
voicebench/evaluator.py
voicebench/models.py
voicebench/parser.py
voicebench/runner.py
voicebench/targets/__init__.py
voicebench/targets/reference_http_server.py
voicebench/targets/roundtable.py
voicebench/trace.py
```
*(Total: 60 tracked files, 0 binary blobs, 0 credentials).*

---

## 6. Proposed Push Command (NOT EXECUTED)

The exact command to publish the candidate branch to the GitHub repository:

```powershell
# >>> NOT EXECUTED - AWAITING USER APPROVAL <<<
git push -u origin reconcile/github-main:main
```

---

## 7. Next Actions for User Approval

Before executing `git push`, the user may choose whether to:
1. Normalize the `file:///C:/Users/Yatha/...` links in `README.md` to relative repository paths (`docs/...`).
2. Add an open-source `LICENSE` (e.g. Apache 2.0 or MIT).
3. Add a clean `.env.example` file.
4. Proceed directly with the push as-is.
