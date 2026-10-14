"""Slice 6: applying (spec 4.10) - ApplyPilot's apply prompt and Chrome handling behind an adapter, cover-letter
checks, batches that review the first jobs before anything is submitted, and the risk controls.
All data is SYNTHETIC; nothing here starts Chrome or Claude Code."""

import json
import re
from pathlib import Path

import pytest
import yaml

from intent_job_agent import apply_bridge
from intent_job_agent.cover_letter_check import check_letter
from intent_job_agent.domain import InvariantError, Job
from intent_job_agent.store import Store
from intent_job_agent.tools import COMMIT_TOOLS, SCHEDULED_TOOLS, TOOL_TIERS, Toolbox

from .conftest import BASE_INTENT, BASE_VOCAB, ROOT, settings
from .test_slice2f import frontmatter

DAY = "2026-10-14"
US = ["location.us.austin"]
PROFILE = {  # ApplyPilot's profile format, synthetic values
    "personal": {
        "full_name": "Sam Example",
        "preferred_name": "Sam",
        "email": "sam.synthetic@example.test",
        "password": "synthetic-password-1",
        "phone": "555-0100",
        "address": "1 Synthetic Way",
        "city": "Austin",
        "province_state": "TX",
        "country": "United States",
        "postal_code": "00000",
    },
    "work_authorization": {"legally_authorized_to_work": "Yes", "require_sponsorship": "No"},
    "compensation": {"salary_expectation": "150000", "salary_currency": "USD"},
    "experience": {"years_of_experience_total": "2", "target_role": "ML engineer"},
    "skills_boundary": {"languages": ["Python"]},
    "resume_facts": {},
    "eeo_voluntary": {},
}
SECRETS = ("sam.synthetic@example.test", "synthetic-password-1", "555-0100", "1 Synthetic Way")
RESUME = "Sam Example. Built ranking models for 12 products; AUC rose from 0.650 to 0.671. Graduating March 2027."


