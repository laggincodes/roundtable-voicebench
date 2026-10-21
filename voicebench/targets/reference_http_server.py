"""Reference HTTP Server for VoiceBench Target Integration & Testing.

Provides a minimal, zero-dependency reference implementation of the VoiceBench
HTTP target protocol using standard Python http.server.
Supports both compliant roundtable behavior and deliberate test modes (timeout, 500, malformed JSON).
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import threading
import time
from typing import Any, Dict, Optional, Tuple


class ReferenceRoundtableHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler implementing VoiceBench target protocol."""

    # Configurable mode on the server class: 'compliant', 'timeout', '500', 'malformed_json', 'missing_events'
    server_mode: str = "compliant"

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stderr HTTP request logging during tests."""
        pass

    def do_POST(self) -> None:
        """Handle incoming scenario execution POST request."""
        mode = getattr(self.server, "server_mode", self.server_mode)

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        # 1. Simulate server-side timeout
        if mode == "timeout":
            time.sleep(3.0)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok", "events": []}')
            return

        # 2. Simulate HTTP 500 Internal Server Error
        if mode == "500":
            err_resp = b'{"error": "Internal Server Error in roundtable engine"}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_resp)))
            self.end_headers()
            self.wfile.write(err_resp)
            return

        # 3. Simulate malformed JSON response
        if mode == "malformed_json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "incomplete", "events": [')
            return

        # 4. Simulate missing events field
        if mode == "missing_events":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "success", "result": "no events provided"}')
            return

        # 5. Compliant execution
        try:
            req_data = json.loads(body.decode("utf-8"))
        except Exception:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "Invalid request JSON"}')
            return

        scenario_id = req_data.get("scenario_id", "")
        turns = req_data.get("turns", [])

        # Generate realistic events based on the scenario
        events = []
        if scenario_id == "SCEN-ROUT-01":
            events.append({
                "event_type": "TRANSCRIPT_FINAL",
                "speaker_id": "dr_aris",
                "payload": "From a physics perspective, battery storage capacity degradation limits current grid deployment.",
                "turn_index": 1,
                "source_timestamp_ms": 350.0,
            })
        elif scenario_id == "SCEN-INT-02":
            events.extend([
                {
                    "event_type": "INTERRUPT_ACK",
                    "speaker_id": "elena_vance",
                    "payload": "Agent halted speech cleanly upon barge-in.",
                    "turn_index": 1,
                    "source_timestamp_ms": 3820.0,
                    "metadata": {"yield_latency_ms": 320.0},
                },
                {
                    "event_type": "TRANSCRIPT_FINAL",
                    "speaker_id": "elena_vance",
                    "payload": "You are correct, let's adjust the repealed tariff numbers.",
                    "turn_index": 1,
                    "source_timestamp_ms": 4050.0,
                },
            ])
        else:
            # Generic valid multi-agent response
            speaker = turns[0].get("target_expert") if turns else "dr_aris"
            events.append({
                "event_type": "TRANSCRIPT_FINAL",
                "speaker_id": speaker or "dr_aris",
                "payload": "Roundtable response acknowledging technical topic.",
                "turn_index": 1,
                "source_timestamp_ms": 200.0,
            })

        response_body = {
            "status": "success",
            "session_id": req_data.get("session_id", "default"),
            "events": events,
            "error": None,
        }

        resp_bytes = json.dumps(response_body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)


class TestServerThread:
    """Thread wrapper for running ReferenceRoundtableHandler during tests."""

    __test__ = False

    def __init__(self, port: int = 0, mode: str = "compliant"):
        self.server = ThreadingHTTPServer(("127.0.0.1", port), ReferenceRoundtableHandler)
        self.server.server_mode = mode  # type: ignore
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/api/v1/roundtable/execute"


def run_standalone_server(port: int = 8080, mode: str = "compliant") -> None:
    """Run server from CLI for manual integration testing."""
    server = ThreadingHTTPServer(("0.0.0.0", port), ReferenceRoundtableHandler)
    server.server_mode = mode  # type: ignore
    print(f"VoiceBench Reference Roundtable Server running on http://localhost:{port}/ (mode: {mode})")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VoiceBench Reference HTTP Target Server")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    parser.add_argument(
        "--mode",
        choices=["compliant", "timeout", "500", "malformed_json", "missing_events"],
        default="compliant",
        help="Server simulation mode",
    )
    args = parser.parse_args()
    run_standalone_server(port=args.port, mode=args.mode)
