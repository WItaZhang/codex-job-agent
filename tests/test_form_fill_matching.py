"""Form-fill matching (specs/form-fill.md 4.4, slice-1 plan section 5): which answer-sheet entry each field read back
answers. The 29 confirmed samples, the pages of decisions 15 and 16, and the rules: asks before the built-in labels,
the longest phrase, ties, overrides, whole words, and record segments. All data here is SYNTHETIC."""

from pathlib import Path

import pytest
import yaml

from intent_job_agent.form_fill.inputs import load_answers
from intent_job_agent.form_fill.matching import match_page, match_question
from intent_job_agent.form_fill.models import ReadField

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/form_fill"
PROFILE_FIELDS = {"personal.full_name", "personal.email", "personal.phone", "personal.city", "personal.address"}
NAMES = {"name.first": {"answer": "Sam", "kind": "text"}, "name.last": {"answer": "Synthetic", "kind": "text"}}


def fixture(name: str) -> dict:
    data = yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))
    assert data["synthetic"] is True
    return data


def load(tmp_path: Path, entries: dict) -> dict:
    path = tmp_path / "answers.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "entries": {**NAMES, **entries}}, allow_unicode=True), "utf-8")
    return load_answers(path, PROFILE_FIELDS, {"relocation"})


def page(rows) -> tuple[ReadField, ...]:
    """[question, value, control] rows -> ReadFields p1.1, p1.2 ... ("review" rows are review-page text)."""
    return tuple(
        ReadField(
            id=f"p1.{n}",
            question=question,
            value=value,
            control=control,
            origin="review_text" if control == "review" else "control",
        )
        for n, (question, value, control) in enumerate(rows, start=1)
    )


WORK_AUTH = fixture("samples_work_auth.yaml")
PROFILE = fixture("samples_profile.yaml")
ERRORS = fixture("samples_fill_errors.yaml")


# --- the 29 confirmed samples -----------------------------------------------------------------------------------


@pytest.mark.parametrize("case", WORK_AUTH["cases"], ids=lambda c: c["question"][:50])
def test_work_auth_samples_map_to_their_entry(tmp_path, case):
    answers = load(tmp_path, WORK_AUTH["answers"])
    found = match_question(case["question"], answers)
    assert found.key == case["entry"] and not found.ambiguous


@pytest.mark.parametrize("case", [c for c in PROFILE["cases"] if "record" not in c], ids=lambda c: c["question"][:50])
def test_profile_samples_map_to_their_entry(tmp_path, case):
    answers = load(tmp_path, PROFILE["answers"])
    assert match_question(case["question"], answers).key == case["entry"]


RECORD_LABELS = {"school": "School", "degree": "Degree", "major": "Field of Study", "start": "From", "end": "To"}


@pytest.mark.parametrize("case", [c for c in PROFILE["cases"] if "record" in c], ids=lambda c: c["note"][:40])
def test_profile_record_samples_map_every_field_of_the_segment(tmp_path, case):
    answers = load(tmp_path, PROFILE["answers"])
    fields = page([(RECORD_LABELS[name], value, "review") for name, value in case["record"].items()])
    found = match_page(fields, answers)
    assert {m.key for m in found.fields.values()} == {case["entry"]}
    assert [found.fields[f.id].part for f in fields] == list(case["record"])
    assert found.missing == ()


def test_the_samples_cover_29_cases():
    assert len(WORK_AUTH["cases"]) + len(PROFILE["cases"]) == 29


# --- pages from decisions 15 and 16 ----------------------------------------------------------------------------


@pytest.mark.parametrize("sample", ERRORS["pages"], ids=lambda p: p["name"])
def test_fill_error_pages_map_each_field(tmp_path, sample):
    answers = load(tmp_path, ERRORS["answers"])
    found = match_page(page(sample["fields"]), answers)
    assert {fid: m.key for fid, m in found.fields.items()} == {fid: e["key"] for fid, e in sample["expect"].items()}
    assert list(found.missing) == sample["missing"]


