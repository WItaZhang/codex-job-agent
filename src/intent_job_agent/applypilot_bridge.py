"""Runs ApplyPilot's vendored discovery and enrichment, then imports what they found as untagged jobs.

ApplyPilot's code keeps its own working database (`data/local/applypilot/applypilot.db`, its original
schema). `sync` copies new postings from there into this project's store and refreshes the text of
jobs that are not tagged or shown yet, or that are not shown and had no text, and the location of jobs nobody
has seen or acted on yet (spec section 10, slice 2c items 18-19). Everything ApplyPilot collects is untrusted
job content.
"""

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .config import Settings
from .domain import InvariantError, Job, Salary
from .pretag import read_locations
from .store import Store

SOURCES = ("jobspy", "workday", "smartextract")
LLM_ENV = ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL")  # ApplyPilot's provider variables
_SALARY = re.compile(r"^([A-Z]{3}|\$)?([\d,]+(?:\.\d+)?)(?:-(?:[A-Z]{3}|\$)?([\d,]+(?:\.\d+)?))?(?:/(\w+))?$")
_PERIODS = {"yearly": "year", "monthly": "month", "hourly": "hour"}
# What ApplyPilot's sources store for a missing value: str(None) from its JobSpy storage, "null" from its
# enrichment's LLM step, "nan" from pandas.
_PLACEHOLDERS = {"none", "null", "nan"}


def staging_dir(data_dir: Path, settings: Settings) -> Path:
    return Path(data_dir) / settings.discovery.applypilot_dir


def llm_configured() -> bool:
    return llm_provider() is not None


def llm_provider() -> str | None:
    """Which provider ApplyPilot's client would use (its precedence: a local URL, then Gemini, then OpenAI)."""
    if os.environ.get("LLM_URL"):
        return "local"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return None


def setup_status(data_dir: Path, settings: Settings) -> dict:
    """What discovery will use, without revealing any secret."""
    if settings.execution_mode == "local_only":
        return {"execution_mode": "local_only", "network_enabled": False, "data_dir": str(data_dir)}
    from .vendor.applypilot import config

    data_dir = Path(data_dir)
    config.load_env(data_dir / ".env")

    def own_or_default(name: str) -> str:
        return "yours" if (data_dir / name).is_file() else "ApplyPilot defaults"

    return {
        "searches_yaml": (data_dir / "searches.yaml").is_file(),
        "employers_yaml": own_or_default("employers.yaml"),
        "sites_yaml": own_or_default("sites.yaml"),
        "boards_yaml": (data_dir / settings.discovery.boards_file).is_file(),
        "llm_provider": llm_provider(),
        "proxy": bool(os.environ.get(settings.discovery.proxy_env)),
        "env_file": str(data_dir / ".env"),
    }


def configure(data_dir: Path, settings: Settings) -> Path:
    """Point the vendored modules at the user's files: searches.yaml, optional sites.yaml and
    employers.yaml (ApplyPilot's bundled lists otherwise), .env for LLM keys, and the working DB."""
    settings.require_external("vendor discovery configuration", data_dir)
    from .vendor.applypilot import config, database
    from .vendor.applypilot.discovery import jobspy, smartextract, workday
    from .vendor.applypilot.enrichment import detail

    data_dir = Path(data_dir)
    stage = staging_dir(data_dir, settings)
    (stage / "config").mkdir(parents=True, exist_ok=True)
    config.load_env(data_dir / ".env")
    config.APP_DIR = stage
    config.DB_PATH = stage / "applypilot.db"
    config.CONFIG_DIR = stage / "config"
    config.SEARCH_CONFIG_PATH = data_dir / "searches.yaml"
    # Names the vendored modules imported directly.
    database.DB_PATH = detail.DB_PATH = config.DB_PATH
    workday.CONFIG_DIR = smartextract.CONFIG_DIR = config.CONFIG_DIR

    sites = yaml.safe_load((config.PACKAGE_DATA / "sites.yaml").read_text(encoding="utf-8"))
    if (data_dir / "sites.yaml").is_file():
        sites["sites"] = (yaml.safe_load((data_dir / "sites.yaml").read_text(encoding="utf-8")) or {}).get("sites", [])
    _complete_base_urls(sites)
    (config.CONFIG_DIR / "sites.yaml").write_text(yaml.safe_dump(sites, allow_unicode=True), encoding="utf-8")
    employers = data_dir / "employers.yaml"
    source = employers if employers.is_file() else config.PACKAGE_DATA / "employers.yaml"
    (config.CONFIG_DIR / "employers.yaml").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    _keep_jobspy_company(jobspy)
    _keep_workday_locations(workday)
    database.init_db()
    db = sqlite3.connect(config.DB_PATH)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS intent_agent_location (url TEXT PRIMARY KEY, location TEXT)")
    finally:
        db.close()
    return stage


