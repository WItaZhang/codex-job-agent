"""Read-only public ATS feeds, normalized without application side effects.

Protocol references (checked 2026-10-02):
https://docs.greenhouse.io/job-board.html
https://github.com/lever/postings-api
https://developers.ashbyhq.com/docs/public-job-posting-api

Only explicit board tokens are fetched, using fixed HTTPS API origins. Returned
job URLs are validated but never fetched here. Feed errors fail the whole call;
callers can therefore distinguish an empty board from a failed refresh.
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import math
import re
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

Provider = Literal["greenhouse", "lever", "ashby", "manual"]
_BOARD_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_LEVER_PAGE_SIZE = 100


class DiscoveryError(ValueError):
    """A feed could not be fetched or fully interpreted; no partial result."""


class _Job(BaseModel):
    """Internal boundary schema; public functions return JSON-ready dictionaries."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    source: Provider
    source_id: str
    url: str
    apply_url: str
    title: str = Field(min_length=1)
    company: str = Field(min_length=1)
    location: str
    description: str
    attributes: dict[str, str]
    fetched_at: str


def _provider(value: str, *, fetch: bool = False) -> str:
    allowed = {"greenhouse", "lever", "ashby"}
    if not fetch:
        allowed.add("manual")
    if not isinstance(value, str) or value not in allowed:
        raise DiscoveryError(f"Unsupported source: {value!r}; expected {', '.join(sorted(allowed))}")
    return value


