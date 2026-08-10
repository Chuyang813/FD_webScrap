# Project completion report

Checked against the assignment scope on 2026-08-10.
Items are marked complete only when backed by automated or live-run evidence.

## Verified reconnaissance

- [x] Both assigned category URLs requested and verified to return HTTP 200.
- [x] Canonical category URLs confirmed against Safco breadcrumb data and configured.
- [x] Robots policy and query pagination rules inspected and enforced.
- [x] Category JSON-LD, public Algolia discovery, PDP JSON-LD, grouped variants,
  current PDP price/stock, and public visibility inspected.
- [x] HTTP selected; browser execution is unnecessary for required scoped data.
- [x] Findings and coverage decisions recorded in `notes/recon.md`.

## Implementation

- [x] Config-driven CLI and environment-only optional LLM secrets.
- [x] Fetcher contract, HTTP transport, disk cache, offline/force modes, conservative
  rate limit, bounded transport concurrency, retries/backoff, and `Retry-After`.
- [x] Robots startup policy and URL enforcement.
- [x] Navigator, canonical URL filtering, Algolia paging, degraded JSON-LD fallback,
  discovery metadata, and page classifier.
- [x] Deterministic Product/Breadcrumb JSON-LD and double-decoded `masterData`
  extraction.
- [x] Product/variant Pydantic models, normalization, price visibility, stable
  identity, content hashes, and field provenance.
- [x] Validator, incomplete-snapshot protection, variant/metadata drift gates, and
  recovery records that never auto-modify selectors.
- [x] Four-table SQLite schema, transactions, idempotent product/variant upsert,
  crawl states, crawl errors, interrupted resume, and attempt reset.
- [x] Nested JSON, row-per-variant CSV, run report, and agreement report.
- [x] Optional strict-schema OpenAI-compatible shadow adapter, bounded structured
  prompt context, local validation, comparator, and explicit skipped/failed evidence.
- [x] Structured JSON logs and completed/partial/failed exit behavior.

## Verification evidence

- [x] 50 automated tests pass (`python -m pytest -q`).
- [x] Normalization/identity and validator tests.
- [x] Fetch/cache/offline/retry and robots tests.
- [x] Navigator and degraded-discovery tests.
- [x] Fixture extraction for glove, multi-variant suture, and JSON-LD fallback.
- [x] Repository, snapshot preservation, idempotency, and export tests.
- [x] Shadow schema/skip behavior and field-level comparator tests.
- [x] Orchestrator success/partial/zero-discovery/redirect tests.
- [x] Full live crawl discovered 154 families and extracted all 154 with 760
  variants and zero failures.
- [x] Live shadow LLM comparison against Gemini produced 0.997 core-field
  agreement over 12 sampled products.
- [x] Second run kept product and variant counts stable without duplicate rows.
- [x] Offline replay completed with 0 HTTP fetches and 19 cache hits.
- [x] Resume processed the next two products and preserved completed records.
- [x] Generated SQLite, JSON, CSV, run report, and agreement report inspected with
  matching database/export counts and no duplicate identities.

## Not completed or intentionally deferred

- **Query-page crawling:** intentionally prohibited because Safco robots disallows
  `?page=`/`?p=`. Public Algolia discovery is used; production needs an approved
  feed/API for contractual completeness.
- **Specifications and recommendations:** structured PDP specifications and
  client-rendered recommendations are not extracted reliably. Empty values carry
  `not_available` provenance and appear in completeness metrics.
- **Shadow sample size:** the committed comparison covers 12 products, bounded by
  free-tier provider quota rather than by design, and samples the head of the
  queue rather than randomly. Production would sample randomly with paid quota.
- **LLM-generated repair suggestions:** the recovery trigger, suggestion schema,
  and audit record exist, but nothing generates candidate selectors from a
  failing page. This is the largest remaining design-to-implementation gap.
- **Advisory-tier agreement:** low scores correctly flag representation
  differences but cannot separate "the LLM reshaped it" from "the deterministic
  reader broke". Canonicalizing both sides would be required to make them
  actionable.
- **Browser fallback:** omitted because it adds no value for the currently required
  initial-HTML fields.
- **Automatic selector mutation:** intentionally prohibited; recovery suggestions
  require fixture validation and human review.
- **Strict per-redirect-hop robots checks:** final destinations are checked after
  following redirects, not before each hop.
- **Catalog retirement:** there is no `last_seen` reconciliation/soft deletion for
  products absent from a future full run.
- **Cache eviction on semantic failure:** a bad 200 response can persist until TTL;
  `--force-refresh` is the documented recovery.
- **Parallel product workers:** the fetcher has a semaphore, but the current runner
  processes product pages sequentially to keep the local POC simple and polite.
- **Distributed operation:** PostgreSQL, durable queues, centralized monitoring,
  browser pools, Docker, and deployment automation remain production work.

These deferred items do not block the assigned two-category deterministic POC; they
are stated explicitly so the output is not presented as more complete than it is.
