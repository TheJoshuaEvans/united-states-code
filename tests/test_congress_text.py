import io
import logging
import urllib.request
from pathlib import Path

import pytest

from congress_bills_mirror.text import TEXT_FILENAME, sync_latest_text

INTRODUCED = {
    "type": "Introduced in Senate",
    "date": "2025-04-09T04:00:00Z",
    "formats": [
        {"type": "Formatted Text", "url": "https://www.congress.gov/119/bills/s1383/BILLS-119s1383is.htm"},
        {"type": "PDF", "url": "https://www.congress.gov/119/bills/s1383/BILLS-119s1383is.pdf"},
        {"type": "Formatted XML", "url": "https://www.congress.gov/119/bills/s1383/BILLS-119s1383is.xml"},
    ],
}
ENGROSSED_AMENDMENT_HOUSE = {
    "type": "Engrossed Amendment House",
    "date": "2026-02-11T05:00:00Z",
    "formats": [
        {"type": "Formatted Text", "url": "https://www.congress.gov/119/bills/s1383/BILLS-119s1383eah.htm"},
        {"type": "PDF", "url": "https://www.congress.gov/119/bills/s1383/BILLS-119s1383eah.pdf"},
        {"type": "Formatted XML", "url": "https://www.congress.gov/119/bills/s1383/BILLS-119s1383eah.xml"},
    ],
}
MULTI_STAGE_VERSIONS = [ENGROSSED_AMENDMENT_HOUSE, INTRODUCED]  # deliberately not date-sorted


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._buffer = io.BytesIO(data)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)


def test_sync_latest_text_downloads_only_the_most_recent_version_by_date(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requested_urls = []

    def fake_urlopen(request: urllib.request.Request) -> _FakeResponse:
        requested_urls.append(request.full_url)
        return _FakeResponse(b"<bill>eah content</bill>")

    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", fake_urlopen)

    dest_path = sync_latest_text(MULTI_STAGE_VERSIONS, tmp_path)

    assert requested_urls == ["https://www.congress.gov/119/bills/s1383/BILLS-119s1383eah.xml"]
    assert dest_path == tmp_path / TEXT_FILENAME
    assert (tmp_path / TEXT_FILENAME).read_bytes() == b"<bill>eah content</bill>"


def test_sync_latest_text_removes_stale_versions_from_before_the_latest_only_rule(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "BILLS-119s1383is.xml").write_bytes(b"old introduced version")
    (tmp_path / "BILLS-119s1383rs.xml").write_bytes(b"old reported version")
    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", lambda request: _FakeResponse(b"latest"))

    sync_latest_text(MULTI_STAGE_VERSIONS, tmp_path)

    assert not (tmp_path / "BILLS-119s1383is.xml").exists()
    assert not (tmp_path / "BILLS-119s1383rs.xml").exists()
    assert (tmp_path / TEXT_FILENAME).read_bytes() == b"latest"


def test_sync_latest_text_overwrites_when_a_newer_version_appears(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / TEXT_FILENAME).write_bytes(b"old text.xml content")
    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", lambda request: _FakeResponse(b"new content"))

    sync_latest_text(MULTI_STAGE_VERSIONS, tmp_path)

    assert (tmp_path / TEXT_FILENAME).read_bytes() == b"new content"


def test_sync_latest_text_returns_none_when_there_are_no_text_versions(tmp_path: Path) -> None:
    assert sync_latest_text([], tmp_path) is None
    assert not (tmp_path / TEXT_FILENAME).exists()


def test_sync_latest_text_skips_when_the_latest_version_has_no_xml_format(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    versions = [{"type": "Referred with amendments", "date": "2026-01-01", "formats": [{"type": "PDF", "url": "https://example.com/x.pdf"}]}]

    with caplog.at_level(logging.INFO, logger="congress_bills_mirror.text"):
        result = sync_latest_text(versions, tmp_path)

    assert result is None
    assert any("no formatted xml" in record.getMessage().lower() for record in caplog.records)


def test_sync_latest_text_handles_a_null_date_without_crashing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    null_dated = {
        "type": "Referred with amendments",
        "date": None,
        "formats": [{"type": "Formatted XML", "url": "https://www.congress.gov/119/bills/hr993/BILLS-119hr993rfs.xml"}],
    }
    requested_urls = []

    def fake_urlopen(request: urllib.request.Request) -> _FakeResponse:
        requested_urls.append(request.full_url)
        return _FakeResponse(b"eah content")

    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", fake_urlopen)

    dest_path = sync_latest_text([null_dated, ENGROSSED_AMENDMENT_HOUSE], tmp_path)

    assert dest_path == tmp_path / TEXT_FILENAME
    assert requested_urls == ["https://www.congress.gov/119/bills/s1383/BILLS-119s1383eah.xml"]


def test_sync_latest_text_creates_dest_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", lambda request: _FakeResponse(b"x"))
    dest = tmp_path / "119" / "s" / "1383"

    sync_latest_text(MULTI_STAGE_VERSIONS, dest)

    assert dest.exists()
