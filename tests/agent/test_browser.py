"""Real Chromium against a local fake ATS; no public jobs or accounts are touched."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from applypilot_agent.browser import BrowserError, BrowserField, BrowserPlan, BrowserSession


@pytest.fixture(scope="module")
def ats():
    state = {"posts": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            state["posts"] += 1
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def do_GET(self):
            mode = urlsplit(self.path).path.strip("/")
            required = '<label>Portfolio<input id="portfolio" required></label>' if mode == "extra" else ""
            if mode == "radio":
                required = """<fieldset><legend>Work mode</legend>
                  <label><input type="radio" name="work[mode]" value="remote" required>Remote</label>
                  <label><input type="radio" name="work[mode]" value="onsite" required>Onsite</label>
                </fieldset>
                <label><input type="checkbox" name="languages" value="Python">Python</label>
                <label><input type="checkbox" name="languages" value="C++">C++</label>"""
            receipt = (
                '<div id="receipt">Application received</div>'
                if mode == "existing"
                else '<div id="receipt" hidden></div>'
            )
            delay = "350" if mode == "delayed" else "0"
            receipt_script = (
                ""
                if mode in {"missing", "url-only"}
                else (
                    "setTimeout(() => {const r=document.querySelector('#receipt'); "
                    f"r.hidden=false; r.textContent='Application received: REF-123';}}, {delay});"
                )
            )
            if mode == "url-only":
                receipt_script = "history.pushState({}, '', '/thanks');"
            if mode == "negative-receipt":
                receipt_script = receipt_script.replace(
                    "Application received: REF-123", "Application received but processing failed"
                )
            if mode == "button-receipt":
                receipt_script = "document.querySelector('#submit').textContent='Application received';"
            background = (
                "document.querySelector('#name').addEventListener('input', () => fetch('/submit', {method:'POST'}).catch(()=>{}));"
                if mode == "autosubmit"
                else ""
            )
            disabled = "disabled" if mode == "disabled-button" else ""
            body = f"""<!doctype html><html><head><title>Local ATS</title></head><body>
            <form id="application">
              <label for="name">Full name</label><input id="name" name="full_name" required>
              <label for="region">Region</label><select id="region" required>
                <option value="">Choose</option><option value="remote">Remote</option>
              </select>
              <label><input id="consent" type="checkbox" required>Confirm facts</label>
              <label for="resume">Resume</label><input id="resume" type="file" required>
              {required}<button id="submit" type="submit" {disabled}>Submit application</button>
            </form>{receipt}
            <script>
              document.querySelector('#application').addEventListener('submit', async e => {{
                e.preventDefault();
                await fetch('/submit', {{method:'POST', body: new FormData(e.target)}});
                {receipt_script}
              }});
              {background}
            </script></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.fixture
def resume(tmp_path):
    path = tmp_path / "resume.txt"
    path.write_text("Confirmed applicant facts", encoding="utf-8")
    return path


def plan(url, resume):
    return BrowserPlan(
        url=url,
        fields=[
            BrowserField(selector="#name", kind="text", value="Alex Applicant"),
            BrowserField(selector="#region", kind="select", value="remote"),
            BrowserField(selector="#consent", kind="checkbox", value=True),
            BrowserField(selector="#resume", kind="file", value=str(resume)),
        ],
        submit_selector="#submit",
        confirmation_selector="#receipt",
        confirmation_text="Application received",
    )


def test_inspect_prepare_delayed_receipt_and_upload(ats, resume, tmp_path):
    base, state = ats
    initial_posts = state["posts"]
    instruction = plan(base + "/delayed", resume)
    with BrowserSession(tmp_path / "evidence", timeout_ms=2000) as browser:
        inspection = browser.inspect(instruction.url)
        assert inspection["title"] == "Local ATS"
        assert inspection["fields"][0]["label"] == "Full name"
        assert inspection["fields"][1]["options"][1] == {"value": "remote", "label": "Remote", "disabled": False}
        assert state["posts"] == initial_posts
        prepared = browser.prepare(instruction)
        assert prepared["status"] == "prepared"
        assert state["posts"] == initial_posts
        selected = browser.page.locator("#resume").evaluate("e => e.files[0].name")
        assert selected == resume.name
        result = browser.submit(instruction)
        assert result["status"] == "submitted"
        assert result["receipt"]["text"] == "Application received: REF-123"
        assert result["receipt"]["observed_at"]
        assert state["posts"] == initial_posts + 1
        for key in ("before_screenshot", "after_screenshot", "evidence_path"):
            assert Path(result[key]).is_file()
        assert json.loads(Path(result["evidence_path"]).read_text())["status"] == "submitted"
        with pytest.raises(BrowserError, match="already attempted"):
            browser.submit(instruction)
        assert state["posts"] == initial_posts + 1


