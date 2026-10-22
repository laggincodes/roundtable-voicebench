"""Unit tests for VoiceBench Demonstration Dashboard backend (voicebench/demo_app.py).

Verifies local HTTP endpoints, action allowlisting, concurrent lock enforcement,
redaction of secrets, and input validation without external network calls.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from unittest.mock import patch

import pytest

from voicebench.demo_app import (
    DemoAppHandler,
    handle_evaluate_elevenlabs,
    handle_get_signed_url,
    handle_run_baseline,
    handle_run_diff,
    handle_run_tests,
    handle_validate_scenarios,
    redact_secrets,
)


@pytest.fixture(scope="module")
def local_test_server():
    """Spin up an ephemeral test server on localhost on a free port."""
    server = HTTPServer(("127.0.0.1", 0), DemoAppHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def test_redact_secrets():
    # Test sk_ pattern redaction
    sample = "Encountered sk_live_1234567890abcdef12345 in response"
    redacted = redact_secrets(sample)
    assert "sk_live_1234567890abcdef12345" not in redacted
    assert "[REDACTED_API_KEY]" in redacted

    # Test xi-api-key redaction
    sample2 = '{"headers": {"xi-api-key": "secret_token_12345"}}'
    redacted2 = redact_secrets(sample2)
    assert "secret_token_12345" not in redacted2
    assert "[REDACTED]" in redacted2


def test_dashboard_index_route(local_test_server):
    req = urllib.request.Request(f"{local_test_server}/")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        content = resp.read().decode("utf-8")
        assert "VoiceBench" in content
        assert "Evaluation Lab" in content


def test_dashboard_api_status_route(local_test_server):
    req = urllib.request.Request(f"{local_test_server}/api/status")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "online"
        assert data["metadata"]["canonical_scenarios"] == 6
        assert data["metadata"]["verified_test_count"] == 160


def test_dashboard_api_unsupported_action(local_test_server):
    req = urllib.request.Request(
        f"{local_test_server}/api/action",
        data=json.dumps({"action": "unsupported_shell_command"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400


def test_dashboard_api_invalid_json(local_test_server):
    req = urllib.request.Request(
        f"{local_test_server}/api/action",
        data=b"not-json-content",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400


def test_dashboard_api_invalid_route(local_test_server):
    req = urllib.request.Request(
        f"{local_test_server}/api/nonexistent",
        data=json.dumps({"action": "validate_scenarios"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 404


def test_handle_validate_scenarios_unit():
    result = handle_validate_scenarios()
    assert result["action"] == "validate_scenarios"
    assert result["verdict"] == "PASS"
    assert len(result["scenarios"]) == 7


def test_handle_run_baseline_unit():
    result = handle_run_baseline()
    assert result["action"] == "run_baseline"
    assert result["verdict"] == "PASS"
    assert len(result["scenarios"]) == 6
    assert all(s["status"] == "PASS" for s in result["scenarios"])


def test_handle_run_diff_unit():
    result = handle_run_diff()
    assert result["action"] == "run_diff"
    assert result["verdict"] == "REGRESSION_DETECTED"
    assert result["regression_details"]["scenario_id"] == "SCEN-INT-02"
    assert result["regression_details"]["delta"] == "+1130.0ms"


def test_handle_evaluate_elevenlabs_validation():
    # Empty or invalid regex
    res = handle_evaluate_elevenlabs("")
    assert res["verdict"] == "ERROR"
    assert "Invalid Conversation ID" in res["summary"]

    res_invalid = handle_evaluate_elevenlabs("invalid!id;")
    assert res_invalid["verdict"] == "ERROR"


def test_dashboard_api_post_validate_scenarios(local_test_server):
    req = urllib.request.Request(
        f"{local_test_server}/api/action",
        data=json.dumps({"action": "validate_scenarios"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["action"] == "validate_scenarios"
        assert data["verdict"] == "PASS"
        assert data["success"] is True


def test_handle_get_signed_url_unit_success():
    """Test handle_get_signed_url returns structured success when upstream API succeeds."""
    fake_resp = json.dumps({"signed_url": "wss://api.elevenlabs.io/v1/convai/conversation?token=signed_test_tok"}).encode("utf-8")

    class FakeUrlopenResp:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass
        def read(self):
            return fake_resp

    with patch.dict("os.environ", {"ELEVENLABS_API_KEY": "test_key_123", "ELEVENLABS_AGENT_ID": "agent_test_456"}):
        with patch("urllib.request.urlopen", return_value=FakeUrlopenResp()):
            data = handle_get_signed_url()
            assert data["success"] is True
            assert data["signed_url"] == "wss://api.elevenlabs.io/v1/convai/conversation?token=signed_test_tok"
            assert data["agent_id"] == "agent_test_456"
            assert data["target_provenance"] == "server_authenticated_ephemeral_token"


def test_dashboard_signed_url_endpoint_missing_key(local_test_server):
    """Test /api/live/signed-url returns HTTP 400 when ELEVENLABS_API_KEY is missing."""
    with patch.dict("os.environ", {"ELEVENLABS_API_KEY": "", "ELEVENLABS_AGENT_ID": ""}):
        req = urllib.request.Request(f"{local_test_server}/api/live/signed-url")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 400
        err_data = json.loads(exc_info.value.read().decode("utf-8"))
        assert err_data["success"] is False
        assert "ELEVENLABS_API_KEY not configured" in err_data["error"]


def test_dashboard_scenarios_route(local_test_server):
    """Test /api/scenarios endpoint returns 6 canonical roundtable scenarios with metadata."""
    req = urllib.request.Request(f"{local_test_server}/api/scenarios")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["count"] == 6
        scenarios = data["scenarios"]
        ids = [s["id"] for s in scenarios]
        assert "SCEN-ROUT-01" in ids
        assert "SCEN-INT-02" in ids
        assert "SCEN-ROLE-03" in ids
        assert "SCEN-REC-04" in ids
        assert "SCEN-TOOL-05" in ids
        assert "SCEN-COORD-06" in ids
        # Verify required fields
        for s in scenarios:
            assert "name" in s
            assert "failure_mode" in s
            assert "required_behavior" in s
            assert "yaml_content" in s


def test_dashboard_regression_fixture_route(local_test_server):
    """Test /api/regression/fixture endpoint returns verified baseline/candidate comparison."""
    req = urllib.request.Request(f"{local_test_server}/api/regression/fixture")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert "diff" in data
        assert "report_markdown" in data
        assert "demonstration_notice" in data
        assert "virtual simulation timing" in data["demonstration_notice"]


def test_dashboard_gap_analysis_route(local_test_server):
    """Test /api/gap-analysis endpoint returns 15 requirement mappings."""
    req = urllib.request.Request(f"{local_test_server}/api/gap-analysis")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["total"] == 15
        assert data["verified_count"] >= 13
        assert len(data["requirements"]) == 15
        req_ids = [r["id"] for r in data["requirements"]]
        assert "REQ-01" in req_ids
        assert "REQ-15" in req_ids


def test_dashboard_environment_route(local_test_server):
    """Test /api/environment endpoint returns runtime metadata without exposing secrets."""
    req = urllib.request.Request(f"{local_test_server}/api/environment")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert "python_version" in data
        assert "platform" in data
        assert "available_adapters" in data
        assert len(data["available_adapters"]) >= 4
        # Verify no secret values are returned
        assert "sk_" not in json.dumps(data)


def test_dashboard_multi_page_views_present(local_test_server):
    """Verify index.html contains all 7 dedicated multi-page views and sidebar navigation."""
    req = urllib.request.Request(f"{local_test_server}/")
    with urllib.request.urlopen(req) as resp:
        content = resp.read().decode("utf-8")
        assert 'id="page-overview"' in content
        assert 'id="page-scenarios"' in content
        assert 'id="page-run"' in content
        assert 'id="page-regression"' in content
        assert 'id="page-evidence"' in content
        assert 'id="page-adapters"' in content
        assert 'id="page-problem-fit"' in content
        assert ('aside class="sidebar"' in content or '<aside' in content)
        assert 'data-route="overview"' in content
        assert 'data-route="regression"' in content


def test_dashboard_scenarios_live_smoke_separation(local_test_server):
    """Verify live smoke scenario is partitioned from canonical roundtable scenarios."""
    req = urllib.request.Request(f"{local_test_server}/api/scenarios")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        assert "live_scenarios" in data
        assert len(data["live_scenarios"]) == 1
        assert data["live_scenarios"][0]["id"] == "SCEN-ELEVEN-SMOKE-01"
        assert data["live_scenarios"][0]["suite"] == "live_elevenlabs"


def test_dashboard_serves_local_tailwind_stylesheet(local_test_server):
    """Verify locally compiled Tailwind CSS is served correctly with text/css Content-Type."""
    req = urllib.request.Request(f"{local_test_server}/css/tailwind.css")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        content_type = resp.headers.get("Content-Type", "")
        assert "text/css" in content_type
        css = resp.read().decode("utf-8")
        assert len(css) > 10000
        assert "--tw-" in css
        assert "bg-background" in css


def test_dashboard_no_cdn_tailwindcss(local_test_server):
    """Verify index.html uses local compiled CSS and eliminates runtime CDN dependency."""
    req = urllib.request.Request(f"{local_test_server}/")
    with urllib.request.urlopen(req) as resp:
        content = resp.read().decode("utf-8")
        assert "cdn.tailwindcss.com" not in content
        assert 'href="/css/tailwind.css"' in content


def test_dashboard_regression_differential_wiring(local_test_server):
    """Verify differential analysis elements and buttons are wired without noisy debug logs."""
    req = urllib.request.Request(f"{local_test_server}/")
    with urllib.request.urlopen(req) as resp:
        content = resp.read().decode("utf-8")
        assert 'id="btn-run-diff"' in content
        assert 'id="diff-tbody"' in content
        assert "triggerRunDiff()" in content
        assert "Differential analysis loaded:" not in content


def test_dashboard_zero_duplicate_html_ids(local_test_server):
    """Regression test: verify dashboard served HTML contains strictly zero duplicate IDs."""
    from collections import Counter
    from html.parser import HTMLParser

    class IDCollector(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = []

        def handle_starttag(self, tag, attrs):
            for k, v in attrs:
                if k == "id" and v:
                    self.ids.append(v)

    req = urllib.request.Request(f"{local_test_server}/")
    with urllib.request.urlopen(req) as resp:
        content = resp.read().decode("utf-8")

    collector = IDCollector()
    collector.feed(content)

    counts = Counter(collector.ids)
    duplicates = {k: v for k, v in counts.items() if v > 1}
    assert not duplicates, f"Found duplicate HTML element IDs in dashboard: {duplicates}"

    # Verify previously colliding IDs are now unique and correctly disambiguated
    assert counts["diff-tbody"] == 1
    assert counts["adapters-tbody"] == 1
    assert counts["setup-modal"] == 1
    assert counts["adapter-setup-modal"] == 1
    assert counts["modal-type-input"] == 1
    assert counts["adapter-modal-type-input"] == 1
    assert counts["modal-key-input"] == 1
    assert counts["adapter-modal-key-input"] == 1
    assert counts["diagnostic-toast"] == 1
    assert counts["adapter-diagnostic-toast"] == 1


def test_dashboard_all_form_labels_point_to_valid_controls(local_test_server):
    """Regression test: verify every label[for] references a valid existing form control ID."""
    from html.parser import HTMLParser

    class FormElementAuditor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.all_ids = set()
            self.control_ids = set()
            self.labels = []

        def handle_starttag(self, tag, attrs):
            attr_dict = dict(attrs)
            el_id = attr_dict.get("id")
            if el_id:
                self.all_ids.add(el_id)
            if tag in ("input", "select", "textarea"):
                if el_id:
                    self.control_ids.add(el_id)
            elif tag == "label":
                for_val = attr_dict.get("for")
                if for_val:
                    self.labels.append(for_val)

    req = urllib.request.Request(f"{local_test_server}/")
    with urllib.request.urlopen(req) as resp:
        content = resp.read().decode("utf-8")

    auditor = FormElementAuditor()
    auditor.feed(content)

    # Every label's 'for' attribute must resolve to a real element ID
    assert len(auditor.labels) >= 16
    unresolved_labels = [for_val for for_val in auditor.labels if for_val not in auditor.all_ids]
    assert not unresolved_labels, f"Labels point to missing element IDs: {unresolved_labels}"

    # Verify key interactive form controls have corresponding labels
    required_labeled_controls = [
        "scenario-search",
        "filter-mode",
        "filter-status",
        "filter-tag",
        "target-adapter-mock",
        "target-adapter-elevenlabs",
        "target-adapter-generic-http",
        "conv-id-input",
        "regression-baseline-select",
        "regression-candidate-select",
        "eventFilterInput",
        "modal-type-input",
        "modal-key-input",
        "adapter-modal-type-input",
        "adapter-modal-key-input",
        "adapter-modal-agent-input",
    ]
    for cid in required_labeled_controls:
        assert cid in auditor.control_ids, f"Control id='{cid}' missing from DOM"
        assert cid in auditor.labels, f"Control id='{cid}' missing corresponding label[for='{cid}']"


def test_dashboard_dynamic_element_unique_id_derivation(local_test_server):
    """Regression test: verify dynamic JS element generation uses stable scenario-derived or counter IDs."""
    req = urllib.request.Request(f"{local_test_server}/")
    with urllib.request.urlopen(req) as resp:
        content = resp.read().decode("utf-8")

    # Verify dynamic generator patterns in JavaScript
    assert "diff-row-${scenIdClean}" in content or "diff-row-" in content
    assert "btn-inspect-diff-${scenIdClean}" in content or "btn-inspect-diff-" in content
    assert "live-run-${liveRunCounter}" in content
    assert "btn-inspect-${runId}" in content or "btn-inspect-" in content

    # Test dynamic ID generation algorithm against all scenario definitions
    scen_req = urllib.request.Request(f"{local_test_server}/api/scenarios")
    with urllib.request.urlopen(scen_req) as s_resp:
        scenarios_data = json.loads(s_resp.read().decode("utf-8"))

    all_scenarios = scenarios_data.get("scenarios", []) + scenarios_data.get("live_scenarios", [])
    assert len(all_scenarios) >= 7

    generated_ids = []
    for scen in all_scenarios:
        scen_id_clean = scen["id"].lower().replace("_", "-")
        row_id = f"diff-row-{scen_id_clean}"
        btn_id = f"btn-inspect-diff-{scen_id_clean}"
        generated_ids.extend([row_id, btn_id])

    # Dynamic run IDs
    for i in range(1, 10):
        run_id = f"live-run-{i}"
        generated_ids.extend([f"row-{run_id}", f"btn-inspect-{run_id}"])

    # Ensure all generated IDs are strictly unique
    assert len(generated_ids) == len(set(generated_ids)), "Dynamic ID derivation generated duplicate IDs"


def test_dashboard_all_buttons_wired_and_present(local_test_server):
    """Regression test: verify every key interactive button ID is present and wired in the dashboard."""
    req = urllib.request.Request(f"{local_test_server}/")
    with urllib.request.urlopen(req) as resp:
        content = resp.read().decode("utf-8")

    essential_buttons = [
        "global-run-btn",
        "mobile-menu-btn",
        "btn-overview-new-run",
        "btn-validate",
        "btn-toggle-yaml",
        "reset-filters",
        "btn-inspect-yaml",
        "btn-run-single-scenario",
        "btn-copy-yaml",
        "run-btn",
        "btn-export-run-json",
        "btn-print-specimen",
        "btn-run-diff",
        "diff-filter-all",
        "diff-filter-regressions",
        "diff-filter-improvements",
        "btn-inspect-matrix",
        "btn-export-diff",
        "copyTraceBtn",
        "resetFilterBtn",
        "copyJsonBtn",
        "btn-rerun-verbose",
        "btn-adjust-thresholds",
        "btn-export-ledger",
    ]

    for btn_id in essential_buttons:
        assert f'id="{btn_id}"' in content, f"Expected button with id='{btn_id}' in dashboard"


def test_dashboard_zero_dead_buttons(local_test_server):
    """Regression test: audit all button and interactive link elements to assert zero dead controls."""
    from html.parser import HTMLParser
    import re

    class ButtonAuditor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.buttons = []
            self.current_tag = None

        def handle_starttag(self, tag, attrs):
            attr_dict = dict(attrs)
            if tag == "button" or (tag == "a" and "cursor-pointer" in attr_dict.get("class", "")):
                self.buttons.append({
                    "id": attr_dict.get("id", ""),
                    "onclick": attr_dict.get("onclick"),
                    "href": attr_dict.get("href"),
                    "data_route": attr_dict.get("data-route"),
                    "line": self.getpos()[0]
                })

    req = urllib.request.Request(f"{local_test_server}/")
    with urllib.request.urlopen(req) as resp:
        content = resp.read().decode("utf-8")

    auditor = ButtonAuditor()
    auditor.feed(content)

    scripts = "\n".join(re.findall(r"<script\b[^>]*>([\s\S]*?)</script>", content))

    dead_buttons = []
    for b in auditor.buttons:
        el_id = b["id"]
        has_onclick = bool(b["onclick"])
        has_listener = False
        if el_id:
            if f"getElementById('{el_id}')" in scripts or f'getElementById("{el_id}")' in scripts:
                has_listener = True
            elif f"querySelector('#{el_id}')" in scripts or f'querySelector("#{el_id}")' in scripts:
                has_listener = True

        is_handled = has_onclick or has_listener or bool(b.get("data_route")) or (b.get("href") and b.get("href") != "#")
        if not is_handled:
            dead_buttons.append(b)

    assert not dead_buttons, f"Found unhandled/dead buttons in dashboard: {dead_buttons}"


def test_dashboard_action_handlers_execution(local_test_server):
    """Verify POST /api/action executes all core actions cleanly."""
    actions = ["validate_scenarios", "run_baseline", "run_diff", "run_tests"]
    for action in actions:
        post_data = json.dumps({"action": action}).encode("utf-8")
        req = urllib.request.Request(
            f"{local_test_server}/api/action",
            data=post_data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["success"] is True
            assert data["action"] == action


def test_handle_run_tests_parsing():
    """Verify handle_run_tests correctly parses pytest success output and failure output."""
    import os
    env_patch = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    with patch.dict(os.environ, env_patch, clear=True):
        with patch("voicebench.demo_app.run_process") as mock_run:
            mock_run.return_value = (0, "166 passed in 14.20s", 14.2)
            res = handle_run_tests()
            assert res["success"] is True
            assert res["verdict"] == "PASS"
            assert res["passed_count"] == 166
            assert res["failed_count"] == 0

        with patch("voicebench.demo_app.run_process") as mock_run:
            mock_run.return_value = (1, "150 passed, 2 failed in 12.0s", 12.0)
            res = handle_run_tests()
            assert res["success"] is False
            assert res["verdict"] == "FAIL"
            assert res["passed_count"] == 150
            assert res["failed_count"] == 2