def _complete_base_urls(sites: dict) -> None:
    """ApplyPilot resolves relative links only for sites listed in its `base_urls`, and its list lacks some
    (SimplyHired): a site missing there uses its search page's domain. Sites it lists as null stay unresolved."""
    base_urls = sites.get("base_urls") or {}
    for site in sites.get("sites") or []:
        name, parts = site.get("name"), urlsplit(site.get("url") or "")
        if name and name not in base_urls and parts.scheme in ("http", "https") and parts.netloc:
            base_urls[name] = f"{parts.scheme}://{parts.netloc}"
    sites["base_urls"] = base_urls


def _keep_jobspy_company(jobspy) -> None:
    """ApplyPilot's JobSpy storage drops the company column; record it beside the row instead."""
    if getattr(jobspy, "_intent_agent_keeps_company", False):
        return
    original = jobspy.store_jobspy_results

    def store_with_company(conn, df, source_label):
        conn.execute("CREATE TABLE IF NOT EXISTS intent_agent_company (url TEXT PRIMARY KEY, company TEXT)")
        for _, row in df.iterrows():
            url, company = str(row.get("job_url", "")), row.get("company")
            if url and url != "nan" and isinstance(company, str) and company:
                conn.execute("INSERT OR IGNORE INTO intent_agent_company (url, company) VALUES (?, ?)", (url, company))
        return original(conn, df, source_label)

    jobspy.store_jobspy_results = store_with_company
    jobspy._intent_agent_keeps_company = True


def _keep_workday_locations(workday) -> None:
    """ApplyPilot reads every Workday posting's detail but stores the search page's location text ("2 Locations",
    or nothing for some employers); record the detail's places beside the row instead (spec 2c item 19)."""
    if getattr(workday.workday_detail, "_intent_agent_keeps_locations", False):
        return
    original = workday.workday_detail

    def detail_keeping_locations(employer, external_path):
        detail = original(employer, external_path)
        info = detail.get("jobPostingInfo", {})
        location = workday_location(info)
        # The link ApplyPilot stores the posting under: the detail's link, or the one its store_results builds.
        url = info.get("externalUrl", "") or f"{employer['base_url']}/{employer['site_id']}{external_path}"
        if location:
            try:
                conn = workday.get_connection()
                conn.execute(
                    "INSERT OR REPLACE INTO intent_agent_location (url, location) VALUES (?, ?)", (url, location)
                )
                conn.commit()
            except sqlite3.Error:  # never cost ApplyPilot the detail it read; the search page's text stays
                pass
        return detail

    detail_keeping_locations._intent_agent_keeps_locations = True
    workday.workday_detail = detail_keeping_locations


def workday_location(info: dict) -> str:
    """A Workday detail's places as written, the primary one first, each once, joined with "; "."""
    places = [info.get("location"), *(info.get("additionalLocations") or [])]
    return "; ".join(dict.fromkeys(p.strip() for p in places if isinstance(p, str) and _value(p)))


