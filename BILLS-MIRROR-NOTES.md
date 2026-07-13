# Bills mirror — design notes

Working notes from the planning conversation for **The Law**'s other prerequisite sub-project: a
server-side mirror of federal legislation from `api.congress.gov` for the current Congress,
replacing the original plan of calling that API directly from the browser with a baked-in shared
key (see `THE-LAW-README.md`'s "API Key Use" section, resolved 2026-07-12).

## Why mirror instead of calling the API directly

`api.congress.gov` has open CORS and a free API key, so a direct browser call was technically
viable — the original plan in `THE-LAW-README.md`. But the key can't actually be kept secret in a
static site (anyone can read it from the network tab), so a shared key baked into the site risks
exhaustion or abuse from someone else's traffic, degrading the tool for everyone. Mirroring bills
into this repo the same way `usc/` already mirrors the U.S. Code sidesteps the problem entirely: a
scheduled GitHub Actions job holds the real key as a secret, fetches server-side, and commits
static files the client reads from `raw.githubusercontent.com` — no key ever ships to a browser.

## What's being mirrored, and what isn't

**Confirmed live (2026-07-12) against the real API:**
- `api.congress.gov` gates *metadata* (bill status, cosponsors, committees, summaries, text-version
  links) behind the key. The actual bill text files are static files on `www.congress.gov`
  (`BILLS-{congress}{type}{number}{version}.xml`/`.htm`/`.pdf`) — no key needed for those at all.
- Bill text XML uses an older `bill.dtd` schema, not USLM. It has no relationship to `usc/`'s XML
  and isn't run through `chunk.py`/`render_json.py`/`render_txt.py` — those are USLM-specific.
- `fromDateTime`/`sort=updateDate` on `/v3/bill/{congress}` gives clean incremental sync, unlike
  USC (no such API; `uscode_mirror` does a full rebuild every run instead).
- `/v3/congress/current` gives the live current-Congress number — used to detect rollover rather
  than hardcoding a Congress number anywhere.
- A bill's own detail payload carries a `laws` field (non-empty once enacted) — the one clean,
  unambiguous "became law" signal the API exposes, and how a mirror consumer distinguishes an
  enacted bill from a still-pending one now that both are kept on disk (see below).
- Observed rate limit on this key: 20,000 requests/hour (the published docs say 5,000; the actual
  `x-ratelimit-limit` response header disagreed).

**Mirrored:** bill detail, cosponsors, committees, CRS summaries (consolidated into one `meta.json`
per bill — see "Consolidated into `meta.json`" below), and each text version's "Formatted XML" file.
Checked live: all three sub-resources are small, single-shape JSON behind the same pagination
pattern as everything else, so including them alongside status+text wasn't a meaningfully bigger
lift — no reason to leave them out of the MVP.

**Not mirrored (yet):** full action history. `latestAction` embedded in the bill detail payload is
enough for now; the full `actions` sub-resource is a longer, more volatile list that isn't needed
for the data-plumbing MVP milestone in `THE-LAW-README.md`.

## Scope: current Congress only

Bills go back to 1799 in the API (428,608 records total as of this session) — irrelevant to a tool
whose entire purpose is diffing pending legislation against current code. The mirror only ever
tracks the current Congress, auto-detected via `/v3/congress/current` so it rolls over on its own
when a new Congress starts (no code change needed, no hardcoded Congress number anywhere).

## Enacted bills are kept, not pruned (revised 2026-07-13)

**First version of this mirror deleted a bill once it became law** (`laws` field non-empty →
`shutil.rmtree` its directory, or at bootstrap, just never write it). The reasoning at the time:
an enacted bill's text is now just part of `usc/`, so keeping a separate copy felt redundant, and
there's no clean way to detect *other* terminal states (vetoed, failed a final vote, stalled in
committee forever) — so pruning only the one unambiguous case (enacted) was the whole rule.

