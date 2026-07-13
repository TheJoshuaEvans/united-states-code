# The Law

## Mission

Most U.S. law is written as amendments to existing text, scattered across bills, session laws, and codifications. There's no easy way for an ordinary person to see what the law *currently is*, let alone what a pending bill would *actually change*. Bill text is written as surgical edit instructions ("strike subsection (b), redesignate...") that are nearly unreadable without resolving them against the current code.

A tool does already exist that does this, called the "[Comparative Print Suite](https://comp.xcentialcorp.com/)", but it is locked behind the congressional firewall and only approved staffers and legislatures are permitted to use it.

This project exists to make understanding the actual impact of newly proposed legislation as easy as possible, as a step toward a genuinely open government. One where any person, not just congressional staff, can independently verify what their government is doing. "The government" in its ideal form is just people trying to serve their community; understanding what it's actually done should not require an insider tool or a law degree.

Ideally, this should be good enough that congressional staff would rather use it than the internal tool they already have.

## Guiding principles

- **Deterministic only.** No ML/LLM in the diffing or amendment-resolution path. Every output must be mechanically traceable: this instruction, this source text, this rule, this result. If a pattern can't be resolved deterministically, the tool must say so loudly rather than guess.
- **No gatekeeping.** No login, no paywall, no firewall.
- **Every output links to its primary source.** The tool is a lens on the official bill/code text, never a replacement for it.
- **No intermediary infrastructure.** Prefer direct, client-side calls to official government APIs.

## Scope

**Initial target:** Federal legislation - bills that amend the U.S. Code - diffed against the current U.S. Code text. Federal regulations (CFR) are a natural extension since the data infrastructure (eCFR) already supports point-in-time diffing well.

**Not part of this project / may be addressed later:**
- Case law (fundamentally different problem — precedent and interpretation, not consolidated text).
- State, county, and city law. The data landscape there is fragmented (patchwork of formats, some codes gated behind exclusive publisher contracts like Municode/American Legal Publishing).
- Any personalized legal interpretation ("what does this mean for *you*") — descriptive diffing only.

**First milestone (MVP slice):** data plumbing only — reliably fetch and cache (a) bill text/status, (b) current U.S. Code text by citation, (c) current CFR text by citation. No amendment-instruction parsing yet; that's a distinct, harder phase that comes after the data layer is solid.

## Architecture

- **Pure HTML + JS + CSS. No backend, no server, no framework.** - The entire site runs client-side and talks directly to official government APIs. Confirmed technically viable. See verified findings below.
- **WCAG 2 AA compliance** - accessibility isn't optional for a public civic resource.
- **Mobile-friendly** - most people don't have a desktop.
- **Licensing:** [MIT](LICENSE). MIT keeps things simple while staying maximally embeddable.

## Verified findings (2026-07-09)

Live in-browser CORS checks against the real endpoints:

| Source | Covers | CORS | Auth |
|---|---|---|---|
| `api.congress.gov` | Bill text, bill status | `Access-Control-Allow-Origin: *` ✅ | Free API key (query param) |
| `api.govinfo.gov` | USC/CFR bulk data, metadata | No CORS support, on any endpoint ❌ | Free API key (query param) |
| `www.ecfr.gov/api/*` (versioner + search) | Current & historical CFR, point-in-time, diffing | `Access-Control-Allow-Origin: *`, full preflight support ✅ | **None required** |

govinfo can't be called directly from the browser, so it can't yet supply USC bulk data under the "no intermediary infrastructure" principle. CFR is unaffected (eCFR covers it). Sourcing USC text is an open problem — see Open decisions.

