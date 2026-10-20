"""Form-fill runner and its two tools (specs/form-fill.plan.md sections 1-3): the answer-sheet instructions, a run
end to end with a fake browser, the automatic feasibility checks, and mutual exclusion with /apply.
All data is SYNTHETIC; nothing here starts Chrome, Claude Code or a browser."""

import json
import threading
import time
import tomllib

import pytest
import yaml

from intent_job_agent import apply_bridge
from intent_job_agent.domain import InvariantError
from intent_job_agent.form_fill import readback, recording, runner
from intent_job_agent.form_fill.inputs import load_answers, load_checklist, profile_field_paths
from intent_job_agent.form_fill.models import JobReport, PageState, ReadField
from intent_job_agent.form_fill.readback import ReadbackError
from intent_job_agent.form_fill.recording import RecordingStore, recording_id
from intent_job_agent.form_fill.redact import Redactor, total_hits
from intent_job_agent.store import Store
from intent_job_agent.tools import COMMIT_TOOLS, SCHEDULED_TOOLS, TOOL_TIERS, Toolbox

from .conftest import BASE_INTENT, BASE_VOCAB, ROOT, settings
from .test_slice6 import DAY, PROFILE, SECRETS, add_jobs, write_applicant, write_letters

RELOCATION = "Yes, anywhere in the United States."
ANSWERS = """# SYNTHETIC test data
version: 1
entries:
  name.legal:
    from_profile: personal.full_name
  name.first:
    answer: Sam
    kind: text
  name.last:
    answer: Example
    kind: text
  contact.email:
    from_profile: personal.email
  contact.phone:
    from_profile: personal.phone
    asks: ["mobile number"]
  work_auth.authorized_us:
    answer: "Yes"
    kind: yes_no
    asks: ["legally authorized to work", "authorized to work in the united states"]
  work_auth.sponsorship_future:
    answer: "Yes"
    kind: yes_no
    asks: ["now or in the future"]
  eeo.hispanic_latino:
    answer: Decline to self-identify
    accept: ["Prefer not to say"]
    otherwise: "No"
    kind: choice
    asks: ["hispanic or latino"]
  education.ms:
    kind: record
    school: Example State University
    aliases: ["ESU"]
    degree: Master of Science
    major: Data Science
    start: 2025-08
    end: 2027-05
    accept:
      degree: ["M.S.", "MS"]
  location.relocation:
    from_applicant: relocation
"""
CHECKLIST = """# SYNTHETIC test data
version: 1
items:
  - id: no_hard_breaks
    rule: Pasted resume or letter text has no line breaks in the middle of a sentence.
    level: format
  - id: open_answers_specific
    rule: Open answers are 2-3 sentences and name something specific about the job.
    level: format
"""
FIELDS = (  # what the fake browser "reads back"; the e-mail and phone are the synthetic profile's own
    ReadField(id="p1.1", question="First Name", origin="control", control="text", value="Sam", required=True),
    ReadField(id="p1.2", question="Email", origin="control", control="text", value="sam.synthetic@example.test"),
    ReadField(
        id="p1.3",
        question="Are you legally authorized | to work?",
        origin="control",
        control="radio",
        value="Yes",
        options=("Yes", "No"),
    ),
    ReadField(id="p1.4", question="Phone", origin="review_text", control="review", value="555-0100"),
)
LOG_SECRET_LINE = "Typed sam.synthetic@example.test and 555-0100 into the form."


def wait_until(check, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def stream(*blocks, kind="assistant") -> str:
    if kind == "assistant":
        return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})
    return json.dumps({"type": "user", "message": {"role": "user", "content": list(blocks)}})


def tool_use(name: str, **arguments) -> dict:
    return {"type": "tool_use", "id": "t", "name": f"mcp__playwright__{name}", "input": arguments}


def tool_result(text: str, as_blocks: bool = True) -> dict:
    content = [{"type": "text", "text": text}] if as_blocks else text
    return {"type": "tool_result", "tool_use_id": "t", "content": content}


BEFORE = PageState(actions=("Submit application",))  # the last page still shows its submit button


class FakeBrowser:
    """Stands in for the applying Chrome, the filling agent and the CDP read. It writes the run log through the
    mask it is given, as apply_bridge.run_application does."""

    def __init__(self, outcomes=None, fail=None, no_tab=(), events=(), move_tab=False, release=None, state=BEFORE):
        self.tabs = [
            {"id": "T0", "type": "page", "url": "https://mail.example.test/inbox"},
            {"id": "T1", "type": "page", "url": "https://jobs.example.test/checking"},
            {"id": "W1", "type": "service_worker", "url": "https://jobs.example.test/sw.js"},
        ]
        self.outcomes = outcomes or {}
        self.fail = fail or {}
        self.no_tab, self.events, self.move_tab, self.release = set(no_tab), list(events), move_tab, release
        self.state = state
        self.calls, self.reads, self.ensured = [], [], 0

    def ensure_chrome(self) -> int:
        self.ensured += 1
        return 9222

    def targets(self) -> list[dict]:
        return [dict(tab) for tab in self.tabs]

    running = True  # whether the applying Chrome already runs when a run starts (open_tabs never starts it)

    def open_tabs(self) -> list[dict] | None:
        return self.targets() if self.running else None

    def fill(self, spec, **options):
        if self.release is not None:
            assert self.release.wait(5)
        self.calls.append((spec, options))
        if self.fail.get(spec.job_id) == "fill":
            raise RuntimeError("claude could not start")
        url = f"https://jobs.example.test/{spec.job_id}/apply"
        lines = [
            stream(tool_use("browser_tabs", action="new")),
            stream(tool_use("browser_navigate", url=url)),
            stream(tool_result(f"### Page state\n- Page URL: {url}/review\n- Page Title: Apply"), kind="user"),
            stream(*self.events) if self.events else None,
            stream({"type": "text", "text": LOG_SECRET_LINE}),
            json.dumps({"type": "result", "result": "RESULT:APPLIED"}),
        ]
        log = options["log_path"]
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("".join(options["mask"](line + "\n") for line in lines if line), encoding="utf-8")
        if spec.job_id not in self.no_tab:
            self.tabs.append({"id": f"N-{spec.job_id}", "type": "page", "url": f"{url}/review"})
        if self.move_tab:
            self.tabs[1]["url"] = "https://jobs.example.test/elsewhere"
        status, reason, notes = self.outcomes.get(
            spec.job_id, ("waiting", None, ["dry run; signed in as sam.synthetic@example.test"])
        )
        return apply_bridge.ApplyOutcome(status, reason, notes, [], [], str(log))

    def read(self, target, out_dir, redactor, sheet=None):
        self.reads.append(target)
        if self.fail.get(target["id"].removeprefix("N-")) == "read":
            raise ReadbackError("job tab not found in the browser")
        out_dir.mkdir(parents=True, exist_ok=True)
        dumped = [field.model_dump(mode="json") for field in FIELDS]
        (out_dir / "readback.json").write_text(json.dumps(redactor.mask_json(dumped)), encoding="utf-8")
        return readback.FinalPage(FIELDS, self.state)


