"""Location reading as a matrix (spec section 10 item 31): every format, times every way a part is written, times
the spacing and dash variants employers use. A rule changed in one place shows up everywhere it applies. All
locations are SYNTHETIC combinations; none is copied from a posting."""

import itertools

from intent_job_agent.domain import Job, Vocabulary
from intent_job_agent.pretag import pretag

from .conftest import BASE_VOCAB

VOCAB = Vocabulary.build(BASE_VOCAB)
LOCATION, BOTH = {"location"}, {"location", "work_mode"}
UNSURE = (set(), set())


def read(location: str) -> tuple[set[str], set[str]]:
    result = pretag(Job(id="j", title="Engineer", location=location, tags=[]), VOCAB)
    tags = {tag for tag in result.tags if tag.startswith(("location.", "work_mode."))}
    return tags, set(result.confident & BOTH)


def wrong(cases) -> list[tuple[str, tuple, tuple]]:
    """(location, read, expected) for every case read otherwise than expected."""
    return [(location, got, want) for location, want in cases if (got := read(location)) != want]


def city(country: str, slug: str) -> tuple[set[str], set[str]]:
    return {f"location.{country}.{slug}"}, LOCATION


# --- spacing and dashes are made regular before anything is read --------------------------------------

SPACES = (" ", "  ", " ", "\t", " \t ")
DASHES = (" - ", " – ", " — ", " ‐ ", " − ", " -", "- ", "  -  ")
COUNTRIES = {"US": "us", "USA": "us", "U.S.": "us", "United States": "us", "United States of America": "us"}
PLACES = [  # (written with " " and " - ", expected)
    ("Santa Clara, CA", city("us", "santa_clara")),
    ("US, CA, Santa Clara", city("us", "santa_clara")),
    ("United States of America, McLean, Virginia", city("us", "mclean")),
    ("California - Palo Alto", city("us", "palo_alto")),
    ("Israel - Tel Aviv", city("israel", "tel_aviv")),
    ("Canada - Ontario", ({"location.canada"}, LOCATION)),
    ("Winston-Salem, NC", city("us", "winston_salem")),  # a hyphen inside a name parts nothing
]


def test_spaces_and_dashes_of_every_kind_read_the_same():
    cases = [
        (written.replace(" - ", dash).replace(" ", space), want)
        for (written, want), dash, space in itertools.product(PLACES, DASHES, SPACES)
    ]
    assert not wrong(cases)


# --- work mode words: every spelling is read and taken out, wherever the dash or space falls ------------

MODES = {
    "Remote": "remote", "WFH": "remote", "W.F.H.": "remote", "Work From Home": "remote", "Work  From Home": "remote",
    "Work From Home": "remote", "Work-From-Home": "remote", "work from home": "remote",
    "Remote-First": "remote", "Remote First": "remote", "Anywhere": "remote",
    "In Office": "onsite", "In  Office": "onsite", "In-Office": "onsite", "In Office": "onsite",
    "On-site": "onsite", "Onsite": "onsite", "On Site": "onsite", "Hybrid": "hybrid",
}  # fmt: skip
STATES = {"Texas": "us", "TX": "us", "California": "us", "CA": "us", "Ontario": "canada", "ON": "canada"}
JOINS = ("{k} - {m} - {s}", "{k} - {m} -{s}", "{k} - {m}-{s}", "{k} -{m}- {s}", "{k}-{m} - {s}", "{k} – {m} – {s}")


def test_country_work_mode_state_reads_the_country_and_the_mode():
    cases = []
    for (country, code), (mode, kind), (state, of), join in itertools.product(
        {**COUNTRIES, "Canada": "canada"}.items(), MODES.items(), STATES.items(), JOINS
    ):
        if code == of:
            want = ({f"location.{code}", f"work_mode.{kind}"}, BOTH)
            cases.append((join.format(k=country, m=mode, s=state), want))
    assert not wrong(cases)


def test_a_work_mode_word_beside_a_country_or_a_city_is_taken_out():
    cases = []
    for (mode, kind), (country, code) in itertools.product(MODES.items(), COUNTRIES.items()):
        cases += [
            (f"{country} - {mode}", ({f"location.{code}", f"work_mode.{kind}"}, BOTH)),
            (f"{mode} - {country}", ({f"location.{code}", f"work_mode.{kind}"}, BOTH)),
            (f"{country}, {mode}", ({f"location.{code}", f"work_mode.{kind}"}, BOTH)),
            (f"Austin, TX ({mode})", ({"location.us.austin", f"work_mode.{kind}"}, BOTH)),
            (f"{country} - {mode} - Austin", ({"location.us.austin", f"work_mode.{kind}"}, BOTH)),
        ]
    cases.append(("India, Work From Home", ({"location.india", "work_mode.remote"}, BOTH)))
    assert not wrong(cases)
