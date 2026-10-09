"""Runs ApplyPilot's vendored discovery and enrichment, then imports what they found as untagged jobs.

ApplyPilot's code keeps its own working database (`data/local/applypilot/applypilot.db`, its original
schema). `sync` copies new postings from there into this project's store and refreshes the text of
jobs that are not tagged or shown yet. Everything ApplyPilot collects is untrusted job content.
"""

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

import yaml

from .config import Settings
from .domain import InvariantError, Job, Salary
from .store import Store

SOURCES = ("jobspy", "workday", "smartextract")
LLM_ENV = ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL")  # ApplyPilot's provider variables
_SALARY = re.compile(r"^([A-Z]{3}|\$)?([\d,]+(?:\.\d+)?)(?:-(?:[A-Z]{3}|\$)?([\d,]+(?:\.\d+)?))?(?:/(\w+))?$")
_PERIODS = {"yearly": "year", "monthly": "month", "hourly": "hour"}


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
    (config.CONFIG_DIR / "sites.yaml").write_text(yaml.safe_dump(sites, allow_unicode=True), encoding="utf-8")
    employers = data_dir / "employers.yaml"
    source = employers if employers.is_file() else config.PACKAGE_DATA / "employers.yaml"
    (config.CONFIG_DIR / "employers.yaml").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    _keep_jobspy_company(jobspy)
    database.init_db()
    return stage


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


def run_sources(data_dir: Path, settings: Settings, sources: list[str], enrich: bool) -> dict:
    """Run the chosen sources, then enrichment. A failing source is reported; the others still run."""
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
            report["stats"] = detail.run_enrichment(limit=settings.discovery.enrich_limit)
        except Exception as error:
            report["error"] = f"{type(error).__name__}: {error}"
        reports["enrich"] = report
    return json.loads(json.dumps(reports, default=str))


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
    """Import new postings from ApplyPilot's working DB; refresh text of jobs not yet tagged or shown."""
    path = staging_dir(data_dir, settings) / "applypilot.db"
    counts = {"new": 0, "updated": 0, "excluded_titles": 0}
    if not path.is_file():
        return counts
    from .vendor.applypilot import config

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
        rows = db.execute(
            "SELECT url, title, salary, description, location, site, strategy, full_description, application_url "
            "FROM jobs ORDER BY discovered_at"
        ).fetchall()
    finally:
        db.close()

    shown = store.shown_job_ids()
    for row in rows:
        url, text = row["url"], row["full_description"] or row["description"] or ""
        seen = store.external_row(url)
        if seen is None:
            title = row["title"] or ""
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
                "application_url": row["application_url"] or "",
            }
            job = Job(
                id=external_job_id(url),
                source=source,
                board=row["site"] or "",
                title=title,
                company=company or "",
                url=url,
                location=row["location"] or "",
                description=text,
                salary=parse_salary(row["salary"]),
                attributes={key: value for key, value in attributes.items() if value},
                tags=[],
            )
            store.add_job(job)
            store.mark_external(url, job.id, "imported")
            counts["new"] += 1
        elif seen["status"] == "imported" and row["full_description"]:
            job = store.job(seen["job_id"])
            if job.description != text and not store.is_tagged(job.id) and job.id not in shown:
                store.add_job(job.model_copy(update={"description": text}))
                counts["updated"] += 1
    return counts


def _raw_searches(data_dir: Path) -> dict:
    path = Path(data_dir) / "searches.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
