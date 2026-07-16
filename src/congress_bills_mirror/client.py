"""Thin `urllib`-based client for `api.congress.gov` -- GET, JSON, pagination, rate-limit backoff.

Every call needs the `CONGRESS_API_KEY` environment variable (never accepted as a CLI argument,
never logged) -- see BILLS-MIRROR-NOTES.md for why the key lives only in CI secrets / a local,
gitignored `.env`, never in anything a client of this mirror touches.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

from http_retry.fetch import fetch_with_retry

logger = logging.getLogger(__name__)

API_BASE = "https://api.congress.gov/v3"

# congress.gov's gateway intermittently 403s the default `Python-urllib/3.x` User-Agent (looks
# like bot-detection, confirmed live: identical requests succeed with any other UA) -- identifying
# the client explicitly avoids it, and is good API etiquette regardless.
USER_AGENT = "congress-bills-mirror (https://github.com/TheJoshuaEvans/united-states-code)"


class MissingApiKeyError(RuntimeError):
    """`CONGRESS_API_KEY` isn't set in the environment."""


def _api_key() -> str:
    key = os.environ.get("CONGRESS_API_KEY")
    if not key:
        raise MissingApiKeyError("CONGRESS_API_KEY environment variable is not set")
    return key


def _fetch(url: str) -> dict[str, Any]:
    """GET `url` (adding the API key if not already present) and return the parsed JSON body.

    Retrying on rate limiting and transient connection failures is handled by
    `http_retry.fetch_with_retry`; see there for the policy.
    """
    if "api_key=" not in url:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}api_key={_api_key()}"
    # congress.gov's own `pagination.next` links come back with unencoded literal spaces (e.g.
    # `sort=updateDate asc`) -- fine for a page we built ourselves via `urlencode`, which escapes
    # this correctly, but a `next` URL is used verbatim from their response, and Python's
    # http.client rejects a raw space in a URL outright. Confirmed live: this crashed mid-sync.
    url = url.replace(" ", "%20")
    redacted_url = url.replace(_api_key(), "***")

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return fetch_with_retry(
        request,
        lambda response: json.loads(response.read().decode("utf-8")),
        describe=redacted_url,
    )


def _get(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    """GET a single (non-paginated) resource at `path` (e.g. "/congress/current")."""
    query = {"format": "json", **(params or {})}
    url = f"{API_BASE}{path}?{urllib.parse.urlencode(query)}"
    return _fetch(url)


def _next_page_url(page: dict[str, Any]) -> str | None:
    # `.get("pagination", {})` isn't enough on its own -- confirmed live elsewhere in this API,
    # a present-but-null value defeats a `.get` default (that only covers a missing key). `or {}`
    # catches both.
    pagination: dict[str, Any] = page.get("pagination") or {}
    next_url = pagination.get("next")
    return str(next_url) if next_url is not None else None


def iter_pages(path: str, params: dict[str, str] | None = None) -> Iterator[dict[str, Any]]:
    """Yield each page's parsed JSON body from `path`, following `pagination.next` to exhaustion."""
    page_params = {"limit": "250", **(params or {})}
    page = _get(path, page_params)
    yield page
    next_url = _next_page_url(page)
    while next_url:
        page = _fetch(next_url)
        yield page
        next_url = _next_page_url(page)


def get_current_congress() -> int:
    """Return the number of the Congress currently in session, per `/congress/current`."""
    return int(_get("/congress/current")["congress"]["number"])


def get_bill_detail(congress: int, bill_type: str, number: str) -> dict[str, Any]:
    """Fetch one bill's detail payload -- title, sponsors, latestAction, `laws` (if enacted), etc."""
    bill: dict[str, Any] = _get(f"/bill/{congress}/{bill_type}/{number}")["bill"]
    return bill


def iter_bill_summaries(congress: int, from_date_time: str) -> Iterator[dict[str, Any]]:
    """Yield lightweight bill entries (type/number/updateDate/...) updated at/after `from_date_time`.

    This is the `/bill/{congress}` list endpoint's own shape -- it does *not* include the `laws`
    field, so a full `get_bill_detail` call is still needed per bill to check enactment.
    """
    params = {"fromDateTime": from_date_time, "sort": "updateDate asc"}
    for page in iter_pages(f"/bill/{congress}", params):
        yield from page.get("bills") or []


def _get_bill_subresource(congress: int, bill_type: str, number: str, subresource: str, key: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in iter_pages(f"/bill/{congress}/{bill_type}/{number}/{subresource}"):
        items.extend(page.get(key) or [])
    return items


def get_cosponsors(congress: int, bill_type: str, number: str) -> list[dict[str, Any]]:
    return _get_bill_subresource(congress, bill_type, number, "cosponsors", "cosponsors")


def get_committees(congress: int, bill_type: str, number: str) -> list[dict[str, Any]]:
    return _get_bill_subresource(congress, bill_type, number, "committees", "committees")


def get_summaries(congress: int, bill_type: str, number: str) -> list[dict[str, Any]]:
    return _get_bill_subresource(congress, bill_type, number, "summaries", "summaries")


def get_text_versions(congress: int, bill_type: str, number: str) -> list[dict[str, Any]]:
    return _get_bill_subresource(congress, bill_type, number, "text", "textVersions")
