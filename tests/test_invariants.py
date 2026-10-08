"""Guarantees the code must enforce regardless of what the model says (spec §6, §9)."""

import random

import pytest

from intent_job_agent.domain import IntentModel, InvariantError, Vocabulary
from intent_job_agent.engine import LabelInput
from intent_job_agent.llm import FakeClient
from intent_job_agent.replay import evaluate

from .conftest import BASE_INTENT, BASE_VOCAB, World

DAY1 = "2026-10-08"


def _one_analysis(world):
    job = world.job(["role.ml_engineering", "company_type.big_tech", "work_mode.onsite"])
    report = world.day(
        DAY1, LabelInput(job=job, value="reject", slot="recommended", reason_keys=["company_type.big_tech"])
    )
    return report.analyses[0]


def test_intent_can_be_initialized_only_once(world):
    world.init()
    with pytest.raises(InvariantError):
        world.store.initialize(IntentModel.build(BASE_INTENT), Vocabulary.build(BASE_VOCAB))


def test_unknown_proposal_cannot_be_applied(world):
    analysis = _one_analysis(world)
    with pytest.raises(InvariantError):
        world.engine.decide(analysis.id, "forged-proposal")
    with pytest.raises(InvariantError):
        world.store.apply_decision("forged-proposal")


def test_stale_proposal_cannot_be_applied(world):
    analysis = _one_analysis(world)
    first, second = analysis.proposals[0], analysis.proposals[1]
    world.engine.decide(analysis.id, first.id)
    with pytest.raises(InvariantError):
        world.engine.decide(analysis.id, second.id)


def test_model_suggested_changes_are_validated_before_they_reach_the_user(tmp_path):
    bad = [
        {"op": "add", "ref": "company_type.big_tech", "level": "exclude"},  # entry exists: would be a parallel entry
        {"op": "add", "ref": "visa.h1b", "level": "avoid"},  # unknown dimension
        {"op": "add_exception", "ref": "company_type.big_tech", "when": "work_mode.remote", "level": "avoid"},
    ]
    llm = FakeClient(handlers={"FeedbackChanges": lambda prompt: {"changes": bad}})
    world = World(tmp_path, llm=llm)
    analysis = _one_analysis(world)
    analysis = world.engine.feedback(analysis.id, "anything")
    assert analysis.proposals == []
    assert analysis.rejected_suggestions  # surfaced for debugging, never offered


def test_every_version_after_the_first_has_a_user_decision(world):
    analysis = _one_analysis(world)
    world.engine.decide(analysis.id, analysis.proposals[0].id)
    versions = world.store.versions()
    assert versions[0]["decision_id"] is None  # initialization
    decisions = {d["id"]: d for d in world.store.decisions()}
    for version in versions[1:]:
        decision = decisions[version["decision_id"]]
        assert decision["kind"] == "accept"
        assert decision["proposal_id"]


# ---------------------------------------------------------------------------
# Independent recomputation of replay (spec §9.4). Deliberately written without
# importing the scoring module.

POINTS = {"strong_avoid": -2, "avoid": -1, "prefer": 1, "strong_prefer": 2}


def independent_recommended(intent_spec: dict, tags: list[str], threshold: float = 1) -> bool:
    expanded = set(tags)
    for tag in tags:
        if tag.startswith("location.") and tag.count(".") == 2:
            expanded.add(tag.rsplit(".", 1)[0])
    entries = {f"{d}.{k}": e for d, keys in intent_spec.items() for k, e in keys.items()}
    requires: dict[str, set] = {}
    for ref, e in entries.items():
        if e["level"] == "exclude" and ref in expanded:
            return False
        if e["level"] == "require":
            requires.setdefault(ref.split(".")[0], set()).add(ref)
    for refs in requires.values():
        if not refs & expanded:
            return False
    total = 0
    for tag in tags:
        ref = tag
        if ref not in entries and tag.startswith("location.") and tag.count(".") == 2:
            ref = tag.rsplit(".", 1)[0]  # fall back to the country entry
        if ref in entries:
            level = entries[ref]["level"]
            for exc in entries[ref].get("exceptions", []):
                if exc["when"] in expanded:
                    level = exc["level"]
            total += POINTS.get(level, 0)
    return total >= threshold


def random_spec(rng: random.Random) -> dict:
    keys = [
        "role.ml_engineering",
        "role.backend_engineering",
        "company_type.big_tech",
        "work_mode.onsite",
        "work_mode.remote",
        "location.china",
        "location.china.shanghai",
        "tech_stack.go",
    ]
    spec: dict = {}
    for ref in rng.sample(keys, rng.randint(1, len(keys))):
        dimension, key = ref.split(".", 1)
        level = rng.choice(["strong_avoid", "avoid", "prefer", "strong_prefer", "exclude", "require"])
        if ref == "location.china.shanghai" and spec.get("location", {}).get("china", {}).get("level") == "exclude":
            level = "exclude"
        spec.setdefault(dimension, {})[key] = {"level": level}
    if "location" in spec and spec["location"].get("china", {}).get("level") == "exclude":
        for key in list(spec["location"]):
            spec["location"][key] = {"level": "exclude"}
    big = spec.get("company_type", {}).get("big_tech")
    if big and big["level"] in POINTS and rng.random() < 0.5:
        other = "strong_prefer" if big["level"] != "strong_prefer" else "avoid"
        big["exceptions"] = [{"when": "work_mode.remote", "level": other}]
    return spec


