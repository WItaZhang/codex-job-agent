"""Open-answer bank, slice 3 (specs/open-answers.md 3.4, 4.6, 4.9; open-answers.slice3.plan.md; samples P1-P16 of
specs/open-answers.samples.md): the draft a filling agent writes and the final the applicant submits become a pair;
code watches the filled tab read-only, classifies the pair, asks the applicant one reason question for a rewrite and
offers to put an edited final into the bank.

All data is SYNTHETIC. The filling agent, the applying Chrome and its reads are fakes, except that the agent's
checkpoint calls go through the real checkpoint service on a fake page browser, and every read is parsed by the real
readback code. The two real-browser tests use a synthetic local page and run only when Playwright's Chromium is
installed. No real profile, website or knowledge base is touched."""

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
import yaml

from intent_job_agent import apply_bridge, browser_session, open_answer_pairs, open_answers, watchdog
from intent_job_agent.config import Settings
from intent_job_agent.domain import InvariantError
from intent_job_agent.form_fill import checkpoint, readback, runner, watch
from intent_job_agent.form_fill.models import AnswerEntry
from intent_job_agent.form_fill.redact import Redactor, total_hits
from intent_job_agent.tools import COMMIT_TOOLS, SCHEDULED_TOOLS, TOOL_TIERS

from .apply_fakes import synthetic_binding
from .browser_fixture import finish_owned, start_owned
from .conftest import ROOT, settings
from .test_form_fill_checkpoint import FakeBrowser as PageBrowser
from .test_form_fill_checkpoint import config as checkpoint_config
from .test_form_fill_checkpoint import control, fills, raw
from .test_form_fill_runner import make_box, write_list
from .test_slice6 import DAY, PROFILE, FakeRunner, add_jobs, write_applicant, write_letters

PAIRS = yaml.safe_load((ROOT / "tests/fixtures/open_answers/pairs.yaml").read_text(encoding="utf-8"))
assert PAIRS["synthetic"] is True
CASES = {case["id"]: case for case in PAIRS["cases"]}
WHY = PAIRS["question"]
ELSE = "Is there anything else you would like us to know?"
DRAFT_WHY, DRAFT_ELSE = PAIRS["draft"], PAIRS["anything_else_draft"]
EDITED_WHY, REWRITTEN_WHY = CASES["P2"]["final"], CASES["P3"]["final"]
EDITED_ELSE = DRAFT_ELSE + " I also mentor new hires."
MARKS = [{"field": "p1.3", "type": "why_role"}, {"field": "p1.4", "type": "anything_else"}]
REDACTOR = Redactor.from_profile(PROFILE)


def flat(text: str) -> str:
    """A text as every read gives it back: each run of white space, line breaks included, as one space."""
    return " ".join(text.split())


BANK = """# SYNTHETIC test data: the applicant's own notes stay as they are
version: 1
entries:
  # an example the applicant wrote by hand
  - id: about-me
    type: about_me
    question: Tell us about yourself.
    text: |-
      I build ranking systems and I like owning the evaluation.
    status: accepted
    from: own words 2026-11-02 对话
"""


# --- the pages ---------------------------------------------------------------------------------------------------


def url_of(job: str) -> str:
    return f"https://jobs.example.test/{job}/apply"


def textarea(order: int, question: str, value: str = "") -> dict:
    node = control(order, question, value)
    node.update(tag="textarea", type="")
    return node


def form(job: str = "acme-ml-01", why: str = DRAFT_WHY, other: str = DRAFT_ELSE, notice: str = "Apply") -> dict:
    """The one-page form: First Name, Email and two open questions, still before submit."""
    page = raw(
        control(1, "First Name", "Sam"),
        control(2, "Email", "sam.synthetic@example.test"),
        textarea(3, WHY, why),
        textarea(4, ELSE, other),
        url=url_of(job),
        heading=notice,
    )
    page["frames"][0]["actions"] = ["Submit application"]
    return page


def thanks(job: str = "acme-ml-01") -> dict:
    page = raw(url=url_of(job) + "/thanks", heading="Thank you for applying!")
    page["frames"][0]["actions"] = []
    return page


