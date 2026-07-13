"""Mirror each already-chunked usc/{title}/{section}.xml file into a sibling .json file.

Deliberately decoupled from chunk.py: reads only from usc_dir (the persisted per-citation XML
this repo publishes), never raw/, never chunk.py's in-memory tree -- so JSON generation can run,
or be re-run, entirely independently of the XML chunking pass. See USC-MIRROR-NOTES.md.

The output is a structural mirror, not a curated re-modeling of the legal hierarchy: every
element survives in document order with its exact tag/text, keyed on ElementTree's own vocabulary
(tag/attrib/text/children/tail) so the shape is self-documenting against chunk.py. The one
deliberate exception is `style`/`class` attributes (see _STYLING_ATTRS): these carry OLRC's
presentational markup (USLM internal style codes, CSS-like classes), not legal-structure or
cross-reference meaning, and are dropped rather than mirrored -- a reader who wants the official
styling has the `.xml` file for that. Dropping them can leave an element's `attrib` empty (`{}`),
same as any other element that never had attributes.
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from uscode_mirror.chunk import USLM_NS

logger = logging.getLogger(__name__)

_XHTML_NS = "http://www.w3.org/1999/xhtml"
_SECTION_TAG = f"{{{USLM_NS}}}section"
_USC_DOC_TAG = f"{{{USLM_NS}}}uscDoc"

# Presentational-only attributes (USLM's own internal style codes, e.g. "-uslm-lc:I80", and
# CSS-like classes, e.g. "indent0", "centered smallCaps") -- confirmed by inventorying every
# attribute name across the full corpus that these two are the only ones with no legal-structure
# or cross-reference meaning. Everything else (href, id, value, identifier, date, topic, role,
# type, origin, idref, status, ...) is kept.
_STYLING_ATTRS = frozenset({"style", "class"})


class NotASectionError(RuntimeError):
    """A usc_dir XML file's root is the known whole-title appendix shape (<uscDoc>), not <section>.

    Expected, not exceptional: the four appendix titles (5A/11A/18A/28A) are chunked by chunk.py
    as one whole-title usc/{title}/full.xml, not a per-citation <section>, and are out of scope
    for JSON rendering right now. Mirrors download.ReservedTitleError: raised per-file by
    render_section_json, caught and logged-and-skipped per-item by render_all_json, never aborts
    the batch. Deliberately narrow (root must be exactly <uscDoc>) rather than "anything that
    isn't <section>" -- a genuinely unrecognized third root shape should raise loud via
    UnrecognizedNamespaceError instead of being silently absorbed here.
    """


class UnrecognizedNamespaceError(RuntimeError):
    """An element tag (or an unexpected root) used an XML namespace/shape this module doesn't know.

    Every per-section chunk file measured across the full corpus uses only two namespaces: the
    default USLM namespace, and XHTML (prefixed html: in source, for a handful of notes' embedded
    tables/inline formatting). This feeds a periodic sync job over data that could shift shape in
    the future -- a third namespace, or a root that's neither <section> nor <uscDoc>, means OLRC's
    markup moved in a way this mirror doesn't understand yet. Raised loudly, never caught inside
    this module, same as chunk.UnexpectedUslmFormatError.
    """


def _tag_name(tag: str) -> str:
    """Render an ElementTree Clark-notation tag as this mirror's JSON tag string.

    USLM's default namespace renders bare (e.g. "section", "ref"), matching the source XML's own
    unprefixed style. XHTML renders with an explicit "html:" prefix (e.g. "html:table", "html:p")
    because USLM and XHTML both define same-named elements (p, b, i, sub, sup) -- a bare mirror
    would silently collide two structurally different elements under one JSON tag string.
    """
    if not tag.startswith("{"):
        raise UnrecognizedNamespaceError(f"Tag {tag!r} has no XML namespace (expected USLM or XHTML)")
    uri, _, local = tag[1:].partition("}")
    if uri == USLM_NS:
        return local
    if uri == _XHTML_NS:
        return f"html:{local}"
    logger.error("Unrecognized XML namespace %r on tag %r", uri, tag)
    raise UnrecognizedNamespaceError(f"Unrecognized XML namespace {uri!r} on tag <{local}>")


def element_to_json(element: ET.Element) -> dict[str, Any]:
    """Recursively mirror one Element and its whole subtree into a JSON-able dict.

    Round-trippable on text: walking text, then each child (element_to_json(child), then that
    child's own tail), in order, reproduces exactly "".join(element.itertext()) for the whole
    subtree. NOT round-trippable on attributes -- _STYLING_ATTRS are dropped; see module
    docstring.
    """
    return {
        "tag": _tag_name(element.tag),
        "attrib": {k: v for k, v in element.attrib.items() if k not in _STYLING_ATTRS},
        "text": element.text,
        "children": [element_to_json(child) for child in element],
        "tail": element.tail,
    }


def render_section_json(xml_path: Path) -> bytes:
    """Parse one usc_dir/*.xml chunk and render it to compact, unicode-preserving JSON bytes.

    Raises NotASectionError if the root is the known appendix shape (<uscDoc>); raises
    UnrecognizedNamespaceError if the root is neither <section> nor <uscDoc>, or if any descendant
    tag uses an unrecognized namespace (see element_to_json / _tag_name).
    """
    root = ET.parse(xml_path).getroot()
    if root.tag == _USC_DOC_TAG:
        raise NotASectionError(f"Root element of {xml_path} is <uscDoc> (appendix full.xml)")
    if root.tag != _SECTION_TAG:
        logger.error("Root element of %s is %r, neither <section> nor <uscDoc>", xml_path, root.tag)
        raise UnrecognizedNamespaceError(f"Root element of {xml_path} is {root.tag!r}, not <section>")
    payload = element_to_json(root)
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def render_all_json(usc_dir: Path) -> list[Path]:
    """Render every usc_dir/**/*.xml chunk into a sibling .json file, returning every path written.

    Walks usc_dir recursively (files live one level down: usc_dir/{title}/{section}.xml). A
    per-file NotASectionError (an appendix full.xml) is caught, logged at info level, and skipped
    -- it does not abort the batch. UnrecognizedNamespaceError is NOT caught here; see its
    docstring.
    """
    written: list[Path] = []
    for xml_path in sorted(usc_dir.rglob("*.xml")):
        try:
            payload = render_section_json(xml_path)
        except NotASectionError:
            logger.info("Skipping %s: appendix full.xml, out of scope for JSON rendering", xml_path)
            continue
        dest = xml_path.with_suffix(".json")
        dest.write_bytes(payload)
        written.append(dest)
    return written
