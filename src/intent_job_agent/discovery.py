"""Read-only public ATS feeds (Greenhouse, Lever, Ashby), normalized into untagged jobs.

Adapted from `src/applypilot_agent/discovery.py` on this repository's `main` branch (see NOTICE.md).

Protocol references (checked 2026-10-02 for the original):
https://docs.greenhouse.io/job-board.html
https://github.com/lever/postings-api
https://developers.ashbyhq.com/docs/public-job-posting-api

Only explicit board tokens are fetched, from fixed HTTPS API origins. Returned job URLs are validated
but never fetched. A feed error fails the whole board, so an outage is never mistaken for an empty
board. Everything returned is untrusted job content: it is shown to the agent for tagging only.
"""

import hashlib
import html
import ipaddress
import json
import math
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from .domain import Job, Salary

PROVIDERS = ("greenhouse", "lever", "ashby")
_BOARD_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_LEVER_PAGE_SIZE = 100
_LEVER_PERIODS = {"per-year-salary": "year", "per-month-salary": "month", "per-hour-wage": "hour"}
_ASHBY_PERIODS = {"1 YEAR": "year", "1 MONTH": "month", "1 HOUR": "hour"}


class DiscoveryError(ValueError):
    """A feed could not be fetched or fully interpreted; no partial result."""