def _text(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise DiscoveryError(f"{name} must be {'a' if allow_empty else 'a nonempty'} string")
    return value.strip()


def _url(value: Any, name: str, *, allow_loopback: bool = False) -> str:
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
    if (
        allow_loopback
        and parsed.scheme == "http"
        and host in {"127.0.0.1", "::1"}
        and parsed.username is None
        and parsed.password is None
    ):
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
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
    # Fragments are browser-local; preserve query parameters because some ATSs
    # encode job identity there. Never strip application-routing information.
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def _html_to_text(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for element in soup(["script", "style"]):
        element.decompose()
    lines = [re.sub(r"[\t \xa0]+", " ", line).strip() for line in soup.get_text("\n").splitlines()]
    return "\n".join(line for line in lines if line)


def stable_job_id(provider: str, source_id: str, url: str) -> str:
    """Stable, filename-safe ID; ATS source IDs must include their board token.

    A known provider ID survives URL changes. Manual jobs without an external
    identity use their validated URL. The full digest avoids ID truncation.
    """
    provider = _provider(provider)
    canonical_url = _url(url, "url", allow_loopback=provider == "manual")
    source_id = _text(source_id, "source_id", allow_empty=provider == "manual")
    if provider != "manual":
        board, separator, external_id = source_id.partition(":")
        if not separator or not _BOARD_TOKEN.fullmatch(board) or not _BOARD_TOKEN.fullmatch(external_id):
            raise DiscoveryError("ATS source_id must be a board-qualified token, for example 'acme:123'")
    identity = json.dumps([provider, source_id or canonical_url], ensure_ascii=False, separators=(",", ":"))
    return "job_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def normalize_job(data: dict) -> dict:
    """Validate a canonical job and convert HTML description to plain text.

    Required fields: source, source_id (empty permitted for manual), url, title,
    company, description. Optional: apply_url (defaults to url), location,
    attributes, fetched_at, id. Supplying an incorrect id is an error.
    """
    if not isinstance(data, dict):
        raise DiscoveryError("Job must be an object")
    payload = dict(data)
    provider = _provider(payload.get("source"))
    url = _url(payload.get("url"), "url", allow_loopback=provider == "manual")
    source_id = _text(payload.get("source_id", ""), "source_id", allow_empty=provider == "manual")
    expected_id = stable_job_id(provider, source_id, url)
    if "id" in payload and payload["id"] != expected_id:
        raise DiscoveryError("Job id does not match its source identity")
    fetched_at = payload.get("fetched_at", datetime.now(UTC).isoformat())
    try:
        timestamp = datetime.fromisoformat(_text(fetched_at, "fetched_at"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timezone missing")
    except ValueError as exc:
        raise DiscoveryError("fetched_at must be an ISO timestamp with a timezone") from exc
    payload.update(
        id=expected_id,
        source=provider,
        source_id=source_id,
        url=url,
        apply_url=_url(payload.get("apply_url", url), "apply_url", allow_loopback=provider == "manual"),
        title=_text(payload.get("title"), "title"),
        company=_text(payload.get("company"), "company"),
        location=_text(payload.get("location", ""), "location", allow_empty=True),
        description=_html_to_text(_text(payload.get("description"), "description", allow_empty=True)),
        attributes=payload.get("attributes", {}),
        fetched_at=timestamp.astimezone(UTC).isoformat(),
    )
    return _Job.model_validate(payload).model_dump()


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


def _greenhouse(item: dict, board: str) -> dict:
    location = _object(item.get("location", {}), "location")
    departments = _objects(item.get("departments", []), "departments")
    attributes = _attributes(item, {"updated_at": "updated_at"})
    if departments:
        attributes["department"] = "; ".join(_text(value.get("name"), "department name") for value in departments)
    # Greenhouse documents entity-encoded HTML, sometimes nested. Decode only
    # this provider's HTML input; plain-text ATS fields retain literal entities.
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
    if item.get("salaryRange") is not None:
        salary = _object(item["salaryRange"], "salaryRange")
        attributes["salary_range"] = json.dumps(salary, ensure_ascii=False, sort_keys=True)
    locations = categories.get("allLocations")
    if locations is not None:
        if not isinstance(locations, list) or not all(isinstance(value, str) for value in locations):
            raise DiscoveryError("allLocations must be an array of strings")
        attributes["all_locations"] = "; ".join(locations)
    return {
        "source_id": f"{board}:{_external_id(item.get('id'))}",
        "url": item.get("hostedUrl"),
        "apply_url": item.get("applyUrl", item.get("hostedUrl")),
        "title": item.get("text"),
        "location": _optional_text(categories, "location"),
        "description": "\n".join(part for part in parts if part),
        "attributes": attributes,
    }


def _ashby(item: dict, board: str) -> dict:
    url = _url(item.get("jobUrl"), "jobUrl")
    # The public API does not document a separate id field; its job URL carries
    # the posting UUID. Do not depend on undocumented optional response fields.
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
    if item.get("compensation") is not None:
        attributes["compensation"] = json.dumps(
            _object(item["compensation"], "compensation"), ensure_ascii=False, sort_keys=True
        )
    description = _description_part(item, "descriptionPlain", "descriptionHtml")
    if "descriptionPlain" not in item and "descriptionHtml" not in item:
        raise DiscoveryError("Ashby posting is missing its description")
    return {
        "source_id": f"{board}:{external_id}",
        "url": url,
        "apply_url": item.get("applyUrl", url),
        "title": item.get("title"),
        "location": _optional_text(item, "location"),
        "description": description,
        "attributes": attributes,
    }


def _get(client: httpx.Client, url: str, params: dict, timeout: float) -> Any:
    try:
        response = client.get(
            url, params=params, timeout=timeout, follow_redirects=False, headers={"Accept": "application/json"}
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # No fallback to an empty list: callers must not mistake an outage for
        # all jobs having closed. Include source URL but never response bodies.
        raise DiscoveryError(f"Could not read public board {url}: {type(exc).__name__}") from exc


def fetch_board(
    provider: str,
    board: str,
    *,
    timeout_seconds: float = 20,
    client: httpx.Client | None = None,
) -> list[dict]:
    """Fetch one entire public board, raising on HTTP/shape/pagination failures.

    ``board`` is a token, never a URL. Lever currently targets its global
    instance. ``company`` is the board token (an identity, not an inferred
    display name). Ashby's explicitly unlisted jobs are intentionally excluded.
    An injected client remains owned by the caller and is never closed here.
    """
    provider = _provider(provider, fetch=True)
    if not isinstance(board, str) or not _BOARD_TOKEN.fullmatch(board):
        raise DiscoveryError("board must be a 1-128 character alphanumeric, underscore, or hyphen token")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise DiscoveryError("timeout_seconds must be a finite positive number")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise DiscoveryError("timeout_seconds must be a finite positive number")
    if client is None:
        with httpx.Client() as owned_client:
            return fetch_board(provider, board, timeout_seconds=timeout_seconds, client=owned_client)

    fetched_at = datetime.now(UTC).isoformat()
    jobs: dict[str, dict] = {}

    def add(items: list[dict]) -> None:
        for index, item in enumerate(items):
            try:
                if provider == "ashby":
                    if "isListed" in item and not isinstance(item["isListed"], bool):
                        raise DiscoveryError("isListed must be a boolean")
                    if item.get("isListed") is False:
                        continue
                parser = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}[provider]
                job = normalize_job(
                    {**parser(item, board), "source": provider, "company": board, "fetched_at": fetched_at}
                )
                previous = jobs.get(job["id"])
                if previous is not None and previous != job:
                    raise DiscoveryError(f"Conflicting duplicate posting {job['source_id']}")
                jobs[job["id"]] = job
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
