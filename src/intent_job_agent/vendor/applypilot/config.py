"""Replacement for ApplyPilot's `applypilot.config`, written for this project (not vendored).

The vendored discovery and enrichment modules use only `load_search_config`, `load_base_urls`,
`CONFIG_DIR` and `DB_PATH` from it. `intent_job_agent.applypilot_bridge.configure` points these at
the user's data directory before every run.
"""

import os
from pathlib import Path

import yaml

PACKAGE_DATA = Path(__file__).parent / "config_data"

# Set by configure(); the placeholders below are never used for real runs.
APP_DIR = Path("data/local/applypilot")
DB_PATH = APP_DIR / "applypilot.db"
CONFIG_DIR = APP_DIR / "config"
SEARCH_CONFIG_PATH = Path("data/local/searches.yaml")

DEFAULTS = {"min_score": 7, "max_apply_attempts": 3, "max_tailor_attempts": 5, "poll_interval": 60}


def normalize_search_config(raw: dict) -> dict:
    """ApplyPilot's code reads `sites`, `location_accept` and `location_reject_non_remote`, while its
    documented example uses `boards` and `location.accept_patterns` / `reject_patterns`. Accept both.

    ApplyPilot's location filter rejects every non-remote job when the accept list is empty; here an
    empty list means "no location filter", expressed as a pattern that matches every location.
    """
    cfg = dict(raw or {})
    location = cfg.get("location") if isinstance(cfg.get("location"), dict) else {}
    if not cfg.get("sites") and cfg.get("boards"):
        cfg["sites"] = list(cfg["boards"])
    accept = cfg.get("location_accept") or location.get("accept_patterns") or []
    cfg["location_accept"] = list(accept) or [""]
    cfg["location_reject_non_remote"] = list(
        cfg.get("location_reject_non_remote") or location.get("reject_patterns") or []
    )
    cfg["exclude_titles"] = list(cfg.get("exclude_titles") or [])
    return cfg


def load_search_config() -> dict:
    if not SEARCH_CONFIG_PATH.exists():
        return {}
    return normalize_search_config(yaml.safe_load(SEARCH_CONFIG_PATH.read_text(encoding="utf-8")))


def load_sites_config() -> dict:
    path = CONFIG_DIR / "sites.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def is_manual_ats(url: str | None) -> bool:
    if not url:
        return False
    return any(domain in url.lower() for domain in load_sites_config().get("manual_ats", []))


def load_blocked_sites() -> tuple[set[str], list[str]]:
    blocked = load_sites_config().get("blocked", {})
    return set(blocked.get("sites", [])), blocked.get("url_patterns", [])


def load_blocked_sso() -> list[str]:
    return load_sites_config().get("blocked_sso", [])


def load_base_urls() -> dict[str, str | None]:
    return load_sites_config().get("base_urls", {})


def load_env(path: Path) -> None:
    """Read KEY=VALUE lines (ApplyPilot's .env convention) without overriding variables already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
