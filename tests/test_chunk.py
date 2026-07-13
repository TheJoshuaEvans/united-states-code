import logging
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from uscode_mirror.chunk import (
    USLM_NS,
    TitleMeta,
    UnexpectedUslmFormatError,
    chunk_all_titles,
    chunk_title,
    iter_claimed_sections,
    live_sections,
    read_title_meta,
    section_number_from_identifier,
)

_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<uscDoc xmlns="http://xml.house.gov/schemas/uslm/1.0" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
)


def _title_xml(dc_type: str, doc_number: str, body: str) -> str:
    """Build a minimal standalone USLM title document around a `<main>` body fragment."""
    meta = f"<meta>\n<dc:type>{dc_type}</dc:type>\n<docNumber>{doc_number}</docNumber>\n</meta>\n"
    return f"{_HEADER}{meta}<main>\n{body}\n</main>\n</uscDoc>\n"


LIVE_SECTION = (
    '<section identifier="/us/usc/t51/s10101">'
    '<num value="10101">§ 10101.</num>'
    "<heading> Definitions</heading>"
    "<content><p>The term “Administration” means the National Aeronautics and Space Administration.</p></content>"
    "</section>"
)

SECOND_LIVE_SECTION = (
    '<section identifier="/us/usc/t51/s10102">'
    '<num value="10102">§ 10102.</num>'
    "<heading> Space Program</heading>"
    "<content><p>Some other operative text.</p></content>"
    "</section>"
)

DEAD_SECTION = (
    '<section status="repealed" identifier="/us/usc/t51/s99"><num value="99">§ 99.</num><heading> Repealed</heading></section>'
)

NO_IDENTIFIER_SECTION = (
    '<notes><note><p>Quoted text: <section><num value="">“SECTION 1.</num><heading>Quoted</heading></section></p></note></notes>'
)

# Models the real Title 25 collision: a live top-level `s1` ("Commissioner of Indian Affairs")
# coexists with a *different* real section (`s5329`) whose own content quotes an illustrative
# model agreement -- and OLRC's markup gives that quoted text's "SECTION 1." heading the exact
# same colliding identifier, with no `status` attribute either, so a status filter alone can't
# distinguish it from a real citation. Only the claim-and-stop walk in iter_claimed_sections
# resolves this correctly.
TITLE25_COLLISION_BODY = (
    '<section identifier="/us/usc/t25/s1">'
    '<num value="1">§ 1.</num>'
    "<heading> Commissioner of Indian Affairs</heading>"
    "<content><p>There shall be in the Department of the Interior a Commissioner of Indian Affairs.</p></content>"
    "</section>"
    '<section identifier="/us/usc/t25/s5329">'
    '<num value="5329">§ 5329.</num>'
    "<heading> Contract or grant specifications</heading>"
    "<content><p>The model agreement referred to reads as follows:</p>"
    '<section identifier="/us/usc/t25/s1">'
    '<num value="1">“SECTION 1.</num>'
    "<heading> AGREEMENT BETWEEN THE SECRETARY AND THE TRIBAL GOVERNMENT</heading>"
    "</section>"
    "</content>"
    "</section>"
)


def test_read_title_meta_extracts_lowercase_title_code_from_a_normal_title(tmp_path: Path) -> None:
    root = ET.fromstring(_title_xml("USCTitle", "51", LIVE_SECTION))

    meta = read_title_meta(root, tmp_path / "usc51.xml")

    assert meta == TitleMeta(title_code="51", is_appendix=False)


def test_read_title_meta_extracts_lowercase_title_code_from_an_appendix_title(tmp_path: Path) -> None:
    root = ET.fromstring(_title_xml("USCTitleAppendix", "5A", "<compiledAct/>"))

    meta = read_title_meta(root, tmp_path / "usc05A.xml")

    assert meta == TitleMeta(title_code="5a", is_appendix=True)


