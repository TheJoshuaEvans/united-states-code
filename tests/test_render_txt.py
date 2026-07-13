import logging
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from uscode_mirror.chunk import USLM_NS
from uscode_mirror.render_txt import (
    NotASectionError,
    UnrecognizedNamespaceError,
    element_to_text,
    render_all_txt,
    render_section_txt,
)

_XHTML_NS = "http://www.w3.org/1999/xhtml"


def _section_xml(body: str, *, with_html_ns: bool = False, identifier: str = "/us/usc/t51/s10101") -> str:
    """Build a standalone per-section chunk, matching chunk.py's own output shape."""
    html_ns = f' xmlns:html="{_XHTML_NS}"' if with_html_ns else ""
    return f'<section xmlns="{USLM_NS}"{html_ns} style="-uslm-lc:I80" id="idxyz" identifier="{identifier}">{body}</section>'


def test_element_to_text_puts_each_block_element_on_its_own_line() -> None:
    # Models the real corpus: no punctuation between num/heading/content in the source markup.
    body = (
        '<num value="501">§ 501.</num>'
        "<heading> Exemption from taxation</heading>"
        "<subsection>"
        '<num value="a">(a)</num>'
        "<heading> Exemption from taxation</heading>"
        "<content><p>An organization described in subsection (c) shall be exempt.</p></content>"
        "</subsection>"
    )
    xml = _section_xml(body)

    result = element_to_text(ET.fromstring(xml))

    assert result == (
        "§ 501.\n"
        "Exemption from taxation\n"
        "(a)\n"
        "Exemption from taxation\n"
        "An organization described in subsection (c) shall be exempt."
    )


def test_element_to_text_keeps_inline_tags_on_the_same_line() -> None:
    # ref/date/i/b/sup/sub/inline/quotedContent are embedded mid-sentence in the real corpus and
    # must not fracture the surrounding prose onto separate lines.
    body = (
        "<content>An organization exempt under "
        '<ref href="/us/usc/t26/s501">section 501</ref> before '
        '<date date="1984-07-18">July 18, 1984</date>, or <i>otherwise</i>.'
        "</content>"
    )
    xml = _section_xml(body)

    result = element_to_text(ET.fromstring(xml))

    assert result == "An organization exempt under section 501 before July 18, 1984, or otherwise."


def test_element_to_text_collapses_source_whitespace_and_line_wraps_within_a_line() -> None:
    body = "<content>Some   text\nthat wraps\n   across lines in the source file.</content>"
    xml = _section_xml(body)

    result = element_to_text(ET.fromstring(xml))

    assert result == "Some text that wraps across lines in the source file."


def test_element_to_text_drops_notes_and_source_credit_but_keeps_their_tail() -> None:
    # Models the real corpus: a <note> or <sourceCredit> is editorial, not operative text, but
    # whatever immediately follows it in the source (the tail) is still part of the parent's
    # running sentence and must survive.
    body = (
        "<content>Operative text."
        '<notes><note topic="amendments"><p>2019—Some editorial history.</p></note></notes>'
        "tail after notes."
        "<sourceCredit>(Pub. L. 100–000, § 1.)</sourceCredit>"
        "tail after credit."
        "</content>"
    )
    xml = _section_xml(body)

    result = element_to_text(ET.fromstring(xml))

    assert result == "Operative text.tail after notes.tail after credit."


def test_element_to_text_drops_footnote_reference_marker_but_keeps_inline_footnote_definition_tail() -> None:
    # Models the real corpus (26 U.S.C. 501(c)(12)(B)(iv)): a footnote marker <ref class=
    # "footnoteRef"> immediately followed by the inline <note type="footnote"> it points at, both
    # embedded mid-sentence. Both must be dropped, but the note's tail -- the rest of the sentence
    # -- must survive.
    body = (
        "<content>under section 311"
        '<ref class="footnoteRef" idref="fn1">1</ref>'
        '<note type="footnote" id="fn1"><num>1</num> See References in Text note below.</note>'
        " of the Rural Electrification Act of 1936.</content>"
    )
    xml = _section_xml(body)

    result = element_to_text(ET.fromstring(xml))

    assert result == "under section 311 of the Rural Electrification Act of 1936."


def test_element_to_text_keeps_an_ordinary_ref_without_footnote_ref_class() -> None:
    body = '<content>denied under <ref href="/us/usc/t26/s502">section 502</ref> or 503.</content>'
    xml = _section_xml(body)

    result = element_to_text(ET.fromstring(xml))

    assert result == "denied under section 502 or 503."


