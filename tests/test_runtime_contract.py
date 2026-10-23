"""Synthetic request bodies and hostile provider outputs; no real accounts."""

import json

import pytest
from pydantic import BaseModel, ConfigDict


def test_serialized_body_is_filtered_and_no_file_or_network(monkeypatch):
    from intent_job_agent.runtime.config import effective_runtime
    from intent_job_agent.runtime.privacy import assemble

    def poison(*args, **kwargs):
        pytest.fail("unexpected I/O")

    budgets = effective_runtime(None).budgets
    monkeypatch.setattr("pathlib.Path.read_text", poison)
    monkeypatch.setattr("socket.socket.connect", poison)
    body = assemble(
        "tagging",
        {
            "job": {"description": "synthetic contact sentinel@example.invalid +1 206 555 0199"},
            "vocabulary": {"entries": ["role.engineer"]},
        },
        ["job", "vocabulary"],
        budgets,
    )
    serialized = json.dumps(body)
    assert "sentinel@example.invalid" not in serialized
    assert "206 555 0199" not in serialized
    assert "[redacted]" in serialized


@pytest.mark.parametrize(
    "node,data,categories",
    [
        ("tagging", {"resume_summary": {"experience": ["private resume sentinel"]}}, ["resume_summary"]),
        ("tagging", {"job": {"description": "job"}}, []),
        ("tagging", {"job": {"description": "job", "profile": "/private/profile.json"}}, ["job"]),
        ("letter", {"letter_material": {"ssn": "synthetic"}}, ["letter_material"]),
        ("unknown", {"job": {"description": "job"}}, ["job"]),
    ],
)
def test_undeclared_or_disallowed_data_rejected(node, data, categories):
    from intent_job_agent.runtime.config import effective_runtime
    from intent_job_agent.runtime.contracts import RuntimeFault
    from intent_job_agent.runtime.privacy import assemble

    with pytest.raises(RuntimeFault):
        assemble(node, data, categories, effective_runtime(None).budgets)


def test_letter_minimal_header_and_analysis_redaction():
    from intent_job_agent.runtime.config import effective_runtime
    from intent_job_agent.runtime.privacy import assemble

    b = effective_runtime(None).budgets
    header = {"letter_header": {"email": "synthetic@example.invalid"}}
    assert assemble("letter", header, ["letter_header"], b)["data"] == header
    body = assemble("analysis", {"feedback": {"text": "email synthetic@example.invalid"}}, ["feedback"], b)
    assert "synthetic@example.invalid" not in json.dumps(body)


def test_schema_defaults_are_not_turned_nullable_and_actor_rejected():
    from intent_job_agent.runtime.contracts import RuntimeFault, validate_value, wire_schema

    class Answer(BaseModel):
        model_config = ConfigDict(extra="forbid")
        note: str = ""
        optional: str | None = None

    schema = wire_schema(Answer)
    assert schema["required"] == ["note", "optional"]
    assert schema["properties"]["note"]["type"] == "string"
    for bad in [
        {"note": None, "optional": None},
        {"note": "ok", "optional": None, "actor": "user"},
        {"next_state": "submit"},
    ]:
        with pytest.raises(RuntimeFault):
            validate_value(Answer, bad)
    assert validate_value(Answer, {"note": "ok", "optional": None}).note == "ok"


def test_v1_consumer_unknown_version_fields_and_boolean_cancel():
    from intent_job_agent.runtime.contracts import Event, RuntimeFault, consume

    base = dict(
        protocol_version=1,
        request_id="r",
        run_id="run",
        node="tagging",
        revision=1,
        status="failed",
        error="budget_exhausted",
    )
    assert consume(base).error == "budget_exhausted"
    for bad in [dict(base, protocol_version=2), dict(base, actor="user"), dict(base, cancel=True)]:
        with pytest.raises(RuntimeFault):
            consume(bad)
    event = Event(**base)
    assert event.usage.input_tokens is None
    assert event.cancel.remote == "unknown"


@pytest.mark.parametrize(
    "node,category,fields",
    [
        ("discovery", "job", {"description": "mail sentinel@example.invalid"}),
        ("form_fill", "tool_result", {"text": "mail sentinel@example.invalid token=secret"}),
        ("intent_edit", "feedback", {"text": "mail sentinel@example.invalid"}),
    ],
)
def test_node_contacts_and_secret_redaction(node, category, fields):
    from intent_job_agent.runtime.config import effective_runtime
    from intent_job_agent.runtime.privacy import assemble

    body = assemble(node, {category: fields}, [category], effective_runtime(None).budgets)
    assert "sentinel@example.invalid" not in json.dumps(body)
    assert "token=secret" not in json.dumps(body)


@pytest.mark.parametrize(
    "category,fields",
    [
        ("form_fields", {"ssn": "synthetic"}),
        ("attachment_refs", {"handles": ["/private/profile.json"]}),
        ("tool_result", {"path": "/private/profile.json"}),
    ],
)
def test_form_fill_has_no_file_or_sensitive_field_escape(category, fields):
    from intent_job_agent.runtime.config import effective_runtime
    from intent_job_agent.runtime.contracts import RuntimeFault
    from intent_job_agent.runtime.privacy import assemble

    with pytest.raises(RuntimeFault):
        assemble("form_fill", {category: fields}, [category], effective_runtime(None).budgets)
