"""The passive look at kept tabs while code waits for the user (spec 4.10 item 10, section 10 item 119; specs/open-
answers.md 4.6 item 2). One loop for both waits: the live re-run of the gate list waits at a gate in one tab, and the
watch of filled tabs waits for the applicant's final open answers in several.

It only lists the applying Chrome's tabs (metadata, no page connection) and hands each kept tab that is still there
to `look`, which may read it read-only. It never clicks, types, navigates, closes a tab or answers a dialog."""

from collections.abc import Callable, Hashable


def find_tab(targets: list[dict], tab: dict) -> dict | None:
    """A kept tab among the browser's targets: the same CDP id in the same Chrome (generation), or None."""
    return next((t for t in targets if t.get("id") == tab["id"] and t.get("generation") == tab.get("generation")), None)


def wait(
    browser,
    tabs: dict[Hashable, dict],
    *,
    since: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    poll_seconds: float,
    timeout_seconds: float,
    interrupt: Callable[[], object | None],
    look: Callable[[Hashable, dict], str | None],
) -> tuple[object, dict]:
    """Wait without touching any page. Each time round: `interrupt()` first (a stop, a reply: anything but None ends
    the wait at once), then the time limit counted from `since`, then each tab in `tabs` (key -> kept tab): one gone
    from the browser ends as "closed", one still there is passed to `look(key, current)`, whose answer (None: not
    yet) ends it. Ended tabs are removed from `tabs`. A tab list that cannot be read is tried again next time.
    Returns (the interrupt's answer, "timeout", or "ended" once every tab it started with has ended; {key: how it
    ended})."""
    ended: dict = {}
    watched = bool(tabs)
    while True:
        said = interrupt()
        if said is not None:
            return said, ended
        if clock() - since >= timeout_seconds:
            return "timeout", ended
        if tabs:
            try:
                listed = browser.targets()
            except Exception:  # noqa: BLE001 - no tab list this time: look again next time
                listed = None
            if listed is not None:
                for key, tab in list(tabs.items()):
                    current = find_tab(listed, tab)
                    how = "closed" if current is None else look(key, current)
                    if how is not None:
                        ended[key] = how
                        del tabs[key]
        if watched and not tabs:
            return "ended", ended
        sleep(poll_seconds)
