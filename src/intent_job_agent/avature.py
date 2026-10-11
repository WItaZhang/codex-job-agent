"""Read-only Avature career sites (spec §10 rules 39-48), normalized into untagged jobs.

An Avature site serves plain HTML: a paginated search listing (`<board>/SearchJobs/?jobOffset=N`) and one
detail page per posting (`<board>/JobDetail/<slug>/<numeric id>`). The page structure was checked read-only on
jobs.lenovo.com and bloomberg.avature.net on 2026-10-10.

The listing must be read completely, or the whole site fails: a partial listing would make closed postings
look open or open ones look closed. Only links under the board's own host and path are followed; redirects are
never followed. Everything read is untrusted job content: it is shown to the agent for tagging only.
"""

import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Tag

from .discovery import DiscoveryError, _html_to_text, _url, stable_job_id
from .domain import Job

PROVIDER = "avature"
_LEGEND = re.compile(r"(\d[\d,.]*)\s*[-–]\s*(\d[\d,.]*)\D*?(\d[\d,.]*\+?)")
_LABELLED = re.compile(r"([^:]{1,40}):\s*(.*)\Z", re.S)


@dataclass(frozen=True)
class ResultsPage:
    """One listing page: its job cards, the offset its Next link asks for, and the legend's total."""

    jobs: list[Job]
    next_offset: int | None
    total: str  # as the site wrote it, e.g. "999+" or "1027"
    empty: bool  # a "No jobs found" page


def site_url(board: str) -> str:
    """The canonical site root (HTTPS, public host, default port, no query); raises DiscoveryError."""
    if not isinstance(board, str) or not board.strip().casefold().startswith("https://"):
        raise DiscoveryError("An Avature board is the site's HTTPS address, e.g. https://jobs.example.com/careers")
    parsed = urlsplit(_url(board, "board"))
    if parsed.query or urlsplit(board.strip()).fragment:
        raise DiscoveryError("An Avature board address must not carry query parameters")
    return urlunsplit(("https", parsed.hostname, parsed.path.rstrip("/"), "", ""))


def _number(text: str) -> int:
    return int(re.sub(r"[,.]", "", text))