class Chrome:
    """The applying Chrome, the filling agent and the CDP reads. The agent fills each job's one-page form in a new tab
    and calls the real checkpoint service (blank, then filled with `marks`), so the drafts are recorded by the real
    code; it stops before submit. The reads made while code watches a filled tab come from `script`, one page per
    read, the last one repeating."""

    running = True

    def __init__(self):
        self.tabs = [{"id": "T0", "type": "page", "url": "https://mail.example.org/inbox"}]
        self.script: dict[str, list[dict]] = {}
        self.drafts: dict[str, tuple[str, str]] = {}
        self.marks: dict[str, list] = {}
        self.outcomes: dict[str, tuple] = {}
        self.calls: list[str] = []
        self.answers: dict[str, str] = {}  # what the checkpoint service answered the agent's filled call
        self.prompts: list[str] = []

    def ensure_chrome(self):
        self.calls.append("ensure_chrome")
        return synthetic_binding()

    def targets(self) -> list[dict]:
        self.calls.append("targets")
        return [{"generation": "synthetic", **tab} for tab in self.tabs]

    def open_tabs(self):
        return [{"generation": "synthetic", **tab} for tab in self.tabs] if self.running else None

    def fill(self, spec, **options):
        self.calls.append("fill")
        self.prompts.append(options["extra_prompt"])
        job, url = spec.job_id, url_of(spec.job_id)
        config = checkpoint.Config.model_validate_json(
            Path(options["extra_servers"][checkpoint.SERVER_NAME]["args"][-1]).read_text(encoding="utf-8")
        )
        pages = PageBrowser()
        session = checkpoint.Session(config, pages, REDACTOR)
        why, other = self.drafts.get(job, (DRAFT_WHY, DRAFT_ELSE))
        pages.show(f"N-{job}", form(job, why="", other=""))
        session.checkpoint("blank", url)
        pages.show(f"N-{job}", form(job, why=why, other=other))
        self.answers[job] = session.checkpoint("filled", url, open_answers=self.marks.get(job, MARKS))
        status, reason = self.outcomes.get(job, ("waiting", None))
        self.tabs.append({"id": f"N-{job}", "type": "page", "url": url})
        log = options["log_path"]
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("", encoding="utf-8")
        return apply_bridge.ApplyOutcome(status, reason, [], [], [], str(log))

    def read(self, target, out_dir, redactor, sheet=None):
        self.calls.append("read")
        job = target["id"].removeprefix("N-")
        why, other = self.drafts.get(job, (DRAFT_WHY, DRAFT_ELSE))
        page = form(job, why=why, other=other)
        out_dir.mkdir(parents=True, exist_ok=True)
        fields = readback.normalize(page)
        dumped = [field.model_dump(mode="json") for field in fields]
        (out_dir / "readback.json").write_text(json.dumps(redactor.mask_json(dumped)), encoding="utf-8")
        snap = readback.snapshot(page, tab=target["id"], read_at="2026-11-06T10:00:00")
        return readback.FinalPage(fields, readback.page_state(page), snap)

    def read_fields(self, target):
        self.calls.append(f"read_fields {target['id']}")
        pages = self.script.setdefault(target["id"], [form(target["id"].removeprefix("N-"))])
        page = pages.pop(0) if len(pages) > 1 else pages[0]
        return readback.page_state(page), readback.snapshot(page, tab=target["id"], read_at="2026-11-06T10:05:00")

    def tab_state(self, target):
        self.calls.append("tab_state")
        raise AssertionError("watching the open answers never glances with tab_state")

    def close_tab(self, target) -> bool:
        self.calls.append("close_tab")
        raise AssertionError("watching the open answers never closes a tab")

    # what the user does in the browser

    def close(self, job: str) -> None:
        self.tabs = [tab for tab in self.tabs if tab["id"] != f"N-{job}"]

    def move(self, job: str, url: str) -> None:
        for tab in self.tabs:
            if tab["id"] == f"N-{job}":
                tab["url"] = url


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class User:
    """Toolbox.sleep while code watches the filled tabs: the clock moves on, and at each pause the user does the next
    thing in `script` (None: nothing yet)."""

    def __init__(self, box, *script):
        self.box, self.script, self.pauses = box, list(script), 0

    def __call__(self, seconds):
        self.pauses += 1
        if self.script:
            action = self.script.pop(0)
            if action is not None:
                action(self.box)
        self.box.clock.now += seconds


@pytest.fixture
def box(tmp_path):
    box = make_box(tmp_path)
    write_applicant(box.data_dir, open_answers="open_answers.yaml")
    (box.data_dir / "applicant" / "open_answers.yaml").write_text(BANK, encoding="utf-8")
    box.form_fill_runner = Chrome()
    box.form_fill_in_background = False
    box.watch_in_background = False
    box.clock = Clock()
    box.sleep = User(box)
    return box


def job_list(box, jobs=("acme-ml-01",)) -> str:
    entries = [
        {
            "id": job,
            "url": f"https://jobs.example.test/{job}",
            "company": "Acme Robotics" if job.startswith("acme") else "Beta Health",
            "title": "ML Engineer",
            "resume": "ml",
            "cover_letter": "none",
        }
        for job in jobs
    ]
    return write_list(box.data_dir / "form-fill" / "lists", entries)


def run(box, script=None, *actions, jobs=("acme-ml-01",)) -> str:
    """A form-fill run over `jobs`, then the watch of the filled tabs (inline here): each tab's reads come from
    `script`, and the user does `actions` at the pauses. Returns the run id."""
    box.form_fill_runner.script = {f"N-{job}": list(pages) for job, pages in (script or {}).items()}
    box.sleep = User(box, *actions)
    return box.start_form_fill(job_list(box, jobs))["run_id"]


def pairs_dir(box) -> Path:
    return box.data_dir / box.settings.open_answers.pairs.dir


def pair(box, run_id: str, job: str = "acme-ml-01", field: str = "p1.3") -> dict:
    return json.loads((pairs_dir(box) / f"{run_id}.{job}.{field}.json").read_text(encoding="utf-8"))


def questions(box) -> dict:
    return {question["id"]: question for question in box.open_answer_pairs()["questions"]}


def short_watch(box, seconds: float = 20) -> None:
    watch = box.settings.open_answers.watch.model_copy(update={"timeout_seconds": seconds})
    box.settings = box.settings.model_copy(
        update={"open_answers": box.settings.open_answers.model_copy(update={"watch": watch})}
    )


def budget(box, max_chars: int) -> None:
    box.settings = box.settings.model_copy(
        update={"open_answers": box.settings.open_answers.model_copy(update={"max_chars": max_chars})}
    )


def says_done(*jobs):
    return lambda box: box.open_answer_pairs(read_now=True, job_ids=list(jobs) or None)


# --- P1-P3: classification ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", PAIRS["cases"], ids=lambda case: case["id"])
def test_a_pair_is_classified_by_a_deterministic_text_distance(case):
    limit = Settings.load(ROOT / "configs/default.yaml").open_answers.pairs.rewritten_above
    kind, change = open_answer_pairs.classify(DRAFT_WHY, case["final"], limit)
    assert kind == case["kind"]
    assert (kind, change) == open_answer_pairs.classify(DRAFT_WHY, case["final"], limit)  # the same every time
    if kind == "unchanged":
        assert change == 0
    elif kind == "edited":
        assert 0 < change <= limit
    else:
        assert change > limit


def test_the_thresholds_and_the_watch_are_provisional_values_in_the_config():
    text = (ROOT / "configs/default.yaml").read_text(encoding="utf-8")
    loaded = yaml.safe_load(text)["open_answers"]
    assert loaded["pairs"] == {"dir": "open-answers/pairs", "rewritten_above": 0.5, "ask_reason_above": 0.5}
    assert loaded["watch"] == {"timeout_seconds": 1800, "poll_seconds": 5, "read_seconds": 30}
    old = {key: value for key, value in yaml.safe_load(text).items() if key != "open_answers"}  # an older workspace
    defaults = Settings.model_validate(old).open_answers
    assert (defaults.pairs.rewritten_above, defaults.watch.poll_seconds, defaults.max_chars) == (0.5, 5, 16000)


