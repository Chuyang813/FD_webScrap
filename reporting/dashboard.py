"""Render a self-contained HTML data-quality dashboard.

The page embeds its own data and styling, so it opens from the filesystem with no
server, no network access, and no third-party libraries. Every chart ships a table
view beneath it, which is both the accessibility fallback and the reason a low
contrast fill is acceptable at these sizes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Iterable

from .collect import DashboardData, collect_dashboard_data


__all__ = [
    "DashboardData",
    "collect_dashboard_data",
    "render_dashboard",
    "write_dashboard",
]


_STYLE = """
.viz-root {
  color-scheme: light;
  --surface-1: #fcfcfb;
  --plane: #f9f9f7;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --text-muted: #898781;
  --grid: #e1e0d9;
  --baseline: #c3c2b7;
  --border: rgba(11,11,11,0.10);
  --series-1: #2a78d6;
  --series-2: #eb6834;
  --series-3: #1baf7a;
  --neutral-fill: #c3c2b7;
  --track: #eceae4;
  --good: #0ca30c;
  --warning: #fab219;
  --critical: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz-root {
    color-scheme: dark;
    --surface-1: #1a1a19;
    --plane: #0d0d0d;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted: #898781;
    --grid: #2c2c2a;
    --baseline: #383835;
    --border: rgba(255,255,255,0.10);
    --series-1: #3987e5;
    --series-2: #d95926;
    --series-3: #199e70;
    --neutral-fill: #56554f;
    --track: #262624;
  }
}
:root[data-theme="dark"] .viz-root {
  color-scheme: dark;
  --surface-1: #1a1a19;
  --plane: #0d0d0d;
  --text-primary: #ffffff;
  --text-secondary: #c3c2b7;
  --text-muted: #898781;
  --grid: #2c2c2a;
  --baseline: #383835;
  --border: rgba(255,255,255,0.10);
  --series-1: #3987e5;
  --series-2: #d95926;
  --series-3: #199e70;
  --neutral-fill: #56554f;
  --track: #262624;
}

* { box-sizing: border-box; }
body { margin: 0; background: var(--plane); }
.viz-root {
  background: var(--plane);
  color: var(--text-primary);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
  padding: 32px 20px 64px;
}
.wrap { max-width: 1080px; margin: 0 auto; }

header.page { margin-bottom: 28px; }
h1 { font-size: 24px; font-weight: 600; margin: 0 0 6px; letter-spacing: -0.01em; }
.sub { color: var(--text-secondary); font-size: 14px; margin: 0; }
.meta { color: var(--text-muted); font-size: 13px; margin: 10px 0 0; }

.badge {
  display: inline-flex; align-items: center; gap: 7px;
  border: 1px solid var(--border); border-radius: 999px;
  padding: 4px 12px 4px 10px; font-size: 13px; font-weight: 500;
  background: var(--surface-1); color: var(--text-primary);
}
.dot { width: 9px; height: 9px; border-radius: 50%; flex: none; }

.kpi { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; margin-bottom: 30px; }
.tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
.tile .label { color: var(--text-secondary); font-size: 13px; margin-bottom: 6px; }
.tile .value { font-size: 27px; font-weight: 600; letter-spacing: -0.02em; }
.tile .note { color: var(--text-muted); font-size: 12.5px; margin-top: 4px; }
.tile.hero .value { font-size: 50px; line-height: 1.05; font-weight: 600; }

section.card {
  background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 10px; padding: 20px 22px; margin-bottom: 18px;
}
h2 { font-size: 16px; font-weight: 600; margin: 0 0 4px; }
.desc { color: var(--text-secondary); font-size: 13.5px; margin: 0 0 18px; max-width: 78ch; }
.group-label {
  font-size: 12px; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase;
  color: var(--text-muted); margin: 20px 0 10px;
}
.group-label:first-child { margin-top: 0; }

.rows { display: flex; flex-direction: column; gap: 9px; }
.row { display: grid; grid-template-columns: minmax(96px, 172px) 1fr auto; align-items: center; gap: 14px; }
.row .name { color: var(--text-secondary); font-size: 13.5px; overflow-wrap: anywhere; }
.track {
  position: relative; height: 12px; background: var(--track);
  border-radius: 2px; overflow: visible;
}
.fill { height: 100%; border-radius: 2px 4px 4px 2px; min-width: 2px; }
.row .val {
  font-size: 13px; color: var(--text-primary); font-variant-numeric: tabular-nums;
  min-width: 64px; text-align: right;
}
.threshold {
  position: absolute; top: -5px; bottom: -5px; width: 1px;
  background: var(--baseline);
}
.threshold::after {
  content: attr(data-label); position: absolute; top: -17px; left: 50%;
  transform: translateX(-50%); white-space: nowrap;
  font-size: 11px; color: var(--text-muted);
}

