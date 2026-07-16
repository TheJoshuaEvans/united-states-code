"""Download and extract the per-title USLM XML zips that `release_points` finds URLs for.

This module only fetches a zip from `uscode.house.gov` into `raw/` and unpacks the single XML
member it contains -- it does not parse or otherwise interpret the extracted XML.
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from http_retry.fetch import fetch_with_retry

logger = logging.getLogger(__name__)


class UnexpectedZipContentsError(RuntimeError):
    """A downloaded zip didn't contain exactly one XML member, as every USLM title zip should."""


class ReservedTitleError(RuntimeError):
    """A title's download URL didn't return a real zip archive.

    OLRC's release-point pages list `[XML]` download links for every title number, including
    titles that are formally "[Reserved]" in the Code (e.g. Title 53, held open for future use)
    and have no content published. Those links 200 with an HTML error page instead of a zip, so
    this is an expected outcome to route around, not a fetch failure.
    """


def download_zip(url: str, raw_dir: Path) -> Path:
    """Stream a title zip from `url` into `raw_dir`, named after the URL's own filename."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest_path = raw_dir / url.rsplit("/", 1)[-1]

    def _consume(response: Any) -> None:
        with dest_path.open("wb") as out_file:
            shutil.copyfileobj(response, out_file)

    fetch_with_retry(url, _consume, describe=url)
    logger.info("Downloaded %s -> %s", url, dest_path)
    return dest_path


def extract_xml(zip_path: Path, raw_dir: Path) -> Path:
    """Unpack the single XML member of a title zip into `raw_dir`."""
    if not zipfile.is_zipfile(zip_path):
        logger.warning("%s is not a real zip archive -- likely a reserved title with no published content", zip_path)
        raise ReservedTitleError(f"{zip_path} is not a real zip archive (likely a reserved title)")
    with zipfile.ZipFile(zip_path) as archive:
        xml_names = [name for name in archive.namelist() if name.endswith(".xml")]
        if len(xml_names) != 1:
            logger.error("Expected exactly one .xml member in %s, found %s", zip_path, xml_names)
            raise UnexpectedZipContentsError(f"Expected exactly one .xml member in {zip_path}, found {xml_names}")
        raw_dir.mkdir(parents=True, exist_ok=True)
        extracted_path = Path(archive.extract(xml_names[0], raw_dir))
        logger.info("Extracted %s -> %s", zip_path, extracted_path)
        return extracted_path


def fetch_title_xml(url: str, raw_dir: Path) -> Path:
    """Download and unpack one title's zip, returning the path to its extracted XML."""
    zip_path = download_zip(url, raw_dir)
    return extract_xml(zip_path, raw_dir)


def fetch_all_titles(urls: Sequence[str], raw_dir: Path) -> list[Path]:
    """Download and unpack every given title zip, one at a time, returning their XML paths.

    Reserved titles (no content published at this release point) are skipped rather than
    aborting the whole batch -- see `ReservedTitleError`.
    """
    xml_paths = []
    for url in urls:
        try:
            xml_paths.append(fetch_title_xml(url, raw_dir))
        except ReservedTitleError:
            logger.info("Skipping %s: reserved title, no content to extract", url)
    return xml_paths