# --- P9: the drafts the checkpoint service records -------------------------------------------------------------


SHEET = checkpoint.sheet_phrases({"contact.email": AnswerEntry(key="contact.email", from_profile="personal.email")})


def test_a_filled_checkpoint_records_the_open_answers_from_its_own_read(tmp_path):
    browser = PageBrowser()
    session = checkpoint.Session(checkpoint_config(tmp_path, sheet=SHEET), browser, REDACTOR)
    url = url_of("acme-ml-01")
    browser.show("N1", form(why="", other=""))
    session.checkpoint("blank", url)
    browser.show("N1", form(other="Call me at 555-0100 any time."))
    marks = [*MARKS, {"field": "p1.9", "type": "other"}, {"field": "p1.2", "type": "about_me"}]
    answer = session.checkpoint("filled", url, open_answers=marks)
    drafts = json.loads((fills(tmp_path) / open_answer_pairs.DRAFTS_FILE).read_text(encoding="utf-8"))
    assert [(d["field"], d["page"], d["type"]) for d in drafts] == [
        ("p1.3", 1, "why_role"),
        ("p1.4", 1, "anything_else"),
    ]
    assert (drafts[0]["question"], drafts[0]["text"], drafts[0]["control"]) == (WHY, flat(DRAFT_WHY), "textarea")
    assert drafts[1]["text"] == "Call me at [phone] any time."  # masked before it is written
    assert answer.startswith("Recorded page p1")  # the page itself is recorded as before
    assert "p1.3" in answer and "p1.4" in answer  # recorded, by number
    assert "p1.9 (not on this page)" in answer and "p1.2 (a contact field)" in answer
    assert flat(DRAFT_WHY) not in answer and "555-0100" not in answer  # the answer never quotes a value


def test_an_empty_or_non_text_field_is_not_an_open_answer(tmp_path):
    browser = PageBrowser()
    session = checkpoint.Session(checkpoint_config(tmp_path), browser, REDACTOR)
    url = url_of("acme-ml-01")
    page = form(other="")
    page["frames"][0]["controls"].append({**control(5, "Do you need a visa?", "", kind="checkbox"), "checked": True})
    browser.show("N1", page)
    answer = session.checkpoint("filled", url, open_answers=[*MARKS, {"field": "p1.5", "type": "other"}])
    drafts = json.loads((fills(tmp_path) / open_answer_pairs.DRAFTS_FILE).read_text(encoding="utf-8"))
    assert [d["field"] for d in drafts] == ["p1.3"]
    assert "p1.4 (empty)" in answer and "p1.5 (not a text field)" in answer


def test_a_blank_checkpoint_never_records_an_open_answer(tmp_path):
    browser = PageBrowser()
    session = checkpoint.Session(checkpoint_config(tmp_path), browser, REDACTOR)
    browser.show("N1", form())
    answer = session.checkpoint("blank", url_of("acme-ml-01"), open_answers=MARKS)
    assert "p1.3" in answer and 'only with stage "filled"' in answer
    assert not (fills(tmp_path) / open_answer_pairs.DRAFTS_FILE).exists()


def test_a_type_outside_the_closed_set_refuses_the_whole_call(tmp_path):
    browser = PageBrowser()
    session = checkpoint.Session(checkpoint_config(tmp_path), browser, REDACTOR)
    browser.show("N1", form())
    answer = session.checkpoint("filled", url_of("acme-ml-01"), open_answers=[{"field": "p1.3", "type": "hobbies"}])
    assert "open_answers" in answer and "nothing was recorded" in answer.lower()
    assert not (fills(tmp_path) / open_answer_pairs.DRAFTS_FILE).exists()
    assert not (fills(tmp_path) / "p1.values.json").exists()


def test_the_checkpoint_tool_schema_takes_open_answers_with_the_closed_set():
    class Stub:
        def checkpoint(self, stage, url, open_answers=None):
            return f"{stage} {url} {len(open_answers or [])}"

    server = checkpoint.build_server(Stub())
    (tool,) = asyncio.run(server.list_tools())
    schema = tool.input_schema
    assert set(schema["properties"]) == {"stage", "url", "open_answers"}
    (mark,) = schema["$defs"].values()
    assert mark["properties"]["type"]["enum"] == list(open_answers.QUESTION_TYPES)
    good = {"stage": "filled", "url": "u", "open_answers": [{"field": "p1.3", "type": "why_role"}]}
    assert "filled u 1" in str(asyncio.run(server.call_tool(checkpoint.TOOL, good)))
    with pytest.raises(Exception, match="type"):
        bad = {"stage": "filled", "open_answers": [{"field": "p1.3", "type": "hobbies"}]}
        asyncio.run(server.call_tool(checkpoint.TOOL, bad))


def test_the_filling_instructions_tell_the_agent_to_report_its_open_answers():
    rules = runner.CHECKPOINT_RULES
    assert "open_answers" in rules and 'stage "filled"' in rules
    for name in open_answers.QUESTION_TYPES:
        assert name in rules


# --- pairs: which jobs get them ----------------------------------------------------------------------------------


def test_a_filled_job_gets_one_pair_per_open_answer_with_the_agent_s_last_value(box):
    box.form_fill_runner.drafts["acme-ml-01"] = (DRAFT_WHY, DRAFT_ELSE)
    run_id = run(box, {"acme-ml-01": [form(), thanks()]})
    why, other = pair(box, run_id), pair(box, run_id, field="p1.4")
    assert (why["job_id"], why["company"], why["type"], why["field"]["question"]) == (
        "acme-ml-01",
        "Acme Robotics",
        "why_role",
        WHY,
    )
    assert (why["draft"]["text"], other["draft"]["text"], other["type"]) == (
        flat(DRAFT_WHY),
        DRAFT_ELSE,
        "anything_else",
    )
    assert why["tab"]["id"] == "N-acme-ml-01" and why["tab"]["host"] == "jobs.example.test"
    assert why["id"] == f"{run_id}.acme-ml-01.p1.3"
    assert sorted(p.name for p in pairs_dir(box).iterdir()) == [
        f"{run_id}.acme-ml-01.p1.3.json",
        f"{run_id}.acme-ml-01.p1.4.json",
    ]
    meta = json.loads((box.data_dir / "form-fill/runs" / run_id / "acme-ml-01/meta.json").read_text("utf-8"))
    assert meta["open_answers"] == 2


