"""Isolated browser execution with observed receipts and an explicit submit boundary.

The caller must gate submission, persist intent, and acquire an application lock
before calling ``submit``. This module never infers authorization from web text.
One session handles one attempt. A post-click timeout is an unknown outcome and
consumes that attempt; it must be reconciled, never blindly resubmitted.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Self
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .browser_checks import (
    CHECK_REQUIRED,
    FIELD_METADATA,
    FILE_SELECTION,
    FORM_VALIDITY,
    INSPECT_FIELDS,
    RECEIPT_CONTAINER,
)
from .browser_models import BrowserError, BrowserField, BrowserPlan, url_origin

__all__ = ["BrowserError", "BrowserField", "BrowserPlan", "BrowserSession"]


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _digest(plan: BrowserPlan) -> str:
    return hashlib.sha256(plan.model_dump_json().encode()).hexdigest()


def _file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _explicit_acknowledgement(text: str) -> bool:
    """A bounded English receipt contract, not a universal semantic classifier.

    Unsupported wording stays unknown. Site-specific adapters are needed for
    other languages and receipt types. A displayed acknowledgement does not
    authenticate the employer's backend record.
    """
    normalized = " ".join(text.casefold().split())
    if re.search(r"\b(error|failed|failure|pending|submitting|processing|not|unable)\b", normalized):
        return False
    return bool(
        re.match(
            r"^(?:(?:your )?application (?:(?:has been|was) )?(?:successfully )?(?:received|submitted)\b"
            r"|(?:thank you|thanks) for applying\b)",
            normalized,
        )
    )


class BrowserSession:
    """Own Chromium and an isolated context, with no real-profile copying.

    Origins are exact allowlisted HTTP(S) origins. With no explicit allowlist,
    the first URL establishes it. Cross-origin resources, service workers, and
    all write requests before the submission boundary are blocked. Forms that
    require background writes during preparation need a dedicated adapter.
    """

    def __init__(
        self,
        evidence_dir: str | Path,
        *,
        headless: bool = True,
        timeout_ms: int = 10_000,
        allowed_origins: list[str] | tuple[str, ...] | None = None,
        storage_state: str | Path | None = None,
        executable_path: str | Path | None = None,
    ) -> None:
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        self.evidence_dir = Path(evidence_dir)
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.origins = {url_origin(url) for url in allowed_origins or ()}
        self.storage_state = str(storage_state) if storage_state is not None else None
        self.executable_path = str(executable_path) if executable_path is not None else None
        self._runtime = self._browser = self._context = self._page = None
        self._prepared_digest: str | None = None
        self._prepared_url: str | None = None
        self._file_hashes: dict[str, str] = {}
        self._writes_enabled = False
        self._attempted = False
        self._blocked: list[dict] = []

    def __enter__(self) -> Self:
        try:
            self._runtime = sync_playwright().start()
            self._browser = self._runtime.chromium.launch(headless=self.headless, executable_path=self.executable_path)
            self._context = self._browser.new_context(
                storage_state=self.storage_state, service_workers="block", accept_downloads=False
            )
            self._context.route("**/*", self._route)
            self._page = self._context.new_page()
            self._page.set_default_timeout(self.timeout_ms)
            self._page.set_default_navigation_timeout(self.timeout_ms)
            return self
        except Exception:
            self.close()
            raise

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close only browser resources created by this instance."""
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._runtime is not None:
                self._runtime.stop()
            self._runtime = self._browser = self._context = self._page = None

    @property
    def page(self):
        if self._page is None:
            raise BrowserError("session_closed", "Use BrowserSession as a context manager")
        return self._page

    def _route(self, route) -> None:
        request = route.request
        try:
            permitted_origin = url_origin(request.url) in self.origins
        except ValueError:
            permitted_origin = False
        reason = None
        if not permitted_origin:
            reason = "origin_not_allowed"
        elif request.method not in {"GET", "HEAD", "OPTIONS"} and not self._writes_enabled:
            reason = "write_before_submit"
        if reason:
            self._blocked.append({"url": request.url, "method": request.method, "reason": reason})
            route.abort("blockedbyclient")
        else:
            route.continue_()

    def _check_origin(self, url: str, *, establish: bool = False) -> None:
        origin = url_origin(url)
        if establish and not self.origins:
            self.origins.add(origin)
        if origin not in self.origins:
            raise BrowserError("origin_not_allowed", "URL origin is outside the allowlist", url=url)

    def _navigate(self, url: str) -> None:
        if self._attempted:
            raise BrowserError("attempt_consumed", "A submitted attempt requires reconciliation in a new session")
        self._prepared_digest = None
        self._prepared_url = None
        self._check_origin(url, establish=True)
        self._blocked.clear()
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            self._check_origin(self.page.url)
        except PlaywrightError as exc:
            raise BrowserError("navigation_failed", "Could not open the allowed form", url=url) from exc

    def inspect(self, url: str) -> dict:
        """Read visible controls and stable selectors without clicking or filling."""
        self._navigate(url)
        return self._snapshot()

    def _snapshot(self) -> dict:
        return {
            "url": self.page.url,
            "title": self.page.title(),
            "fields": self.page.evaluate(INSPECT_FIELDS),
            "iframes": len(self.page.frames) - 1,
            "blocked_requests": list(self._blocked),
            "observed_at": _timestamp(),
        }

    def _one(self, selector: str):
        try:
            locator = self.page.locator(f"css={selector}")
            count = locator.count()
            if count != 1 or not locator.is_visible():
                raise BrowserError("ambiguous_field", "Selector must identify one visible element", selector=selector)
            return locator
        except PlaywrightError as exc:
            raise BrowserError("invalid_selector", "Selector must be valid CSS", selector=selector) from exc

    def _field(self, field: BrowserField):
        locator = self._one(field.selector)
        meta = locator.evaluate(FIELD_METADATA)
        kind_matches = {
            "text": meta["tag"] == "textarea"
            or (
                meta["tag"] == "input"
                and meta["type"]
                in {
                    "text",
                    "email",
                    "tel",
                    "url",
                    "search",
                    "number",
                    "date",
                    "month",
                    "week",
                    "time",
                    "datetime-local",
                }
            ),
            "select": meta["tag"] == "select" and not meta["multiple"],
            "checkbox": meta["tag"] == "input" and meta["type"] == "checkbox",
            "radio": meta["tag"] == "input" and meta["type"] == "radio",
            "file": meta["tag"] == "input" and meta["type"] == "file" and not meta["multiple"],
        }
        if not kind_matches[field.kind] or meta["disabled"] or meta["readOnly"]:
            raise BrowserError(
                "unsupported_field", "Control does not match the typed field plan", selector=field.selector
            )
        return locator

    def _check_required(self, plan: BrowserPlan) -> None:
        if len(self.page.frames) > 1:
            raise BrowserError("unsupported_frame", "Embedded forms require a dedicated adapter")
        check = self.page.evaluate(CHECK_REQUIRED, [field.selector for field in plan.fields])
        if check["duplicate"]:
            raise BrowserError("duplicate_target", "Two selectors target the same control")
        if check["conflictingRadios"]:
            raise BrowserError("conflicting_radio_choices", "A radio group must have exactly one planned choice")
        if check["missing"]:
            raise BrowserError(
                "unplanned_required_field", "Required controls are missing from the plan", fields=check["missing"]
            )
        if check["unplanned"]:
            raise BrowserError(
                "unplanned_prefilled_field",
                "Prefilled controls require explicit plan values",
                fields=check["unplanned"],
            )

    def prepare(self, plan: BrowserPlan) -> dict:
        """Fill supported native controls; never click a submit control."""
        self._navigate(plan.url)
        self._file_hashes.clear()
        try:
            for field in plan.fields:
                self._field(field)
            self._check_required(plan)
            for field in plan.fields:
                locator = self._field(field)
                if field.kind == "text":
                    locator.fill(field.value)
                elif field.kind == "select":
                    locator.select_option(value=field.value)
                elif field.kind in {"checkbox", "radio"}:
                    locator.set_checked(field.value)
                else:
                    path = Path(field.value).expanduser().resolve()
                    if not path.is_file():
                        raise BrowserError("file_missing", "Planned attachment does not exist", path=str(path))
                    self._file_hashes[field.selector] = _file_hash(path)
                    locator.set_input_files(str(path))
            self._check_values(plan)
            if any(event["reason"] == "write_before_submit" for event in self._blocked):
                raise BrowserError("unsupported_background_write", "Form attempted a write during preparation")
            self._prepared_digest = _digest(plan)
            self._prepared_url = self.page.url
            return {"status": "prepared", "plan_digest": self._prepared_digest, **self._snapshot()}
        except PlaywrightError as exc:
            raise BrowserError("preparation_failed", "Form could not be filled and verified") from exc

    def _check_values(self, plan: BrowserPlan) -> None:
        self._check_origin(self.page.url)
        if self._prepared_url is not None and self.page.url != self._prepared_url:
            raise BrowserError("page_changed", "The prepared page URL changed before submission")
        self._check_required(plan)
        for field in plan.fields:
            locator = self._field(field)
            if field.kind == "file":
                path = Path(field.value).expanduser().resolve()
                if not path.is_file() or _file_hash(path) != self._file_hashes.get(field.selector):
                    raise BrowserError(
                        "attachment_changed", "Attachment changed after preparation", selector=field.selector
                    )
                selected = locator.evaluate(FILE_SELECTION)
                matches = selected == [{"name": path.name, "size": path.stat().st_size}]
            elif field.kind in {"checkbox", "radio"}:
                matches = locator.is_checked() == field.value
            else:
                matches = locator.input_value() == field.value
            if not matches:
                raise BrowserError(
                    "value_changed", "Observed value differs from the authorized plan", selector=field.selector
                )
            if not locator.evaluate(FORM_VALIDITY):
                raise BrowserError(
                    "invalid_field", "A field fails the browser's native validation", selector=field.selector
                )

    def _receipt(self, plan: BrowserPlan) -> dict | None:
        self._check_origin(self.page.url)
        locator = self.page.locator(f"css={plan.confirmation_selector}")
        if locator.count() != 1 or not locator.is_visible():
            return None
        if not locator.evaluate(RECEIPT_CONTAINER):
            return None
        text = locator.inner_text().strip()
        if plan.confirmation_text not in text or not _explicit_acknowledgement(text):
            return None
        return {
            "url": self.page.url,
            "text": text,
            "selector": plan.confirmation_selector,
            "observed_at": _timestamp(),
            "verification": "observed_page_acknowledgement",
        }

    def submit(self, plan: BrowserPlan) -> dict:
        """Submit exactly once after caller authorization; independently observe receipt.

        Calling this is the trusted caller's submission boundary. A returned
        ``unknown`` must not be retried automatically, even if clicking timed out.
        """
        if self._attempted:
            raise BrowserError("attempt_consumed", "Submission was already attempted; reconcile before retrying")
        if self._prepared_digest != _digest(plan):
            raise BrowserError("plan_not_prepared", "The exact plan must be prepared in this session first")
        if not all((plan.submit_selector, plan.confirmation_selector, plan.confirmation_text)):
            raise BrowserError(
                "missing_confirmation_contract", "Submit and explicit confirmation selectors/text are required"
            )
        if not _explicit_acknowledgement(plan.confirmation_text):
            raise BrowserError(
                "unsupported_confirmation", "Expected text must be an explicit application acknowledgement"
            )
        self._check_values(plan)
        button = self._one(plan.submit_selector)
        if button.evaluate("e => !((e.tagName === 'BUTTON') || (e.tagName === 'INPUT' && e.type === 'submit'))"):
            raise BrowserError("unsupported_submit", "Submission target must be a native button or submit input")
        if self._receipt(plan):
            raise BrowserError("preexisting_confirmation", "Matching confirmation already exists before submission")
        if any(event["reason"] == "write_before_submit" for event in self._blocked):
            raise BrowserError("unsupported_background_write", "Form attempted a write before the submission boundary")
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        attempt_id = uuid4().hex
        before = self.evidence_dir / f"{attempt_id}-before.png"
        after = self.evidence_dir / f"{attempt_id}-after.png"
        self.page.screenshot(path=str(before), full_page=True)
        self._check_values(plan)
        if self._receipt(plan):
            raise BrowserError("preexisting_confirmation", "Confirmation appeared before submission")
        self._attempted = True
        self._writes_enabled = True
        result = {
            "status": "unknown",
            "attempt_id": attempt_id,
            "attempted_at": _timestamp(),
            "plan_digest": _digest(plan),
            "receipt": None,
            "before_screenshot": str(before.resolve()),
        }
        try:
            button.click(timeout=self.timeout_ms, no_wait_after=True)
            deadline = monotonic() + self.timeout_ms / 1000
            while monotonic() < deadline:
                try:
                    receipt = self._receipt(plan)
                except PlaywrightError:
                    # Navigation can replace the execution context while a real
                    # receipt is loading. Keep observing; never click again.
                    receipt = None
                if receipt is not None:
                    result.update(status="submitted", receipt=receipt)
                    break
                self.page.wait_for_timeout(min(100, self.timeout_ms))
            if result["status"] == "unknown":
                result["reason"] = "confirmation_not_observed"
        except (PlaywrightError, BrowserError) as exc:
            result["reason"] = "submission_observation_failed"
            result["error"] = exc.code if isinstance(exc, BrowserError) else type(exc).__name__
        finally:
            self._writes_enabled = False
        try:
            self.page.screenshot(path=str(after), full_page=True)
            result["after_screenshot"] = str(after.resolve())
        except PlaywrightError:
            result.update(status="unknown", reason="evidence_capture_failed")
        evidence = self.evidence_dir / f"{attempt_id}-receipt.json"
        result["evidence_path"] = str(evidence.resolve())
        evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
