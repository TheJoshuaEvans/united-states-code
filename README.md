# united-states-code

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
- **`.json`** — a structured translation of the citation, including OLRC's editorial notes, source-credit, and cross-reference metadata (tagged separately from the operative text).
- **`.txt`** — the operative legal text only, plain and readable. No notes, no source credit, no cross-reference annotations.

All three formats represent the same underlying citation and are generated together, so they never disagree with each other.

## Directory layout

Files are organized by title and citation path, with all three formats living as neighbors:

```
usc/{title}/{section}.xml
usc/{title}/{section}.json
usc/{title}/{section}.txt
```

For example, 26 U.S.C. § 501(c)(3):

```
usc/26/501/c/3.xml
usc/26/501/c/3.json
usc/26/501/c/3.txt
```

Chunking is at the whole-section level — a citation file contains its entire section, not just the specific subsection or paragraph requested.

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
