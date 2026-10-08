"""Deterministic scoring: levels, exceptions, location hierarchy, hard constraints, salary."""

from intent_job_agent.domain import Compensation, IntentModel, Salary
from intent_job_agent.scoring import is_recommended, score_job

from .conftest import BASE_INTENT, FIXTURES, settings

S = settings()


def score(intent_spec, tags, salary=None, compensation=None):
    intent = IntentModel.build(intent_spec, compensation=compensation)
    return score_job(intent, tags, salary, S)


def test_s09_same_dimension_keys_are_summed():
    case = next(c for c in FIXTURES["scenarios"] if c["id"] == "s09_multi_key_scoring")
    spec = {**BASE_INTENT, "tech_stack": {**BASE_INTENT["tech_stack"], **case["intent_overrides"]["tech_stack"]}}
    a, b, c = (score(spec, case["jobs"][name]).score for name in "ABC")
    assert c > a > b


def test_levels_use_configured_points():
    result = score(BASE_INTENT, ["role.ml_engineering", "company_type.big_tech", "work_mode.onsite"])
    assert result.score == 2 - 1 - 1
    assert not result.excluded


def test_exclude_and_require():
    assert score(BASE_INTENT, ["seniority.senior", "role.ml_engineering"]).excluded
    spec = {"work_mode": {"remote": {"level": "require"}}, "location": {"us": {"level": "require"}}}
    assert not score(spec, ["work_mode.remote", "location.us.seattle"]).excluded
    assert score(spec, ["work_mode.onsite", "location.us.seattle"]).excluded  # across dimensions: AND
    assert score(spec, ["work_mode.remote"]).excluded  # a required dimension without the key is excluded


def test_requires_in_one_dimension_are_or():
    spec = {"location": {"us": {"level": "require"}, "china.shanghai": {"level": "require"}}}
    assert not score(spec, ["location.china.shanghai"]).excluded
    assert not score(spec, ["location.us.seattle"]).excluded
    assert score(spec, ["location.china.beijing"]).excluded


def test_location_most_specific_entry_wins_and_is_counted_once():
    spec = {"location": {"china": {"level": "avoid"}, "china.shanghai": {"level": "prefer"}}}
    assert score(spec, ["location.china.shanghai"]).score == 1
    assert score(spec, ["location.china.beijing"]).score == -1


def test_excluded_country_excludes_its_cities():
    spec = {"location": {"china": {"level": "exclude"}}}
    assert score(spec, ["location.china.shanghai"]).excluded
    assert not score(spec, ["location.us.seattle"]).excluded


def test_exception_replaces_default_level_when_condition_holds():
    spec = {
        "company_type": {
            "big_tech": {"level": "avoid", "exceptions": [{"when": "work_mode.remote", "level": "prefer"}]}
        }
    }
    assert score(spec, ["company_type.big_tech", "work_mode.remote"]).score == 1
    assert score(spec, ["company_type.big_tech", "work_mode.onsite"]).score == -1


def test_salary_floor_is_a_soft_penalty():
    comp = Compensation(currency="CNY", period="month", floor=25000)
    low = Salary(currency="CNY", period="month", min=15000, max=20000)
    high = Salary(currency="CNY", period="month", min=20000, max=30000)
    base = ["role.ml_engineering"]
    assert score(BASE_INTENT, base, low, comp).score == 2 - 2
    assert not score(BASE_INTENT, base, low, comp).excluded
    assert score(BASE_INTENT, base, high, comp).score == 2
    assert score(BASE_INTENT, base, None, comp).score == 2  # unknown salary: no penalty
    assert score(BASE_INTENT, base, low, None).score == 2  # salary not considered


def test_recommendation_line():
    assert is_recommended(score(BASE_INTENT, ["role.backend_engineering"]), S)  # 1 >= θ
    assert not is_recommended(score(BASE_INTENT, ["company_type.big_tech"]), S)
    assert not is_recommended(score(BASE_INTENT, ["seniority.senior", "role.ml_engineering"]), S)
