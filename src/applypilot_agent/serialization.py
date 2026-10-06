"""Stable serialization for approval binding and evidence fingerprints."""

import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel


def canonical(value: object) -> str:
    def encode(item):
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        raise TypeError(f"Unsupported serialized type: {type(item).__name__}")

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=encode)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def job_digest(job: BaseModel) -> str:
    # Re-fetch time is not a material change to an application.
    data = job.model_dump(mode="json")
    data.pop("fetched_at", None)
    return digest(data)


def destination_key(url: str) -> str:
    """Conservative dedupe: keep functional query parameters, drop known trackers."""
    parts = urlsplit(url)
    params = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in {"gclid", "fbclid"}
    ]
    return urlunsplit(
        (parts.scheme.casefold(), parts.netloc.casefold(), parts.path or "/", urlencode(sorted(params)), "")
    )
