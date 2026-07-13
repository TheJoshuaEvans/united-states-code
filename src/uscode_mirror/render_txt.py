"""Mirror each already-chunked usc/{title}/{section}.xml file into a sibling .txt file.

Deliberately decoupled from chunk.py and render_json.py, the same way render_json.py is decoupled
from chunk.py: reads only from usc_dir (the persisted per-citation XML this repo publishes), never
raw/, never another module's in-memory tree -- so TXT generation can run, or be re-run, entirely
independently of the XML/JSON rendering passes. See USC-MIRROR-NOTES.md.

Unlike render_json.py, this is a curated rendering, not a structural mirror: `.txt` carries the
operative legal text only, with OLRC's editorial notes, source-credit, and footnote-reference
markers dropped entirely (see _EXCLUDED_TAGS / _FOOTNOTE_REF_CLASS below), and USLM's own
document structure translated into one line per structural element (num, heading, subsection,
paragraph, content, ...) rather than raw text/tail concatenation -- USLM's markup carries no
punctuation between a heading and the text that follows it (e.g. `<num>(a)</num><heading>
Exemption from taxation</heading><content>An organization...`), so a plain itertext()-style join
would run words together. Splitting on structural (block) element boundaries, instead of inventing
punctuation, keeps the rendering mechanical and deterministic while staying readable. A small set
of purely typographic tags (_INLINE_TAGS: ref, date, i, b, sup, sub, inline, quotedContent) are
exempted from this line-per-element treatment because they're embedded mid-sentence in the source
(e.g. "before <date>July 18, 1984</date>, or") and forcing a line break around them would fracture
running prose instead of clarifying it.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path

from uscode_mirror.chunk import USLM_NS

logger = logging.getLogger(__name__)

_XHTML_NS = "http://www.w3.org/1999/xhtml"
_SECTION_TAG = f"{{{USLM_NS}}}section"
_USC_DOC_TAG = f"{{{USLM_NS}}}uscDoc"

# Tags whose text is part of the surrounding sentence, not a structural element of its own --
# confirmed by inspecting real usage across the corpus: <ref>/<date> are inline citations and
# dates embedded mid-sentence, <i>/<b>/<sup>/<sub> are typographic emphasis, <inline> is USLM's own
# generic run of styled text (e.g. small-caps defined terms inside a heading), and <quotedContent>
# consistently opens right after a lead-in like "provided that:" in the same sentence. Every other
# tag (num, heading, chapeau, content, p, subsection, paragraph, subparagraph, clause, subclause,
# item, level, title, continuation, proviso, signature, name, ...) is treated as a block: it starts
# and ends its own line.
_INLINE_TAGS = frozenset({"ref", "date", "i", "b", "sup", "sub", "inline", "quotedContent"})

# Editorial content, not operative legal text -- dropped entirely (text and descendants), per this
# mirror's documented .txt contract ("no notes, no source credit, no cross-reference annotations").
# A dropped element's *tail* is NOT dropped: the tail is the parent's running text that continues
# after the excluded element, not part of the excluded element itself (e.g. a footnote definition
# embedded mid-sentence: "...loan under section 311<ref class=footnoteRef>1</ref><note>...</note>
# of the Rural Electrification Act..." -- dropping <note> must still keep " of the Rural
# Electrification Act..." which is its tail).
_EXCLUDED_TAGS = frozenset({"note", "notes", "sourceCredit"})

# The superscript footnote-reference marker (e.g. the "1" pointing at a <note type="footnote">) is
# a cross-reference annotation to editorial content being dropped anyway -- confirmed as the only
# `class` value ever used on <ref> across the corpus. An ordinary <ref> (a citation that's part of
# the operative sentence, e.g. "denied under section 502 or 503") has no such class and is kept.
_FOOTNOTE_REF_CLASS = "footnoteRef"


class NotASectionError(RuntimeError):
    """A usc_dir XML file's root is the known whole-title appendix shape (<uscDoc>), not <section>.

    Expected, not exceptional: the four appendix titles (5A/11A/18A/28A) are chunked by chunk.py
    as one whole-title usc/{title}/full.xml, not a per-citation <section>, and are out of scope
    for TXT rendering right now, same as render_json.py.
    """


class UnrecognizedNamespaceError(RuntimeError):
    """An element tag (or an unexpected root) used an XML namespace/shape this module doesn't know.

    Mirrors render_json.UnrecognizedNamespaceError: raised loudly, never caught inside this module
    -- a third namespace, or a root that's neither <section> nor <uscDoc>, means OLRC's markup
    moved in a way this mirror doesn't understand yet.
    """


def _tag_name(tag: str) -> str:
    """Render an ElementTree Clark-notation tag as this module's bare tag-name string.

    Same resolution as render_json._tag_name: USLM's default namespace renders bare (e.g.
    "section", "ref"); XHTML tags (only ever seen inside notes, which _EXCLUDED_TAGS drops before
    this distinction would matter) render with an "html:" prefix.
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


