"""Form-fill slice 2a: the recording folder (specs/form-fill.md 4.1, 4.3, 5.6, 6.2, 6.5; slice-2a plan sections 1
and 5): recording ids, whether a job needs another real visit, network records, MHTML masking, what goes into form/
and fills/<run>/, and which read each page's values come from. All data is SYNTHETIC."""

import base64
import json
import quopri

import pytest
from intent_job_agent.form_fill.recorder import Recorder, Snapshot
from intent_job_agent.form_fill.recording import Capture, RecordingStore, read_run, recording_id

from intent_job_agent.form_fill import recording
from intent_job_agent.form_fill.models import ReadField
from intent_job_agent.form_fill.redact import Redactor

PROFILE = {
    "personal": {
        "full_name": "Sam Example",
        "email": "sam.synthetic@example.test",
        "phone": "555-010-0199",
        "city": "Riverton",
        "address": "12 Elm Street",
        "postal_code": "40001",
        "linkedin_url": "https://www.linkedin.com/in/sam-synthetic",
        "password": "synthetic-password-1",
    }
}
SECRETS = (
    "sam.synthetic@example.test",
    "sam.synthetic%40example.test",
    "555-010-0199",
    "5550100199",
    "12 Elm Street",
    "synthetic-password-1",
)
URL = "https://jobs.example.test/acme/apply"


@pytest.fixture
def redactor():
    return Redactor.from_profile(PROFILE)


def field(question, value="", control="text", section=""):
    return ReadField(id="p1.0", question=question, origin="control", control=control, value=value, section=section)


def snap(*fields, marker=("Apply",), url=URL, at="2026-10-20T10:00:00"):
    return Snapshot(tab="T1", url=url, marker=tuple(marker), fields=tuple(fields), read_at=at)


def capture(html="<html><body>form</body></html>"):
    return Capture(html=html, screenshot=b"\x89PNG synthetic", why="", mhtml=None)


def leaks(root) -> list[str]:
    found = []
    for path in root.rglob("*"):
        if path.is_file():
            text = path.read_bytes().decode("utf-8", errors="replace")
            found += [f"{path.name}: {secret}" for secret in SECRETS if secret in text]
    return found


# --- recording ids (spec 6.2) -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://job-boards.greenhouse.io/acme/jobs/1234567", "greenhouse-acme-1234567"),
        ("https://boards.greenhouse.io/Acme/jobs/1234567?gh_src=abc", "greenhouse-acme-1234567"),
        ("https://jobs.lever.co/beta/0f1e2d3c-aaaa-bbbb/apply?lever-source=LinkedIn", "lever-beta-0f1e2d3c-aaaa-bbbb"),
        ("https://jobs.ashbyhq.com/gamma/9a8b7c6d-1111/application", "ashby-gamma-9a8b7c6d-1111"),
        (
            "https://delta.wd5.myworkdayjobs.com/en-US/External/job/Austin-TX/ML-Engineer_R-0042/apply",
            "workday-delta-r-0042",
        ),
        ("https://www.linkedin.com/jobs/view/4012345678/?refId=x&trackingId=y", "linkedin-4012345678"),
        ("https://www.linkedin.com/jobs/search/?currentJobId=4012345678", "linkedin-4012345678"),
    ],
)
def test_known_platforms_get_readable_ids(url, expected):
    assert recording_id(url) == expected


def test_other_sites_get_the_host_and_a_hash_and_tracking_does_not_count():
    a = recording_id("https://careers.example.test/jobs/77?utm_source=x&id=5")
    b = recording_id("https://Careers.Example.test/jobs/77?id=5&utm_campaign=y")
    assert a == b
    assert a.startswith("other-careers.example.test-") and len(a.rsplit("-", 1)[1]) == 8
    assert recording_id("https://careers.example.test/jobs/78?id=5") != a


# --- another real visit? (spec 4.1, 6.5) ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("conclusion", "reason", "visit"),
    [
        ("check_then_submit", None, False),
        ("do_not_submit", None, False),
        ("maybe_submitted", None, False),
        ("not_eligible", None, False),
        ("not_filled", "expired", False),
        ("not_filled", "wrong_job", False),
        ("not_filled", "email_only", False),
        ("not_filled", "already_applied", False),
        ("not_filled", "gate", True),
        ("not_filled", "login_failed", True),
        ("not_filled", "needs_answer", True),
        ("not_filled", "page_error", True),
        ("not_filled", "stuck", True),
        ("not_filled", "system_error", True),
        ("unreadable", None, True),
    ],
)
def test_the_last_conclusion_decides_another_visit(tmp_path, redactor, conclusion, reason, visit):
    store = RecordingStore(tmp_path, "greenhouse-acme-1", "run-1", redactor)
    store.write_meta(URL, "Acme", "ML Engineer")
    recording.note_conclusion(store.rec_dir, "run-1", conclusion, reason)
    assert recording.needs_visit(store.rec_dir, revisit=False)[0] is visit
    assert recording.needs_visit(store.rec_dir, revisit=True)[0] is True
    assert recording.needs_visit(store.rec_dir, revisit=False)[1]["conclusion"] == conclusion


