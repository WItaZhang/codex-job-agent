"""Form-fill slice 1: reading back the final page (specs/form-fill.plan.md, sections 1 and 5).

All pages and values are SYNTHETIC. The raw snapshots under tests/fixtures/form_fill/pages/ have EXTRACT_JS's shape.
The browser tests run only when Playwright's Chromium is installed; they load local HTML and never use the network."""

import base64
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from intent_job_agent.domain import InvariantError
from intent_job_agent.form_fill import readback
from intent_job_agent.form_fill.models import ReadField
from intent_job_agent.form_fill.readback import find_job_tab, list_targets, normalize

PAGES = Path(__file__).resolve().parent / "fixtures/form_fill/pages"
EMAIL = "sam.synthetic@example.test"


def page(name: str) -> dict:
    raw = json.loads((PAGES / f"{name}.json").read_text(encoding="utf-8"))
    assert raw["synthetic"] is True
    return raw


def field(n, question, control, value, options=(), required=False):
    origin = "review_text" if control == "review" else "control"
    return ReadField(
        id=f"p1.{n}",
        question=question,
        origin=origin,
        control=control,
        value=value,
        options=tuple(options),
        required=required,
    )


# --- normalize: three page shapes ------------------------------------------------------------------------------


def test_single_page_form_reads_every_control():
    assert normalize(page("greenhouse_like_single_page")) == (
        field(1, "Location", "review", "Austin, TX"),
        field(2, "First Name", "text", "Sam", required=True),
        field(3, "Last Name", "text", "Example", required=True),
        field(4, "Email", "text", EMAIL, required=True),
        field(5, "Resume/CV", "file", "resume_synthetic.pdf", required=True),
        field(
            6,
            "Are you legally authorized to work in the United States?",
            "select",
            "Yes",
            ("Yes", "No"),
            required=True,
        ),
        field(
            7,
            "Will you now or in the future require sponsorship for employment visa status?",
            "radio",
            "No",
            ("Yes", "No"),
            required=True,
        ),
        field(
            8,
            "Which offices could you work from?",
            "checkbox",
            "Austin; Remote (US)",
            ("Austin", "Remote (US)", "New York"),
        ),
        field(9, "Why do you want to work here?", "textarea", "Because the team builds developer tools."),
        field(10, "LinkedIn Profile", "text", ""),
        field(11, "I agree to the synthetic privacy notice", "checkbox", "Yes"),
    )


def test_review_page_reads_question_value_pairs():
    assert normalize(page("workday_like_review")) == (
        field(1, "How Did You Hear About Us?", "review", "Company Website"),
        field(2, "Have you previously worked for Acme Synthetic?", "review", "No"),
        field(3, "Legal Name", "review", "Sam Example"),
        field(4, "Email Address", "review", EMAIL),
        field(5, "Phone Number", "review", "555-0100"),
        field(6, "School or University", "review", "Synthetic State University"),
        field(7, "Degree", "review", "Master's Degree"),
        field(8, "From", "review", "08/2025"),
        field(9, "Are you legally authorized to work in the United States?", "review", "Yes"),
        field(10, "Will you now or in the future require sponsorship?", "review", "No"),
        field(11, "Resume/CV", "review", "resume_synthetic.pdf"),
    )


def test_last_step_mixes_controls_and_review_pairs():
    assert normalize(page("linkedin_like_last_step")) == (
        field(1, "Mobile phone number", "review", "555-0100"),
        field(2, "Resume", "review", "resume_synthetic.pdf"),
        field(3, "Email address", "select", EMAIL, ("Select an option", EMAIL), required=True),
        field(4, "Location (city)", "combobox", "Austin, Texas, United States", required=True),
        field(5, "How many years of work experience do you have with Python?", "text", "3", required=True),
        field(6, "Are you comfortable commuting to this job's location?", "radio", "Yes", ("Yes", "No"), required=True),
        field(7, "Do you have a Master's degree?", "radio", "Yes", ("Yes", "No")),
        field(8, "Follow Acme Synthetic to stay up to date with their page.", "checkbox", ""),
    )


# --- normalize: rules ------------------------------------------------------------------------------------------


def control(order, **kw):
    node = {"order": order, "tag": "input", "type": "text", "role": "", "id": "", "name": "", "value": ""}
    node["labels"] = kw.pop("labels", {})
    node.update(kw)
    return node