@pytest.mark.parametrize("mode", ["missing", "url-only"])
def test_no_receipt_is_unknown_without_retry(ats, resume, tmp_path, mode):
    base, state = ats
    initial_posts = state["posts"]
    instruction = plan(base + "/" + mode, resume)
    with BrowserSession(tmp_path, timeout_ms=500) as browser:
        browser.prepare(instruction)
        result = browser.submit(instruction)
        assert result["status"] == "unknown"
        assert result["receipt"] is None
        assert state["posts"] == initial_posts + 1
        with pytest.raises(BrowserError) as caught:
            browser.submit(instruction)
        assert caught.value.code == "attempt_consumed"


def test_unexpected_mandatory_field_blocks_preparation(ats, resume, tmp_path):
    base, state = ats
    initial_posts = state["posts"]
    with BrowserSession(tmp_path) as browser:
        with pytest.raises(BrowserError) as caught:
            browser.prepare(plan(base + "/extra", resume))
        assert caught.value.code == "unplanned_required_field"
        assert caught.value.details["fields"][0]["id"] == "portfolio"
        assert browser.page.locator("#name").input_value() == ""
        assert state["posts"] == initial_posts


def test_existing_confirmation_blocks_submission(ats, resume, tmp_path):
    base, state = ats
    initial_posts = state["posts"]
    instruction = plan(base + "/existing", resume)
    with BrowserSession(tmp_path) as browser:
        browser.prepare(instruction)
        with pytest.raises(BrowserError) as caught:
            browser.submit(instruction)
        assert caught.value.code == "preexisting_confirmation"
        assert state["posts"] == initial_posts


@pytest.mark.parametrize("change", ["value", "file", "required", "page"])
def test_recheck_before_click(ats, resume, tmp_path, change):
    base, state = ats
    initial_posts = state["posts"]
    instruction = plan(base + "/normal", resume)
    with BrowserSession(tmp_path) as browser:
        browser.prepare(instruction)
        if change == "value":
            browser.page.locator("#name").fill("Changed name")
        elif change == "file":
            resume.write_text("X" * len(resume.read_text()))
        elif change == "required":
            browser.page.evaluate(
                "() => { const f=document.createElement('input'); f.required=true; f.id='new'; document.querySelector('form').append(f); }"
            )
        else:
            browser.page.evaluate("() => history.pushState({}, '', '/another-job')")
        with pytest.raises(BrowserError) as caught:
            browser.submit(instruction)
        assert (
            caught.value.code
            == {
                "value": "value_changed",
                "file": "attachment_changed",
                "required": "unplanned_required_field",
                "page": "page_changed",
            }[change]
        )
        assert state["posts"] == initial_posts


def test_click_timeout_consumes_attempt(ats, resume, tmp_path):
    base, state = ats
    initial_posts = state["posts"]
    instruction = plan(base + "/disabled-button", resume)
    with BrowserSession(tmp_path, timeout_ms=350) as browser:
        browser.prepare(instruction)
        result = browser.submit(instruction)
        assert result["status"] == "unknown"
        assert result["reason"] == "submission_observation_failed"
        assert state["posts"] == initial_posts
        with pytest.raises(BrowserError) as caught:
            browser.submit(instruction)
        assert caught.value.code == "attempt_consumed"


def test_changed_plan_and_unprepared_plan_cannot_submit(ats, resume, tmp_path):
    base, _ = ats
    instruction = plan(base + "/normal", resume)
    with BrowserSession(tmp_path) as browser:
        with pytest.raises(BrowserError) as caught:
            browser.submit(instruction)
        assert caught.value.code == "plan_not_prepared"
        browser.prepare(instruction)
        changed = instruction.model_copy(update={"confirmation_text": "Something else"})
        with pytest.raises(BrowserError) as caught:
            browser.submit(changed)
        assert caught.value.code == "plan_not_prepared"


def test_background_writes_blocked_before_submit(ats, resume, tmp_path):
    base, state = ats
    initial_posts = state["posts"]
    with BrowserSession(tmp_path) as browser:
        with pytest.raises(BrowserError) as caught:
            browser.prepare(plan(base + "/autosubmit", resume))
        assert caught.value.code == "unsupported_background_write"
        assert state["posts"] == initial_posts