**API Key Use (resolved 2026-07-12, supersedes the plan below):** `api.congress.gov` is no longer called from the browser at all. Pending bills are mirrored server-side into this same repo — a scheduled GitHub Actions job holds the real `CONGRESS_API_KEY` as a secret, fetches bill status/cosponsors/committees/summaries/text, and commits static JSON/XML to `bills/`, exactly the same "no live backend, batch job builds static files" pattern already used for `usc/`. See `BILLS-MIRROR-NOTES.md` for the mirror's design. The client reads bills from `raw.githubusercontent.com` the same way it reads USC text: no key ever ships to a browser, so there's nothing for a bad actor to read from the network tab or exhaust.

~~Superseded plan~~: `api.congress.gov` requires a free API key passed in the URL, which can't be kept secret in a static site (anyone can read it from the network tab). It's a rate-limit meter, not a real secret, but a shared key baked into the site risks exhaustion from heavy traffic or abuse. The original plan was a hybrid model — ship a baked-in shared key so the site works instantly with zero setup, but let users optionally paste their own free key (stored in `localStorage`) to bypass the shared quota if it's ever exhausted. Abandoned in favor of the server-side mirror above, which sidesteps the problem entirely rather than mitigating it.

## Notes on the legal landscape

- **No statute found that makes this explicitly illegal.** Federal statutes, bills, and regulations are public domain (17 U.S.C. § 105 denies copyright to U.S. government works), and `congress.gov`/`govinfo.gov` explicitly publish bulk data and APIs *for* reuse.
- **[*Georgia v. Public.Resource.Org*](https://www.oyez.org/cases/2019/18-1150)** (SCOTUS, 2020, 5–4) reinforced the "government edicts doctrine" — text authored by a body with lawmaking authority can't be copyrighted, even state-level annotations a state tried to paywall. This closes off the main IP risk, including at the state level if this project ever expands there.
- **Real risk area: unauthorized practice of law (UPL).** Courts have drawn the line at *displaying* text (fine — this is what Westlaw, GovTrack, eCFR already do) vs. software making a *judgment call* on a user's behalf. Staying strictly on the "shows the diff" side of that line, with clear disclaimers, is a hard requirement, not a nice-to-have.
- **Licensing friction at the state/local layer** — some state codes and most municipal codes are gated by exclusive-ish publisher contracts. Not relevant to the federal-only MVP, but worth remembering.

## Prior art

- **[`unitedstates/uscode`](https://github.com/usgpo/uslm) / USLM** — official OLRC-produced XML schema for the U.S. Code, designed for exactly this versioning problem. Already exists; use it, don't reinvent it.
- **[`nickvido/us-code`](https://github.com/nickvido/us-code)** — U.S. Code already turned into git history, one commit per OLRC release point. NOTE: This project does not appear to be maintained, or is only updated rarely. It is not a reliable source for the latest code
- **eCFR.gov** — already does point-in-time + visual diff for regulations. The regulatory half of this problem is largely solved; the legislative half (pending bills vs. current code) is the actual gap.
- **House Comparative Print Suite** (`compare.house.gov`) — Congress already built the exact tool this project is trying to make public. It's staff-only, behind the House firewall. This project is, bluntly, trying to make that capability available to everyone.
- **Deepbills (Cato Institute)** — XML tagging of which USC sections a bill touches. Useful reference for citation-parsing patterns, doesn't resolve full diffs.
- **Akoma Ntoso** — the international standard USLM is a dialect of; worth knowing about if this ever needs to model amendment lifecycles more richly.

## Open decisions (not yet settled)

- Tech stack details beyond "vanilla HTML/JS/CSS, no framework" (build tooling, if any; testing approach).
- Exact starting point for the amendment-instruction parser, once the data-plumbing MVP is solid.

**Resolved:** sourcing U.S. Code text and pending-bill text/status without a CORS-capable API — both now solved the same way, a scheduled server-side mirror in this repo (`usc/` and `bills/` respectively) rather than a live browser call to govinfo or congress.gov.

## Note on AI use for development
Developers can use whatever tools they deem appropriate, what matters is the final quality of submitted code. However, this project does not consider AI development "first class" - and any AI specific configuration files must not be included in the project source.
