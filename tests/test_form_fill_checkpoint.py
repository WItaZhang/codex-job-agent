"""Form-fill slice 2a: the checkpoint service (specs/form-fill.md 4.2, 4.3, 6.1 items 2, 3 and 8; slice-2a plan).

The session logic runs on a fake browser. The browser test (only when Playwright's Chromium is installed) drives a
SYNTHETIC three-step form served from 127.0.0.1 with one Playwright client, as the filling agent would, while the
service reads the same Chrome with its own connection."""

import json
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from intent_job_agent.form_fill import checkpoint
from intent_job_agent.form_fill.checkpoint import Config, Session, Worker
from intent_job_agent.form_fill.recording import Capture, read_run
from intent_job_agent.form_fill.redact import Redactor

from .test_form_fill_recording import PROFILE, leaks

URL = "https://jobs.example.test/acme/apply"


def control(order, question, value="", section="", required=False, kind="text"):
    return {
        "order": order,
        "tag": "input",
        "type": kind,
        "role": "",
        "id": f"c{order}",
        "name": f"c{order}",
        "labels": {"label_for": question},
        "value": value,
        "options": [],
        "required": required,
        "section": section,
    }


def raw(*controls, url=URL, heading="My Information", step=""):
    frame = {
        "url": url,
        "scope": "page",
        "controls": list(controls),
        "pairs": [],
        "actions": ["Next"],
        "notices": [heading],
        "step": step,
        "headings": [heading] if heading else [],
    }
    return {"url": url, "frames": [frame]}


def at(page: dict, url: str) -> dict:
    """A copy of a raw page shown at another address."""
    moved = json.loads(json.dumps(page))
    moved["url"] = url
    for frame in moved["frames"]:
        frame["url"] = url
    return moved


INFO = raw(control(1, "First Name", required=True), control(2, "Email"))
INFO_FILLED = raw(control(1, "First Name", "Sam", required=True), control(2, "Email", "sam.synthetic@example.test"))
EXPERIENCE = raw(control(1, "School"), control(2, "Degree"), heading="My Experience")
EXPERIENCE_FILLED = raw(
    control(1, "School", "Example State University"), control(2, "Degree", "MS"), heading="My Experience"
)


class FakeBrowser:
    """The applying Chrome as the service sees it: tabs, and what each tab shows now."""

    def __init__(self):
        self.list = [{"id": "T0", "type": "page", "url": "https://mail.example.test/inbox"}]
        self.pages: dict[str, dict] = {}
        self.events: list[dict] = []
        self.captures = 0
        self.delay = 0.0

    def show(self, tab: str, page: dict) -> None:
        if tab not in self.pages:
            self.list.append({"id": tab, "type": "page", "url": page["url"]})
        self.pages[tab] = page
        for item in self.list:
            if item["id"] == tab:
                item["url"] = page["url"]

    def tabs(self) -> list[dict]:
        return [dict(item) for item in self.list]

    def read(self, tab: str) -> dict:
        time.sleep(self.delay)
        return json.loads(json.dumps(self.pages[tab]))

    def capture(self, tab: str, mhtml: bool = False) -> Capture:
        self.captures += 1
        return Capture(html="<p>synthetic</p>", screenshot=b"\x89PNG", why="", mhtml=None)

    def drain_network(self, bodies=frozenset()) -> list[dict]:
        out, self.events = self.events, []
        return out

    def idle(self, seconds: float) -> None:
        time.sleep(seconds)


def config(tmp_path, **changes) -> Config:
    values = {
        "port": 9222,
        "before": ["T0"],
        "recordings_dir": str(tmp_path / "recordings"),
        "rec_id": "greenhouse-acme-1",
        "run_id": "run-1",
        "job_id": "acme-ml-01",
        "url": URL,
        "company": "Acme",
        "title": "ML Engineer",
        "profile": str(tmp_path / "profile.json"),
        "timeout_seconds": 5,
        "interval_seconds": 1,
        "stuck_after": 3,
        "ignore_filled_on_page": None,
        "network_max_mb": 30,
    }
    return Config(**{**values, **changes})