def test_read_title_meta_raises_on_unrecognized_dc_type(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    root = ET.fromstring(_title_xml("SomethingElse", "51", LIVE_SECTION))

    with caplog.at_level(logging.ERROR, logger="uscode_mirror.chunk"):
        with pytest.raises(UnexpectedUslmFormatError):
            read_title_meta(root, tmp_path / "usc51.xml")

    assert any(record.levelno == logging.ERROR for record in caplog.records)


def test_iter_claimed_sections_does_not_descend_into_a_claimed_sections_subtree() -> None:
    root = ET.fromstring(_title_xml("USCTitle", "25", TITLE25_COLLISION_BODY))

    claimed = list(iter_claimed_sections(root))

    assert [s.get("identifier") for s in claimed] == ["/us/usc/t25/s1", "/us/usc/t25/s5329"]
    heading = "".join(claimed[0].find(f"{{{USLM_NS}}}heading").itertext())  # type: ignore[union-attr]
    assert "Commissioner" in heading


def test_live_sections_excludes_status_bearing_sections() -> None:
    root = ET.fromstring(_title_xml("USCTitle", "51", LIVE_SECTION + DEAD_SECTION))

    identifiers = [s.get("identifier") for s in live_sections(root)]

    assert identifiers == ["/us/usc/t51/s10101"]


def test_live_sections_never_picks_up_elements_with_no_identifier_attribute() -> None:
    root = ET.fromstring(_title_xml("USCTitle", "51", LIVE_SECTION + NO_IDENTIFIER_SECTION))

    identifiers = [s.get("identifier") for s in live_sections(root)]

    assert identifiers == ["/us/usc/t51/s10101"]


def test_section_number_from_identifier_normalizes_en_dash_to_ascii_hyphen(tmp_path: Path) -> None:
    number = section_number_from_identifier("/us/usc/t6/s124h–1", "6", tmp_path / "usc06.xml")

    assert number == "124h-1"


def test_section_number_from_identifier_raises_on_a_title_token_mismatch(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    with caplog.at_level(logging.ERROR, logger="uscode_mirror.chunk"):
        with pytest.raises(UnexpectedUslmFormatError):
            section_number_from_identifier("/us/usc/t7/s100", "6", tmp_path / "usc06.xml")

    assert any(record.levelno == logging.ERROR for record in caplog.records)


def test_chunk_title_writes_one_file_per_live_section(tmp_path: Path) -> None:
    xml_path = tmp_path / "usc51.xml"
    xml_path.write_text(_title_xml("USCTitle", "51", LIVE_SECTION + SECOND_LIVE_SECTION))
    usc_dir = tmp_path / "usc"

    written = chunk_title(xml_path, usc_dir)

    assert sorted(written) == sorted([usc_dir / "51" / "10101.xml", usc_dir / "51" / "10102.xml"])
    assert (usc_dir / "51" / "10101.xml").exists()
    assert (usc_dir / "51" / "10102.xml").exists()


def test_chunk_title_disambiguates_two_distinct_live_sections_sharing_one_number(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    # Models the real 10 U.S.C. s130g case: two different, currently-operative sections that
    # happen to share one section number, each cross-referencing the other by footnote rather
    # than carrying a distinguishing identifier.
    first = (
        '<section identifier="/us/usc/t10/s130g">'
        '<num value="130g">§ 130g.</num>'
        "<heading> Oversight of sensitive activities</heading>"
        "<content><p>First section's operative text.</p></content>"
        "</section>"
    )
    second = (
        '<section identifier="/us/usc/t10/s130g">'
        '<num value="130g">§ 130g.</num>'
        "<heading> Notification requirements for waivers</heading>"
        "<content><p>Second section's operative text.</p></content>"
        "</section>"
    )
    xml_path = tmp_path / "usc10.xml"
    xml_path.write_text(_title_xml("USCTitle", "10", first + second))
    usc_dir = tmp_path / "usc"

    with caplog.at_level(logging.WARNING, logger="uscode_mirror.chunk"):
        written = chunk_title(xml_path, usc_dir)

    assert sorted(written) == sorted([usc_dir / "10" / "130g.xml", usc_dir / "10" / "130g_2.xml"])
    assert "Oversight of sensitive activities" in (usc_dir / "10" / "130g.xml").read_text()
    assert "Notification requirements for waivers" in (usc_dir / "10" / "130g_2.xml").read_text()
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_chunk_title_logs_completion_for_a_normal_title(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    xml_path = tmp_path / "usc51.xml"
    xml_path.write_text(_title_xml("USCTitle", "51", LIVE_SECTION + SECOND_LIVE_SECTION))
    usc_dir = tmp_path / "usc"

    with caplog.at_level(logging.INFO, logger="uscode_mirror.chunk"):
        chunk_title(xml_path, usc_dir)

    assert any("51" in record.getMessage() and "2" in record.getMessage() for record in caplog.records)


def test_chunk_title_skips_a_status_bearing_section_entirely(tmp_path: Path) -> None:
    xml_path = tmp_path / "usc51.xml"
    xml_path.write_text(_title_xml("USCTitle", "51", LIVE_SECTION + DEAD_SECTION))
    usc_dir = tmp_path / "usc"

    written = chunk_title(xml_path, usc_dir)

    assert written == [usc_dir / "51" / "10101.xml"]
    assert not (usc_dir / "51" / "99.xml").exists()


def test_chunk_title_resolves_the_title_25_shaped_identifier_collision_to_a_single_outer_file(tmp_path: Path) -> None:
    xml_path = tmp_path / "usc25.xml"
    xml_path.write_text(_title_xml("USCTitle", "25", TITLE25_COLLISION_BODY))
    usc_dir = tmp_path / "usc"

    written = chunk_title(xml_path, usc_dir)

    assert sorted(written) == sorted([usc_dir / "25" / "1.xml", usc_dir / "25" / "5329.xml"])
    s1_content = (usc_dir / "25" / "1.xml").read_text()
    assert "Commissioner" in s1_content
    assert "AGREEMENT BETWEEN THE SECRETARY" not in s1_content
    s5329_content = (usc_dir / "25" / "5329.xml").read_text()
    assert "AGREEMENT BETWEEN THE SECRETARY" in s5329_content


def test_chunk_title_output_xml_is_standalone_and_reparseable_with_the_uslm_namespace_declared(tmp_path: Path) -> None:
    xml_path = tmp_path / "usc51.xml"
    xml_path.write_text(_title_xml("USCTitle", "51", LIVE_SECTION))
    usc_dir = tmp_path / "usc"

    chunk_title(xml_path, usc_dir)

    output_bytes = (usc_dir / "51" / "10101.xml").read_bytes()
    assert output_bytes.startswith(b"<?xml")
    assert b"UTF-8" in output_bytes[:60]
    assert b'xmlns="http://xml.house.gov/schemas/uslm/1.0"' in output_bytes
    reparsed = ET.fromstring(output_bytes)
    assert reparsed.tag == f"{{{USLM_NS}}}section"


def test_chunk_title_uses_doc_number_not_the_input_filename_for_the_title_directory(tmp_path: Path) -> None:
    xml_path = tmp_path / "usc05A.xml"  # mixed-case filename, unlike docNumber "5a"
    xml_path.write_text(_title_xml("USCTitleAppendix", "5a", "<compiledAct/>"))
    usc_dir = tmp_path / "usc"

    written = chunk_title(xml_path, usc_dir)

    assert written == [usc_dir / "5a" / "full.xml"]


def test_chunk_title_copies_appendix_titles_whole_byte_for_byte(tmp_path: Path) -> None:
    xml_path = tmp_path / "usc11a.xml"
    xml_bytes = _title_xml("USCTitleAppendix", "11a", '<courtRule identifier="/us/usc/rule1001"/>').encode()
    xml_path.write_bytes(xml_bytes)
    usc_dir = tmp_path / "usc"

    written = chunk_title(xml_path, usc_dir)

    assert written == [usc_dir / "11a" / "full.xml"]
    assert (usc_dir / "11a" / "full.xml").read_bytes() == xml_bytes


def test_chunk_title_logs_completion_for_an_appendix_title(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    xml_path = tmp_path / "usc11a.xml"
    xml_path.write_bytes(_title_xml("USCTitleAppendix", "11a", '<courtRule identifier="/us/usc/rule1001"/>').encode())
    usc_dir = tmp_path / "usc"

    with caplog.at_level(logging.INFO, logger="uscode_mirror.chunk"):
        chunk_title(xml_path, usc_dir)

    assert any("11a" in record.getMessage() for record in caplog.records)


def test_chunk_title_raises_on_a_live_section_with_an_unexpected_identifier_shape(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    weird_section = '<section identifier="/us/usc/t51/s3 /us/usc/t51/s4"><num value="3, 4">§§ 3, 4.</num></section>'
    xml_path = tmp_path / "usc51.xml"
    xml_path.write_text(_title_xml("USCTitle", "51", weird_section))
    usc_dir = tmp_path / "usc"

    with caplog.at_level(logging.ERROR, logger="uscode_mirror.chunk"):
        with pytest.raises(UnexpectedUslmFormatError):
            chunk_title(xml_path, usc_dir)

    assert any(record.levelno == logging.ERROR for record in caplog.records)


def test_chunk_all_titles_processes_every_xml_file_in_the_given_directory(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "usc51.xml").write_text(_title_xml("USCTitle", "51", LIVE_SECTION))
    (raw_dir / "usc52.xml").write_text(_title_xml("USCTitle", "52", SECOND_LIVE_SECTION.replace("t51", "t52")))
    (raw_dir / "usc11a.xml").write_text(_title_xml("USCTitleAppendix", "11a", "<courtRule/>"))
    usc_dir = tmp_path / "usc"

    written = chunk_all_titles(raw_dir, usc_dir)

    assert sorted(written) == sorted(
        [
            usc_dir / "51" / "10101.xml",
            usc_dir / "52" / "10102.xml",
            usc_dir / "11a" / "full.xml",
        ]
    )


def test_chunk_all_titles_does_not_catch_an_unexpected_uslm_format_error(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "usc51.xml").write_text(_title_xml("USCTitle", "51", LIVE_SECTION))
    (raw_dir / "usc99.xml").write_text(_title_xml("NotARealType", "99", LIVE_SECTION))
    usc_dir = tmp_path / "usc"

    with pytest.raises(UnexpectedUslmFormatError):
        chunk_all_titles(raw_dir, usc_dir)
