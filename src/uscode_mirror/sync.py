"""Central entry point for the full download -> chunk -> render pipeline.

Run this end to end instead of invoking `download`, `chunk`, `render_json`, and `render_txt`
individually. It only does real work when OLRC has published a release point newer than the one
already mirrored in `usc_dir` -- otherwise it's a no-op, which is what makes it safe to run on a
schedule. When a new release point *is* found, the whole corpus is re-downloaded and re-parsed
from scratch; nothing here tries to figure out which titles actually changed and update just
those, since a release point only records which titles it touched, not which sections moved,
renumbered, or dropped within them -- a full reparse is the only way to guarantee `usc_dir`
doesn't end up with stale files a partial update would've missed.
"""

from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path

from uscode_mirror import chunk, download, release_points, render_json, render_txt

logger = logging.getLogger(__name__)

DEFAULT_RAW_DIR = Path("raw")
DEFAULT_USC_DIR = Path("usc")

_RELEASE_POINT_MARKER_NAME = ".release-point"


def _marker_path(usc_dir: Path) -> Path:
    return usc_dir / _RELEASE_POINT_MARKER_NAME


def synced_release_point_label(usc_dir: Path) -> str | None:
    """The release point label (e.g. "119-100") `usc_dir` was last fully synced to.

    None if `usc_dir` has never been synced by this module (no marker file yet).
    """
    marker = _marker_path(usc_dir)
    if not marker.exists():
        return None
    return marker.read_text().strip() or None


def _write_synced_release_point_label(usc_dir: Path, label: str) -> None:
    usc_dir.mkdir(parents=True, exist_ok=True)
    _marker_path(usc_dir).write_text(label + "\n")


def sync(raw_dir: Path = DEFAULT_RAW_DIR, usc_dir: Path = DEFAULT_USC_DIR, force: bool = False) -> bool:
    """Run the full pipeline if a new release point is live; return whether it actually ran.

    Discovers the latest release point and compares it against the one recorded in `usc_dir`'s
    marker file, skipping the whole download/chunk/render pass when they already match (unless
    `force` is set). When it does run, `raw_dir` and `usc_dir` are wiped and rebuilt from scratch
    -- see the module docstring for why this isn't an incremental update.
    """
    index_html = release_points.fetch_release_points_index()
    points = release_points.parse_release_points(index_html)
    latest = release_points.latest_release_point(points)

    synced_label = synced_release_point_label(usc_dir)
    if not force and synced_label == latest.label:
        logger.info("Already current at release point %s -- skipping sync", latest.label)
        return False

    logger.info("Syncing to release point %s (previously %s)", latest.label, synced_label)

    page_html = release_points.fetch_release_point_page(latest)
    titles = release_points.parse_titles(page_html, latest)
    urls = release_points.full_code_urls(latest, titles)

    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    if usc_dir.exists():
        shutil.rmtree(usc_dir)

    xml_paths = download.fetch_all_titles(urls, raw_dir)
    logger.info("Downloaded %d title XML file(s)", len(xml_paths))

    chunked_paths = chunk.chunk_all_titles(raw_dir, usc_dir)
    logger.info("Chunked %d citation file(s)", len(chunked_paths))

    json_paths = render_json.render_all_json(usc_dir)
    logger.info("Rendered %d JSON file(s)", len(json_paths))

    txt_paths = render_txt.render_all_txt(usc_dir)
    logger.info("Rendered %d TXT file(s)", len(txt_paths))

    _write_synced_release_point_label(usc_dir, latest.label)
    logger.info("Sync complete at release point %s", latest.label)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR, help="Directory to download/extract title XML into")
    parser.add_argument("--usc-dir", type=Path, default=DEFAULT_USC_DIR, help="Directory to write chunked XML/JSON/TXT into")
    parser.add_argument("--force", action="store_true", help="Re-sync even if already current at the latest release point")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sync(raw_dir=args.raw_dir, usc_dir=args.usc_dir, force=args.force)


if __name__ == "__main__":
    main()
