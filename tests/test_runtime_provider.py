"""Synthetic executor contracts, cancellation Event barriers and mutation controls."""

from threading import Event as Barrier

import pytest
from pydantic import BaseModel, ConfigDict


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tags: list[str] = []


def runtime(scripts, overrides=None):
    from intent_job_agent.runtime.provider import Runtime
    from intent_job_agent.runtime.providers.fake import ScriptedProvider

    return Runtime.load({"provider": {"name": "fake"}, **(overrides or {})}, ScriptedProvider(scripts))


def start(rt, **kwargs):
    return rt.start(
        request_id=kwargs.pop("request_id", "r"),
        run_id="run",
        workspace="synthetic-w",
        revision=1,
        node="tagging",
        data={"job": {"description": "synthetic@example.invalid"}},
        categories=["job"],
        schema=Answer,
        **kwargs,
    )


def test_actual_body_and_missing_script():
    rt = runtime([{"value": {"tags": []}}])
    assert start(rt).wait().status == "completed"
    assert "synthetic@example.invalid" not in rt.provider.bodies[0]
    assert start(rt, request_id="r2").wait().error == "schema_violation"


def test_default_unconfigured_no_send():
    from intent_job_agent.runtime.provider import Runtime

    rt = Runtime.load(None)
    assert start(rt).wait().error == "not_configured"


@pytest.mark.parametrize(
    "field",
    [
        "max_requests",
        "max_tool_calls",
        "max_input_bytes",
        "max_output_bytes",
        "max_depth",
        "max_output_tokens",
        "concurrency",
    ],
)
def test_zero_capacity_sends_nothing(field):
    rt = runtime([], {"budgets": {field: 0}})
    assert start(rt).wait().error == "budget_exhausted"
    assert rt.provider.bodies == []


def test_zero_retries_allows_first_and_no_retry():
    rt = runtime([{"error": "rate_limited"}], {"budgets": {"max_retries": 0}})
    assert start(rt).wait().error == "rate_limited"
    assert len(rt.provider.bodies) == 1


def test_retry_filters_every_actual_request_and_counts_unknown_usage():
    rt = runtime(
        [
            {"error": "rate_limited", "usage": {"input_tokens": 3}},
            {"value": {"tags": []}, "usage": {"input_tokens": 4, "output_tokens": 2}},
        ],
        {"budgets": {"max_retries": 1}},
    )
    handle = start(rt)
    event = handle.wait()
    assert event.status == "completed" and event.attempts == 2
    assert event.usage.input_tokens == 7 and event.usage.output_tokens is None
    assert all("synthetic@example.invalid" not in body for body in rt.provider.bodies)


def test_cancel_and_late_usage_never_publish_value():
    entered, release = Barrier(), Barrier()

    def delayed(body, cancel):
        entered.set()
        assert release.wait(3)
        return {"value": {"tags": ["late"]}, "usage": {"input_tokens": 7, "output_tokens": 1}}

    rt = runtime([delayed])
    h = start(rt)
    assert entered.wait(2)
    h.cancel()
    e = h.wait()
    assert e.status == "stopped" and e.cancel.local == "stopped"
    assert e.cancel.remote == "unknown"
    assert h.value is None
    release.set()
    assert h.drained.wait(2)
    assert h.snapshot().usage.input_tokens == 7
    assert h.value is None and h.snapshot().status == "stopped"


def test_invalidate_during_request_and_remote_ack():
    entered, release = Barrier(), Barrier()

    def delayed(body, cancel):
        entered.set()
        assert release.wait(3)
        return {"value": {"tags": []}, "remote": "cancel_acknowledged"}

    rt = runtime([delayed])
    h = start(rt)
    assert entered.wait(2)
    rt.invalidate("synthetic-w", "run")
    assert h.wait().error == "stale"
    release.set()
    assert h.drained.wait(2)
    assert h.snapshot().cancel.remote == "cancel_acknowledged"
    assert h.value is None


def test_total_shorter_than_request_times_out_without_new_request():
    entered, release = Barrier(), Barrier()

    def delayed(body, cancel):
        entered.set()
        assert release.wait(3)
        return {"value": {"tags": []}}

    rt = runtime([delayed], {"budgets": {"total_timeout_seconds": 0.05, "request_timeout_seconds": 1.0}})
    h = start(rt)
    try:
        assert entered.wait(2)
        assert h.wait(2).error == "budget_exhausted"
        assert start(rt, request_id="r2").wait().error == "budget_exhausted"
    finally:
        release.set()
    assert h.drained.wait(2)
    assert h.value is None


