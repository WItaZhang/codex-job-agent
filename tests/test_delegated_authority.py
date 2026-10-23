"""Decision 25: synthetic records, fake runners, no application websites or private data."""

import asyncio
import importlib.util
import json
import tomllib

import pytest
import yaml

from intent_job_agent import applicant
from intent_job_agent.config import Settings
from intent_job_agent.domain import InvariantError
from intent_job_agent.mcp_server import build_server
from intent_job_agent.tools import COMMIT_TOOLS

from . import test_apply_form_fill as scenarios
from .conftest import ROOT
from .test_apply_form_fill import direct, ready

box = scenarios.box


def autonomous(box):
    box.settings = box.settings.model_copy(update={"apply": box.settings.apply.model_copy(update={"review_first": 0})})


def change_list(box, names):
    path = box.data_dir / "applicant/applicant.yaml"
    data = yaml.safe_load(path.read_text())
    if names is None:
        data.pop("careful_companies")
    else:
        data["careful_companies"] = names
    path.write_text(yaml.safe_dump(data))


@pytest.mark.parametrize(
    "company,names,expected",
    [
        ("Stripe", ["Stripe, Inc."], True),
        ("Acme LLC", ["Acme Corporation"], True),
        ("ACME Research", ["Acme"], True),
        ("", [], True),
        ("Unknown", [], True),
        ("N/A", [], True),
        ("Beta", ["Acme"], False),
        ("Beta", [], False),
    ],
)
def test_conservative_company_matching(company, names, expected):
    assert applicant.careful(company, names) is expected


def test_shipped_default_is_autonomous():
    assert Settings.load(ROOT / "configs/default.yaml").apply.review_first == 0


def test_normal_and_retry_routes_do_not_require_review(box):
    autonomous(box)
    jobs = ready(box, ["Beta", "Stripe"])
    batch = box.prepare_apply_batch(only=jobs)
    assert [j["mode"] for j in batch["jobs"]] == ["submit", "careful"]
    box.start_applying(batch["batch_id"], batch["summary"])
    assert [(s.job_id, s.mode) for s in direct(box)] == [(jobs[0], "submit")]
    assert box.store.applications()[jobs[1]]["status"] == "waiting"


def test_explicit_fill_only_is_preserved(box):
    autonomous(box)
    jobs = ready(box, ["Beta"])
    batch = box.prepare_apply_batch(only=jobs, stop_before_submit=True)
    assert batch["jobs"][0]["mode"] == "review"
    box.start_applying(batch["batch_id"], batch["summary"])
    assert direct(box) == []


def test_list_added_after_preparation_is_checked_before_execution(box):
    autonomous(box)
    ready(box, ["Beta"])
    batch = box.prepare_apply_batch()
    change_list(box, ["Beta Inc."])
    box.start_applying(batch["batch_id"], batch["summary"])
    assert direct(box) == []
    assert box.store.applications()["j00"]["status"] == "waiting"


@pytest.mark.parametrize("stage", ["prepare", "start", "spec"])
def test_missing_list_fails_closed(box, stage):
    autonomous(box)
    ready(box, ["Beta"])
    batch = box.prepare_apply_batch()
    change_list(box, None)
    with pytest.raises(InvariantError, match="careful_companies"):
        if stage == "prepare":
            box.prepare_apply_batch()
        elif stage == "start":
            box.start_applying(batch["batch_id"], batch["summary"])
        else:
            box._apply_spec("j00", "submit")
    assert direct(box) == []


def test_last_submit_guard_checks_current_company_list(box):
    autonomous(box)
    ready(box, ["Stripe"])
    with pytest.raises(InvariantError, match="protected"):
        box._apply_spec("j00", "submit")


def test_restored_hold_does_not_trust_old_submit_mode(box):
    autonomous(box)
    ready(box, ["Beta"])
    batch = box.prepare_apply_batch()
    box.store.record_application_result(
        "j00",
        "held",
        "captcha",
        hold={
            "batch_id": batch["batch_id"],
            "phase": "rest",
            "n": 1,
            "mode": "submit",
            "via": "applypilot",
            "reason": "captcha",
            "note": "synthetic",
            "tab_id": None,
            "tab_url": None,
            "times": 1,
        },
    )
    change_list(box, ["Beta"])
    plan = box.prepare_resume(["j00"])
    assert plan["jobs"][0]["mode"] == "careful"
    box.resume_applying(plan["job_ids"], plan["summary"])
    assert direct(box) == []


def test_authorization_source_and_project_permissions(box):
    decision = box.store.decisions()[-1]
    assert json.loads(decision["payload"])["approved_via"] == "delegated_coordinator"
    permissions = json.loads((ROOT / ".claude/settings.json").read_text())["permissions"]
    expected = {f"mcp__intent-agent__{name}" for name in COMMIT_TOOLS}
    assert expected <= set(permissions["allow"])
    assert not expected & set(permissions.get("ask", []))
    config = tomllib.loads((ROOT / ".codex/config.toml").read_text())
    assert all(
        config["mcp_servers"]["intent-agent"]["tools"][name]["approval_mode"] == "approve" for name in COMMIT_TOOLS
    )
    for tool in asyncio.run(build_server(box).list_tools()):
        assert not (tool.meta or {}).get("anthropic/requiresUserInteraction", False)


