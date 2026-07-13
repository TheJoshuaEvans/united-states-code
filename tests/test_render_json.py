import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from uscode_mirror.chunk import USLM_NS
from uscode_mirror.render_json import (
    NotASectionError,
    UnrecognizedNamespaceError,
    element_to_json,
    render_all_json,
    render_section_json,
)

_XHTML_NS = "http://www.w3.org/1999/xhtml"


def _section_xml(body: str, *, with_html_ns: bool = False, identifier: str = "/us/usc/t51/s10101") -> str:
    """Build a standalone per-section chunk, matching chunk.py's own output shape and attribute
    order (style, id, identifier), optionally also declaring the XHTML namespace some real notes
    use for embedded tables/inline formatting."""
    html_ns = f' xmlns:html="{_XHTML_NS}"' if with_html_ns else ""
    return f'<section xmlns="{USLM_NS}"{html_ns} style="-uslm-lc:I80" id="idxyz" identifier="{identifier}">{body}</section>'


def _flatten(node: dict) -> str:  # type: ignore[type-arg]
    """Reconstruct the text a rendered node represents, the same way Element.itertext() would."""
    parts = [node["text"] or ""]
    for child in node["children"]:
        parts.append(_flatten(child))
        parts.append(child["tail"] or "")
    return "".join(parts)


def test_element_to_json_mirrors_tag_attrib_text_children_tail_in_order() -> None:
    # style/class in the fixture (section's own style, num's class) are stripped -- see
    # test_element_to_json_strips_style_and_class_attributes for that behavior in isolation; this
    # test's job is everything else about the mirror (order, text, tail, non-styling attribs).
    body = '<num value="10101" class="bold">§ 10101.</num> and more before heading<heading> Definitions</heading>'
    xml = _section_xml(body)

    result = element_to_json(ET.fromstring(xml))

    assert result == {
        "tag": "section",
        "attrib": {"id": "idxyz", "identifier": "/us/usc/t51/s10101"},
        "text": None,
        "children": [
            {
                "tag": "num",
                "attrib": {"value": "10101"},
                "text": "§ 10101.",
                "children": [],
                "tail": " and more before heading",
            },
            {"tag": "heading", "attrib": {}, "text": " Definitions", "children": [], "tail": None},
        ],
        "tail": None,
    }
    # dict == comparison doesn't check key order -- assert it explicitly, since remaining
    # attribute order is still part of what this mirror promises.
    assert list(result["attrib"]) == ["id", "identifier"]


def test_element_to_json_strips_style_and_class_attributes() -> None:
    # Models the real corpus: style/class attributes are OLRC's presentational markup only (USLM
    # internal style codes, CSS-like classes) -- no legal-structure or cross-reference meaning.
    # An element whose only attributes were style/class ends up with an empty attrib dict, same
    # as any other attribute-less element.
    body = '<content><p style="-uslm-lc:I11" class="indent0">Operative text.</p></content>'
    xml = _section_xml(body)

    result = element_to_json(ET.fromstring(xml))

    p_node = result["children"][0]["children"][0]
    assert p_node["attrib"] == {}
    # section's own style (added by _section_xml) is stripped too; id/identifier survive.
    assert result["attrib"] == {"id": "idxyz", "identifier": "/us/usc/t51/s10101"}


def test_element_to_json_prefixes_xhtml_tags_and_leaves_uslm_tags_bare() -> None:
    # Models the real corpus: a note mixing a bare USLM <p> alongside an XHTML <html:table>,
    # both declared in the same document -- the case a bare/unprefixed mirror would collide.
    body = (
        "<notes>"
        '<note topic="miscellaneous">'
        "<p>Plain USLM paragraph.</p>"
        "<html:table><html:tbody><html:tr><html:td>Cell text</html:td></html:tr></html:tbody></html:table>"
        "</note>"
        "</notes>"
    )
    xml = _section_xml(body, with_html_ns=True)

    result = element_to_json(ET.fromstring(xml))

    note = result["children"][0]["children"][0]
    p_node, table_node = note["children"]
    assert p_node["tag"] == "p"
    assert table_node["tag"] == "html:table"
    tbody_node = table_node["children"][0]
    tr_node = tbody_node["children"][0]
    td_node = tr_node["children"][0]
    assert [tbody_node["tag"], tr_node["tag"], td_node["tag"]] == ["html:tbody", "html:tr", "html:td"]
    assert td_node["text"] == "Cell text"