@pytest.fixture
def setup(tmp_path):
    browser = FakeBrowser()
    session = Session(config(tmp_path), browser, Redactor.from_profile(PROFILE))
    return session, browser, tmp_path


def fills(tmp_path):
    return tmp_path / "recordings" / "greenhouse-acme-1" / "fills" / "run-1"


def test_blank_returns_the_field_numbers_and_questions(setup):
    session, browser, _ = setup
    browser.show("N1", INFO)
    text = session.checkpoint("blank", URL)
    assert "p1.1 First Name (required)" in text and "p1.2 Email" in text
    assert "data" in text.lower()  # the questions are page text, not instructions


def test_filled_records_the_values(setup):
    session, browser, tmp_path = setup
    browser.show("N1", INFO)
    session.checkpoint("blank", URL)
    browser.show("N1", INFO_FILLED)
    assert "p1" in session.checkpoint("filled", URL)
    values = json.loads((fills(tmp_path) / "p1.values.json").read_text(encoding="utf-8"))
    assert [f["value"] for f in values["fields"]] == ["Sam", "[email]"]
    assert leaks(tmp_path) == []


def test_the_tab_is_found_by_its_address_among_the_new_tabs(setup):
    session, browser, tmp_path = setup
    browser.show("N1", INFO)
    browser.show("N2", at(EXPERIENCE, "https://sso.example.test/login"))
    session.checkpoint("blank", "https://sso.example.test/login")
    logged = json.loads((fills(tmp_path) / "checkpoints.json").read_text(encoding="utf-8"))
    assert logged[-1]["tab_url"] == logged[-1]["agent_url"] == "https://sso.example.test/login"
    assert logged[-1]["tab"] == "N2"


def test_an_unknown_address_falls_back_to_the_last_tab(setup):
    session, browser, tmp_path = setup
    browser.show("N1", INFO)
    browser.show("N2", at(INFO, "https://jobs.example.test/other"))
    session.checkpoint("blank", URL)
    session.checkpoint("filled", "https://jobs.example.test/somewhere-else")
    logged = json.loads((fills(tmp_path) / "checkpoints.json").read_text(encoding="utf-8"))
    assert logged[-1]["tab"] == "N1"
    assert logged[-1]["tab_url"] != logged[-1]["agent_url"]  # check 2 shows the mismatch


def test_no_job_tab_is_said_and_logged(setup):
    session, _, tmp_path = setup
    text = session.checkpoint("blank", URL)
    assert "could not find" in text.lower()
    assert json.loads((fills(tmp_path) / "checkpoints.json").read_text())[-1]["result"] == "no_tab"


def test_a_page_with_no_fields_records_nothing(setup):
    session, browser, _ = setup
    browser.show("N1", raw(heading="About the job"))
    assert "no form fields" in session.checkpoint("blank", URL).lower()


def test_three_filled_on_one_page_say_stop(setup):
    session, browser, _ = setup
    browser.show("N1", INFO_FILLED)
    assert "STOP" not in session.checkpoint("filled", URL)
    assert "STOP" not in session.checkpoint("filled", URL)
    assert "RESULT:FAILED:stuck" in session.checkpoint("filled", URL)


def test_a_filled_call_on_the_ignored_page_is_answered_but_not_recorded(tmp_path):
    browser = FakeBrowser()
    session = Session(config(tmp_path, ignore_filled_on_page=2), browser, Redactor.from_profile(PROFILE))
    browser.show("N1", INFO)
    session.checkpoint("blank", URL)
    browser.show("N1", INFO_FILLED)
    session.checkpoint("filled", URL)
    browser.show("N1", EXPERIENCE)
    session.checkpoint("blank", URL)
    browser.show("N1", EXPERIENCE_FILLED)
    assert "p2" in session.checkpoint("filled", URL)  # answered as usual
    assert not (fills(tmp_path) / "p2.values.json").exists()
    assert json.loads((fills(tmp_path) / "checkpoints.json").read_text())[-1]["result"] == "ignored"