def test_element_to_text_drops_empty_lines_left_by_excluded_or_empty_blocks() -> None:
    body = "<heading></heading><content>Only the content survives.</content>"
    xml = _section_xml(body)

    result = element_to_text(ET.fromstring(xml))

    assert result == "Only the content survives."


def test_element_to_text_raises_on_unrecognized_namespace() -> None:
    xml = (
        f'<section xmlns="{USLM_NS}" xmlns:foreign="urn:bogus" identifier="/us/usc/t51/s1">'
        '<num value="1">§ 1.</num><foreign:weird>oops</foreign:weird></section>'
    )

    with pytest.raises(UnrecognizedNamespaceError):
        element_to_text(ET.fromstring(xml))


def test_render_section_txt_produces_unicode_preserving_bytes_with_trailing_newline(tmp_path: Path) -> None:
    xml = _section_xml(
        '<num value="501">§ 501.</num><heading> Exemption—“special” cases</heading>',
        identifier="/us/usc/t26/s501",
    )
    xml_path = tmp_path / "501.xml"
    xml_path.write_text(xml)

    result = render_section_txt(xml_path)

    expected = (element_to_text(ET.fromstring(xml)) + "\n").encode("utf-8")
    assert result == expected
    assert "—“special”".encode() in result


def test_render_section_txt_raises_not_a_section_error_on_appendix_root(tmp_path: Path) -> None:
    xml_path = tmp_path / "full.xml"
    xml_path.write_text(f'<uscDoc xmlns="{USLM_NS}"><main/></uscDoc>')

    with pytest.raises(NotASectionError):
        render_section_txt(xml_path)


def test_render_section_txt_raises_unrecognized_namespace_error_on_an_unknown_root_shape(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    xml_path = tmp_path / "weird.xml"
    xml_path.write_text(f'<title xmlns="{USLM_NS}"><section identifier="/us/usc/t51/s1"/></title>')

    with caplog.at_level(logging.ERROR, logger="uscode_mirror.render_txt"):
        with pytest.raises(UnrecognizedNamespaceError):
            render_section_txt(xml_path)

    assert any(record.levelno == logging.ERROR for record in caplog.records)


def test_render_all_txt_skips_appendix_full_xml_and_does_not_abort_batch(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    usc_dir = tmp_path / "usc"
    (usc_dir / "26").mkdir(parents=True)
    (usc_dir / "26" / "501.xml").write_text(_section_xml('<num value="501">§ 501.</num>', identifier="/us/usc/t26/s501"))
    (usc_dir / "5a").mkdir(parents=True)
    (usc_dir / "5a" / "full.xml").write_text(f'<uscDoc xmlns="{USLM_NS}"><main/></uscDoc>')

    with caplog.at_level(logging.INFO, logger="uscode_mirror.render_txt"):
        written = render_all_txt(usc_dir)

    assert written == [usc_dir / "26" / "501.txt"]
    assert (usc_dir / "26" / "501.txt").exists()
    assert not (usc_dir / "5a" / "full.txt").exists()
    assert any(record.levelno == logging.INFO and "full.xml" in record.getMessage() for record in caplog.records)


def test_render_all_txt_does_not_catch_unrecognized_namespace_error(tmp_path: Path) -> None:
    usc_dir = tmp_path / "usc"
    (usc_dir / "26").mkdir(parents=True)
    (usc_dir / "26" / "501.xml").write_text(_section_xml('<num value="501">§ 501.</num>', identifier="/us/usc/t26/s501"))
    (usc_dir / "51").mkdir(parents=True)
    (usc_dir / "51" / "bad.xml").write_text(f'<title xmlns="{USLM_NS}"><section identifier="/us/usc/t51/s1"/></title>')

    with pytest.raises(UnrecognizedNamespaceError):
        render_all_txt(usc_dir)


def test_render_all_txt_walks_nested_title_directories_and_returns_paths_sorted(tmp_path: Path) -> None:
    usc_dir = tmp_path / "usc"
    (usc_dir / "26").mkdir(parents=True)
    (usc_dir / "26" / "501.xml").write_text(_section_xml('<num value="501">§ 501.</num>', identifier="/us/usc/t26/s501"))
    (usc_dir / "10").mkdir(parents=True)
    (usc_dir / "10" / "5.xml").write_text(_section_xml('<num value="5">§ 5.</num>', identifier="/us/usc/t10/s5"))

    written = render_all_txt(usc_dir)

    assert written == sorted([usc_dir / "10" / "5.txt", usc_dir / "26" / "501.txt"])
