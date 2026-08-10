# Safco reconnaissance

Reconnaissance date: 2026-08-10 (America/Toronto)

## URL verification

Both assigned category URLs were requested directly before any crawl logic was
written, to confirm they resolve and to capture the canonical form used by the
site's own breadcrumb data.

| Assigned category URL | HTTP result | Decision |
|---|---:|---|
| `https://www.safcodental.com/catalog/sutures-surgical-products` | 200 | Verified canonical Sutures & Surgical Products category. |
| `https://www.safcodental.com/catalog/gloves` | 200 | Verified canonical Dental Exam Gloves category. |

Both match the values Safco uses in its breadcrumb structured data, so no
redirect or canonicalization adjustment is required. These are the URLs
configured in `config.yaml`.

## Robots policy

`https://www.safcodental.com/robots.txt` returned HTTP 200. The two canonical
category URLs and clean `/product/...` URLs are allowed for the configured user
agent. Query pagination patterns such as `/*?page=` and `/*?p=` are disallowed.
The implementation therefore does not crawl disallowed query-page URLs.

## Fetch and data findings

| Finding | Sutures & surgical | Exam gloves |
|---|---|---|
| Initial category HTML | HTTP 200, about 1.02 MB | HTTP 200, about 1.01 MB |
| Server-rendered product data | Yes | Yes |
| Category JSON-LD | `ItemList` plus breadcrumbs/subcategories | `ItemList` plus breadcrumbs/subcategories |
| Initial product families | 15 | 15 |
| Product page JSON-LD | Product + BreadcrumbList | Product + BreadcrumbList |
| Embedded variants | `window.masterData`, 48 variants on inspected Perma Sharp page | `window.masterData`, 4 variants on inspected Silkcare page |
| Public prices | Yes, USD | Yes, USD |
| Browser required | No | No |
| Current public Algolia families (`visibility_catalog=1`, `distinct=true`) | 56 | 98 |

Safco uses Magento/Hyva and Algolia on the client, but JavaScript execution is
not required. The category HTML exposes a runtime `window.algoliaConfig`; its
public, expiring search credential and index name can be read dynamically and
used against Algolia's JSON endpoint. Variant SKU, manufacturer part number,
description, stock, image, and price are embedded in product HTML. Direct HTTP
is therefore the selected primary fetch strategy.

## Pagination and coverage decision

The category UI is Algolia-backed and advertises `?page=N` navigation, which
Safco robots rules disallow. The implementation does not request those URLs.
Instead it reads the same public Algolia configuration delivered by the root
page and POSTs category-facet queries to Algolia. It uses `distinct=true` and
`visibility_catalog=1`, yielding 56 current sutures/surgical families and 98
glove families during recon. `family_url` is preferred because some raw hit URLs
use a Safco route that robots disallows. The 15 JSON-LD families remain a safe
fallback and cross-check if the public search integration changes.

This improves current coverage but is not a contractual completeness guarantee.
A production crawler should use a Safco-approved feed/API and monitor counts.

## Variants and prices

Product family pages are grouped products. The parent JSON-LD provides family
metadata and a public starting price. `window.masterData` provides child items,
including SKU, manufacturer part number, description, pack text, stock label,
image, and public price. Price visibility is therefore `public` when these
values parse successfully; absent values retain an explicit non-public status.

## Alternatives/recommendations

Recommendation widgets are loaded by client-side Adobe/Magento services and are
not part of the deterministic initial response inspected here. The POC emits an
empty alternatives list with provenance/limitations documented rather than
launching a browser solely for non-core recommendations.
