"""Form-fill slice 2a: page identity, field numbering, stuck pages and the background reads (specs/form-fill.md 4.2,
4.3, 5.3; specs/form-fill.slice2a.plan.md). Pure logic on SYNTHETIC snapshots; no browser."""

import json

import pytest

from intent_job_agent.form_fill.models import ReadField
from intent_job_agent.form_fill.recorder import Recorder, Snapshot, prefilled_sources

URL = "https://jobs.example.test/acme/apply"


def f(question, value="", control="text", section="", occurrence=1, origin="control", required=False):
    return ReadField(
        id="p1.0",  # provisional; the recorder numbers fields
        question=question,
        origin=origin,
        control=control,
        value=value,
        section=section,
        occurrence=occurrence,
        required=required,
    )


def snap(*fields, marker=("My Information",), url=URL, tab="T1", at="2026-10-20T10:00:00"):
    return Snapshot(tab=tab, url=url, marker=tuple(marker), fields=tuple(fields), read_at=at)


INFO = (f("First Name"), f("Last Name"), f("Email"))
EXPERIENCE = (f("Job Title", section="Work Experience 1"), f("Company", section="Work Experience 1"))
VOLUNTARY = (f("Gender", control="select"), f("Veteran Status", control="select"))


def ids(obs):
    return [field.id for field in obs.fields]


# --- page identity ----------------------------------------------------------------------------------------------


def test_the_same_title_and_address_is_the_same_page():
    rec = Recorder()
    first = rec.observe(snap(*INFO), "blank")
    again = rec.observe(snap(*(field.model_copy(update={"value": "x"}) for field in INFO)), "filled")
    assert (first.page, again.page) == (1, 1)
    assert first.new_page and not again.new_page
    assert ids(first) == ids(again) == ["p1.1", "p1.2", "p1.3"]


def test_a_new_title_is_a_new_page_and_going_back_keeps_the_old_number():
    rec = Recorder()
    assert rec.observe(snap(*INFO), "blank").page == 1
    assert rec.observe(snap(*EXPERIENCE, marker=("My Experience",)), "blank").page == 2
    back = rec.observe(snap(*INFO), "auto")
    assert (back.page, back.new_page) == (1, False)
    assert ids(back) == ["p1.1", "p1.2", "p1.3"]


def test_a_follow_up_field_with_its_own_heading_does_not_change_the_page():
    rec = Recorder()
    rec.observe(snap(f("Do you need sponsorship?", control="radio"), f("Email")), "blank")
    grown = rec.observe(
        snap(
            f("Do you need sponsorship?", control="radio", value="Yes"),
            f("Which visa?", section="Visa details"),
            f("Email", section="Visa details"),  # the new heading is now the nearest one above Email too
            marker=("My Information", "Visa details"),
        ),
        "auto",
    )
    assert grown.page == 1
    assert ids(grown) == ["p1.1", "p1.3", "p1.2"]  # the follow-up takes the next number; the others keep theirs
    assert grown.fields[1].appeared_after == "p1.1"


def test_a_follow_up_that_goes_away_leaves_the_same_page():
    rec = Recorder()
    rec.observe(snap(f("Sponsorship?"), f("Which visa?"), f("Email")), "blank")
    assert rec.observe(snap(f("Sponsorship?"), f("Email")), "auto").page == 1


def test_the_same_title_with_completely_different_fields_is_a_new_page():
    rec = Recorder()
    rec.observe(snap(*INFO, marker=("Apply to Acme",)), "blank")
    second = rec.observe(snap(*VOLUNTARY, f("Disability", control="select"), marker=("Apply to Acme",)), "blank")
    assert second.page == 2


def test_without_titles_the_field_structure_tells_pages_apart():
    rec = Recorder()
    assert rec.observe(snap(*INFO, marker=()), "blank").page == 1
    assert rec.observe(snap(*INFO, f("Phone"), marker=()), "auto").page == 1  # a field appeared, the rest stayed
    assert rec.observe(snap(*VOLUNTARY, marker=()), "blank").page == 2
    assert rec.observe(snap(*INFO, f("Phone"), marker=()), "auto").page == 1