def run_sources(data_dir: Path, settings: Settings, sources: list[str], enrich: bool) -> dict:
    """Run the chosen sources, then enrichment. A failing source is reported; the others still run."""
    settings.require_external("job discovery", data_dir)
    configure(data_dir, settings)
    from .vendor.applypilot import config

    cfg = config.load_search_config()
    if not cfg:
        raise InvariantError(f"No searches.yaml in {data_dir}; create it before discovering jobs")
    proxy = os.environ.get(settings.discovery.proxy_env) or None
    reports = {}
    for source in sources:
        report = {"error": None, "skipped": None, "stats": None}
        try:
            if source == "jobspy":
                from .vendor.applypilot.discovery import jobspy

                report["stats"] = jobspy.run_discovery({**cfg, "proxy": proxy} if proxy else cfg)
            elif source == "workday":
                from .vendor.applypilot.discovery import workday

                workday.setup_proxy(proxy)
                report["stats"] = workday.run_workday_discovery()
            elif source == "smartextract":
                if not llm_configured():
                    report["skipped"] = "no LLM key configured"
                else:
                    from .vendor.applypilot.discovery import smartextract

                    report["stats"] = smartextract.run_smart_extract()
        except Exception as error:  # one source's failure must not stop the others
            report["error"] = f"{type(error).__name__}: {error}"
        reports[source] = report
    if enrich:
        report = {"error": None, "skipped": None, "stats": None}
        try:
            from .vendor.applypilot.enrichment import detail

            detail.set_proxy(proxy)
            report["retried"] = _retry_missing_text(config.DB_PATH)
            report["stats"] = detail.run_enrichment(limit=settings.discovery.enrich_limit)
        except Exception as error:
            report["error"] = f"{type(error).__name__}: {error}"
        reports["enrich"] = report
    return json.loads(json.dumps(reports, default=str))


def _retry_missing_text(path: Path) -> int:
    """Rows ApplyPilot's enrichment finished without real text go back to it, once each. Relative links are
    resolved first, as its enrichment does, so each row is remembered under the link it is retried at."""
    from .vendor.applypilot.enrichment import detail

    db = sqlite3.connect(path)
    try:
        detail.resolve_all_urls(db)
        db.execute("CREATE TABLE IF NOT EXISTS intent_agent_retried (url TEXT PRIMARY KEY)")
        rows = db.execute(
            "SELECT url, site, full_description FROM jobs WHERE detail_scraped_at IS NOT NULL "
            "AND url NOT IN (SELECT url FROM intent_agent_retried)"
        ).fetchall()
        retry = [(url,) for url, site, text in rows if site not in detail.SKIP_DETAIL_SITES and not _value(text)]
        with db:
            db.executemany(
                "UPDATE jobs SET full_description = NULL, detail_scraped_at = NULL, detail_error = NULL WHERE url = ?",
                retry,
            )
            db.executemany("INSERT INTO intent_agent_retried (url) VALUES (?)", retry)
    finally:
        db.close()
    return len(retry)


def _value(raw) -> str:
    """A field from ApplyPilot's DB, empty when its source stored a placeholder for "nothing"."""
    text = "" if raw is None else str(raw)
    return "" if not text.strip() or text.strip().casefold() in _PLACEHOLDERS else text


def external_job_id(url: str) -> str:
    return "job_" + hashlib.sha256(json.dumps(["applypilot", url]).encode()).hexdigest()


def parse_salary(text: str | None) -> Salary | None:
    """ApplyPilot stores JobSpy pay as e.g. "USD150,000-USD200,000/yearly"; keep yearly, monthly, hourly."""
    match = _SALARY.match((text or "").replace(" ", ""))
    if not match or _PERIODS.get(match.group(4) or "") is None:
        return None
    low, high = (float(v.replace(",", "")) if v else None for v in match.group(2, 3))
    currency = match.group(1) if match.group(1) not in (None, "$") else "USD"
    return Salary(currency=currency, period=_PERIODS[match.group(4)], min=low, max=high)


