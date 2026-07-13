import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from congress_bills_mirror.sync import (
    INDEX_FILENAME,
    INITIAL_SYNC_CUTOFF,
    META_FILENAME,
    sync,
    synced_congress,
    synced_last_sync,
)

PENDING_BILL = {
    "congress": 119,
    "type": "HR",
    "number": "877",
    "title": "Deliver for Veterans Act",
    "latestAction": {"actionDate": "2025-04-08", "text": "Referred to committee."},
}

ENACTED_BILL = {
    "congress": 119,
    "type": "S",
    "number": "1003",
    "title": "Lulu's Law",
    "laws": [{"number": "119-100", "type": "Public Law"}],
}


def _patch_current_congress(monkeypatch: pytest.MonkeyPatch, number: int = 119) -> None:
    monkeypatch.setattr("congress_bills_mirror.sync.client.get_current_congress", lambda: number)


def _patch_bill_summaries(monkeypatch: pytest.MonkeyPatch, summaries: list[dict[str, Any]]) -> list[str]:
    calls = []

    def fake_iter(congress: int, from_date_time: str) -> list[dict[str, Any]]:
        calls.append(from_date_time)
        return summaries

    monkeypatch.setattr("congress_bills_mirror.sync.client.iter_bill_summaries", fake_iter)
    return calls


def _patch_bill_details(monkeypatch: pytest.MonkeyPatch, details_by_number: dict[str, dict[str, Any]]) -> None:
    monkeypatch.setattr(
        "congress_bills_mirror.sync.client.get_bill_detail",
        lambda congress, bill_type, number: details_by_number[number],
    )


def _patch_empty_subresources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("congress_bills_mirror.sync.client.get_cosponsors", lambda *a: [])
    monkeypatch.setattr("congress_bills_mirror.sync.client.get_committees", lambda *a: [])
    monkeypatch.setattr("congress_bills_mirror.sync.client.get_summaries", lambda *a: [])
    monkeypatch.setattr("congress_bills_mirror.sync.client.get_text_versions", lambda *a: [])
    monkeypatch.setattr("congress_bills_mirror.sync.text.sync_latest_text", lambda versions, dest: [])


