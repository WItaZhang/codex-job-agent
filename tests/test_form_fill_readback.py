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
from intent_job_agent.form_fill.models import PageState, ReadField
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


def test_a_pressed_button_group_reads_the_pressed_button():
    toggle = control(
        4,
        tag="button",
        type="",
        role="togglegroup",
        labels={"preceding": "Will you require sponsorship?"},
        options=[{"text": "Yes", "value": "", "selected": False}, {"text": "No", "value": "", "selected": True}],
    )
    assert normalize(one_frame([toggle])) == (field(1, "Will you require sponsorship?", "radio", "No", ("Yes", "No")),)


def test_an_aria_checkbox_reads_aria_checked():
    widget = control(2, tag="div", type="", role="checkbox", labels={"aria_label": "Follow the company"}, checked=True)
    assert normalize(one_frame([widget])) == (field(1, "Follow the company", "checkbox", "Yes"),)


def test_a_group_question_prefers_the_title_inside_the_group():
    radios = [
        control(
            n,
            type="radio",
            group="n:edu",
            labels={"label_for": label, "preceding": "Please refer to the posting.", "inner": "Education status"},
            option_label=label,
            checked=n == 5,
        )
        for n, label in ((5, "Student"), (6, "Graduated"))
    ]
    assert normalize(one_frame(radios)) == (field(1, "Education status", "radio", "Student", ("Student", "Graduated")),)


def test_a_lone_checkbox_in_a_titled_group_takes_the_group_title():
    box = control(
        3,
        type="checkbox",
        group="n:I agree",
        labels={"label_for": "I agree", "inner": "May we contact you about future roles?"},
        option_label="I agree",
        checked=False,
    )
    assert normalize(one_frame([box])) == (
        field(1, "May we contact you about future roles?", "checkbox", "", ("I agree",)),
    )


def test_page_state_reads_the_buttons_left_and_any_received_message():
    raw = {
        "frames": [
            {"url": "u", "controls": [], "pairs": [], "actions": ["Edit", " Submit\n application ", "Back"]},
            {"url": "v", "error": "cross-origin"},
            {"url": "w", "controls": [], "pairs": [], "actions": ["Next"], "notices": ["Contact info"]},
        ]
    }
    assert readback.page_state(raw) == PageState(actions=("Submit application", "Next"))
    assert readback.page_state(raw).before_submit
    done = {"frames": [{"url": "u", "actions": ["Done"], "notices": ["Your application was sent to Acme!"]}]}
    assert readback.page_state(done) == PageState(received="Your application was sent to Acme!")
    assert not readback.page_state(done).before_submit
    assert readback.page_state({"frames": [{"url": "u", "controls": [], "pairs": []}]}) == PageState()


@pytest.mark.parametrize("bad", [{"frames": [{"actions": "Submit"}]}, {"frames": [{"notices": [1]}]}])
def test_page_state_rejects_malformed_lists(bad):
    with pytest.raises(InvariantError):
        readback.page_state(bad)