TAG_POOL = [
    "role.ml_engineering",
    "role.backend_engineering",
    "company_type.big_tech",
    "work_mode.onsite",
    "work_mode.remote",
    "location.china.shanghai",
    "location.china.beijing",
    "location.us.seattle",
    "tech_stack.go",
]


def test_replay_matches_independent_recomputation(tmp_path):
    rng = random.Random(7)
    for trial in range(40):
        (tmp_path / str(trial)).mkdir()
        world = World(tmp_path / str(trial))
        labels = []
        for _ in range(12):
            tags = rng.sample(TAG_POOL, rng.randint(1, 4))
            labels.append((world.history(tags, rng.choice(["want", "reject"])), tags))
        spec = random_spec(rng)
        intent = IntentModel.build(spec)
        result = evaluate(intent, world.store, world.settings)
        expected = {
            label.id for label, tags in labels if independent_recommended(spec, tags) == (label.value == "want")
        }
        assert result.agree == expected, (spec, trial)


# ---------------------------------------------------------------------------
# Multi-day simulation from initialization (spec §9).


def test_multi_day_simulation_keeps_all_guarantees(tmp_path):
    rng = random.Random(11)
    truth = {
        "role.ml_engineering": 2,
        "role.backend_engineering": -1,
        "company_type.big_tech": 1,
        "work_mode.onsite": -2,
        "location.china.beijing": -2,
        "tech_stack.go": -1,
    }

    def user_wants(tags):
        return sum(truth.get(t, 0) for t in tags) >= 1

    world = World(tmp_path)
    world.init()
    chosen_exceptions = 0
    for day in range(12):
        inputs = []
        for slot in ["recommended"] * 7 + ["exploration"] * 3:
            tags = sorted(set(rng.sample(TAG_POOL, rng.randint(2, 4))))
            job = world.job(tags)
            value = "want" if user_wants(tags) else "reject"
            reasons = []
            if rng.random() < 0.3:
                strongest = max(tags, key=lambda t: abs(truth.get(t, 0)))
                if truth.get(strongest, 0) != 0 and (truth[strongest] > 0) == (value == "want"):
                    reasons = [strongest]
            inputs.append(LabelInput(job=job, value=value, slot=slot, reason_keys=reasons))
        report = world.day(f"2026-11-{day + 1:02d}", *inputs)
        for analysis in report.analyses:
            analysis = world.engine.refresh(analysis.id)  # an earlier choice today may have resolved it
            if analysis.status != "open":
                continue
            if analysis.step1 != "skip" and analysis.causes:
                analysis = world.engine.choose_cause(analysis.id, analysis.causes[0].id)
            if analysis.proposals and rng.random() < 0.8:
                proposal = analysis.proposals[0]
                if any(c.summary()[0] == "add_exception" for c in proposal.changes):
                    chosen_exceptions += 1
                world.engine.decide(analysis.id, proposal.id)
            else:
                world.engine.decline(analysis.id)

    versions = world.store.versions()
    decisions = {d["id"]: d for d in world.store.decisions()}
    # §9.1 every change has a recorded user choice
    assert all(decisions[v["decision_id"]]["kind"] == "accept" for v in versions[1:])
    accepted = [d for d in decisions.values() if d["kind"] == "accept"]
    assert len(accepted) == len(versions) - 1
    # §9.2 one entry per dimension.key at every version (also holds structurally after re-validation)
    for version in versions:
        intent = IntentModel.model_validate_json(version["data"])
        refs = [ref for ref, _ in intent.iter_entries()]
        assert len(refs) == len(set(refs))
    # §9.3 exceptions only exist if the user chose a proposal adding one
    final = world.store.current_intent()
    exceptions = sum(len(entry.exceptions) for _, entry in final.iter_entries())
    assert exceptions <= chosen_exceptions
    # §9.4 replay equals independent recomputation on the final intent
    spec = {
        d: {k: e.model_dump(mode="json", include={"level", "exceptions"}) for k, e in keys.items()}
        for d, keys in final.entries.items()
    }
    result = evaluate(final, world.store, world.settings)
    expected = {
        label.id
        for label in world.store.labels()
        if label.value in {"want", "reject"}
        and not label.superseded
        and independent_recommended(spec, world.store.effective_tags(label.job_id)) == (label.value == "want")
    }
    assert result.agree == expected
    assert len(versions) > 1, "the simulation should have produced at least one accepted change"


def test_other_analyses_of_the_day_are_refreshed_after_a_choice(world):
    jobs = [world.job(["role.backend_engineering", "company_type.big_tech", "work_mode.hybrid"]) for _ in range(2)]
    report = world.day(
        DAY1,
        *(LabelInput(job=j, value="reject", slot="recommended", reason_keys=["company_type.big_tech"]) for j in jobs),
    )
    first, second = report.analyses
    stronger = next(p for p in first.proposals if p.changes[-1].summary()[2] == "strong_avoid")
    world.engine.decide(first.id, stronger.id)
    with pytest.raises(InvariantError):  # its options were built for the old version
        world.engine.decide(second.id, second.proposals[0].id)
    refreshed = world.engine.refresh(second.id)
    assert refreshed.status == "resolved"  # the same change already explains this reject


def test_job_tag_aliases_are_canonicalized(world):
    job = world.job(["company_type.大厂", "work_mode.onsite", "role.ml_engineering"])
    world.day(DAY1, LabelInput(job=job, value="reject", slot="exploration"))
    assert world.store.effective_tags(job.id) == ["company_type.big_tech", "work_mode.onsite", "role.ml_engineering"]