def test_first_run_bootstraps_from_the_initial_cutoff(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_current_congress(monkeypatch)
    from_date_times = _patch_bill_summaries(monkeypatch, [PENDING_BILL])
    _patch_bill_details(monkeypatch, {"877": PENDING_BILL})
    _patch_empty_subresources(monkeypatch)
    bills_dir = tmp_path / "bills"

    result = sync(bills_dir=bills_dir)

    assert result.rolled_over is True
    assert from_date_times == [INITIAL_SYNC_CUTOFF]
    assert synced_congress(bills_dir) == 119


def test_pending_bill_is_written_to_disk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_current_congress(monkeypatch)
    _patch_bill_summaries(monkeypatch, [PENDING_BILL])
    _patch_bill_details(monkeypatch, {"877": PENDING_BILL})
    _patch_empty_subresources(monkeypatch)
    bills_dir = tmp_path / "bills"

    result = sync(bills_dir=bills_dir)

    meta_path = bills_dir / "119" / "hr" / "877" / META_FILENAME
    assert result.written == 1
    assert result.enacted == 0
    assert json.loads(meta_path.read_text())["status"] == PENDING_BILL


def test_enacted_bill_is_written_and_kept_not_pruned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_current_congress(monkeypatch)
    _patch_bill_summaries(monkeypatch, [ENACTED_BILL])
    _patch_bill_details(monkeypatch, {"1003": ENACTED_BILL})
    _patch_empty_subresources(monkeypatch)
    bills_dir = tmp_path / "bills"

    result = sync(bills_dir=bills_dir)

    meta_path = bills_dir / "119" / "s" / "1003" / META_FILENAME
    assert result.written == 1
    assert result.enacted == 1
    assert json.loads(meta_path.read_text())["status"] == ENACTED_BILL


def test_bill_that_became_enacted_since_last_sync_is_updated_in_place_not_deleted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bills_dir = tmp_path / "bills"
    existing_dir = bills_dir / "119" / "s" / "1003"
    existing_dir.mkdir(parents=True)
    (existing_dir / META_FILENAME).write_text(json.dumps({"status": {"laws": []}}))
    bills_dir.mkdir(parents=True, exist_ok=True)
    (bills_dir / ".congress").write_text("119\n")
    (bills_dir / ".last-sync").write_text("2026-07-11T00:00:00Z\n")

    _patch_current_congress(monkeypatch)
    _patch_bill_summaries(monkeypatch, [ENACTED_BILL])
    _patch_bill_details(monkeypatch, {"1003": ENACTED_BILL})
    _patch_empty_subresources(monkeypatch)

    result = sync(bills_dir=bills_dir)

    assert result.enacted == 1
    assert existing_dir.exists()
    assert json.loads((existing_dir / META_FILENAME).read_text())["status"] == ENACTED_BILL


def test_legacy_per_resource_files_are_cleaned_up_on_next_sync(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A bill synced before status/cosponsors/committees/summaries were consolidated into
    `meta.json` (revised 2026-07-13) should self-heal into the new layout, not accumulate both."""
    bills_dir = tmp_path / "bills"
    existing_dir = bills_dir / "119" / "hr" / "877"
    existing_dir.mkdir(parents=True)
    (existing_dir / "status.json").write_text(json.dumps(PENDING_BILL))
    (existing_dir / "cosponsors.json").write_text(json.dumps([{"bioguideId": "OLD"}]))
    bills_dir.mkdir(parents=True, exist_ok=True)
    (bills_dir / ".congress").write_text("119\n")
    (bills_dir / ".last-sync").write_text("2026-07-11T00:00:00Z\n")

    _patch_current_congress(monkeypatch)
    _patch_bill_summaries(monkeypatch, [PENDING_BILL])
    _patch_bill_details(monkeypatch, {"877": PENDING_BILL})
    _patch_empty_subresources(monkeypatch)

    sync(bills_dir=bills_dir)

    assert not (existing_dir / "status.json").exists()
    assert not (existing_dir / "cosponsors.json").exists()
    assert (existing_dir / META_FILENAME).exists()


def test_incremental_sync_uses_the_stored_watermark(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bills_dir = tmp_path / "bills"
    bills_dir.mkdir(parents=True)
    (bills_dir / ".congress").write_text("119\n")
    (bills_dir / ".last-sync").write_text("2026-08-01T00:00:00Z\n")

    _patch_current_congress(monkeypatch)
    from_date_times = _patch_bill_summaries(monkeypatch, [])
    _patch_empty_subresources(monkeypatch)

    result = sync(bills_dir=bills_dir)

    assert result.rolled_over is False
    assert from_date_times == ["2026-08-01T00:00:00Z"]


def test_congress_rollover_wipes_the_old_congress_and_resets_to_the_cutoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bills_dir = tmp_path / "bills"
    stale_dir = bills_dir / "118" / "hr" / "1"
    stale_dir.mkdir(parents=True)
    (stale_dir / "status.json").write_text("{}")
    (bills_dir / ".congress").write_text("118\n")
    (bills_dir / ".last-sync").write_text("2026-01-01T00:00:00Z\n")

    _patch_current_congress(monkeypatch, number=119)
    from_date_times = _patch_bill_summaries(monkeypatch, [])
    _patch_empty_subresources(monkeypatch)

    result = sync(bills_dir=bills_dir)

    assert result.rolled_over is True
    assert from_date_times == [INITIAL_SYNC_CUTOFF]
    assert not stale_dir.exists()
    assert synced_congress(bills_dir) == 119


def test_sync_writes_a_fresh_last_sync_watermark(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_current_congress(monkeypatch)
    _patch_bill_summaries(monkeypatch, [])
    _patch_empty_subresources(monkeypatch)
    bills_dir = tmp_path / "bills"

    sync(bills_dir=bills_dir)

    watermark = synced_last_sync(bills_dir)
    assert watermark is not None
    assert watermark != INITIAL_SYNC_CUTOFF


def test_index_is_written_as_an_empty_list_when_a_sync_touches_no_bills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Regression: _write_index used to assume bills/{congress}/ already existed, which it doesn't
    # on a run that touches zero bills (e.g. a bootstrap into a quiet window).
    _patch_current_congress(monkeypatch)
    _patch_bill_summaries(monkeypatch, [])
    _patch_empty_subresources(monkeypatch)
    bills_dir = tmp_path / "bills"

    sync(bills_dir=bills_dir)

    assert json.loads((bills_dir / "119" / INDEX_FILENAME).read_text()) == []


def test_limit_stops_processing_early(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    second_bill = {**PENDING_BILL, "number": "878"}
    _patch_current_congress(monkeypatch)
    _patch_bill_summaries(monkeypatch, [PENDING_BILL, second_bill])
    _patch_bill_details(monkeypatch, {"877": PENDING_BILL, "878": second_bill})
    _patch_empty_subresources(monkeypatch)
    bills_dir = tmp_path / "bills"

    result = sync(bills_dir=bills_dir, limit=1)

    assert result.written == 1


def test_index_is_sorted_by_latest_action_not_update_date(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # updateDate would sort these the *other* way (older bill "updated" more recently by
    # congress.gov's own backend noise) -- the index must ignore updateDate entirely.
    older_action_bill = {
        "type": "HR",
        "number": "1",
        "title": "Old Action Bill",
        "updateDate": "2026-07-13",
        "latestAction": {"actionDate": "2025-01-01", "text": "Referred to committee."},
    }
    newer_action_bill = {
        "type": "HR",
        "number": "2",
        "title": "New Action Bill",
        "updateDate": "2025-02-01",
        "latestAction": {"actionDate": "2026-06-01", "text": "Passed House."},
    }
    _patch_current_congress(monkeypatch)
    _patch_bill_summaries(monkeypatch, [older_action_bill, newer_action_bill])
    _patch_bill_details(monkeypatch, {"1": older_action_bill, "2": newer_action_bill})
    _patch_empty_subresources(monkeypatch)
    bills_dir = tmp_path / "bills"

    sync(bills_dir=bills_dir)

    index = json.loads((bills_dir / "119" / INDEX_FILENAME).read_text())
    assert [entry["number"] for entry in index] == ["2", "1"]


def test_index_includes_bills_not_touched_by_this_sync_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bills_dir = tmp_path / "bills"
    untouched_dir = bills_dir / "119" / "s" / "1003"
    untouched_dir.mkdir(parents=True)
    (untouched_dir / META_FILENAME).write_text(json.dumps({"status": ENACTED_BILL}))
    bills_dir.mkdir(parents=True, exist_ok=True)
    (bills_dir / ".congress").write_text("119\n")
    (bills_dir / ".last-sync").write_text("2026-07-11T00:00:00Z\n")

    _patch_current_congress(monkeypatch)
    _patch_bill_summaries(monkeypatch, [PENDING_BILL])
    _patch_bill_details(monkeypatch, {"877": PENDING_BILL})
    _patch_empty_subresources(monkeypatch)

    sync(bills_dir=bills_dir)

    index = json.loads((bills_dir / "119" / INDEX_FILENAME).read_text())
    numbers = {entry["number"] for entry in index}
    assert numbers == {"877", "1003"}


def test_index_marks_enacted_bills(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_current_congress(monkeypatch)
    _patch_bill_summaries(monkeypatch, [PENDING_BILL, ENACTED_BILL])
    _patch_bill_details(monkeypatch, {"877": PENDING_BILL, "1003": ENACTED_BILL})
    _patch_empty_subresources(monkeypatch)
    bills_dir = tmp_path / "bills"

    sync(bills_dir=bills_dir)

    index = json.loads((bills_dir / "119" / INDEX_FILENAME).read_text())
    by_number = {entry["number"]: entry for entry in index}
    assert by_number["877"]["enacted"] is False
    assert by_number["1003"]["enacted"] is True


def test_index_handles_a_bill_with_no_latest_action(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    no_action_bill = {"type": "HR", "number": "5", "title": "No Action Yet"}
    _patch_current_congress(monkeypatch)
    _patch_bill_summaries(monkeypatch, [PENDING_BILL, no_action_bill])
    _patch_bill_details(monkeypatch, {"877": PENDING_BILL, "5": no_action_bill})
    _patch_empty_subresources(monkeypatch)
    bills_dir = tmp_path / "bills"

    sync(bills_dir=bills_dir)

    index = json.loads((bills_dir / "119" / INDEX_FILENAME).read_text())
    assert [entry["number"] for entry in index] == ["877", "5"]


def _bill_with_action_days_ago(number: str, days_ago: int) -> dict[str, Any]:
    action_date = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    return {
        "type": "HR",
        "number": number,
        "title": f"Bill from {days_ago} days ago",
        "latestAction": {"actionDate": action_date, "text": "Some action."},
    }


def test_windowed_indexes_filter_by_days_since_latest_action(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    recent = _bill_with_action_days_ago("1", 3)  # in 7d, 30d, and full
    mid = _bill_with_action_days_ago("2", 10)  # in 30d and full, not 7d
    old = _bill_with_action_days_ago("3", 40)  # in full only
    bills = [recent, mid, old]
    _patch_current_congress(monkeypatch)
    _patch_bill_summaries(monkeypatch, bills)
    _patch_bill_details(monkeypatch, {b["number"]: b for b in bills})
    _patch_empty_subresources(monkeypatch)
    bills_dir = tmp_path / "bills"

    sync(bills_dir=bills_dir)

    def numbers(filename: str) -> set[str]:
        return {entry["number"] for entry in json.loads((bills_dir / "119" / filename).read_text())}

    assert numbers(INDEX_FILENAME) == {"1", "2", "3"}
    assert numbers("index-30d.json") == {"1", "2"}
    assert numbers("index-7d.json") == {"1"}


def test_windowed_indexes_preserve_newest_first_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    newer = _bill_with_action_days_ago("1", 1)
    older = _bill_with_action_days_ago("2", 5)
    bills = [older, newer]  # deliberately out of order going in
    _patch_current_congress(monkeypatch)
    _patch_bill_summaries(monkeypatch, bills)
    _patch_bill_details(monkeypatch, {b["number"]: b for b in bills})
    _patch_empty_subresources(monkeypatch)
    bills_dir = tmp_path / "bills"

    sync(bills_dir=bills_dir)

    index_7d = json.loads((bills_dir / "119" / "index-7d.json").read_text())
    assert [entry["number"] for entry in index_7d] == ["1", "2"]


def test_non_empty_subresources_are_included_as_meta_keys_empty_ones_omitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_current_congress(monkeypatch)
    _patch_bill_summaries(monkeypatch, [PENDING_BILL])
    _patch_bill_details(monkeypatch, {"877": PENDING_BILL})
    monkeypatch.setattr("congress_bills_mirror.sync.client.get_cosponsors", lambda *a: [{"bioguideId": "G000594"}])
    monkeypatch.setattr("congress_bills_mirror.sync.client.get_committees", lambda *a: [])
    monkeypatch.setattr("congress_bills_mirror.sync.client.get_summaries", lambda *a: [])
    monkeypatch.setattr("congress_bills_mirror.sync.client.get_text_versions", lambda *a: [])
    monkeypatch.setattr("congress_bills_mirror.sync.text.sync_latest_text", lambda versions, dest: [])
    bills_dir = tmp_path / "bills"

    sync(bills_dir=bills_dir)

    meta = json.loads((bills_dir / "119" / "hr" / "877" / META_FILENAME).read_text())
    assert meta["cosponsors"] == [{"bioguideId": "G000594"}]
    assert "committees" not in meta
    assert "summaries" not in meta
