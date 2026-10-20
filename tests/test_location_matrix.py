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


# --- the city position: one rule in every format ----------------------------------------------------

# (country, state name, code, a city of that state)
STATE_CITIES = [
    ("us", "California", "CA", "Santa Clara"),
    ("us", "Texas", "TX", "Austin"),
    ("us", "Georgia", "GA", "Atlanta"),
    ("us", "Washington", "WA", "Seattle"),
    ("us", "Virginia", "VA", "McLean"),
    ("canada", "Ontario", "ON", "Toronto"),
    ("canada", "British Columbia", "BC", "Vancouver"),
]
# Well-known cities named like a state or province, read as the city only beside that state or province.
NAMED_LIKE_A_STATE = [
    ("us", "New York", "NY", "New York"),
    ("us", "District of Columbia", "DC", "Washington"),
    ("canada", "Quebec", "QC", "Quebec"),
]
NAMES = {"us": ("US", "USA", "United States", "United States of America"), "canada": ("Canada",)}
FORMATS = {
    "state - city": "{s} - {c}",
    "country, state, city": "{k}, {s}, {c}",
    "country, city, state": "{k}, {c}, {s}",
    "city, state": "{c}, {s}",  # US only, as before
    "city, state, country": "{c}, {s}, {k}",
}


def forms(name: str, code: str) -> tuple[str, ...]:
    """A state as its name, its code and its code with dots."""
    return name, code, ".".join(code) + "."


def matrix(rows, city_of, expected) -> list[tuple[str, tuple]]:
    """Every format x every country name x every form of the state, with `city_of(row)` in the city position;
    `expected(row, format, form)` where form 0, 1, 2 is the state written as its name, code, dotted code."""
    cases = []
    for row, (fmt, text), state in itertools.product(rows, FORMATS.items(), range(3)):
        country, name, code, _ = row
        if fmt == "city, state" and country != "us":
            continue
        for k in NAMES[country]:
            location = text.format(k=k, s=forms(name, code)[state], c=city_of(row))
            cases.append((location, expected(row, fmt, state)))
    return cases


def test_a_city_of_the_state_is_read_in_every_format():
    cases = matrix(STATE_CITIES, lambda r: r[3], lambda r, *_: city(r[0], r[3].casefold().replace(" ", "_")))
    assert not wrong(cases)


def test_a_state_or_province_in_the_city_position_is_not_read_as_a_city():
    for written in (lambda r: r[1], lambda r: r[2], lambda r: ".".join(r[2]) + "."):  # its name, code, dotted code
        assert not wrong(matrix(STATE_CITIES, written, lambda *_: UNSURE))
    other = {"California": "Texas", "Texas": "California"}  # another state's name or code
    rows = [row for row in STATE_CITIES if row[1] in other]
    assert not wrong(matrix(rows, lambda r: other[r[1]], lambda *_: UNSURE))
    assert not wrong(matrix(rows, lambda r: {"Texas": "TX", "California": "CA"}[other[r[1]]], lambda *_: UNSURE))


def test_a_city_named_like_its_state_is_read_beside_that_state_only():
    named = lambda r, *_: city(r[0], r[3].casefold().replace(" ", "_"))  # noqa: E731
    assert not wrong(matrix(NAMED_LIKE_A_STATE, lambda r: r[3], named))

    # The state's code in the city position and its name, also the city's, in the state position: country first,
    # either part can be the state, so the name is the city ("US, New York, NY"); other formats keep their order.
    def swapped(r, fmt, form):
        return named(r) if fmt.startswith("country,") and form == 0 and r[1] == r[3] else UNSURE

    assert not wrong(matrix(NAMED_LIKE_A_STATE, lambda r: r[2], swapped))
    elsewhere = [  # beside another state, or where no state stands
        ("Washington - Washington", UNSURE),
        ("US, WA, Washington", UNSURE),
        ("Washington, WA", UNSURE),
        ("California - New York", UNSURE),
        ("District of Columbia, Washington, United States", UNSURE),
        ("US, New York", ({"location.us"}, LOCATION)),  # a country and its state
        ("USA - Washington", ({"location.us"}, LOCATION)),
    ]
    assert not wrong(elsewhere)