**Revised, user decision 2026-07-13: don't prune at all.** The problem the original version
created: a bill missing from `bills/` is ambiguous. It could mean "not synced yet" (the sync
watermark hasn't reached it) or "was synced, then became law and got deleted" — and a diffing
consumer can't tell those apart without cross-checking `usc/` itself, which defeats the point of
having bill-status data in the first place. Storage cost turned out not to be the real
consideration either — an already-enacted bill's `meta.json`/`text.xml` are the same small size as
any other bill's.

So now: every bill the sync touches gets written, full stop. `meta.json`'s `status.laws` field
(non-empty once enacted, confirmed directly against a real enacted bill:
`[{"number": "119-100", "type": "Public Law"}]`) is how a consumer tells "still pending" apart from
"already became law" — both states are represented on disk, not just one of them. The archive is
now genuinely "every bill synced since the cutoff, current status included," not a narrower
"not-yet-enacted" subset. Congress rollover still wipes the whole `bills/{congress}/` tree when a
new Congress starts (a separate, unrelated mechanism — see below), so this doesn't grow unbounded.

## Bootstrap and incremental sync are the same code path

Both are just `fromDateTime`-filtered crawls of `/v3/bill/{congress}`, differing only in which
watermark they start from — so there's one sync loop, not two:

- **Steady state**: watermark = the `.last-sync` marker from the previous run.
- **Fresh mirror or Congress rollover**: watermark = `INITIAL_SYNC_CUTOFF`, a hardcoded constant
  (`2026-07-11T00:00:00Z`) rather than an unbounded crawl of the full ~17k-bill Congress. This is a
  cost-limiting shortcut, not a correctness rule — a bill last touched before the cutoff and never
  updated again is permanently invisible to the mirror, but anything still moving gets an
  `updateDate` bump and is picked up the moment it does. Confirmed live: 834 bills in the 119th
  Congress have `updateDate` since 2026-07-11, the actual first-run size (down from candidates of
  2,379 at a 07-01 cutoff and 5,194 at 06-01 — tightened twice during planning once the real counts
  were checked live).

**`updateDate` doesn't mean "something legislatively happened."** Sampled every bill that updated on
2026-07-11 alone (a Saturday, 152 of them): their `latestAction.actionDate` values were scattered
back to January 2025, not clustered on that Saturday — congress.gov's own indexing/backfill
pipeline touching old records, not Congress acting on a weekend. Incremental sync handles this fine
regardless (re-writing a bill's files on a no-op "update" is harmless and idempotent), it's just
worth knowing that "N bills updated" overstates "N bills where something actually happened."

The watermark itself is set to wall-clock time at the *start* of a run, not the newest `updateDate`
seen mid-run, so nothing that changes while the sync is running gets missed on the next pass.

## Storage layout

Sibling to `usc/`, one directory per bill, two files:

```
bills/{congress}/{type}/{number}/meta.json
bills/{congress}/{type}/{number}/text.xml
```

`type` lowercase (`hr`, `s`, `hjres`, `sjres`, `hconres`, `sconres`, `hres`, `sres`), matching both
the API's own URL casing and `usc/`'s existing lowercase-title convention.

## Consolidated into `meta.json` (revised 2026-07-13)

**First version of this mirror wrote four separate files** per bill: `status.json` (the bill detail
payload), `cosponsors.json`, `committees.json`, `summaries.json` — five files per bill counting
`text.xml`. Each was a structural mirror of its own API endpoint's response, matching `usc/`'s
"structural mirror, not a re-modeling" `.json` philosophy.

**Revised, user decision 2026-07-13: combine all four into one `meta.json`.** Purely an ergonomics
call — five small files per bill was more filesystem noise than the data justified. `meta.json` is
`{"status": <bill detail>, "cosponsors": [...], "committees": [...], "summaries": [...]}`; the three
sub-resource keys are omitted (not written as empty arrays) when that sub-resource is empty (most
bills have none) — an absent key means "none," not "not fetched," the same rule the old absent-file
convention followed. `status` itself is still the bill detail payload completely unmodified, so
nothing about the "structural mirror" property changed, just where each piece lives on disk.

`sync.py` deletes any of the four legacy filenames it finds in a bill's directory the next time that
bill is synced — the same self-healing-cleanup pattern already used for the `text.xml` "latest
version only" migration below, no separate one-off migration script needed.

## Text: latest version only, not one file per stage (decided 2026-07-13)

A bill can have several published text versions as it moves through the legislative process
(introduced, reported, engrossed, engrossed-amendment-house, ...) — congress.gov's `/text`
sub-resource returns all of them, each with its own official filename (e.g.
`BILLS-119s1383is.xml`, `BILLS-119s1383rs.xml`, `BILLS-119s1383es.xml`,
`BILLS-119s1383eah.xml` for a single bill, S.1383, that had gone through four stages). The first
version of this mirror downloaded every version it found, one file per stage.

**Revised, user decision 2026-07-13: keep only the most recent text version, always as a fixed
`text.xml`.** The diff engine this mirror exists for needs the bill's text as it stands *now* — a
pile of past stage-by-stage versions sitting in the same directory doesn't serve that and risks
being mistaken for "the" current text (which one is current isn't obvious without reading each
file's `bill-stage` attribute or cross-referencing `meta.json`). The stronger argument, once
surfaced: this is exactly the principle `usc/` already follows — that mirror doesn't keep old
release points' text as separate files either, only the current snapshot lives in the working tree,
and *how* a citation's text changed over time is recoverable from `git log`, not from coexisting
files. Applying the same rule to `bills/` is consistent, not a new pattern.

Mechanics (`text.py`): the version with the latest `date` field is selected (not assumed to be
first/last in the API's own list order), its `formats` entry of type `"Formatted XML"` is
downloaded to `text.xml`, and any other `*.xml` file already in that bill's directory is deleted
first — self-healing cleanup for bills synced under the old one-file-per-stage behavior, no separate
migration script needed. `meta.json`'s own `status.textVersions: {count, url}` field is untouched
and still accurately reports how many versions congress.gov has, for anyone who wants the full
history straight from the API.

Two marker files at `bills/` root, parallel to `usc/.release-point`:
- `.congress` — the Congress number last synced, used to detect rollover.
- `.last-sync` — the `fromDateTime` watermark for the next run.

## Latest-bills index (added 2026-07-14)

Watching bill files appear one by one during a sync run, the user pointed out there was no way to
answer "what's new" without opening every `meta.json` and comparing dates by hand — a flat mirror
with one file per bill has no query capability of its own. Fix: `bills/{congress}/index.json`, a
single file listing every mirrored bill (`type`, `number`, `title`, `latestAction`, `enacted`)
sorted most-recent-first.

**Sort key: `status.latestAction.actionDate`, explicitly not `updateDate`.** This was the user's
own call, and it's the right one — already established above ("`updateDate` doesn't mean 'something
legislatively happened'"): congress.gov's backend reprocesses old records constantly, bumping
`updateDate` for reasons that have nothing to do with actual legislative activity. `latestAction`
only changes when a real event is recorded (a referral, a vote, a markup, becoming law), so it's the
only field that actually answers "what's new" the way a human means it. Sorting by `updateDate`
would surface reindexing noise ahead of, say, a bill that just passed the House.

Mechanics (`sync.py`): `_build_index` reads every `meta.json` currently in `bills/{congress}/`
(not just bills this run touched — a full rebuild from what's on disk, same "regenerate rather than
patch" philosophy as everything else in this repo) and writes `index.json` sorted by
`(latestAction.actionDate desc, type+number asc)` — the second key only breaks ties deterministically,
it doesn't mean anything on its own. A bill with no `latestAction` at all (essentially never happens,
but not impossible) sorts to the end rather than crashing, via the same "`.get(...) or default`"
pattern the null-handling fixes elsewhere in this codebase use. Rebuilt unconditionally at the end
of every `sync()` call, including a run that touches zero bills (a real bug, caught immediately:
the very first version of `_write_index` assumed `bills/{congress}/` already existed, which it
doesn't before any bill has ever been written).

## Windowed indexes: `index-7d.json` / `index-30d.json` (added 2026-07-14)

The full index has an obvious scaling problem, spotted by the user before it became a real one: at
the current bootstrap-cutoff scale (~900 bills) it's a few hundred KB, but `index.json` only grows —
nothing is ever pruned from it (see "Enacted bills are kept, not pruned" above), so over a full
Congress's two-year life it trends toward the whole Congress's bill count. Measured directly against
246 real synced bills: ~407 bytes/entry average, projecting to **~6.6 MB at 17,000 bills** — nowhere
near GitHub's limits, but a consumer that only wants "what changed this week" shouldn't have to
download and filter multiple megabytes to get it.

**Decided: keep the full index (some consumer might genuinely want the complete set, and it's still
small enough not to be a hard problem), and add two bounded-size companions** — `index-7d.json` and
`index-30d.json`, the same per-entry shape, filtered to `latestAction.actionDate` within 7 or 30 days
of the sync run's own start time (`run_started_at`, the same instant already used for the `.last-sync`
watermark — one "now" for everything a run produces, not a fresh timestamp per artifact). Built by
filtering `_build_index`'s already-sorted output (`_filter_recent`), not a second disk read or a
second sort — filtering a sorted list preserves order for free.

Rejected alternative: capping the *full* index to N most-recent entries instead of adding separate
windowed files. Doesn't actually serve both needs — a consumer wanting the complete historical set
loses it entirely, while "recent" becomes an arbitrary count rather than a meaningful time window
("last 7 days" is a real, stable question; "most recent 250" silently means different time spans
depending on how much happened that week). Three files, each answering one clear question, beat one
file trying to answer two.

## Rate limits

834 bills × 4 calls (detail + cosponsors + committees + summaries) ≈ 3,300 requests for the first
run — comfortably inside the observed 20,000/hr ceiling, a run of a couple minutes. Ongoing daily
runs are in the same ballpark. The client still backs off gracefully on an HTTP 429 (honoring
`Retry-After` when present) rather than hard-failing, as cheap insurance for whenever the archive
grows past a Congress rollover or two and a run legitimately needs more requests.

**Also retries on transient connection failures, not just 429** (added 2026-07-13, confirmed live:
an `http.client.IncompleteRead` killed a real sync run mid-bill — the server hung up before finishing
its response, nothing to do with rate limits). `_fetch` retries `IncompleteRead`,
`RemoteDisconnected`, `ConnectionError`, `TimeoutError`, and `URLError` (connection refused, DNS
failure, etc.) after a short fixed delay, up to the same `_MAX_RETRIES` ceiling as the 429 path —
distinct handling because these are network-level failures (no response at all), not an actual
error response from the server the way a 429/404/500 is.

## Inherited principles (from the main project's README/CLAUDE.md and USC-MIRROR-NOTES.md)

- No live backend / no intermediary infrastructure at request time — the scheduled sync job is the
  one accepted exception, same as `usc/`'s.
- Every served bill should be traceable back to its actual congress.gov source.
- The real API key lives only in a GitHub Actions secret and a local, gitignored `.env` — never in
  anything a consumer of this mirror touches, and never logged (the client redacts it from any
  logged URL).