def test_the_draft_is_the_read_after_the_agent_quit(tmp_path):
    fill = tmp_path / "fills" / "run-1"
    fill.mkdir(parents=True)
    draft = {"field": "p1.3", "page": 1, "type": "why_role", "question": WHY, "section": "Apply", "occurrence": 1}
    (fill / open_answer_pairs.DRAFTS_FILE).write_text(
        json.dumps([{**draft, "control": "textarea", "text": "at the filled checkpoint", "read_at": "t1"}]), "utf-8"
    )
    final = {"page": 1, "read_at": "t2", "fields": [{"id": "p1.3", "question": WHY, "origin": "control"}]}
    final["fields"][0].update(control="textarea", value="after the agent quit", section="Apply")
    (fill / "final.json").write_text(json.dumps(final), encoding="utf-8")
    tab = {"id": "N1", "url": url_of("acme-ml-01"), "generation": "synthetic"}
    (made,) = open_answer_pairs.pairs_from_run(
        fill, run_id="run-1", job_id="acme-ml-01", company="Acme", title="ML", recording_id="r", tab=tab
    )
    assert (made.draft.text, made.draft.read_at) == ("after the agent quit", "t2")
    final["page"] = 2  # the agent ended on another page: the filled checkpoint's value stands
    (fill / "final.json").write_text(json.dumps(final), encoding="utf-8")
    (made,) = open_answer_pairs.pairs_from_run(
        fill, run_id="run-1", job_id="acme-ml-01", company="Acme", title="ML", recording_id="r", tab=tab
    )
    assert made.draft.text == "at the filled checkpoint"


@pytest.mark.parametrize("outcome", [("failed", "captcha"), ("failed", "needs_answer")])
def test_pairs_wait_only_for_jobs_left_filled_in_their_tab(box, outcome):
    box.form_fill_runner.outcomes["acme-ml-01"] = outcome
    run_id = run(box, {"acme-ml-01": [form(), thanks()]})
    names = sorted(p.name for p in pairs_dir(box).iterdir()) if pairs_dir(box).is_dir() else []
    if outcome[1] == "captcha":  # held at a gate: refilled later, its drafts recorded again then
        assert names == []
    else:  # filled and read back, stopped early: the applicant may still finish and submit it by hand
        assert names == [f"{run_id}.acme-ml-01.p1.3.json", f"{run_id}.acme-ml-01.p1.4.json"]


def test_only_form_fill_jobs_of_an_apply_step_get_pairs(box):  # P14
    box.settings = box.settings.model_copy(update={"apply": box.settings.apply.model_copy(update={"review_first": 0})})
    box.apply_runner = FakeRunner()
    box.apply_in_background = False
    jobs = add_jobs(box, ["Stripe, Inc.", "Acme"])  # Stripe is on the careful list: the form-fill module fills it
    write_letters(box, jobs)
    batch = box.prepare_apply_batch(DAY)
    box.form_fill_runner.script = {"N-j00": [form("j00"), thanks("j00")]}
    box.start_applying(batch["batch_id"], batch["summary"])
    made = [json.loads(p.read_text(encoding="utf-8")) for p in pairs_dir(box).iterdir()]
    assert {p["job_id"] for p in made} == {"j00"}
    assert all(p["status"] == "final" and p["final"]["ended"] == "received" for p in made)
    (submitted,) = box.apply_runner.calls  # Acme, auto-submitted by ApplyPilot's agent: no checkpoint, no drafts
    assert submitted.job_id == "j01" and "open_answers" not in (submitted.sheet or "")
    assert all("open_answers" in prompt for prompt in box.form_fill_runner.prompts)


# --- P4-P8: reading the final back -----------------------------------------------------------------------------


def test_the_final_is_the_last_read_before_the_page_says_received(box):  # P4
    received = form(why="A value shown after the submit", notice="Thank you for applying!")
    run_id = run(box, {"acme-ml-01": [form(), form(why=EDITED_WHY), received]})
    why, other = pair(box, run_id), pair(box, run_id, field="p1.4")
    assert (why["status"], why["final"]["text"], why["final"]["ended"]) == ("final", flat(EDITED_WHY), "received")
    assert why["kind"] == "edited" and 0 < why["change"] <= 0.5
    assert (other["kind"], other["change"], other["final"]["text"]) == ("unchanged", 0, DRAFT_ELSE)
    watched = [call for call in box.form_fill_runner.calls if call not in ("targets", "ensure_chrome")]
    assert watched[-3:] == ["read_fields N-acme-ml-01"] * 3  # after the fill and the read back, only reads


def test_a_closed_tab_keeps_the_last_read(box):  # P5
    close = lambda box: box.form_fill_runner.close("acme-ml-01")  # noqa: E731
    run_id = run(box, {"acme-ml-01": [form(), form(why=REWRITTEN_WHY)]}, None, close)
    why = pair(box, run_id)
    assert (why["final"]["text"], why["final"]["ended"], why["kind"]) == (flat(REWRITTEN_WHY), "closed", "rewritten")


def test_a_tab_that_leaves_the_job_s_site_keeps_the_last_read_and_is_never_read_there(box):  # P6
    move = lambda box: box.form_fill_runner.move("acme-ml-01", "https://mail.example.org/inbox")  # noqa: E731
    run_id = run(box, {"acme-ml-01": [form(), form(why=EDITED_WHY)]}, None, move)
    why = pair(box, run_id)
    assert (why["final"]["text"], why["final"]["ended"]) == (flat(EDITED_WHY), "left")
    assert box.form_fill_runner.calls.count("read_fields N-acme-ml-01") == 2  # never once on the other site


