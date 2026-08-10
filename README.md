# Frontier Dental AI Catalog Agent

An HTTP-first, agent-based proof of concept that discovers, extracts, normalizes,
validates, stores, and exports product data from Safco Dental Supply for two
assigned categories: **Sutures & Surgical Products** and **Dental Exam Gloves**.

The normal crawl path is fully deterministic. An optional LLM shadow extractor
runs alongside it on a sample and reports field-level agreement, so the AI
component produces a measurable signal instead of being an untested code path.

---

## Table of contents

- [Quick start](#quick-start)
- [Architecture overview](#architecture-overview)
- [Why this approach](#why-this-approach)
- [Agent responsibilities](#agent-responsibilities)
- [Setup and execution](#setup-and-execution)
- [Output schema](#output-schema)
- [Failure handling](#failure-handling)
- [Data quality monitoring](#data-quality-monitoring)
- [Scaling to full-site crawling](#scaling-to-full-site-crawling)
- [Limitations](#limitations)
- [Testing](#testing)
- [Engineering trade-offs](#engineering-trade-offs)

---

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
python -m pip install -r requirements.txt

python main.py --max-products 5 --no-llm
```

No credentials are required. The deterministic crawler is the whole product; the
LLM is optional and off by default when `--no-llm` is passed.

### Current sample in this repository

| Metric | Value |
|---|---:|
| Product families discovered | 154 (56 sutures/surgical + 98 gloves) |
| Product families extracted | 7 |
| Variants extracted | 22 |
| Categories covered | 2 of 2 |
| Automated tests | 50 passing |

The committed dataset is a **sample**, not a full crawl. `--max-products` is a
global demo limit, not a discovery limit: discovery consistently enumerates all
154 public families, and the remaining URLs are persisted as `pending` crawl
state. Running `python main.py` without a limit continues from that state and
extracts the rest.

---

## Architecture overview

```text
                        ┌──────────────────┐
                        │   Orchestrator   │  run control, budgets, reporting
                        └────────┬─────────┘
                                 │
                        ┌────────▼─────────┐
                        │  Robots policy   │  deny-on-unknown, checked per URL
                        └────────┬─────────┘
                                 │
                        ┌────────▼─────────┐
                        │  Cached fetcher  │  disk cache → HTTP, offline replay
                        └────────┬─────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
┌───────▼───────┐      ┌─────────▼────────┐      ┌────────▼────────┐
│   Navigator   │─────▶│  Page classifier │─────▶│    Extractor    │
│ Algolia paging│      │ category/product │      │ JSON-LD +       │
│ URL canonical │      │ /unknown         │      │ masterData      │
└───────────────┘      └──────────────────┘      └────────┬────────┘
                                                          │
                              ┌───────────────────────────┼──────────────┐
                              │                           │              │
                     ┌────────▼────────┐         ┌────────▼───────┐      │
                     │    Validator    │         │  Shadow LLM    │      │
                     │ Pydantic + dedup│         │  (sampled)     │      │
                     └────────┬────────┘         └────────┬───────┘      │
                              │                           │              │
                              │                  ┌────────▼───────┐      │
                              │                  │   Comparator   │      │
                              │                  │ field agreement│      │
                              │                  └────────┬───────┘      │
                              │                           │              │
                     ┌────────▼───────────────────────────▼──────────────▼─┐
                     │                  Recovery agent                     │
                     │  records triggers + review suggestions; never edits  │
                     │  selectors automatically                             │
                     └────────┬────────────────────────────────────────────┘
                              │
                     ┌────────▼────────┐
                     │  SQLite (4 tbl) │──▶ products.json / products.csv
                     │  idempotent     │──▶ run_report.json
                     │  upsert + state │──▶ agreement_report.json
                     └─────────────────┘
```

Only the sampled subset reaches the shadow extractor and comparator; the other
products go straight from validation to storage.

### Reconnaissance findings that shaped the design

Reconnaissance ran before any implementation decision was fixed. Full detail is
in [`notes/recon.md`](notes/recon.md); the decisions it drove:

| Finding | Consequence |
|---|---|
| Product JSON-LD and all grouped variants are present in the initial HTML | No browser automation; direct HTTP is the primary fetcher |
| Safco runs Magento/Hyva with Algolia on the client | Category paging uses the public Algolia integration, not DOM scraping |
| `robots.txt` disallows `/*?page=` and `/*?p=` | UI pagination is never requested; discovery uses the allowed path instead |
| Prices, SKUs, stock, and pack text are public and server-rendered | Price visibility resolves to `public`; no authentication attempted |
| Recommendation blocks are Alpine.js components hydrated by `recs-sdk.js` | Alternative products are not in the initial response and are emitted empty |
| No specification markup exists on the product pages | `specifications` is emitted empty with `not_available` provenance |

---

## Why this approach

**HTTP first, browser only if proven necessary.** The fetch layer sits behind a
`Fetcher` protocol so the transport is a configuration decision, not an
architectural one. Reconnaissance showed every required field is in the initial
HTML, so a browser would have added a Chromium dependency, roughly an order of
magnitude more latency per page, and a much heavier production scaling story for
no additional data. A `BrowserFetcher` can be added behind the same interface
without touching any downstream agent.

**Deterministic extraction on the critical path; the LLM beside it.** Safco's
product pages are well-templated. Sending each page to an LLM would be slower,
more expensive, non-reproducible, and harder to test, while extracting the same
values that JSON-LD already states explicitly. The LLM instead runs in shadow
mode on a sample and its output is compared field by field against the
deterministic result. That converts the AI from an untested fallback branch into
a continuous drift signal, and it is the same signal that would flag a Safco
layout change in production.

**Discovery through the public search integration.** The category UI advertises
`?page=N` navigation that `robots.txt` disallows. Rather than ignore the policy
or accept the 15 families in the category JSON-LD, the Navigator reads the
`window.algoliaConfig` that the allowed category page itself serves and queries
the same public index the site's own front end uses. This reaches all 154 public
families while staying inside the stated crawl policy. The JSON-LD list remains a
documented degraded fallback if that integration changes.

**Plain Python agents rather than an agent framework.** The assignment asks for
separated responsibilities, not a specific framework. Explicit classes keep
failure domains, state ownership, and test boundaries visible, and avoid a
dependency whose abstractions would need explaining in review.

**SQLite rather than PostgreSQL.** It gives transactions, uniqueness
constraints, indexes, JSON columns, and durable checkpoint state with zero setup
for a reviewer. A repository layer isolates the choice so a production migration
is a single implementation swap.

---

## Agent responsibilities

| Agent | Owns | Explicitly does not |
|---|---|---|
| **Orchestrator** | Run control, product budget, report assembly, exit status | Parse HTML, hold selectors, write SQL |
| **Navigator** | Category discovery, Algolia paging, URL canonicalization, rejecting robots-disallowed routes, crawl-state enqueue | Interpret product fields |
| **Page classifier** | Deterministic `category` / `product` / `unknown` decision from URL shape and structured-data markers | Extract data |
| **Extractor** | Product/Breadcrumb JSON-LD, double-decoded `window.masterData` variants, per-field provenance, extraction warnings | Decide record validity |
| **Validator** | Pydantic contract enforcement, normalization, incomplete-snapshot protection, duplicate detection | Fetch or retry |
| **Recovery** | Recording triggers (missing field, validation failure, agreement drop) and review suggestions | Modify selectors automatically |
| **Shadow LLM** | Bounded structured context, strict-schema extraction, local validation | Participate in the normal crawl path |
| **Comparator** | Field-level agreement between the two extractors, using per-type comparison rules | Claim accuracy |

The Recovery agent is deliberately advisory. It produces auditable
`RecoveryRecord` entries with `selectors_modified: False` as a type-level
guarantee; adopting a repair remains a human decision.

---

## Setup and execution

Python 3.11 or newer.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
python -m pip install -r requirements.txt
```

### Optional: enable the shadow LLM

```bash
cp .env.example .env
```

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=your-model
LLM_API_KEY=your-key
LLM_BASE_URL=https://api.openai.com/v1   # required for non-OpenAI compatible hosts
```

The adapter calls an OpenAI-compatible `/chat/completions` endpoint with strict
JSON Schema output and validates the response against the same Pydantic contract
the deterministic extractor uses. With no configuration present, shadow execution
is reported as `skipped` with the missing variable names, and the crawl completes
normally. Secrets are read only from the environment and are never written to
cache metadata or reports.

Any compatible provider works. Gemini was used for the results below, through
Google's OpenAI-compatible endpoint
(`https://generativelanguage.googleapis.com/v1beta/openai`), which accepted the
generated JSON Schema unchanged.

**Provider quota.** Free tiers are small — Gemini's allows roughly 5 requests per
minute and 20 per day per model. `config.yaml` therefore defaults
`llm.shadow_sample` to 2. The adapter paces requests to
`llm.requests_per_minute`, retries 429 with backoff honouring `Retry-After`, and
stops sampling for the rest of the run once a quota is exhausted rather than
spending attempts that cannot succeed.

### Commands

```bash
python main.py                                   # full configured crawl
python main.py --max-products 5 --no-llm         # small reviewer demo
python main.py --resume --max-products 5         # continue pending / interrupted work
python main.py --force-refresh --max-products 5  # bypass fresh cache entries
python main.py --offline --max-products 5        # cache only; makes no network requests
python main.py --max-products 5 --shadow-sample 2 # attempt LLM shadow comparison
python -m pytest -q                              # test suite
```

`--offline` and `--force-refresh` are mutually exclusive.

**Exit codes:** `0` completed, `2` partial, `1` failed. A partial run cannot be
mistaken for success by CI or a scheduler.

### Watching a run

When stderr is a terminal, a live progress view is shown: robots decision,
per-category discovery, a completion bar with ETA, and one line per product with
its variant count and whether it came from cache.

```text
  Safco Dental catalog crawl
  categories: sutures_surgical, gloves   mode: resume, llm-shadow

  robots      ALLOWED (enforced)
  discovery   sutures_surgical       56 families   algolia
  discovery   gloves                 98 families   algolia

  extracting 147 product families
  ok   http  gloves           › aurelia-vibrant-trade      4 var
  ok   cache sutures_surgical › surgifoam-reg              6 var
  ████████░░░░░░░░░░░░░░ 54/147  36%  prod 54 var 171 cache 12 err 0  01:12 eta 02:04
```

| Flag | Effect |
|---|---|
| *(default)* | Progress view on when stderr is a terminal, off when redirected |
| `--progress` | Force the view on |
| `--no-progress` | Force it off; JSON events go to stderr as before |
| `--log-path PATH` | Write JSON events to a file |

The human and machine views never share a stream. With the progress view active,
structured JSON events are written to `logs/crawler.jsonl` instead of stderr, so
piping output to a log processor still yields clean JSON lines.

### Configuration

All runtime behaviour lives in [`config.yaml`](config.yaml): rate limit,
concurrency, cache TTL, robots enforcement and unknown-policy, timeouts, retry
budget, category list, storage path, output paths, and LLM sampling. No code
change is needed to retarget or retune the crawl.

---

## Output schema

### Artifacts

| File | Contents |
|---|---|
| `data/catalog.db` | SQLite: `products`, `variants`, `crawl_state`, `crawl_errors` |
| `output/products.json` | Nested products, each with its variants |
| `output/products.csv` | Flat, one row per variant |
| `output/run_report.json` | Status, metrics, discovery, completeness, provenance, audits |
| `output/agreement_report.json` | Shadow evidence and field-level agreement, or skip reason |

### Product

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Required; blank rejected |
| `brand` | `str \| None` | Manufacturer from JSON-LD |
| `category_path` | `list[str]` | Full hierarchy, e.g. `["Dental Supplies", "Dental Exam Gloves", "Nitrile gloves"]` |
| `product_url` | `str` | Canonical family URL; the product identity key |
| `description` | `str \| None` | HTML stripped, whitespace normalized |
| `specifications` | `dict[str, str]` | Empty on Safco; see limitations |
| `image_urls` | `list[str]` | Deduplicated, absolute |
| `alternative_product_urls` | `list[str]` | Empty on Safco; see limitations |
| `price_visibility` | `PriceVisibility` | Family-level visibility state |
| `field_provenance` | `dict[str, FieldSource]` | Per-field extraction source |
| `content_hash` | `str \| None` | Change detection across runs |
| `scraped_at` | `datetime` | UTC, timezone-aware |

### Variant

| Field | Type | Notes |
|---|---|---|
| `product_url` | `str` | Parent family |
| `sku` | `str \| None` | Safco SKU, e.g. `5106359` |
| `item_number` | `str \| None` | Manufacturer part number, e.g. `01S0630` |
| `product_code` | `str \| None` | Additional identifier when present |
| `option_values` | `dict[str, str]` | Distinguishing attributes, e.g. size or pack |
| `image_urls` | `list[str]` | Variant-specific imagery |
| `price` | `Decimal \| None` | Negative values rejected |
| `currency` | `str \| None` | Three-letter ISO-style code |
| `price_visibility` | `PriceVisibility` | Distinguishes "absent" from "not public" |
| `unit_pack_size` | `str \| None` | Normalized pack text |
| `availability` | `str \| None` | Stock label, e.g. `In stock` |
| `field_provenance` | `dict[str, FieldSource]` | Per-field extraction source |

### Enumerations

```text
PriceVisibility : public | login_required | not_present | unknown
FieldSource     : api | json_ld | embedded_state | dom
                  | llm_fallback | llm_shadow | derived | not_available
CrawlStatus     : pending | in_progress | completed | failed | skipped
```

`price_visibility` exists so a null price is never ambiguous. `public` with a
value means extraction succeeded; `login_required` means extraction succeeded and
established that the price is gated; `unknown` means the crawler could not tell.
Collapsing these into a nullable number would make an extractor regression
indistinguishable from correct behaviour.

### Identity and idempotency

Products are keyed on canonical family URL. Variants prefer SKU
(`variant_key = "sku:5106359"`), falling back to a stable combination of
item/product identifiers and normalized option values. Both use upsert
semantics, so repeated runs update rather than duplicate. A complete variant
snapshot replaces stale variants; a degraded or incomplete snapshot is blocked
from overwriting a known-good record.

### Sample record

```json
{
  "name": "OraSoothe Sockit!",
  "brand": "Septodont",
  "category_path": ["Dental Supplies", "Sutures & surgical products",
                    "Surgical medicaments and packing"],
  "product_url": "https://www.safcodental.com/product/orasoothe-reg-sockit-gel",
  "price_visibility": "public",
  "field_provenance": {"name": "json_ld", "brand": "json_ld",
                       "specifications": "not_available"},
  "variants": [
    {
      "sku": "5106359",
      "item_number": "01S0630",
      "option_values": {"description": "Oral Coating Rinse - Hygiene, 3.4 oz Bottle"},
      "price": "9.99",
      "currency": "USD",
      "price_visibility": "public",
      "availability": "In stock",
      "field_provenance": {"sku": "embedded_state", "price": "embedded_state"}
    }
  ]
}
```

---

## Failure handling

**Transport.** Retries apply only to retryable conditions: timeouts, connection
resets, transient 5xx, and 429. Exponential backoff with jitter, and
`Retry-After` is honoured when present. Permanent 404s, robots denials, and
structural extraction failures are not retried, because repeating them only adds
load.

**Crawl state.** Every URL carries `pending`, `in_progress`, `completed`,
`failed`, or `skipped` state in SQLite. An interrupted run leaves `in_progress`
rows that `--resume` reclaims, so a long crawl never restarts from zero. Success
resets the per-run attempt counter; failures retain structured error context in
`crawl_errors` including fetch source, status code, and page type.

**Data integrity.** The validator rejects records that would degrade storage: a
family whose variant count drops sharply, or whose previously populated metadata
arrives empty, does not overwrite the good record. This makes a partial upstream
failure visible rather than silently destructive.

**Run status.** Zero discovery, degraded-count mismatch, validation errors, and
snapshot drift all downgrade the run to `partial` or `failed` rather than being
reported as success.

**LLM failures are never crawl failures.** Provider errors, schema violations,
and missing configuration are captured as evidence in the agreement report; the
deterministic crawl proceeds unaffected.

**Robots.** Policy is fetched at startup and enforced per URL. `unknown_policy`
defaults to `deny`, so an unreachable or unparseable policy stops the crawl
rather than assuming permission.

---

## Data quality monitoring

The run report already emits four independent signals, each catching a different
failure mode.

**1. Field completeness.** Per-field population rates for products and variants.
Catches an extractor that silently stops finding a field.

```json
"variant_completeness": {"sku": 1.0, "price": 1.0, "unit_pack_size": 0.8636}
```

**2. Provenance distribution.** Counts by `FieldSource` across the run. This is
the most sensitive drift signal: if `json_ld` share collapses while
`not_available` rises, the page contract changed even though the crawl still
"succeeded."

```json
"field_provenance_counts": {"derived": 77, "embedded_state": 132,
                            "json_ld": 42, "not_available": 14}
```

**3. Discovery count drift.** Discovered families per category, with the index's
own reported total for cross-check. A category dropping from 98 to 60 indicates a
discovery regression, not a smaller catalog.

**4. Cross-extractor agreement.** Field-level agreement between the deterministic
and LLM extractors on a sample, using per-type comparison: normalized equality
for identifiers, numeric tolerance for prices, set comparison for image and
alternative URLs, structured key comparison for specifications.

This is the signal that catches *silent* drift — the case the other three miss.
If Safco moves a value and a selector starts returning a plausible but wrong
string, completeness stays at 100% and provenance is unchanged, but agreement
against an independent reader drops. Falling below the configured threshold
triggers a Recovery record.

**What this does not claim.** Cross-extractor agreement is consistency, not
accuracy. Both extractors can agree and both be wrong. Real accuracy requires
labelled samples, manual audit, or a trusted reference feed. The report is
labelled `"terminology": "cross-extractor agreement"` so the number is not read
as correctness.

**In production** these would be emitted per run to a metrics backend with alerts
on: extraction success below 95%, per-category count change beyond ±20%,
provenance share shifting more than 10 points run over run, agreement below
threshold, and any rise in validation failure rate. The first four detect a site
change before the data reaches consumers.

---

## Scaling to full-site crawling

**Today:** one process, sequential product fetches, SQLite, local files. Bounded
by politeness rather than throughput — at one request per second, 154 families is
a few minutes and the full catalog would be hours.

**The change that matters is separating politeness from parallelism.** Scaling
does not mean hitting Safco harder; it means keeping workers busy under a fixed
origin budget.

```text
Scheduler ──▶ Durable queue (SQS / Redis)
                     │
        ┌────────────┼────────────┐
   HTTP worker  HTTP worker  HTTP worker      ← scale horizontally
        └────────────┼────────────┘
              shared token bucket             ← one origin rate, enforced globally
                     │
            Extraction + validation
                     │
                 PostgreSQL
                     │
        metrics / logs / alerting
```

Concretely:

1. **Queue.** Move `crawl_state` to a durable queue with visibility timeouts, so
   a crashed worker's in-flight URLs return to the queue automatically. The state
   machine already implemented maps onto this directly.
2. **Storage.** Swap the repository implementation for PostgreSQL. Upsert
   semantics and identity rules are already isolated behind that boundary.
3. **Shared rate limiting.** A per-domain token bucket in Redis, so N workers
   collectively respect one origin budget, with adaptive backoff that reduces
   concurrency when 429 or 5xx rates rise.
4. **Discovery.** Replace the public Algolia integration with a Safco-approved
   feed or API. The current approach is correct for a POC but is not a
   contractual completeness guarantee and could change without notice.
5. **Incremental crawls.** `content_hash` is already persisted; a scheduled run
   can skip re-extraction of unchanged pages and re-crawl fast-moving categories
   more often than static ones.
6. **Browser pool.** Only if a future section requires JavaScript — as a small
   pooled fallback behind the existing `Fetcher` protocol, not the default path.
7. **Operations.** Structured JSON logs to a central store, the four quality
   signals as metrics with alerts, secrets from a managed secret store rather
   than `.env`, and containerized workers on a scheduler.

---

## Limitations

Stated plainly rather than hidden; each notes what would resolve it.

- **The committed dataset is a 7-family sample**, not a full crawl. Discovery
  covers all 154; extraction was limited for review convenience. Removing
  `--max-products` completes it from persisted crawl state.
- **Specifications are empty.** Verified against cached product pages: Safco's
  product pages carry no specification markup at all. Emitted as `{}` with
  `not_available` provenance rather than fabricated. A specification feed or
  manufacturer data source would be required.
- **Alternative products are empty.** Recommendation blocks are Alpine.js
  components hydrated client-side by `recs-sdk.js`; the initial HTML contains
  only empty containers. Capturing them needs either browser rendering or the
  recommendation service API. Emitting same-category products instead would
  invent a relationship the site does not assert.
- **The shadow LLM path has not been exercised against a live provider.** No
  credentials were configured in this environment, so `agreement_report.json`
  currently records `"status": "skipped"` with the missing variable names. The
  adapter, schema validation, comparator, and skip/failure handling are covered
  by tests with mocked responses. Supplying credentials and rerunning populates
  the report.
- **No LLM-generated repair suggestions.** `RecoveryAgent` records and validates
  suggestions and guarantees selectors are never auto-modified, but no component
  currently generates candidates from a failing page. The trigger and audit path
  exist; the generator does not.
- **Discovery depends on Safco's current public Algolia integration**, which can
  change without notice. The JSON-LD list is the documented fallback, and the run
  report records which method was used.
- **Redirects are validated after `httpx` follows them**, not per hop.
- **A truncated but successful HTTP 200 can stay cached until TTL.**
  `--force-refresh` is the documented recovery.
- **No catalog retirement.** Products that disappear from a later run are not
  soft-deleted; `last_seen` reconciliation is deferred and must not run during a
  limited or degraded crawl.
- **Product pages are fetched sequentially.** The transport has a semaphore and
  is concurrency-ready, but the runner is sequential to keep the POC simple.
- **Validated against two categories only.** Other Safco sections may use
  different templates.

---

## Testing

50 tests, no network access required:

```bash
python -m pytest -q
```

| Area | Covers |
|---|---|
| Normalization / identity | Price parsing, whitespace, pack size, stable variant keys |
| Validator | Missing name, malformed URL, duplicate images, invalid price |
| Extraction fixtures | Glove page, multi-variant suture page, JSON-LD-only fallback |
| Fetch / cache | Cache hit, offline mode, force refresh, retry, `Retry-After` |
| Robots | Allowed, disallowed, unknown policy |
| Navigator | Algolia paging, canonicalization, degraded discovery |
| Repository | Insert, upsert, second-run idempotency, snapshot protection, export |
| Comparator | Exact, numeric, set, and structured comparison; missing-field disagreement |
| Shadow LLM | Strict schema handling, skip behaviour, provider failure as evidence |
| Orchestrator | Completed, partial, zero-discovery, and redirect paths |

Fixtures make extraction reproducible without hitting the live site, which also
means a future selector change can be tested before deployment.

---

## Engineering trade-offs

| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| Transport | Direct HTTP | Playwright default | Recon proved all required fields are server-rendered; browser adds latency and a Chromium dependency for no data |
| AI placement | Shadow + recovery | LLM on every page | Deterministic parsing of stable templates is cheaper, testable, reproducible; shadow mode still yields a live drift signal |
| Discovery | Public Algolia index | `?page=N` crawling | `robots.txt` disallows the query pagination; Algolia reaches all 154 families within policy |
| Storage | SQLite | PostgreSQL | Zero-setup transactions and durable state for a reviewer; repository boundary keeps migration cheap |
| Structure | Plain Python agents | Agent framework | Explicit boundaries and failure domains; no framework abstractions to explain |
| Repair | Advisory only | Auto-applied selectors | An LLM suggestion is a hypothesis; adopting it unreviewed risks silently corrupting the catalog |
