"""Transport tests use synthetic replies; they do not measure model quality."""

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from types import SimpleNamespace

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from applypilot_agent.evaluation.profile_memory_codex import (
    CodexCallConfig,
    CodexCallError,
    CodexChatModel,
    CodexCLI,
)


class SyntheticProfile(BaseModel):
    """A synthetic profile for adapter validation."""

    city: str
    skills: list[str]


def make_bridge(tmp_path, max_calls=3):
    return CodexCLI(
        CodexCallConfig(
            executable="codex", model="test-model", reasoning_effort="medium", timeout_seconds=15, max_calls=max_calls
        ),
        tmp_path / "calls",
    )


def envelope(name="SyntheticProfile", arguments=None):
    return {
        "content": "",
        "tool_calls": [
            {"name": name, "arguments_json": json.dumps(arguments or {"city": "Seattle", "skills": ["Python"]})}
        ],
    }


def completed_events(answer, *, extra=None):
    events = [
        {"type": "thread.started", "thread_id": "synthetic-thread"},
        {"type": "turn.started"},
        *(extra or []),
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(answer)}},
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 12, "cached_input_tokens": 3, "output_tokens": 7, "reasoning_output_tokens": 2},
        },
    ]
    return "\n".join(json.dumps(event) for event in events)


def install_reply(monkeypatch, answer, **event_kwargs):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=completed_events(answer, **event_kwargs), stderr="synthetic stderr")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_named_tool_round_trip_is_measured_and_isolated(tmp_path, monkeypatch):
    calls = install_reply(monkeypatch, envelope())
    bridge = make_bridge(tmp_path)
    model = CodexChatModel(bridge=bridge)
    response = model.bind_tools([SyntheticProfile], tool_choice="SyntheticProfile").invoke(
        [HumanMessage(content="Move my preference to Seattle")]
    )
    assert response.tool_calls[0]["args"] == {"city": "Seattle", "skills": ["Python"]}
    assert response.usage_metadata["total_tokens"] == 19
    record = bridge.calls[0]
    assert record.model_requested == "test-model"
    assert record.model_observed is None
    assert record.thread_id == "synthetic-thread"
    assert record.usage["cached_input_tokens"] == 3
    assert not record.contaminated
    args, kwargs = calls[0]
    assert "--ephemeral" in args and "--ignore-user-config" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "features.memories=false" in args
    assert "memories.use_memories=false" in args
    assert "features.shell_tool=false" in args
    assert "--ignore-rules" not in args
    assert kwargs["input"].endswith("}")
    assert kwargs["timeout"] == 15
    path = tmp_path / "calls" / "call_001"
    assert json.loads((path / "metadata.json").read_text()) == asdict(record)
    assert json.loads((path / "request.json").read_text())["tools"][0]["function"]["name"] == "SyntheticProfile"
    assert (path / "stderr.txt").read_text() == "synthetic stderr"
    assert (path / "output_schema.json").exists() and (path / "stdout.jsonl").exists()


@pytest.mark.parametrize(
    ("answer", "choice", "match"),
    [
        ({"content": "", "tool_calls": []}, "SyntheticProfile", "Named tool_choice"),
        ({"content": "", "tool_calls": []}, "any", "Required tool_choice"),
        (envelope(), "none", "forbids"),
        (envelope(name="unknown"), "auto", "unbound"),
        (envelope(arguments=["not an object"]), "auto", "decode to an object"),
    ],
)
def test_invalid_tool_contract_fails_instead_of_succeeding_as_noop(tmp_path, monkeypatch, answer, choice, match):
    calls = install_reply(monkeypatch, answer)
    bridge = make_bridge(tmp_path)
    with pytest.raises(CodexCallError, match=match):
        bridge.generate([{"role": "user", "content": "synthetic"}], [SyntheticProfile], choice)
    assert len(calls) == 1
    assert bridge.calls[0].error


def test_tool_execution_contaminates_even_when_final_answer_is_valid(tmp_path, monkeypatch):
    install_reply(
        monkeypatch,
        envelope(),
        extra=[{"type": "item.completed", "item": {"type": "command_execution", "command": "read labels"}}],
    )
    bridge = make_bridge(tmp_path)
    with pytest.raises(CodexCallError, match="contaminated"):
        bridge.generate([{"role": "user", "content": "synthetic"}], [SyntheticProfile])
    assert bridge.calls[0].contaminated
    assert bridge.calls[0].usage["output_tokens"] == 7
    assert not (tmp_path / "calls" / "call_001" / "response.json").exists()


def test_known_pre_turn_feature_warning_is_saved_without_tool_contamination(tmp_path, monkeypatch):
    warning = {
        "type": "item.completed",
        "item": {
            "type": "error",
            "message": "Under-development features enabled: skip_host_skill_discovery. Synthetic warning.",
        },
    }
    output = completed_events(envelope()).splitlines()
    output.insert(1, json.dumps(warning))
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="\n".join(output), stderr="")
    )
    bridge = make_bridge(tmp_path)
    bridge.generate([{"role": "user", "content": "synthetic"}], [SyntheticProfile], "SyntheticProfile")
    assert not bridge.calls[0].contaminated
    assert bridge.calls[0].diagnostics == [warning["item"]]


