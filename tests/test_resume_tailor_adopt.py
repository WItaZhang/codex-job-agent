"""Resume tailoring, slice 2 (specs/resume-tailor.md section 3, items 3-4; section 9 item 10): adopting a version
for one job, the cover letter that must use it, and the checks before applying. All data is SYNTHETIC."""

import json

import pytest
import yaml

from intent_job_agent import applicant as applicant_files
from intent_job_agent.domain import InvariantError, Job
from intent_job_agent.form_fill import runner as form_fill
from intent_job_agent.tools import SCHEDULED_TOOLS, TOOL_TIERS

from .apply_fakes import write_sheet
from .test_resume_tailor import BASE_TEX, JD, FakeClaude, evaluation, make_box, run_dir, start

# The tailored version drops "used by 40 engineers": 40 is then not in the resume that is uploaded.
TAILORED = BASE_TEX.replace("used by 40 engineers", "shared across teams").replace(
    "on 3 product surfaces.", "on 3 product surfaces, measured online."
)


def letter(extra: str = "") -> str:
    body = " ".join(["I built ranking models and measured them online with the product team."] * 28)
    return f"Dear Hiring Team,\n\n{body} {extra}\n\nSam Example"


def tailored_box(tmp_path, evaluations=None):
    """A job j01 with a run whose v01 reached stop, and a second job j02."""
    box = make_box(tmp_path)
    box.letter_renderer = lambda text, pdf_path, name, day: pdf_path.write_bytes(b"%PDF-1.4 letter")
    box.store.add_job(
        Job(
            id="j02",
            title="ML Engineer",
            company="Other Co",
            url="https://jobs.example.test/j02",
            description=JD,
            source="greenhouse",
            tags=[],
        )  # fmt: skip
    )
    box.tailor_runner = FakeClaude(evaluations or [evaluation(issues=("major",)), evaluation()])
    run = start(box)["run_id"]
    box.evaluate_tailor_version(run, "v00")
    box.save_tailor_version(run, TAILORED)
    box.evaluate_tailor_version(run, "v01")
    return box, run


def tailored_id(box, job_id="j01"):
    return box._ready_applicant().tailored_for(job_id).id


def test_adopt_tool_tier():
    assert TOOL_TIERS["adopt_tailored_resume"] == "prepare"
    assert "adopt_tailored_resume" not in SCHEDULED_TOOLS


def test_only_a_sound_version_of_this_jobs_run_can_be_adopted(tmp_path):
    box, run = tailored_box(tmp_path, [evaluation(scores=(3, 4, 4, 4, 4, 4)), evaluation()])
    with pytest.raises(InvariantError, match="reason"):
        box.adopt_tailored_resume("j01", run, "v00")  # continue, not stop: needs a reason
    with pytest.raises(InvariantError, match="j02"):
        box.adopt_tailored_resume("j02", run, "v01")  # the run is for j01
    with pytest.raises(InvariantError, match="actor"):
        box.adopt_tailored_resume("j01", run, "v01", actor="someone")
    with pytest.raises(InvariantError, match="v09"):
        box.adopt_tailored_resume("j01", run, "v09")
    pasted = start(box, job_id=None, jd_text=JD, slug="acme-ml")["run_id"]
    box.tailor_runner.queue.append(evaluation())
    box.evaluate_tailor_version(pasted, "v00")
    with pytest.raises(InvariantError, match="pasted"):
        box.adopt_tailored_resume("j01", pasted, "v00")
    adopted = box.adopt_tailored_resume("j01", run, "v01")
    assert adopted["resume_id"] == tailored_id(box) and adopted["resume_id"].startswith("tailored-")
    history = (run_dir(box, run).parent / "adoptions.jsonl").read_text().splitlines()
    assert json.loads(history[-1])["actor"] == "delegated_coordinator"
    assert json.loads(history[-1])["version"] == "v01"


def test_a_failed_or_unevaluated_version_cannot_be_adopted(tmp_path):
    bad = evaluation()
    del bad["scores"]["format"]
    box, run = tailored_box(tmp_path, [evaluation(issues=("major",)), bad])
    with pytest.raises(InvariantError, match="fail|evaluation"):
        box.adopt_tailored_resume("j01", run, "v01")
    box.save_tailor_version(run, TAILORED + "% replaced")
    with pytest.raises(InvariantError, match="evaluation"):
        box.adopt_tailored_resume("j01", run, "v01")


def test_a_base_breaking_hard_checks_cannot_be_adopted(tmp_path):
    box = make_box(tmp_path)
    (box.data_dir / "applicant" / "resumes" / "ml.tex").write_text(BASE_TEX.replace("GraphLens", "Graph tool"))
    box.tailor_runner = FakeClaude([evaluation(issues=("major",))])
    run = start(box)["run_id"]
    box.evaluate_tailor_version(run, "v00")
    with pytest.raises(InvariantError, match="precheck"):
        box.adopt_tailored_resume("j01", run, "v00", reason="the base is fine")