def one_frame(controls=(), pairs=()):
    return {"frames": [{"url": "https://synthetic.example.test/", "controls": list(controls), "pairs": list(pairs)}]}


def test_question_priority_follows_label_sources():
    every = {
        "label_for": "A",
        "labelledby": "B",
        "aria_label": "C",
        "wrapping": "D",
        "legend": "E",
        "preceding": "F",
        "placeholder": "G",
    }
    order = ["label_for", "labelledby", "aria_label", "wrapping", "legend", "preceding", "placeholder"]
    for i, source in enumerate(order):
        labels = {k: v for k, v in every.items() if k not in order[:i]}
        (got,) = normalize(one_frame([control(1, labels=labels)]))
        assert got.question == every[source], source
    (none,) = normalize(one_frame([control(1)]))
    assert none.question == ""


def test_radios_group_by_group_key_and_take_the_group_question():
    radios = [
        control(
            3,
            type="radio",
            group="c:7",
            labels={"label_for": "Yes", "preceding": "Do you have a degree?"},
            group_labels={"labelledby": "", "aria_label": "Degree question"},
            option_label="Yes",
            checked=False,
        ),
        control(
            4,
            type="radio",
            group="c:7",
            labels={"label_for": "No", "preceding": "Yes"},
            group_labels={"labelledby": "", "aria_label": "Degree question"},
            option_label="No",
            checked=False,
            required=True,
        ),
    ]
    assert normalize(one_frame(radios)) == (field(1, "Degree question", "radio", "", ("Yes", "No"), required=True),)


def test_password_and_combobox_display():
    nodes = [
        control(1, type="password", labels={"label_for": "Create password"}, value="[已填]"),
        control(2, role="combobox", labels={"label_for": "Country"}, value="", display="United States"),
        control(
            3,
            tag="div",
            type="",
            role="listbox",
            labels={"aria_label": "Skills"},
            value="",
            options=[
                {"text": "Python", "value": "", "selected": True},
                {"text": "Go", "value": "", "selected": False},
            ],
        ),
        control(4, tag="div", type="", editable=True, labels={"preceding": "Cover letter"}, value="Dear team"),
    ]
    assert normalize(one_frame(nodes)) == (
        field(1, "Create password", "text", "[已填]"),
        field(2, "Country", "combobox", "United States"),
        field(3, "Skills", "select", "Python", ("Python", "Go")),
        field(4, "Cover letter", "textarea", "Dear team"),
    )


def test_review_pairs_repeating_a_control_are_dropped_after_cleanup():
    nodes = [control(5, labels={"label_for": "Phone *"}, value="555-0100")]
    pairs = [
        {"order": 9, "question": "  phone: ", "value": "555-0100", "source": "label"},
        {"order": 10, "question": "Phone:", "value": "555-0199", "source": "label"},
    ]
    assert normalize(one_frame(nodes, pairs)) == (
        field(1, "Phone", "text", "555-0100", required=True),
        field(2, "Phone", "review", "555-0199"),
    )


def test_frames_are_read_in_order_and_failed_frames_skipped():
    raw = {
        "frames": [
            {"url": "https://a.synthetic.example.test/", "controls": [control(50, labels={"label_for": "Main"})]},
            {"url": "https://b.synthetic.example.test/", "error": "cross-origin"},
            {"url": "https://c.synthetic.example.test/", "controls": [control(1, labels={"label_for": "Inner"})]},
        ]
    }
    assert [f.question for f in normalize(raw)] == ["Main", "Inner"]


@pytest.mark.parametrize("bad", [None, [], {}, {"frames": "x"}, {"frames": [{"controls": "x"}]}, {"frames": [1]}])
def test_malformed_raw_is_rejected(bad):
    with pytest.raises(InvariantError):
        normalize(bad)


# --- find_job_tab ----------------------------------------------------------------------------------------------


def tab(tid, url, kind="page"):
    return {"id": tid, "type": kind, "url": url}


BEFORE = [
    tab("A", "https://mail.synthetic.example.test/inbox"),
    tab("X", "chrome-extension://abc/background.html", "background_page"),
]


def test_one_new_page_is_the_job_tab():
    new = tab("B", "https://boards.synthetic.example.test/acme/jobs/1")
    assert find_job_tab(BEFORE, [*BEFORE, new], None) == new