def test_a_review_page_repeating_the_questions_is_its_own_page():
    rec = Recorder()
    rec.observe(snap(*INFO, marker=("My Information",)), "blank")
    review = tuple(
        field.model_copy(update={"origin": "review_text", "control": "review", "value": "v"}) for field in INFO
    )
    assert rec.observe(snap(*review, marker=("Review",)), "filled").page == 2


def test_a_read_with_no_fields_is_no_page():
    rec = Recorder()
    assert rec.observe(snap(marker=("About the job",)), "auto") is None
    assert rec.observe(snap(*INFO), "blank").page == 1


def test_another_address_is_another_page():
    rec = Recorder()
    rec.observe(snap(*INFO), "blank")
    assert rec.observe(snap(*INFO, url="https://jobs.example.test/acme/apply/step-2"), "blank").page == 2


def test_the_query_and_fragment_do_not_change_the_page():
    rec = Recorder()
    rec.observe(snap(*INFO), "blank")
    assert rec.observe(snap(*INFO, url=URL + "?step=1#top"), "filled").page == 1


# --- numbering --------------------------------------------------------------------------------------------------


def test_two_education_segments_number_their_school_fields_apart():
    rec = Recorder()
    obs = rec.observe(
        snap(
            f("School", section="Education 1"),
            f("Degree", section="Education 1"),
            f("School", section="Education 2"),
            f("Degree", section="Education 2"),
        ),
        "blank",
    )
    assert ids(obs) == ["p1.1", "p1.2", "p1.3", "p1.4"]


def test_the_same_question_twice_in_one_section_is_told_apart_by_occurrence():
    rec = Recorder()
    obs = rec.observe(snap(f("Month", occurrence=1), f("Month", occurrence=2)), "blank")
    assert ids(obs) == ["p1.1", "p1.2"]


def test_a_field_inserted_in_the_middle_keeps_the_others_numbers():
    rec = Recorder()
    rec.observe(snap(f("A"), f("B"), f("C")), "blank")
    obs = rec.observe(snap(f("A"), f("A2"), f("B"), f("C")), "filled")
    assert ids(obs) == ["p1.1", "p1.4", "p1.2", "p1.3"]
    assert obs.fields[1].appeared_after == "p1.1"
    later = rec.observe(snap(f("A"), f("A2"), f("B"), f("C")), "auto")
    assert later.fields[1].appeared_after == "p1.1"  # kept with the number


def test_page_numbers_go_into_the_field_ids():
    rec = Recorder()
    rec.observe(snap(*INFO), "blank")
    obs = rec.observe(snap(*EXPERIENCE, marker=("My Experience",)), "blank")
    assert ids(obs) == ["p2.1", "p2.2"]


# --- stuck ------------------------------------------------------------------------------------------------------


def test_three_filled_checkpoints_on_one_page_say_stop():
    rec = Recorder(stuck_after=3)
    rec.observe(snap(*INFO), "blank")
    assert not rec.observe(snap(*INFO), "filled").stop
    assert not rec.observe(snap(*INFO), "auto").stop
    assert not rec.observe(snap(*INFO), "filled").stop
    assert rec.observe(snap(*INFO), "filled").stop


def test_moving_to_another_page_starts_the_count_again():
    rec = Recorder(stuck_after=3)
    rec.observe(snap(*INFO), "filled")
    rec.observe(snap(*INFO), "filled")
    rec.observe(snap(*EXPERIENCE, marker=("My Experience",)), "auto")  # the page moved on, then came back
    assert not rec.observe(snap(*INFO), "filled").stop
    assert not rec.observe(snap(*INFO), "filled").stop
    assert rec.observe(snap(*INFO), "filled").stop


# --- background reads -------------------------------------------------------------------------------------------


