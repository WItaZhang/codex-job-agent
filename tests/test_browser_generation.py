"""Synthetic held-tab migration: only the same proven instance may be adopted."""

import pytest

from intent_job_agent.tools import Toolbox


@pytest.mark.parametrize("held_generation", [None, "old-instance"])
def test_old_or_missing_generation_reopens_instead_of_adopting(held_generation):
    hold = {"tab_id": "same-id", "tab_url": "http://127.0.0.1/form", "generation": held_generation}
    tabs = [{"id": "same-id", "url": hold["tab_url"], "type": "page", "generation": "new-instance"}]
    assert Toolbox._held_tab(hold, tabs) == (None, 0)


def test_same_generation_preserves_unique_url_and_duplicate_protection():
    hold = {"tab_id": "old-id", "tab_url": "http://127.0.0.1/form", "generation": "same-instance"}
    tab = {"id": "new-id", "url": hold["tab_url"], "type": "page", "generation": "same-instance"}
    assert Toolbox._held_tab(hold, [tab]) == (tab, 1)
    assert Toolbox._held_tab(hold, [tab, {**tab, "id": "duplicate"}]) == (None, 2)