def test_extra_segments_are_marked_and_keep_their_parts(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    sample = ERRORS["pages"][0]
    found = match_page(page(sample["fields"]), answers)
    extra = [fid for fid, m in found.fields.items() if m.extra]
    assert extra == [f"p1.{n}" for n in (*range(11, 16), *range(21, 25))]
    assert [found.fields[f"p1.{n}"].part for n in range(11, 16)] == ["title", "company", "range", "city", "other"]
    segments = [found.fields[f"p1.{n}"].segment for n in (6, 11, 16, 21, 25, 30)]
    assert segments == sorted(set(segments)), "six segments, in page order"


# --- rules ------------------------------------------------------------------------------------------------------


def test_the_longest_phrase_wins(tmp_path):
    answers = load(
        tmp_path,
        {
            "work_auth.sponsorship_now": {"answer": "No", "kind": "yes_no", "asks": ["require sponsorship"]},
            "work_auth.sponsorship_future": {"answer": "Yes", "kind": "yes_no", "asks": ["now or in the future"]},
        },
    )
    found = match_question("Will you now or in the future require sponsorship?", answers)
    assert found.key == "work_auth.sponsorship_future"


def test_equal_phrases_on_two_entries_are_ambiguous(tmp_path):
    answers = load(
        tmp_path,
        {
            "screening.relocate": {"answer": "Yes", "kind": "yes_no", "asks": ["relocate"]},
            "screening.overtime": {"answer": "No", "kind": "yes_no", "asks": ["overtime"]},
        },
    )
    found = match_question("Are you willing to relocate or work overtime?", answers)
    assert found.key is None
    assert set(found.ambiguous) == {"screening.relocate", "screening.overtime"}


def test_overrides_win_even_with_a_shorter_phrase(tmp_path):
    answers = load(tmp_path, WORK_AUTH["answers"])
    question = "Will you now or in the future require sponsorship? If you are on F-1/OPT/STEM OPT, please select Yes."
    assert match_question(question, answers).key == "work_auth.sponsorship_opt_named"


def test_asks_come_before_the_built_in_labels(tmp_path):
    answers = load(
        tmp_path,
        {
            "contact.email": {"from_profile": "personal.email"},
            "screening.email_updates": {"answer": "No", "kind": "yes_no", "asks": ["email"]},
        },
    )
    assert match_question("Email", answers).key == "screening.email_updates"


def test_phrases_match_whole_words_only(tmp_path):
    answers = load(tmp_path, {"screening.location_ok": {"answer": "Yes", "kind": "yes_no", "asks": ["location"]}})
    assert match_question("Are you open to relocation?", answers).key is None
    assert match_question("Is the location OK for you?", answers).key == "screening.location_ok"


@pytest.mark.parametrize(
    ("question", "key"),
    [
        ("First name", "name.first"),
        ("Legal Last Name", "name.last"),
        ("E-mail address", "contact.mail"),
        ("Mobile phone number (optional)", "contact.cell"),
        ("Location", "contact.town"),
        ("City", "contact.town"),
        ("Current location", "contact.town"),
        ("Preferred location", None),
        ("What city would you like to work in?", None),
        ("What is your first name and nickname?", None),
    ],
)
def test_built_in_labels_match_the_whole_question_and_follow_from_profile(tmp_path, question, key):
    answers = load(
        tmp_path,
        {
            "contact.mail": {"from_profile": "personal.email"},
            "contact.cell": {"from_profile": "personal.phone"},
            "contact.town": {"from_profile": "personal.city"},
        },
    )
    assert match_question(question, answers).key == key


def test_a_segment_finds_its_record_by_an_alias_inside_the_value(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    fields = page([["Company", "鲁门（Lumen）", "review"], ["Job Title", "ML Intern", "review"]])
    found = match_page(fields, answers)
    assert found.fields["p1.1"].key == "experience.lumen" and not found.fields["p1.1"].extra


def test_the_same_record_twice_makes_the_second_segment_extra(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    fields = page(
        [
            ["School", "Northern River University", "review"],
            ["Degree", "BS", "review"],
            ["School", "NRU", "review"],
            ["Degree", "BS", "review"],
        ]
    )
    found = match_page(fields, answers)
    assert [found.fields[f"p1.{n}"].extra for n in range(1, 5)] == [False, False, True, True]
    assert found.missing == ("education.ms_esu",)


def test_fields_outside_segments_are_matched_as_questions(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    fields = page(
        [
            ["City", "[city]", "text"],
            ["School", "ESU", "review"],
            ["City", "Springfield", "review"],
            ["Do you agree to let us contact you about job opportunities?", "Yes", "checkbox"],
            ["City", "[city]", "text"],
        ]
    )
    found = match_page(fields, answers)
    assert [found.fields[f"p1.{n}"].key for n in range(1, 6)] == [
        "contact.city",
        "education.ms_esu",
        "education.ms_esu",
        "screening.contact_consent",
        "contact.city",
    ]
    assert found.fields["p1.3"].part == "city" and found.fields["p1.5"].part is None


def test_a_page_without_segments_misses_no_record(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    found = match_page(page([["Email", "[email]", "text"]]), answers)
    assert found.missing == ()


# --- review round 1 (decision 19) -------------------------------------------------------------------------------


def test_asks_win_over_record_labels(tmp_path):
    answers = load(
        tmp_path,
        {
            **ERRORS["answers"],
            "availability.start": {"answer": "2026-12-14", "kind": "date", "asks": ["start date"]},
            "screening.degree_level": {"answer": "Master's", "kind": "choice", "asks": ["degree"]},
        },
    )
    fields = page(
        [
            ["School", "ESU", "review"],
            ["Graduation Date", "12/2026", "review"],
            ["Start Date", "12/14/2026", "review"],
            ["First name", "Sam", "review"],
            ["Degree", "Master's", "review"],
        ]
    )
    found = match_page(fields, answers)
    assert found.fields["p1.3"].key == "availability.start" and found.fields["p1.3"].segment is None
    assert found.fields["p1.5"].key == "screening.degree_level" and not found.fields["p1.5"].extra
    assert found.missing == ("education.beng_nru",)


def test_one_unrelated_field_inside_a_segment_keeps_it_open(tmp_path):
    answers = load(tmp_path, {**ERRORS["answers"], "education.gpa": {"answer": "3.6", "kind": "text", "asks": ["gpa"]}})
    fields = page(
        [
            ["School or University", "Example State University", "review"],
            ["Degree", "MS", "review"],
            ["Overall Result (GPA)", "3.6", "review"],
            ["From", "2025", "review"],
            ["To (Actual or Expected)", "2030", "review"],
            ["Are you willing to travel?", "Yes", "review"],
            ["Do you agree to let us contact you about job opportunities?", "Yes", "review"],
            ["City", "[city]", "review"],
        ]
    )
    found = match_page(fields, answers)
    assert found.fields["p1.3"].key == "education.gpa" and found.fields["p1.3"].segment is None
    assert [found.fields[f"p1.{n}"].part for n in (4, 5)] == ["start", "end"]
    assert found.fields["p1.8"].segment is None and found.fields["p1.8"].key == "contact.city", "two fields end it"


def test_record_labels_keep_their_meaning_with_an_optional_suffix(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    fields = page(
        [["Company", "Lumen", "review"], ["City (optional)", "Hangzhou", "review"], ["To", "08/2024", "review"]]
    )
    found = match_page(fields, answers)
    assert [found.fields[f"p1.{n}"].part for n in (1, 2, 3)] == ["company", "city", "end"]


def test_an_exact_name_later_on_the_page_beats_an_earlier_partial_one(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    fields = page(
        [
            ["Your title", "Volunteer Tutor", "review"],
            ["Company", "Lumen Labs Foundation", "review"],
            ["Your title", "Machine Learning Engineer Intern", "review"],
            ["Company", "Lumen Search (China) Co., Ltd.", "review"],
        ]
    )
    found = match_page(fields, answers)
    assert found.fields["p1.1"].extra and found.fields["p1.3"].key == "experience.lumen"


def test_two_records_at_one_school_are_told_apart_by_the_degree(tmp_path):
    two = {
        "education.ms_nru": {**ERRORS["answers"]["education.beng_nru"], "degree": "Master of Science"},
        "education.beng_nru": ERRORS["answers"]["education.beng_nru"],
    }
    two["education.ms_nru"] = {k: v for k, v in two["education.ms_nru"].items() if k not in ("accept", "otherwise")}
    answers = load(tmp_path, two)
    fields = page(
        [
            ["School", "Northern River University", "review"],
            ["Degree", "Master of Science", "review"],
            ["School", "Northern River University", "review"],
            ["Degree", "Bachelor of Engineering", "review"],
        ]
    )
    found = match_page(fields, answers)
    assert [found.fields[f"p1.{n}"].key for n in (1, 3)] == ["education.ms_nru", "education.beng_nru"]
    assert found.missing == ()


def test_records_that_stay_tied_are_ambiguous_but_not_missing(tmp_path):
    twin = {k: v for k, v in ERRORS["answers"]["education.beng_nru"].items() if k not in ("accept", "otherwise")}
    answers = load(tmp_path, {"education.a": twin, "education.b": {**twin, "start": "2019-09"}})
    found = match_page(page([["School", "Northern River University", "review"]]), answers)
    assert set(found.fields["p1.1"].ambiguous) == {"education.a", "education.b"} and found.missing == ()


def test_a_segment_with_its_school_left_empty_is_matched_by_its_other_fields(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    fields = page(
        [
            ["School or University", "No Response", "review"],
            ["Degree", "Master of Science", "review"],
            ["Field of Study", "Statistics", "review"],
        ]
    )
    found = match_page(fields, answers)
    assert found.fields["p1.1"].key == "education.ms_esu" and not found.fields["p1.1"].extra


def test_a_school_left_empty_with_nothing_else_matching_is_neither_extra_nor_matched(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    found = match_page(page([["School", "", "review"], ["Degree", "Doctor of Music", "review"]]), answers)
    assert found.fields["p1.2"].unanchored and not found.fields["p1.2"].extra and found.fields["p1.2"].key is None


def test_the_schools_otherwise_finds_the_segment(tmp_path):
    entry = {**ERRORS["answers"]["education.ms_esu"], "otherwise": {"school": "Other"}}
    answers = load(tmp_path, {"education.ms_esu": entry})
    found = match_page(page([["School", "Other", "review"], ["Degree", "MS", "review"]]), answers)
    assert found.fields["p1.1"].key == "education.ms_esu" and found.missing == ()


# --- review round 2 (decision 20) -------------------------------------------------------------------------------


def test_a_tie_waits_for_the_unique_matches_on_the_page(tmp_path):
    bs = {k: v for k, v in ERRORS["answers"]["education.beng_nru"].items() if k not in ("accept", "otherwise")}
    ms = {**bs, "degree": "Master of Science"}
    answers = load(tmp_path, {"education.bs": bs, "education.ms": ms})
    fields = page(
        [
            ["School", "Northern River University", "review"],
            ["Field of Study", "Geographic Information System", "review"],
            ["From", "2019", "review"],
            ["School", "Northern River University", "review"],
            ["Degree", "Master of Science", "review"],
        ]
    )
    found = match_page(fields, answers)
    assert [found.fields[f"p1.{n}"].key for n in (1, 4)] == ["education.bs", "education.ms"]


def test_a_tie_with_both_records_taken_is_an_extra_segment(tmp_path):
    bs = {k: v for k, v in ERRORS["answers"]["education.beng_nru"].items() if k not in ("accept", "otherwise")}
    answers = load(tmp_path, {"education.bs": bs, "education.ms": {**bs, "degree": "Master of Science"}})
    fields = page(
        [
            ["School", "Northern River University", "review"],
            ["School", "Northern River University", "review"],
            ["Degree", "Bachelor of Engineering", "review"],
            ["School", "Northern River University", "review"],
            ["Degree", "Master of Science", "review"],
        ]
    )
    found = match_page(fields, answers)
    assert found.fields["p1.1"].extra and [found.fields[f"p1.{n}"].key for n in (2, 4)] == [
        "education.bs",
        "education.ms",
    ]


@pytest.mark.parametrize(
    "value",
    ["No Selection", "No answer", "No option selected", "Nothing selected", "-- Make a Selection --", "Choose..."],
)
def test_more_placeholder_spellings_are_empty(value):
    from intent_job_agent.form_fill.matching import is_empty

    assert is_empty(value)


@pytest.mark.parametrize("value", ["No", "None of the above", "No preference", "Not applicable"])
def test_real_answers_are_not_placeholders(value):
    from intent_job_agent.form_fill.matching import is_empty

    assert not is_empty(value)


# --- review round 3 (decision 21) -------------------------------------------------------------------------------


def test_dates_listed_before_the_company_join_that_companys_segment(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    fields = page(
        [
            ["From", "06/2026", "review"],
            ["To", "09/2026", "review"],
            ["Employer Name", "Vista Video Inc.", "review"],
            ["Job Title", "Software Engineer Intern", "review"],
            ["From", "06/2024", "review"],
            ["To", "08/2024", "review"],
            ["Employer Name", "Lumen Search (China) Co., Ltd.", "review"],
            ["Job Title", "Machine Learning Engineer Intern", "review"],
        ]
    )
    found = match_page(fields, answers)
    assert [found.fields[f"p1.{n}"].key for n in range(1, 9)] == ["experience.vista"] * 4 + ["experience.lumen"] * 4


def test_a_city_alone_before_a_school_stays_a_question(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    found = match_page(page([["City", "[city]", "text"], ["School", "ESU", "review"]]), answers)
    assert found.fields["p1.1"].key == "contact.city" and found.fields["p1.1"].segment is None


# --- review round 4 (decision 22) -------------------------------------------------------------------------------


def test_a_second_end_date_with_the_same_value_stays_in_its_segment(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    fields = page(
        [
            ["School", "ESU", "review"],
            ["End Date", "12/2026", "review"],
            ["Graduation Date", "12/2026", "review"],
            ["School", "Northern River University", "review"],
            ["End Date", "06/2025", "review"],
        ]
    )
    found = match_page(fields, answers)
    assert [found.fields[f"p1.{n}"].key for n in range(1, 6)] == ["education.ms_esu"] * 3 + ["education.beng_nru"] * 2


def test_a_date_after_unrelated_fields_goes_back_to_its_segment(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    fields = page(
        [
            ["School", "ESU", "review"],
            ["Degree", "MS", "review"],
            ["GPA", "3.9", "review"],
            ["GPA scale", "4.0", "review"],
            ["End date", "12/2026", "review"],
            ["School", "Northern River University", "review"],
            ["Degree", "BS", "review"],
            ["GPA", "3.8", "review"],
            ["GPA scale", "4.0", "review"],
            ["End date", "06/2025", "review"],
        ]
    )
    found = match_page(fields, answers)
    assert found.fields["p1.5"].key == "education.ms_esu" and found.fields["p1.10"].key == "education.beng_nru"


# --- decision 23: a phrase with a few words inside ---------------------------------------------------------------

YEARS = {
    "screening.years_experience": {"answer": "0", "kind": "choice", "asks": ["years of experience"]},
    "location.onsite_5_days": {"answer": "Yes", "kind": "yes_no", "asks": ["come into the office"]},
}


@pytest.mark.parametrize(
    "question",
    [
        "How many years of Engineering experience do you currently have?",
        "How many years of professional software experience do you have?",
    ],
)
def test_a_phrase_with_up_to_two_words_inside_still_matches(tmp_path, question):
    assert match_question(question, load(tmp_path, YEARS)).key == "screening.years_experience"


def test_three_words_inside_do_not_match(tmp_path):
    question = "How many years of full time paid engineering experience do you have?"
    assert match_question(question, load(tmp_path, YEARS)).key is None


def test_a_loose_match_never_beats_an_exact_one(tmp_path):
    ml = {"screening.ml_years": {"answer": "1", "kind": "choice", "asks": ["years of ml engineering experience"]}}
    answers = load(tmp_path, {**YEARS, **ml})
    assert match_question("How many years of ML engineering experience?", answers).key == "screening.ml_years"


def test_a_negation_inside_is_never_a_filler(tmp_path):
    answers = load(
        tmp_path, {"work_auth.now": {"answer": "No", "kind": "yes_no", "asks": ["currently require sponsorship"]}}
    )
    assert match_question("Do you currently not require any sponsorship?", answers).key is None
    assert match_question("Do you currently don't require sponsorship?", answers).key is None


def test_a_loose_match_comes_after_the_built_in_labels(tmp_path):
    answers = load(tmp_path, {"contact.email": {"from_profile": "personal.email"}, **YEARS})
    assert match_question("Email", answers).key == "contact.email"
