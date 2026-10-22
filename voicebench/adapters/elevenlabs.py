"""ElevenLabs Conversational AI Target Adapter for VoiceBench.

Integrates with official ElevenLabs Conversational AI REST APIs:
  - Agent Verification: GET /v1/convai/agents/{agent_id}
  - Conversation Extraction: GET /v1/convai/conversations/{conversation_id}

Strictly implements VoiceBench TargetAdapter contract, explicit AdapterCapabilities,
credential redaction, monotonic wall-clock timing, and transport fault classification.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from voicebench.adapters.base import TargetAdapter
from voicebench.models import (
    AdapterCapabilities,
    ConversationTrace,
    EventType,
    Scenario,
)
from voicebench.trace import TraceCollector


class ElevenLabsConvAIAdapter(TargetAdapter):
    """Target adapter for evaluating ElevenLabs Conversational AI agents.

    Operates either against:
      1. A live or staging ElevenLabs API endpoint using official API keys and agent IDs.
      2. A local or mock endpoint implementing the ElevenLabs ConvAI REST response schema.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        agent_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        base_url: str = "https://api.elevenlabs.io",
        timeout_seconds: float = 15.0,
    ):
        target_config = agent_id or "default"
        super().__init__(target_type="elevenlabs_convai", target_config=target_config)
        # Resolve credentials and target conversation from environment if not explicitly provided
        self._api_key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        self.agent_id = agent_id or os.environ.get("ELEVENLABS_AGENT_ID", "")
        self.conversation_id = conversation_id or os.environ.get("ELEVENLABS_CONVERSATION_ID", "")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._evaluated_scenario_id: Optional[str] = None

    def __repr__(self) -> str:
        masked_key = f"...{self._api_key[-4:]}" if len(self._api_key) >= 4 else ("set" if self._api_key else "none")
        return (
            f"ElevenLabsConvAIAdapter(agent_id={self.agent_id or 'none'}, "
            f"conversation_id={self.conversation_id or 'none'}, "
            f"base_url={self.base_url}, api_key={masked_key})"
        )

    def _redact(self, message: str) -> str:
        """Sanitize secrets from error strings, logs, or payloads."""
        if not self._api_key or len(self._api_key) < 4:
            return message
        return message.replace(self._api_key, "[REDACTED_ELEVENLABS_API_KEY]")

    def get_capabilities(self) -> AdapterCapabilities:
        """Declare capabilities of ElevenLabs Conversational AI REST adapter."""
        return AdapterCapabilities(
            supports_text_input=True,
            supports_audio_input=False,
            supports_barge_in=False,  # REST transcript details do not provide sub-second audio energy cancellation timestamps
            supports_tool_calls=True,  # Extracted from transcript tool_calls and tool_results
            supports_multi_agent=False,  # Native ConvAI sessions are single agent
            timing_mode="target_reported_call_relative",
        )

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize adapter resources or override credentials/endpoints from configuration."""
        if config:
            if "api_key" in config:
                self._api_key = str(config["api_key"])
            if "agent_id" in config:
                self.agent_id = str(config["agent_id"])
                self.target_config = self.agent_id
            if "conversation_id" in config:
                self.conversation_id = str(config["conversation_id"])
            if "base_url" in config:
                self.base_url = str(config["base_url"]).rstrip("/")
            if "timeout_seconds" in config:
                self.timeout_seconds = float(config["timeout_seconds"])
        self._evaluated_scenario_id = None

    def teardown(self) -> None:
        """Clean up resources upon test completion."""
        self._evaluated_scenario_id = None

    def validate_scenario_compatibility(self, scenario: Scenario) -> List[str]:
        """Validate scenario compatibility, identifying barge-in or multi-speaker requirements."""
        # Check standard capabilities first (audio, barge-in, tools)
        unsupported = super().validate_scenario_compatibility(scenario)

        # Explicit check for multi-agent floor arbitration requirements
        exp = scenario.expectations
        if exp.max_cross_talk_overlap_ms is not None:
            unsupported.append(
                f"Scenario '{scenario.id}' requires multi-agent cross-talk collision measurement, "
                "which is unsupported by single-agent ElevenLabs ConvAI adapter."
            )
        if exp.expected_speaker_sequence and len(set(exp.expected_speaker_sequence)) > 1:
            unsupported.append(
                f"Scenario '{scenario.id}' requires multi-speaker coordination across multiple agents, "
                "which is unsupported by single-agent ElevenLabs ConvAI adapter."
            )

        return unsupported

    def get_agent_info(self, agent_id: Optional[str] = None) -> Dict[str, Any]:
        """Verify agent connectivity and retrieve configuration from GET /v1/convai/agents/{agent_id}."""
        target_agent = agent_id or self.agent_id
        if not target_agent:
            raise ValueError("Agent ID is required to fetch agent info.")
        if not self._api_key:
            raise ValueError("ElevenLabs API key is required to query ElevenLabs API.")

        url = f"{self.base_url}/v1/convai/agents/{target_agent}"
        headers = {
            "xi-api-key": self._api_key,
            "User-Agent": "VoiceBench-ElevenLabs-Adapter/0.2.0",
        }
        req = urllib.request.Request(url, headers=headers, method="GET")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except urllib.error.HTTPError as e:
            code = e.code
            body = self._redact(e.read().decode("utf-8", errors="replace"))
            if code in (401, 403):
                raise PermissionError(f"ElevenLabs authentication failed (HTTP {code}): Invalid or missing API key.")
            elif code == 404:
                raise ValueError(f"ElevenLabs agent '{target_agent}' not found (HTTP 404).")
            elif code == 429:
                raise RuntimeError(f"ElevenLabs rate limit / quota exceeded (HTTP 429): {body}")
            raise RuntimeError(f"ElevenLabs API HTTP {code} error: {body}")
        except Exception as e:
            raise RuntimeError(f"ElevenLabs connection failure: {self._redact(str(e))}")

    def get_conversation_data(self, conversation_id: str) -> Dict[str, Any]:
        """Verify and retrieve raw conversation details from GET /v1/convai/conversations/{conversation_id}."""
        clean_id = conversation_id.strip() if conversation_id else ""
        if not clean_id:
            raise ValueError("Conversation ID is required to fetch conversation details.")
        if not self._api_key:
            raise ValueError("ElevenLabs API key is required to query ElevenLabs API.")

        url = f"{self.base_url}/v1/convai/conversations/{clean_id}"
        headers = {
            "xi-api-key": self._api_key,
            "User-Agent": "VoiceBench-ElevenLabs-Adapter/0.2.0",
        }
        req = urllib.request.Request(url, headers=headers, method="GET")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except urllib.error.HTTPError as e:
            code = e.code
            body = self._redact(e.read().decode("utf-8", errors="replace"))
            if code in (401, 403):
                raise PermissionError(f"ElevenLabs authentication failed (HTTP {code}): Invalid or missing API key.")
            elif code == 404:
                raise ValueError(f"ElevenLabs conversation '{clean_id}' not found (HTTP 404).")
            elif code == 429:
                raise RuntimeError(f"ElevenLabs rate limit / quota exceeded (HTTP 429): {body}")
            raise RuntimeError(f"ElevenLabs API HTTP {code} error: {body}")
        except Exception as e:
            raise RuntimeError(f"ElevenLabs connection failure: {self._redact(str(e))}")

    def fetch_conversation_trace(
        self,
        conversation_id: str,
        scenario_id: str = "ELEVEN-CONV",
    ) -> ConversationTrace:
        """Fetch full conversation history and convert to VoiceBench ConversationTrace."""
        collector = TraceCollector(
            scenario_id=scenario_id,
            target_type=self.target_type,
            target_config=self.agent_id or "default",
            session_id=conversation_id,
            use_virtual_time=False,
            timing_mode="target_reported_call_relative",
        )

        if not self._api_key:
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                payload={
                    "error": "ConfigurationError: Missing ELEVENLABS_API_KEY",
                    "conversation_id": conversation_id,
                },
                is_simulated=False,
                event_provenance="client_observed",
            )
            return collector.to_trace()

        url = f"{self.base_url}/v1/convai/conversations/{conversation_id}"
        headers = {
            "xi-api-key": self._api_key,
            "User-Agent": "VoiceBench-ElevenLabs-Adapter/0.2.0",
        }
        req = urllib.request.Request(url, headers=headers, method="GET")

        start_monotonic = time.monotonic()
        try:
            collector.record_event(
                event_type=EventType.SESSION_START,
                payload={"conversation_id": conversation_id, "agent_id": self.agent_id},
                is_simulated=False,
                event_provenance="client_observed",
            )

            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw_bytes = resp.read()
                elapsed_ms = (time.monotonic() - start_monotonic) * 1000.0

            try:
                data = json.loads(raw_bytes.decode("utf-8"))
            except Exception as e:
                collector.record_event(
                    event_type=EventType.TRANSPORT_ERROR,
                    payload={"error": f"MalformedResponseError: Invalid JSON body: {e}"},
                    is_simulated=False,
                    event_provenance="client_observed",
                )
                return collector.to_trace()

            if not isinstance(data, dict):
                collector.record_event(
                    event_type=EventType.TRANSPORT_ERROR,
                    payload={"error": "MalformedResponseError: API response is not a JSON object."},
                    is_simulated=False,
                    event_provenance="client_observed",
                )
                return collector.to_trace()

            # Verify returned conversation ID matches requested ID
            returned_id = data.get("conversation_id")
            if returned_id and returned_id != conversation_id:
                collector.record_event(
                    event_type=EventType.TRANSPORT_ERROR,
                    payload={
                        "error": (
                            f"ConversationIdMismatchError: Returned conversation ID '{returned_id}' "
                            f"does not match requested conversation ID '{conversation_id}'."
                        ),
                        "requested_id": conversation_id,
                        "returned_id": returned_id,
                    },
                    is_simulated=False,
                    event_provenance="client_observed",
                )
                return collector.to_trace()

            # Check if conversation is still in progress / processing
            conv_status = str(data.get("status", "done")).lower()
            if conv_status in ("processing", "in_progress", "active"):
                collector.record_event(
                    event_type=EventType.TRANSPORT_ERROR,
                    payload={
                        "error": (
                            f"ConversationProcessingError: Conversation '{conversation_id}' is still in "
                            f"status '{conv_status}'. Transcript is not yet finalized."
                        ),
                        "status": conv_status,
                        "conversation_id": conversation_id,
                    },
                    is_simulated=False,
                    event_provenance="client_observed",
                )
                return collector.to_trace()

            # Verify 'transcript' list presence
            if "transcript" not in data or not isinstance(data["transcript"], list):
                collector.record_event(
                    event_type=EventType.TRANSPORT_ERROR,
                    payload={"error": "MalformedResponseError: Response missing 'transcript' list"},
                    is_simulated=False,
                    event_provenance="client_observed",
                )
                return collector.to_trace()

            # Verify non-empty transcript
            if len(data["transcript"]) == 0:
                collector.record_event(
                    event_type=EventType.TRANSPORT_ERROR,
                    payload={
                        "error": f"EmptyTranscriptError: Conversation '{conversation_id}' returned an empty transcript (0 messages).",
                        "conversation_id": conversation_id,
                    },
                    is_simulated=False,
                    event_provenance="client_observed",
                )
                return collector.to_trace()

            # Verify conversation belongs to configured agent (if agent_id configured)
            conv_agent_id = data.get("agent_id")
            if self.agent_id and conv_agent_id and conv_agent_id != self.agent_id:
                collector.record_event(
                    event_type=EventType.TRANSPORT_ERROR,
                    payload={
                        "error": (
                            f"AgentMismatchError: Conversation '{conversation_id}' belongs to agent "
                            f"'{conv_agent_id}', but adapter was configured for '{self.agent_id}'."
                        )
                    },
                    is_simulated=False,
                    event_provenance="client_observed",
                )
                return collector.to_trace()

            effective_speaker_id = self.agent_id or conv_agent_id or "agent"

            # Parse transcript turns into structured events
            for idx, item in enumerate(data["transcript"]):
                role = item.get("role", "unknown")
                msg = item.get("message", "")
                time_secs = item.get("time_in_call_secs", 0.0)
                source_ts_ms = round(float(time_secs) * 1000.0, 2)
                turn_idx = idx + 1

                # 1. Parse tool_calls if present
                tool_calls = item.get("tool_calls", [])
                if isinstance(tool_calls, list):
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            t_name = tc.get("tool_name", "")
                            raw_args = tc.get("params_as_json", {})
                            parsed_args = raw_args
                            if isinstance(raw_args, str):
                                try:
                                    parsed_args = json.loads(raw_args)
                                except Exception:
                                    parsed_args = {"_raw": raw_args}
                            collector.record_event(
                                event_type=EventType.TOOL_CALL,
                                speaker_id=effective_speaker_id,
                                payload={
                                    "tool": t_name,
                                    "args": parsed_args,
                                    "request_id": tc.get("request_id"),
                                },
                                turn_index=turn_idx,
                                timestamp_ms=source_ts_ms,
                                source_timestamp_ms=source_ts_ms,
                                is_simulated=False,
                                event_provenance="target_reported",
                                metadata={
                                    "tool_has_been_called": tc.get("tool_has_been_called", True),
                                    "request_id": tc.get("request_id"),
                                    "call_relative_secs": time_secs,
                                },
                            )

                # 2. Parse tool_results if present
                tool_results = item.get("tool_results", [])
                if isinstance(tool_results, list):
                    for tr in tool_results:
                        if isinstance(tr, dict):
                            t_name = tr.get("tool_name", "")
                            is_err = bool(tr.get("is_error", False))
                            res_val = tr.get("result_value")
                            parsed_res = res_val
                            if isinstance(res_val, str) and res_val.strip().startswith(("{", "[")):
                                try:
                                    parsed_res = json.loads(res_val)
                                except Exception:
                                    parsed_res = res_val
                            raw_err = tr.get("raw_error_message")
                            collector.record_event(
                                event_type=EventType.TOOL_RESULT,
                                speaker_id=effective_speaker_id,
                                payload={
                                    "tool": t_name,
                                    "result": parsed_res,
                                    "is_error": is_err,
                                    "error": raw_err,
                                    "request_id": tr.get("request_id"),
                                },
                                turn_index=turn_idx,
                                timestamp_ms=source_ts_ms,
                                source_timestamp_ms=source_ts_ms,
                                is_simulated=False,
                                event_provenance="target_reported",
                                metadata={
                                    "is_error": is_err,
                                    "error_type": tr.get("error_type"),
                                    "request_id": tr.get("request_id"),
                                    "call_relative_secs": time_secs,
                                },
                            )

                # 3. Parse speech transcript message
                if role == "agent":
                    collector.record_event(
                        event_type=EventType.TRANSCRIPT_FINAL,
                        speaker_id=effective_speaker_id,
                        payload=msg,
                        turn_index=turn_idx,
                        timestamp_ms=source_ts_ms,
                        source_timestamp_ms=source_ts_ms,
                        is_simulated=False,
                        event_provenance="target_reported",
                        metadata={"time_in_call_secs": time_secs},
                    )
                elif role == "user":
                    collector.record_event(
                        event_type=EventType.USER_INPUT,
                        speaker_id="human_user",
                        payload=msg,
                        turn_index=turn_idx,
                        timestamp_ms=source_ts_ms,
                        source_timestamp_ms=source_ts_ms,
                        is_simulated=False,
                        event_provenance="target_reported",
                        metadata={"time_in_call_secs": time_secs},
                    )
                else:
                    collector.record_event(
                        event_type=EventType.SPEECH_CHUNK,
                        speaker_id=role,
                        payload=msg,
                        turn_index=turn_idx,
                        timestamp_ms=source_ts_ms,
                        source_timestamp_ms=source_ts_ms,
                        is_simulated=False,
                        event_provenance="target_reported",
                        metadata={"time_in_call_secs": time_secs},
                    )

            # Record session end with client-observed REST fetch latency
            collector.record_event(
                event_type=EventType.SESSION_END,
                payload={
                    "status": data.get("status", "done"),
                    "call_duration_secs": data.get("metadata", {}).get("call_duration_secs"),
                    "fetch_latency_ms": round(elapsed_ms, 2),
                    "message_count": len(data.get("transcript", [])),
                },
                is_simulated=False,
                event_provenance="client_observed",
                metadata={
                    "note": "fetch_latency_ms measures client-observed HTTP GET request elapsed time, not conversational latency.",
                    "raw_transcript": data.get("transcript", []),
                },
            )

        except urllib.error.HTTPError as e:
            code = e.code
            err_body = self._redact(e.read().decode("utf-8", errors="replace"))
            if code in (401, 403):
                err_msg = f"AuthenticationError: HTTP {code} (Invalid or missing ElevenLabs API key)"
            elif code == 404:
                err_msg = f"NotFoundError: HTTP 404 (Conversation {conversation_id} not found)"
            elif code == 429:
                err_msg = f"RateLimitError: HTTP 429 (Quota exceeded): {err_body}"
            else:
                err_msg = f"HttpError: HTTP {code}: {err_body}"

            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                payload={"error": err_msg, "status_code": code},
                is_simulated=False,
                event_provenance="client_observed",
            )
        except urllib.error.URLError as e:
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                payload={"error": f"ConnectionError: {self._redact(str(e.reason))}"},
                is_simulated=False,
                event_provenance="client_observed",
            )
        except TimeoutError:
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                payload={"error": f"TimeoutError: Request timed out after {self.timeout_seconds}s"},
                is_simulated=False,
                event_provenance="client_observed",
            )
        except Exception as e:
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                payload={"error": f"UnexpectedError: {self._redact(str(e))}"},
                is_simulated=False,
                event_provenance="client_observed",
            )

        return collector.to_trace()

    def execute_scenario(self, scenario: Scenario) -> ConversationTrace:
        """Execute or evaluate scenario against ElevenLabs target."""
        collector = TraceCollector(
            scenario_id=scenario.id,
            target_type=self.target_type,
            target_config=self.agent_id or "unconfigured",
            use_virtual_time=False,
            timing_mode="target_reported_call_relative",
        )

        # 1. Compatibility check
        unsupported = self.validate_scenario_compatibility(scenario)
        if unsupported:
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                payload={"error": f"UnsupportedCapabilityError: {'; '.join(unsupported)}"},
                is_simulated=False,
                event_provenance="client_observed",
            )
            return collector.to_trace()

        # 2. Check credentials
        if not self._api_key:
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                payload={
                    "error": (
                        "ConfigurationError: Missing ELEVENLABS_API_KEY. "
                        "Set ELEVENLABS_API_KEY environment variable to execute live evaluation."
                    )
                },
                is_simulated=False,
                event_provenance="client_observed",
            )
            return collector.to_trace()

        # 3. Check conversation ID (reject missing or fabricated IDs)
        if not self.conversation_id:
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                payload={
                    "error": (
                        "ConfigurationError: Missing ELEVENLABS_CONVERSATION_ID. "
                        "Pass --conversation-id <id> or set ELEVENLABS_CONVERSATION_ID "
                        "to evaluate a real completed conversation transcript."
                    )
                },
                is_simulated=False,
                event_provenance="client_observed",
            )
            return collector.to_trace()

        # 4. Prevent accidental conversation reuse across distinct scenarios
        if self._evaluated_scenario_id is not None and self._evaluated_scenario_id != scenario.id:
            collector.record_event(
                event_type=EventType.TRANSPORT_ERROR,
                payload={
                    "error": (
                        f"ConversationReuseError: Conversation '{self.conversation_id}' was already "
                        f"evaluated for scenario '{self._evaluated_scenario_id}'. Reusing a single "
                        "conversation across multiple distinct scenarios is prohibited."
                    )
                },
                is_simulated=False,
                event_provenance="client_observed",
            )
            return collector.to_trace()

        self._evaluated_scenario_id = scenario.id
        return self.fetch_conversation_trace(conversation_id=self.conversation_id, scenario_id=scenario.id)