def test_cover_letters_use_the_adopted_resume_for_that_job_only(tmp_path):
    box, run = tailored_box(tmp_path)
    box.adopt_tailored_resume("j01", run, "v01")
    rid = tailored_id(box)
    listed = {r["id"]: r for r in box.get_cover_letter_materials(job_id="j01")["resumes"]}
    assert listed[rid]["tailored"] is True and listed[rid]["job_id"] == "j01"
    assert rid not in {r["id"] for r in box.get_cover_letter_materials(job_id="j02")["resumes"]}
    assert "measured online" in box.get_resume(rid)["text"]

    plain = box.save_cover_letter("j01", "ml", letter())
    assert not plain["ok"] and any(rid in p for p in plain["problems"])
    dropped = box.save_cover_letter("j01", rid, letter("It served 40 engineers."))
    assert not dropped["ok"] and any("40" in p for p in dropped["problems"])
    assert box.save_cover_letter("j01", rid, letter())["ok"]
    assert box.store.cover_letter("j01")["resume_id"] == rid
    with pytest.raises(InvariantError, match="j01"):
        box.save_cover_letter("j02", rid, letter())


def test_withdrawing_or_readopting_voids_the_letter(tmp_path):
    box, run = tailored_box(tmp_path, [evaluation(issues=("major",)), evaluation(), evaluation()])
    box.adopt_tailored_resume("j01", run, "v01")
    rid = tailored_id(box)
    assert box.save_cover_letter("j01", rid, letter())["ok"]
    box.adopt_tailored_resume("j01", run, "v00", reason="the user prefers the base", actor="user")
    assert box.store.cover_letter("j01") is None and not box._letter_ready("j01")
    assert box.save_cover_letter("j01", rid, letter())["ok"]
    out = box.adopt_tailored_resume("j01")
    assert out["withdrawn"] is True
    assert box._ready_applicant().tailored_for("j01") is None
    assert box.store.cover_letter("j01") is None
    assert rid not in {r["id"] for r in box.get_cover_letter_materials(job_id="j01")["resumes"]}
    with pytest.raises(InvariantError):
        box.adopt_tailored_resume("j02")  # nothing adopted for j02


def test_applying_uploads_the_adopted_pdf_and_refuses_a_changed_one(tmp_path):
    box, run = tailored_box(tmp_path)
    box.adopt_tailored_resume("j01", run, "v01")
    rid = tailored_id(box)
    assert box.save_cover_letter("j01", rid, letter())["ok"]
    spec = box._apply_spec("j01", "stop")
    assert spec.resume_pdf == run_dir(box, run) / "v01" / "resume.pdf"
    assert spec.resume_text.read_text(encoding="utf-8").count("measured online") == 1

    write_sheet(box.data_dir)
    path = box._step_list(None, "x", [{"job_id": "j01", "company": "Synthetic Co", "title": "ML Engineer"}],
                          stem="tailor-check")  # fmt: skip

    def problems() -> str:
        try:
            form_fill.load_inputs(path, box._ready_applicant(), box.settings)
        except InvariantError as error:
            return str(error)
        return ""

    assert "changed or is gone" not in problems()
    (run_dir(box, run) / "v01" / "resume.pdf").write_text("edited after adoption", encoding="utf-8")
    with pytest.raises(InvariantError, match="changed or is gone"):
        box._apply_spec("j01", "stop")
    assert "changed or is gone" in problems()


def test_the_tailored_file_is_code_managed(tmp_path):
    box, run = tailored_box(tmp_path)
    box.adopt_tailored_resume("j01", run, "v01")
    path = box.data_dir / "applicant" / applicant_files.TAILORED_FILE
    entries = yaml.safe_load(path.read_text())
    assert [e["job_id"] for e in entries] == ["j01"] and len(entries[0]["sha256"]) == 64
    entries[0]["id"] = "ml"  # an entry that is not a tailored one is refused
    path.write_text(yaml.safe_dump(entries), encoding="utf-8")
    with pytest.raises(InvariantError, match="tailored"):
        box._ready_applicant()


def test_quick_modes_adopt_only_checked_versions_without_evaluation(tmp_path):
    from .test_resume_tailor import use_base

    box = make_box(tmp_path)
    base = BASE_TEX.replace(r"\end{document}", "\\section*{Skills}\nPython\n\\end{document}")
    use_base(box, base)
    direct = start(box, mode="base")["run_id"]
    assert box.adopt_tailored_resume("j01", direct, "v00")["version"] == "v00"
    run = start(box, mode="skills")["run_id"]
    with pytest.raises(InvariantError):
        box.adopt_tailored_resume("j01", run, "v00")
    with pytest.raises(InvariantError, match="Skills section"):
        box.save_tailor_version(run, base.replace("GraphLens", "Another"))
    saved = box.save_tailor_version(run, base.replace("Python", "Python, SQL"))
    assert saved == {"version": "v01", "pages": 1, "precheck": []}
    assert box.adopt_tailored_resume("j01", run, "v01")["version"] == "v01"
    assert box.get_tailor_run(run)["costs"]["evaluations"] == 0