def test_done_editing_reads_that_tab_at_once_and_the_others_go_on(box):  # P7
    script = {
        "acme-ml-01": [form(), form(why=EDITED_WHY)],
        "beta-ds-02": [form("beta-ds-02"), form("beta-ds-02"), form("beta-ds-02"), thanks("beta-ds-02")],
    }
    run_id = run(box, script, None, says_done("acme-ml-01"), jobs=("acme-ml-01", "beta-ds-02"))
    acme, beta = pair(box, run_id), pair(box, run_id, "beta-ds-02")
    assert (acme["final"]["ended"], acme["final"]["text"]) == ("user_done", flat(EDITED_WHY))
    assert (beta["final"]["ended"], beta["kind"]) == ("received", "unchanged")


def test_done_editing_after_the_watch_ended_starts_a_new_one_that_reads_at_once(box):  # P7
    short_watch(box)
    run_id = run(box, {"acme-ml-01": [form()]})  # nobody edits within the watch: it ends at its time limit
    assert pair(box, run_id)["status"] == "watching"
    box.form_fill_runner.script["N-acme-ml-01"] = [form(why=REWRITTEN_WHY)]  # the applicant came back and edited
    reads = box.form_fill_runner.calls.count("read_fields N-acme-ml-01")
    status = box.open_answer_pairs(read_now=True)
    assert status["reading_now"] == "all"
    why = pair(box, run_id)
    assert (why["final"]["ended"], why["final"]["text"], why["kind"]) == ("user_done", flat(REWRITTEN_WHY), "rewritten")
    assert box.form_fill_runner.calls.count("read_fields N-acme-ml-01") == reads + 1  # one read, at once
    assert pair(box, run_id, field="p1.4")["final"]["ended"] == "user_done"  # the same page shows both


def test_only_what_this_watch_saw_counts(box):  # P8
    short_watch(box)
    script = {"acme-ml-01": [form(why=EDITED_WHY)], "beta-ds-02": [form("beta-ds-02")]}
    run_id = run(box, script, jobs=("acme-ml-01", "beta-ds-02"))
    waiting = pair(box, run_id)
    assert (waiting["status"], waiting["last_seen"]["text"], waiting["final"]) == ("watching", flat(EDITED_WHY), None)
    status = box.form_fill_status()["open_answers"]
    assert status["waiting"] == 4 and status["questions"] == 0
    # nobody watched while the applicant submitted one and closed the other
    box.form_fill_runner.script["N-acme-ml-01"] = [thanks()]
    box.form_fill_runner.close("beta-ds-02")
    box.open_answer_pairs()  # starts watching again (inline here)
    acme, beta = pair(box, run_id), pair(box, run_id, "beta-ds-02")
    assert (acme["status"], acme["kind"]) == ("no_final", None) and acme["no_final"]
    assert beta["status"] == "no_final"
    assert box.open_answer_pairs()["questions"] == []


def test_the_watch_never_starts_the_applying_chrome(box):
    short_watch(box)
    box.form_fill_runner.running = True
    run_id = run(box, {"acme-ml-01": [form(why=EDITED_WHY)]})
    box.form_fill_runner.running = False  # the applicant quit Chrome: the tabs are gone
    calls = len(box.form_fill_runner.calls)
    box.open_answer_pairs()
    assert "ensure_chrome" not in box.form_fill_runner.calls[calls:]
    assert pair(box, run_id)["status"] == "no_final"


# --- P10-P13: the questions ----------------------------------------------------------------------------------------


def settled(box) -> str:
    """One run whose open answers ended final: p1.3 rewritten (P3), p1.4 edited."""
    run_id = run(box, {"acme-ml-01": [form(why=REWRITTEN_WHY, other=EDITED_ELSE), thanks()]})
    assert pair(box, run_id)["kind"] == "rewritten" and pair(box, run_id, field="p1.4")["kind"] == "edited"
    return run_id


def test_a_rewrite_gets_a_reason_question_and_an_edit_only_the_promotion(box):
    run_id = settled(box)
    why, other = f"{run_id}.acme-ml-01.p1.3", f"{run_id}.acme-ml-01.p1.4"
    asked = questions(box)
    assert set(asked) == {f"{why}:reason", f"{why}:promote", f"{other}:promote"}
    reason = asked[f"{why}:reason"]
    assert [(o["n"], o["text"]) for o in reason["options"]] == [
        (1, "内容取舍：选的事实或经历不对"),
        (2, "动机：没写出我为什么想要这份工作"),
        (3, "口吻：不像我说话"),
        (4, "太长"),
        (5, "其他（我自己说）"),
        (0, "跳过"),
    ]
    assert reason["options"][4]["needs"] == "text" and reason["question"] == WHY and reason["kind"] == "rewritten"
    promote = asked[f"{other}:promote"]
    assert [o["n"] for o in promote["options"]] == [1, 2, 3, 4, 0]
    assert "anything_else" in promote["options"][0]["text"] and promote["options"][1]["needs"] == "question_type"
    assert promote["options"][3]["needs"] == "text" and promote["options"][-1]["text"] == "跳过"


def test_a_reason_is_recorded_only_from_the_user_s_reply_and_their_words_verbatim(box):  # P10
    run_id = settled(box)
    question = f"{run_id}.acme-ml-01.p1.3:reason"
    with pytest.raises(InvariantError, match="user_report"):
        box.open_answer_choice(question, 2, user_report=" ")
    with pytest.raises(InvariantError, match="text"):
        box.open_answer_choice(question, 5, user_report="5")
    words = "I would never call myself a self-starter."
    result = box.open_answer_choice(question, 5, user_report=f"选 5：{words}", text=words)
    assert result["recorded"] == "reason"
    saved = pair(box, run_id)["reason"]
    assert (saved["option"], saved["key"], saved["text"]) == (5, "other", words)
    assert question not in questions(box)
    with pytest.raises(InvariantError, match="不用回答"):
        box.open_answer_choice(question, 1, user_report="1")


