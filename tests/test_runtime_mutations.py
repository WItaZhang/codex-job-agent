"""Same product assertions run against deliberate mutations via TEST-ONLY env selection.

R2B_MUTATION={filter,cancel,usage,authority} pytest this file must fail. No production switch.
"""

import json
import os
from threading import Event as Barrier

import pytest
from pydantic import BaseModel, ConfigDict

from intent_job_agent.runtime import privacy
from intent_job_agent.runtime.contracts import Cancellation, Usage
from intent_job_agent.runtime.provider import Handle, Runtime
from intent_job_agent.runtime.providers.fake import ScriptedProvider


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tags: list[str]


@pytest.fixture(autouse=True)
def controlled_mutation(monkeypatch):
    mutation = os.environ.get("R2B_MUTATION")
    if mutation == "filter":
        monkeypatch.setattr(
            privacy,
            "assemble",
            lambda node, data, categories, budgets: {"node": node, "data": data, "categories": categories},
        )
    elif mutation == "cancel":
        original = Handle.cancel

        def false_ack(self):
            original(self)
            with self._lock:
                self._event = self._event.model_copy(
                    update={"cancel": Cancellation(local="stopped", remote="cancel_acknowledged")}
                )

        monkeypatch.setattr(Handle, "cancel", false_ack)
    elif mutation == "usage":
        original = Handle.observed

        def zero_unknown(self, reply):
            original(self, reply)
            with self._lock:
                self._event = self._event.model_copy(update={"usage": Usage(input_tokens=0, output_tokens=0)})

        monkeypatch.setattr(Handle, "observed", zero_unknown)
    elif mutation == "authority":
        monkeypatch.setattr(
            "intent_job_agent.runtime.provider.validate_value", lambda model, value: model.model_construct(**value)
        )


def call(rt):
    return rt.structured(
        request_id="r",
        workspace="w",
        run_id="run",
        revision=1,
        node="tagging",
        data={"job": {"description": "synthetic sentinel@example.invalid +1 206 555 0199"}},
        categories=["job"],
        schema=Value,
    )


def test_product_serialized_body_contract(monkeypatch):
    rt = Runtime.load({"provider": {"name": "fake"}}, ScriptedProvider([{"value": {"tags": []}}]))

    def poison(*args, **kwargs):
        pytest.fail("file/network sink reached")

    monkeypatch.setattr("socket.socket.connect", poison)
    monkeypatch.setattr("builtins.open", poison)
    monkeypatch.setattr("pathlib.Path.open", poison)
    assert call(rt).wait(2).status == "completed"
    body = json.loads(rt.provider.bodies[0])
    assert "sentinel@example.invalid" not in json.dumps(body)
    assert "206 555 0199" not in json.dumps(body)


def test_product_cancel_does_not_invent_remote_ack():
    entered, release = Barrier(), Barrier()

    def pending(body, cancel):
        entered.set()
        assert release.wait(2)
        return {"value": {"tags": []}}

    rt = Runtime.load({"provider": {"name": "fake"}}, ScriptedProvider([pending]))
    h = call(rt)
    try:
        assert entered.wait(2)
        h.cancel()
        assert h.wait(1).cancel.remote == "unknown"
    finally:
        release.set()
    assert h.drained.wait(2)


def test_product_missing_usage_is_unknown():
    rt = Runtime.load({"provider": {"name": "fake"}}, ScriptedProvider([{"value": {"tags": []}}]))
    event = call(rt).wait(2)
    assert event.usage.input_tokens is None
    assert event.usage.output_tokens is None


def test_product_model_authority_fields_rejected():
    rt = Runtime.load(
        {"provider": {"name": "fake"}},
        ScriptedProvider([{"value": {"tags": [], "actor": "user", "next_state": "submit"}}]),
    )
    h = call(rt)
    assert h.wait(2).error == "schema_violation"
    assert h.value is None
