"""Page identity, field numbers, stuck pages and the background reads (specs/form-fill.md 4.2, 4.3, 5.3; slice-2a plan).

Pure logic over snapshots: one read of one tab, as readback.snapshot makes it. The state is plain JSON (state.json),
so the runner numbers the read made after the filling agent quit the same way the checkpoint service did.

- A page is the same page when the address (without query and fragment) is the same and one read's questions all
  appear in the other (a follow-up appeared or went away), or when the title is the same and the two reads share a
  question. The same title with no question in common (two or more on each side) is another page.
- A field keeps its number for good: by its identity (origin, section, question, occurrence), else by its question
  among the numbers not yet used in this read (a new heading above it changed its section), else the page's next
  number, with `appeared_after` naming the nearest field above it that was numbered before.
"""

from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .matching import is_empty, norm
from .models import ReadField

STAGES = ("blank", "filled", "auto", "final")
STUCK_AFTER = 3


@dataclass(frozen=True)
class Snapshot:
    """One read of one tab: its address, its title (the current step, or every heading), the fields in page order
    (ids still provisional) and whether a password field was shown."""

    tab: str
    url: str
    marker: tuple[str, ...]
    fields: tuple[ReadField, ...]
    read_at: str = ""
    password: bool = False


@dataclass(frozen=True)
class Observation:
    """A snapshot placed on a page: the page number, the fields numbered p<page>.<n>, whether the page is new,
    whether the page has had too many filled checkpoints (stop), and, for a background read, whether it replaces
    the page's last one (a read of a page being taken down does not)."""

    page: int
    fields: tuple[ReadField, ...]
    new_page: bool
    title: str = ""
    tab: str = ""
    url: str = ""
    read_at: str = ""
    stop: bool = False
    keep: bool = True


def url_key(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.lower()}{parts.path.rstrip('/')}"


def _question(f: ReadField) -> tuple[str, str]:
    return (f.origin, norm(f.question))


def _identity(f: ReadField) -> tuple[str, str, str, int]:
    return (f.origin, norm(f.section), norm(f.question), f.occurrence)


def _within(a: Counter, b: Counter) -> bool:
    return all(b[key] >= n for key, n in a.items())


def _disjoint(a: Counter, b: Counter) -> bool:
    return sum(a.values()) >= 2 and sum(b.values()) >= 2 and not (a & b)


@dataclass
class _Number:
    n: int
    after: str | None
    question: tuple[str, str]


@dataclass
class _Page:
    no: int
    url: str
    title: str
    markers: list[tuple[str, ...]] = field(default_factory=list)
    questions: Counter = field(default_factory=Counter)  # of the last read kept
    numbers: dict[tuple, _Number] = field(default_factory=dict)
    next: int = 1
    auto: Counter | None = None  # questions of the last background read kept