def test_asterisks_around_a_question_are_dropped():
    pairs = [{"order": 1, "question": "**Gender**", "value": "Female", "source": "row"}]
    nodes = [control(2, labels={"label_for": "* First name"}, value="Sam")]
    assert normalize(one_frame(nodes, pairs)) == (
        field(1, "Gender", "review", "Female"),
        field(2, "First name", "text", "Sam", required=True),
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


# A single-page form in the shape of the 2026-10-15 Ashby page: a Yes / No button group with its native checkbox
# hidden beside it, a radio group titled by a label that points at no control, with a description in between, and
# a consent group whose question is a paragraph followed by a link. SYNTHETIC.
BUTTONS_HTML = """<!doctype html><html><body>
<h1>Synthetic Engineer</h1>
<div><div><h2>Location</h2><p>Austin</p></div><div><h2>Department</h2><p>Platform</p></div></div>
<form>
  <div><label for="nm">Name</label><input id="nm"></div>
  <div><label for="q-sponsor">Will you now or in the future require sponsorship?</label>
    <div><button type="button" aria-pressed="false">Yes</button><button type="button" aria-pressed="true">No</button>
      <input type="checkbox" tabindex="-1" name="q-sponsor" style="opacity:0;width:1px;height:1px"></div></div>
  <div><h2>Education</h2>
    <div><p>This role is open to candidates who have worked on synthetic systems for a while, and this paragraph says
      who may apply and why, at some length.</p><p>Please refer to the job posting for more information.</p></div>
    <fieldset style="border:0"><label for="q-edu">Current education status</label>
      <div><p>Please answer this question truthfully.</p></div>
      <div><span><input type="radio" id="e0" name="edu"></span><label for="e0">I am currently a student.</label></div>
      <div><span><input type="radio" id="e1" name="edu"></span><label for="e1">I graduated last year.</label></div>
    </fieldset></div>
  <fieldset style="border:0"><label for="q-consent"></label>
    <div><p>May Synthetic contact you about future roles?</p><p><a href="#">Privacy Policy</a></p></div>
    <div><span><input type="checkbox" id="c0" name="I agree"></span><label for="c0">I agree</label></div>
  </fieldset>
  <button type="submit">Submit Application</button>
</form></body></html>"""

EXPECTED_BUTTONS = (
    field(1, "Name", "text", "Sam Example"),
    field(2, "Will you now or in the future require sponsorship?", "radio", "No", ("Yes", "No")),
    field(
        3,
        "Current education status",
        "radio",
        "I am currently a student.",
        ("I am currently a student.", "I graduated last year."),
    ),
    field(4, "May Synthetic contact you about future roles?", "checkbox", "", ("I agree",)),
)


def test_button_groups_and_group_titles_in_a_real_browser(chromium):
    _, pg, _ = chromium
    pg.set_content(BUTTONS_HTML)
    pg.fill("#nm", "Sam Example")
    pg.check("#e0")
    frame = pg.evaluate(readback.EXTRACT_JS)
    raw = {"frames": [frame]}
    assert normalize(raw) == EXPECTED_BUTTONS
    assert readback.page_state(raw) == PageState(actions=("Submit Application",))


LONG_QUESTION = "Race categories are defined as follows. " + "A synthetic definition sentence for this test. " * 30

# A last step in the shape of the 2026-10-15 LinkedIn page: the review sits in a <dialog> opened as a modal over the
# job page; its rows are two paragraphs (question, value) under small headings. SYNTHETIC.
DIALOG_HTML = f"""<!doctype html><html><body>
<header><input aria-label="Search" placeholder="Search"><nav><a href="#">Jobs</a></nav></header>
<main><h1>Synthetic Engineer</h1><p>Austin, TX · Over 100 applicants</p><a href="#">Continue</a></main>
<footer><label for="lang">Select language</label><select id="lang"><option>English</option></select></footer>
<input type="file" id="up">
<dialog id="apply" aria-labelledby="dh">
  <header><h2 id="dh">Apply to Synthetic</h2></header>
  <div><p>8/8 pages</p></div>
  <div><h3>Review your application</h3><p>The employer will also receive a copy of your profile</p></div>
  <section><div><h4>Contact info</h4><button type="button">Edit</button></div>
    <div><div><p>First name</p><p>Sam</p></div><div><p>Email address</p><p>{EMAIL}</p></div>
      <div><p>Mobile phone number</p><p>555-0100</p></div></div></section>
  <section><div><h4>Resume</h4><button type="button">Edit</button></div>
    <div><p>Upload a resume in PDF format</p><div><div><p>PDF</p></div><p>resume_synthetic.pdf</p></div></div></section>
  <section><div><p>Work Experience</p><button type="button">Edit</button></div>
    <div><div><p>Your title</p><p>Engineer Intern</p></div><div><p>Company</p><p>Synthetic Labs</p></div>
      <div><p>Dates of employment</p><div><p>6</p><p>/</p><p>2024</p><p> - </p><p>8</p><p>/</p><p>2024</p></div></div>
      <div><p>City</p><p>Austin</p></div></div></section>
  <section><div><h4>Additional Questions</h4><button type="button">Edit</button></div>
    <div><p>Are you legally authorized to work in the country?</p><p>Yes</p></div>
    <div><p>Will you require sponsorship?</p><p>No</p></div>
    <div><p>**Race/Ethnicity**<br><br>{LONG_QUESTION}</p><p>I prefer not to specify</p></div></section>
  <section><div><h4>Privacy</h4><button type="button">Edit</button></div>
    <div><div><p>You declare that you have read the privacy notice.</p><p>I consent</p></div></div></section>
  <div role="checkbox" aria-label="Mark job as a top choice" aria-checked="false">
    <div><input id="tc" type="checkbox"><label for="tc"></label></div><p>Mark job as a top choice</p></div>
  <div role="checkbox" aria-label="Follow Synthetic to stay up to date" aria-checked="true">
    <div><input id="fo" type="checkbox" checked><label for="fo"></label></div>
    <p>Follow Synthetic to stay up to date</p></div>
  <div><div role="alert"><p>Resume uploaded</p></div><div><p>1m</p><p>1/2</p></div></div>
  <footer><button type="button">Submit application</button></footer>
</dialog></body></html>"""

EXPECTED_DIALOG = (
    field(1, "First name", "review", "Sam"),
    field(2, "Email address", "review", EMAIL),
    field(3, "Mobile phone number", "review", "555-0100"),
    field(4, "Upload a resume in PDF format", "review", "PDF resume_synthetic.pdf"),
    field(5, "Your title", "review", "Engineer Intern"),
    field(6, "Company", "review", "Synthetic Labs"),
    field(7, "Dates of employment", "review", "6 / 2024 - 8 / 2024"),
    field(8, "City", "review", "Austin"),
    field(9, "Are you legally authorized to work in the country?", "review", "Yes"),
    field(10, "Will you require sponsorship?", "review", "No"),
    field(11, "Race/Ethnicity " + LONG_QUESTION.strip(), "review", "I prefer not to specify"),
    field(12, "You declare that you have read the privacy notice.", "review", "I consent"),
    field(13, "Mark job as a top choice", "checkbox", ""),
    field(14, "Follow Synthetic to stay up to date", "checkbox", "Yes"),
)


def test_a_modal_dialog_is_read_alone_with_its_review_rows(chromium):
    _, pg, _ = chromium
    pg.set_content(DIALOG_HTML)
    pg.evaluate("document.getElementById('apply').showModal()")
    raw = {"frames": [pg.evaluate(readback.EXTRACT_JS)]}
    assert normalize(raw) == EXPECTED_DIALOG
    assert readback.page_state(raw) == PageState(actions=("Submit application",))


# A review page in the shape of the 2026-10-15 Workday page: groups titled by a small heading, holding rows that are
# a heading or an orphan label plus a value. SYNTHETIC.
SECTIONS_HTML = f"""<!doctype html><html><body>
<h3>My Information</h3>
<div role="group"><h4>Contact</h4><div>
  <div><h4>Email Address</h4><div><div><span>{EMAIL}</span></div></div></div>
  <div><h4>Phone</h4><div><div><span>555-0100 (Mobile)</span></div></div></div></div></div>
<h3>Voluntary Disclosures</h3>
<div role="group"><h4>Equal Opportunity Employment</h4><div>
  <div><label for="gender">Gender</label><div><span>Female</span></div></div>
  <div><label for="veteran">Veterans</label><div><span>I do not wish to self-identify.</span></div></div></div></div>
<button type="button">Back</button><button type="button">Submit</button>
</body></html>"""


def test_review_sections_are_not_rows_of_their_own(chromium):
    _, pg, _ = chromium
    pg.set_content(SECTIONS_HTML)
    raw = {"frames": [pg.evaluate(readback.EXTRACT_JS)]}
    assert normalize(raw) == (
        field(1, "Email Address", "review", EMAIL),
        field(2, "Phone", "review", "555-0100 (Mobile)"),
        field(3, "Gender", "review", "Female"),
        field(4, "Veterans", "review", "I do not wish to self-identify."),
    )
    assert readback.page_state(raw) == PageState(actions=("Submit",))


def test_a_page_with_nothing_to_read_in_its_dialog_is_read_whole(chromium):
    _, pg, _ = chromium
    html = """<!doctype html><html><body><label for="a">Answer</label><input id="a" value="x">
      <dialog id="d"><p>We use cookies.</p><button type="button">OK</button></dialog></body></html>"""
    pg.set_content(html)
    pg.evaluate("document.getElementById('d').showModal()")
    assert normalize({"frames": [pg.evaluate(readback.EXTRACT_JS)]}) == (field(1, "Answer", "text", "x"),)


def test_a_received_page_is_not_before_submit(chromium):
    _, pg, _ = chromium
    pg.set_content(
        """<!doctype html><html><body><h1>Thank you for applying!</h1><p>We will be in touch.</p>
        <a href="#">Back to jobs</a><button type="button">Done</button></body></html>"""
    )
    state = readback.page_state({"frames": [pg.evaluate(readback.EXTRACT_JS)]})
    assert state == PageState(received="Thank you for applying!") and not state.before_submit


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

    final = readback.read_final_page(port, target, tmp_path, StandInRedactor())

    assert final.fields == EXPECTED_BROWSER
    assert final.state == PageState(actions=("Submit application",))
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


def black_rows(browser, png: bytes, width: int) -> int:
    """How many rows of a PNG hold an unbroken run of black pixels at least `width` wide."""
    pg = browser.new_page()
    try:
        url = "data:image/png;base64," + base64.b64encode(png).decode()
        return pg.evaluate(
            """async ([url, width]) => {
                const img = new Image(); img.src = url; await img.decode();
                const c = document.createElement("canvas"); c.width = img.width; c.height = img.height;
                const g = c.getContext("2d"); g.drawImage(img, 0, 0);
                const d = g.getImageData(0, 0, img.width, img.height).data;
                let rows = 0;
                for (let y = 0; y < img.height; y++) {
                    let run = 0, best = 0;
                    for (let x = 0; x < img.width; x++) {
                        const i = 4 * (y * img.width + x);
                        run = d[i] < 16 && d[i + 1] < 16 && d[i + 2] < 16 ? run + 1 : 0;
                        best = Math.max(best, run);
                    }
                    if (best >= width) rows++;
                }
                return rows;
            }""",
            [url, width],
        )
    finally:
        pg.close()


def test_a_contact_value_in_a_modal_dialog_is_blacked_out(chromium, tmp_path):
    """A <dialog> opened as a modal sits in the browser's top layer, above any overlay drawn on the page; the
    blackout has to follow the element itself, on a scrolled page too."""
    browser, pg, port = chromium
    html = f"""<!doctype html><html><body style="margin:0"><div style="height:1800px">Synthetic page</div>
      <dialog id="d"><p>Email address</p><p id="em" style="font:20px sans-serif;padding:6px">{EMAIL}</p></dialog>
      </body></html>"""
    pg.goto("data:text/html;base64," + base64.b64encode(html.encode()).decode())
    pg.evaluate("document.getElementById('d').showModal(); window.scrollTo(0, 900)")
    box = pg.locator("#em").bounding_box()
    target = find_job_tab([], list_targets(port), pg.url)

    readback.read_final_page(port, target, tmp_path, StandInRedactor())

    png = (tmp_path / "screenshot.png").read_bytes()
    assert black_rows(browser, png, int(box["width"] * 0.9)) >= int(box["height"] * 0.8)
    assert pg.evaluate("document.getElementById('d').open") and not pg.is_closed()  # the page is left as it was


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