def test_the_background_read_keeps_the_values_from_before_the_page_moved_on(setup):
    session, browser, tmp_path = setup
    browser.show("N1", EXPERIENCE)
    session.checkpoint("blank", URL)
    browser.show("N1", EXPERIENCE_FILLED)
    session.poll()  # the agent filled the page and moves on without checkpoint("filled")
    browser.show("N1", INFO)
    session.poll()
    pages = read_run(tmp_path / "recordings" / "greenhouse-acme-1", "run-1")
    assert (pages[0].read_from, [f.value for f in pages[0].fields]) == ("auto", ["Example State University", "MS"])


def test_the_background_read_records_first_page_requests_without_cookies(setup):
    session, browser, tmp_path = setup
    browser.show("N1", INFO)
    browser.events = [
        {
            "tab": "N1",
            "method": "GET",
            "url": "https://jobs.example.test/app.js",
            "status": 200,
            "headers": {"content-type": "application/javascript", "set-cookie": "sid=1"},
            "body": b"var a = 1;",
        },
        {
            "tab": "N1",
            "method": "POST",
            "url": "https://jobs.example.test/api/draft?x=1",
            "status": 200,
            "headers": {},
            "body": b"",
        },
        {
            "tab": "T0",
            "method": "GET",
            "url": "https://mail.example.test/inbox.js",
            "status": 200,
            "headers": {},
            "body": b"mail",
        },
    ]
    session.poll()
    form = tmp_path / "recordings" / "greenhouse-acme-1" / "form"
    lines = [json.loads(line) for line in (form / "network.partial.jsonl").read_text().splitlines()]
    assert [entry["url"] for entry in lines] == ["https://jobs.example.test/app.js"]  # not the mail tab's
    assert "set-cookie" not in lines[0]["headers"]
    assert json.loads((fills(tmp_path) / "requests.json").read_text()) == ["POST https://jobs.example.test/api/draft"]


def test_a_slow_checkpoint_answers_within_its_time_limit(tmp_path):
    browser = FakeBrowser()
    browser.show("N1", INFO)
    browser.delay = 0.5
    session = Session(config(tmp_path, timeout_seconds=0.2), browser, Redactor.from_profile(PROFILE))
    worker = Worker(lambda: session, interval=0.05, timeout=0.2)
    worker.start()
    try:
        started = time.monotonic()
        text = worker.checkpoint("blank", URL)
        assert time.monotonic() - started < 0.45
        assert text == checkpoint.TIMEOUT_TEXT
    finally:
        worker.stop()


def test_the_worker_polls_between_checkpoints(tmp_path):
    browser = FakeBrowser()
    browser.show("N1", INFO_FILLED)
    session = Session(config(tmp_path), browser, Redactor.from_profile(PROFILE))
    worker = Worker(lambda: session, interval=0.02, timeout=2)
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while not (fills(tmp_path) / "p1.auto.json").exists():
            assert time.monotonic() < deadline, "no background read"
            time.sleep(0.02)
        assert "p1.1" in worker.checkpoint("blank", URL)
    finally:
        worker.stop()


def test_the_service_offers_only_the_checkpoint_tool(tmp_path):
    import asyncio

    class Stub:
        def checkpoint(self, stage, url):
            return f"{stage} {url}"

    server = checkpoint.build_server(Stub())
    tools = asyncio.run(server.list_tools())
    assert [tool.name for tool in tools] == ["checkpoint"]
    schema = tools[0].input_schema
    assert set(schema["properties"]) == {"stage", "url"}
    assert schema["properties"]["stage"].get("enum") == ["blank", "filled"] or "anyOf" in schema["properties"]["stage"]


def test_the_server_entry_runs_this_module_with_the_config_file(tmp_path):
    entry = checkpoint.server_entry(tmp_path / "checkpoint.json")
    assert entry["args"][-3:] == ["-m", "intent_job_agent.form_fill.checkpoint", str(tmp_path / "checkpoint.json")]


# --- a real browser ---------------------------------------------------------------------------------------------

