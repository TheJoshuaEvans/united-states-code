"""Discover U.S. Code release points and the download URLs for their per-title USLM XML zips.

A release point is OLRC's term for a snapshot of the Code taken immediately after a given
Public Law is incorporated. This module only figures out *where* the current full corpus can be
downloaded from `uscode.house.gov` — it does not fetch or extract anything itself.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from http_retry.fetch import fetch_with_retry

logger = logging.getLogger(__name__)

RELEASE_POINTS_INDEX_URL = "https://uscode.house.gov/download/priorreleasepoints.htm"
RELEASE_POINT_BASE_URL = "https://uscode.house.gov/download/releasepoints/us/pl"


class ReleasePageFormatError(RuntimeError):
    """A uscode.house.gov page didn't match the markup this module expects.

    Raised instead of silently returning an empty list, since an empty result here almost
    certainly means OLRC changed their site's HTML rather than that there's genuinely nothing to
    find -- these pages always list at least one entry when the module was written.
    """


@dataclass(frozen=True, order=True)
class ReleasePoint:
    """A single OLRC release point, identified by Congress number and Public Law number."""

    congress: int
    law: int

    @property
    def label(self) -> str:
        return f"{self.congress}-{self.law}"


_RELEASE_POINT_HREF_RE = re.compile(r"releasepoints/us/pl/(\d+)/(\d+)/")


class _ReleasePointLinkParser(HTMLParser):
    """Collects release points from anchor hrefs.

    OLRC keeps not-yet-published release points on the page but wrapped in an HTML comment;
    HTMLParser never fires `handle_starttag` for markup inside a comment, so those are skipped
    automatically rather than needing to be filtered out explicitly.
    """

    def __init__(self) -> None:
        super().__init__()
        self.release_points: list[ReleasePoint] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name != "href" or value is None:
                continue
            match = _RELEASE_POINT_HREF_RE.search(value)
            if match:
                self.release_points.append(ReleasePoint(int(match.group(1)), int(match.group(2))))


def parse_release_points(html: str) -> list[ReleasePoint]:
    """Parse the prior-release-points index page into a de-duplicated, chronological list."""
    parser = _ReleasePointLinkParser()
    parser.feed(html)
    release_points = sorted(set(parser.release_points))
    if not release_points:
        logger.error(
            "Parsed zero release points from the index page (expected "
            '<a href="releasepoints/us/pl/{congress}/{law}/..."> links) -- '
            "uscode.house.gov's markup may have changed"
        )
        raise ReleasePageFormatError(
            "No release points found while parsing the prior-release-points index page; the page's markup may have changed"
        )
    return release_points


def _decode_utf8(response: Any) -> str:
    raw: bytes = response.read()
    return raw.decode("utf-8")


def fetch_release_points_index() -> str:
    """Fetch the raw HTML of the prior-release-points index page."""
    return fetch_with_retry(RELEASE_POINTS_INDEX_URL, _decode_utf8, describe=RELEASE_POINTS_INDEX_URL)


def latest_release_point(release_points: Sequence[ReleasePoint]) -> ReleasePoint:
    """Return the most recent release point in a list, e.g. the output of `parse_release_points`."""
    if not release_points:
        raise ValueError("No release points to choose from")
    return max(release_points)


def title_zip_url(release_point: ReleasePoint, title: str) -> str:
    """Build the download URL for one title's USLM XML zip at a given release point."""
    return f"{RELEASE_POINT_BASE_URL}/{release_point.congress}/{release_point.law}/xml_usc{title}@{release_point.label}.zip"


def release_point_page_url(release_point: ReleasePoint) -> str:
    """Build the URL of a release point's own download page, which lists exactly the titles it publishes."""
    return f"{RELEASE_POINT_BASE_URL}/{release_point.congress}/{release_point.law}/usc-rp@{release_point.label}.htm"


_TITLE_HREF_RE = re.compile(r"xml_usc([0-9A-Za-z]+)@(\d+-\d+)\.zip$")
_TITLE_SORT_RE = re.compile(r"(\d+)([a-zA-Z]*)")


def _title_sort_key(title: str) -> tuple[int, str]:
    match = _TITLE_SORT_RE.fullmatch(title)
    if not match:
        return (0, title)
    number, suffix = match.groups()
    return (int(number), suffix)


class _TitleLinkParser(HTMLParser):
    """Collects title codes from a release point page's own `xml_usc{title}@{label}.zip` links.

    Reading titles fresh off the page (rather than a hardcoded list) is what lets a Congress
    adding or retiring a title - as has happened before, e.g. Titles 51-54 - show up correctly
    without a code change here. The combined "All" corpus zip and other formats (htm/pdf/pcc) of
    the same title are excluded, since they aren't per-title XML.
    """

    def __init__(self, label: str) -> None:
        super().__init__()
        self._label = label
        self.titles: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name != "href" or value is None:
                continue
            match = _TITLE_HREF_RE.search(value)
            if match and match.group(2) == self._label and match.group(1).lower() != "all":
                self.titles.append(match.group(1))


def parse_titles(html: str, release_point: ReleasePoint) -> list[str]:
    """Parse a release point's own page for exactly the titles it publishes at that point."""
    parser = _TitleLinkParser(release_point.label)
    parser.feed(html)
    titles = sorted(set(parser.titles), key=_title_sort_key)
    if not titles:
        logger.error(
            "Parsed zero titles from release point %s's page (expected "
            '<a href="xml_usc{title}@%s.zip"> links) -- uscode.house.gov\'s markup may have changed',
            release_point.label,
            release_point.label,
        )
        raise ReleasePageFormatError(
            f"No titles found while parsing release point {release_point.label}'s page; the page's markup may have changed"
        )
    return titles


def fetch_release_point_page(release_point: ReleasePoint) -> str:
    """Fetch the raw HTML of a release point's own download page."""
    url = release_point_page_url(release_point)
    return fetch_with_retry(url, _decode_utf8, describe=url)


def full_code_urls(release_point: ReleasePoint, titles: Sequence[str]) -> list[str]:
    """Return the download URL for each given title's USLM XML zip at the given release point."""
    return [title_zip_url(release_point, title) for title in titles]


def get_latest_full_code_urls() -> list[str]:
    """Fetch the index, find the latest live release point, and return its title URLs.

    The set of titles is read fresh from that release point's own page, not hardcoded.
    """
    index_html = fetch_release_points_index()
    release_points = parse_release_points(index_html)
    point = latest_release_point(release_points)
    page_html = fetch_release_point_page(point)
    titles = parse_titles(page_html, point)
    return full_code_urls(point, titles)