def letter(extra: str = "", words: int = 300) -> str:
    """A letter that passes every check: starts with Dear, plain words, numbers only from the resume."""
    body = " ".join(["I built ranking models and shipped them with the product team."] * (words // 10))
    return f"Dear Hiring Team,\n\n{body} {extra}\n\nAUC rose from 0.650 to 0.671 across 12 products.\n\nSam Example"


def write_applicant(data, careful=("Stripe",), skill_dir=None, read_dirs=()):
    folder = data / "applicant"
    (folder / "resumes").mkdir(parents=True)
    (folder / "profile.json").write_text(json.dumps(PROFILE), encoding="utf-8")
    for name in ("ml", "infra"):
        (folder / "resumes" / f"{name}.pdf").write_bytes(b"%PDF-1.4 synthetic")
        (folder / "resumes" / f"{name}.md").write_text(RESUME, encoding="utf-8")
    config = {
        "profile": "profile.json",
        "resumes": [
            {"id": "ml", "pdf": "resumes/ml.pdf", "text": "resumes/ml.md", "use_for": "ML roles"},
            {"id": "infra", "pdf": "resumes/infra.pdf", "text": "resumes/infra.md", "use_for": "Infra roles"},
        ],
        "careful_companies": list(careful),
    }
    if read_dirs:
        config["apply_agent_read"] = [str(d) for d in read_dirs]
    if skill_dir:
        config["cover_letter_skill"] = str(skill_dir)
        config["cover_letter_context"] = [str(skill_dir.parent / "facts.md")]
    (folder / "applicant.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")


class FakeRunner:
    """Stands in for Chrome + Claude Code: records what it was asked to do and returns canned outcomes."""

    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = outcomes or {}

    def __call__(self, spec):
        self.calls.append(spec)
        status, reason, notes = self.outcomes.get(
            spec.job_id, ("waiting" if spec.mode == "stop" else "applied", None, [])
        )
        return apply_bridge.ApplyOutcome(status=status, reason=reason, notes=notes, other_sites=[], emails=[], log=None)


@pytest.fixture
def box(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    s = settings()
    box = Toolbox(s, Store(tmp_path / "agent.sqlite", s), data_dir=data)
    spec = {**BASE_INTENT, "location": {"us": {"level": "require"}}}
    (data / "intent.yaml").write_text(
        yaml.safe_dump({"intent": spec, "vocabulary": BASE_VOCAB}, allow_unicode=True), encoding="utf-8"
    )
    box.initialize_intent(str(data / "intent.yaml"))
    box.apply_runner = FakeRunner()
    box.letter_renderer = lambda text, pdf_path, name, day: pdf_path.write_bytes(b"%PDF-1.4 letter")
    box.apply_in_background = False
    return box


def add_jobs(box, companies):
    for n, company in enumerate(companies):
        job_id = f"j{n:02d}"
        box.store.add_job(
            Job(
                id=job_id,
                title=f"ML Engineer {n}",
                company=company,
                url=f"https://jobs.example.test/{job_id}",
                description="SYNTHETIC posting. Our team serves 40 markets.",
                tags=[],
            )
        )
        box.store.set_job_tags(job_id, ["role.ml_engineering", *US])
    box.build_apply_list(DAY)
    return [f"j{n:02d}" for n in range(len(companies))]


def write_letters(box, job_ids, resume="ml"):
    for job_id in job_ids:
        assert box.save_cover_letter(job_id, resume, letter())["ok"]


COMPANIES = [f"Co{n}" for n in range(10)] + ["Stripe, Inc.", "Acme", "Beta"]


# --- vendored code -------------------------------------------------------------------------------------


def test_vendored_apply_modules_only_change_imports():
    folder = ROOT / "src/intent_job_agent/vendor/applypilot/apply"
    for name in ("__init__.py", "prompt.py", "chrome.py"):
        text = (folder / name).read_text(encoding="utf-8")
        assert text.startswith("# Vendored from ApplyPilot")
        assert not re.search(r"^\s*(from|import) applypilot", text, re.M)
    from intent_job_agent.vendor.applypilot.apply import chrome, prompt  # noqa: F401


def test_the_vendored_prompt_gets_our_rules(box):
    write_applicant(box.data_dir)
    jobs = add_jobs(box, ["Acme"])
    write_letters(box, jobs)
    spec = box._apply_spec(jobs[0], "stop")
    profile = box._applicant().profile
    text = apply_bridge.build_prompt(spec, box.data_dir, box.settings, profile)
    assert "Sam Example" in text and "Dear Hiring Team" in text  # ApplyPilot's prompt, from the profile
    assert "Do NOT click the final Submit" in text  # stop mode uses ApplyPilot's dry run
    assert "arbitration" in text.lower() and "ssn" in text.lower() and "application fee" in text.lower()
    assert "new tab" in text.lower() and "leave this tab open" in text.lower()
    assert "do not send any email" in text.lower()  # an email application is a submission
    assert "RESULT:FAILED:already_applied" in text  # never submit a second application
    submit = apply_bridge.build_prompt(box._apply_spec(jobs[0], "submit"), box.data_dir, box.settings, profile)
    assert "Do NOT click the final Submit" not in submit and "close this tab" in submit.lower()
    assert "do not send any email" not in submit.lower()


# --- applicant setup -----------------------------------------------------------------------------------


def test_setup_reports_what_is_missing(box):
    setup = box.get_apply_setup()
    assert setup["ready"] is False and any("applicant.yaml" in item for item in setup["missing"])
    with pytest.raises(InvariantError):
        box.prepare_apply_batch(DAY)


def test_setup_and_materials_never_return_contact_details(box, tmp_path):
    write_applicant(box.data_dir)
    setup = box.get_apply_setup()
    assert setup["ready"] is True and [r["id"] for r in setup["resumes"]] == ["ml", "infra"]
    materials = box.get_cover_letter_materials()
    assert "cover-letter" in materials["skill"]["source"]  # the project's general skill by default
    assert materials["resumes"][0] == {"id": "ml", "use_for": "ML roles"}
    assert box.get_resume("ml")["text"] == RESUME
    for payload in (setup, materials):
        dumped = json.dumps(payload, ensure_ascii=False)
        assert not any(secret in dumped for secret in SECRETS)


def test_a_personal_cover_letter_skill_can_be_plugged_in(box, tmp_path):
    kb = tmp_path / "kb"
    skill = kb / "skills" / "cover-letter"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: cover-letter\n---\nMy own rules.", encoding="utf-8")
    (skill / "references" / "voice.md").write_text("Plain voice.", encoding="utf-8")
    (kb / "skills" / "facts.md").write_text("Team results stay team results.", encoding="utf-8")
    write_applicant(box.data_dir, skill_dir=skill)
    materials = box.get_cover_letter_materials()
    assert materials["skill"]["source"] == str(skill)
    files = {item["path"]: item["text"] for item in materials["skill"]["files"]}
    assert files["SKILL.md"].endswith("My own rules.") and files["references/voice.md"] == "Plain voice."
    assert materials["context"] == [
        {"path": str(kb / "skills" / "facts.md"), "text": "Team results stay team results."}
    ]


# --- cover letters -------------------------------------------------------------------------------------


def test_cover_letter_rules():
    job = "We serve 40 markets."
    s = settings().apply.cover_letter
    check = lambda text: check_letter(text, RESUME, job, s.min_words, s.max_words)  # noqa: E731
    assert check(letter()) == []
    assert check(letter("We serve 40 markets.")) == []  # numbers from the posting are fine
    assert any("5" in p for p in check(letter("I have 5 years of experience.")))
    assert any("em dash" in p for p in check(letter("Fast — and careful.")))
    assert check(letter("From May–June I shipped it.")) == []  # en dash ranges are allowed
    assert any("passionate" in p for p in check(letter("I am passionate about ranking.")))
    assert check("Here is the cover letter:\n" + letter())  # anything before the greeting is model preamble
    assert any("Dear" in p for p in check(letter().replace("Dear Hiring Team,", "Hello,")))
    assert any("words" in p for p in check(letter(words=60)))
    assert any("words" in p for p in check(letter(words=600)))


def test_saving_a_letter_checks_it_first(box):
    write_applicant(box.data_dir)
    (job_id,) = add_jobs(box, ["Acme"])
    bad = box.save_cover_letter(job_id, "ml", letter("I am passionate about it."))
    assert bad["ok"] is False and bad["problems"]
    folder = box.data_dir / box.settings.apply.letters_dir
    assert not folder.exists() or not list(folder.iterdir())
    good = box.save_cover_letter(job_id, "ml", letter())
    assert good["ok"] is True
    assert (folder / f"{job_id}.txt").read_text(encoding="utf-8").startswith("Dear")
    assert (folder / f"{job_id}.pdf").exists()
    with pytest.raises(InvariantError):
        box.save_cover_letter(job_id, "nope", letter())
    assert box.check_cover_letter(job_id, "ml", letter("I am passionate."))["problems"]


# --- batches, approval and the run ---------------------------------------------------------------------


def test_batch_reviews_the_first_ten_and_stops_for_careful_companies(box):
    write_applicant(box.data_dir)
    jobs = add_jobs(box, COMPANIES)
    batch = box.prepare_apply_batch(DAY)
    modes = {item["job_id"]: item["mode"] for item in batch["jobs"]}
    assert [modes[j] for j in jobs[:10]] == ["review"] * 10
    assert modes["j10"] == "careful" and modes["j11"] == modes["j12"] == "submit"
    assert all(item["letter"] == "missing" for item in batch["jobs"])
    assert "10" in batch["summary"] and "Co0" in batch["summary"]
    smaller = box.prepare_apply_batch(DAY, exclude=["j00"])  # e.g. a letter that never passed
    assert "j00" not in {item["job_id"] for item in smaller["jobs"]}


def test_starting_needs_the_shown_summary_and_every_letter(box):
    write_applicant(box.data_dir)
    jobs = add_jobs(box, COMPANIES)
    batch = box.prepare_apply_batch(DAY)
    with pytest.raises(InvariantError):  # letters missing
        box.start_applying(batch["batch_id"], batch["summary"])
    write_letters(box, jobs)
    with pytest.raises(InvariantError):
        box.start_applying(batch["batch_id"], "something else")
    box.start_applying(batch["batch_id"], batch["summary"])


def test_review_phase_then_the_rest(box):
    write_applicant(box.data_dir)
    jobs = add_jobs(box, COMPANIES)
    write_letters(box, jobs)
    note = ["Mandatory arbitration: disputes go to binding arbitration."]
    box.apply_runner = FakeRunner({"j03": ("failed", "arbitration", note), "j12": ("failed", "captcha", [])})
    batch = box.prepare_apply_batch(DAY)
    box.start_applying(batch["batch_id"], batch["summary"])
    assert [spec.job_id for spec in box.apply_runner.calls] == jobs[:10]
    assert {spec.mode for spec in box.apply_runner.calls} == {"stop"}  # nothing is submitted while reviewing

    status = box.apply_status()
    assert status["phase"] == "review" and status["status"] == "done"
    by_id = {item["job_id"]: item for item in status["jobs"]}
    assert by_id["j00"]["result"] == "waiting" and by_id["j03"]["result"] == "failed"
    assert by_id["j03"]["reason"] == "arbitration" and by_id["j03"]["notes"] == note
    applications = box.store.applications()
    assert applications["j00"]["status"] == "waiting" and applications["j03"]["status"] == "failed"

    rest_summary = status["next"]["summary"]
    assert "Stripe" in rest_summary
    with pytest.raises(InvariantError):  # the first summary cannot start the second phase
        box.start_applying(batch["batch_id"], batch["summary"])
    box.start_applying(batch["batch_id"], rest_summary)
    rest = box.apply_runner.calls[10:]
    assert {spec.job_id: spec.mode for spec in rest} == {"j10": "stop", "j11": "submit", "j12": "submit"}
    applications = box.store.applications()
    assert applications["j11"]["status"] == "applied"
    assert applications["j10"]["status"] == "waiting" and applications["j12"]["status"] == "failed"
    assert box.apply_status()["next"] is None
    with pytest.raises(InvariantError):  # the batch is finished
        box.start_applying(batch["batch_id"], rest_summary)

    box.mark_applications(["j00"], "applied")  # the user submitted a reviewed form
    assert box.store.applications()["j00"]["status"] == "applied"


def test_each_result_is_recorded_as_soon_as_it_is_known(box):
    write_applicant(box.data_dir)
    jobs = add_jobs(box, COMPANIES[:2])
    write_letters(box, jobs)
    seen = []

    def runner(spec):  # by the second job, the first one's result is already in the database
        seen.append(Store(box.store.path, box.settings).applications()["j00"]["status"])
        return apply_bridge.ApplyOutcome(status="waiting", reason=None)

    box.apply_runner = runner
    batch = box.prepare_apply_batch(DAY)
    box.start_applying(batch["batch_id"], batch["summary"])
    assert seen == ["pending", "waiting"]


def test_only_pending_jobs_are_applied_to(box):
    write_applicant(box.data_dir)
    jobs = add_jobs(box, COMPANIES[:3])
    write_letters(box, jobs)
    batch = box.prepare_apply_batch(DAY)
    box.mark_applications(["j01"], "skipped")  # changed their mind after seeing the batch
    with pytest.raises(InvariantError):
        box.start_applying(batch["batch_id"], batch["summary"])


def test_checklist_lists_other_sites_and_emails(box):
    write_applicant(box.data_dir)
    jobs = add_jobs(box, ["Acme"])
    write_letters(box, jobs)

    def runner(spec):
        return apply_bridge.ApplyOutcome(
            status="waiting",
            reason=None,
            notes=[],
            other_sites=["mail.example.test"],
            emails=[{"to": "jobs@acme.test", "subject": "Application"}],
            log=None,
        )

    box.apply_runner = runner
    batch = box.prepare_apply_batch(DAY)
    box.start_applying(batch["batch_id"], batch["summary"])
    checklist = box.apply_status()["checklist"]
    assert checklist == [
        {
            "job_id": "j00",
            "other_sites": ["mail.example.test"],
            "emails": [{"to": "jobs@acme.test", "subject": "Application"}],
        }
    ]


def test_contact_details_never_reach_the_database(box, tmp_path):
    write_applicant(box.data_dir)
    jobs = add_jobs(box, COMPANIES[:2])
    write_letters(box, jobs)
    batch = box.prepare_apply_batch(DAY)
    box.start_applying(batch["batch_id"], batch["summary"])
    box.apply_status()
    raw = (tmp_path / "agent.sqlite").read_bytes()
    assert not any(secret.encode() in raw for secret in SECRETS)


def test_tool_tiers_and_approval():
    assert TOOL_TIERS["start_applying"] == "commit" and "start_applying" in COMMIT_TOOLS
    for name in ("get_apply_setup", "get_cover_letter_materials", "get_resume", "check_cover_letter"):
        assert TOOL_TIERS[name] == "read"
    for name in ("prepare_apply_batch", "save_cover_letter", "apply_status"):
        assert TOOL_TIERS[name] == "prepare"
    applying = {"prepare_apply_batch", "save_cover_letter", "start_applying", "apply_status"}
    assert not applying & SCHEDULED_TOOLS
    ask = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))["permissions"]["ask"]
    assert "mcp__intent-agent__start_applying" in ask


# --- the adapter's risk controls (spec 4.10, plan slice 6) ------------------------------------------------


def test_mcp_servers_are_pinned_and_risky_sites_blocked():
    s = settings()
    config = apply_bridge.mcp_config(s, 9222)["mcpServers"]
    args = config["playwright"]["args"]
    assert s.apply.mcp.playwright in args and "--cdp-endpoint=http://localhost:9222" in args
    blocked = next(a for a in args if a.startswith("--blocked-origins="))
    assert "https://mail.google.com" in blocked and "https://www.paypal.com" in blocked
    assert "@latest" not in json.dumps(config)
    assert s.apply.mcp.gmail in config["gmail"]["args"]


def test_the_apply_agent_gets_only_the_browser_and_gmail(tmp_path):
    s = settings()
    command = apply_bridge.claude_command(s, tmp_path / "mcp.json")
    assert command[command.index("--tools") + 1] == ""  # no built-in tools: no shell, no files, no web search
    assert "--strict-mcp-config" in command  # none of the user's own MCP servers
    # Permission checks stay on: an allowlist instead of ApplyPilot's bypassPermissions.
    assert "bypassPermissions" not in command and command[command.index("--permission-mode") + 1] == "default"
    allowed = command[command.index("--allowedTools") + 1].split(",")
    assert allowed == [
        "mcp__playwright",
        "mcp__gmail__search_emails",
        "mcp__gmail__read_email",
        "mcp__gmail__send_email",
    ]
    disallowed = command[command.index("--disallowedTools") + 1]
    assert "mcp__gmail__delete_email" in disallowed and "mcp__gmail__send_email" not in disallowed


def test_the_copied_chrome_profile_loses_passwords_cards_and_autofill(tmp_path):
    for profile in ("Default", "Profile 1"):
        folder = tmp_path / profile
        folder.mkdir()
        for name in ("Login Data", "Login Data-journal", "Web Data", "Account Web Data", "Cookies", "Bookmarks"):
            (folder / name).write_text("x")
    removed = apply_bridge.strip_profile(tmp_path, settings().apply.chrome_profile_strip)
    assert len(removed) == 8
    for profile in ("Default", "Profile 1"):
        assert sorted(p.name for p in (tmp_path / profile).iterdir()) == ["Bookmarks", "Cookies"]


@pytest.mark.parametrize(
    ("output", "mode", "expected"),
    [
        ("done\nRESULT:APPLIED", "submit", ("applied", None)),
        ("filled, dry run\nRESULT:APPLIED", "stop", ("waiting", None)),
        ("RESULT:EXPIRED", "submit", ("failed", "expired")),
        ("RESULT:CAPTCHA", "stop", ("failed", "captcha")),
        ("NOTE: clause\nRESULT:FAILED:arbitration", "submit", ("failed", "arbitration")),
        ("RESULT:FAILED:ssn_required**", "submit", ("failed", "ssn_required")),
        ("NOTE: shows applied on 2026-10-01\nRESULT:FAILED:already_applied", "submit", ("applied", "already_applied")),
        ("RESULT:FAILED:already_applied", "stop", ("applied", "already_applied")),
        ("no result here", "submit", ("failed", "no_result_line")),
    ],
)
def test_results_map_to_list_statuses(output, mode, expected):
    assert apply_bridge.outcome_status(mode, apply_bridge.parse_result(output)) == expected


def test_notes_and_audit_from_the_stream():
    events = [
        {"name": "mcp__playwright__browser_navigate", "input": {"url": "https://jobs.acme.test/apply/1"}},
        {"name": "mcp__playwright__browser_navigate", "input": {"url": "https://boards.greenhouse.io/acme/2"}},
        {"name": "mcp__playwright__browser_navigate", "input": {"url": "https://evil.example.test/x"}},
        {"name": "mcp__gmail__send_email", "input": {"to": ["jobs@acme.test"], "subject": "Application", "body": "b"}},
    ]
    audit = apply_bridge.audit(events, "https://jobs.acme.test/apply/1")
    assert audit["other_sites"] == ["boards.greenhouse.io", "evil.example.test"]
    assert audit["emails"] == [{"to": "jobs@acme.test", "subject": "Application"}]
    assert apply_bridge.notes("x\nNOTE: arbitration clause text\nRESULT:FAILED:arbitration") == [
        "arbitration clause text"
    ]


FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
prompt = sys.stdin.read()
assert "ADDITIONAL RULES" in prompt
if os.environ.get("FAKE_ARGS"):
    open(os.environ["FAKE_ARGS"], "w").write(json.dumps({"argv": sys.argv[1:], "prompt": prompt}))
def say(message):
    print(json.dumps(message), flush=True)
say({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "mcp__playwright__browser_navigate", "input": {"url": "https://jobs.example.test/j00"}},
    {"type": "tool_use", "name": "mcp__playwright__browser_navigate", "input": {"url": "https://other.example.test/"}},
    {"type": "text", "text": "Filled every field."}]}})