def _norm_label(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")


def _posting_id(board: str, url: str) -> tuple[str, str]:
    """(canonical job URL, numeric id) for a JobDetail link on the board's own host and path."""
    site = urlsplit(board)
    link = urlsplit(url)
    if link.scheme != "https" or link.hostname != site.hostname or link.port not in (None, 443):
        raise DiscoveryError("A job link points outside the board's site")
    match = re.fullmatch(re.escape(site.path) + r"/JobDetail/[^/]+/(\d+)/?", link.path)
    if match is None:
        raise DiscoveryError("A job link is not a JobDetail link under the board's address")
    return urlunsplit(("https", site.hostname, link.path, "", "")), match.group(1)


def _get(client: httpx.Client, url: str, params: dict | None, timeout: float) -> str:
    try:
        response = client.get(url, params=params, timeout=timeout, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise DiscoveryError(f"Could not read {url}: {type(exc).__name__}") from exc
    if response.status_code != 200:
        # Redirects are refused too: they would lead off the checked address.
        raise DiscoveryError(f"Could not read {url}: HTTP {response.status_code}")
    return response.text


def _card(board: str, company: str, article: Tag) -> Job:
    heading = article.select_one(".article__header__text__title")
    title = heading.get_text(" ", strip=True) if heading else ""
    anchor = heading.find("a", href=True) if heading else None
    if not title or anchor is None:
        raise DiscoveryError("A job card has no title or no JobDetail link; the page layout may have changed")
    url, number = _posting_id(board, urljoin(board + "/", anchor["href"]))

    attributes: dict[str, str] = {}
    category = article.select_one(".article__header__text > .paragraph")
    if category and (text := category.get_text(" ", strip=True)):
        attributes["category"] = text
    subtitle = article.select_one(".article__header__text__subtitle")
    items: list[str] = []
    if subtitle is not None:
        spans = subtitle.find_all("span")
        texts = [span.get_text(" ", strip=True) for span in spans] if spans else subtitle.get_text("\n").splitlines()
        items = [text.strip() for text in texts if text.strip()]
    location = ""
    if items and not _LABELLED.match(items[0]):
        location = items.pop(0)
    for item in items:
        if labelled := _LABELLED.match(item):
            attributes.setdefault(_norm_label(labelled.group(1)), labelled.group(2).strip())
    if items:
        attributes["listing"] = "; ".join(items)

    return Job(
        id=stable_job_id(PROVIDER, f"{urlsplit(board).hostname}:{number}"),
        source=PROVIDER,
        board=board,
        title=title,
        company=company,
        url=url,
        location=location,
        attributes=attributes,
        tags=[],
    )


def parse_results(board: str, company: str, page: str) -> ResultsPage:
    """Parse one listing page; anything but a well-formed results page raises DiscoveryError."""
    soup = BeautifulSoup(page, "html.parser")
    legend = soup.select_one(".list-controls__text__legend")
    match = _LEGEND.search(legend.get_text(" ", strip=True)) if legend else None
    if match is None:
        raise DiscoveryError("Not an Avature results page (no result count); the site may have returned an error")
    first, last, total = _number(match.group(1)), _number(match.group(2)), match.group(3)
    empty = last < first
    cards = soup.select("article.article--result")
    jobs = [] if empty else [_card(board, company, article) for article in cards]
    if not empty and not jobs:
        raise DiscoveryError("A results page lists no job cards; the page layout may have changed")

    next_offset = None
    link = soup.select_one("a.paginationNextLink[href]")
    if link is not None:
        offsets = parse_qs(urlsplit(link["href"]).query).get("jobOffset", [])
        if len(offsets) != 1 or not offsets[0].isdigit():
            raise DiscoveryError("The Next link carries no job offset")
        next_offset = int(offsets[0])
    return ResultsPage(jobs=jobs, next_offset=next_offset, total=total, empty=empty)


def _page(board: str, company: str, offset: int, timeout: float, client: httpx.Client) -> ResultsPage:
    return parse_results(board, company, _get(client, f"{board}/SearchJobs/", {"jobOffset": offset}, timeout))


def first_page(board: str, *, timeout_seconds: float, client: httpx.Client | None = None) -> ResultsPage:
    """Only the first listing page, for a preview: one request, no details."""
    board = site_url(board)
    if client is None:
        with httpx.Client() as owned:
            return first_page(board, timeout_seconds=timeout_seconds, client=owned)
    return _page(board, "", 0, timeout_seconds, client)


def list_site(
    board: str,
    *,
    company: str,
    timeout_seconds: float,
    request_delay_seconds: float,
    max_pages: int,
    client: httpx.Client | None = None,
) -> list[Job]:
    """Every posting on the site, from the listing cards only (no description yet).

    Pages are followed by offset until a results page without a Next link. Raises DiscoveryError when the
    listing cannot be shown to be complete: an error page, an empty page after the first, a Next offset that
    does not follow from the page, more than `max_pages` pages, or a final total that does not match.
    """
    board = site_url(board)
    if client is None:
        with httpx.Client() as owned:
            return list_site(
                board,
                company=company,
                timeout_seconds=timeout_seconds,
                request_delay_seconds=request_delay_seconds,
                max_pages=max_pages,
                client=owned,
            )
    company = company.strip() or urlsplit(board).hostname
    jobs: dict[str, Job] = {}
    offset = 0
    for pages in range(max_pages + 1):
        if pages == max_pages:
            raise DiscoveryError(f"The listing has more than {max_pages} pages; refusing a partial listing")
        if pages:
            time.sleep(request_delay_seconds)
        page = _page(board, company, offset, timeout_seconds, client)
        if page.empty:
            if offset == 0:
                return []  # the site has no postings right now
            # Just past the end, Avature serves "No jobs found" with a Next link back to the start.
            raise DiscoveryError(f"Offset {offset} returned no jobs before the listing ended")
        for job in page.jobs:
            jobs.setdefault(job.id, job)  # a posting shifted across pages while reading appears twice
        if page.next_offset is None:
            break
        if page.next_offset != offset + len(page.jobs):
            raise DiscoveryError(f"Pagination skipped from offset {offset} to {page.next_offset}")
        offset = page.next_offset
    if not page.total.endswith("+") and _number(page.total) != len(jobs):
        raise DiscoveryError(f"The site reports {page.total} jobs but {len(jobs)} were read")
    return list(jobs.values())


def _field_text(field: Tag) -> str:
    value = field.select_one(".article__content__view__field__value")
    return _html_to_text(str(value if value is not None else field))


def parse_detail(page: str) -> tuple[dict[str, str], str]:
    """Labelled fields as attributes, and the unlabelled paragraphs with their section headings as the
    description, in page order. Hidden fields are skipped and identical paragraphs kept once."""
    soup = BeautifulSoup(page, "html.parser")
    articles = soup.select("article.article--details")
    if not articles:
        raise DiscoveryError("Not an Avature job page; the site may have returned an error")
    attributes: dict[str, str] = {}
    blocks: list[str] = []
    seen: set[str] = set()
    for article in articles:
        heading = article.select_one(".article__header__text__title")
        if heading is not None and (text := heading.get_text(" ", strip=True)):
            blocks.append(text)
        for field in article.select(".article__content__view__field"):
            if "visibility--hidden" in field.get("class", []):
                continue
            label = field.select_one(".article__content__view__field__label")
            text = _field_text(field)
            if label is not None:
                key = _norm_label(label.get_text(" ", strip=True))
                if key and text:
                    attributes[key] = text
            elif text and text not in seen:
                seen.add(text)
                blocks.append(text)
    return attributes, "\n".join(blocks)


def read_detail(job: Job, *, timeout_seconds: float, client: httpx.Client | None = None) -> Job:
    """The job with its detail page's fields added to its attributes and its description filled in."""
    board = site_url(job.board)
    url, _ = _posting_id(board, job.url)  # checked before any request
    if client is None:
        with httpx.Client() as owned:
            return read_detail(job, timeout_seconds=timeout_seconds, client=owned)
    attributes, description = parse_detail(_get(client, url, None, timeout_seconds))
    return job.model_copy(update={"attributes": {**job.attributes, **attributes}, "description": description})
