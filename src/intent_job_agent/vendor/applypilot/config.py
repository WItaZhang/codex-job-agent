"""Replacement for ApplyPilot's `applypilot.config`, written for this project (not vendored).

The vendored discovery and enrichment modules use only `load_search_config`, `load_base_urls`,
`CONFIG_DIR` and `DB_PATH` from it; the vendored `apply/prompt.py` and `apply/chrome.py` also use the
profile, log, worker and Chrome helpers below. `intent_job_agent.applypilot_bridge.configure` and
`intent_job_agent.apply_bridge.configure` point these at the user's data directory before every run.
`get_chrome_path` and `get_chrome_user_data` are copied from ApplyPilot's config (AGPL-3.0-only).
"""

import json
import os
import platform
import shutil
from pathlib import Path

import yaml

PACKAGE_DATA = Path(__file__).parent / "config_data"

# Set by configure(); the placeholders below are never used for real runs.
APP_DIR = Path("data/local/applypilot")
DB_PATH = APP_DIR / "applypilot.db"
CONFIG_DIR = APP_DIR / "config"
SEARCH_CONFIG_PATH = Path("data/local/searches.yaml")

ENV_PATH = Path("data/local/.env")
PROFILE_PATH = Path("data/local/applicant/profile.json")
LOG_DIR = APP_DIR / "logs"
CHROME_WORKER_DIR = APP_DIR / "chrome-workers"
APPLY_WORKER_DIR = APP_DIR / "apply-workers"

DEFAULTS = {
    "min_score": 7,
    "max_apply_attempts": 3,
    "max_tailor_attempts": 5,
    "poll_interval": 60,
    "viewport": "1280x900",
}


def ensure_dirs() -> None:
    for directory in (APP_DIR, LOG_DIR, CHROME_WORKER_DIR, APPLY_WORKER_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def load_profile() -> dict:
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(f"Applicant profile not found at {PROFILE_PATH}")
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def get_chrome_path() -> str:
    """Chrome/Chromium executable (CHROME_PATH overrides), as ApplyPilot finds it."""
    env_path = os.environ.get("CHROME_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    system = platform.system()
    if system == "Windows":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
    elif system == "Darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    else:
        candidates = [
            Path(found)
            for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium")
            if (found := shutil.which(name))
        ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise FileNotFoundError("Chrome/Chromium not found. Install Chrome or set CHROME_PATH.")


def get_chrome_user_data() -> Path:
    """The user's everyday Chrome profile, which ApplyPilot copies for its workers."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    return Path.home() / ".config" / "google-chrome"


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


def load_env(path: Path | None = None) -> None:
    """Read KEY=VALUE lines (ApplyPilot's .env convention) without overriding variables already set."""
    path = ENV_PATH if path is None else path
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
