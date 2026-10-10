"""Slice 2e: explicit intent edits and the degree_requirement dimension (with backfill).

Everything here is SYNTHETIC test data.
"""

import json

import pytest

from intent_job_agent.domain import DIMENSIONS, IntentModel, InvariantError
from intent_job_agent.scoring import score_job
from intent_job_agent.store import Store
from intent_job_agent.tools import SCHEDULED_TOOLS, TOOL_TIERS, Toolbox

from .conftest import settings
from .test_tools import _pool, import_jobs, tagged, write_init

DAY1 = "2026-10-08"
CHINA_REQUIRE = [{"op": "add", "ref": "location.china", "level": "require"}]


@pytest.fixture
def box(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    s = settings()
    return Toolbox(s, Store(tmp_path / "agent.sqlite", s), data_dir=data)


def change_options(view: dict) -> list[dict]:
    return [o for o in view["options"] if "proposal_id" in o]


# --- degree_requirement dimension ---------------------------------------------------------


def test_degree_requirement_is_a_dimension_with_builtin_aliases(box):
    assert "degree_requirement" in DIMENSIONS
    box.initialize_intent(write_init(box))
    tagged(box, [("phd_job", [])])
    result = box.submit_job_tags("phd_job", ["degree_requirement.博士", "degree_requirement.ms"])
    assert result["tags"] == ["degree_requirement.phd", "degree_requirement.masters"]
    assert result["new_keys"] == []


# --- explicit intent edits ---------------------------------------------------------------------


def test_command_offers_the_hard_option_and_its_soft_twin_without_writing(box):
    box.initialize_intent(write_init(box))
    view = box.propose_intent_edit("中国也可以", CHINA_REQUIRE)
    assert view["kind"] == "command"
    assert view["request"] == "中国也可以"
    summaries = [o["summary"] for o in change_options(view)]
    assert summaries == ["location.china：未设置 → 强烈偏好", "location.china：未设置 → 必须"]
    assert [o.get("choice") for o in view["options"][-2:]] == ["feedback", "no_change"]
    assert box.get_intent()["version"] == 1  # nothing is written before the user decides
    assert [a["id"] for a in box.list_open_analyses()] == [view["id"]]


def test_command_options_are_not_filtered_by_direction(box):
    box.initialize_intent(write_init(box))
    _pool(box)
    today = box.select_today(DAY1)["jobs"]
    box.record_labels(DAY1, [{"job_id": j["job_id"], "value": "want"} for j in today if j["slot"] == "recommended"])
    view = box.propose_intent_edit("顺便加个偏好", [{"op": "add", "ref": "domain.healthcare", "level": "prefer"}])
    options = change_options(view)
    assert [o["summary"] for o in options] == ["domain.healthcare：未设置 → 偏好"]
    assert options[0]["replay"].startswith("修好 0 条")


def test_deciding_a_command_writes_a_version_logged_as_a_command(box):
    box.initialize_intent(write_init(box))
    view = box.propose_intent_edit("中国也可以", CHINA_REQUIRE)
    chosen = next(o for o in change_options(view) if o["summary"].endswith("必须"))
    with pytest.raises(InvariantError):
        box.decide(view["id"], chosen["n"], summary="location.china：未设置 → 偏好")
    intent = box.decide(view["id"], chosen["n"], summary=chosen["summary"])
    assert intent["version"] == 2
    assert {"ref": "location.china", "level": "必须", "exceptions": []} in intent["entries"]
    decision = [d for d in box.store.decisions() if d["kind"] == "accept"][-1]
    payload = json.loads(decision["payload"])
    assert payload["origin"] == "command"
    assert payload["approved_via"] == "host_permission_prompt"
    assert box.list_open_analyses() == []


def test_a_command_built_on_an_old_version_must_be_refreshed(box):
    box.initialize_intent(write_init(box))
    first = box.propose_intent_edit("中国也可以", CHINA_REQUIRE)
    second = box.propose_intent_edit("加个金融偏好", [{"op": "add", "ref": "domain.fintech", "level": "prefer"}])
    hard = next(o for o in change_options(first) if o["summary"].endswith("必须"))
    box.decide(first["id"], hard["n"], summary=hard["summary"])
    option = change_options(second)[0]
    assert box.get_analysis(second["id"])["stale"]
    with pytest.raises(InvariantError):
        box.decide(second["id"], option["n"], summary=option["summary"])
    refreshed = box.refresh(second["id"])
    assert refreshed["status"] == "open"  # a command has no label that could be "resolved"
    option = change_options(refreshed)[0]
    assert box.decide(second["id"], option["n"], summary=option["summary"])["version"] == 3


def test_a_command_that_breaks_the_location_hierarchy_is_refused(box):
    intent = {"location": {"china": {"level": "exclude"}}, "role": {"ml_engineering": {"level": "prefer"}}}
    box.initialize_intent(write_init(box, intent=intent))
    with pytest.raises(InvariantError):
        box.propose_intent_edit("上海可以", [{"op": "add", "ref": "location.china.shanghai", "level": "prefer"}])
    with pytest.raises(InvariantError):
        box.propose_intent_edit("什么都不改", [])
    assert box.list_open_analyses() == []


def test_two_country_requires_are_alternatives():
    intent = IntentModel.build({"location": {"us": {"level": "require"}, "china": {"level": "require"}}})
    s = settings()
    assert not score_job(intent, ["location.us.seattle"], None, s).excluded
    assert not score_job(intent, ["location.china.shanghai"], None, s).excluded
    assert score_job(intent, ["location.uk"], None, s).excluded


# --- backfill --------------------------------------------------------------------------------------


def _labelled_day(box) -> list[dict]:
    """A day of labels on jobs tagged before slice 2e, i.e. without degree_requirement coverage."""
    box.initialize_intent(write_init(box))
    _pool(box)
    with box.store.db:  # as if these jobs had been tagged before the dimension existed
        box.store.db.execute("DELETE FROM backfills")
    today = box.select_today(DAY1)["jobs"]
    target = next(j for j in today if j["slot"] == "recommended")
    box.record_labels(DAY1, [{"job_id": target["job_id"], "value": "reject"}])
    return today


def test_jobs_tagged_now_already_cover_the_new_dimension(box):
    box.initialize_intent(write_init(box))
    tagged(box, [("new", ["role.ml_engineering", "degree_requirement.phd"])])
    assert box.list_backfill_jobs("degree_requirement")["jobs"] == []
    with pytest.raises(InvariantError):
        box.backfill_tags("new", "degree_requirement", ["degree_requirement.masters"])


def test_backfill_lists_tagged_jobs_including_shown_and_labelled_ones(box):
    today = _labelled_day(box)
    tagged(box, [("fresh", ["role.ml_engineering"])])
    import_jobs(box, [{"id": "untagged", "title": "Not tagged yet", "description": "SYNTHETIC"}])
    listing = box.list_backfill_jobs("degree_requirement", limit=100)
    listed = [j["job_id"] for j in listing["jobs"]]
    labelled = {label.job_id for label in box.store.labels()}
    assert listed[0] in labelled  # labelled jobs first: they matter for open analyses
    assert {j["job_id"] for j in today} <= set(listed)
    assert "fresh" not in listed  # tagged after the dimension existed
    assert "untagged" not in listed and len(listed) == 15
    assert "degree_requirement" in listing["rules"]
    with pytest.raises(InvariantError):
        box.list_backfill_jobs("role")  # only dimensions configured for backfill


def test_backfill_only_adds_tags_of_the_new_dimension(box):
    today = _labelled_day(box)
    shown = today[0]["job_id"]
    before = box.store.effective_tags(shown)
    with pytest.raises(InvariantError):
        box.backfill_tags(shown, "degree_requirement", ["tech_stack.rust"])
    with pytest.raises(InvariantError):
        box.backfill_tags(shown, "role", ["role.data_science"])
    result = box.backfill_tags(shown, "degree_requirement", ["degree_requirement.博士"])
    assert result["tags"] == before + ["degree_requirement.phd"]
    assert box.store.effective_tags(shown) == before + ["degree_requirement.phd"]
    assert shown in box.store.shown_job_ids()
    with pytest.raises(InvariantError):  # once per job and dimension
        box.backfill_tags(shown, "degree_requirement", ["degree_requirement.masters"])
    other = today[1]["job_id"]
    assert box.backfill_tags(other, "degree_requirement", [])["tags"] == box.store.effective_tags(other)
    listed = {j["job_id"] for j in box.list_backfill_jobs("degree_requirement", limit=100)["jobs"]}
    assert shown not in listed and other not in listed


def test_backfilling_a_labelled_job_makes_open_analyses_stale(box):
    _labelled_day(box)
    [analysis] = box.list_open_analyses()
    assert analysis["step1"] == "ask" and not analysis["stale"]
    job_id = analysis["jobs"][0]["job_id"]
    box.backfill_tags(job_id, "degree_requirement", ["degree_requirement.phd"])
    assert box.get_analysis(analysis["id"])["stale"]
    refreshed = box.refresh(analysis["id"])
    assert not refreshed["stale"]
    known = {c["cause_id"] for c in analysis["causes"]}
    assert known <= {c["cause_id"] for c in refreshed["causes"]}  # earlier causes keep their ids
    degree = next(c for c in refreshed["causes"] if c["refs"] == ["degree_requirement.phd"])
    view = box.choose_cause(analysis["id"], degree["cause_id"])
    summaries = [o["summary"] for o in change_options(view)]
    assert "degree_requirement.phd：未设置 → 强烈回避" in summaries
    assert "degree_requirement.phd：未设置 → 排除" in summaries


# --- tiers ---------------------------------------------------------------------------------------------


def test_new_tools_are_prepare_level_and_not_for_scheduled_runs():
    for name in ("propose_intent_edit", "list_backfill_jobs", "backfill_tags"):
        assert TOOL_TIERS[name] == "prepare"
        assert name not in SCHEDULED_TOOLS