def test_a_job_never_recorded_needs_a_visit(tmp_path):
    assert recording.needs_visit(tmp_path / "nothing-here", revisit=False) == (True, None)


# --- what goes where (spec 6.2) ---------------------------------------------------------------------------------


def test_the_blank_form_holds_structure_and_the_fill_holds_values(tmp_path, redactor):
    rec, store = Recorder(), RecordingStore(tmp_path, "greenhouse-acme-1", "run-1", redactor)
    store.write_meta(URL, "Acme", "ML Engineer")
    blank = rec.observe(snap(field("Email", "sam.synthetic@example.test"), field("School", "Old U")), "blank")
    store.save(blank, "blank", capture())
    filled = rec.observe(snap(field("Email", "sam.synthetic@example.test"), field("School", "Example U")), "filled")
    store.save(filled, "filled", capture())
    store.save_state(rec)

    structure = json.loads((store.rec_dir / "form" / "p1.structure.json").read_text(encoding="utf-8"))
    assert [f["question"] for f in structure["fields"]] == ["Email", "School"]
    assert all("value" not in f for f in structure["fields"])  # no values in form/
    assert "Old U" not in json.dumps(structure)
    assert (store.rec_dir / "form" / "p1.html").is_file() and (store.rec_dir / "form" / "p1.png").is_file()
    prefilled = json.loads((store.fill_dir / "p1.prefilled.json").read_text(encoding="utf-8"))
    assert [f["value"] for f in prefilled["fields"]] == ["[email]", "Old U"]
    values = json.loads((store.fill_dir / "p1.values.json").read_text(encoding="utf-8"))
    assert (
        values["page"] == 1 and values["read_at"] and [f["value"] for f in values["fields"]] == ["[email]", "Example U"]
    )
    assert (store.fill_dir / "p1.filled.html").is_file() and (store.fill_dir / "p1.filled.png").is_file()
    meta = json.loads((store.rec_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["url"] == URL and meta["platform"] == "greenhouse" and meta["replay_capability"] == "check_only"
    assert leaks(tmp_path) == []


def test_a_second_blank_of_the_same_page_keeps_the_first(tmp_path, redactor):
    rec, store = Recorder(), RecordingStore(tmp_path, "r", "run-1", redactor)
    store.save(rec.observe(snap(field("School", "Old U")), "blank"), "blank", capture())
    store.save(rec.observe(snap(field("School", "Typed")), "blank"), "blank", capture())
    prefilled = json.loads((store.fill_dir / "p1.prefilled.json").read_text(encoding="utf-8"))
    assert prefilled["fields"][0]["value"] == "Old U"


def test_a_changed_form_on_a_later_visit_goes_to_form_v2(tmp_path, redactor):
    first = RecordingStore(tmp_path, "r", "run-1", redactor)
    first.save(Recorder().observe(snap(field("Email"), field("School")), "blank"), "blank", capture())
    same = RecordingStore(tmp_path, "r", "run-2", redactor)
    same.save(Recorder().observe(snap(field("Email"), field("School")), "blank"), "blank", capture())
    assert same.form_dir.name == "form"
    changed = RecordingStore(tmp_path, "r", "run-3", redactor)
    changed.save(Recorder().observe(snap(field("Email"), field("Portfolio")), "blank"), "blank", capture())
    assert changed.form_dir.name == "form-v2"
    assert (tmp_path / "r" / "form-v2" / "p1.structure.json").is_file()
    assert json.loads((tmp_path / "r" / "form" / "p1.structure.json").read_text())["fields"][1]["question"] == "School"


def test_a_screenshot_that_could_not_be_blacked_out_is_noted_not_saved(tmp_path, redactor):
    store = RecordingStore(tmp_path, "r", "run-1", redactor)
    obs = Recorder().observe(snap(field("Email")), "blank")
    store.save(obs, "blank", Capture(html="<p>x</p>", screenshot=None, why="子框架里有联系方式，涂不到"))
    assert not (store.rec_dir / "form" / "p1.png").exists()
    assert "涂不到" in (store.rec_dir / "form" / "p1.png.not-saved.txt").read_text(encoding="utf-8")


def test_checkpoints_and_requests_are_logged(tmp_path, redactor):
    store = RecordingStore(tmp_path, "r", "run-1", redactor)
    store.log_checkpoint({"stage": "blank", "page": 1, "agent_url": URL, "tab_url": URL, "seconds": 0.4})
    store.add_request("POST", "https://jobs.example.test/api/upload?token=abc&email=sam.synthetic@example.test")
    store.add_request("POST", "https://jobs.example.test/api/upload?x=1")
    store.add_request("GET", "https://jobs.example.test/page")
    assert json.loads((store.fill_dir / "checkpoints.json").read_text())[0]["stage"] == "blank"
    assert json.loads((store.fill_dir / "requests.json").read_text()) == ["POST https://jobs.example.test/api/upload"]


# --- network records (spec 6.2) ---------------------------------------------------------------------------------


def event(url="https://jobs.example.test/app.js", body=b"console.log(1)", method="GET", headers=None, status=200):
    headers = headers or {"content-type": "application/javascript"}
    return {"method": method, "url": url, "status": status, "headers": headers, "body": body}


def test_cookies_and_auth_headers_are_dropped(redactor):
    entry, own = recording.network_entry(
        event(
            headers={
                "content-type": "text/html",
                "set-cookie": "sid=1",
                "cookie": "sid=1",
                "authorization": "Bearer x",
                "x-csrf-token": "t",
                "x-session-id": "s",
                "cache-control": "no-cache",
            }
        ),
        redactor,
    )
    assert own is False
    assert entry["headers"] == {"content-type": "text/html", "cache-control": "no-cache"}
    assert entry["body_text"] == "console.log(1)"


def test_a_response_with_contact_details_is_dropped_whole(redactor):
    entry, own = recording.network_entry(event(body=b'{"email": "sam.synthetic@example.test"}'), redactor)
    assert entry is None and own is True  # the applicant's own e-mail: a signed-in page
    entry, own = recording.network_entry(event(body=b"mailto:help@acme.example.test"), redactor)
    assert entry is None and own is False  # someone else's address: dropped, but no sign of a signed-in page
    entry, own = recording.network_entry(
        event(url="https://jobs.example.test/x?e=sam.synthetic@example.test"), redactor
    )
    assert entry is None and own is True


def test_binary_bodies_are_kept_as_base64(redactor):
    entry, _ = recording.network_entry(event(body=b"\x89PNG\x00\x01", headers={"content-type": "image/png"}), redactor)
    assert base64.b64decode(entry["body_base64"]) == b"\x89PNG\x00\x01"


def test_other_methods_keep_no_content(redactor):
    assert recording.network_entry(event(method="POST"), redactor) == (None, False)
    assert recording.request_entry("POST", "https://a.example.test/save?draft=1") == "POST https://a.example.test/save"


@pytest.mark.parametrize(
    ("pages", "password", "contact", "kept"),
    [(1, False, False, True), (2, False, False, False), (1, True, False, False), (1, False, True, False)],
)
def test_the_network_record_is_kept_only_for_a_signed_out_single_page_form(
    tmp_path, redactor, pages, password, contact, kept
):
    rec, store = Recorder(), RecordingStore(tmp_path, "r", "run-1", redactor)
    store.save(rec.observe(snap(field("Email")), "blank"), "blank", capture())
    if pages == 2:
        store.save(
            rec.observe(snap(field("Gender"), field("Race"), marker=("Voluntary",)), "blank"), "blank", capture()
        )
    assert store.add_network(
        {"url": "https://jobs.example.test/app.js", "status": 200, "headers": {}, "body_text": "1"}
    )
    if password:
        store.note_password()
    if contact:
        store.note_signed_in()
    store.save_state(rec)
    assert recording.finish_network(store) is kept
    assert (store.rec_dir / "form" / "network.json").is_file() is kept
    assert not (store.rec_dir / "form" / "network.partial.jsonl").exists()


def test_the_network_record_stops_at_its_size_limit(tmp_path, redactor):
    store = RecordingStore(tmp_path, "r", "run-1", redactor, network_max_bytes=100)
    assert store.add_network({"url": "a", "status": 200, "headers": {}, "body_text": "x" * 60})
    assert not store.add_network({"url": "b", "status": 200, "headers": {}, "body_text": "x" * 60})


# --- MHTML (spec 5.6) -------------------------------------------------------------------------------------------


def mhtml(html: str) -> str:
    body = quopri.encodestring(html.encode("utf-8")).decode("ascii")
    return (
        "From: <Saved by Blink>\r\n"
        "Snapshot-Content-Location: https://jobs.example.test/acme/apply\r\n"
        "Subject: Apply\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/related; type="text/html"; boundary="----MultipartBoundary--abc"\r\n'
        "\r\n"
        "------MultipartBoundary--abc\r\n"
        "Content-Type: text/html\r\n"
        "Content-ID: <frame-1@mhtml.blink>\r\n"
        "Content-Transfer-Encoding: quoted-printable\r\n"
        "Content-Location: https://jobs.example.test/acme/apply\r\n"
        "\r\n"
        f"{body}\r\n"
        "------MultipartBoundary--abc\r\n"
        "Content-Type: image/png\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        "Content-Location: https://jobs.example.test/logo.png\r\n"
        "\r\n"
        f"{base64.b64encode(b'PNG').decode()}\r\n"
        "------MultipartBoundary--abc--\r\n"
    )


def test_mhtml_parts_are_decoded_masked_and_written_back(redactor):
    page = "<p>Signed in as sam.synthetic@example.test, call 555-010-0199</p>" + "<p>filler text</p>" * 20
    masked = recording.mask_mhtml(mhtml(page), redactor)
    assert masked is not None
    assert "sam.synthetic" not in quopri.decodestring(masked.encode()).decode("utf-8", errors="replace")
    assert "[email]" in quopri.decodestring(masked.encode()).decode("utf-8", errors="replace")
    assert "Content-Location: https://jobs.example.test/logo.png" in masked  # resource links stay
    assert recording.mhtml_hits(masked, redactor) == 0


def test_mhtml_that_cannot_be_parsed_is_not_kept(redactor):
    assert recording.mask_mhtml("not a mime document sam.synthetic@example.test", redactor) is None


# --- which read each page's values come from (spec 4.3, 5.3) ----------------------------------------------------


def three_pages(tmp_path, redactor, skip_filled_2=False, skip_auto_2=False):
    """A three-page form: p1 blank+filled, p2 blank + (filled or only the background read), p3 blank+final."""
    rec, store = Recorder(), RecordingStore(tmp_path, "r", "run-1", redactor)
    p1 = (field("First Name", "Sam"), field("Email", "sam.synthetic@example.test"))
    store.save(
        rec.observe(snap(field("First Name"), field("Email", "sam.synthetic@example.test"), marker=("Info",)), "blank"),
        "blank",
        capture(),
    )
    store.save(rec.observe(snap(*p1, marker=("Info",), at="2026-10-20T10:01:00"), "filled"), "filled", capture())
    store.save(rec.observe(snap(field("School"), field("Degree"), marker=("Experience",)), "blank"), "blank", capture())
    p2 = snap(
        field("School", "Example State University"),
        field("Degree", "Master of Science"),
        marker=("Experience",),
        at="2026-10-20T10:02:00",
    )
    if not skip_auto_2:
        store.save(rec.observe(p2, "auto"), "auto", None)
    if not skip_filled_2:
        store.save(rec.observe(p2, "filled"), "filled", capture())
    store.save(rec.observe(snap(field("Gender"), field("Race"), marker=("Voluntary",)), "blank"), "blank", capture())
    store.save(
        rec.observe(
            snap(field("Gender", "Female"), field("Race"), marker=("Voluntary",), at="2026-10-20T10:03:00"), "final"
        ),
        "final",
        capture(),
    )
    store.save_state(rec)
    return store


def test_each_page_takes_its_last_filled_read_and_the_current_page_its_final_read(tmp_path, redactor):
    pages = read_run(three_pages(tmp_path, redactor).rec_dir, "run-1")
    assert [(p.page, p.read_from) for p in pages] == [(1, "filled"), (2, "filled"), (3, "final")]
    assert [p.title for p in pages] == ["Info", "Experience", "Voluntary"]
    assert [f.value for f in pages[1].fields] == ["Example State University", "Master of Science"]
    assert pages[0].read_at == "2026-10-20T10:01:00"
    assert pages[0].sources == {"p1.1": "undeclared", "p1.2": "prefilled"}


def test_a_page_without_its_filled_read_takes_the_background_read_not_the_next_blank(tmp_path, redactor):
    pages = read_run(three_pages(tmp_path, redactor, skip_filled_2=True).rec_dir, "run-1")
    assert pages[1].read_from == "auto"
    assert [f.value for f in pages[1].fields] == ["Example State University", "Master of Science"]


def test_a_page_with_neither_read_is_unrecorded(tmp_path, redactor):
    pages = read_run(three_pages(tmp_path, redactor, skip_filled_2=True, skip_auto_2=True).rec_dir, "run-1")
    assert pages[1].read_from == "unrecorded"
    assert [f.question for f in pages[1].fields] == ["School", "Degree"]  # from the blank structure
    assert all(f.value == "" for f in pages[1].fields)


def test_nothing_in_the_recording_is_a_contact_value(tmp_path, redactor):
    three_pages(tmp_path, redactor)
    assert leaks(tmp_path) == []