def _text(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise DiscoveryError(f"{name} must be {'a' if allow_empty else 'a nonempty'} string")
    return value.strip()


def _url(value: Any, name: str) -> str:
    """Require a public-looking HTTPS URL, without resolving or fetching it."""
    value = _text(value, name)
    if re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise DiscoveryError(f"{name} contains whitespace, a control character, or a backslash")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError as exc:
        raise DiscoveryError(f"{name} is not a valid URL") from exc
    if parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None:
        raise DiscoveryError(f"{name} must be HTTPS without credentials")
    if port not in (None, 443) or "." not in host or host.endswith((".localhost", ".local", ".internal")):
        raise DiscoveryError(f"{name} must use a public DNS hostname and the default HTTPS port")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise DiscoveryError(f"{name} must not contain an IP address")
    if not all(_HOST_LABEL.fullmatch(label) for label in host.split(".")):
        raise DiscoveryError(f"{name} contains an invalid hostname")
    # Fragments are browser-local; query parameters may carry job identity.
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def _html_to_text(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for element in soup(["script", "style"]):
        element.decompose()
    lines = [re.sub(r"[\t \xa0]+", " ", line).strip() for line in soup.get_text("\n").splitlines()]
    return "\n".join(line for line in lines if line)


def stable_job_id(provider: str, source_id: str) -> str:
    """Stable ID from the board-qualified posting id, so a posting survives URL changes."""
    identity = json.dumps([provider, source_id], ensure_ascii=False, separators=(",", ":"))
    return "job_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _objects(value: Any, name: str) -> list[dict]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise DiscoveryError(f"{name} must be an array of objects")
    return value


def _object(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise DiscoveryError(f"{name} must be an object")
    return value


def _optional_text(data: dict, key: str) -> str:
    value = data.get(key)
    return "" if value is None else _text(value, key, allow_empty=True)


def _description_part(data: dict, plain_key: str, html_key: str) -> str:
    """Keep literal angle brackets in documented plain-text response fields."""
    plain = _optional_text(data, plain_key)
    return html.escape(plain) if plain else _optional_text(data, html_key)


def _attributes(data: dict, fields: dict[str, str]) -> dict[str, str]:
    return {target: text for source, target in fields.items() if (text := _optional_text(data, source))}


def _external_id(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not _BOARD_TOKEN.fullmatch(value):
        raise DiscoveryError("Posting id must be a nonempty alphanumeric, underscore, or hyphen token")
    return value


def _number(value: Any) -> float | None:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _salary(currency: Any, period: str | None, low: Any, high: Any) -> Salary | None:
    """Structured pay when the feed states currency, period and at least one bound; otherwise unknown."""
    low, high = _number(low), _number(high)
    if not isinstance(currency, str) or not currency or period is None or (low is None and high is None):
        return None
    return Salary(currency=currency, period=period, min=low, max=high)


def _greenhouse(item: dict, board: str) -> dict:
    location = _object(item.get("location", {}), "location")
    departments = _objects(item.get("departments", []), "departments")
    attributes = _attributes(item, {"updated_at": "updated_at"})
    if departments:
        attributes["department"] = "; ".join(_text(value.get("name"), "department name") for value in departments)
    # Greenhouse documents entity-encoded HTML, sometimes nested; decode only this provider's HTML.
    content = _text(item.get("content"), "content", allow_empty=True)
    while (decoded := html.unescape(content)) != content:
        content = decoded
    return {
        "source_id": f"{board}:{_external_id(item.get('id'))}",
        "url": item.get("absolute_url"),
        "title": item.get("title"),
        "location": _optional_text(location, "name"),
        "description": content,
        "attributes": attributes,
        "salary": None,
    }


def _lever(item: dict, board: str) -> dict:
    categories = _object(item.get("categories", {}), "categories")
    attributes = _attributes(categories, {"commitment": "employment_type", "team": "team", "department": "department"})
    attributes.update(_attributes(item, {"country": "country", "workplaceType": "workplace_type"}))
    description = _description_part(item, "descriptionPlain", "description")
    if not description:
        description = "\n".join(
            filter(
                None,
                [
                    _description_part(item, "openingPlain", "opening"),
                    _description_part(item, "descriptionBodyPlain", "descriptionBody"),
                ],
            )
        )
    parts = [description]
    for section in _objects(item.get("lists", []), "lists"):
        parts.extend(
            [
                html.escape(_text(section.get("text"), "list title", allow_empty=True)),
                _text(section.get("content"), "list content", allow_empty=True),
            ]
        )
    parts.extend(
        [
            _description_part(item, "additionalPlain", "additional"),
            _description_part(item, "salaryDescriptionPlain", "salaryDescription"),
        ]
    )
    salary = None
    if item.get("salaryRange") is not None:
        raw = _object(item["salaryRange"], "salaryRange")
        attributes["salary_range"] = json.dumps(raw, ensure_ascii=False, sort_keys=True)
        salary = _salary(raw.get("currency"), _LEVER_PERIODS.get(raw.get("interval")), raw.get("min"), raw.get("max"))
    locations = categories.get("allLocations")
    if locations is not None:
        if not isinstance(locations, list) or not all(isinstance(value, str) for value in locations):
            raise DiscoveryError("allLocations must be an array of strings")
        attributes["all_locations"] = "; ".join(locations)
    return {
        "source_id": f"{board}:{_external_id(item.get('id'))}",
        "url": item.get("hostedUrl"),
        "title": item.get("text"),
        "location": _optional_text(categories, "location"),
        "description": "\n".join(part for part in parts if part),
        "attributes": attributes,
        "salary": salary,
    }


def _ashby(item: dict, board: str) -> dict:
    url = _url(item.get("jobUrl"), "jobUrl")
    # The public API documents no separate id field; the job URL carries the posting UUID.
    external_id = _external_id(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    attributes = _attributes(
        item,
        {
            "department": "department",
            "team": "team",
            "employmentType": "employment_type",
            "workplaceType": "workplace_type",
            "publishedAt": "published_at",
        },
    )
    if "isRemote" in item:
        if not isinstance(item["isRemote"], bool):
            raise DiscoveryError("isRemote must be a boolean")
        attributes["is_remote"] = str(item["isRemote"]).lower()
    secondary = _objects(item.get("secondaryLocations", []), "secondaryLocations")
    if secondary:
        attributes["secondary_locations"] = "; ".join(
            _text(value.get("location"), "secondary location") for value in secondary
        )
    salary = None
    if item.get("compensation") is not None:
        raw = _object(item["compensation"], "compensation")
        attributes["compensation"] = json.dumps(raw, ensure_ascii=False, sort_keys=True)
        for component in _objects(raw.get("summaryComponents", []), "summaryComponents"):
            if component.get("compensationType") == "Salary":
                salary = _salary(
                    component.get("currencyCode"),
                    _ASHBY_PERIODS.get(component.get("interval")),
                    component.get("minValue"),
                    component.get("maxValue"),
                )
                break
    description = _description_part(item, "descriptionPlain", "descriptionHtml")
    if "descriptionPlain" not in item and "descriptionHtml" not in item:
        raise DiscoveryError("Ashby posting is missing its description")
    return {
        "source_id": f"{board}:{external_id}",
        "url": url,
        "title": item.get("title"),
        "location": _optional_text(item, "location"),
        "description": description,
        "attributes": attributes,
        "salary": salary,
    }


def _get(client: httpx.Client, url: str, params: dict, timeout: float) -> Any:
    try:
        response = client.get(
            url, params=params, timeout=timeout, follow_redirects=False, headers={"Accept": "application/json"}
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # No fallback to an empty list; never include response bodies in errors.
        raise DiscoveryError(f"Could not read public board {url}: {type(exc).__name__}") from exc


def fetch_board(
    provider: str, board: str, *, company: str, timeout_seconds: float, client: httpx.Client | None = None
) -> list[Job]:
    """Fetch one entire public board as untagged jobs, raising on HTTP, shape or pagination failures.

    `board` is a token, never a URL. Ashby's explicitly unlisted jobs are excluded. An injected
    client stays owned by the caller.
    """
    if provider not in PROVIDERS:
        raise DiscoveryError(f"Unsupported board type {provider!r}; expected one of {', '.join(PROVIDERS)}")
    if not isinstance(board, str) or not _BOARD_TOKEN.fullmatch(board):
        raise DiscoveryError("board must be a 1-128 character alphanumeric, underscore, or hyphen token")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int | float):
        raise DiscoveryError("timeout_seconds must be a finite positive number")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise DiscoveryError("timeout_seconds must be a finite positive number")
    if client is None:
        with httpx.Client() as owned:
            return fetch_board(provider, board, company=company, timeout_seconds=timeout_seconds, client=owned)

    company = company.strip() or board
    jobs: dict[str, Job] = {}
    parse = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}[provider]

    def add(items: list[dict]) -> None:
        for index, item in enumerate(items):
            try:
                if provider == "ashby":
                    if "isListed" in item and not isinstance(item["isListed"], bool):
                        raise DiscoveryError("isListed must be a boolean")
                    if item.get("isListed") is False:
                        continue
                data = parse(item, board)
                job = Job(
                    id=stable_job_id(provider, data["source_id"]),
                    source=provider,
                    board=board,
                    title=_text(data["title"], "title"),
                    company=company,
                    url=_url(data["url"], "url"),
                    location=data["location"],
                    description=_html_to_text(data["description"]),
                    attributes=data["attributes"],
                    salary=data["salary"],
                    tags=[],
                )
                previous = jobs.get(job.id)
                if previous is not None and previous != job:
                    raise DiscoveryError(f"Conflicting duplicate posting {data['source_id']}")
                jobs[job.id] = job
            except ValueError as exc:
                raise DiscoveryError(f"Invalid {provider}/{board} posting at batch index {index}: {exc}") from exc

    if provider == "greenhouse":
        response = _object(
            _get(
                client, f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", {"content": "true"}, timeout_seconds
            ),
            "Greenhouse response",
        )
        items = _objects(response.get("jobs"), "jobs")
        if "meta" in response:
            total = _object(response["meta"], "meta").get("total")
            if isinstance(total, bool) or not isinstance(total, int) or total != len(items):
                raise DiscoveryError("Greenhouse meta.total does not match the returned job count")
        add(items)
    elif provider == "ashby":
        response = _object(
            _get(
                client,
                f"https://api.ashbyhq.com/posting-api/job-board/{board}",
                {"includeCompensation": "true"},
                timeout_seconds,
            ),
            "Ashby response",
        )
        add(_objects(response.get("jobs"), "jobs"))
    else:
        skip = 0
        while True:
            items = _objects(
                _get(
                    client,
                    f"https://api.lever.co/v0/postings/{board}",
                    {"mode": "json", "skip": skip, "limit": _LEVER_PAGE_SIZE},
                    timeout_seconds,
                ),
                "Lever response",
            )
            previous_count = len(jobs)
            add(items)
            if len(items) < _LEVER_PAGE_SIZE:
                break
            if len(jobs) == previous_count:
                raise DiscoveryError("Lever pagination made no progress; refusing a partial or repeated feed")
            skip += len(items)
    return list(jobs.values())
