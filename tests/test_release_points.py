import logging

import pytest

from uscode_mirror.release_points import (
    RELEASE_POINTS_INDEX_URL,
    ReleasePageFormatError,
    ReleasePoint,
    fetch_release_point_page,
    fetch_release_points_index,
    full_code_urls,
    get_latest_full_code_urls,
    latest_release_point,
    parse_release_points,
    parse_titles,
    release_point_page_url,
    title_zip_url,
)

# Modeled on the real https://uscode.house.gov/download/priorreleasepoints.htm structure:
# live entries as plain <li><a href="releasepoints/us/pl/{congress}/{law}/...">, not-yet-published
# release points left commented out by OLRC, and (observed on the real page) an occasional
# duplicate anchor for the same release point.
SAMPLE_INDEX_HTML = """
<html><body>
<ul>
<li class="releasepoint"><a class="releasepoint"
    href="releasepoints/us/pl/119/100/usc-rp@119-100.htm">
    PL 119-100, title 47</a></li>
<!--    <li class="releasepoint"><a class="releasepoint"
    href="releasepoints/us/pl/119/101/usc-rp@119-101.htm">
    PL 119-101, title 10 (not yet published)</a></li>  -->
<li class="releasepoint"><a class="releasepoint"
    href="releasepoints/us/pl/119/99/usc-rp@119-99.htm">
    PL 119-99, title 16</a></li>
<li class="releasepoint"><a class="releasepoint"
    href="releasepoints/us/pl/119/99/usc-rp@119-99.htm">
    mirrored link to the same release point</a></li>
<li class="releasepoint"><a class="releasepoint"
    href="releasepoints/us/pl/118/262/usc-rp@118-262.htm">
    PL 118-262</a></li>
</ul>
</body></html>
"""


def _sample_release_point_html(label: str) -> str:
    """Build a release point's own download page, modeled on the real
    https://uscode.house.gov/download/releasepoints/us/pl/{congress}/{law}/usc-rp@{label}.htm.

    Deliberately includes: an out-of-order and duplicated title, an appendix title, the combined
    "All" corpus zip (which isn't a title), a non-XML format of the same title (which shouldn't be
    mistaken for the XML one), and a title belonging to a *different* release point (which
    shouldn't leak in) -- this is what lets titles be discovered fresh each run instead of
    hardcoded, so a Congress adding or retiring a title doesn't require a code change.
    """
    return f"""
<html><body>
<a href="xml_usc06@{label}.zip">Title 6</a>
<a href="xml_usc01@{label}.zip">Title 1</a>
<a href="xml_usc01@{label}.zip">Title 1 (mirrored)</a>
<a href="xml_usc05a@{label}.zip">Title 5a</a>
<a href="xml_usc05@{label}.zip">Title 5</a>
<a href="xml_uscAll@{label}.zip">Full corpus</a>
<a href="htm_usc01@{label}.zip">Title 1 (HTML format)</a>
<a href="xml_usc07@118-262.zip">Title 7 from a different release point</a>
</body></html>
"""


SAMPLE_RELEASE_POINT_HTML = _sample_release_point_html("119-99")


def test_parse_release_points_skips_not_yet_published_entries() -> None:
    points = parse_release_points(SAMPLE_INDEX_HTML)
    assert ReleasePoint(119, 101) not in points


def test_parse_release_points_deduplicates_repeated_anchors() -> None:
    points = parse_release_points(SAMPLE_INDEX_HTML)
    assert points.count(ReleasePoint(119, 99)) == 1


def test_parse_release_points_sorts_chronologically() -> None:
    points = parse_release_points(SAMPLE_INDEX_HTML)
    assert points == sorted(points)
    assert points[0] == ReleasePoint(118, 262)
    assert points[-1] == ReleasePoint(119, 100)


def test_latest_release_point_returns_most_recent() -> None:
    points = [ReleasePoint(118, 262), ReleasePoint(119, 100), ReleasePoint(119, 99)]
    assert latest_release_point(points) == ReleasePoint(119, 100)


def test_latest_release_point_rejects_empty_list() -> None:
    with pytest.raises(ValueError):
        latest_release_point([])


def test_release_point_label() -> None:
    assert ReleasePoint(119, 99).label == "119-99"


def test_title_zip_url_matches_known_pattern() -> None:
    point = ReleasePoint(119, 99)
    assert title_zip_url(point, "51") == ("https://uscode.house.gov/download/releasepoints/us/pl/119/99/xml_usc51@119-99.zip")