def test_skipping_records_nothing_about_the_answer(box):
    run_id = settled(box)
    box.open_answer_choice(f"{run_id}.acme-ml-01.p1.3:reason", 0, user_report="跳过")
    assert pair(box, run_id)["reason"] == {"skipped": True, "at": pair(box, run_id)["reason"]["at"]}
    box.open_answer_choice(f"{run_id}.acme-ml-01.p1.3:promote", 0, user_report="跳过")
    assert set(pair(box, run_id)["promotion"]) == {"skipped", "at"}
    assert (box.data_dir / "applicant/open_answers.yaml").read_text(encoding="utf-8") == BANK


def bank_entries(box):
    return open_answers.load(box.data_dir / "applicant/open_answers.yaml", REDACTOR, 16000).entries


def test_promoting_appends_the_final_verbatim_and_keeps_the_file_as_it_was(box):  # P11
    run_id = settled(box)
    result = box.open_answer_choice(f"{run_id}.acme-ml-01.p1.4:promote", 1, user_report="收进去")
    assert result["recorded"] == "promoted" and result["entry_id"] == "pair-acme-ml-01-p1-4"
    text = (box.data_dir / "applicant/open_answers.yaml").read_text(encoding="utf-8")
    assert text.startswith(BANK)  # the applicant's own lines and comments, unchanged
    old, new = bank_entries(box)
    assert old.id == "about-me"
    assert (new.id, new.type, new.question, new.text, new.status) == (
        "pair-acme-ml-01-p1-4",
        "anything_else",
        ELSE,
        EDITED_ELSE,
        "accepted",
    )
    assert new.from_ == f"choice {run_id}.acme-ml-01.p1.4:promote#1" and new.added and not new.source_words
    assert pair(box, run_id, field="p1.4")["promotion"]["entry_id"] == new.id
    assert f"{run_id}.acme-ml-01.p1.4:promote" not in questions(box)
    section = open_answers.section(open_answers.load(box.data_dir / "applicant/open_answers.yaml", None, 16000))
    assert EDITED_ELSE in section  # the filling agents get it from the next run on


def test_promoting_under_another_type_and_declining(box):
    run_id = settled(box)
    with pytest.raises(InvariantError, match="question_type"):
        box.open_answer_choice(f"{run_id}.acme-ml-01.p1.3:promote", 2, user_report="2", question_type="hobbies")
    box.open_answer_choice(f"{run_id}.acme-ml-01.p1.3:promote", 2, user_report="2，origin", question_type="origin")
    assert bank_entries(box)[-1].type == "origin" and bank_entries(box)[-1].text == flat(REWRITTEN_WHY)
    box.open_answer_choice(f"{run_id}.acme-ml-01.p1.4:promote", 3, user_report="不收")
    assert pair(box, run_id, field="p1.4")["promotion"]["option"] == 3
    assert len(bank_entries(box)) == 2


def test_a_final_with_contact_details_is_not_promoted_and_never_edited(box):  # P12
    box.form_fill_runner.drafts["acme-ml-01"] = (DRAFT_WHY, DRAFT_ELSE)
    run_id = run(box, {"acme-ml-01": [form(other=EDITED_ELSE + " Call me at 555-0100."), thanks()]})
    question = f"{run_id}.acme-ml-01.p1.4:promote"
    result = box.open_answer_choice(question, 1, user_report="收")
    assert result["recorded"] == "rejected" and "联系方式" in result["message"]
    assert (box.data_dir / "applicant/open_answers.yaml").read_text(encoding="utf-8") == BANK
    narrowed = questions(box)[question]
    assert "联系方式" in narrowed["ask"] and [o["n"] for o in narrowed["options"]] == [3, 4, 0]
    with pytest.raises(InvariantError):
        box.open_answer_choice(question, 1, user_report="还是收")
    box.open_answer_choice(question, 4, user_report="我改一下", text=EDITED_ELSE)
    assert bank_entries(box)[-1].text == EDITED_ELSE


def test_over_the_budget_is_not_promoted_and_never_cut(box):  # P12
    run_id = settled(box)
    size = len(open_answers.section(open_answers.load(box.data_dir / "applicant/open_answers.yaml", None, 16000)))
    budget(box, size + 20)
    question = f"{run_id}.acme-ml-01.p1.3:promote"
    result = box.open_answer_choice(question, 1, user_report="收")
    assert result["recorded"] == "rejected" and f"上限 {size + 20}" in result["message"]
    assert (box.data_dir / "applicant/open_answers.yaml").read_text(encoding="utf-8") == BANK
    assert [o["n"] for o in questions(box)[question]["options"]] == [3, 4, 0]


def test_own_words_go_in_verbatim_and_a_chinese_text_is_rendered_by_choice(box):  # P13
    run_id = settled(box)
    english = PAIRS["own_words_en"]
    box.open_answer_choice(f"{run_id}.acme-ml-01.p1.3:promote", 4, user_report=english, text=english)
    entry = bank_entries(box)[-1]
    assert (entry.text, entry.source_words) == (english, (english,))
    assert entry.from_ == f"own words {run_id}.acme-ml-01.p1.3:promote#4"
    chinese, renderings = PAIRS["own_words_zh"], PAIRS["renderings"]
    question = f"{run_id}.acme-ml-01.p1.4:promote"
    for bad in ([renderings[0]], [*renderings, "a", "b"], [renderings[0], renderings[0]], ["", renderings[1]]):
        with pytest.raises(InvariantError, match="renderings"):
            box.open_answer_choice(question, 4, user_report=chinese, text=chinese, renderings=bad)
    result = box.open_answer_choice(question, 4, user_report=chinese, text=chinese, renderings=renderings)
    assert result["recorded"] == "renderings" and len(bank_entries(box)) == 2  # nothing written yet
    render = questions(box)[f"{run_id}.acme-ml-01.p1.4:render"]
    assert [(o["n"], o["text"]) for o in render["options"]] == [
        (1, renderings[0]),
        (2, renderings[1]),
        (3, "都不对，我自己写"),
        (0, "跳过"),
    ]
    box.open_answer_choice(f"{run_id}.acme-ml-01.p1.4:render", 2, user_report="第二个")
    entry = bank_entries(box)[-1]
    assert (entry.text, entry.source_words) == (renderings[1], (chinese,))
    assert entry.from_ == f"choice {run_id}.acme-ml-01.p1.4:render#2 (rendering of own words)"


