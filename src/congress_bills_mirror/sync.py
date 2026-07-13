"""Central entry point for the bills mirror sync.

Mirrors bill status/cosponsors/committees/summaries/text from `api.congress.gov` for the current
Congress only. Status, cosponsors, committees, and summaries are consolidated into one
`meta.json` per bill (revised 2026-07-13; see BILLS-MIRROR-NOTES.md) -- two files per bill,
`meta.json` and `text.xml`, not five. Every bill the incremental crawl touches gets written,
regardless of whether it's since become law -- enacted bills are *kept*, not pruned, specifically
so a consumer can tell "this bill hasn't been synced yet" apart from "this bill was synced and has
since become law" by checking `meta.json`'s own `status.laws` field, rather than both cases looking
identical (nothing on disk). A per-Congress `index.json` (added 2026-07-14) lists every mirrored
bill sorted by most recent real legislative action, rebuilt from what's on disk at the end of every
run -- see `_build_index` for why it sorts on `latestAction`, not `updateDate`. `index-7d.json` and
`index-30d.json` (added 2026-07-14) are the same data windowed to the last 7/30 days, so a consumer
wanting "what's new" doesn't have to download the full index once it grows to Congress-sized scale.

Bootstrap and steady-state incremental sync are the *same* `fromDateTime`-filtered crawl against
`/bill/{congress}`; a fresh mirror (or one that just rolled into a new Congress) simply starts that
crawl from a fixed cutoff instead of a stored watermark, so it doesn't have to re-fetch the full
~17k-bill Congress from scratch. See `INITIAL_SYNC_CUTOFF` below.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from congress_bills_mirror import client, text

logger = logging.getLogger(__name__)

DEFAULT_BILLS_DIR = Path("bills")
META_FILENAME = "meta.json"
INDEX_FILENAME = "index.json"

# (filename, days) for the windowed indexes alongside the full one -- see `_write_indexes`.
_INDEX_WINDOWS = (("index-7d.json", 7), ("index-30d.json", 30))

# Filenames a bill's own directory may still carry from before status/cosponsors/committees/
# summaries were consolidated into one `meta.json` (revised 2026-07-13) -- cleaned up on next sync
# so an old bill self-heals into the new layout without a separate migration step.
_LEGACY_FILENAMES = ("status.json", "cosponsors.json", "committees.json", "summaries.json")

# Arbitrary starting point for a fresh mirror or a new Congress -- chosen only to keep the first
# sync small, not for correctness. A bill last touched before this date and never updated again is
# permanently invisible to the mirror; anything still moving gets an updateDate bump and is picked
# up the moment it does.
INITIAL_SYNC_CUTOFF = "2026-07-11T00:00:00Z"

_CONGRESS_MARKER_NAME = ".congress"
_LAST_SYNC_MARKER_NAME = ".last-sync"


def _congress_marker_path(bills_dir: Path) -> Path:
    return bills_dir / _CONGRESS_MARKER_NAME


def _last_sync_marker_path(bills_dir: Path) -> Path:
    return bills_dir / _LAST_SYNC_MARKER_NAME


def synced_congress(bills_dir: Path) -> int | None:
    """The Congress number `bills_dir` was last synced against, or None if never synced."""
    marker = _congress_marker_path(bills_dir)
    if not marker.exists():
        return None
    value = marker.read_text().strip()
    return int(value) if value else None


def synced_last_sync(bills_dir: Path) -> str | None:
    """The `fromDateTime` watermark `bills_dir` was last synced through, or None if never synced."""
    marker = _last_sync_marker_path(bills_dir)
    if not marker.exists():
        return None
    return marker.read_text().strip() or None


def _write_markers(bills_dir: Path, congress: int, last_sync: str) -> None:
    bills_dir.mkdir(parents=True, exist_ok=True)
    _congress_marker_path(bills_dir).write_text(f"{congress}\n")
    _last_sync_marker_path(bills_dir).write_text(f"{last_sync}\n")


@dataclass(frozen=True)
class SyncResult:
    written: int
    enacted: int
    rolled_over: bool


def _bill_dir(bills_dir: Path, congress: int, bill_type: str, number: str) -> Path:
    return bills_dir / str(congress) / bill_type.lower() / number


def _sync_one_bill(bills_dir: Path, congress: int, bill_type: str, number: str) -> bool:
    """Fetch one bill's detail, sub-resources, and text, and write all of it.

    Written unconditionally, whether or not the bill has since become law -- see this module's
    docstring for why enacted bills are kept rather than pruned. Returns True if the bill's own
    `laws` field is non-empty (for the caller's summary count), False otherwise.
    """
    # The list endpoint's own `type` field comes back uppercase ("HR"); the detail endpoint
    # tolerates that but sub-resource paths (cosponsors/committees/summaries/text) don't -- so
    # lowercase once here, at the single place that talks to every one of these endpoints.
    bill_type = bill_type.lower()
    detail = client.get_bill_detail(congress, bill_type, number)
    bill_dir = _bill_dir(bills_dir, congress, bill_type, number)
    bill_dir.mkdir(parents=True, exist_ok=True)

    meta: dict[str, Any] = {"status": detail}
    for key, fetch_items in (
        ("cosponsors", client.get_cosponsors),
        ("committees", client.get_committees),
        ("summaries", client.get_summaries),
    ):
        items = fetch_items(congress, bill_type, number)
        if items:
            meta[key] = items
    (bill_dir / META_FILENAME).write_text(json.dumps(meta, indent=2))

    for legacy_filename in _LEGACY_FILENAMES:
        legacy_path = bill_dir / legacy_filename
        if legacy_path.exists():
            legacy_path.unlink()

    text_versions = client.get_text_versions(congress, bill_type, number)
    text.sync_latest_text(text_versions, bill_dir)

    laws = detail.get("laws")
    if laws:
        law = laws[0]
        logger.info("Synced %s %s: became %s %s", bill_type.upper(), number, law.get("type"), law.get("number"))
    else:
        logger.info("Synced %s %s: %s", bill_type.upper(), number, str(detail.get("title", ""))[:80])
    return bool(laws)


def _index_entry(bill_type: str, number: str, status: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": bill_type,
        "number": number,
        "title": status.get("title"),
        "latestAction": status.get("latestAction"),
        "enacted": bool(status.get("laws")),
    }


def _build_index(bills_dir: Path, congress: int) -> list[dict[str, Any]]:
    """Rebuild the full bill index for `congress`, sorted by most recent *real* activity.

    Sorted by `latestAction.actionDate`, not `updateDate` -- `updateDate` bumps for congress.gov's
    own backend reprocessing as often as it does for an actual floor action (confirmed live, see
    BILLS-MIRROR-NOTES.md), so it doesn't mean "something happened" the way `latestAction` does.

    Reads every bill currently on disk, not just the ones this sync run touched, so the index
    always matches the full current state of `bills/{congress}/` -- cheap, since it's a local read
    over data already there, not new API calls.
    """
    congress_dir = bills_dir / str(congress)
    entries = []
    if congress_dir.exists():
        for type_dir in sorted(p for p in congress_dir.iterdir() if p.is_dir()):
            for bill_dir in sorted(p for p in type_dir.iterdir() if p.is_dir()):
                meta_path = bill_dir / META_FILENAME
                if not meta_path.exists():
                    continue
                status = json.loads(meta_path.read_text()).get("status", {})
                entries.append(_index_entry(type_dir.name, bill_dir.name, status))

    entries.sort(key=lambda entry: (entry["type"], entry["number"]))
    entries.sort(key=lambda entry: (entry["latestAction"] or {}).get("actionDate") or "", reverse=True)
    return entries


def _filter_recent(entries: list[dict[str, Any]], as_of: datetime, days: int) -> list[dict[str, Any]]:
    """Keep only entries whose `latestAction.actionDate` falls within `days` of `as_of`.

    `entries` is assumed already sorted newest-first (as `_build_index` returns it), so filtering
    preserves that order without needing to re-sort.
    """
    cutoff = (as_of - timedelta(days=days)).strftime("%Y-%m-%d")
    return [entry for entry in entries if ((entry["latestAction"] or {}).get("actionDate") or "") >= cutoff]


def _write_indexes(bills_dir: Path, congress: int, as_of: datetime) -> dict[str, int]:
    """Write the full index plus the windowed (7d/30d) ones; return each one's entry count.

    The full index can grow to match the whole Congress (tens of thousands of bills by the time one
    ends, once enacted bills stop being pruned) -- fine for completeness, but a consumer that only
    wants "what's new" shouldn't have to download and filter that. The windowed indexes exist for
    exactly that: bounded-size views of recent activity, alongside the complete one for anyone who
    wants it. See BILLS-MIRROR-NOTES.md.
    """
    entries = _build_index(bills_dir, congress)
    congress_dir = bills_dir / str(congress)
    congress_dir.mkdir(parents=True, exist_ok=True)

    (congress_dir / INDEX_FILENAME).write_text(json.dumps(entries, indent=2))
    counts = {"all": len(entries)}
    for filename, days in _INDEX_WINDOWS:
        windowed = _filter_recent(entries, as_of, days)
        (congress_dir / filename).write_text(json.dumps(windowed, indent=2))
        counts[f"{days}d"] = len(windowed)
    return counts


def sync(bills_dir: Path = DEFAULT_BILLS_DIR, limit: int | None = None) -> SyncResult:
    """Run one bills sync pass; return counts of what happened.

    `limit` caps how many bills are processed in one call -- for manual smoke testing only, never
    passed by the scheduled workflow.
    """
    current_congress = client.get_current_congress()
    stored_congress = synced_congress(bills_dir)
    rolled_over = stored_congress != current_congress

    if rolled_over:
        if bills_dir.exists():
            shutil.rmtree(bills_dir)
        watermark = INITIAL_SYNC_CUTOFF
        logger.info(
            "Congress rolled over (%s -> %s) -- wiping %s and starting from %s",
            stored_congress, current_congress, bills_dir, watermark,
        )
    else:
        watermark = synced_last_sync(bills_dir) or INITIAL_SYNC_CUTOFF
        logger.info("Syncing Congress %d incrementally from %s", current_congress, watermark)

    run_started_at_dt = datetime.now(UTC)
    run_started_at = run_started_at_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    written = 0
    enacted = 0
    for count, bill_summary in enumerate(client.iter_bill_summaries(current_congress, watermark), start=1):
        if limit is not None and count > limit:
            logger.info("Reached manual --limit %d -- stopping early", limit)
            break
        if _sync_one_bill(bills_dir, current_congress, bill_summary["type"], bill_summary["number"]):
            enacted += 1
        written += 1

    index_counts = _write_indexes(bills_dir, current_congress, run_started_at_dt)
    _write_markers(bills_dir, current_congress, run_started_at)
    logger.info(
        "Sync complete: %d written (%d enacted), indexed %d total / %d in 7d / %d in 30d, watermark now %s",
        written, enacted, index_counts["all"], index_counts["7d"], index_counts["30d"], run_started_at,
    )
    return SyncResult(written=written, enacted=enacted, rolled_over=rolled_over)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bills-dir", type=Path, default=DEFAULT_BILLS_DIR, help="Directory to write bills/ into")
    parser.add_argument("--limit", type=int, default=None, help="Manual testing only: stop after N bills")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sync(bills_dir=args.bills_dir, limit=args.limit)


if __name__ == "__main__":
    main()