def test_codex_lock_identity_is_namespaced(monkeypatch):
    spec = importlib.util.spec_from_file_location("code_owner", ROOT / ".claude/hooks/code_owner.py")
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    for key in ("CLAUDE_CODE_HOST_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "synthetic")
    assert owner.session_id() == "codex:synthetic"


@pytest.mark.parametrize("name", ["nan", "undefined", "Not disclosed", "Confidential Company"])
def test_placeholder_company_is_not_submittable(name):
    assert applicant.careful(name, [])


def test_company_tags_also_protect_a_named_listing(box):
    autonomous(box)
    ready(box, ["Beta"])
    job = box.store.job("j00")
    box.store.add_job(job.model_copy(update={"tags": [*job.tags, "company.stripe"]}))
    batch = box.prepare_apply_batch()
    assert batch["jobs"][0]["mode"] == "careful"


def test_mid_step_list_change_skips_only_affected_job(box):
    autonomous(box)
    ready(box, ["Alpha", "Beta", "Gamma"])
    original = box.apply_runner

    def execute(spec):
        result = original(spec)
        if spec.job_id == "j00":
            change_list(box, ["Beta"])
        return result

    box.apply_runner = execute
    batch = box.prepare_apply_batch()
    box.start_applying(batch["batch_id"], batch["summary"])
    status = box.apply_status()
    assert status["status"] == "done"
    assert [(s.job_id, s.mode) for s in original.calls] == [("j00", "submit"), ("j02", "submit")]
    assert box.store.applications()["j01"]["status"] == "pending"
    assert next(j for j in status["jobs"] if j["job_id"] == "j01")["reason"] == "protection_changed"


def seed_hold(box, job_id, batch, n=1, mode="submit"):
    box.store.record_application_result(
        job_id,
        "held",
        "captcha",
        hold={
            "batch_id": batch["batch_id"],
            "phase": "rest",
            "n": n,
            "mode": mode,
            "via": "applypilot" if mode == "submit" else "form_fill",
            "reason": "captcha",
            "note": "synthetic",
            "tab_id": None,
            "tab_url": None,
            "times": 1,
        },
    )


def test_mid_resume_list_change_keeps_hold_without_stale_resuming(box):
    autonomous(box)
    jobs = ready(box, ["Alpha", "Beta", "Gamma"])
    batch = box.prepare_apply_batch()
    for n, j in enumerate(jobs, 1):
        seed_hold(box, j, batch, n)
    original = box.apply_runner

    def execute(spec):
        result = original(spec)
        if spec.job_id == "j00":
            change_list(box, ["Beta"])
        return result

    box.apply_runner = execute
    plan = box.prepare_resume(jobs)
    box.resume_applying(plan["job_ids"], plan["summary"])
    assert box.apply_status()["status"] == "done"
    assert [s.job_id for s in original.calls] == ["j00", "j02"]
    hold = box.store.hold("j01")
    assert hold["resuming"] == 0 and hold["times"] == 1 and hold["reason"] == "captcha"


def test_coordinator_status_changes_do_not_claim_user_actions(box):
    autonomous(box)
    ready(box, ["Beta"])
    box.prepare_apply_batch()
    box.store.record_application_result("j00", "failed", "synthetic")
    before = box.store.apply_attempts()
    box.mark_applications(["j00"], "pending", actor="delegated_coordinator")
    assert box.store.apply_attempts() == before
    decision = box.store.decisions()[-1]
    assert json.loads(decision["payload"])["approved_via"] == "delegated_coordinator"
    with pytest.raises(InvariantError):
        box.mark_applications(["j00"], "applied", actor="delegated_coordinator")
    with pytest.raises(InvariantError):
        box.mark_applications(["j00"], "skipped", actor="delegated_coordinator")
    box.mark_applications(["j00"], "applied", actor="user", user_report="User explicitly reported submission")
    assert box.store.apply_attempts()[-1]["via"] == "user"


@pytest.mark.parametrize(
    "name",
    [
        "Stealth Startup",
        "Private",
        "Anonymous",
        "Company Confidential",
        "Hiring Company",
        "未公开",
        "某公司",
        "Global Confidential Employer",
    ],
)
def test_generic_unidentified_employers_stop(name):
    assert applicant.careful(name, [])


@pytest.mark.parametrize("name", ["Co", "Inc.", "LLC", "..."])
def test_suffix_only_protection_entry_reports_error(box, name):
    change_list(box, [name])
    with pytest.raises(InvariantError, match="must identify a company"):
        box._applicant()


def test_saved_protection_survives_list_deletion_and_new_retry(box):
    autonomous(box)
    jobs = ready(box, ["Stripe"])
    first = box.prepare_apply_batch()
    assert first["jobs"][0]["mode"] == "careful"
    change_list(box, [])
    retry = box.prepare_apply_batch(only=jobs)
    assert retry["jobs"][0]["mode"] == "careful"
    box.start_applying(retry["batch_id"], retry["summary"])
    assert direct(box) == []


def test_source_name_is_not_an_employer(box):
    autonomous(box)
    ready(box, ["Beta"])
    job = box.store.job("j00")
    box.store.add_job(job.model_copy(update={"company": job.source}))
    assert box.prepare_apply_batch()["jobs"][0]["mode"] == "careful"


def test_per_job_guards_never_recompute_whole_database_groups(box, monkeypatch):
    autonomous(box)
    ready(box, ["Beta"])

    def unexpected(*args):
        pytest.fail("per-job guard must not group/score the full database")

    monkeypatch.setattr(box, "_same_posting", unexpected)
    for _ in range(30):
        assert not box._submission_block("j00")
        box._apply_spec("j00", "submit")


def test_instructions_preserve_privacy_and_unrelated_discovery_guidance():
    from intent_job_agent.mcp_server import INSTRUCTIONS

    assert "Never ask for or show passwords, an SSN, card details or keys in chat." in INSTRUCTIONS
    assert "country_include" in INSTRUCTIONS and "list_backfill_jobs" in INSTRUCTIONS
    assert "agreement line first" in INSTRUCTIONS and "poll boards_status" in INSTRUCTIONS


@pytest.mark.parametrize("company,board", [("Talent.com", "Talent.com"), ("Indeed", "indeed")])
def test_aggregator_name_without_employer_is_protected(box, company, board):
    autonomous(box)
    ready(box, [company])
    job = box.store.job("j00")
    box.store.add_job(job.model_copy(update={"source": "jobspy", "board": board}))
    assert box.prepare_apply_batch()["jobs"][0]["mode"] == "careful"


def test_foreign_lock_cannot_be_claimed_or_released(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("code_owner_test", ROOT / ".claude/hooks/code_owner.py")
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    monkeypatch.setattr(owner, "session_id", lambda: "codex:synthetic")
    monkeypatch.setattr(owner, "git_paths", lambda directory: (tmp_path, tmp_path, tmp_path))
    lock = tmp_path / owner.LOCK_NAME
    lock.write_text("other-session\nother\n")
    assert owner.cli(["claim"]) == 1
    assert owner.cli(["release"]) == 1
    assert lock.read_text() == "other-session\nother\n"


def test_saved_company_aliases_protect_merged_listing(box):
    autonomous(box)
    ready(box, ["Beta", "Beta"])
    first, other = box.store.job("j00"), box.store.job("j01")
    # Same public application URL is existing deterministic duplicate evidence; no inferred company relation.
    box.store.add_job(
        other.model_copy(update={"url": first.url, "title": first.title, "tags": [*other.tags, "company.stripe"]})
    )
    batch = box.prepare_apply_batch()
    assert len(batch["jobs"]) == 1
    assert batch["jobs"][0]["mode"] == "careful"
    box.start_applying(batch["batch_id"], batch["summary"])
    assert direct(box) == []


def test_5000_jobs_30_applications_group_once_per_tool_call(box, monkeypatch):
    import time

    from .test_slice6 import add_jobs, write_letters

    autonomous(box)
    jobs = add_jobs(box, [f"Synthetic Employer {n}" for n in range(5000)])
    write_letters(box, jobs[:30])
    original = box._apply_postings
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(box, "_apply_postings", counted)
    start = time.monotonic()
    batch = box.prepare_apply_batch(only=jobs[:30])
    preparation = time.monotonic() - start
    assert len(calls) == 1
    calls.clear()
    start = time.monotonic()
    box.start_applying(batch["batch_id"], batch["summary"])
    execution = time.monotonic() - start
    assert len(calls) == 1
    assert len(direct(box)) == 30
    print(f"Synthetic 5000 jobs / 30 applications: prepare={preparation:.3f}s start={execution:.3f}s; grouping=1+1")


def test_mid_resume_missing_list_preserves_all_remaining_holds(box):
    autonomous(box)
    jobs = ready(box, ["Alpha", "Beta", "Gamma"])
    batch = box.prepare_apply_batch()
    for n, job in enumerate(jobs, 1):
        seed_hold(box, job, batch, n)
    original = box.apply_runner

    def execute(spec):
        result = original(spec)
        change_list(box, None)
        return result

    box.apply_runner = execute
    plan = box.prepare_resume(jobs)
    box.resume_applying(plan["job_ids"], plan["summary"])
    assert box.apply_status()["status"] == "done"
    assert [spec.job_id for spec in original.calls] == ["j00"]
    for job in jobs[1:]:
        hold = box.store.hold(job)
        assert hold["resuming"] == 0 and hold["times"] == 1 and hold["reason"] == "captcha"
