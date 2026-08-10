# Frontier Dental AI Catalog Agent

An HTTP-first Python proof of concept that discovers, extracts, validates, stores,
and exports Safco Dental product families and variants for the assigned Sutures &
Surgical Products and Dental Exam Gloves categories. The normal crawl is fully
deterministic; an optional LLM shadow path provides measurable comparison evidence
without becoming a crawl dependency.

## Status

Implemented and verified on 2026-08-10:

- 50 automated tests pass.
- A limited live crawl discovered 154 public product families: 56
  sutures/surgical and 98 gloves.
- Live extraction, cache-only offline replay, resume, idempotent upsert, JSON/CSV
  export, and run reporting were exercised successfully.
- The current checked output database contains 7 sampled product families and 22
  variants. `--max-products` is a global demo limit, not a discovery limit.

The URL supplied for the first category is currently a Safco 404:

```text
https://www.safcodental.com/catalog/sutures-surgicalproducts
```

The crawler uses the verified working URL, which has a hyphen between `surgical`
and `products`:

```text
https://www.safcodental.com/catalog/sutures-surgical-products
```

The gloves URL is unchanged:

```text
https://www.safcodental.com/catalog/gloves
```

Detailed evidence is in [`notes/recon.md`](notes/recon.md). Completion and deferred
items are in [`PROJECT_STATUS.md`](PROJECT_STATUS.md).

## Architecture and responsibilities

```text
Orchestrator
  -> robots policy
  -> cached HTTP fetcher
  -> Navigator -> Page Classifier
  -> deterministic Extractor -> Validator
  -> SQLite Repository -> JSON / CSV / run report
                            \
                             -> optional shadow LLM -> comparator / agreement report
  -> Recovery Agent (records evidence and review suggestions only)
```

- **Navigator:** reads category state, queries the public category search index,
  canonicalizes URLs, and rejects unsafe or robots-disallowed product routes.
- **Classifier:** identifies category, product, and unknown responses from
  deterministic signals.
- **Extractor:** reads Product/Breadcrumb JSON-LD and Safco's embedded
  `window.masterData` variant map. Current PDP prices and stock come from the PDP,
  not the discovery index.
- **Validator:** normalizes the shared Pydantic contract and rejects bad or
  duplicate records.
- **Repository:** performs transactional, idempotent upserts and persists crawl
  state/errors in SQLite.
- **Recovery:** records validation/drift evidence and possible human actions. It
  never silently edits selectors.
- **Shadow LLM:** optionally extracts a bounded structured sample and compares it
  field by field with deterministic output. This metric is agreement, not accuracy.

The components are plain Python rather than an agent framework so their boundaries,
failure behavior, and tests remain easy to inspect.

## Fetch and crawl policy

Recon showed that a browser is unnecessary: product JSON-LD and all grouped
variants are in the initial HTML. Safco's robots policy disallows UI query
pagination such as `?page=` and `?p=`, so the crawler never requests those URLs.

Instead, each allowed category root exposes a runtime `window.algoliaConfig`. The
Navigator parses its current public search credentials and pages the category facet
through Algolia with `visibility_catalog=1` and `distinct=true`. Credentials are not
hard-coded or written to cache metadata. The category JSON-LD list (15 families) is
an explicit degraded fallback, and the run report records discovery method, counts,
reported hits, and degraded reason.

Clean same-origin `/product/...` URLs are accepted; disallowed
`/catalog/product/view/id/...` routes are rejected. The HTTP layer uses a configurable
one-request-per-second default, retryable transport/status filtering, exponential
backoff, `Retry-After`, a disk cache, and a two-request semaphore. The current
orchestrator processes product pages sequentially, so the configured concurrency is
transport-ready rather than parallel product execution.

## Setup

Python 3.11 or newer is recommended.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The deterministic crawler needs no credentials. For optional shadow extraction,
copy `.env.example` to `.env` and set:

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=your-model
LLM_API_KEY=your-key
# Optional for provider=openai; required for other compatible providers.
LLM_BASE_URL=https://api.openai.com/v1
```

The adapter calls an OpenAI-compatible `/chat/completions` endpoint with strict JSON
Schema output and validates the response locally. If configuration is absent, shadow
execution is explicitly reported as `skipped`; the crawl still completes.

## Running

```bash
# Full configured crawl
python main.py

# Small reviewer demo across both categories
python main.py --max-products 5 --no-llm

# Continue pending, failed, or interrupted work
python main.py --resume --max-products 5 --no-llm

# Ignore fresh cache entries and fetch remotely
python main.py --force-refresh --max-products 5 --no-llm

# Make no network requests; every request must exist in cache
python main.py --offline --max-products 5 --no-llm

# Attempt an LLM shadow comparison on two extracted products
python main.py --max-products 5 --shadow-sample 2

