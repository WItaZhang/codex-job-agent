"""Domain invariants: closed dimensions, one entry per key, exception and location rules."""

import pytest

from intent_job_agent.changes import AddEntry, AddException, SetLevel, apply_changes
from intent_job_agent.domain import (
    DIMENSIONS,
    ExceptionRule,
    IntentEntry,
    IntentModel,
    InvariantError,
    Level,
    Vocabulary,
    parse_ref,
)

from .conftest import BASE_INTENT, BASE_VOCAB

VOCAB = Vocabulary.build(BASE_VOCAB)


def test_dimensions_are_the_fixed_eleven():
    assert DIMENSIONS == (
        "role",
        "domain",
        "specialty",
        "seniority",
        "company_type",
        "company",
        "location",
        "work_mode",
        "employment_type",
        "tech_stack",
        "sponsorship",
    )


@pytest.mark.parametrize("bad", ["salary.high", "role", "role.", "role.a.b", "location.a.b.c", "Role.ml"])
def test_refs_outside_the_schema_are_rejected(bad):
    with pytest.raises(InvariantError):
        parse_ref(bad)


def test_location_ref_has_country_parent():
    ref = parse_ref("location.us.seattle")
    assert (ref.dimension, ref.key, ref.parent) == ("location", "us.seattle", "location.us")
    assert parse_ref("location.us").parent is None


def test_unknown_dimension_cannot_enter_the_intent():
    with pytest.raises(InvariantError):
        IntentModel.build({"outsourcing": {"yes": {"level": "avoid"}}})


def test_one_entry_per_key_adding_an_existing_key_is_rejected():
    intent = IntentModel.build(BASE_INTENT)
    with pytest.raises(InvariantError):
        apply_changes(intent, [AddEntry(ref="company_type.big_tech", level=Level.strong_avoid)], VOCAB)


def test_alias_of_existing_key_cannot_create_a_parallel_entry():
    intent = IntentModel.build(BASE_INTENT)
    # "大厂" is an alias of company_type.big_tech; a parallel entry would be a patch.
    with pytest.raises(InvariantError):
        apply_changes(intent, [AddEntry(ref="company_type.大厂", level=Level.avoid)], VOCAB)


def test_set_level_must_state_the_current_level():
    intent = IntentModel.build(BASE_INTENT)
    with pytest.raises(InvariantError):
        apply_changes(
            intent, [SetLevel(ref="company_type.big_tech", from_level=Level.prefer, to_level=Level.avoid)], VOCAB
        )


def test_exception_level_cannot_equal_default_level():
    with pytest.raises(InvariantError):
        IntentEntry.create(
            "company_type.big_tech",
            Level.prefer,
            exceptions=[ExceptionRule(when="work_mode.remote", level=Level.prefer)],
        )


def test_exception_needs_one_known_condition_on_another_key():
    intent = IntentModel.build(BASE_INTENT)
    ok = apply_changes(
        intent, [AddException(ref="company_type.big_tech", when="work_mode.remote", level=Level.prefer)], VOCAB
    )
    assert ok.entry("company_type.big_tech").exceptions[0].when == "work_mode.remote"
    with pytest.raises(InvariantError):  # condition must be an existing vocabulary key
        apply_changes(
            intent, [AddException(ref="company_type.big_tech", when="work_mode.moon", level=Level.prefer)], VOCAB
        )
    with pytest.raises(InvariantError):  # condition must be another key
        apply_changes(
            intent, [AddException(ref="company_type.big_tech", when="company_type.big_tech", level=Level.prefer)], VOCAB
        )


def test_exceptions_only_on_soft_entries():
    with pytest.raises(InvariantError):
        IntentEntry.create(
            "seniority.senior", Level.exclude, exceptions=[ExceptionRule(when="work_mode.remote", level=Level.avoid)]
        )


def test_excluded_country_rejects_non_excluded_cities():
    with pytest.raises(InvariantError):
        IntentModel.build({"location": {"china": {"level": "exclude"}, "china.shanghai": {"level": "prefer"}}})
    # Soft levels may override the country.
    IntentModel.build({"location": {"china": {"level": "avoid"}, "china.shanghai": {"level": "prefer"}}})
    # Narrowing under a required country is fine.
    IntentModel.build({"location": {"us": {"level": "require"}, "us.seattle": {"level": "exclude"}}})


def test_intent_round_trips_through_json():
    intent = IntentModel.build(BASE_INTENT)
    assert IntentModel.model_validate_json(intent.model_dump_json()) == intent
