# USC Mirror — design notes

Working notes from the planning conversation for **The Law**'s prerequisite sub-project: a CORS-accessible mirror of the current United States Code. Carry this into whatever new repo/directory this gets built in — it's a standalone concern from the bill-diffing tool itself, useful independent of it.

## The problem

The diff engine needs two things: a pending bill's amendatory instructions (have this — `api.congress.gov`, CORS-open) and the current text of the U.S. Code section(s) each instruction targets (don't have this). Without the second piece, we can display raw "strike X, insert Y" instructions but can't resolve them into an actual before/after — which is the entire point of the project. `eCFR.gov` doesn't substitute: it covers the Code of Federal Regulations, a categorically different body of law (agency rules, not statutes), so a bill amending "title 26, United States Code" has no meaningful mapping onto CFR text.

**Confirmed live (2026-07-09), both GET and OPTIONS preflight, no `Access-Control-*` headers on either:**
- `api.govinfo.gov` (all endpoints checked: `/collections`, `/collections/USCODE`, `/search`, `/packages/.../summary`) and its bulk-data mirror at `www.govinfo.gov/bulkdata/...`
- `uscode.house.gov` — the actual official OLRC USLM source, checked on a real release file and via explicit OPTIONS preflight, not just the HTML landing page

Neither official host serves CORS headers. No browser-side fetch will work against either, ever, without them changing something on their end (not something we can wait on — the tool needs to work regardless).

## What's being replicated

Officially: **the United States Code**, published in **USLM (United States Legislative Markup)** XML by the **Office of the Law Revision Counsel (OLRC)**, distributed as periodic **"Release Points"** — OLRC's own term for a snapshot taken immediately after a given Public Law is incorporated.