def test_several_new_pages_pick_last_url():
    one = tab("B", "https://boards.synthetic.example.test/acme/jobs/1")
    two = tab("C", "https://synthetic.wd1.example.test/en-US/careers/job/1/apply")
    after = [*BEFORE, one, two]
    assert find_job_tab(BEFORE, after, two["url"]) == two
    assert find_job_tab(BEFORE, after, None) is None
    assert find_job_tab(BEFORE, after, "https://elsewhere.example.test/") is None


def test_popup_on_same_host_and_path_prefix():
    job = tab("B", "https://synthetic.wd1.example.test/en-US/careers/job/1/apply/step2")
    other = tab("C", "https://news.synthetic.example.test/en-US/today")
    last = "https://synthetic.wd1.example.test/en-US/careers/job/1/apply"
    assert find_job_tab(BEFORE, [*BEFORE, job, other], last) == job


def test_no_new_page_falls_back_to_last_url():
    job = tab("B", "https://boards.synthetic.example.test/acme/jobs/1")
    before = [*BEFORE, job]
    assert find_job_tab(before, before, job["url"]) == job
    assert find_job_tab(before, before, None) is None
    assert find_job_tab(before, BEFORE, job["url"]) is None  # the job tab was closed


def test_extension_and_devtools_targets_are_ignored():
    after = [
        *BEFORE,
        tab("D", "devtools://devtools/bundled/inspector.html"),
        tab("E", "chrome-extension://abc/popup.html"),
        tab("F", "https://boards.synthetic.example.test/acme/jobs/1", "iframe"),
        tab("G", "https://boards.synthetic.example.test/sw.js", "service_worker"),
    ]
    assert find_job_tab(BEFORE, after, None) is None
    job = tab("B", "https://boards.synthetic.example.test/acme/jobs/1")
    assert find_job_tab(BEFORE, [*after, job], None) == job


# --- list_targets against a local stand-in for Chrome's /json/list ---------------------------------------------


def test_list_targets_reads_json_list():
    targets = [tab("A", "https://synthetic.example.test/")]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(targets if self.path == "/json/list" else {}).encode()
            self.send_response(200 if self.path == "/json/list" else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert list_targets(server.server_address[1]) == targets
    finally:
        server.shutdown()


# --- real browser (skipped without Playwright's Chromium) ------------------------------------------------------

FORM_HTML = """<!doctype html><html><head><title>Synthetic form</title>
<style>.sr {position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}</style>
<script>window.synthetic = 1;</script></head><body>
<h1>Apply: Synthetic Engineer</h1>
<form>
  <label for="fn">First Name <span>*</span></label><input id="fn" name="fn" required>
  <label>Email <input id="em" type="email" aria-required="true"></label>
  <input type="hidden" name="token" value="secret-token">
  <input type="password" id="pw" aria-label="Create password">
  <label for="auth">Are you legally authorized to work in the United States?</label>
  <select id="auth"><option value="">Select...</option>
    <option value="1">Yes</option><option value="0">No</option></select>
  <fieldset><legend>Will you require sponsorship?</legend>
    <label><input type="radio" name="sp" value="y" class="sr"> Yes</label>
    <label><input type="radio" name="sp" value="n" class="sr"> No</label>
  </fieldset>
  <p>Resume/CV</p><input type="file" id="cv" style="display:none"><button type="button">Attach</button>
  <span id="whyq">Why us?</span><textarea id="why" aria-labelledby="whyq"></textarea>
  <div style="display:none"><label for="ghost">Ghost</label><input id="ghost"></div>
  <input id="city" role="combobox" aria-label="City">
  <div role="radiogroup" aria-label="Remote?">
    <div role="radio" aria-checked="false">Yes</div><div role="radio" aria-checked="true">No</div>
  </div>
  <label><input type="checkbox" id="agree"> I agree</label>
  <input type="submit" value="Submit application">
</form>
<dl><dt>Phone</dt><dd>555-0100</dd></dl>
<table><tr><th>Degree</th><td>Master's Degree</td></tr></table>
<div><span>Start date:</span><span>2026-12-14</span></div>
<div><span>Empty:</span><span></span></div>
</body></html>"""

EXPECTED_BROWSER = (
    field(1, "First Name", "text", "Sam", required=True),
    field(2, "Email", "text", EMAIL, required=True),
    field(3, "Create password", "text", "[已填]"),
    field(4, "Are you legally authorized to work in the United States?", "select", "Yes", ("Yes", "No")),
    field(5, "Will you require sponsorship?", "radio", "No", ("Yes", "No")),
    field(6, "Resume/CV", "file", ""),
    field(7, "Why us?", "textarea", "Because tools"),
    field(8, "City", "combobox", "Austin"),
    field(9, "Remote?", "radio", "No", ("Yes", "No")),
    field(10, "I agree", "checkbox", "Yes"),
    field(11, "Phone", "review", "555-0100"),
    field(12, "Degree", "review", "Master's Degree"),
    field(13, "Start date", "review", "2026-12-14"),
)


def fill_synthetic(pg):
    pg.fill("#fn", "Sam")
    pg.fill("#em", EMAIL)
    pg.fill("#pw", "synthetic-password-1")
    pg.select_option("#auth", "1")
    pg.check("input[name=sp][value=n]", force=True)
    pg.fill("#why", "Because   tools")
    pg.fill("#city", "Austin")
    pg.check("#agree")


@pytest.fixture(scope="module")
def chromium():
    sync_api = pytest.importorskip("playwright.sync_api")
    port = free_port()
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, args=[f"--remote-debugging-port={port}"])
        except Exception as exc:  # no browser installed
            pytest.skip(f"Chromium not available: {exc}")
        page = browser.new_page()
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith("data:") else route.abort())
        yield browser, page, port
        browser.close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_extract_js_in_a_real_browser(chromium):
    _, pg, _ = chromium
    pg.set_content(FORM_HTML)
    fill_synthetic(pg)
    frame = pg.evaluate(readback.EXTRACT_JS)
    assert "secret-token" not in json.dumps(frame)
    assert "synthetic-password-1" not in json.dumps(frame)
    assert normalize({"frames": [frame]}) == EXPECTED_BROWSER