def test_without_a_bank_there_is_no_promotion_question(box):
    path = box.data_dir / "applicant" / "applicant.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config.pop("open_answers")
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    run_id = settled(box)
    assert set(questions(box)) == {f"{run_id}.acme-ml-01.p1.3:reason"}
    with pytest.raises(InvariantError, match="open_answers"):
        box.open_answer_choice(f"{run_id}.acme-ml-01.p1.3:promote", 1, user_report="收")


ENTRY = {
    "id": "pair-acme-ml-01-p1-3",
    "type": "why_role",
    "question": WHY,
    "text": "Search quality is the problem I keep coming back to.",
    "status": "accepted",
    "from": "choice 20261106-101500-ab12.acme-ml-01.p1.3:promote#1",
    "added": "2026-11-06",
}


@pytest.mark.parametrize(
    "bank",
    [
        "# SYNTHETIC\nversion: 1\nentries: []\n",
        "# SYNTHETIC\nentries:\n- id: about-me\n  type: about_me\n  question: Q?\n  text: A.\n  status: draft\n\n"
        "# the version comes last here\nversion: 1\n",
    ],
    ids=["empty flow list", "version after entries"],
)
def test_the_entry_point_appends_whatever_the_layout_and_keeps_every_line(tmp_path, bank):
    path = tmp_path / "open_answers.yaml"
    path.write_text(bank, encoding="utf-8")
    added = open_answers.add_entry(path, ENTRY, REDACTOR, 16000)
    loaded = open_answers.load(path, REDACTOR, 16000)
    assert loaded.entries[-1] == added and added.text == ENTRY["text"] and added.from_ == ENTRY["from"]
    kept = [line for line in bank.splitlines() if line != "entries: []"]
    lines = path.read_text(encoding="utf-8").splitlines()
    assert all(line in lines for line in kept)  # the applicant's lines, comments included, are all still there
    again = open_answers.add_entry(path, ENTRY, REDACTOR, 16000)  # the same id once more
    assert again.id == "pair-acme-ml-01-p1-3-2"


def test_the_entry_point_never_writes_into_a_bank_that_fails_its_checks(tmp_path):
    path = tmp_path / "open_answers.yaml"
    broken = "version: 2\nentries: []\n"
    path.write_text(broken, encoding="utf-8")
    with pytest.raises(InvariantError, match="version must be 1"):
        open_answers.add_entry(path, ENTRY, REDACTOR, 16000)
    assert path.read_text(encoding="utf-8") == broken
    path.write_text("version: 1\nentries: []\n", encoding="utf-8")
    with pytest.raises(open_answers.Rejected) as rejected:
        open_answers.add_entry(path, {**ENTRY, "text": "Reach me at sam.synthetic@example.test."}, REDACTOR, 16000)
    assert rejected.value.kind == "contact" and "sam.synthetic" not in str(rejected.value)
    with pytest.raises(open_answers.Rejected) as rejected:
        open_answers.add_entry(path, ENTRY, REDACTOR, 100)
    assert (rejected.value.kind, rejected.value.max) == ("budget", 100) and rejected.value.size > 100
    assert path.read_text(encoding="utf-8") == "version: 1\nentries: []\n"


# --- P15: privacy ----------------------------------------------------------------------------------------------


def test_pairs_are_masked_and_their_texts_never_leave_through_a_tool(box):
    box.form_fill_runner.drafts["acme-ml-01"] = (DRAFT_WHY + " Write to sam.synthetic@example.test.", DRAFT_ELSE)
    run_id = run(box, {"acme-ml-01": [form(why=REWRITTEN_WHY + " Or call 555-0100."), thanks()]})
    saved = pair(box, run_id)
    assert saved["draft"]["text"].endswith("Write to [email].") and saved["final"]["text"].endswith("call [phone].")
    assert total_hits(REDACTOR.scan([pairs_dir(box)])) == 0
    shown = json.dumps([box.open_answer_pairs(), box.form_fill_status()], ensure_ascii=False)
    for text in (DRAFT_WHY, REWRITTEN_WHY, DRAFT_ELSE):
        assert flat(text) not in shown
    assert str(pairs_dir(box)) in shown  # the applicant reads them in the files
    database = box.store.path.read_bytes()
    assert REWRITTEN_WHY.split("\n")[0].encode() not in database and DRAFT_WHY[:40].encode() not in database
    assert "Search quality" not in json.dumps(box.get_tagging_rules(), ensure_ascii=False)


# --- P16: making way -------------------------------------------------------------------------------------------


