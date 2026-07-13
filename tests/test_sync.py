import logging
from pathlib import Path

import pytest

from uscode_mirror.release_points import ReleasePoint
from uscode_mirror.sync import sync, synced_release_point_label

SAMPLE_INDEX_HTML = """
<html><body>
<a href="releasepoints/us/pl/119/100/usc-rp@119-100.htm">PL 119-100</a>
</body></html>
"""

SAMPLE_RELEASE_POINT_HTML = """
<html><body>
<a href="xml_usc01@119-100.zip">Title 1</a>
</body></html>
"""


def _patch_release_points_at_119_100(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("uscode_mirror.sync.release_points.fetch_release_points_index", lambda: SAMPLE_INDEX_HTML)
    monkeypatch.setattr(
        "uscode_mirror.sync.release_points.fetch_release_point_page",
        lambda point: SAMPLE_RELEASE_POINT_HTML,
    )


def _patch_pipeline_stages(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[object]]:
    """Stub out download/chunk/render so a full sync run touches no network and no real XML.

    Returns a dict of call logs, keyed by stage name, so tests can assert what ran.
    """
    calls: dict[str, list[object]] = {"download": [], "chunk": [], "json": [], "txt": []}

    def fake_fetch_all_titles(urls: list[str], raw_dir: Path) -> list[Path]:
        calls["download"].append((list(urls), raw_dir))
        raw_dir.mkdir(parents=True, exist_ok=True)
        return [raw_dir / "usc01.xml"]

    def fake_chunk_all_titles(raw_dir: Path, usc_dir: Path) -> list[Path]:
        calls["chunk"].append((raw_dir, usc_dir))
        usc_dir.mkdir(parents=True, exist_ok=True)
        return [usc_dir / "1" / "1.xml"]

    def fake_render_all_json(usc_dir: Path) -> list[Path]:
        calls["json"].append(usc_dir)
        return [usc_dir / "1" / "1.json"]

    def fake_render_all_txt(usc_dir: Path) -> list[Path]:
        calls["txt"].append(usc_dir)
        return [usc_dir / "1" / "1.txt"]

    monkeypatch.setattr("uscode_mirror.sync.download.fetch_all_titles", fake_fetch_all_titles)
    monkeypatch.setattr("uscode_mirror.sync.chunk.chunk_all_titles", fake_chunk_all_titles)
    monkeypatch.setattr("uscode_mirror.sync.render_json.render_all_json", fake_render_all_json)
    monkeypatch.setattr("uscode_mirror.sync.render_txt.render_all_txt", fake_render_all_txt)
    return calls


def test_synced_release_point_label_returns_none_when_never_synced(tmp_path: Path) -> None:
    assert synced_release_point_label(tmp_path / "usc") is None


def test_sync_runs_full_pipeline_when_never_synced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_release_points_at_119_100(monkeypatch)
    calls = _patch_pipeline_stages(monkeypatch)
    raw_dir, usc_dir = tmp_path / "raw", tmp_path / "usc"

    ran = sync(raw_dir=raw_dir, usc_dir=usc_dir)

    assert ran is True
    assert len(calls["download"]) == 1
    assert len(calls["chunk"]) == 1
    assert len(calls["json"]) == 1
    assert len(calls["txt"]) == 1
    assert synced_release_point_label(usc_dir) == "119-100"


def test_sync_skips_when_already_current(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_release_points_at_119_100(monkeypatch)
    calls = _patch_pipeline_stages(monkeypatch)
    raw_dir, usc_dir = tmp_path / "raw", tmp_path / "usc"
    usc_dir.mkdir(parents=True)
    (usc_dir / ".release-point").write_text("119-100\n")

    with caplog.at_level(logging.INFO, logger="uscode_mirror.sync"):
        ran = sync(raw_dir=raw_dir, usc_dir=usc_dir)

    assert ran is False
    assert calls["download"] == []
    assert calls["chunk"] == []
    assert calls["json"] == []
    assert calls["txt"] == []
    assert any("skipping" in record.getMessage().lower() for record in caplog.records)


def test_sync_runs_when_a_newer_release_point_is_live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_release_points_at_119_100(monkeypatch)
    calls = _patch_pipeline_stages(monkeypatch)
    raw_dir, usc_dir = tmp_path / "raw", tmp_path / "usc"
    usc_dir.mkdir(parents=True)
    (usc_dir / ".release-point").write_text("119-99\n")

    ran = sync(raw_dir=raw_dir, usc_dir=usc_dir)

    assert ran is True
    assert len(calls["download"]) == 1
    assert synced_release_point_label(usc_dir) == "119-100"


def test_sync_force_reruns_even_when_already_current(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_release_points_at_119_100(monkeypatch)
    calls = _patch_pipeline_stages(monkeypatch)
    raw_dir, usc_dir = tmp_path / "raw", tmp_path / "usc"
    usc_dir.mkdir(parents=True)
    (usc_dir / ".release-point").write_text("119-100\n")

    ran = sync(raw_dir=raw_dir, usc_dir=usc_dir, force=True)

    assert ran is True
    assert len(calls["download"]) == 1


def test_sync_wipes_stale_files_from_raw_and_usc_dirs_before_rebuilding(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_release_points_at_119_100(monkeypatch)
    _patch_pipeline_stages(monkeypatch)
    raw_dir, usc_dir = tmp_path / "raw", tmp_path / "usc"
    raw_dir.mkdir(parents=True)
    (raw_dir / "stale.xml").write_text("old title content")
    usc_dir.mkdir(parents=True)
    (usc_dir / "stale-title").mkdir()
    (usc_dir / "stale-title" / "stale.xml").write_text("old chunk")

    sync(raw_dir=raw_dir, usc_dir=usc_dir)

    assert not (raw_dir / "stale.xml").exists()
    assert not (usc_dir / "stale-title").exists()


def test_full_code_urls_passed_to_download_come_from_the_release_points_own_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_release_points_at_119_100(monkeypatch)
    calls = _patch_pipeline_stages(monkeypatch)
    raw_dir, usc_dir = tmp_path / "raw", tmp_path / "usc"

    sync(raw_dir=raw_dir, usc_dir=usc_dir)

    urls, _ = calls["download"][0]
    assert urls == [
        f"https://uscode.house.gov/download/releasepoints/us/pl/{ReleasePoint(119, 100).congress}/"
        f"{ReleasePoint(119, 100).law}/xml_usc01@119-100.zip"
    ]