def test_origin_restriction(ats, tmp_path):
    base, _ = ats
    with BrowserSession(tmp_path, allowed_origins=["https://example.invalid"]) as browser:
        with pytest.raises(BrowserError) as caught:
            browser.inspect(base)
        assert caught.value.code == "origin_not_allowed"


@pytest.mark.parametrize("mode", ["negative-receipt", "button-receipt"])
def test_progress_error_and_control_text_are_not_receipts(ats, resume, tmp_path, mode):
    base, _ = ats
    instruction = plan(base + "/" + mode, resume)
    if mode == "button-receipt":
        instruction = instruction.model_copy(update={"confirmation_selector": "#submit"})
    with BrowserSession(tmp_path, timeout_ms=500) as browser:
        browser.prepare(instruction)
        result = browser.submit(instruction)
        assert result["status"] == "unknown"
        assert result["receipt"] is None


@pytest.mark.parametrize("expected", ["submitted", "Submitting...", "Application not submitted", "Application pending"])
def test_vague_or_negative_confirmation_contract_blocks_click(ats, resume, tmp_path, expected):
    base, state = ats
    initial_posts = state["posts"]
    instruction = plan(base + "/normal", resume).model_copy(update={"confirmation_text": expected})
    with BrowserSession(tmp_path) as browser:
        browser.prepare(instruction)
        with pytest.raises(BrowserError) as caught:
            browser.submit(instruction)
        assert caught.value.code == "unsupported_confirmation"
        assert state["posts"] == initial_posts


def test_prefilled_optional_answers_are_not_silently_submitted(ats, resume, tmp_path):
    base, state = ats
    initial_posts = state["posts"]
    instruction = plan(base + "/normal", resume)
    with BrowserSession(tmp_path) as browser:
        browser.prepare(instruction)
        browser.page.evaluate("""() => {
            const checkbox = document.createElement('input');
            checkbox.type='checkbox'; checkbox.checked=true; checkbox.id='marketing';
            document.querySelector('form').append(checkbox);
        }""")
        with pytest.raises(BrowserError) as caught:
            browser.submit(instruction)
        assert caught.value.code == "unplanned_prefilled_field"
        assert state["posts"] == initial_posts


def test_plan_validates_data_types():
    with pytest.raises(ValidationError):
        BrowserField(selector="#check", kind="checkbox", value="true")
    with pytest.raises(ValidationError):
        BrowserPlan(url="javascript:alert(1)")
    with pytest.raises(ValidationError):
        BrowserPlan(url="https://username:secret@example.com")
    field = BrowserField(selector="#name", kind="text", value="Alex")
    with pytest.raises(ValidationError):
        BrowserPlan(url="https://example.com", fields=[field, field])
    with pytest.raises(ValidationError):
        BrowserField(selector="#radio", kind="radio", value=False)


def test_native_required_radio_group_and_choice_selectors(ats, resume, tmp_path):
    base, state = ats
    initial_posts = state["posts"]
    instruction = plan(base + "/radio", resume)
    with BrowserSession(tmp_path, timeout_ms=1500) as browser:
        observed = browser.inspect(instruction.url)
        choices = {f["choice_value"]: f for f in observed["fields"] if f["type"] == "radio"}
        assert set(choices) == {"remote", "onsite"}
        assert all(f["selector"] for f in choices.values())
        checkbox = next(f for f in observed["fields"] if f["choice_value"] == "C++")
        assert browser.page.locator(checkbox["selector"]).count() == 1
        chosen = BrowserField(selector=choices["remote"]["selector"], kind="radio", value=True)
        instruction = instruction.model_copy(update={"fields": (*instruction.fields, chosen)})
        browser.prepare(instruction)
        assert browser.page.locator(choices["remote"]["selector"]).is_checked()
        assert not browser.page.locator(choices["onsite"]["selector"]).is_checked()
        assert state["posts"] == initial_posts
        assert browser.submit(instruction)["status"] == "submitted"


def test_conflicting_radio_group_choices_rejected_before_filling(ats, resume, tmp_path):
    base, state = ats
    initial_posts = state["posts"]
    instruction = plan(base + "/radio", resume)
    with BrowserSession(tmp_path) as browser:
        observed = browser.inspect(instruction.url)
        radios = [
            BrowserField(selector=f["selector"], kind="radio", value=True)
            for f in observed["fields"]
            if f["type"] == "radio"
        ]
        instruction = instruction.model_copy(update={"fields": (*instruction.fields, *radios)})
        with pytest.raises(BrowserError) as caught:
            browser.prepare(instruction)
        assert caught.value.code == "conflicting_radio_choices"
        assert browser.page.locator("#name").input_value() == ""
        assert state["posts"] == initial_posts
