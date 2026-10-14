"""Slice 5: the apply list (spec 4.9) - who enters, order, why, status, files and the agreement rate.
All data is SYNTHETIC; the cases are the ones listed in plan.md (slice 5, section 3)."""

import csv
import json
import re

import pytest
import yaml

from intent_job_agent.apply_list import agreement
from intent_job_agent.domain import IntentModel, InvariantError, Job, Label, Salary
from intent_job_agent.store import Store
from intent_job_agent.tools import SCHEDULED_TOOLS, TOOL_TIERS, Toolbox

from .conftest import BASE_INTENT, BASE_VOCAB, ROOT, settings
from .test_slice2f import frontmatter

DAY1, DAY2, DAY3 = "2026-10-14", "2026-10-15", "2026-10-16"
US = ["location.us.austin"]


def make_box(tmp_path, compensation=None) -> Toolbox:
    data = tmp_path / "data"
    data.mkdir()
    s = settings()
    box = Toolbox(s, Store(tmp_path / "agent.sqlite", s), data_dir=data)
    spec = {**BASE_INTENT, "location": {"us": {"level": "require"}}}
    doc = {"intent": spec, "vocabulary": BASE_VOCAB, "compensation": compensation}
    (data / "intent.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    box.initialize_intent(str(data / "intent.yaml"))
    return box


@pytest.fixture
def box(tmp_path):
    return make_box(tmp_path)


def add(box, job_id, tags=None, **kw) -> str:
    """Store a job; tagged unless `tags` is None."""
    box.store.add_job(Job(id=job_id, title=kw.pop("title", f"Synthetic {job_id}"), tags=[], **kw))
    if tags is not None:
        box.store.set_job_tags(job_id, tags)
    return job_id


def label(box, job_id, value, day=DAY1) -> None:
    box.store.add_label(Label(id=f"label-{job_id}", job_id=job_id, day=day, slot="recommended", value=value))


def edit(box, text, change) -> None:
    """The user's explicit command, confirmed by picking its (only soft) option."""
    view = box.propose_intent_edit(text, [change])
    option = next(o for o in view["options"] if "proposal_id" in o)
    box.decide(view["id"], option["n"], summary=option["summary"])


def ids(result) -> list[str]:
    return [item["job_id"] for item in result["jobs"]]


# --- who enters, in what order, and why (cases 1-6) ---------------------------------------------


def test_who_enters_the_list_and_in_what_order(box):
    add(box, "A", ["role.ml_engineering", *US])  # strong_prefer: recommended
    add(box, "B", ["role.ml_engineering", "location.uk.london"])  # breaks require location.us
    add(box, "C", ["role.ml_engineering", *US])  # recommended, but the user rejected it
    add(box, "D", ["role.data_science", *US])  # below the line, but the user wanted it
    add(box, "E", ["role.ml_engineering", *US], source="greenhouse", board="acme")
    box.store.record_board_snapshot("greenhouse", "acme", [])  # E is gone from its board
    add(box, "F", None, title="ML Engineer")  # not fully tagged yet
    add(box, "G", ["role.ml_engineering", "tech_stack.python", *US])
    add(box, "H", ["role.backend_engineering", *US])
    add(box, "M", ["role.data_science", *US])  # misread only: judged by the intent like any other job
    label(box, "C", "reject")
    label(box, "D", "want")
    label(box, "M", "misread")

    result = box.build_apply_list(DAY1)
    assert ids(result) == ["D", "G", "A", "H"]  # wanted first, then by score
    by_id = {item["job_id"]: item for item in result["jobs"]}
    assert by_id["D"]["why"][0] == "你标了想投" and by_id["D"]["wanted"] is True
    assert by_id["A"]["why"] == ["role.ml_engineering 强烈偏好"] and by_id["A"]["wanted"] is False
    assert by_id["G"]["why"] == ["role.ml_engineering 强烈偏好", "tech_stack.python 偏好"]
    assert all(item["new"] for item in result["jobs"])
    assert result["pending"] == 4 and result["new"] == 4
    assert "score" not in by_id["A"]  # levels in words only (spec 3.1)


def test_a_superseded_reject_still_keeps_the_job_out(box):
    add(box, "C", ["specialty.recsys", *US])  # no entry: below the line, so the reject agrees
    label(box, "C", "reject")
    edit(box, "推荐算法很想做", {"op": "add", "ref": "specialty.recsys", "level": "strong_prefer"})
    assert box.store.label("label-C").superseded  # the confirmed change broke this label
    assert "C" not in ids(box.build_apply_list(DAY1))


def test_salary_below_the_floor_is_named(tmp_path):
    box = make_box(tmp_path, compensation={"currency": "USD", "period": "year", "floor": 200000})
    low = Salary(currency="USD", period="year", min=120000, max=150000)
    add(box, "S", ["role.ml_engineering", "specialty.ai_infra", "tech_stack.python", *US], salary=low)
    (item,) = box.build_apply_list(DAY1)["jobs"]
    assert item["why"][-1] == "薪资低于下限"


# --- status and "new" (cases 7-8) -------------------------------------------------------------------


def test_status_and_new_marks(box):
    for name in ("A", "G", "K"):
        add(box, name, ["role.ml_engineering", *US])
    first = box.build_apply_list(DAY1)
    assert box.build_apply_list(DAY1)["jobs"] == first["jobs"]  # same day: still new, nothing changes

    box.mark_applications(["A"], "applied")
    box.mark_applications(["G"], "skipped")
    add(box, "N", ["role.ml_engineering", *US])
    second = box.build_apply_list(DAY2)
    assert ids(second) == ["K", "N"]
    assert {item["job_id"]: item["new"] for item in second["jobs"]} == {"K": False, "N": True}

    box.mark_applications(["G"], "pending")  # the user changed their mind
    assert "G" in ids(box.build_apply_list(DAY2))


def test_marking_needs_a_listed_job_and_a_known_status(box):
    add(box, "A", ["role.ml_engineering", *US])
    add(box, "X", ["role.ml_engineering", *US])
    box.build_apply_list(DAY1)
    box.store.add_job(Job(id="never", title="Unlisted", tags=[]))
    with pytest.raises(InvariantError):
        box.mark_applications(["never"], "applied")
    with pytest.raises(InvariantError):
        box.mark_applications(["A"], "interviewing")
    with pytest.raises(InvariantError):  # nothing is changed when one id is wrong
        box.mark_applications(["X", "never"], "applied")
    assert "X" in ids(box.build_apply_list(DAY1))


def test_a_job_the_intent_drops_comes_back_without_new(box):
    add(box, "H", ["role.backend_engineering", *US])
    assert ids(box.build_apply_list(DAY1)) == ["H"]
    edit(
        box,
        "后端不太想做",
        {"op": "set", "ref": "role.backend_engineering", "from_level": "prefer", "to_level": "avoid"},
    )
    assert ids(box.build_apply_list(DAY2)) == []
    edit(
        box,
        "后端其实可以",
        {"op": "set", "ref": "role.backend_engineering", "from_level": "avoid", "to_level": "prefer"},
    )
    (item,) = box.build_apply_list(DAY3)["jobs"]
    assert item["job_id"] == "H" and item["new"] is False


# --- agreement rate (cases 9-10) ----------------------------------------------------------------------


class Labels:
    """A label source for the pure agreement function."""

    def __init__(self, labels, jobs):
        self._labels, self._jobs = labels, jobs

    def labels(self):
        return self._labels

    def job(self, job_id):
        return self._jobs[job_id]

    def effective_tags(self, job_id):
        return self._jobs[job_id].tags


def test_agreement_counts_the_last_seven_labelled_days():
    intent = IntentModel.build(BASE_INTENT)
    good = Job(id="good", title="Good", tags=["role.ml_engineering"])  # recommended
    poor = Job(id="poor", title="Poor", tags=["role.data_science"])  # not recommended
    days = ["2026-09-01", "2026-09-02", "2026-09-05", "2026-09-09", "2026-09-10",
            "2026-09-20", "2026-09-21", "2026-09-30", "2026-10-02"]  # fmt: skip
    labels = []

    def add_label(day, job, value, superseded=False):
        labels.append(Label(id=f"l{len(labels)}", job_id=job.id, day=day, slot="recommended", value=value,
                            superseded=superseded))  # fmt: skip

    add_label(days[0], poor, "want")  # outside the window (9 labelled days, gaps included)
    add_label(days[1], good, "reject")  # outside the window
    add_label(days[2], good, "want")  # agrees
    add_label(days[3], poor, "reject")  # agrees
    add_label(days[4], good, "reject")  # disagrees
    add_label(days[5], poor, "want")  # disagrees
    add_label(days[6], poor, "misread")  # not an intent signal
    add_label(days[7], good, "reject", superseded=True)  # outdated: does not count
    add_label(days[8], good, "want")  # agrees
    result = agreement(intent, Labels(labels, {"good": good, "poor": poor}), settings(), 7)
    assert result.days == tuple(days[2:])
    assert (result.want_agree, result.want_total) == (2, 3)
    assert (result.reject_agree, result.reject_total) == (1, 2)
    assert result.text() == "最近 7 个标注日一致率 60%（3/5；想投 2/3，不要 1/2）"


def test_agreement_with_fewer_days_and_with_no_labels(box):
    assert box.build_apply_list(DAY1)["agreement"] == "还没有 label"
    add(box, "A", ["role.ml_engineering", *US])
    label(box, "A", "want", day="2026-10-10")
    assert box.build_apply_list(DAY1)["agreement"] == "最近 1 个标注日一致率 100%（1/1；想投 1/1，不要 0/0）"


# --- files, tools and the skill --------------------------------------------------------------------


def test_files_for_the_next_step(box):
    add(box, "A", ["role.ml_engineering", *US], url="https://example.test/a", description="SYNTHETIC secret text")
    result = box.build_apply_list(DAY1)
    folder = box.data_dir / box.settings.apply_list.dir
    assert result["files"] == {"json": str(folder / f"{DAY1}.json"), "csv": str(folder / f"{DAY1}.csv")}

    doc = json.loads((folder / f"{DAY1}.json").read_text(encoding="utf-8"))
    assert doc["day"] == DAY1 and doc["agreement"] == result["agreement"]
    (item,) = doc["jobs"]
    assert {"n", "job_id", "new", "wanted", "title", "company", "location", "url", "why"} <= set(item)
    assert item["url"] == "https://example.test/a" and item["location"] == "us.austin"

    with (folder / f"{DAY1}.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["job_id"] == "A" and rows[0]["why"] == "role.ml_engineering 强烈偏好"
    for path in folder.iterdir():
        assert "secret" not in path.read_text(encoding="utf-8")  # posting text never goes into the list


def test_posting_text_cannot_become_a_spreadsheet_formula(box):
    add(box, "A", ["role.ml_engineering", *US], title='=HYPERLINK("https://evil.test","Apply")', company="@corp")
    box.build_apply_list(DAY1)
    path = box.data_dir / box.settings.apply_list.dir / f"{DAY1}.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        (row,) = list(csv.DictReader(handle))
    assert row["title"].startswith("'=") and row["company"] == "'@corp"


def test_the_day_must_be_a_date(box):
    with pytest.raises(InvariantError):
        box.build_apply_list("../../escape")


def test_only_the_first_n_are_returned_but_the_files_hold_all(box):
    shown = box.settings.apply_list.shown
    for n in range(shown + 3):
        add(box, f"j{n}", ["role.ml_engineering", *US])
    result = box.build_apply_list(DAY1)
    assert len(result["jobs"]) == shown and result["pending"] == shown + 3
    assert len(box.build_apply_list(DAY1, limit=shown + 3)["jobs"]) == shown + 3
    doc = json.loads((box.data_dir / box.settings.apply_list.dir / f"{DAY1}.json").read_text(encoding="utf-8"))
    assert len(doc["jobs"]) == shown + 3


def test_new_tools_are_prepare_tier_and_marking_is_never_scheduled():
    assert TOOL_TIERS["build_apply_list"] == TOOL_TIERS["mark_applications"] == "prepare"
    assert "mark_applications" not in SCHEDULED_TOOLS


def test_apply_list_skill():
    meta, text = frontmatter(ROOT / ".claude/skills/apply-list/SKILL.md")
    assert meta["name"] == "apply-list" and meta["description"]
    called = set(re.findall(r"`([a-z_]+)\(", text))
    assert called <= set(TOOL_TIERS), called - set(TOOL_TIERS)
    assert {"build_apply_list", "mark_applications"} <= called
    assert "record_labels" not in called  # skipping is not a label
    assert "allowed-tools" not in meta
