"""Slice one title's USLM XML into small standalone files, one per live citation.

Normal titles (`dc:type` == `USCTitle`) are walked top-down for `<section>` elements that carry an
`identifier` -- the walk claims a matched section's entire subtree and does not recurse into it
looking for more matches, because a section's own notes can quote old statute text that itself
carries a same-tag, differently-content, colliding `identifier` (see the Title 25 s1/s5329 case in
this module's tests). Sections with a `status` attribute (any value -- repealed, omitted,
transferred, renumbered, vacant, reserved) are dead and are skipped; their subtrees are still
claimed, not descended into, by the same walk.

Appendix titles (`dc:type` == `USCTitleAppendix`: 5A, 11A, 18A, 28A) aren't shaped like ordinary
titles at all -- their citable content is `<courtRule>`/`<reorganizationPlan>`, not `<section>`,
and they aren't the target of ordinary "Section X of title Y is amended" bill language -- so
they're mirrored as one whole-title file each, copied byte-for-byte, no parsing/rewrapping.
"""

from __future__ import annotations

import logging
import re
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)

USLM_NS = "http://xml.house.gov/schemas/uslm/1.0"
_DC_NS = "http://purl.org/dc/elements/1.1/"

_SECTION_TAG = f"{{{USLM_NS}}}section"
_META_TAG = f"{{{USLM_NS}}}meta"
_DOC_NUMBER_TAG = f"{{{USLM_NS}}}docNumber"
_DC_TYPE_TAG = f"{{{_DC_NS}}}type"

_NORMAL_TITLE_TYPE = "USCTitle"
_APPENDIX_TITLE_TYPE = "USCTitleAppendix"

_IDENTIFIER_RE = re.compile(r"^/us/usc/t(?P<title>[0-9a-z]+)/s(?P<number>[^/\s]+)$")
_SAFE_NUMBER_RE = re.compile(r"^[0-9A-Za-z]+(?:-[0-9A-Za-z]+)*$")

# Re-declares the USLM namespace as the *default* (unprefixed) namespace on any element this
# module serializes with ET.tostring, matching the source file's own style, instead of ET's
# auto-generated `ns0:` prefix. This mutates process-wide ElementTree state; it's idempotent, so
# registering once at import time is sufficient and safe.
ET.register_namespace("", USLM_NS)


class UnexpectedUslmFormatError(RuntimeError):
    """A title's XML didn't match the USLM structure this module relies on.

    Covers a missing/unrecognized `<dc:type>` or `<docNumber>`, and a live section whose
    `identifier` doesn't match the simple `/us/usc/t{title}/s{number}` shape this module uses to
    file it. This feeds a periodic sync job over data that could shift format in the future, so
    it's raised loudly and never caught inside this module -- silently mis-filing or dropping a
    citation would be worse than stopping the run for a human to look at. Contrast with
    `download.ReservedTitleError`, which is an *expected*, per-title condition that's deliberately
    caught and skipped.
    """


@dataclass(frozen=True)
class TitleMeta:
    """The two facts this module needs out of a title's `<meta>` block."""

    title_code: str
    is_appendix: bool


def read_title_meta(root: ET.Element, source: Path) -> TitleMeta:
    """Read `title_code`/`is_appendix` out of a parsed title document's `<meta>` block."""
    meta = root.find(_META_TAG)
    if meta is None:
        logger.error("No <meta> element found in %s", source)
        raise UnexpectedUslmFormatError(f"No <meta> element found in {source}")

    dc_type_elem = meta.find(_DC_TYPE_TAG)
    dc_type = dc_type_elem.text.strip() if dc_type_elem is not None and dc_type_elem.text else None
    if dc_type not in (_NORMAL_TITLE_TYPE, _APPENDIX_TITLE_TYPE):
        logger.error("Unrecognized or missing <dc:type> (%r) in %s", dc_type, source)
        raise UnexpectedUslmFormatError(f"Unrecognized or missing <dc:type> ({dc_type!r}) in {source}")

    doc_number_elem = meta.find(_DOC_NUMBER_TAG)
    doc_number = doc_number_elem.text.strip() if doc_number_elem is not None and doc_number_elem.text else None
    if not doc_number:
        logger.error("Missing or empty <docNumber> in %s", source)
        raise UnexpectedUslmFormatError(f"Missing or empty <docNumber> in {source}")

    return TitleMeta(title_code=doc_number.lower(), is_appendix=(dc_type == _APPENDIX_TITLE_TYPE))


def iter_claimed_sections(elem: ET.Element) -> Iterator[ET.Element]:
    """Depth-first walk yielding every `<section identifier=...>`, live or dead.

    Stops descending the moment an element matches (tag == section, identifier present) -- see
    module docstring. A flat `.iter()`/`.findall('.//section')` is NOT equivalent and must not be
    used here: it would also surface a dead/quoted section's nested colliding identifier as if it
    were its own top-level citation.
    """
    if elem.tag == _SECTION_TAG and elem.get("identifier") is not None:
        yield elem
        return
    for child in elem:
        yield from iter_claimed_sections(child)


