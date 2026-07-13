# United States Code

A CORS-accessible, static mirror of the current United States Code, chunked to one file per citation and published in three formats.

## What this is

The official U.S. Code, as published in USLM (United States Legislative Markup) XML by the Office of the Law Revision Counsel (OLRC), re-chunked into small per-citation files and hosted somewhere a browser can `fetch()` directly. The official sources (`uscode.house.gov`, `api.govinfo.gov`) don't send CORS headers, so no browser-side client can read them directly — this repo exists to make that possible without any live backend in the request path.

There is no server, proxy, or API. Every file in `usc/` is static and pre-generated. Consumers read it straight off `raw.githubusercontent.com`.

## Source

- Schema: [usgpo/uslm](https://github.com/usgpo/uslm)
- Content: OLRC's periodic USLM "Release Points," each representing the Code as it stood immediately after a given Public Law was incorporated.

## Formats

Each citation is published in three independent, parallel formats:

- **`.xml`** — the official USLM markup for that citation, sliced from the full title file. The USLM namespace is re-declared on the extracted root so the file is valid standalone XML; the content itself is unmodified.
- **`.json`** — a structural mirror of that citation's `.xml`: every element, in its original document order, with its exact tag/text — including OLRC's editorial notes, source-credit, and cross-reference metadata (tagged separately from the operative text, the same way the XML tags them). Not a curated or semantic re-modeling of the legal hierarchy. The one deliberate departure from 1:1: `style`/`class` attributes (OLRC's presentational markup — USLM's internal style codes, CSS-like classes) are stripped, since they carry no legal-structure or cross-reference meaning; every other attribute is kept. Anyone who wants the official styling has the `.xml`.
- **`.txt`** — the operative legal text only, plain and readable. No notes, no source credit, no cross-reference annotations.

All three formats represent the same underlying citation and never disagree with each other. `.json` is derived deterministically from the already-published `.xml` for that same citation (a separate rendering pass, not a shared in-memory parse), so it can't drift from it.

## Directory layout

Files are organized by title and section number, with all three formats living as neighbors:

```
usc/{title}/{section}.xml
usc/{title}/{section}.json
usc/{title}/{section}.txt
```

For example, 26 U.S.C. § 501 (including all of its subsections, such as (c)(3)):

```
usc/26/501.xml
usc/26/501.json
usc/26/501.txt
```

Chunking is at the whole-section level — a citation file contains its entire section, not just the specific subsection or paragraph requested. There is no subsection-level nesting in the path.

## Duplicate section numbers

Rarely, two different laws each claim the same "next available" section number within a title, and OLRC keeps both as distinct, currently-operative sections rather than silently dropping one — cross-referenced only by an editorial footnote (e.g. "Another section 130g is set out after this section."), not by a distinguishing identifier. When this happens, the first section in document order is mirrored normally (`usc/10/130g.xml`) and each subsequent one gets an underscore-numbered suffix (`usc/10/130g_2.xml`), not a hyphen — a hyphen is reserved for a section number that's genuinely part of the citation itself (e.g. `usc/42/2000e-1.xml`). This is not an official OLRC citation form; it's this mirror's own deterministic tie-break, applied in the order sections appear in the source file.

## Live sections only

Only sections currently in force are mirrored. OLRC marks a section as dead — repealed, omitted, transferred, renumbered, vacant, or reserved — with a `status` attribute in the source USLM; any section carrying one, regardless of its value, is intentionally excluded here. A citation pointing at a dead section will 404 against this mirror. Consumers should treat that 404 as "not currently in force," not as a fetch failure — the section may well appear elsewhere in the U.S. Code's own history, just not as live text.

## Appendix titles are whole-file, not per-citation

Four titles — 5A, 11A, 18A, and 28A — are "Appendix" titles rather than ordinary codified titles: they hold Presidential Reorganization Plans and the Federal Rules of Bankruptcy/Civil/Criminal/Appellate Procedure and Evidence, none of which went through the ordinary codification process (court rules are promulgated by the Supreme Court under the Rules Enabling Act, not enacted by Congress; reorganization plans restructure agencies, not the Code's topic hierarchy). Their content isn't shaped like `<section>` citations at all, and none of it is the target of ordinary "Section X of title Y is amended" bill language. Rather than per-citation chunking, each is mirrored as a single whole-title file:

```
usc/5a/full.xml
usc/11a/full.xml
usc/18a/full.xml
usc/28a/full.xml
```

## Freshness and provenance

Every emitted file carries OLRC's own "current through Public Law X-Y" marker for its title as metadata. Freshness is per-title: a title untouched by recent legislation is fully current, just unchanged, even if other titles have moved ahead of it. Every file also links back to its actual source on `uscode.house.gov` for independent verification.

## Update schedule

A scheduled job runs Monday, Wednesday, and Friday, checking OLRC's release-point history and re-syncing any titles with new Public Laws incorporated since the last run.

## Principles

- **Deterministic.** The sync and chunking process is structural re-formatting of already-authoritative text — no interpretation, fully auditable.
- **No live backend.** The scheduled sync job is a build-time batch process; nothing runs at request time.
- **Traceable to source.** Every served citation links back to its official OLRC source.
- **Open.** MIT-licensed, no AI-tooling attribution in the shipped artifact.

## Development

The sync/chunking job is Python, targeting **3.13**. Environment and dependency management use the standard toolchain — stdlib `venv` + `pip`, with [pip-tools](https://github.com/jazzband/pip-tools) for locking — not `uv` or `poetry`.

Setup:

```sh
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Running the sync end to end (download -> chunk -> render):

```sh
uscode-mirror-sync
```

Discovers OLRC's latest release point and, if `usc/` isn't already synced to it (tracked via the
`usc/.release-point` marker file), wipes and fully rebuilds `raw/` and `usc/` from scratch —
otherwise it's a no-op. Pass `--force` to rebuild even when already current.

Layout:

- `src/uscode_mirror/` — the package (src layout, so tests import the installed package, not the working directory)
- `tests/` — pytest suite
- `requirements.in` / `requirements-dev.in` — top-level runtime/dev dependencies, hand-edited
- `requirements.txt` / `requirements-dev.txt` — fully pinned lockfiles, generated via `pip-compile`; regenerate after editing the `.in` files and commit the result

Expectations before submitting code:

```sh
pytest              # tests pass
mypy src            # strict type checking passes
ruff check .        # lint passes
ruff format .       # formatting applied
```

`pyproject.toml` holds all tool config (mypy strict mode, ruff rules, pytest paths) — no scattered `setup.cfg`/`tox.ini`/etc.

## License

MIT — see [LICENSE](LICENSE).

## Note on AI use for development
Developers can use whatever tools they deem appropriate, what matters is the final quality of submitted code. However, this project does not consider AI development "first class" - and any AI specific configuration files must not be included in the project source.
