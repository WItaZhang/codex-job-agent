"""A measured Codex CLI transport for profile experiments, not native Memories.

Both experiment arms use this same transport. LangMem's actual extraction and
repair graph receives genuine AIMessage tool calls; this module only translates
the model interface. Every CLI invocation starts a fresh ephemeral session.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict

ENVELOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "tool_calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "arguments_json": {"type": "string"},
                },
                "required": ["name", "arguments_json"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["content", "tool_calls"],
    "additionalProperties": False,
}

# These are experiment isolation invariants, not candidate-specific tuning.
ISOLATION_CONFIG = {
    "project_doc_max_bytes": 0,
    "memories.use_memories": False,
    "memories.generate_memories": False,
    "features.memories": False,
    "features.hooks": False,
    "features.shell_tool": False,
    "features.apps": False,
    "features.plugins": False,
    "features.multi_agent": False,
    "features.skill_search": False,
    "features.skip_host_skill_discovery": True,
    "web_search": "disabled",
}


@dataclass(frozen=True)
class CodexCallConfig:
    executable: str
    model: str
    reasoning_effort: str
    timeout_seconds: float
    max_calls: int
    config_overrides: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.timeout_seconds <= 0 or self.max_calls <= 0:
            raise ValueError("timeout_seconds and max_calls must be positive")
        reserved = {*ISOLATION_CONFIG, "model", "model_reasoning_effort", "sandbox_mode", "approval_policy"}
        if reserved.intersection(self.config_overrides):
            raise ValueError("config_overrides may not replace model or isolation settings")


@dataclass
class CallRecord:
    call_dir: str
    model_requested: str
    model_observed: str | None = None
    thread_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    contaminated: bool = False
    contaminating_events: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    returncode: int | None = None


class CodexCallError(RuntimeError):
    """A failed, malformed, timed out, over-budget or contaminated model call."""


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _as_text(value: str | bytes | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


def _encode_message(message: BaseMessage | dict[str, Any]) -> dict[str, Any]:
    if isinstance(message, dict):
        return message
    roles = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool", "function": "function"}
    result = {"role": roles.get(message.type, message.type), "content": message.content}
    if message.name:
        result["name"] = message.name
    for key in ("tool_calls", "tool_call_id"):
        value = getattr(message, key, None)
        if value:
            result[key] = value
    return result


def _validate_envelope(value: Any, tools: Sequence[dict], choice: Any) -> dict:
    if not isinstance(value, dict) or set(value) != {"content", "tool_calls"}:
        raise CodexCallError("Final response must have exactly content and tool_calls")
    if not isinstance(value["content"], str) or not isinstance(value["tool_calls"], list):
        raise CodexCallError("Invalid envelope field types")
    names = {tool["function"]["name"] for tool in tools}
    calls = value["tool_calls"]
    for call in calls:
        if not isinstance(call, dict) or set(call) != {"name", "arguments_json"}:
            raise CodexCallError("Invalid tool call envelope")
        if not isinstance(call["name"], str) or call["name"] not in names:
            raise CodexCallError("Model called an unbound tool")
        if not isinstance(call["arguments_json"], str):
            raise CodexCallError("Tool arguments_json must be a string")
        if not isinstance(json.loads(call["arguments_json"]), dict):
            raise CodexCallError("Tool arguments must decode to an object")
    if choice == "none" and calls:
        raise CodexCallError("tool_choice=none forbids tool calls")
    required_name = None
    if isinstance(choice, dict):
        required_name = choice.get("function", {}).get("name") or choice.get("name")
        if not required_name:
            raise CodexCallError("Unsupported tool_choice object")
    elif isinstance(choice, str) and choice not in {"auto", "none", "any", "required"}:
        required_name = choice
    if required_name and (not calls or any(call["name"] != required_name for call in calls)):
        raise CodexCallError("Named tool_choice requires at least one call to that tool only")
    if (choice is True or choice in ("any", "required")) and not calls:
        raise CodexCallError("Required tool_choice returned no tool calls")
    return value


class CodexCLI:
    def __init__(self, config: CodexCallConfig, root_dir: Path):
        self.config = config
        self.root_dir = Path(root_dir).resolve()
        self.calls: list[CallRecord] = []
        # Trustcall may repair multiple calls concurrently. Serialize transport
        # allocation and reads so budgets, directories and metadata stay paired.
        self._lock = threading.RLock()

    def generate(
        self,
        messages: Sequence[BaseMessage | dict[str, Any]],
        tools: Sequence[Any] = (),
        tool_choice: Any = None,
        response_schema: dict | None = None,
    ) -> dict:
        with self._lock:
            return self._generate_once(messages, tools, tool_choice, response_schema)

    def _generate_once(
        self,
        messages: Sequence[BaseMessage | dict[str, Any]],
        tools: Sequence[Any],
        tool_choice: Any,
        response_schema: dict | None,
    ) -> dict:
        if len(self.calls) >= self.config.max_calls:
            raise CodexCallError(f"Model call budget exhausted ({self.config.max_calls})")
        call_dir = self.root_dir / f"call_{len(self.calls) + 1:03d}"
        call_dir.mkdir(parents=True, exist_ok=False)
        record = CallRecord(call_dir=str(call_dir), model_requested=self.config.model)
        self.calls.append(record)
        bound_tools = [convert_to_openai_tool(tool) for tool in tools]
        request = {
            "messages": [_encode_message(message) for message in messages],
            "tools": bound_tools,
            "tool_choice": tool_choice,
            "response_schema": response_schema,
        }
        prompt = (
            "Act as the chat model for the serialized conversation below. Follow its message roles and tool schemas. "
            "Do not use any Codex tools, external information, filesystem, browser, or memories. "
            "Return only the requested JSON envelope. The envelope content is your assistant text; tool_calls lists "
            "the requested function calls, with each arguments_json a valid JSON object encoded as a string. "
            "Honor tool_choice: a named tool requires one or more calls to only that tool; any/required requires "
            "at least one call; none forbids calls. Multiple calls are allowed. These are returned data, not "
            "Codex tool executions. Do not invent tool names.\n\n" + json.dumps(request, ensure_ascii=False, indent=2)
        )
        schema_path = call_dir / "output_schema.json"
        _write_json(schema_path, ENVELOPE_SCHEMA)
        _write_json(call_dir / "request.json", request)
        (call_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        args = [
            self.config.executable,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--json",
            "--color",
            "never",
            "--output-schema",
            str(schema_path),
            "--model",
            self.config.model,
        ]
        settings = (
            self.config.config_overrides
            | ISOLATION_CONFIG
            | {
                "model_reasoning_effort": self.config.reasoning_effort,
                "approval_policy": "never",
            }
        )
        for key, value in settings.items():
            args.extend(["--config", f"{key}={json.dumps(value)}"])
        args.append("-")
        _write_json(call_dir / "command.json", args)
        stdout, stderr = "", ""
        started = time.perf_counter()
        try:
            result = subprocess.run(
                args,
                input=prompt,
                cwd=call_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.timeout_seconds,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            stdout, stderr = result.stdout, result.stderr
            record.returncode = result.returncode
            final_text = self._parse_events(stdout, record)
            if result.returncode:
                raise CodexCallError(f"Codex exited with status {result.returncode}; see stderr.txt")
            if record.contaminated:
                raise CodexCallError("Codex used external tools; this call is contaminated and cannot be scored")
            envelope = _validate_envelope(json.loads(final_text), bound_tools, tool_choice)
            _write_json(call_dir / "response.json", envelope)
            return envelope
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = _as_text(exc.stdout), _as_text(exc.stderr)
            # Keep partial usage and tool evidence without hiding the timeout.
            try:
                self._parse_events(stdout, record)
            except (ValueError, CodexCallError):
                pass
            record.error = f"Codex timed out after {self.config.timeout_seconds} seconds"
            raise CodexCallError(record.error) from exc
        except (OSError, ValueError, CodexCallError) as exc:
            record.error = str(exc)
            raise CodexCallError(record.error) from exc
        finally:
            record.elapsed_seconds = time.perf_counter() - started
            (call_dir / "stdout.jsonl").write_text(stdout, encoding="utf-8")
            (call_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
            _write_json(call_dir / "metadata.json", asdict(record))

    @staticmethod
    def _parse_events(stdout: str, record: CallRecord) -> str:
        final_text = None
        completed = False
        turn_started = False
        failure = None
        for line in stdout.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            event_type = event.get("type")
            if event_type == "thread.started":
                record.thread_id = event.get("thread_id")
            if event_type == "turn.started":
                turn_started = True
            if isinstance(event.get("model"), str):
                record.model_observed = event["model"]
            if event_type == "turn.completed":
                completed = True
                record.usage = event.get("usage") or {}
            elif event_type in {"turn.failed", "error"}:
                failure = event_type
            item = event.get("item")
            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type == "error":
                    record.diagnostics.append(item)
                    # Current CLI emits this startup warning as an ErrorItem.
                    # It has no tool execution semantics. Other errors fail.
                    known_startup_warning = not turn_started and str(item.get("message", "")).startswith(
                        "Under-development features enabled: "
                    )
                    if not known_startup_warning:
                        failure = "item.error"
                elif item_type not in {"agent_message", "reasoning"}:
                    record.contaminated = True
                    record.contaminating_events.append({"event_type": event_type, "item_type": item_type})
                if item_type == "agent_message" and event_type == "item.completed":
                    final_text = item.get("text")
        if failure:
            raise CodexCallError(f"Codex emitted {failure}; see stdout.jsonl")
        if not completed or not isinstance(final_text, str):
            raise CodexCallError("Codex did not complete with an assistant message")
        return final_text


class CodexChatModel(BaseChatModel):
    """LangChain model interface backed by the common measured CLI transport."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    bridge: CodexCLI

    @property
    def _llm_type(self) -> str:
        return "codex-cli-profile-experiment"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model": self.bridge.config.model, "reasoning_effort": self.bridge.config.reasoning_effort}

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any):
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools], tool_choice=tool_choice, **kwargs)

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        if stop:
            raise ValueError("This experiment transport does not implement stop sequences")
        with self.bridge._lock:
            return self._generate_locked(messages, **kwargs)

    def _generate_locked(self, messages: list[BaseMessage], **kwargs) -> ChatResult:
        result = self.bridge.generate(messages, kwargs.get("tools", ()), kwargs.get("tool_choice"))
        record = self.bridge.calls[-1]
        tool_calls = [
            {
                "id": f"call_{len(self.bridge.calls)}_{index}",
                "type": "tool_call",
                "name": call["name"],
                "args": json.loads(call["arguments_json"]),
            }
            for index, call in enumerate(result["tool_calls"])
        ]
        usage = record.usage
        usage_metadata = None
        if isinstance(usage.get("input_tokens"), int) and isinstance(usage.get("output_tokens"), int):
            usage_metadata = {
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "total_tokens": usage["input_tokens"] + usage["output_tokens"],
            }
        message = AIMessage(
            content=result["content"],
            tool_calls=tool_calls,
            usage_metadata=usage_metadata,
            response_metadata={"codex_call": asdict(record)},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])