.stack { display: flex; gap: 2px; height: 22px; border-radius: 2px; overflow: hidden; }
.seg { position: relative; min-width: 3px; }
.seg:first-child { border-radius: 2px 0 0 2px; }
.seg:last-child { border-radius: 0 2px 2px 0; }
.legend { display: flex; flex-wrap: wrap; gap: 8px 20px; margin-top: 14px; }
.key { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-secondary); }
.key .swatch { width: 11px; height: 11px; border-radius: 2px; flex: none; }
.key b { color: var(--text-primary); font-weight: 600; font-variant-numeric: tabular-nums; }

[data-tip] { cursor: default; }
[data-tip]:hover::after, [data-tip]:focus-visible::after {
  content: attr(data-tip); position: absolute; left: 50%; bottom: calc(100% + 8px);
  transform: translateX(-50%); z-index: 20;
  background: var(--text-primary); color: var(--surface-1);
  font-size: 12.5px; line-height: 1.4; white-space: nowrap;
  padding: 6px 9px; border-radius: 6px; pointer-events: none;
}
.hit { position: absolute; inset: -7px 0; }

details { margin-top: 18px; border-top: 1px solid var(--grid); padding-top: 12px; }
summary { cursor: pointer; font-size: 13px; color: var(--text-secondary); }
summary:hover { color: var(--text-primary); }
table { border-collapse: collapse; width: 100%; margin-top: 12px; font-size: 13px; }
th, td { text-align: left; padding: 6px 12px 6px 0; border-bottom: 1px solid var(--grid); }
th { color: var(--text-muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.scroll { overflow-x: auto; }

.two { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 26px; }
.note-box {
  border-left: 2px solid var(--baseline); padding: 2px 0 2px 14px;
  color: var(--text-secondary); font-size: 13.5px; margin-top: 18px; max-width: 78ch;
}
footer.page { color: var(--text-muted); font-size: 12.5px; margin-top: 28px; text-align: center; }
"""


def _esc(value: object) -> str:
    return escape(str(value), quote=True)


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%".replace(".0%", "%")


def _label(name: str) -> str:
    return name.replace("_", " ")


def _tile(label: str, value: str, note: str = "", hero: bool = False) -> str:
    classes = "tile hero" if hero else "tile"
    note_html = f'<div class="note">{_esc(note)}</div>' if note else ""
    return (
        f'<div class="{classes}"><div class="label">{_esc(label)}</div>'
        f'<div class="value">{_esc(value)}</div>{note_html}</div>'
    )


def _bar_rows(
    items: Iterable[tuple[str, float, str, str]],
    *,
    colour: str = "var(--series-1)",
    threshold: float | None = None,
) -> str:
    """Rows of (name, fraction 0-1, printed value, tooltip)."""

    def marker(first: bool) -> str:
        """Repeat the rule on every row so it reads as one line across the group."""

        if threshold is None:
            return ""
        label = f' data-label="{_pct(threshold)}"' if first else ""
        return f'<div class="threshold" style="left:{threshold * 100:.4f}%"{label}></div>'

    out = ['<div class="rows">']
    for index, (name, fraction, value, tip) in enumerate(items):
        width = max(0.0, min(1.0, fraction)) * 100
        out.append(
            f'<div class="row"><div class="name">{_esc(_label(name))}</div>'
            f'<div class="track" data-tip="{_esc(tip)}" tabindex="0">'
            f'<div class="hit"></div>'
            f'<div class="fill" style="width:{width:.4f}%;background:{colour}"></div>'
            f"{marker(index == 0)}</div>"
            f'<div class="val">{_esc(value)}</div></div>'
        )
    out.append("</div>")
    return "".join(out)


def _table(headers: list[str], rows: list[list[str]], numeric_from: int = 1) -> str:
    head = "".join(
        f'<th class="num">{_esc(h)}</th>' if i >= numeric_from else f"<th>{_esc(h)}</th>"
        for i, h in enumerate(headers)
    )
    body = "".join(
        "<tr>"
        + "".join(
            f'<td class="num">{_esc(c)}</td>' if i >= numeric_from else f"<td>{_esc(c)}</td>"
            for i, c in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return (
        f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _table_view(headers: list[str], rows: list[list[str]], numeric_from: int = 1) -> str:
    return (
        "<details><summary>Table view</summary>"
        + _table(headers, rows, numeric_from)
        + "</details>"
    )


def _status_colour(status: str | None) -> str:
    return {
        "completed": "var(--good)",
        "partial": "var(--warning)",
        "preserved": "var(--warning)",
    }.get(status or "", "var(--critical)")


def _coverage_section(data: DashboardData) -> str:
    if not data.discovery:
        return ""
    counts = {
        name: int(item.get("count") or 0) for name, item in data.discovery.items()
    }
    # Bar length must carry the magnitude; scaling every bar to full width would
    # make 56 and 98 look identical.
    largest = max(counts.values(), default=0)
    rows = []
    table_rows = []
    for name, item in data.discovery.items():
        found = counts[name]
        method = str(item.get("method") or "unknown")
        status = str(item.get("status") or "unknown")
        rows.append(
            (
                name,
                found / largest if largest else 0.0,
                f"{found:,}",
                f"{name}: {found} families discovered via {method} ({status})",
            )
        )
        table_rows.append([name, f"{found:,}", method, status])
    return f"""
<section class="card">
  <h2>Discovery coverage</h2>
  <p class="desc">Product families found per assigned category. Discovery uses the
  public search index the site's own front end calls, because
  <code>robots.txt</code> disallows the query pagination the UI advertises.</p>
  {_bar_rows(rows)}
  {_table_view(["Category", "Families", "Method", "Status"], table_rows, numeric_from=1)}
</section>"""


def _completeness_section(data: DashboardData) -> str:
    def block(title: str, items: list[tuple[str, float, int]], total: int) -> str:
        rows = [
            (name, fraction, _pct(fraction), f"{name}: {count:,} of {total:,} populated")
            for name, fraction, count in items
        ]
        return f'<div class="group-label">{_esc(title)}</div>{_bar_rows(rows)}'

    table_rows = [
        ["product", name, _pct(fraction), f"{count:,}"]
        for name, fraction, count in data.product_completeness
    ] + [
        ["variant", name, _pct(fraction), f"{count:,}"]
        for name, fraction, count in data.variant_completeness
    ]
    return f"""
<section class="card">
  <h2>Field completeness</h2>
  <p class="desc">Share of records carrying a real value. Two fields sit at zero by
  verified absence rather than extractor failure: Safco's product pages contain no
  specification markup, and recommendations are hydrated client-side, so both are
  emitted empty with <code>not_available</code> provenance instead of being
  invented.</p>
  <div class="two">
    <div>{block(f"Product · {data.products:,} rows", data.product_completeness, data.products)}</div>
    <div>{block(f"Variant · {data.variants:,} rows", data.variant_completeness, data.variants)}</div>
  </div>
  {_table_view(["Level", "Field", "Complete", "Rows"], table_rows, numeric_from=2)}
</section>"""


def _provenance_section(data: DashboardData) -> str:
    if not data.provenance:
        return ""
    palette = {
        "json_ld": "var(--series-1)",
        "embedded_state": "var(--series-2)",
        "derived": "var(--series-3)",
    }
    total = sum(count for _, count in data.provenance) or 1
    segments = []
    keys = []
    for name, count in data.provenance:
        # Absence is a gap, not a category, so it takes the neutral fill and never
        # spends a categorical slot.
        colour = palette.get(name, "var(--neutral-fill)")
        share = count / total
        segments.append(
            f'<div class="seg" style="flex:{share:.6f} 1 0;background:{colour}"'
            f' data-tip="{_esc(f"{_label(name)}: {count:,} fields ({_pct(share)})")}"'
            f' tabindex="0"></div>'
        )
        keys.append(
            f'<span class="key"><span class="swatch" style="background:{colour}"></span>'
            f"{_esc(_label(name))} <b>{_pct(share)}</b></span>"
        )
    table_rows = [
        [_label(name), f"{count:,}", _pct(count / total)] for name, count in data.provenance
    ]
    return f"""
<section class="card">
  <h2>Field provenance</h2>
  <p class="desc">Which source produced each stored value, across every product and
  variant field. This is the most sensitive drift signal available: if the
  <em>json_ld</em> share collapses while <em>not_available</em> rises, the page
  contract changed even though the crawl still reported success.</p>
  <div class="stack">{"".join(segments)}</div>
  <div class="legend">{"".join(keys)}</div>
  {_table_view(["Source", "Fields", "Share"], table_rows)}
</section>"""


def _agreement_section(data: DashboardData) -> str:
    if not data.agreement_core_fields and not data.agreement_advisory_fields:
        reason = data.agreement_status or "not run"
        return f"""
<section class="card">
  <h2>Cross-extractor agreement</h2>
  <p class="desc">No shadow comparison is available in this report
  (<code>{_esc(reason)}</code>). Configure a provider in <code>.env</code> and run
  with <code>--shadow-sample N</code> to populate it.</p>
</section>"""

    core_rows = [
        (name, score, f"{score:.3f}", f"{name}: {score:.3f} agreement")
        for name, score in data.agreement_core_fields
    ]
    advisory_rows = [
        (name, score, f"{score:.3f}", f"{name}: {score:.3f} agreement")
        for name, score in data.agreement_advisory_fields
    ]
    table_rows = [["core", name, f"{score:.3f}"] for name, score in data.agreement_core_fields]
    table_rows += [
        ["advisory", name, f"{score:.3f}"] for name, score in data.agreement_advisory_fields
    ]
    core_value = f"{data.agreement_core:.3f}" if data.agreement_core is not None else "n/a"
    adv_value = f"{data.agreement_advisory:.3f}" if data.agreement_advisory is not None else "n/a"
    return f"""
<section class="card">
  <h2>Cross-extractor agreement</h2>
  <p class="desc">The deterministic extractor and an LLM reading the same
  {data.agreement_sample} pages independently, compared field by field. This catches
  <em>silent</em> drift, the case completeness and provenance both miss: if a
  selector starts returning a plausible but wrong value, those two stay flat while
  agreement against an independent reader falls.</p>

  <div class="group-label">Core · {_esc(core_value)} · sets the drift threshold</div>
  {_bar_rows(core_rows, threshold=data.agreement_threshold)}

  <div class="group-label">Advisory · {_esc(adv_value)} · informational only</div>
  {_bar_rows(advisory_rows, colour="var(--neutral-fill)")}

  <div class="legend">
    <span class="key"><span class="swatch" style="background:var(--series-1)"></span>
      Core — both readers use the same explicit source, so disagreement is real drift</span>
    <span class="key"><span class="swatch" style="background:var(--neutral-fill)"></span>
      Advisory — both answers are defensible but shaped differently</span>
  </div>

  <div class="note-box">Scored flat across all fields this sample reads 0.76, which
  looks like a failing extractor. It is not. <em>category_path</em> and
  <em>option_values</em> sit at zero because the deterministic reader copies the
  site's breadcrumb and raw option encoding verbatim while the LLM infers its own
  granularity and reshapes options into semantic keys. Both readings are
  defensible, so comparing them by equality measures formatting rather than
  correctness. Only the core score is compared to the threshold, so a
  representation difference can neither manufacture nor mask an alert.
  <br><br>This is <strong>consistency, not accuracy</strong> — both extractors can
  agree and both be wrong. Real accuracy needs labelled samples or a trusted
  reference feed.</div>

  {_table_view(["Tier", "Field", "Agreement"], table_rows, numeric_from=2)}
</section>"""


def _distribution_section(data: DashboardData) -> str:
    if not data.categories:
        return ""
    top = max(count for _, count in data.categories)
    cat_rows = [
        (name, count / top, f"{count:,}", f"{name}: {count:,} families")
        for name, count in data.categories
    ]
    brand_rows = []
    if data.brands:
        brand_top = max(count for _, count in data.brands)
        brand_rows = [
            (name, count / brand_top, f"{count:,}", f"{name}: {count:,} families")
            for name, count in data.brands
        ]
    brand_block = (
        f'<div><div class="group-label">Top manufacturers</div>{_bar_rows(brand_rows)}</div>'
        if brand_rows
        else ""
    )
    table_rows = [[name, f"{count:,}"] for name, count in data.categories]
    table_rows += [[f"brand: {name}", f"{count:,}"] for name, count in data.brands]
    return f"""
<section class="card">
  <h2>Catalogue shape</h2>
  <p class="desc">How the extracted families distribute across the category
  hierarchy and their manufacturers. A sharp change in these counts between runs is
  a discovery regression rather than a smaller catalogue.</p>
  <div class="two">
    <div><div class="group-label">Category</div>{_bar_rows(cat_rows)}</div>
    {brand_block}
  </div>
  {_table_view(["Group", "Families"], table_rows)}
</section>"""


def _health_section(data: DashboardData) -> str:
    state_rows = [[name, f"{count:,}"] for name, count in data.crawl_state]
    error_rows = [[name, f"{count:,}"] for name, count in data.crawl_errors]
    price_rows = [[name, f"{count:,}"] for name, count in data.price_visibility]
    variant_rows = [[name, f"{count:,}"] for name, count in data.variants_per_product]
    errors_note = (
        "<p class=\"desc\">The error log is cumulative across runs: entries can "
        "predate a fix and are retained as an audit trail, so a non-zero count here "
        "does not mean the latest run failed.</p>"
        if error_rows
        else ""
    )
    return f"""
<section class="card">
  <h2>Crawl health</h2>
  <p class="desc">Checkpoint state, price visibility, and the families carrying the
  most variants. Price is always stored with an explicit visibility state, so a null
  price is never ambiguous between "gated" and "we failed to read it".</p>
  <div class="two">
    <div>
      <div class="group-label">Crawl state</div>
      {_table(["Status", "URLs"], state_rows)}
      <div class="group-label">Price visibility</div>
      {_table(["State", "Families"], price_rows)}
    </div>
    <div>
      <div class="group-label">Recorded errors (cumulative)</div>
      {_table(["Type", "Count"], error_rows) if error_rows else '<p class="desc">None recorded.</p>'}
      <div class="group-label">Most variants per family</div>
      {_table(["Family", "Variants"], variant_rows)}
    </div>
  </div>
  {errors_note}
</section>"""


def render_dashboard(data: DashboardData) -> str:
    coverage = data.coverage
    metrics = data.run_metrics
    failures = metrics.get("failures")
    duration = metrics.get("duration_seconds")

    tiles = [
        _tile(
            "Product families",
            f"{data.products:,}",
            f"of {data.discovered:,} discovered" if data.discovered else "",
            hero=True,
        ),
        _tile("Variants", f"{data.variants:,}", "current PDP child records"),
        _tile("Coverage", _pct(coverage) if coverage is not None else "n/a", "extracted of discovered"),
        _tile("Failures", f"{failures:,}" if failures is not None else "n/a", "in the last run"),
    ]
    if data.agreement_core is not None:
        tiles.append(
            _tile(
                "Core agreement",
                f"{data.agreement_core:.3f}",
                f"{data.agreement_sample} sampled pages",
            )
        )

    status = data.run_status or "unknown"
    duration_text = f" · last run {duration:.0f}s" if isinstance(duration, (int, float)) else ""
    model_text = f" · shadow model {data.agreement_model}" if data.agreement_model else ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Safco catalogue — data quality</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="viz-root">
<div class="wrap">

<header class="page">
  <h1>Safco catalogue &mdash; data quality</h1>
  <p class="sub">Sutures &amp; Surgical Products and Dental Exam Gloves, extracted by
  the Frontier Dental catalogue agent.</p>
  <p class="meta">
    <span class="badge"><span class="dot" style="background:{_status_colour(status)}"></span>
      run {_esc(status)}</span>
    &nbsp; generated {_esc(data.generated_at)}{_esc(duration_text)}{_esc(model_text)}
  </p>
</header>

<div class="kpi">{"".join(tiles)}</div>

{_coverage_section(data)}
{_completeness_section(data)}
{_provenance_section(data)}
{_agreement_section(data)}
{_distribution_section(data)}
{_health_section(data)}

<footer class="page">
  Generated from <code>data/catalog.db</code>, <code>output/run_report.json</code>,
  and <code>output/agreement_report.json</code>. No network access, no external
  assets.
</footer>

</div>
</div>
</body>
</html>
"""


def write_dashboard(
    path: str | Path,
    *,
    sqlite_path: str | Path,
    run_report_path: str | Path | None = None,
    agreement_path: str | Path | None = None,
    agreement_threshold: float = 0.80,
    generated_at: str | None = None,
) -> Path:
    data = collect_dashboard_data(
        sqlite_path=sqlite_path,
        run_report_path=run_report_path,
        agreement_path=agreement_path,
        agreement_threshold=agreement_threshold,
        generated_at=generated_at or datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_dashboard(data), encoding="utf-8")
    return target