def live_sections(root: ET.Element) -> list[ET.Element]:
    """Every claimed section from `iter_claimed_sections` that has no `status` attribute."""
    return [section for section in iter_claimed_sections(root) if section.get("status") is None]


def section_number_from_identifier(identifier: str, title_code: str, source: Path) -> str:
    """Extract and normalize (EN DASH -> ASCII hyphen) a live section's number.

    Raises UnexpectedUslmFormatError if `identifier` doesn't match `/us/usc/t{title_code}/s{number}`
    exactly (including a title-token mismatch, or a number containing anything other than
    alphanumerics/hyphens after normalization) -- a live section's identifier is documented and
    measured to always be exactly one clean path with no spaces and no "...", so any deviation is a
    real anomaly, not something to route around.
    """
    match = _IDENTIFIER_RE.match(identifier)
    if match is None or match.group("title") != title_code:
        logger.error("Identifier %r in %s doesn't match /us/usc/t%s/s{number}", identifier, source, title_code)
        raise UnexpectedUslmFormatError(
            f"Live section identifier {identifier!r} in {source} doesn't match the expected "
            f"/us/usc/t{title_code}/s{{number}} shape"
        )

    number = match.group("number").replace("–", "-")
    if not _SAFE_NUMBER_RE.match(number):
        logger.error("Identifier %r in %s has an unexpected section number shape: %r", identifier, source, number)
        raise UnexpectedUslmFormatError(
            f"Live section identifier {identifier!r} in {source} has an unexpected section number shape: {number!r}"
        )
    return number


def serialize_section(section: ET.Element) -> bytes:
    """Serialize one claimed `<section>` element into a standalone, valid, re-parseable XML document."""
    return cast(bytes, ET.tostring(section, encoding="UTF-8", xml_declaration=True))


def chunk_title(xml_path: Path, usc_dir: Path) -> list[Path]:
    """Chunk one title's extracted XML file into `usc_dir`, returning every path written.

    Normal titles: one `usc_dir/{title_code}/{section_number}.xml` per live section.
    Appendix titles: a single `usc_dir/{title_code}/full.xml`, the raw file copied byte-for-byte.
    """
    root = ET.parse(xml_path).getroot()
    meta = read_title_meta(root, xml_path)
    title_dir = usc_dir / meta.title_code
    title_dir.mkdir(parents=True, exist_ok=True)

    if meta.is_appendix:
        dest = title_dir / "full.xml"
        shutil.copyfile(xml_path, dest)
        logger.info("Processed title %s (appendix): wrote %s", meta.title_code, dest)
        return [dest]

    written: list[Path] = []
    occurrences: dict[str, int] = {}
    for section in live_sections(root):
        identifier = section.get("identifier")
        if identifier is None:
            raise UnexpectedUslmFormatError(
                f"live_sections() yielded a section with no identifier from {xml_path} -- contract violation"
            )
        number = section_number_from_identifier(identifier, meta.title_code, xml_path)

        # Rare but real: two distinct, currently-operative sections occasionally share one
        # section number (e.g. 10 U.S.C. s130g), when two different laws each claimed the same
        # "next available" number. OLRC keeps both, cross-referenced by a footnote ("Another
        # section 130g is set out after this section."), rather than an alternate identifier --
        # so document order is the only signal available here to tell them apart. Suffix with an
        # underscore (not a hyphen) so it's visually distinct from a genuinely hyphenated section
        # number like 2000e-1.
        occurrence = occurrences.get(number, 0) + 1
        occurrences[number] = occurrence
        filename = f"{number}.xml" if occurrence == 1 else f"{number}_{occurrence}.xml"
        if occurrence > 1:
            logger.warning(
                "Duplicate live section number %r in %s (occurrence %d) -- writing to %s",
                number,
                xml_path,
                occurrence,
                filename,
            )

        dest = title_dir / filename
        dest.write_bytes(serialize_section(section))
        written.append(dest)
    logger.info("Processed title %s: wrote %d section file(s)", meta.title_code, len(written))
    return written


def chunk_all_titles(raw_dir: Path, usc_dir: Path) -> list[Path]:
    """Chunk every `*.xml` file directly in `raw_dir`, returning every path written.

    Mirrors `download.fetch_all_titles`'s shape: processes whatever's actually present (there's
    naturally no usc53.xml, since Title 53 is reserved and download.py never produces one -- no
    special-casing needed here). Unlike `fetch_all_titles`, a per-title `UnexpectedUslmFormatError`
    is NOT caught -- it propagates and aborts the whole batch; see that exception's docstring.
    """
    written: list[Path] = []
    for xml_path in sorted(raw_dir.glob("*.xml")):
        written.extend(chunk_title(xml_path, usc_dir))
    return written