def make_box(tmp_path) -> Toolbox:
    data = tmp_path / "data"
    data.mkdir()
    s = settings()
    box = Toolbox(s, Store(tmp_path / "agent.sqlite", s), data_dir=data)
    (data / "intent.yaml").write_text(
        yaml.safe_dump({"intent": {**BASE_INTENT, "location": {"us": {"level": "require"}}}, "vocabulary": BASE_VOCAB}),
        encoding="utf-8",
    )
    box.initialize_intent(str(data / "intent.yaml"))
    box.letter_renderer = lambda text, pdf_path, name, day: pdf_path.write_bytes(b"%PDF-1.4 letter")
    return box


def write_list(folder, jobs) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "list.yaml"
    path.write_text(yaml.safe_dump({"jobs": jobs}), encoding="utf-8")
    return str(path)


@pytest.fixture
def ff(tmp_path):
    """A toolbox with synthetic applicant files, answers, checklist and a two-job list (one cover letter, one
    without), and a fake browser that runs in the caller's thread."""
    box = make_box(tmp_path)
    write_applicant(box.data_dir, relocation=RELOCATION)
    applicant = box.data_dir / "applicant"
    (applicant / "answers.yaml").write_text(ANSWERS, encoding="utf-8")
    (applicant / "checklist.yaml").write_text(CHECKLIST, encoding="utf-8")
    lists = box.data_dir / "form-fill" / "lists"
    (lists / "letters").mkdir(parents=True)
    (lists / "letters" / "acme-ml-01.txt").write_text("Dear Hiring Team, SYNTHETIC letter.", encoding="utf-8")
    (lists / "letters" / "acme-ml-01.pdf").write_bytes(b"%PDF-1.4 synthetic")
    path = write_list(
        lists,
        [
            {
                "id": "acme-ml-01",
                "url": "https://jobs.example.test/acme-ml-01",
                "company": "Acme Robotics",
                "title": "ML Engineer",
                "resume": "ml",
                "cover_letter": "letters/acme-ml-01.txt",
            },
            {
                "id": "beta-ds-02",
                "url": "https://jobs.example.test/beta-ds-02",
                "company": "Beta Health",
                "title": "Data Scientist",
                "resume": "infra",
                "cover_letter": "none",
            },
        ],
    )
    box.form_fill_runner = FakeBrowser()
    box.form_fill_in_background = False
    return box, path


def run_dir(box, result) -> object:
    return box.data_dir / box.settings.form_fill.dir / "runs" / result["run_id"]


# --- configuration and permissions ------------------------------------------------------------------------------


def test_form_fill_settings():
    form_fill = settings().form_fill
    assert (form_fill.dir, form_fill.answers_file, form_fill.checklist_file) == (
        "form-fill",
        "answers.yaml",
        "checklist.yaml",
    )
    assert form_fill.disallowed_tools == [
        "mcp__playwright__browser_run_code_unsafe",
        "mcp__playwright__browser_close",
        "mcp__playwright__browser_network_request",
        "mcp__playwright__browser_network_requests",
    ]


def test_tiers_approval_and_never_scheduled():
    assert TOOL_TIERS["start_form_fill"] == "commit" and "start_form_fill" in COMMIT_TOOLS
    assert TOOL_TIERS["form_fill_status"] == "prepare"
    assert not {"start_form_fill", "form_fill_status"} & SCHEDULED_TOOLS
    for tool in (Toolbox.start_form_fill, Toolbox.form_fill_status):
        assert "never submits" in tool.__doc__
    permissions = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))["permissions"]
    assert "mcp__intent-agent__start_form_fill" in permissions["ask"]
    assert "Read(./data/local/form-fill/runs/*/*/log.jsonl)" in permissions["deny"]
    codex = tomllib.loads((ROOT / ".codex/config.toml").read_text(encoding="utf-8"))
    tools = codex["mcp_servers"]["intent-agent"]["tools"]
    assert {name: tools[name]["approval_mode"] for name in COMMIT_TOOLS} == dict.fromkeys(COMMIT_TOOLS, "prompt")