class StandInRedactor:
    """Masks the synthetic email only; the real redactor lives in redact.py."""

    def mask(self, text: str) -> str:
        return text.replace(EMAIL, "[email]")

    def mask_json(self, obj):
        return json.loads(self.mask(json.dumps(obj, ensure_ascii=False)))


def test_read_final_page_over_cdp_leaves_the_page_open(chromium, tmp_path):
    _, pg, port = chromium
    url = "data:text/html;base64," + base64.b64encode(FORM_HTML.encode()).decode()
    pg.goto(url)
    fill_synthetic(pg)
    before_url = pg.url
    targets = list_targets(port)
    target = find_job_tab([], targets, before_url)
    assert target is not None

    fields = readback.read_final_page(port, target, tmp_path, StandInRedactor())

    assert fields == EXPECTED_BROWSER
    assert not pg.is_closed() and pg.url == before_url
    assert pg.input_value("#fn") == "Sam"  # nothing was cleared or retyped
    assert {p.name for p in tmp_path.iterdir()} == {"page.html", "screenshot.png", "readback.json", "raw.json"}
    html = (tmp_path / "page.html").read_text(encoding="utf-8")
    assert "<script" not in html and "<style" not in html and "Synthetic form" in html
    for name in ("page.html", "readback.json", "raw.json"):
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert EMAIL not in text and "synthetic-password-1" not in text, name
    saved = json.loads((tmp_path / "readback.json").read_text(encoding="utf-8"))
    assert saved[1] == {**EXPECTED_BROWSER[1].model_dump(mode="json"), "value": "[email]"}
    assert (tmp_path / "screenshot.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


# --- the screenshot: contact values blacked out, or not saved (specs/form-fill.md 5.6) ---------------------------


def node(i, parent, text, path=None):
    return {"i": i, "parent": parent, "path": path or f"body > :nth-child({i + 1})", "text": text}


def test_contact_paths_pick_the_smallest_elements_holding_a_value():
    nodes = [
        node(0, -1, f"Apply Email {EMAIL} Name Sam"),  # body: only its child holds the value
        node(1, 0, f"Email {EMAIL}"),
        node(2, 1, EMAIL),  # an input's value
        node(3, 0, "Name Sam"),
    ]
    assert readback.contact_paths(nodes, StandInRedactor()) == ["body > :nth-child(3)"]


def test_contact_paths_keep_a_container_whose_own_text_holds_a_value():
    nodes = [
        node(0, -1, f"{EMAIL} and again {EMAIL}"),
        node(1, 0, EMAIL),
        node(2, -1, "nothing here"),
    ]
    assert readback.contact_paths(nodes, StandInRedactor()) == ["body > :nth-child(1)", "body > :nth-child(2)"]


@pytest.mark.parametrize("bad", [None, [1], [{"i": 0}], [{"i": 0, "parent": -1, "path": 3, "text": ""}]])
def test_contact_paths_reject_malformed_nodes(bad):
    with pytest.raises(InvariantError):
        readback.contact_paths(bad, StandInRedactor())


def test_page_scripts_only_read():
    """Nothing run in the page clicks, types, submits, navigates, focuses or changes the document."""
    for script in (readback.EXTRACT_JS, readback.CONTACT_JS):
        for word in (
            ".click(",
            ".submit(",
            "requestSubmit",
            "dispatchEvent",
            ".focus(",
            "location =",
            "location.assign",
            "location.replace",
            "location.href =",
            "window.open",
            ".remove(",
            "appendChild",
            "innerHTML",
            "el.value =",
            "el.checked =",
        ):
            assert word not in script, word


def pixel(browser, png: bytes, x: float, y: float) -> list[int]:
    """The RGBA pixel at (x, y) of a PNG, decoded by the browser itself."""
    pg = browser.new_page()
    try:
        url = "data:image/png;base64," + base64.b64encode(png).decode()
        return pg.evaluate(
            """async ([url, x, y]) => {
                const img = new Image(); img.src = url; await img.decode();
                const c = document.createElement("canvas"); c.width = img.width; c.height = img.height;
                const g = c.getContext("2d"); g.drawImage(img, 0, 0);
                return Array.from(g.getImageData(x, y, 1, 1).data);
            }""",
            [url, int(x), int(y)],
        )
    finally:
        pg.close()


def test_the_saved_screenshot_has_the_contact_value_blacked_out(chromium, tmp_path):
    browser, pg, port = chromium
    url = "data:text/html;base64," + base64.b64encode(FORM_HTML.encode()).decode()
    pg.goto(url)
    fill_synthetic(pg)
    target = find_job_tab([], list_targets(port), pg.url)

    readback.read_final_page(port, target, tmp_path, StandInRedactor())

    png = (tmp_path / "screenshot.png").read_bytes()
    email, name = pg.locator("#em").bounding_box(), pg.locator("#fn").bounding_box()
    assert pixel(browser, png, email["x"] + 4, email["y"] + email["height"] / 2)[:3] == [0, 0, 0]
    assert pixel(browser, png, name["x"] + 4, name["y"] + name["height"] / 2)[:3] != [0, 0, 0]
    assert pg.input_value("#em") == EMAIL and not pg.is_closed()  # the page itself is unchanged
    assert not (tmp_path / readback.SCREENSHOT_NOTE).exists()


def test_a_screenshot_that_cannot_be_blacked_out_is_not_saved(chromium, tmp_path):
    _, pg, port = chromium
    inner = f"<p>Signed in as {EMAIL}</p>"
    html = f'<!doctype html><html><body><h1>Synthetic</h1><iframe srcdoc="{inner}"></iframe></body></html>'
    pg.goto("data:text/html;base64," + base64.b64encode(html.encode()).decode())
    pg.wait_for_function("document.querySelector('iframe').contentDocument.readyState === 'complete'")
    target = find_job_tab([], list_targets(port), pg.url)

    readback.read_final_page(port, target, tmp_path, StandInRedactor())

    assert not (tmp_path / "screenshot.png").exists()
    note = (tmp_path / readback.SCREENSHOT_NOTE).read_text(encoding="utf-8")
    assert note.startswith("截图未存：打码失败") and EMAIL not in note
    assert {p.name for p in tmp_path.iterdir()} == {"page.html", "readback.json", "raw.json", readback.SCREENSHOT_NOTE}


def test_the_reader_never_drives_the_page():
    """read_final_page and its helpers only evaluate the read scripts, take the content and a screenshot, and
    disconnect: no click, type, fill, key press, navigation or close."""
    import ast
    import inspect

    forbidden = {
        "click",
        "dblclick",
        "fill",
        "type",
        "press",
        "goto",
        "reload",
        "go_back",
        "go_forward",
        "close",
        "check",
        "uncheck",
        "select_option",
        "set_input_files",
        "bring_to_front",
        "set_viewport_size",
        "tap",
        "focus",
        "dispatch_event",
        "add_script_tag",
        "add_style_tag",
        "set_content",
    }
    tree = ast.parse(inspect.getsource(readback))
    called = {
        node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not called & forbidden