# Test suite
python -m pytest -q
```

`--force-refresh` and `--offline` are mutually exclusive. Exit code `0` means
completed, `2` means partial, and `1` means failed. A partial run therefore cannot be
mistaken for success in CI or a scheduler.

## Data model and output

Product identity is the canonical family URL. Variant identity prefers SKU, then a
stable combination of item/product identifiers and normalized options. Products and
variants are stored separately; complete variant snapshots remove stale variants,
while degraded/incomplete snapshots are prevented from overwriting a known-good
record. Large variant-count drops and nonempty-to-empty product metadata changes are
also guarded.

Price always carries a visibility state: `public`, `login_required`, `not_present`,
or `unknown`. Field provenance identifies JSON-LD, embedded state, derived values,
LLM shadow output, or unavailable fields.

Generated artifacts:

- `data/catalog.db` — four SQLite tables: `products`, `variants`, `crawl_state`,
  and `crawl_errors`.
- `output/products.json` — nested products with their variants.
- `output/products.csv` — one row per variant.
- `output/run_report.json` — status, fetch/cache/retry/failure metrics, discovery
  completeness, field completeness, provenance, extraction audits, and recovery.
- `output/agreement_report.json` — shadow execution evidence and field-level
  cross-extractor agreement, or an explicit skip/failure reason.

All JSON is UTF-8. On older Windows PowerShell, use `Get-Content -Encoding UTF8`
when inspecting it so symbols such as `™` and `®` render correctly.

## Reliability, resume, and data quality

- Crawl states are `pending`, `in_progress`, `completed`, or `failed`; interrupted
  `in_progress` URLs are eligible for resume.
- Success resets per-run attempts. Failures retain structured error context and
  respect the configured extra retry count.
- Cache keys include method, URL, body, and a one-way hash of representation-changing
  headers; API keys/cookies are never stored in plaintext metadata.
- Content hashes and uniqueness constraints make repeated runs idempotent.
- Run status is `completed`, `partial`, or `failed`; zero discovery, degraded count
  mismatches, validation errors, and snapshot drift are visible rather than silently
  treated as complete.
- Completeness and provenance are reported for both product and variant fields.

## Tests and verified runs

The 50-test suite covers normalization, identity, validation, deterministic
extraction fixtures, JSON-LD fallback completeness, cache/offline behavior, retry,
robots matching, navigation, repository replacement rules, exports, comparator,
shadow LLM schema handling, and orchestrator completed/partial/failed behavior.

Verified integration behavior includes:

- live discovery of `56 + 98 = 154` families;
- live extraction of both category types and multi-variant PDPs;
- an identical second run without duplicate product/variant rows;
- cache-only replay with zero HTTP requests;
- resume after completed work, adding only the next pending products;
- explicit skipped shadow evidence when credentials are absent.

Live site counts, prices, and availability can change after these observations.

## Known limitations and deferred work

- This is a two-category POC using Safco's current public Algolia integration, not a
  contractual completeness guarantee. Production should use an owner-approved feed
  or API and monitor count/provenance drift.
- PDP `specifications` and client-rendered recommendation/alternative products are
  not reliably available in the initial structured payload. They are emitted empty
  with `not_available` provenance and 0% completeness rather than fabricated.
- No real LLM provider call was made in this workspace because no credentials were
  supplied. The strict adapter, skip/failure evidence, validation, and comparison
  path are covered with deterministic mocked tests.
- Browser extraction is not implemented because recon proved it unnecessary for the
  required fields. It would be a separate fallback worker if the site later required
  JavaScript execution.
- Redirect destinations are validated after `httpx` follows them. Strict per-hop
  robots authorization before each redirect request is not implemented.
- A bad/truncated HTTP 200 response may remain cached until the 24-hour TTL; rerun
  with `--force-refresh` after a suspected cached-page failure.
- The database does not track `last_seen` or retire products that disappear from a
  later complete catalog run. Full-run reconciliation/soft deletion is deferred; it
  must not run during limited or degraded discovery.
- Recovery produces reviewable suggestions only; automatic selector deployment is
  intentionally excluded.
- SQLite and sequential orchestration suit a local POC, not distributed workers. A
  production path would use a durable queue, PostgreSQL, centralized metrics, and
  domain-wide rate limiting, while retaining HTTP workers and a small browser
  fallback pool.

## Engineering trade-offs

- **HTTP over browser:** lighter, reproducible, cacheable, and sufficient for the
  inspected site state.
- **Deterministic over LLM critical path:** cheaper and testable; the LLM is useful as
  a shadow drift signal and recovery assistant.
- **SQLite over PostgreSQL:** self-contained transactions and resume for reviewer
  use, with a repository boundary for later migration.
- **Simple orchestrator over an agent framework:** explicit ownership and fewer
  operational dependencies for this POC.