def test_outcome_statuses_are_the_report_statuses():
    assert runner.STATUSES == JobReport.model_fields["status"].annotation.__args__


# --- the answer sheet in the filling instructions --------------------------------------------------------------


def sheet(tmp_path, answers=ANSWERS):
    (tmp_path / "profile.json").write_text(json.dumps(PROFILE), encoding="utf-8")
    (tmp_path / "answers.yaml").write_text(answers, encoding="utf-8")
    (tmp_path / "checklist.yaml").write_text(CHECKLIST, encoding="utf-8")
    loaded = load_answers(tmp_path / "answers.yaml", profile_field_paths(tmp_path / "profile.json"), {"relocation"})
    return loaded, load_checklist(tmp_path / "checklist.yaml")


def test_instructions_carry_the_sheet_and_the_checklist_but_no_contact_details(tmp_path):
    answers, checklist = sheet(tmp_path)
    resolved = runner.resolve(answers, PROFILE, {"relocation": RELOCATION})
    text = runner.instructions(resolved, checklist)
    lines = text.splitlines()
    assert text.index("== ANSWER SHEET ==") < text.index("== CHECKLIST ==")
    assert any('"legally authorized to work"' in line and line.endswith("-> Yes") for line in lines)
    assert any(line.startswith("- name.first") and line.endswith("-> Sam") for line in lines)
    assert any("name.legal" in line and line.endswith("-> Sam Example") for line in lines)  # from_profile, resolved
    assert any("location.relocation" in line and line.endswith(f"-> {RELOCATION}") for line in lines)
    hispanic = next(line for line in lines if "hispanic or latino" in line)
    assert "Decline to self-identify" in hispanic and "Prefer not to say" in hispanic and '"No"' in hispanic
    (record,) = [line for line in lines if "Example State University" in line]  # a record is one line
    for part in ("education.ms", "ESU", "Master of Science", "M.S.", "Data Science", "2025-08", "2027-05"):
        assert part in record
    assert "contact." not in text and "mobile number" not in text
    assert not any(secret in text for secret in SECRETS)
    for item in checklist:
        assert f"- {item.id}: {item.rule}" in text
    lowered = text.lower()
    for rule in ("by the sheet", "its own rule", "never guess", "leave", "required", "stop"):
        assert rule in lowered


def test_instructions_need_every_reference_resolved(tmp_path):
    answers, checklist = sheet(tmp_path)
    with pytest.raises(InvariantError, match="location.relocation"):
        runner.instructions(answers, checklist)
    with pytest.raises(InvariantError, match="relocation"):
        runner.resolve(answers, PROFILE, {})


# --- a run end to end with the fake browser ------------------------------------------------------------------


def test_a_run_fills_reads_back_and_writes_the_feasibility_table(ff):
    box, path = ff
    assert box.form_fill_status() == {"status": "idle"}
    result = box.start_form_fill(path)
    assert result["jobs"] == 2 and result["run_id"]
    folder = run_dir(box, result)
    status = box.form_fill_status()
    assert status["status"] == "done" and status["run_id"] == result["run_id"]
    assert status["feasibility"] == str(folder / "feasibility.md")
    assert [job["job_id"] for job in status["jobs"]] == ["acme-ml-01", "beta-ds-02"]
    for job in status["jobs"]:
        assert (job["state"], job["status"], job["fields"]) == ("done", "filled", len(FIELDS))
        assert job["page_dir"] == str(folder / job["job_id"])
        assert job["checks"] == {
            "tab_found": True,
            "redaction_hits": 0,
            "not_submitted": True,
            "other_tabs_unchanged": True,
            # the fake agent never calls checkpoint: the recording holds only the final read
            "browser_errors": 0,
            "checkpoints": 0,
            "checkpoint_tab_matches": False,
            "checkpoint_in_time": False,
            "pages_saved": False,
            "recording_hits": 0,
        }
        assert not any(secret in note for note in job["notes"] for secret in SECRETS)

    fake = box.form_fill_runner
    (first, options), (second, options2) = fake.calls
    assert first.mode == "stop" and first.url == "https://jobs.example.test/acme-ml-01"
    assert first.letter_txt.read_text(encoding="utf-8").startswith("Dear") and first.letter_pdf.is_file()
    assert first.resume_pdf.name == "ml.pdf" and second.resume_pdf.name == "infra.pdf"
    assert not second.letter_txt.exists() and "cover letter" in options2["extra_prompt"].lower()
    assert first.relocation == RELOCATION
    assert options["gmail"] is False
    assert options["extra_disallowed"] == tuple(box.settings.form_fill.disallowed_tools)
    assert options["log_path"] == folder / "acme-ml-01" / "log.jsonl"
    assert "sam.synthetic" not in options["mask"]("sam.synthetic@example.test")
    assert "== ANSWER SHEET ==" in options["extra_prompt"] and "== CHECKLIST ==" in options["extra_prompt"]
    assert not any(secret in options["extra_prompt"] for secret in SECRETS)
    assert [target["id"] for target in fake.reads] == ["N-acme-ml-01", "N-beta-ds-02"]

    meta = json.loads((folder / "acme-ml-01" / "meta.json").read_text(encoding="utf-8"))
    assert {key: meta[key] for key in ("job_id", "company", "title", "url", "status", "fields")} == {
        "job_id": "acme-ml-01",
        "company": "Acme Robotics",
        "title": "ML Engineer",
        "url": "https://jobs.example.test/acme-ml-01",
        "status": "filled",
        "fields": 4,
    }
    assert meta["started"] <= meta["finished"]

    report = (folder / "feasibility.md").read_text(encoding="utf-8")
    assert "| 编号 | 题目 | 读回值 | 来源 |" in report and "一致" in report and "不一致" in report and "漏" in report
    assert "| p1.1 | First Name | Sam | 控件（text） |" in report
    assert "| p1.2 | Email | [email] |" in report
    assert "Are you legally authorized \\| to work?" in report  # a pipe cannot break the table
    assert "| p1.4 | Phone | [phone] | 确认页文字 |" in report
    for check in ("找得到标签页", "打码", "没有提交", "别的标签页没动"):
        assert check in report
    for check in (
        "打码（运行目录）",
        "6.1-6 打码",
        "找得到标签页",
        "6.1-1 不干扰 Playwright MCP",
        "没有提交",
        "别的标签页没动",
    ):
        assert (
            f"| {check}" in report
            and next(line for line in report.splitlines() if line.startswith(f"| {check}")).split("|")[2].strip()
            == "通过"
        ), check
    assert "| 6.1-2 认得出标签页 | 不通过（请你检查） | 填表 agent 一次都没调用 checkpoint |" in report
    assert report.count("## ") >= 3  # both jobs and the whole-run line

    redactor = Redactor.from_profile(PROFILE)
    assert total_hits(redactor.scan([folder])) == 0
    for file in folder.rglob("*"):
        if file.is_file():
            assert not any(secret in file.read_text(encoding="utf-8") for secret in SECRETS), file


