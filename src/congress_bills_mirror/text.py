"""Fetch a bill's latest text version.

Text lives as static files on `www.congress.gov`, not behind `api.congress.gov` -- no API key
needed here, only the URL the API's `text` sub-resource hands back.

Only the most recent text version is kept, as `text.xml` -- deliberately not one file per stage
(introduced/reported/engrossed/...). A diffing tool needs the bill's text as it stands *now*; older
stage-by-stage versions sitting alongside it as separate files risk being mistaken for "the"
current text. The same principle already applies to `usc/`: that mirror doesn't keep old release
points' text as separate files either, just the current snapshot -- history lives in `git log`, not
in coexisting files. See BILLS-MIRROR-NOTES.md.
"""

from __future__ import annotations

import logging
import shutil
import urllib.request
from pathlib import Path
from typing import Any

from congress_bills_mirror.client import USER_AGENT
from http_retry.fetch import fetch_with_retry

logger = logging.getLogger(__name__)

_WANTED_FORMAT_TYPE = "Formatted XML"
TEXT_FILENAME = "text.xml"


def _xml_format_url(version: dict[str, Any]) -> str | None:
    # `or []`, not `.get("formats", [])` -- same present-but-null gotcha as this module's own date
    # handling below, and `client.py`'s pagination helpers: a `.get` default only covers a missing key.
    for fmt in version.get("formats") or []:
        if fmt.get("type") == _WANTED_FORMAT_TYPE:
            url = fmt.get("url")
            return str(url) if url is not None else None
    return None


def _latest_version(text_versions: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not text_versions:
        return None
    # `.get("date", "")` isn't enough -- confirmed live, some versions have `"date": null` in the
    # real API response (key present, value None), which `.get`'s default only covers when the key
    # is *absent*. `or ""` catches both "missing" and "present but null".
    return max(text_versions, key=lambda version: version.get("date") or "")


def sync_latest_text(text_versions: list[dict[str, Any]], dest_dir: Path) -> Path | None:
    """Download the most recent text version's XML into `dest_dir` as `text.xml`.

    Also removes any other `*.xml` file already in `dest_dir` -- self-healing cleanup for bills
    synced before this "latest only" rule, or if `dest_dir` somehow has a stale file under it.

    Returns None (nothing written) if there are no text versions yet, or the latest one has no
    "Formatted XML" format (seen on some resolution/procedural types) -- not an error, mirroring
    `uscode_mirror.download`'s `ReservedTitleError` philosophy of "expected, not a failure."
    """
    version = _latest_version(text_versions)
    if version is None:
        logger.info("No text versions yet -- skipping")
        return None

    url = _xml_format_url(version)
    if url is None:
        logger.info("No %s format for the latest text version (%r) -- skipping", _WANTED_FORMAT_TYPE, version.get("type"))
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / TEXT_FILENAME
    for stale in dest_dir.glob("*.xml"):
        if stale != dest_path:
            stale.unlink()
            logger.info("Removed stale text version %s", stale)

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    def _consume(response: Any) -> None:
        with dest_path.open("wb") as out_file:
            shutil.copyfileobj(response, out_file)

    fetch_with_retry(request, _consume, describe=url)
    logger.info("Downloaded %s -> %s", url, dest_path)
    return dest_path