- Schema: [usgpo/uslm](https://github.com/usgpo/uslm) (official, open)
- Source: `https://uscode.house.gov/download/releasepoints/us/pl/{congress}/{law}/xml_usc{title}@{congress}-{law}.zip` (per-title) or `xml_uscAll@{congress}-{law}.zip` (full corpus)
- Full history/index: `https://uscode.house.gov/download/priorreleasepoints.htm`

## Recommended architecture: scheduled mirror, not a live proxy

A CI job (e.g. GitHub Actions on a schedule) does a plain server-side fetch of official USLM XML — CORS is a browser-enforced policy, so it doesn't apply to a CI runner — parses it, and publishes the result somewhere CORS-open. The live site's runtime request path stays 100% static-file fetch, same as everything else in the "no backend" architecture; the only new thing is a periodic, fully mechanical batch job with nothing to interpret and nothing running at request time.

**Confirmed CORS-open hosting target:** `raw.githubusercontent.com` returns `Access-Control-Allow-Origin: *` on `GET` (re-verified live 2026-07-09, plus OPTIONS, which also carries the header despite returning 403). GitHub Pages independently tested live the same day on four plain `*.github.io` sites — also `200` + `Access-Control-Allow-Origin: *` on `GET` — so the assumption held, but **the actual plan is to read straight off `raw.githubusercontent.com`**, not Pages: no `gh-pages` branch or build step needed, the sync job just commits chunked JSON to the repo and the app fetches it directly by path.

**Rejected alternatives, and why:**
- **Self-hosted CORS-adding proxy** (Cloudflare Worker, etc.) — this is live backend infrastructure in the runtime request path: an uptime dependency, a bill, a single point of failure. Contradicts "no intermediary infrastructure" far more directly than a batch mirror does.
- **Public third-party CORS proxy** (corsproxy.io etc.) — no control, no audit trail, sees every citation every user looks up, can vanish or start tampering at any time. Wrong trust model for civic infrastructure.
- **Third-party mirrors** (Cornell LII, Justia, etc.) — better-intentioned but replaces "official primary source" with "someone else's copy" as the actual data dependency — directly against the project's "every output links to its primary source" principle.

## Critical sizing constraint: don't mirror per-title

Measured directly from the current full release (2026-07-09):

| | Size |
|---|---|
| Full USC, compressed zip | 104 MB |
| Full USC, uncompressed XML | 696 MB across 58 files |
| Largest single title (Title 42, Public Health & Welfare) | **113 MB** — exceeds GitHub's 100 MB hard per-file push limit |
| Title 26 (tax), Title 10 (armed forces) | ~55 MB each — over GitHub's 50 MB soft warning threshold |

**Conclusion: the sync job must parse each title's XML and re-emit it as one small file per citation** (e.g. `usc/json/26/501/c/3.json`), not one blob per title. This isn't just a workaround for git's file-size limits — it matches the actual access pattern (nobody wants the whole of Title 42, they want one section) and avoids forcing a mobile user to download 113 MB to read one subsection. No Git LFS needed if files are chunked this small.

## Output formats: three, from one parse pass

**Decided: emit three independent per-citation formats**, not just JSON:

- **`.xml`** — the official USLM markup, sliced to just that citation's subtree. Not byte-identical to a substring of the source file: the extracted root needs the USLM `xmlns` re-declared to be valid standalone XML on its own. Still purely structural re-wrapping, no content changes — consistent with "deterministic only."
- **`.json`** — structured translation for the diff engine and any UI consumer; the one place that also carries editorial notes, source-credit, and cross-reference metadata (tagged as such, distinguishable from operative text).
- **`.txt`** — operative legal text only, plain and readable (e.g. `(a) Short title.—This Act may be cited as...`). No notes, no source credit, no cross-ref annotations — just the text of the law itself. Decided over including notes inline because plain text has no structure to keep editorial commentary visually distinct from statutory text.

**Revised (was: fanned out from one shared in-memory parsed node per citation in a single pass).** `.json` rendering, as actually implemented (`render_json.py`), is a deliberately separate second pass: it reads only the already-persisted `usc/{title}/{section}.xml` chunk `chunk.py` wrote, never `raw/`, never `chunk.py`'s own in-memory tree. This still satisfies the original "can't drift" goal, by a different and arguably more robust mechanism — JSON is derived deterministically from the exact bytes already published as the `.xml` chunk, which *is* the actual source of truth a consumer fetches, rather than from a second serializer call against a shared tree that could in principle diverge from what `chunk.py`'s own `serialize_section` wrote to disk. `.txt` rendering isn't built yet; whether it follows the same read-the-xml-chunk pattern or the original shared-pass idea is still open. Directory layout, as actually implemented: neighbors, not format-first, e.g. `usc/26/501.xml`, `usc/26/501.json`, `usc/26/501.txt` (see README's "Directory layout" section, which is authoritative).

**Storage cost:** roughly triples total repo size vs. JSON-only (XML chunks re-add namespace boilerplate per file, JSON carries the most structure, text is the leanest). Individual file sizes stay trivially small either way — chunking already solved the 100 MB hard-limit problem, this only multiplies file *count*. Total repo size landing in the 1–2 GB range is a soft concern (clone time, GitHub's storage-quota nag threshold) but not a hard blocker.

## Update cadence and freshness (measured, not assumed)

Pulled directly from OLRC's own release-point history:

- **Cadence:** roughly every 1–3 weeks during active legislative periods, with real multi-month gaps during lighter periods (e.g. recesses) — not staffer neglect, just less enacted volume to incorporate.
- **Enactment-to-availability lag:** at least ~2 weeks observed directly — Public Law 119-100 was signed 06/26/2026; as of 07/09/2026 (13 days later) its release point exists in the page's HTML but is still commented out, i.e. not yet live.
- **Freshness is per-title, not uniform.** Each release point only updates the specific titles the triggering Public Law actually touched (one release point touched 35 titles at once; another touched just 1). A title nobody's amended recently is fully current, just untouched — not stale.

**Design implication:** the sync job should walk the *entire* release-point history, not just grab "the latest," so it knows — per title — exactly which Public Law each title is current through. Mirror OLRC's own "current through Pub. L. X" currency marker as provenance metadata on every emitted citation file. This is a freebie for the project's determinism/traceability principle: every citation served can honestly state its own freshness instead of implying a real-time accuracy it doesn't have.

**Decided: sync runs Mon/Wed/Fri** (GitHub Actions `cron: '0 8 * * 1,3,5'`, time arbitrary/adjustable) — more frequent than the measured 1–3 week release cadence, but the job is a cheap no-op when nothing new has posted, so the only cost of running "too often" is a few wasted CI minutes in exchange for picking up a new release point sooner once OLRC actually publishes it.

## Inherited principles (from the main project's README/CLAUDE.md — this sub-project should follow the same rules)

- Deterministic only — the sync/chunking process is structural re-formatting of already-authoritative text, no interpretation, fully auditable.
- No live backend / no intermediary infrastructure at request time (a scheduled batch job is the one accepted exception, and only because it's build-time, not request-time).
- Every served citation should link back to its actual official `uscode.house.gov` source for independent verification, even though it's being served from a mirror.
- MIT-equivalent openness, no AI-tooling attribution in the shipped artifact.

## Chunking granularity: section-level, confirmed safe by measurement

Before committing to whole-section chunking, measured every one of the 65,680 `<section>` elements in the full 119-99 release (serialized subtree size, via `raw/xml_uscAll@119-99/`, gitignored working copy of the full corpus kept for this kind of direct measurement):

| | |
|---|---|
| Median | 4.7 KB |
| p90 | 23 KB |
| p99 | 101 KB |
| **Largest single section** | **1.67 MB** — 42 U.S.C. § 1395ww (Medicare hospital reimbursement formulas) |
| Sections over 1 MB | 4 |
| Sections over 500 KB | 30 |
| Sections over 100 KB | 664 (~1% of all sections) |

Other outliers: 42 U.S.C. § 1396a (Medicaid state plan requirements, 1.49 MB), 19 U.S.C. § 3805 (trade agreement implementation, 1.5 MB), 50 U.S.C. § 1701 (IEEPA), 26 U.S.C. §§ 168/401 (depreciation/pension rules) — exactly the sections with reputations for legislative sprawl.

**Decided: whole-section chunking, no subsection/paragraph split, no exceptions for outliers.** Even the single worst section in the entire Code (1.67 MB) is nowhere near GitHub's 100 MB hard limit or the 50 MB soft-warning threshold that motivated per-citation chunking in the first place. ~30 sections out of 65,680 costing a mobile reader over 500 KB instead of a few KB is a real but minor and rare cost, not worth the complexity of a finer, inconsistent split.

## Still open / not decided

- **`.json` rendering is implemented** (`render_json.py`), as a structural mirror of each already-chunked `.xml` file — every element, in document order, with its exact tag/text (keyed on ElementTree's own tag/attrib/text/children/tail vocabulary), not a curated/semantic re-modeling of the legal hierarchy. See the revised note under "Output formats" above for why it reads from `usc/*.xml` rather than sharing `chunk.py`'s in-memory tree.
- **Not strictly 1:1 on attributes:** `style`/`class` attributes are deliberately stripped (and the resulting `attrib` left empty, `{}`, for elements that only ever had those two) — user call, 2026-07-12: they're OLRC's presentational markup only (USLM's internal style codes like `-uslm-lc:I80`, CSS-like classes like `indent0`), carry no legal-structure or cross-reference meaning, and a reader who wants official styling has the `.xml`. Confirmed by inventorying every attribute name across the full 50,936-file corpus that `style`/`class` are the only two with no such meaning — everything else (`href`, `id`, `value`, `identifier`, `date`, `topic`, `role`, `type`, `origin`, `idref`, `status`, ...) is kept as-is.
- **Appendix titles (5A/11A/18A/28A) don't get `.json` yet.** Their whole-file `full.xml` is structurally very different (court rules, reorganization plans, real `<table>` markup) from an ordinary per-section chunk; rendering them is deliberately deferred rather than folded into the first pass.
- **Accepted size tradeoff:** the mirror is still measurably heavier than the source XML per file even after stripping styling — roughly 1.35x on real sections measured directly (e.g. `usc/26/501.xml` 286 KB → `.json` 392 KB; `usc/10/101.xml` 176 KB → 238 KB), because every node repeats the literal keys `tag`/`attrib`/`text`/`children`/`tail`. This compounds the repo-size concern already flagged above ("Storage cost," "Critical sizing constraint") but was a deliberate choice — fidelity of legal/structural content over compactness — not an oversight.
- **`.txt` rendering isn't built yet.**