@pytest.mark.parametrize(
    ("fake", "expected"),
    [
        (FakeBrowser(outcomes={"acme-ml-01": ("failed", "captcha", ["captcha on sign-in"])}), "gate"),
        (FakeBrowser(outcomes={"acme-ml-01": ("failed", "login_issue", [])}), "gate"),
        (FakeBrowser(no_tab={"acme-ml-01"}), "tab_lost"),
        (FakeBrowser(fail={"acme-ml-01": "read"}), "readback_failed"),
        (FakeBrowser(fail={"acme-ml-01": "fill"}), "system_error"),
        (FakeBrowser(outcomes={"acme-ml-01": ("pending", "system_error", ["exit code 1"])}), "system_error"),
    ],
)
def test_outcome_statuses(ff, fake, expected):
    box, path = ff
    box.form_fill_runner = fake
    box.start_form_fill(path)
    first, second = box.form_fill_status()["jobs"]
    assert first["status"] == expected
    assert second["status"] == "filled"  # one broken job does not stop the run
    if expected in ("tab_lost", "system_error"):
        assert first["page_dir"] is None and first["fields"] == 0
    if expected in ("readback_failed", "system_error") and fake.fail:
        assert any("Error" in note for note in first["notes"])


def test_a_gate_still_reads_the_page_it_stopped_on(ff):
    box, path = ff
    box.form_fill_runner = FakeBrowser(outcomes={"acme-ml-01": ("failed", "captcha", ["captcha on sign-in"])})
    box.start_form_fill(path)
    first = box.form_fill_status()["jobs"][0]
    assert first["status"] == "gate" and first["agent_result"] == "captcha" and first["fields"] == len(FIELDS)
    assert "captcha on sign-in" in first["notes"]


def test_an_agent_that_stopped_early_is_noted(ff):
    box, path = ff
    box.form_fill_runner = FakeBrowser(outcomes={"acme-ml-01": ("failed", "stuck", [])})
    box.start_form_fill(path)
    first = box.form_fill_status()["jobs"][0]
    assert first["agent_result"] == "stuck" and any("stuck" in note for note in first["notes"])


SUBMIT_LIKE = [
    tool_use("browser_type", element="Field of Study input", ref="e1", text="Data", submit=True),
    tool_use("browser_click", element="Review button (not submit)", ref="e9"),
]


def test_submit_like_actions_on_a_page_still_before_submit_are_warnings(ff):
    """Enter in a search box and a click the agent described with "submit" do not fail check 4 while the last page
    still shows its submit button and no received message; they are listed as warnings, in full in meta.json."""
    box, path = ff
    box.form_fill_runner = FakeBrowser(events=SUBMIT_LIKE)
    result = box.start_form_fill(path)
    first = box.form_fill_status()["jobs"][0]
    assert first["checks"]["not_submitted"] is True
    report = (run_dir(box, result) / "feasibility.md").read_text(encoding="utf-8")
    row = next(line for line in report.splitlines() if line.startswith("| 没有提交 | 通过"))
    assert "Submit application" in row and "警告" in row and "2 条" in row and "meta.json" in row
    meta = json.loads((run_dir(box, result) / "acme-ml-01" / "meta.json").read_text(encoding="utf-8"))
    assert meta["page"] == {"actions": ["Submit application"], "received": ""}
    assert len(meta["submit_warnings"]) == 2
    assert "Field of Study input" in meta["submit_warnings"][0]
    assert "Review button (not submit)" in meta["submit_warnings"][1]


def test_a_page_that_says_the_application_was_received_fails_check_4(ff):
    box, path = ff
    box.form_fill_runner = FakeBrowser(state=PageState(received="Thank you for applying!"))
    result = box.start_form_fill(path)
    first = box.form_fill_status()["jobs"][0]
    assert first["checks"]["not_submitted"] is False
    report = (run_dir(box, result) / "feasibility.md").read_text(encoding="utf-8")
    assert "| 没有提交 | 不通过" in report and "Thank you for applying!" in report