class _LineBreak:
    """Sentinel token marking a block-element boundary in the flattened token stream."""


_LINE_BREAK = _LineBreak()


def _collect(element: ET.Element, tokens: list[str | _LineBreak]) -> None:
    """Depth-first walk appending text/tail fragments and `_LINE_BREAK` sentinels to `tokens`.

    An excluded tag (_EXCLUDED_TAGS, or a footnote-marker <ref>) contributes neither its own text
    nor its children -- but its tail is still collected by the caller's loop, since the tail
    belongs to the parent's running text, not to the excluded element. See module docstring.
    """
    tag = _tag_name(element.tag)
    if tag in _EXCLUDED_TAGS or (tag == "ref" and element.get("class") == _FOOTNOTE_REF_CLASS):
        return

    is_block = tag not in _INLINE_TAGS
    if is_block:
        tokens.append(_LINE_BREAK)
    if element.text:
        tokens.append(element.text)
    for child in element:
        _collect(child, tokens)
        if child.tail:
            tokens.append(child.tail)
    if is_block:
        tokens.append(_LINE_BREAK)


def element_to_text(element: ET.Element) -> str:
    """Flatten one Element and its whole subtree into readable, newline-per-block plain text.

    Whitespace within a line is normalized (any run of whitespace, including a source line-wrap,
    collapses to a single space; leading/trailing whitespace is stripped) so a block's own text
    reads as one clean line regardless of how OLRC wrapped it in the source XML. Lines that are
    empty after normalization (e.g. a block whose only content was an excluded child) are dropped
    rather than emitted as blank lines.
    """
    tokens: list[str | _LineBreak] = []
    _collect(element, tokens)

    lines: list[str] = []
    current: list[str] = []
    for token in tokens:
        if isinstance(token, _LineBreak):
            words = "".join(current).split()
            if words:
                lines.append(" ".join(words))
            current = []
        else:
            current.append(token)
    words = "".join(current).split()
    if words:
        lines.append(" ".join(words))

    return "\n".join(lines)


def render_section_txt(xml_path: Path) -> bytes:
    """Parse one usc_dir/*.xml chunk and render it to unicode-preserving plain-text bytes.

    Raises NotASectionError if the root is the known appendix shape (<uscDoc>); raises
    UnrecognizedNamespaceError if the root is neither <section> nor <uscDoc>, or if any descendant
    tag uses an unrecognized namespace (see element_to_text / _tag_name).
    """
    root = ET.parse(xml_path).getroot()
    if root.tag == _USC_DOC_TAG:
        raise NotASectionError(f"Root element of {xml_path} is <uscDoc> (appendix full.xml)")
    if root.tag != _SECTION_TAG:
        logger.error("Root element of %s is %r, neither <section> nor <uscDoc>", xml_path, root.tag)
        raise UnrecognizedNamespaceError(f"Root element of {xml_path} is {root.tag!r}, not <section>")
    return (element_to_text(root) + "\n").encode("utf-8")


def render_all_txt(usc_dir: Path) -> list[Path]:
    """Render every usc_dir/**/*.xml chunk into a sibling .txt file, returning every path written.

    Walks usc_dir recursively (files live one level down: usc_dir/{title}/{section}.xml). A
    per-file NotASectionError (an appendix full.xml) is caught, logged at info level, and skipped
    -- it does not abort the batch. UnrecognizedNamespaceError is NOT caught here; see its
    docstring.
    """
    written: list[Path] = []
    for xml_path in sorted(usc_dir.rglob("*.xml")):
        try:
            payload = render_section_txt(xml_path)
        except NotASectionError:
            logger.info("Skipping %s: appendix full.xml, out of scope for TXT rendering", xml_path)
            continue
        dest = xml_path.with_suffix(".txt")
        dest.write_bytes(payload)
        written.append(dest)
    return written