def test_element_to_json_round_trips_against_itertext() -> None:
    # Nested mixed content shaped like a real notes section: running text interleaved with ref,
    # quotedContent (itself containing a ref), and date.
    body = (
        "<content><p>See "
        '<ref href="/us/usc/t51/s10102">section 10102</ref> and '
        '<quotedContent origin="/us/pl/100/1">'
        '<ref href="/us/usc/t51/s1">“Section 1”</ref> quoted text'
        "</quotedContent> "
        'effective <date date="1958-07-29">July 29, 1958</date>.</p></content>'
    )
    xml = _section_xml(body)
    root = ET.fromstring(xml)

    result = element_to_json(root)

    assert _flatten(result) == "".join(root.itertext())


def test_element_to_json_raises_on_unrecognized_namespace() -> None:
    xml = (
        f'<section xmlns="{USLM_NS}" xmlns:foreign="urn:bogus" identifier="/us/usc/t51/s1">'
        '<num value="1">§ 1.</num><foreign:weird>oops</foreign:weird></section>'
    )

    with pytest.raises(UnrecognizedNamespaceError):
        element_to_json(ET.fromstring(xml))


def test_render_section_json_produces_compact_unicode_preserving_bytes_with_trailing_newline(tmp_path: Path) -> None:
    heading = "<heading> Exemption—“special” cases</heading>"
    xml = _section_xml(f'<num value="501">§ 501.</num>{heading}', identifier="/us/usc/t26/s501")
    xml_path = tmp_path / "501.xml"
    xml_path.write_text(xml)

    result = render_section_json(xml_path)

    expected = element_to_json(ET.fromstring(xml))
    expected_bytes = (json.dumps(expected, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    assert result == expected_bytes
    assert "—“special”".encode() in result
    assert b"\\u" not in result


def test_render_section_json_raises_not_a_section_error_on_appendix_root(tmp_path: Path) -> None:
    xml_path = tmp_path / "full.xml"
    xml_path.write_text(f'<uscDoc xmlns="{USLM_NS}"><main/></uscDoc>')

    with pytest.raises(NotASectionError):
        render_section_json(xml_path)


def test_render_section_json_raises_unrecognized_namespace_error_on_an_unknown_root_shape(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    xml_path = tmp_path / "weird.xml"
    xml_path.write_text(f'<title xmlns="{USLM_NS}"><section identifier="/us/usc/t51/s1"/></title>')

    with caplog.at_level(logging.ERROR, logger="uscode_mirror.render_json"):
        with pytest.raises(UnrecognizedNamespaceError):
            render_section_json(xml_path)

    assert any(record.levelno == logging.ERROR for record in caplog.records)


def test_render_all_json_skips_appendix_full_xml_and_does_not_abort_batch(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    usc_dir = tmp_path / "usc"
    (usc_dir / "26").mkdir(parents=True)
    (usc_dir / "26" / "501.xml").write_text(_section_xml('<num value="501">§ 501.</num>', identifier="/us/usc/t26/s501"))
    (usc_dir / "5a").mkdir(parents=True)
    (usc_dir / "5a" / "full.xml").write_text(f'<uscDoc xmlns="{USLM_NS}"><main/></uscDoc>')

    with caplog.at_level(logging.INFO, logger="uscode_mirror.render_json"):
        written = render_all_json(usc_dir)

    assert written == [usc_dir / "26" / "501.json"]
    assert (usc_dir / "26" / "501.json").exists()
    assert not (usc_dir / "5a" / "full.json").exists()
    assert any(record.levelno == logging.INFO and "full.xml" in record.getMessage() for record in caplog.records)


def test_render_all_json_does_not_catch_unrecognized_namespace_error(tmp_path: Path) -> None:
    usc_dir = tmp_path / "usc"
    (usc_dir / "26").mkdir(parents=True)
    (usc_dir / "26" / "501.xml").write_text(_section_xml('<num value="501">§ 501.</num>', identifier="/us/usc/t26/s501"))
    (usc_dir / "51").mkdir(parents=True)
    (usc_dir / "51" / "bad.xml").write_text(f'<title xmlns="{USLM_NS}"><section identifier="/us/usc/t51/s1"/></title>')

    with pytest.raises(UnrecognizedNamespaceError):
        render_all_json(usc_dir)


def test_render_all_json_walks_nested_title_directories_and_returns_paths_sorted(tmp_path: Path) -> None:
    usc_dir = tmp_path / "usc"
    (usc_dir / "26").mkdir(parents=True)
    (usc_dir / "26" / "501.xml").write_text(_section_xml('<num value="501">§ 501.</num>', identifier="/us/usc/t26/s501"))
    (usc_dir / "10").mkdir(parents=True)
    (usc_dir / "10" / "5.xml").write_text(_section_xml('<num value="5">§ 5.</num>', identifier="/us/usc/t10/s5"))

    written = render_all_json(usc_dir)

    assert written == sorted([usc_dir / "10" / "5.json", usc_dir / "26" / "501.json"])