def wait_until(check, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def background(box) -> None:
    box.watch_in_background = True
    box.clock = time.monotonic
    box.sleep = lambda seconds: time.sleep(0.01)


def test_a_new_run_ends_the_watch_first_and_the_pairs_go_on_waiting(box):
    background(box)
    first = box.start_form_fill(job_list(box))["run_id"]
    wait_until(lambda: box._watch is not None and box.store.activity() is not None)
    watch = box._watch
    assert box.store.activity()["run_id"] == watch["run_id"] and watch["run_id"].startswith("watch-")
    assert box.apply_status().get("other_session") is None  # this server's own watch, not another session
    wait_until(lambda: pair(box, first)["last_seen"] is not None)
    box.form_fill_runner.drafts["beta-ds-02"] = (DRAFT_WHY, DRAFT_ELSE)
    second = box.start_form_fill(job_list(box, ("beta-ds-02",)))["run_id"]
    assert watch["closed"] and watch["status"] == "stopped"
    assert pair(box, first)["status"] == "watching"  # it goes on waiting for its final
    wait_until(lambda: box._watch is not watch and box._watch is not None)
    box.stop_applying()  # "停下" also ends a watch
    wait_until(lambda: box._watch["closed"])
    assert box.store.activity() is None
    assert pair(box, second, "beta-ds-02")["status"] == "watching"


def test_another_session_s_watch_gives_way(box, monkeypatch):
    other = (424242, "another-session")
    box.store.begin_activity(other, "form_fill", run_id="watch-20261106-090000-abcd")
    real_alive = watchdog.alive
    monkeypatch.setattr(watchdog, "alive", lambda pid, started: (pid, started) == other or real_alive(pid, started))
    assert "读回开放题定稿" in box.apply_status()["other_session"]["message"]
    run(box, {"acme-ml-01": [form(), thanks()]})
    assert box.store.activity() is None
    box.store.begin_activity(other, "form_fill", run_id="a-real-fill")  # a fill of another session: never taken over
    with pytest.raises(InvariantError, match="另一个会话"):
        box.start_form_fill(job_list(box))


def test_the_shared_wait_finds_a_kept_tab_only_in_its_own_chrome():
    """The live re-run of the gate list waits on the same code; tests/test_gate_list.py covers that wait itself."""
    tabs = {"j": {"id": "A", "generation": "g"}}

    class Browser:
        def targets(self):
            return [{"id": "A", "generation": "other", "url": "x"}]

    said, ended = watch.wait(
        Browser(),
        tabs,
        since=0,
        clock=lambda: 0,
        sleep=lambda s: None,
        poll_seconds=1,
        timeout_seconds=10,
        interrupt=lambda: None,
        look=lambda key, current: None,
    )
    assert (said, ended) == ("ended", {"j": "closed"})  # the same id in another Chrome is not this tab


# --- the tools --------------------------------------------------------------------------------------------------


def test_the_two_tools_are_prepare_tier_and_never_scheduled():
    assert TOOL_TIERS["open_answer_pairs"] == TOOL_TIERS["open_answer_choice"] == "prepare"
    assert not {"open_answer_pairs", "open_answer_choice"} & (SCHEDULED_TOOLS | COMMIT_TOOLS)


def test_the_status_lists_the_watch_and_the_waiting_pairs(box):
    short_watch(box)
    run_id = run(box, {"acme-ml-01": [form()]})
    status = box.open_answer_pairs()  # starts watching again; inline here it runs to its time limit
    assert status["pairs_dir"] == str(pairs_dir(box))
    assert {(w["pair_id"], w["question"], w["type"]) for w in status["waiting"]} == {
        (f"{run_id}.acme-ml-01.p1.3", WHY, "why_role"),
        (f"{run_id}.acme-ml-01.p1.4", ELSE, "anything_else"),
    }
    assert status["watch"]["status"] in ("done", "timeout") and "改完了" in status["message"]


# --- real browser (synthetic local page) ------------------------------------------------------------------------


def _target_id(page) -> str:
    session = page.context.new_cdp_session(page)
    try:
        return session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
    finally:
        session.detach()


def test_tab_fields_reads_a_text_area_in_a_real_browser(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        context, binding = start_owned(tmp_path, p)
        try:
            page = context.new_page()
            page.set_content(
                f"<h1>Apply</h1><label for='why'>{WHY}</label><textarea id='why'></textarea>"
                "<button type='button'>Submit application</button>"
            )
            page.fill("#why", EDITED_WHY)
            target = {"id": _target_id(page), "url": page.url, "generation": binding.generation}
            result = {}
            reader = threading.Thread(
                target=lambda: result.update(
                    read=readback.tab_fields(binding, target, 20, expected=(binding.root, binding.db_path))
                )
            )
            reader.start()
            while reader.is_alive():  # Playwright's sync API answers only while this thread asks it something
                page.wait_for_timeout(50)
            state, snap = result["read"]
        finally:
            finish_owned(binding)
    (field,) = [f for f in snap.fields if f.question == WHY]
    assert (field.control, field.value) == ("textarea", flat(EDITED_WHY))
    assert state.before_submit and not state.received


def test_a_read_only_connection_leaves_the_page_s_dialogs_alone(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        context, binding = start_owned(tmp_path, p)
        try:
            page = context.new_page()
            page.set_content("<h1>Apply</h1>")
            seen = []
            page.on("dialog", lambda dialog: seen.append(dialog))  # the person or agent the dialog is for
            connected, done = threading.Event(), threading.Event()

            def hold():
                with readback._connected({}, binding, (binding.root, binding.db_path)):
                    connected.set()
                    done.wait(10)

            holder = threading.Thread(target=hold)
            holder.start()
            while not connected.is_set():
                page.wait_for_timeout(50)
            page.evaluate("setTimeout(() => { document.title = 'confirm=' + confirm('Submit?') }, 50)")
            page.wait_for_timeout(1500)
            done.set()
            while holder.is_alive():
                page.wait_for_timeout(50)
            assert len(seen) == 1
            seen[0].accept()
            page.wait_for_timeout(300)
            title = page.title()
        finally:
            finish_owned(binding)
    assert title == "confirm=true"  # not dismissed by the read-only connection meanwhile


def test_the_runner_reads_fields_through_the_readback(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(readback, "tab_fields", lambda *args, **kwargs: calls.append((args, kwargs)) or "read")
    s = settings()
    s = s.model_copy(
        update={"storage": s.storage.model_copy(update={"data_dir": str(tmp_path), "db_path": str(tmp_path / "a.db")})}
    )
    ff = runner.FormFillRunner(tmp_path, s, tmp_path / "profile.json")
    ff.binding = synthetic_binding()
    assert ff.read_fields({"id": "N1"}) == "read"
    ((args, kwargs),) = calls
    assert args == (ff.binding, {"id": "N1"}, s.open_answers.watch.read_seconds)
    assert kwargs["expected"] == browser_session.scope(s)