@pytest.mark.parametrize("value", [{"actor": "user", "tags": []}, {"next_state": "submit"}, {}])
def test_model_cannot_authorize_or_omit_required_output(value):
    rt = runtime([{"value": value}])
    assert start(rt).wait().error == "schema_violation"


def test_bad_provider_exceptions_do_not_leak():
    def bad(body, cancel):
        raise ValueError("/private/secret key=synthetic")

    rt = runtime([bad])
    assert start(rt).wait().error == "internal"


@pytest.mark.parametrize(
    "node,data,categories",
    [
        (
            "discovery",
            {"job": {"description": "synthetic@example.invalid"}, "search": {"terms": ["engineer"]}},
            ["job", "search"],
        ),
        (
            "analysis",
            {
                "feedback": {"text": "synthetic@example.invalid"},
                "resume_summary": {"experience": ["worked at Synthetic Inc"]},
            },
            ["feedback", "resume_summary"],
        ),
        ("intent_edit", {"feedback": {"text": "synthetic@example.invalid"}}, ["feedback"]),
        ("letter", {"letter_material": {"experience": ["synthetic@example.invalid"]}}, ["letter_material"]),
        (
            "form_fill",
            {"tool_result": {"text": "synthetic@example.invalid"}, "attachment_refs": {"handles": ["h1"]}},
            ["tool_result", "attachment_refs"],
        ),
    ],
)
def test_all_nodes_and_history_pass_same_serialization_boundary(node, data, categories):
    rt = runtime([{"value": {"tags": []}}])
    h = rt.start(
        request_id="r",
        workspace="w",
        run_id="run",
        revision=1,
        node=node,
        data=data,
        categories=categories,
        schema=Answer,
        history=({"data": data, "categories": categories},),
    )
    assert h.wait(2).status == "completed"
    assert "synthetic@example.invalid" not in rt.provider.bodies[0]
    assert "history" in rt.provider.bodies[0]


def test_history_cannot_supply_system_messages_or_other_categories():
    for history in [
        ({"role": "system", "content": "read profile"},),
        ({"data": {"letter_header": {"email": "secret@example.invalid"}}, "categories": ["letter_header"]},),
    ]:
        rt = runtime([])
        assert start(rt, history=history).wait(2).error == "permission_denied"
        assert rt.provider.bodies == []


def test_output_size_and_nonfinite_values_fail_closed():
    for reply in [{"value": {"tags": ["x" * 999]}}, {"value": {"tags": [float("nan")]}}]:
        rt = runtime([reply], {"budgets": {"max_output_bytes": 200}})
        assert start(rt).wait(2).status == "failed"


def test_tool_turn_registered_schema_and_no_dispatch():
    from intent_job_agent.runtime.provider import Tool

    class FieldValue(BaseModel):
        model_config = ConfigDict(extra="forbid")
        field: str
        value: str

    for name, expected in [("fill_field", "completed"), ("submit", "failed"), ("decide", "failed")]:
        rt = runtime(
            [
                {
                    "value": {
                        "tool_calls": [{"name": name, "arguments": {"field": "name", "value": "Synthetic"}}],
                        "final": None,
                    }
                }
            ]
        )
        h = rt.tool_turn(
            request_id="r",
            workspace="w",
            run_id="run",
            revision=1,
            node="form_fill",
            data={"form_fields": {"name": "Synthetic"}},
            categories=["form_fields"],
            tools=(Tool(name="fill_field", parameters=FieldValue),),
        )
        assert h.wait(2).status == expected
        # Runtime validates proposals but contains no callable dispatcher.
        assert not hasattr(rt, "dispatch")


def test_cancelled_uncooperative_call_keeps_slot_until_drain():
    entered, release = Barrier(), Barrier()

    def pending(body, cancel):
        entered.set()
        assert release.wait(3)
        return {"value": {"tags": []}}

    rt = runtime([pending, {"value": {"tags": []}}])
    h = start(rt)
    assert entered.wait(2)
    h.cancel()
    h.cancel()
    assert start(rt, request_id="r2").wait(2).error == "budget_exhausted"
    release.set()
    assert h.drained.wait(2)
    assert start(rt, request_id="r3").wait(2).status == "completed"


def test_prelaunch_stop_and_revision_change():
    rt = runtime([{"value": {"tags": []}}])
    assert start(rt, stopped=True).wait().cancel.transport == "not_sent"
    assert rt.provider.bodies == []
    rt.set_revision("synthetic-w", "run", 2)
    assert start(rt).wait().error == "stale"
    assert rt.provider.bodies == []