def test_release_point_page_url_matches_known_pattern() -> None:
    point = ReleasePoint(119, 99)
    assert release_point_page_url(point) == ("https://uscode.house.gov/download/releasepoints/us/pl/119/99/usc-rp@119-99.htm")


def test_parse_titles_excludes_titles_from_a_different_release_point() -> None:
    titles = parse_titles(SAMPLE_RELEASE_POINT_HTML, ReleasePoint(119, 99))
    assert "07" not in titles


def test_parse_titles_excludes_the_full_corpus_zip() -> None:
    titles = parse_titles(SAMPLE_RELEASE_POINT_HTML, ReleasePoint(119, 99))
    assert "All" not in titles
    assert "all" not in titles
    assert not any(title.lower() == "all" for title in titles)


def test_parse_titles_excludes_non_xml_formats_of_the_same_title() -> None:
    # htm_usc01@... shouldn't cause "01" to appear twice or in a different form.
    titles = parse_titles(SAMPLE_RELEASE_POINT_HTML, ReleasePoint(119, 99))
    assert titles.count("01") == 1


def test_parse_titles_sorts_numerically_with_appendices_after_their_number() -> None:
    titles = parse_titles(SAMPLE_RELEASE_POINT_HTML, ReleasePoint(119, 99))
    assert titles == ["01", "05", "05a", "06"]


def test_full_code_urls_builds_one_url_per_given_title_in_order() -> None:
    point = ReleasePoint(119, 99)
    urls = full_code_urls(point, ["01", "05a", "06"])
    assert urls == [
        title_zip_url(point, "01"),
        title_zip_url(point, "05a"),
        title_zip_url(point, "06"),
    ]


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body.encode("utf-8")


def test_fetch_release_points_index_returns_decoded_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls = []

    def fake_urlopen(url: str) -> _FakeResponse:
        requested_urls.append(url)
        return _FakeResponse(SAMPLE_INDEX_HTML)

    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", fake_urlopen)

    html = fetch_release_points_index()

    assert requested_urls == [RELEASE_POINTS_INDEX_URL]
    assert html == SAMPLE_INDEX_HTML


def test_fetch_release_point_page_returns_decoded_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    point = ReleasePoint(119, 99)
    requested_urls = []

    def fake_urlopen(url: str) -> _FakeResponse:
        requested_urls.append(url)
        return _FakeResponse(SAMPLE_RELEASE_POINT_HTML)

    monkeypatch.setattr("http_retry.fetch.urllib.request.urlopen", fake_urlopen)

    html = fetch_release_point_page(point)

    assert requested_urls == [release_point_page_url(point)]
    assert html == SAMPLE_RELEASE_POINT_HTML


def test_get_latest_full_code_urls_uses_latest_live_release_points_own_titles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latest = ReleasePoint(119, 100)
    monkeypatch.setattr(
        "uscode_mirror.release_points.fetch_release_points_index",
        lambda: SAMPLE_INDEX_HTML,
    )
    monkeypatch.setattr(
        "uscode_mirror.release_points.fetch_release_point_page",
        lambda point: _sample_release_point_html(point.label),
    )

    urls = get_latest_full_code_urls()

    assert urls == full_code_urls(latest, ["01", "05", "05a", "06"])


UNRECOGNIZABLE_HTML = "<html><body><p>Nothing resembling the expected link format.</p></body></html>"


def test_parse_release_points_raises_loudly_on_unrecognized_page_format(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="uscode_mirror.release_points"):
        with pytest.raises(ReleasePageFormatError):
            parse_release_points(UNRECOGNIZABLE_HTML)

    assert any("release point" in record.getMessage().lower() for record in caplog.records)
    assert any(record.levelno == logging.ERROR for record in caplog.records)


def test_parse_titles_raises_loudly_on_unrecognized_page_format(
    caplog: pytest.LogCaptureFixture,
) -> None:
    point = ReleasePoint(119, 99)

    with caplog.at_level(logging.ERROR, logger="uscode_mirror.release_points"):
        with pytest.raises(ReleasePageFormatError):
            parse_titles(UNRECOGNIZABLE_HTML, point)

    assert any("119-99" in record.getMessage() for record in caplog.records)
    assert any(record.levelno == logging.ERROR for record in caplog.records)
