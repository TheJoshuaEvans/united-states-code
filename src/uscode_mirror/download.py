"""Download and extract the per-title USLM XML zips that `release_points` finds URLs for.

This module only fetches a zip from `uscode.house.gov` into `raw/` and unpacks the single XML
member it contains -- it does not parse or otherwise interpret the extracted XML.
"""

from __future__ import annotations

import logging
import shutil
import urllib.request
import zipfile
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger(__name__)


class UnexpectedZipContentsError(RuntimeError):
    """A downloaded zip didn't contain exactly one XML member, as every USLM title zip should."""


def download_zip(url: str, raw_dir: Path) -> Path:
    """Stream a title zip from `url` into `raw_dir`, named after the URL's own filename."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest_path = raw_dir / url.rsplit("/", 1)[-1]
    with urllib.request.urlopen(url) as response, dest_path.open("wb") as out_file:
        shutil.copyfileobj(response, out_file)
    return dest_path


def extract_xml(zip_path: Path, raw_dir: Path) -> Path:
    """Unpack the single XML member of a title zip into `raw_dir`."""
    with zipfile.ZipFile(zip_path) as archive:
        xml_names = [name for name in archive.namelist() if name.endswith(".xml")]
        if len(xml_names) != 1:
            logger.error("Expected exactly one .xml member in %s, found %s", zip_path, xml_names)
            raise UnexpectedZipContentsError(f"Expected exactly one .xml member in {zip_path}, found {xml_names}")
        raw_dir.mkdir(parents=True, exist_ok=True)
        return Path(archive.extract(xml_names[0], raw_dir))


def fetch_title_xml(url: str, raw_dir: Path) -> Path:
    """Download and unpack one title's zip, returning the path to its extracted XML."""
    zip_path = download_zip(url, raw_dir)
    return extract_xml(zip_path, raw_dir)


def fetch_all_titles(urls: Sequence[str], raw_dir: Path) -> list[Path]:
    """Download and unpack every given title zip, one at a time, returning their XML paths."""
    return [fetch_title_xml(url, raw_dir) for url in urls]