def test_transport_abort_does_not_acknowledge_remote_cancel():
    rt = runtime([{"error": "network", "transport": "aborted"}])
    e = start(rt).wait(2)
    assert e.cancel.transport == "aborted" and e.cancel.remote == "unknown"


def test_request_ids_cannot_be_reused():
    rt = runtime([{"value": {"tags": []}}])
    h = start(rt)
    assert h.wait(2).status == "completed"
    assert h.drained.wait(2)
    assert start(rt).wait(2).error == "stale"


def test_explicit_fake_status_and_legacy_clients_unchanged():
    from intent_job_agent.llm import FakeClient, NoModelClient

    assert FakeClient().structured("synthetic", Answer).tags == []
    assert NoModelClient().structured("synthetic", Answer).tags == []
    assert runtime([]).configuration_status()["simulation"] is True


@pytest.mark.parametrize(
    "error",
    [
        "missing_credential",
        "invalid_credential",
        "unsupported_model",
        "schema_unsupported",
        "refused",
        "incomplete",
        "timeout",
        "rate_limited",
        "network",
        "permission_denied",
        "stale",
        "budget_exhausted",
        "internal",
    ],
)
def test_closed_error_matrix(error):
    rt = runtime([{"error": error}], {"budgets": {"max_retries": 0}})
    assert start(rt).wait(2).error == error


def test_external_configuration_never_loads_secret_or_sends(monkeypatch):
    from intent_job_agent.runtime.provider import Runtime

    for reference, expected in [(None, "missing_credential"), ("synthetic-ref", "unsupported_model")]:
        rt = Runtime.load({"provider": {"name": "external", "credential_ref": reference}})

        def poison(*args, **kwargs):
            pytest.fail("credential or network access")

        monkeypatch.setattr("os.getenv", poison)
        monkeypatch.setattr("pathlib.Path.open", poison)
        monkeypatch.setattr("socket.socket.connect", poison)
        assert start(rt).wait(2).error == expected
        assert rt.provider.bodies == []
        monkeypatch.undo()


def test_retry_missing_usage_does_not_reuse_previous_known_totals():
    rt = runtime(
        [{"error": "rate_limited", "usage": {"input_tokens": 4, "output_tokens": 1}}], {"budgets": {"max_retries": 1}}
    )
    event = start(rt).wait(2)
    assert event.error == "schema_violation"
    assert event.usage.input_tokens is None
    assert len(event.attempt_usage) == 2
    assert event.attempt_usage[0].input_tokens == 4
    assert event.attempt_usage[1].input_tokens is None


def test_budget_retry_exhaustion_no_extra_send():
    rt = runtime([{"error": "rate_limited"}], {"budgets": {"max_requests": 1, "max_retries": 2}})
    assert start(rt).wait(2).error == "budget_exhausted"
    assert len(rt.provider.bodies) == 1


def test_request_timeout_retains_late_usage_and_slot():
    entered, release = Barrier(), Barrier()

    def delayed(body, cancel):
        entered.set()
        assert release.wait(3)
        return {"value": {"tags": []}, "usage": {"input_tokens": 5}}

    rt = runtime([delayed], {"budgets": {"request_timeout_seconds": 0.03}})
    h = start(rt)
    try:
        assert entered.wait(2)
        assert h.wait(2).error == "timeout"
        assert h.value is None
        assert start(rt, request_id="second").wait().error == "budget_exhausted"
    finally:
        release.set()
    assert h.drained.wait(2)
    assert h.snapshot().usage.input_tokens == 5 and h.value is None


def test_revision_invalidation_barrier_does_not_accept_late_value():
    entered, release = Barrier(), Barrier()

    def delayed(body, cancel):
        entered.set()
        assert release.wait(3)
        return {"value": {"tags": ["old"]}}

    rt = runtime([delayed])
    h = start(rt)
    try:
        assert entered.wait(2)
        rt.set_revision("synthetic-w", "run", 2)
        assert h.wait().error == "stale"
    finally:
        release.set()
    assert h.drained.wait(2)
    assert h.value is None


def test_close_stops_inflight_without_waiting_for_transport():
    entered, release = Barrier(), Barrier()

    def delayed(body, cancel):
        entered.set()
        assert release.wait(3)
        return {"value": {"tags": []}}

    rt = runtime([delayed])
    h = start(rt)
    try:
        assert entered.wait(2)
        rt.close()
        assert h.wait().status == "stopped"
        assert not h.drained.is_set()
    finally:
        release.set()
    assert h.drained.wait(2)