class Recorder:
    """The pages of one run, their field numbers, each tab's current page and its run of filled checkpoints."""

    def __init__(self, stuck_after: int = STUCK_AFTER):
        self.stuck_after = stuck_after
        self._pages: list[_Page] = []
        self._current: dict[str, int] = {}
        self._streak: dict[str, list[int]] = {}  # tab -> [page, filled checkpoints in a row there]

    @property
    def pages(self) -> int:
        return len(self._pages)

    def title(self, page: int) -> str:
        return self._pages[page - 1].title

    def observe(self, snap: Snapshot, stage: str) -> Observation | None:
        """Place a read on its page and number its fields. None when it shows no field (a page loading, a job
        description)."""
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        if not snap.fields:
            return None
        questions = Counter(_question(f) for f in snap.fields)
        found = self._find(snap, questions)
        new = found is None
        page = self._new_page(snap) if new else self._pages[found - 1]
        if snap.marker and snap.marker not in page.markers:
            page.markers.append(snap.marker)
        if not page.title and snap.marker:
            page.title = " / ".join(snap.marker)
        keep = True
        if stage == "auto":
            keep = page.auto is None or not (_within(questions, page.auto) and questions != page.auto)
            if keep:
                page.auto = questions
        if keep:
            page.questions = questions
        fields = self._number(page, snap.fields)
        self._current[snap.tab] = page.no
        streak = self._streak.setdefault(snap.tab, [page.no, 0])
        if streak[0] != page.no:
            streak[:] = [page.no, 0]
        if stage == "filled":
            streak[1] += 1
        return Observation(
            page=page.no,
            fields=fields,
            new_page=new,
            title=page.title,
            tab=snap.tab,
            url=snap.url,
            read_at=snap.read_at,
            stop=stage == "filled" and streak[1] >= self.stuck_after,
            keep=keep,
        )

    def peek(self, snap: Snapshot) -> int | None:
        """The page a read would be placed on, without placing it (the next number for a new page)."""
        if not snap.fields:
            return None
        found = self._find(snap, Counter(_question(f) for f in snap.fields))
        return found if found is not None else len(self._pages) + 1

    def _find(self, snap: Snapshot, questions: Counter) -> int | None:
        key = url_key(snap.url)
        current = self._current.get(snap.tab)
        if current is not None and self._same(self._pages[current - 1], key, snap.marker, questions):
            return current
        for page in reversed(self._pages):
            if page.no != current and self._same(page, key, snap.marker, questions):
                return page.no
        return None

    @staticmethod
    def _same(page: _Page, key: str, marker: tuple[str, ...], questions: Counter) -> bool:
        if page.url != key:
            return False
        if _within(page.questions, questions) or _within(questions, page.questions):
            return True
        return bool(marker) and marker in page.markers and not _disjoint(page.questions, questions)

    def _new_page(self, snap: Snapshot) -> _Page:
        page = _Page(no=len(self._pages) + 1, url=url_key(snap.url), title=" / ".join(snap.marker))
        self._pages.append(page)
        return page

    def _number(self, page: _Page, fields: tuple[ReadField, ...]) -> tuple[ReadField, ...]:
        numbered_before = bool(page.numbers)
        used: set[int] = set()
        picked: list[_Number | None] = []
        for f in fields:  # by identity
            entry = page.numbers.get(_identity(f))
            if entry is not None and entry.n not in used:
                used.add(entry.n)
                picked.append(entry)
            else:
                picked.append(None)
        old = [entry is not None for entry in picked]
        for i, f in enumerate(fields):  # by question, among the numbers this read has not used
            if picked[i] is not None:
                continue
            spare = sorted(
                (e for e in page.numbers.values() if e.question == _question(f) and e.n not in used),
                key=lambda e: e.n,
            )
            if spare:
                picked[i], old[i] = spare[0], True
                used.add(spare[0].n)
                page.numbers[_identity(f)] = spare[0]
        out = []
        for i, f in enumerate(fields):
            entry = picked[i]
            if entry is None:
                above = next((j for j in range(i - 1, -1, -1) if old[j]), None)
                after = f"p{page.no}.{picked[above].n}" if numbered_before and above is not None else None
                entry = _Number(page.next, after, _question(f))
                page.next += 1
                page.numbers[_identity(f)] = entry
                picked[i] = entry
            out.append(f.model_copy(update={"id": f"p{page.no}.{entry.n}", "appeared_after": entry.after}))
        return tuple(out)

    # --- state.json ---------------------------------------------------------------------------------------------

    def to_json(self) -> dict:
        def counter(c: Counter | None):
            return None if c is None else [[*key, n] for key, n in c.items()]

        return {
            "pages": [
                {
                    "no": p.no,
                    "url": p.url,
                    "title": p.title,
                    "markers": [list(m) for m in p.markers],
                    "questions": counter(p.questions),
                    "auto": counter(p.auto),
                    "next": p.next,
                    "numbers": [[list(key), e.n, e.after, list(e.question)] for key, e in p.numbers.items()],
                }
                for p in self._pages
            ],
            "current": dict(self._current),
            "streak": {tab: list(s) for tab, s in self._streak.items()},
        }

    @classmethod
    def from_json(cls, data: dict, stuck_after: int = STUCK_AFTER) -> "Recorder":
        def counter(rows) -> Counter | None:
            return None if rows is None else Counter({(row[0], row[1]): row[2] for row in rows})

        rec = cls(stuck_after)
        for raw in data.get("pages", []):
            page = _Page(
                no=raw["no"],
                url=raw["url"],
                title=raw.get("title", ""),
                markers=[tuple(m) for m in raw.get("markers", [])],
                questions=counter(raw.get("questions")) or Counter(),
                auto=counter(raw.get("auto")),
                next=raw.get("next", 1),
            )
            shared: dict[int, _Number] = {}  # one entry per number, also under its other identities
            for key, n, after, question in raw.get("numbers", []):
                entry = shared.setdefault(n, _Number(n, after, tuple(question)))
                page.numbers[(key[0], key[1], key[2], key[3])] = entry
            rec._pages.append(page)
        rec._current = {tab: int(no) for tab, no in data.get("current", {}).items()}
        rec._streak = {tab: [int(s[0]), int(s[1])] for tab, s in data.get("streak", {}).items()}
        return rec


def prefilled_sources(blank: tuple[ReadField, ...] | None, filled: tuple[ReadField, ...]) -> dict[str, str]:
    """The source of every non-empty field (spec 5.3): page prefill when it had the same value at checkpoint
    ("blank"), "unrecorded" when the page has no blank read to tell, otherwise "undeclared" (the filling agent's own
    declarations come with slice 2c)."""
    before = None if blank is None else {f.id: f.value for f in blank}
    out = {}
    for f in filled:
        if is_empty(f.value):
            continue
        if before is None:
            out[f.id] = "unrecorded"
        elif not is_empty(before.get(f.id, "")) and before.get(f.id) == f.value:
            out[f.id] = "prefilled"
        else:
            out[f.id] = "undeclared"
    return out