def sync(store: Store, data_dir: Path, settings: Settings) -> dict:
    """Import new postings from ApplyPilot's working DB; refresh text of jobs not yet tagged or shown, and the
    location of jobs not shown, corrected or acted on (Workday's detail lists every place)."""
    settings.require_external("discovery synchronization", data_dir, store.path)
    path = staging_dir(data_dir, settings) / "applypilot.db"
    counts = {"new": 0, "updated": 0, "relocated": 0, "retagged": 0, "relinked": 0, "excluded_titles": 0}
    if not path.is_file():
        return counts
    configure(data_dir, settings)  # the base URLs that links are resolved with
    from .vendor.applypilot import config
    from .vendor.applypilot.enrichment import detail

    excluded = [word.casefold() for word in config.normalize_search_config(_raw_searches(data_dir))["exclude_titles"]]
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        companies = (
            {row["url"]: row["company"] for row in db.execute("SELECT url, company FROM intent_agent_company")}
            if "intent_agent_company" in tables
            else {}
        )
        locations = {
            row["url"]: row["location"] for row in db.execute("SELECT url, location FROM intent_agent_location")
        }
        rows = db.execute(
            "SELECT url, title, salary, description, location, site, strategy, full_description, application_url "
            "FROM jobs ORDER BY discovered_at"
        ).fetchall()
    finally:
        db.close()

    counts["relinked"] = _relink(store)
    shown = store.shown_job_ids()
    # Jobs whose location stays as it is (item 18): shown, corrected, or past "pending" on the apply list.
    kept = shown | set(store.tag_overrides())
    kept |= {job_id for job_id, application in store.applications().items() if application["status"] != "pending"}
    for row in rows:
        url = detail.resolve_url(row["url"], row["site"] or "") or row["url"]
        text = _value(row["full_description"]) or _value(row["description"])
        location = _value(locations.get(row["url"])) or _value(row["location"])
        seen = store.external_row(url)
        if seen is None:
            title = _value(row["title"])
            if any(word in title.casefold() for word in excluded):
                store.mark_external(url, None, "excluded")
                counts["excluded_titles"] += 1
                continue
            strategy = row["strategy"] or ""
            source = "jobspy" if strategy == "jobspy" else "workday" if strategy == "workday_api" else "smartextract"
            company = companies.get(url, "") if source == "jobspy" else row["site"] if source == "workday" else ""
            attributes = {
                "site": row["site"] or "",
                "strategy": strategy,
                "application_url": _value(row["application_url"]),
            }
            job = Job(
                id=external_job_id(url),
                source=source,
                board=row["site"] or "",
                title=title,
                company=company or "",
                url=url,
                location=location,
                description=text,
                salary=parse_salary(_value(row["salary"])),
                attributes={key: value for key, value in attributes.items() if value},
                tags=[],
            )
            store.add_job(job)
            store.mark_external(url, job.id, "imported")
            counts["new"] += 1
        elif seen["status"] == "imported":
            job = store.job(seen["job_id"])
            stored = _value(job.description)  # a placeholder stored before 2026-10-15 is no text
            tagged = store.is_tagged(job.id)
            if text and text != stored and job.id not in shown and not (tagged and stored):
                if tagged:  # it was tagged without its text: tag it again from the text
                    store.untag(job.id)
                    counts["retagged"] += 1
                stored = text
            if stored != job.description:
                store.add_job(store.job(job.id).model_copy(update={"description": stored}))
                counts["updated"] += 1
            if location and location != job.location and job.id not in kept:
                counts["retagged"] += _relocate(store, job.id, location)
                counts["relocated"] += 1
    return counts


def _relocate(store: Store, job_id: str, location: str) -> bool:
    """Give a stored job the location just read (item 18). A tagged job swaps its location tags for the pre-tags
    when every place is read, and otherwise goes back to tagging (returns True). Being tagged is kept as it was, so
    the swap uses no tagging budget."""
    job, requeued = store.job(job_id), False
    if store.is_tagged(job_id):
        places = read_locations(location, store.vocabulary())
        if places is None:
            job, requeued = store.untag(job_id), True
        else:
            job = job.model_copy(update={"tags": [t for t in job.tags if not t.startswith("location.")] + places})
    store.add_job(job.model_copy(update={"location": location}))
    return requeued


def _relink(store: Store) -> int:
    """Jobs stored with a relative link (before 2026-10-15) get the full link, keeping their id, tags and apply
    status; the full link is recorded too, so ApplyPilot rewriting its row later finds the same job."""
    from .vendor.applypilot.enrichment import detail

    relinked = 0
    for external in store.external_rows():
        url, job_id = external["url"], external["job_id"]
        if external["status"] != "imported" or url.startswith(("http://", "https://")):
            continue
        job = store.job(job_id)
        full = detail.resolve_url(url, job.board)
        if full and full != url and store.external_row(full) is None:
            store.add_job(job.model_copy(update={"url": full}))
            store.mark_external(full, job_id, "imported")
            relinked += 1
    return relinked


def _raw_searches(data_dir: Path) -> dict:
    path = Path(data_dir) / "searches.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