@pytest.mark.parametrize(
    "fake",
    [
        FakeBrowser(events=SUBMIT_LIKE, fail={"acme-ml-01": "read"}),  # the last page could not be read
        FakeBrowser(events=SUBMIT_LIKE, state=PageState()),  # no submit or next button left on it
    ],
)
def test_submit_like_actions_without_a_page_before_submit_fail_check_4(ff, fake):
    box, path = ff
    box.form_fill_runner = fake
    result = box.start_form_fill(path)
    first = box.form_fill_status()["jobs"][0]
    assert first["checks"]["not_submitted"] is False
    report = (run_dir(box, result) / "feasibility.md").read_text(encoding="utf-8")
    assert "submit: true" in report and "Review button (not submit)" in report and "不通过" in report


def test_an_agent_that_says_it_applied_fails_check_4(ff):
    box, path = ff
    box.form_fill_runner = FakeBrowser(outcomes={"acme-ml-01": ("applied", None, [])})
    box.start_form_fill(path)
    assert box.form_fill_status()["jobs"][0]["checks"]["not_submitted"] is False


def test_long_notes_point_to_meta_json(ff):
    box, path = ff
    box.form_fill_runner = FakeBrowser(outcomes={"acme-ml-01": ("waiting", None, ["x" * 400])})
    result = box.start_form_fill(path)
    report = (run_dir(box, result) / "feasibility.md").read_text(encoding="utf-8")
    assert "x" * 300 + "…（全文见 meta.json）" in report
    assert "（全文见 readback.json）" not in report  # no field value here is that long


def test_a_moved_original_tab_fails_check_5(ff):
    box, path = ff
    box.form_fill_runner = FakeBrowser(move_tab=True)
    box.start_form_fill(path)
    first = box.form_fill_status()["jobs"][0]
    assert first["checks"]["other_tabs_unchanged"] is False


