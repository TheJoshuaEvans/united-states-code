import io
import logging
import zipfile
from pathlib import Path

import pytest

from uscode_mirror.download import (
    UnexpectedZipContentsError,
    download_zip,
    extract_xml,
    fetch_all_titles,
    fetch_title_xml,
)

SAMPLE_XML = b'<?xml version="1.0"?><uscDoc><main>Title 51 content</main></uscDoc>'


def _build_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class _FakeResponse:
    """Mimics the subset of urlopen()'s context-managed response object we actually read."""

    def __init__(self, data: bytes) -> None:
        self._buffer = io.BytesIO(data)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)


def test_download_zip_streams_body_to_a_file_named_after_the_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    zip_bytes = _build_zip({"usc51.xml": SAMPLE_XML})
    requested_urls = []

    def fake_urlopen(url: str) -> _FakeResponse:
        requested_urls.append(url)
        return _FakeResponse(zip_bytes)

    monkeypatch.setattr("uscode_mirror.download.urllib.request.urlopen", fake_urlopen)

    url = "https://uscode.house.gov/download/releasepoints/us/pl/119/99/xml_usc51@119-99.zip"
    dest_path = download_zip(url, tmp_path)

    assert requested_urls == [url]
    assert dest_path == tmp_path / "xml_usc51@119-99.zip"
    assert dest_path.read_bytes() == zip_bytes


def test_extract_xml_unpacks_the_single_xml_member(tmp_path: Path) -> None:
    zip_path = tmp_path / "xml_usc51@119-99.zip"
    zip_path.write_bytes(_build_zip({"usc51.xml": SAMPLE_XML}))

    xml_path = extract_xml(zip_path, tmp_path)

    assert xml_path == tmp_path / "usc51.xml"
    assert xml_path.read_bytes() == SAMPLE_XML


def test_extract_xml_raises_on_zero_xml_members(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    zip_path = tmp_path / "empty.zip"
    zip_path.write_bytes(_build_zip({"readme.txt": b"no xml here"}))

    with caplog.at_level(logging.ERROR, logger="uscode_mirror.download"):
        with pytest.raises(UnexpectedZipContentsError):
            extract_xml(zip_path, tmp_path)

    assert any(record.levelno == logging.ERROR for record in caplog.records)


def test_extract_xml_raises_on_multiple_xml_members(tmp_path: Path) -> None:
    zip_path = tmp_path / "ambiguous.zip"
    zip_path.write_bytes(_build_zip({"usc51.xml": SAMPLE_XML, "extra.xml": SAMPLE_XML}))

    with pytest.raises(UnexpectedZipContentsError):
        extract_xml(zip_path, tmp_path)


def test_fetch_title_xml_downloads_and_extracts_leaving_both_files_in_raw_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    zip_bytes = _build_zip({"usc51.xml": SAMPLE_XML})
    monkeypatch.setattr(
        "uscode_mirror.download.urllib.request.urlopen",
        lambda url: _FakeResponse(zip_bytes),
    )

    url = "https://uscode.house.gov/download/releasepoints/us/pl/119/99/xml_usc51@119-99.zip"
    xml_path = fetch_title_xml(url, tmp_path)

    assert xml_path == tmp_path / "usc51.xml"
    assert xml_path.read_bytes() == SAMPLE_XML
    assert (tmp_path / "xml_usc51@119-99.zip").exists()


def test_fetch_all_titles_processes_every_url_in_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    zips_by_url = {
        "https://uscode.house.gov/.../xml_usc01@119-99.zip": _build_zip({"usc01.xml": b"title 1"}),
        "https://uscode.house.gov/.../xml_usc02@119-99.zip": _build_zip({"usc02.xml": b"title 2"}),
    }
    requested_urls = []

    def fake_urlopen(url: str) -> _FakeResponse:
        requested_urls.append(url)
        return _FakeResponse(zips_by_url[url])

    monkeypatch.setattr("uscode_mirror.download.urllib.request.urlopen", fake_urlopen)

    xml_paths = fetch_all_titles(list(zips_by_url), tmp_path)

    assert requested_urls == list(zips_by_url)
    assert xml_paths == [tmp_path / "usc01.xml", tmp_path / "usc02.xml"]
    assert (tmp_path / "usc01.xml").read_bytes() == b"title 1"
    assert (tmp_path / "usc02.xml").read_bytes() == b"title 2"