def test_unrecognized_error_item_fails_even_with_valid_final_output(tmp_path, monkeypatch):
    install_reply(
        monkeypatch,
        envelope(),
        extra=[{"type": "item.completed", "item": {"type": "error", "message": "Unknown execution error"}}],
    )
    bridge = make_bridge(tmp_path)
    with pytest.raises(CodexCallError, match="item.error"):
        bridge.generate([{"role": "user", "content": "synthetic"}], [SyntheticProfile])


def test_timeout_preserves_partial_evidence_and_does_not_retry(tmp_path, monkeypatch):
    launches = []

    def run(args, **kwargs):
        launches.append(args)
        raise subprocess.TimeoutExpired(args, timeout=15, output=b'{"type":"thread.started","thread_id":"partial"}\n')

    monkeypatch.setattr(subprocess, "run", run)
    bridge = make_bridge(tmp_path)
    with pytest.raises(CodexCallError, match="timed out"):
        bridge.generate([{"role": "user", "content": "synthetic"}])
    assert len(launches) == 1
    assert bridge.calls[0].thread_id == "partial"
    assert "partial" in (tmp_path / "calls" / "call_001" / "stdout.jsonl").read_text()


def test_call_budget_blocks_launch_and_context_preserves_tool_messages(tmp_path, monkeypatch):
    calls = install_reply(monkeypatch, envelope())
    bridge = make_bridge(tmp_path, max_calls=1)
    messages = [
        AIMessage(content="", tool_calls=[{"name": "SyntheticProfile", "args": {}, "id": "prior"}]),
        ToolMessage(content="validation failed", tool_call_id="prior"),
    ]
    bridge.generate(messages, [SyntheticProfile], "any")
    request = json.loads((tmp_path / "calls" / "call_001" / "request.json").read_text())
    assert request["messages"][0]["tool_calls"][0]["id"] == "prior"
    assert request["messages"][1]["tool_call_id"] == "prior"
    with pytest.raises(CodexCallError, match="budget exhausted"):
        bridge.generate(messages, [SyntheticProfile])
    assert len(calls) == 1


def test_real_langmem_applies_model_patch_preserving_profile_identity(tmp_path, monkeypatch):
    langmem = pytest.importorskip("langmem")
    install_reply(
        monkeypatch,
        envelope(
            "PatchDoc",
            {
                "json_doc_id": "current-profile",
                "planned_edits": "Replace city and preserve skills.",
                "patches": [{"op": "replace", "path": "/city", "value": "Seattle"}],
            },
        ),
    )
    bridge = make_bridge(tmp_path)
    manager = langmem.create_memory_manager(
        CodexChatModel(bridge=bridge),
        schemas=[SyntheticProfile],
        instructions="Maintain the one current user profile.",
        enable_inserts=False,
    )
    result = manager.invoke(
        {
            "messages": [{"role": "user", "content": "Now I want Seattle instead."}],
            "existing": [("current-profile", SyntheticProfile(city="Boston", skills=["Python"]))],
            "max_steps": 1,
        }
    )
    assert len(result) == 1 and result[0].id == "current-profile"
    assert result[0].content.model_dump() == {"city": "Seattle", "skills": ["Python"]}
    request = json.loads((tmp_path / "calls" / "call_001" / "request.json").read_text())
    assert any(tool["function"]["name"] == "PatchDoc" for tool in request["tools"])
    assert "Boston" in json.dumps(request["messages"])


def test_concurrent_repair_calls_keep_budget_directories_and_metadata_paired(tmp_path, monkeypatch):
    launches = []

    def run(args, **kwargs):
        ordinal = len(launches) + 1
        launches.append(args)
        time.sleep(0.01)
        output = completed_events(envelope())
        output = output.replace("synthetic-thread", f"synthetic-{ordinal}")
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    bridge = make_bridge(tmp_path, max_calls=4)
    model = CodexChatModel(bridge=bridge).bind_tools([SyntheticProfile], tool_choice="SyntheticProfile")

    def invoke(index):
        try:
            response = model.invoke([HumanMessage(content=f"synthetic repair {index}")])
            return response.response_metadata["codex_call"]
        except CodexCallError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(invoke, range(8)))
    successful = [result for result in results if isinstance(result, dict)]
    assert len(launches) == 4 and len(successful) == 4
    assert len({result["thread_id"] for result in successful}) == 4
    assert len({result["call_dir"] for result in successful}) == 4
    for result in successful:
        ordinal = int(result["call_dir"].split("call_")[-1])
        assert result["thread_id"] == f"synthetic-{ordinal}"
    assert all("budget exhausted" in result for result in results if isinstance(result, str))