STEPS = {
    "/step1": """<!doctype html><html><head><script src="/app.js"></script></head><body>
      <h1>My Information</h1><form action="/step2" method="get">
      <label for="fn">First Name</label><input id="fn" name="fn" required>
      <label for="em">Email</label><input id="em" name="em">
      <button type="submit">Next</button></form></body></html>""",
    "/step2": """<!doctype html><html><body><h1>My Experience</h1><form action="/step3" method="get">
      <label for="sc">School</label><input id="sc" name="sc">
      <label for="dg">Degree</label><select id="dg" name="dg"><option value="">Select One</option>
        <option>Master of Science</option></select>
      <button type="submit">Next</button></form></body></html>""",
    "/step3": """<!doctype html><html><body><h1>Voluntary Disclosures</h1><form>
      <label for="ge">Gender</label><select id="ge"><option value="">Select One</option><option>Female</option></select>
      <button type="button">Review</button></form></body></html>""",
    "/app.js": "window.synthetic = 1;",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the stdlib's name
        path = self.path.split("?", 1)[0]
        body = STEPS.get(path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript" if path.endswith(".js") else "text/html")
        self.send_header("Set-Cookie", "sid=synthetic")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def site():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_the_service_records_a_three_step_form_in_a_real_browser(tmp_path, site):
    sync_api = pytest.importorskip("playwright.sync_api")
    port = free_port()
    with sync_api.sync_playwright() as p:
        try:  # a persistent context: the agent's tabs live in Chrome's default context, as in the applying Chrome
            browser = p.chromium.launch_persistent_context(
                str(tmp_path / "chrome"), headless=True, args=[f"--remote-debugging-port={port}"]
            )
        except Exception as exc:  # no browser installed
            pytest.skip(f"Chromium not available: {exc}")
        context = browser
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list") as answer:
            before = [t["id"] for t in json.load(answer)]
        redactor = Redactor.from_profile(PROFILE)
        cfg = config(tmp_path, port=port, before=before, url=f"{site}/step1", interval_seconds=0.2)
        worker = Worker(
            lambda: Session(cfg, checkpoint.LiveBrowser(port, redactor), redactor), interval=0.2, timeout=10
        )
        worker.start()
        try:
            agent = context.new_page()  # the filling agent's tab, opened after the service connected
            agent.goto(f"{site}/step1")
            assert "p1.1 First Name" in worker.checkpoint("blank", agent.url)
            agent.fill("#fn", "Sam")
            agent.fill("#em", "sam.synthetic@example.test")
            worker.checkpoint("filled", agent.url)
            agent.click("text=Next")
            agent.wait_for_url("**/step2**")
            worker.checkpoint("blank", agent.url)
            agent.fill("#sc", "Example State University")
            agent.select_option("#dg", "Master of Science")
            worker.poll_now()  # step 2 is left without checkpoint("filled")
            agent.click("text=Next")
            agent.wait_for_url("**/step3**")
            worker.checkpoint("blank", agent.url)
            worker.poll_now()
        finally:
            worker.stop()
        browser.close()
    rec = tmp_path / "recordings" / "greenhouse-acme-1"
    pages = read_run(rec, "run-1")
    assert [(p.page, p.read_from) for p in pages] == [(1, "filled"), (2, "auto"), (3, "auto")]
    assert [f.value for f in pages[0].fields] == ["Sam", "[email]"]
    assert [f.value for f in pages[1].fields] == ["Example State University", "Master of Science"]
    logged = json.loads((rec / "fills" / "run-1" / "checkpoints.json").read_text())
    assert all(entry["tab_url"] == entry["agent_url"] for entry in logged)
    assert all(entry["seconds"] < 5 for entry in logged)
    network = (rec / "form" / "network.partial.jsonl").read_text()
    assert "/app.js" in network and "sid=synthetic" not in network
    assert (rec / "form" / "p1.png").is_file() and (rec / "form" / "p1.html").is_file()
    assert leaks(rec) == []  # the browser's own profile folder is not the recording's
