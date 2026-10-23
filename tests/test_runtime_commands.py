"""Synthetic public protocol consumers: never import code from a wire reference."""

import pytest
from pydantic import BaseModel, ConfigDict

from intent_job_agent.runtime.contracts import RuntimeFault, consume


@pytest.mark.parametrize("raw", [None, [], "x", 1])
def test_non_object_events_are_fixed_errors(raw):
    with pytest.raises(RuntimeFault) as error:
        consume(raw)
    assert error.value.code == "schema_violation"


def test_completed_event_cannot_claim_local_stop():
    with pytest.raises(RuntimeFault):
        consume(
            {
                "protocol_version": 1,
                "request_id": "r",
                "run_id": "run",
                "node": "tagging",
                "revision": 1,
                "status": "completed",
                "cancel": {"local": "stopped"},
            }
        )


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tags: list[str]


def consumer():
    from intent_job_agent.runtime.commands import Consumer
    from intent_job_agent.runtime.provider import Runtime
    from intent_job_agent.runtime.providers.fake import ScriptedProvider

    rt = Runtime.load({"provider": {"name": "fake"}}, ScriptedProvider([{"value": {"tags": []}}]))
    return Consumer(rt, workspace="w", run_id="run", revision=1, schemas={"tags-v1": Answer}, tools={})


def command(**updates):
    return {
        "protocol_version": 1,
        "command": "structured",
        "workspace": "w",
        "run_id": "run",
        "revision": 1,
        "request_id": "r",
        "node": "tagging",
        "schema_ref": "tags-v1",
        "data": {"job": {"description": "synthetic@example.invalid"}},
        "categories": ["job"],
        **updates,
    }


def test_command_only_uses_registered_reference():
    c = consumer()
    event = c.dispatch(command())
    assert event.protocol_version == 1
    assert c.wait("r", 2).event.status == "completed"
    status = c.dispatch(
        {
            "protocol_version": 1,
            "command": "status",
            "workspace": "w",
            "run_id": "run",
            "revision": 1,
            "request_id": "r",
        }
    )
    assert status.value == {"tags": []}


@pytest.mark.parametrize(
    "changes",
    [
        {"protocol_version": 2},
        {"protocol_version": None},
        {"command": "submit"},
        {"actor": "user"},
        {"next_state": "submit"},
        {"workspace": "other"},
        {"run_id": "other"},
        {"revision": 0},
        {"schema_ref": "os.system"},
        {"schema": {"import": "os"}},
        {"tool_refs": ["submit"]},
    ],
)
def test_commands_reject_unknowns_and_cross_scope(changes):
    c = consumer()
    with pytest.raises(RuntimeFault):
        c.dispatch(command(**changes))
    assert c.runtime.provider.bodies == []


def test_missing_version_and_nonobjects():
    c = consumer()
    raw = command()
    del raw["protocol_version"]
    for bad in [raw, None, [], "x", 1]:
        with pytest.raises(RuntimeFault):
            c.dispatch(bad)
    assert c.runtime.provider.bodies == []


def test_live_command_cancel_and_revision_binding():
    from threading import Event as Barrier

    from intent_job_agent.runtime.commands import Consumer
    from intent_job_agent.runtime.provider import Runtime
    from intent_job_agent.runtime.providers.fake import ScriptedProvider

    entered, release = Barrier(), Barrier()

    def delayed(body, cancel):
        entered.set()
        assert release.wait(3)
        return {"value": {"tags": ["late"]}, "usage": {"input_tokens": 2}}

    rt = Runtime.load({"provider": {"name": "fake"}}, ScriptedProvider([delayed]))
    c = Consumer(rt, workspace="w", run_id="run", revision=1, schemas={"tags-v1": Answer}, tools={})
    c.dispatch(command())
    control = {
        "protocol_version": 1,
        "command": "cancel",
        "workspace": "w",
        "run_id": "run",
        "revision": 1,
        "request_id": "r",
    }
    try:
        assert entered.wait(2)
        assert c.dispatch(control).event.status == "stopped"
        assert c.dispatch(control).event.cancel.remote == "unknown"
        assert c.wait("r", 1).value is None
        c.advance_revision(2)
        with pytest.raises(RuntimeFault):
            c.dispatch(control)
        with pytest.raises(RuntimeFault):
            c.dispatch(dict(control, revision=2))  # old handle is not a new revision's result
    finally:
        release.set()
    assert c._handles["r"].drained.wait(2)


def test_tool_reference_is_closed_and_no_wire_schema():
    from intent_job_agent.runtime.commands import Consumer
    from intent_job_agent.runtime.provider import Runtime, Tool
    from intent_job_agent.runtime.providers.fake import ScriptedProvider

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        field: str
        value: str

    rt = Runtime.load(
        {"provider": {"name": "fake"}},
        ScriptedProvider(
            [
                {
                    "value": {
                        "tool_calls": [{"name": "fill_field", "arguments": {"field": "name", "value": "Synthetic"}}],
                        "final": None,
                    }
                }
            ]
        ),
    )
    c = Consumer(
        rt,
        workspace="w",
        run_id="run",
        revision=1,
        schemas={},
        tools={"fill-v1": Tool(name="fill_field", parameters=Params)},
    )
    raw = {
        "protocol_version": 1,
        "command": "tool_turn",
        "workspace": "w",
        "run_id": "run",
        "revision": 1,
        "request_id": "r",
        "node": "form_fill",
        "tool_refs": ["fill-v1"],
        "data": {"form_fields": {"name": "Synthetic"}},
        "categories": ["form_fields"],
    }
    for bad in [dict(raw, tool_refs=["os.system"]), dict(raw, actor="user"), dict(raw, schema_ref="os.system")]:
        with pytest.raises(RuntimeFault):
            c.dispatch(bad)
    assert rt.provider.bodies == []
    c.dispatch(raw)
    assert c.wait("r", 2).event.status == "completed"
    assert c.wait("r", 2).value["tool_calls"][0]["name"] == "fill_field"


def test_completed_event_roundtrip():
    c = consumer()
    c.dispatch(command())
    response = c.wait("r", 2)
    assert consume(response.event.model_dump(mode="json")).status == "completed"