say({"type": "result", "result": "Done.\\nRESULT:APPLIED"})
"""


def test_one_application_run_reads_the_stream(box, tmp_path, monkeypatch):
    """The real process handling, with a stand-in for the claude command (no browser, no network)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(FAKE_CLAUDE, encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    write_applicant(box.data_dir)
    jobs = add_jobs(box, ["Acme"])
    write_letters(box, jobs)
    profile = box._applicant().profile
    outcome = apply_bridge.run_application(box._apply_spec(jobs[0], "submit"), box.data_dir, box.settings, profile, 9)
    assert outcome.status == "applied" and outcome.other_sites == ["other.example.test"]
    log = box.data_dir / box.settings.apply.logs_dir / "j00.jsonl"
    assert outcome.log == str(log) and "browser_navigate" in log.read_text(encoding="utf-8")
    stopped = apply_bridge.run_application(box._apply_spec(jobs[0], "stop"), box.data_dir, box.settings, profile, 9)
    assert stopped.status == "waiting"


def test_reading_files_runs_restricted_to_the_listed_folders(box, tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(FAKE_CLAUDE, encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("FAKE_ARGS", str(tmp_path / "args.json"))
    kb = tmp_path / "kb"
    kb.mkdir()
    write_applicant(box.data_dir, read_dirs=[kb])
    jobs = add_jobs(box, ["Acme"])
    write_letters(box, jobs)
    applicant = box._applicant()
    spec = box._apply_spec(jobs[0], "submit")
    apply_bridge.run_application(spec, box.data_dir, box.settings, applicant.profile, 9, applicant.apply_agent_read)
    seen = json.loads((tmp_path / "args.json").read_text(encoding="utf-8"))
    argv = seen["argv"]
    assert "--restricted" in argv and argv[argv.index("--tools") + 1] == "Read,Glob,Grep"
    assert argv[argv.index("--add-dir") + 1] == str(kb)
    deny = json.loads(argv[argv.index("--settings") + 1])["permissions"]["deny"]
    assert f"Read(/{box.data_dir.resolve()}/**)" in deny and f"Read(/{applicant.profile.resolve()})" in deny
    assert str(kb) in seen["prompt"] and "read-only" in seen["prompt"]


def test_without_folders_the_apply_agent_has_no_file_tools(tmp_path):
    command = apply_bridge.claude_command(settings(), tmp_path / "mcp.json")
    assert command[command.index("--tools") + 1] == "" and "--restricted" not in command
    assert "--add-dir" not in command and "FILES" not in apply_bridge.extra_rules("submit")


@pytest.mark.parametrize("where", ["root", "home", "data_parent", "inside_data", "missing", "has_profile"])
def test_folders_that_would_expose_private_files_are_refused(box, tmp_path, where):
    outside_profile = tmp_path / "secrets"
    outside_profile.mkdir()
    folders = {
        "root": Path("/"),
        "home": Path.home(),
        "data_parent": box.data_dir.parent,
        "inside_data": box.data_dir / "applicant",
        "missing": tmp_path / "nope",
        "has_profile": outside_profile,
    }
    write_applicant(box.data_dir, read_dirs=[folders[where]])
    if where == "has_profile":  # a profile kept outside the data directory is protected too
        config = yaml.safe_load((box.data_dir / "applicant" / "applicant.yaml").read_text(encoding="utf-8"))
        (outside_profile / "profile.json").write_text(json.dumps(PROFILE), encoding="utf-8")
        config["profile"] = str(outside_profile / "profile.json")
        (box.data_dir / "applicant" / "applicant.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    setup = box.get_apply_setup()
    assert setup["ready"] is False and setup["missing"]
    with pytest.raises(InvariantError):
        box.prepare_apply_batch(DAY)


def test_pdf_page_count():
    assert apply_bridge.pdf_page_count(b"<< /Type /Page >> << /Type /Pages >>") == 1


# --- orchestration files -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("skill", "needed"),
    [
        ("apply", {"get_apply_setup", "prepare_apply_batch", "start_applying", "apply_status", "mark_applications"}),
        ("cover-letter", {"get_cover_letter_materials", "get_resume", "check_cover_letter", "save_cover_letter"}),
    ],
)
def test_apply_skills(skill, needed):
    meta, text = frontmatter(ROOT / ".claude/skills" / skill / "SKILL.md")
    assert meta["name"] == skill and meta["description"] and "allowed-tools" not in meta
    called = set(re.findall(r"`([a-z_]+)\(", text))
    assert called <= set(TOOL_TIERS), called - set(TOOL_TIERS)
    assert needed <= called
    assert not re.search(r"qianqian|zhang", text, re.I)  # the general skill carries no personal facts


def test_cover_letter_writer_cannot_commit_or_apply():
    meta, _ = frontmatter(ROOT / ".claude/agents/cover-letter-writer.md")
    assert meta["name"] == "cover-letter-writer" and meta["model"] == "sonnet"
    tools = {t.strip() for t in str(meta["tools"]).split(",")}
    assert tools == {"Read", "Glob", "Grep"} | {  # may read files, never write them
        f"mcp__intent-agent__{name}"
        for name in ("get_cover_letter_materials", "get_resume", "show_job", "check_cover_letter", "save_cover_letter")
    }


def test_no_agent_may_read_the_applicant_profile_or_keys():
    deny = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))["permissions"]["deny"]
    assert {"Read(./data/local/.env)", "Read(./data/local/applicant/profile.json)"} <= set(deny)