def test_a_read_of_a_page_being_torn_down_does_not_replace_the_last_one():
    rec = Recorder()
    rec.observe(snap(*INFO), "blank")
    assert rec.observe(snap(*INFO), "auto").keep
    assert not rec.observe(snap(INFO[0]), "auto").keep  # fewer fields, all of them already there
    assert rec.observe(snap(*INFO, f("Phone")), "auto").keep


def test_tabs_keep_their_own_current_page():
    rec = Recorder()
    rec.observe(snap(*INFO, tab="T1"), "blank")
    popup = rec.observe(snap(f("Password", control="text"), marker=("Sign in",), tab="T2"), "blank")
    assert popup.page == 2
    assert rec.observe(snap(*INFO, tab="T1"), "auto").page == 1


# --- state ------------------------------------------------------------------------------------------------------


def test_the_state_survives_json_and_numbering_goes_on():
    rec = Recorder()
    rec.observe(snap(f("A"), f("B")), "blank")
    rec.observe(snap(*EXPERIENCE, marker=("My Experience",)), "blank")
    again = Recorder.from_json(json.loads(json.dumps(rec.to_json())))
    obs = again.observe(
        snap(*EXPERIENCE, f("Start Date", section="Work Experience 1"), marker=("My Experience",)), "final"
    )
    assert ids(obs) == ["p2.1", "p2.2", "p2.3"]
    assert again.pages == 2
    assert again.title(2) == "My Experience"


# --- page prefill (spec 5.3) -----------------------------------------------------------------------------------


def test_a_value_present_at_blank_and_unchanged_is_page_prefill():
    blank = (
        ReadField(id="p1.1", question="Email", origin="control", control="text", value="[email]"),
        ReadField(id="p1.2", question="City", origin="control", control="text", value=""),
        ReadField(id="p1.3", question="School", origin="control", control="text", value="Old U"),
    )
    filled = (
        ReadField(id="p1.1", question="Email", origin="control", control="text", value="[email]"),
        ReadField(id="p1.2", question="City", origin="control", control="text", value="[city]"),
        ReadField(id="p1.3", question="School", origin="control", control="text", value="Example State University"),
        ReadField(id="p1.4", question="Why us?", origin="control", control="textarea", value="Because"),
    )
    assert prefilled_sources(blank, filled) == {
        "p1.1": "prefilled",
        "p1.2": "undeclared",
        "p1.3": "undeclared",
        "p1.4": "undeclared",
    }


def test_without_a_blank_read_filled_fields_have_no_recorded_source():
    filled = (
        ReadField(id="p1.1", question="Email", origin="control", control="text", value="[email]"),
        ReadField(id="p1.2", question="City", origin="control", control="text", value=""),
    )
    assert prefilled_sources(None, filled) == {"p1.1": "unrecorded"}


@pytest.mark.parametrize("stage", ["blank", "filled", "auto", "final"])
def test_every_stage_is_known(stage):
    assert Recorder().observe(snap(*INFO), stage).page == 1


def test_an_unknown_stage_is_refused():
    with pytest.raises(ValueError):
        Recorder().observe(snap(*INFO), "submitted")


def test_a_follow_up_that_went_away_does_not_freeze_the_background_read():
    """Review #5: once the shorter page shows twice in a row, it is the page, and its values are kept."""
    rec = Recorder()
    rec.observe(snap(f("Sponsorship?", "Yes"), f("Which visa?", "H-1B"), f("Years")), "blank")
    assert rec.observe(snap(f("Sponsorship?", "Yes"), f("Which visa?", "H-1B"), f("Years")), "auto").keep
    assert not rec.observe(snap(f("Sponsorship?", "No"), f("Years")), "auto").keep  # maybe the page going away
    later = rec.observe(snap(f("Sponsorship?", "No"), f("Years", "5")), "auto")
    assert later.keep and [x.value for x in later.fields] == ["No", "5"]
