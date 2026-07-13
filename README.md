# United States Code

A CORS-accessible, static mirror of the current United States Code, plus federal legislation for the current Congress — both chunked to one file per citation/bill and published as static, pre-generated data.

## What this is

Two independent mirrors, sharing one repo because they're both prerequisite data plumbing for the same downstream bill-diffing app (see `THE-LAW-README.md`):

- **`usc/`** — the official U.S. Code, as published in USLM (United States Legislative Markup) XML by the Office of the Law Revision Counsel (OLRC), re-chunked into small per-citation files and hosted somewhere a browser can `fetch()` directly. The official sources (`uscode.house.gov`, `api.govinfo.gov`) don't send CORS headers, so no browser-side client can read them directly — this repo exists to make that possible without any live backend in the request path.
- **`bills/`** — bill status/text from `api.congress.gov` for the current Congress, mirrored server-side so the API key that gates it never has to ship to a browser. See "Bills" below and `BILLS-MIRROR-NOTES.md` for the design rationale.

There is no server, proxy, or API. Every file in `usc/` and `bills/` is static and pre-generated. Consumers read it straight off `raw.githubusercontent.com`.

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

## Bills

`bills/` mirrors federal legislation for the **current Congress only**, sourced from `api.congress.gov` — every bill the sync touches, whether still moving through Congress or already signed into law. A bill isn't removed once enacted; see below for why.

Each bill gets its own directory, just two files:

```
bills/{congress}/{type}/{number}/meta.json
bills/{congress}/{type}/{number}/text.xml
```

For example, H.R. 877 in the 119th Congress:

```
bills/119/hr/877/meta.json
bills/119/hr/877/text.xml
```

`meta.json` has a `status` key (the bill detail payload — title, sponsors, latestAction, `laws` if enacted, etc.) plus `cosponsors`/`committees`/`summaries` keys, each present only when that sub-resource is non-empty — an absent key means "none," not "not fetched":

```json
{
  "status": { "title": "...", "latestAction": {"...": "..."}, "...": "..." },
  "cosponsors": [ {"...": "..."} ]
}
```

`type` is lowercase, one of congress.gov's own eight bill-type codes:

| Type | Full name | Can it become law? |
|---|---|---|
| `hr` | House Bill | Yes — needs both chambers + President (or a veto override). |
| `s` | Senate Bill | Yes — same path, Senate-originated. |
| `hjres` | House Joint Resolution | Yes — functionally almost identical to a bill. Constitutional amendments use this type, but skip the President. |
| `sjres` | Senate Joint Resolution | Yes — same, Senate-originated. |
| `hres` | House Simple Resolution | No — affects only the House itself (internal rules, expressing an opinion); never goes to the Senate or President. |
| `sres` | Senate Simple Resolution | No — same, Senate-only. |
| `hconres` | House Concurrent Resolution | No — needs both chambers to agree, but no presidential signature (e.g. setting a joint adjournment date). |
| `sconres` | Senate Concurrent Resolution | No — same, Senate-originated. |

Only the four "Yes" types can ever get a `laws` entry under `meta.json`'s `status` key — a fully-adopted `hres`/`sres`/`hconres`/`sconres` has a terminal state of "Agreed to," not "enacted," so it never gets one even once it's completely done moving.

`text.xml` is always the *most recent* published text version only (introduced, reported, engrossed, etc. — a bill that's moved through several stages only keeps its latest one). History of how a bill's text changed lives in this repo's own `git log`, the same way `usc/` doesn't keep old release points' text as separate files either.

**A bill that becomes law stays in the mirror — it isn't deleted.** `meta.json`'s `status.laws` field is how a consumer tells "still pending" apart from "already enacted"; without keeping the bill, that distinction collapses into "not on disk," which is also what a bill that simply hasn't been synced yet looks like. Enacted bills only disappear when the whole `bills/{congress}/` tree is wiped at a Congress rollover, the same as everything else from that Congress. Every bill's `status` links back to its page on congress.gov via its own fields.

### Finding the latest bills

`bills/{congress}/index.json` lists every mirrored bill for that Congress — `type`, `number`, `title`, `latestAction`, `enacted` — sorted with the most recently *acted-on* bill first, so a consumer can find what's new without opening every `meta.json`:

```json
[
  { "type": "hr", "number": "877", "title": "...", "latestAction": {"actionDate": "2026-07-13", "text": "..."}, "enacted": false },
  { "type": "s", "number": "1003", "title": "...", "latestAction": {"actionDate": "2026-06-26", "text": "Became Public Law No: 119-100."}, "enacted": true }
]
```

The sort key is `latestAction.actionDate` — the date of the most recent actual legislative action (a referral, a vote, a markup) — deliberately **not** `updateDate`. `updateDate` bumps just as often for congress.gov's own backend reprocessing of old records as it does for something a human in Congress actually did, so it doesn't mean "this changed recently" the way `latestAction` does; see `BILLS-MIRROR-NOTES.md` for how that was confirmed. The index is rebuilt from every bill currently on disk at the end of each sync run, not just the ones that run touched, so it always reflects the full current archive.

`index.json` lists every bill ever mirrored for that Congress, which grows toward the whole Congress's bill count over its two-year life (tens of thousands, once enacted bills stop being pruned — see above). Two more files give bounded-size views of only what's recent, same fields, same sort:

- `bills/{congress}/index-7d.json` — bills with a `latestAction` in the last 7 days.
- `bills/{congress}/index-30d.json` — bills with a `latestAction` in the last 30 days.

A consumer that just wants "what changed recently" reads one of those instead of downloading and filtering the full index. Anyone who wants the complete set still has `index.json`, or can walk the directory tree directly.

**Update schedule:** a scheduled job runs daily — bills move far faster than USC release points.

## Principles

- **Deterministic.** The sync and chunking process is structural re-formatting of already-authoritative text — no interpretation, fully auditable.
- **No live backend.** The scheduled sync jobs are build-time batch processes; nothing runs at request time.
- **Traceable to source.** Every served citation or bill links back to its official source (OLRC or congress.gov).
- **Open.** MIT-licensed, no AI-tooling attribution in the shipped artifact.

## Development

The sync/chunking job is Python, targeting **3.13**. Environment and dependency management use the standard toolchain — stdlib `venv` + `pip`, with [pip-tools](https://github.com/jazzband/pip-tools) for locking — not `uv` or `poetry`.

Setup:

```sh
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Running the USC sync end to end (download -> chunk -> render):

```sh
uscode-mirror-sync
```

Discovers OLRC's latest release point and, if `usc/` isn't already synced to it (tracked via the
`usc/.release-point` marker file), wipes and fully rebuilds `raw/` and `usc/` from scratch —
otherwise it's a no-op. Pass `--force` to rebuild even when already current.

Running the bills sync (needs a free key from [api.congress.gov](https://api.congress.gov/sign-up/), set as `CONGRESS_API_KEY`):

```sh
export CONGRESS_API_KEY=your-key-here   # or put it in a local, gitignored .env
congress-bills-sync
```

Discovers the current Congress and, if `bills/` isn't already tracking it, wipes and re-bootstraps
from `INITIAL_SYNC_CUTOFF` (see `BILLS-MIRROR-NOTES.md`) — otherwise it incrementally syncs from
the `bills/.last-sync` watermark. `--limit N` caps how many bills are processed, for manual testing
only; the scheduled workflow never passes it.

Layout:

- `src/uscode_mirror/` — the USC mirror package
- `src/congress_bills_mirror/` — the bills mirror package
- (src layout for both, so tests import the installed package, not the working directory)
- `tests/` — pytest suite for both packages
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
