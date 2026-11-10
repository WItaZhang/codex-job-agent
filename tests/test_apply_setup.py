"""Synthetic startup cases. Never use a real Gmail credential or mailbox."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent

from intent_job_agent import gmail_setup
from intent_job_agent.store import Store
from intent_job_agent.tools import Toolbox

from .conftest import ROOT, settings
from .test_slice6 import write_applicant


@pytest.fixture
def mail(tmp_path, monkeypatch):
    oauth, credentials = tmp_path / "oauth.json", tmp_path / "credentials.json"
    monkeypatch.setenv("GMAIL_OAUTH_PATH", str(oauth))
    monkeypatch.setenv("GMAIL_CREDENTIALS_PATH", str(credentials))
    monkeypatch.setattr(gmail_setup.shutil, "which", lambda name: "/synthetic/bin/" + name)
    return settings(), oauth, credentials


def test_absent_files_are_actionable_and_never_start_a_service(mail, monkeypatch):
    s, oauth, credentials = mail
    monkeypatch.setattr(gmail_setup, "probe", lambda *_: pytest.fail("No credentials: no subprocess"))
    result = gmail_setup.status(s, connect=True)
    assert result["status"] == "not_configured" and not result["ready"]
    assert "gmail-setup" in result["guide"]
    oauth.write_text("synthetic client")
    assert gmail_setup.status(s, connect=True)["status"] == "authorization_required"
    credentials.write_text("synthetic token")
    assert gmail_setup.status(s)["status"] == "not_checked"


@pytest.mark.parametrize(
    "text,is_error,expected",
    [
        ("", False, "ready"),
        ("Error: invalid_grant synthetic-secret", False, "authorization_required"),
        (
            "Error: No access, refresh token, API key or refresh handler callback is set.",
            False,
            "authorization_required",
        ),
        ("Error: Insufficient Permission synthetic-secret", False, "authorization_required"),
        ("Error: network unavailable synthetic-secret", False, "connection_failed"),
        ("synthetic-secret", True, "connection_failed"),
        ("Unexpected provider output synthetic-secret", False, "connection_failed"),
    ],
)
def test_probe_result_checks_provider_errors_without_exposing_them(mail, monkeypatch, text, is_error, expected):
    s, oauth, credentials = mail
    oauth.write_text("synthetic client")
    credentials.write_text("synthetic token")
    reply = CallToolResult(content=[TextContent(type="text", text=text)], isError=is_error)
    monkeypatch.setattr(gmail_setup, "probe", lambda *_: reply)
    result = gmail_setup.status(s, connect=True)
    assert result["status"] == expected
    assert result["ready"] is (expected == "ready")
    assert "synthetic-secret" not in json.dumps(result)


@pytest.mark.parametrize("error,expected", [(TimeoutError(), "timeout"), (OSError("secret"), "connection_failed")])
def test_connection_failures_are_bounded_statuses(mail, monkeypatch, error, expected):
    s, oauth, credentials = mail
    oauth.touch()
    credentials.touch()

    def fail(*_):
        raise error

    monkeypatch.setattr(gmail_setup, "probe", fail)
    result = gmail_setup.status(s, connect=True)
    assert result["status"] == expected and "secret" not in json.dumps(result)


def test_disabled_or_local_only_never_inspects_credentials(mail, monkeypatch):
    s, *_ = mail
    monkeypatch.setattr(Path, "is_file", lambda *_: pytest.fail("No credential inspection"))
    disabled = s.model_copy(
        update={"apply": s.apply.model_copy(update={"gmail": s.apply.gmail.model_copy(update={"enabled": False})})}
    )
    assert gmail_setup.status(disabled, connect=True)["status"] == "disabled"
    local = s.model_copy(update={"execution_mode": "local_only"})
    assert gmail_setup.status(local, connect=True)["status"] == "disabled"


def test_setup_separates_materials_web_and_mail(mail, tmp_path, monkeypatch):
    from intent_job_agent import tools

    s, *_ = mail
    monkeypatch.setattr(tools, "_chrome_found", lambda: True)
    box = Toolbox(s, Store(tmp_path / "agent.sqlite", s), data_dir=tmp_path / "data")
    write_applicant(box.data_dir)
    result = box.get_apply_setup()
    assert result["materials_ready"] and result["can_start_web"]
    assert not result["ready"] and result["gmail"]["status"] == "not_configured"
    assert result["warnings"] and result["ready_scope"]
    monkeypatch.setattr(tools.shutil, "which", lambda name: None if name == "claude" else "/synthetic/npx")
    result = box.get_apply_setup()
    assert not result["ready"] and not result["can_start_web"]
    assert result["missing_programs"] == ["claude"]


def test_setup_file_presence_does_not_mean_live_connection(mail, tmp_path, monkeypatch):
    from intent_job_agent import tools

    s, oauth, credentials = mail
    oauth.touch()
    credentials.touch()
    monkeypatch.setattr(tools, "_chrome_found", lambda: True)
    box = Toolbox(s, Store(tmp_path / "agent.sqlite", s), data_dir=tmp_path / "data")
    write_applicant(box.data_dir)
    assert not box.get_apply_setup()["ready"]
    monkeypatch.setattr(gmail_setup, "probe", lambda *_: CallToolResult(content=[TextContent(type="text", text="")]))
    result = box.get_apply_setup(check_connections=True)
    assert result["ready"] and result["gmail"]["status"] == "ready"


def synthetic_npx(tmp_path, monkeypatch, mode="ready"):
    record = tmp_path / "protocol.json"
    script = (ROOT / "tests/fixtures/gmail_mcp.py").read_text()
    script = script.replace('MODE = "ready"', f"MODE = {mode!r}").replace('RECORD = ""', f"RECORD = {str(record)!r}")
    binary = tmp_path / "npx"
    binary.write_text(f"#!{sys.executable}\n" + script)
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    return record


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("ready", "ready"),
        ("revoked", "authorization_required"),
        ("malformed", "connection_failed"),
        ("timeout", "timeout"),
    ],
)
def test_real_stdio_protocol_is_private_bounded_and_never_reads_mail(
    mail, tmp_path, monkeypatch, caplog, capfd, mode, expected
):
    s, oauth, credentials = mail
    oauth.write_text("synthetic client; not parsed by project")
    credentials.write_text("synthetic token; not parsed by project")
    record = synthetic_npx(tmp_path, monkeypatch, mode)
    s = s.model_copy(
        update={
            "apply": s.apply.model_copy(update={"gmail": s.apply.gmail.model_copy(update={"timeout_seconds": 0.8})})
        }
    )
    start = time.monotonic()
    result = gmail_setup.status(s, connect=True)
    assert result["status"] == expected
    assert time.monotonic() - start < 10  # Includes the SDK's bounded process teardown.
    seen = json.loads(record.read_text())
    assert seen["cwd_files"] == [] and not Path(seen["cwd"]).exists()
    assert seen["oauth"] == str(oauth) and seen["credentials"] == str(credentials)
    assert seen["argv"] == ["-y", s.apply.mcp.gmail]  # never auth
    if mode != "malformed":
        assert seen["calls"][-1] == {
            "method": "tools/call",
            "params": {"name": "search_emails", "arguments": {"query": s.apply.gmail.probe_query, "maxResults": 1}},
        }
    with pytest.raises(ProcessLookupError):
        os.kill(seen["pid"], 0)
    captured = capfd.readouterr()
    assert "synthetic-secret" not in json.dumps(result) + caplog.text + captured.out + captured.err


def test_actual_service_wrapper_uses_same_paths_and_empty_cwd(mail, tmp_path, monkeypatch):
    s, oauth, credentials = mail
    record = synthetic_npx(tmp_path, monkeypatch)
    entry = gmail_setup.server_config(s)
    subprocess.run([entry["command"], *entry["args"]], input="", text=True, check=True, timeout=10)
    seen = json.loads(record.read_text())
    assert seen["cwd_files"] == [] and not Path(seen["cwd"]).exists()
    assert seen["oauth"] == str(oauth) and seen["credentials"] == str(credentials)
    assert seen["argv"] == ["-y", s.apply.mcp.gmail]


def test_authorization_is_an_explicit_command_using_shared_paths(mail, monkeypatch, capsys):
    from intent_job_agent import workspace

    s, oauth, credentials = mail
    monkeypatch.setattr(workspace, "resolve_settings", lambda **_: s)
    calls = []
    monkeypatch.setattr(gmail_setup, "_provider", lambda *a, **kw: calls.append((a, kw)) or 0)
    assert gmail_setup.main(["status"]) == 1 and not calls
    assert gmail_setup.main(["auth"]) == 1 and not calls
    oauth.touch()
    assert gmail_setup.main(["auth"]) == 0
    assert calls == [
        (
            (s.apply.mcp.gmail, {"GMAIL_OAUTH_PATH": str(oauth), "GMAIL_CREDENTIALS_PATH": str(credentials)}),
            {"auth": True},
        )
    ]
    assert not credentials.exists()  # The synthetic provider never obtains a real token.


@pytest.mark.parametrize("state", ["not_configured", "authorization_required", "timeout", "ready"])
def test_run_rechecks_mail_and_preserves_fill_only_permissions(tmp_path, monkeypatch, state):
    from intent_job_agent import apply_bridge

    from .apply_fakes import synthetic_binding
    from .test_gate_handoff import FAKE_CLAUDE_ENV
    from .test_gate_handoff import box as make_box
    from .test_slice6 import add_jobs, fake_claude, write_letters

    box = make_box.__wrapped__(tmp_path)
    s = box.settings
    (job,) = add_jobs(box, ["Synthetic Acme"])
    write_letters(box, [job])
    fake_claude(tmp_path, monkeypatch, FAKE_CLAUDE_ENV, gmail_ready=False)
    checked = []

    def check(settings, *, connect=False):
        checked.append(connect)
        return {"ready": state == "ready", "status": state}

    monkeypatch.setattr(gmail_setup, "status", check)
    outcome = apply_bridge.run_application(
        box._apply_spec(job, "stop"),
        box.data_dir,
        s,
        box._applicant().profile,
        synthetic_binding(),
        extra_disallowed=tuple(s.form_fill.disallowed_tools),
    )
    assert checked == [True]
    seen = json.loads((tmp_path / "args.json").read_text())
    argv = seen["argv"]
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "mcp__gmail__send_email" not in allowed
    assert "Do NOT click the final Submit" in seen["prompt"]
    servers = json.loads(seen["mcp"])["mcpServers"]
    assert ("gmail" in servers) is (state == "ready")
    if state != "ready":
        assert "mcp__gmail" not in allowed
        assert f"Gmail unavailable ({state})" in seen["prompt"]
        assert "RESULT:FAILED:email_verification" in seen["prompt"]
        assert "Do not claim you searched Gmail" in seen["prompt"]
        assert any(f"Gmail unavailable ({state})" in note for note in outcome.notes)
    else:
        assert "mcp__gmail__read_email" in allowed and "mcp__gmail__search_emails" in allowed


def test_disabled_gmail_does_not_block_web_readiness(mail, tmp_path, monkeypatch):
    from intent_job_agent import tools

    s, *_ = mail
    s = s.model_copy(
        update={"apply": s.apply.model_copy(update={"gmail": s.apply.gmail.model_copy(update={"enabled": False})})}
    )
    monkeypatch.setattr(tools, "_chrome_found", lambda: True)
    box = Toolbox(s, Store(tmp_path / "agent.sqlite", s), data_dir=tmp_path / "data")
    write_applicant(box.data_dir)
    result = box.get_apply_setup(check_connections=True)
    assert result["ready"] and result["can_start_web"]
    assert result["gmail"]["status"] == "disabled" and not result["gmail"]["ready"]


@pytest.mark.parametrize("found", [True, False])
def test_setup_checks_the_configured_chrome_binary(mail, tmp_path, monkeypatch, found):
    from intent_job_agent import tools

    s, *_ = mail
    binary = "/synthetic/Chrome-Jobs"
    s = s.model_copy(update={"apply": s.apply.model_copy(update={"browser_executable": binary})})
    monkeypatch.setattr(tools, "_chrome_found", lambda: pytest.fail("Must check configured binary"))
    monkeypatch.setattr(tools.shutil, "which", lambda name: None if name == binary and not found else name)
    box = Toolbox(s, Store(tmp_path / "agent.sqlite", s), data_dir=tmp_path / "data")
    write_applicant(box.data_dir)
    result = box.get_apply_setup()
    assert result["programs"]["chrome"] is found
    assert result["can_start_web"] is found