def test_the_last_page_url_comes_from_the_log(tmp_path):
    log = tmp_path / "log.jsonl"
    log.write_text(
        "\n".join(
            [
                stream(tool_use("browser_navigate", url="https://a.example.test/1")),
                stream(tool_result("- Page URL: https://a.example.test/2\n- Page Title: x"), kind="user"),
                "not json",
                stream(tool_use("browser_click", element="Next", ref="e1")),
                stream(
                    tool_result("### Page state\n- Page URL: https://a.example.test/3", as_blocks=False), kind="user"
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    events, urls = runner.read_log(log)
    assert [event["name"] for event in events] == [
        "mcp__playwright__browser_navigate",
        "mcp__playwright__browser_click",
    ]
    assert urls == ["https://a.example.test/1", "https://a.example.test/2", "https://a.example.test/3"]
    assert runner.read_log(tmp_path / "missing.jsonl") == ([], [])


# --- validation before anything starts ------------------------------------------------------------------------


def test_invalid_inputs_are_refused_together_and_nothing_starts(ff):
    box, path = ff
    answers = box.data_dir / "applicant" / "answers.yaml"
    answers.write_text(ANSWERS.replace("from_profile: personal.phone", "answer: '555-0100'"), encoding="utf-8")
    (box.data_dir / "applicant" / "checklist.yaml").unlink()
    data = yaml.safe_load(open(path, encoding="utf-8"))
    data["jobs"][0]["url"] = "http://jobs.example.test/x"
    write_list(box.data_dir / "form-fill" / "lists", data["jobs"])
    with pytest.raises(InvariantError) as error:
        box.start_form_fill(path)
    message = str(error.value)
    assert "list.yaml" in message and "answers.yaml" in message and "checklist.yaml" in message
    assert "555-0100" not in message  # a contact value written by mistake is never echoed
    assert box.form_fill_runner.ensured == 0 and box.form_fill_runner.calls == []
    assert box.form_fill_status() == {"status": "idle"}
    assert not (box.data_dir / "form-fill" / "runs").exists()


@pytest.mark.parametrize("job_id", ["../escape", "a/b", ".hidden"])
def test_job_ids_must_be_safe_folder_names(ff, job_id):
    box, path = ff
    data = yaml.safe_load(open(path, encoding="utf-8"))
    data["jobs"][0]["id"] = job_id
    write_list(box.data_dir / "form-fill" / "lists", data["jobs"])
    with pytest.raises(InvariantError, match="id"):
        box.start_form_fill(path)
    assert box.form_fill_runner.calls == []


def test_a_missing_list_is_refused(ff):
    box, _ = ff
    with pytest.raises(InvariantError, match="missing"):
        box.start_form_fill(str(box.data_dir / "form-fill" / "lists" / "nope.yaml"))


# --- one Chrome: form filling and applying never run at once ------------------------------------------------


def test_applying_waits_for_a_form_fill_run(ff):
    box, path = ff
    release = threading.Event()
    box.form_fill_runner = FakeBrowser(release=release)
    box.form_fill_in_background = True
    box.start_form_fill(path)
    try:
        assert box.form_fill_status()["status"] == "running"
        with pytest.raises(InvariantError, match="form-fill"):
            box.start_applying("batch-x", "summary")
        with pytest.raises(InvariantError, match="form-fill"):
            box.start_form_fill(path)
    finally:
        release.set()
    wait_until(lambda: box.form_fill_status()["status"] != "running")
    assert box.form_fill_status()["status"] == "done"


def test_form_filling_waits_for_an_apply_step(ff):
    box, path = ff
    jobs = add_jobs(box, ["Acme"])
    write_letters(box, jobs)
    batch = box.prepare_apply_batch(DAY)
    release = threading.Event()

    def blocking(spec):
        assert release.wait(5)
        return apply_bridge.ApplyOutcome("waiting", None, [], [], [], None)

    box.apply_runner = blocking
    box.apply_in_background = True
    box.start_applying(batch["batch_id"], batch["summary"])
    try:
        with pytest.raises(InvariantError, match="apply"):
            box.start_form_fill(path)
    finally:
        release.set()
    wait_until(lambda: box.apply_status()["status"] != "running")
    assert box.form_fill_runner.calls == []
    box.start_form_fill(path)  # free again
    assert box.form_fill_status()["status"] == "done"


class NoScreenshotBrowser(FakeBrowser):
    """Reads the page but, like readback.read_final_page when the blackout fails, leaves the screenshot out."""

    def read(self, target, out_dir, redactor, sheet=None):
        final = super().read(target, out_dir, redactor)
        (out_dir / readback.SCREENSHOT_NOTE).write_text("截图未存：打码失败（子框架里有联系方式）", encoding="utf-8")
        return final


def test_a_screenshot_left_out_is_noted(ff):
    box, path = ff
    box.form_fill_runner = NoScreenshotBrowser()
    result = box.start_form_fill(path)
    first = box.form_fill_status()["jobs"][0]
    assert first["status"] == "filled"
    assert any(note.startswith("截图未存：打码失败") for note in first["notes"])
    assert "截图未存" in (run_dir(box, result) / "feasibility.md").read_text(encoding="utf-8")


def test_the_agent_result_is_masked_too(ff):
    box, path = ff
    box.form_fill_runner = FakeBrowser(outcomes={"acme-ml-01": ("failed", "sent to sam.synthetic@example.test", [])})
    result = box.start_form_fill(path)
    first = box.form_fill_status()["jobs"][0]
    assert "sam.synthetic" not in first["agent_result"] and "[email]" in first["agent_result"]
    meta = (run_dir(box, result) / "acme-ml-01" / "meta.json").read_text(encoding="utf-8")
    assert "sam.synthetic" not in meta


def test_input_errors_are_masked(tmp_path):
    """An answers.yaml error may quote a contact value (decision 21)."""
    from .test_slice6 import write_applicant as write

    data = tmp_path / "data"
    write(data, relocation=RELOCATION)
    (data / "applicant/answers.yaml").write_text(ANSWERS + '  contact.mobile:\n    from_profile: "555-0100"\n', "utf-8")
    (data / "applicant/checklist.yaml").write_text(CHECKLIST, encoding="utf-8")
    listing = tmp_path / "list.yaml"
    listing.write_text(
        "jobs:\n  - {id: a, url: https://example.test/a, company: A, title: T, resume: ml, cover_letter: none}\n"
    )
    from intent_job_agent import applicant as applicant_files

    applicant = applicant_files.load(data, settings().apply.applicant_dir)
    with pytest.raises(InvariantError) as error:
        runner.load_inputs(listing, applicant, settings())
    assert "555-0100" not in str(error.value) and "[phone]" in str(error.value)


# --- decision 23 ------------------------------------------------------------------------------------------------


def test_run_rules_stop_on_a_stated_eligibility_bar_without_an_untrue_answer():
    assert "RESULT:FAILED:not_eligible" in runner.RUN_RULES and "untrue" in runner.RUN_RULES


def test_not_eligible_is_the_agents_result(ff):
    box, path = ff
    note = 'Education says "not open to current students"; the true answer is auto-rejected.'
    box.form_fill_runner = FakeBrowser(outcomes={"acme-ml-01": ("failed", "not_eligible", [note])})
    box.start_form_fill(path)
    first = box.form_fill_status()["jobs"][0]
    assert first["agent_result"] == "not_eligible" and note in first["notes"]


def test_notes_keep_up_to_2000_characters(ff):
    box, path = ff
    box.form_fill_runner = FakeBrowser(outcomes={"acme-ml-01": ("waiting", None, ["y" * 1500 + " end"])})
    box.start_form_fill(path)
    assert "y" * 1500 + " end" in box.form_fill_status()["jobs"][0]["notes"]


def test_personal_tabs_are_listed_at_the_start_and_the_run_still_goes(ff):
    box, path = ff
    fake = FakeBrowser()
    fake.tabs += [
        {"id": "G1", "type": "page", "url": "https://mail.google.com/mail/u/1/#inbox"},
        {"id": "S1", "type": "page", "url": "https://records.synthetic.edu/student/details?id=1"},
        {"id": "G2", "type": "page", "url": "https://mail.google.com/mail/u/0/#inbox"},
    ]
    box.form_fill_runner = fake
    result = box.start_form_fill(path)
    assert result["personal_tabs"] == ["https://mail.google.com", "https://records.synthetic.edu"]
    assert "关掉" in result["warning"] and len(fake.calls) == 2
    report = (run_dir(box, result) / "feasibility.md").read_text(encoding="utf-8")
    assert "https://mail.google.com" in report and "student/details" not in report


def test_no_personal_tabs_no_warning(ff):
    box, path = ff
    result = box.start_form_fill(path)  # the fake's own tabs: a made-up mail site and a job page
    assert result["personal_tabs"] == [] and "warning" not in result


def test_personal_tabs_rule():
    tabs = [
        {"type": "page", "url": "https://www.chase.com/personal"},
        {"type": "page", "url": "https://secure05a.chase.com/web/auth"},  # a subdomain of a listed site
        {"type": "page", "url": "https://jobs.example.test/apply"},
        {"type": "service_worker", "url": "https://mail.google.com/sw.js"},
        {"type": "page", "url": "chrome://newtab/"},
        {"type": "page", "url": "https://my.synthetic.edu.cn/student"},  # an "edu" label, not only a .edu ending
        {"type": "page", "url": "https://www.education.example.test/"},  # "education" is not an edu label
        {"type": "page", "url": "https://careers.synthetic.edu/jobs/42/apply"},  # a job on the list
    ]
    found = runner.personal_tabs(
        tabs, ["https://www.chase.com", "https://mail.google.com"], skip=["https://careers.synthetic.edu/jobs/42"]
    )
    assert found == ["https://www.chase.com", "https://secure05a.chase.com", "https://my.synthetic.edu.cn"]


def test_a_chrome_not_running_yet_is_not_started_for_the_tab_check(ff):
    box, path = ff
    fake = FakeBrowser()
    fake.running = False
    box.form_fill_runner = fake
    result = box.start_form_fill(path)
    assert result["personal_tabs"] == [] and "没能检查" in result["warning"]
    assert box.form_fill_status()["personal_tabs_checked"] is False and len(fake.calls) == 2
    assert "没能检查" in (run_dir(box, result) / "feasibility.md").read_text(encoding="utf-8")


# --- slice 2a: the checkpoint service, recordings and reports at the end of a run ------------------------------


def recordings(box):
    return box.data_dir / box.settings.form_fill.dir / box.settings.form_fill.recordings_dir


def test_slice_2a_settings():
    form_fill = settings().form_fill
    assert form_fill.recordings_dir == "recordings"
    assert (form_fill.checkpoint_timeout_seconds, form_fill.watch_interval_seconds, form_fill.stuck_after) == (5, 1, 3)
    assert form_fill.network_max_mb == 30


def test_the_filling_agent_gets_the_checkpoint_service(ff):
    box, path = ff
    result = box.start_form_fill(path)
    (spec, options), _ = box.form_fill_runner.calls
    servers = options["extra_servers"]
    assert list(servers) == ["checkpoint"]
    config_path = servers["checkpoint"]["args"][-1]
    config = json.loads(open(config_path, encoding="utf-8").read())
    assert config["before"] == ["T0", "T1", "W1"]  # every tab and worker open before the run
    assert config["rec_id"] == recording_id(spec.url) and config["run_id"] == result["run_id"]
    assert config["job_id"] == "acme-ml-01" and config["url"] == spec.url
    assert config["timeout_seconds"] == 5 and config["interval_seconds"] == 1 and config["stuck_after"] == 3
    assert config["recordings_dir"] == str(recordings(box))
    assert config["ignore_filled_on_page"] is None
    assert not any(secret in json.dumps(config) for secret in SECRETS)
    assert options["extra_allowed"] == ("mcp__checkpoint__checkpoint",)
    assert "== CHECKPOINTS ==" in options["extra_prompt"]
    assert 'checkpoint with stage "blank"' in options["extra_prompt"]
    assert "RESULT:FAILED:stuck" in options["extra_prompt"]


def test_a_run_writes_the_reports_and_the_recordings_last_conclusion(ff):
    box, path = ff
    result = box.start_form_fill(path)
    folder = run_dir(box, result)
    assert (folder / "summary.md").is_file()
    report = json.loads((folder / "acme-ml-01.json").read_text(encoding="utf-8"))
    rec = recording_id("https://jobs.example.test/acme-ml-01")
    assert report["recording_id"] == rec
    assert [(page["page"], page["read_from"]) for page in report["pages"]] == [(1, "final")]
    assert report["conclusion"] == "check_then_submit"
    meta = json.loads((recordings(box) / rec / "meta.json").read_text(encoding="utf-8"))
    assert meta["last"] == {"run": result["run_id"], "conclusion": "check_then_submit", "reason": None}
    assert (recordings(box) / rec / "fills" / result["run_id"] / "final.json").is_file()
    status = box.form_fill_status()
    assert status["summary"] == str(folder / "summary.md")
    assert status["jobs"][0]["recording_id"] == rec
    redactor = Redactor.from_profile(PROFILE)
    assert total_hits(redactor.scan([recordings(box)])) == 0


def test_a_job_recorded_before_is_not_visited_again(ff):
    box, path = ff
    url = "https://jobs.example.test/acme-ml-01"
    store = RecordingStore(recordings(box), recording_id(url), "20261019-000000-aaaa", Redactor.from_profile(PROFILE))
    store.write_meta(url, "Acme Robotics", "ML Engineer")
    recording.note_conclusion(store.rec_dir, "20261019-000000-aaaa", "check_then_submit", None)
    result = box.start_form_fill(path)
    assert [spec.job_id for spec, _ in box.form_fill_runner.calls] == ["beta-ds-02"]
    assert result["skipped"] == [
        {
            "job_id": "acme-ml-01",
            "recording_id": recording_id(url),
            "last_run": "20261019-000000-aaaa",
            "last_conclusion": "check_then_submit",
        }
    ]
    first = box.form_fill_status()["jobs"][0]
    assert first["state"] == "skipped" and first["status"] is None
    summary = (run_dir(box, result) / "summary.md").read_text(encoding="utf-8")
    assert "已录制，请用重放重测" in summary and "acme-ml-01" in summary


def test_revisit_or_a_gate_last_time_visits_a_recorded_job_again(ff):
    box, path = ff
    jobs = yaml.safe_load(open(path, encoding="utf-8"))["jobs"]
    jobs[0]["revisit"] = True
    write_list(box.data_dir / "form-fill" / "lists", jobs)
    for job, conclusion, reason in (
        (jobs[0], "check_then_submit", None),
        (jobs[1], "not_filled", "gate"),
    ):
        store = RecordingStore(recordings(box), recording_id(job["url"]), "old", Redactor.from_profile(PROFILE))
        store.write_meta(job["url"], job["company"], job["title"])
        recording.note_conclusion(store.rec_dir, "old", conclusion, reason)
    result = box.start_form_fill(path)
    assert [spec.job_id for spec, _ in box.form_fill_runner.calls] == ["acme-ml-01", "beta-ds-02"]
    assert result["skipped"] == []


def test_ignore_filled_on_page_goes_to_the_checkpoint_service(ff):
    box, path = ff
    jobs = yaml.safe_load(open(path, encoding="utf-8"))["jobs"]
    jobs[0]["ignore_filled_on_page"] = 2
    write_list(box.data_dir / "form-fill" / "lists", jobs)
    box.start_form_fill(path)
    (_, options), _ = box.form_fill_runner.calls
    config = json.loads(open(options["extra_servers"]["checkpoint"]["args"][-1], encoding="utf-8").read())
    assert config["ignore_filled_on_page"] == 2


def test_the_final_read_goes_into_the_recording_after_the_checkpoints(ff):
    """The checkpoint service wrote page 1 while the agent ran; the final read lands on page 2 and numbers on."""
    box, path = ff

    class RecordingFake(FakeBrowser):
        def fill(self, spec, **options):
            outcome = super().fill(spec, **options)
            config = json.loads(open(options["extra_servers"]["checkpoint"]["args"][-1], encoding="utf-8").read())
            from intent_job_agent.form_fill.recorder import Recorder, Snapshot

            store = RecordingStore(recordings(box), config["rec_id"], config["run_id"], Redactor.from_profile(PROFILE))
            rec = Recorder()
            info = Snapshot(
                tab=f"N-{spec.job_id}",
                url=spec.url,
                marker=("My Information",),
                fields=(ReadField(id="p1.0", question="Middle Name", origin="control", control="text", value=""),),
                read_at="2026-10-20T10:00:00",
            )
            store.save(rec.observe(info, "blank"), "blank", None)
            store.save(rec.observe(info, "filled"), "filled", None)
            store.save_state(rec)
            return outcome

    box.form_fill_runner = RecordingFake()
    result = box.start_form_fill(path)
    report = json.loads((run_dir(box, result) / "acme-ml-01.json").read_text(encoding="utf-8"))
    assert [(page["page"], page["read_from"]) for page in report["pages"]] == [(1, "filled"), (2, "final")]
    assert {row["field_id"] for row in report["fields"]} >= {"p1.1", "p2.1", "p2.4"}


# --- the merge review of 2026-10-20 (decision 32) ----------------------------------------------------------------


def test_two_entries_of_one_posting_are_refused(ff):
    """Review #32: one posting, one recording, one real visit (spec 6.5)."""
    box, path = ff
    jobs = yaml.safe_load(open(path, encoding="utf-8"))["jobs"]
    jobs[1]["url"] = jobs[0]["url"] + "?utm_source=linkedin"
    write_list(box.data_dir / "form-fill" / "lists", jobs)
    with pytest.raises(InvariantError, match="same posting"):
        box.start_form_fill(path)
    assert box.form_fill_runner.calls == []


def test_the_service_gets_the_sheet_phrases_but_no_answer(ff):
    box, path = ff
    box.start_form_fill(path)
    (_, options), _ = box.form_fill_runner.calls
    config = json.loads(open(options["extra_servers"]["checkpoint"]["args"][-1], encoding="utf-8").read())
    assert config["sheet"]["contact.email"]["from_profile"] == "personal.email"
    assert "work_auth.authorized_us" in config["sheet"] and "answer" not in config["sheet"]["work_auth.authorized_us"]
    assert "Yes" not in json.dumps(config["sheet"])


def test_a_received_page_seen_while_recording_fails_the_submit_check(ff):
    """Review #10: the final page may have moved on; a page recorded on the way said it was received."""
    box, path = ff

    class SentFake(FakeBrowser):
        def fill(self, spec, **options):
            outcome = super().fill(spec, **options)
            config = json.loads(open(options["extra_servers"]["checkpoint"]["args"][-1], encoding="utf-8").read())
            store = RecordingStore(recordings(box), config["rec_id"], config["run_id"], Redactor.from_profile(PROFILE))
            store.note_received("Your application was sent", spec.url)
            return outcome

    box.form_fill_runner = SentFake()
    result = box.start_form_fill(path)
    report = json.loads((run_dir(box, result) / "acme-ml-01.json").read_text(encoding="utf-8"))
    assert report["conclusion"] == "maybe_submitted"
    assert box.form_fill_status()["jobs"][0]["checks"]["not_submitted"] is False


def test_the_feasibility_table_names_a_missing_mhtml_and_network_record(ff):
    """Review #31: 6.1-5 checks the MHTML (or why it is not there) and a single-page form's network.json."""
    box, path = ff
    result = box.start_form_fill(path)
    report = (run_dir(box, result) / "feasibility.md").read_text(encoding="utf-8")
    row = next(line for line in report.splitlines() if line.startswith("| 6.1-5 存得下"))
    assert "不通过" in row and "network.json" in row


def test_the_final_read_is_masked_by_column(ff):
    """Review #17: a contact field's text that profile.json does not hold never reaches final.json."""
    box, path = ff

    class OddPhone(FakeBrowser):
        def read(self, target, out_dir, redactor, sheet=None):
            final = super().read(target, out_dir, redactor, sheet)
            odd = ReadField(
                id="p1.5", question="Mobile number", origin="control", control="text", value="call me later"
            )
            return readback.FinalPage((*final.fields, odd), final.state)

    box.form_fill_runner = OddPhone()
    result = box.start_form_fill(path)
    final = recordings(box) / recording_id("https://jobs.example.test/acme-ml-01") / "fills" / result["run_id"]
    text = (final / "final.json").read_text(encoding="utf-8")
    assert "call me later" not in text and "[其他值，已隐去]" in text
