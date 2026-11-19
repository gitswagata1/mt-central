# flake8: noqa: E501  (long SQL / HTML template lines)
"""Build the hosted demo (static snapshot of demo mode) into docs/ for GitHub Pages.

    python generate_data.py && python build_pages.py

Everything is computed from the synthetic DuckDB file with the same SQL the
Streamlit app uses. Charts are interactive (Plotly); copilot answers are
pre-computed for the example questions and show their SQL and rows.
"""

import html
import shutil
from pathlib import Path

import duckdb
import plotly
import plotly.express as px

import copilot

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
con = duckdb.connect(str(ROOT / "data" / "procurement_synthetic.duckdb"), read_only=True)


def q(sql):
    return con.execute(sql).df()


def table(df, limit=None):
    df = df.head(limit) if limit else df
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in r) + "</tr>"
                   for r in df.itertuples(index=False))
    return f'<div class="tbl"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def chart(fig):
    fig.update_layout(autosize=True, margin=dict(l=10, r=10, t=50, b=10), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", legend_title_text="")
    return fig.to_html(full_html=False, include_plotlyjs=False, default_width="100%", default_height="360px", config={"displayModeBar": False, "responsive": True})


k = q("""SELECT SUM(line_value) spend, COUNT(*) lines, COUNT(DISTINCT vendor_id) vendors,
         AVG(CASE WHEN delay_days <= 0 THEN 1 ELSE 0 END) on_time,
         AVG(CASE WHEN delay_days > 0 THEN delay_days END) avg_late FROM po_lines""").iloc[0]
alerts_price = q("""WITH med AS (SELECT material_id, MEDIAN(unit_price) mp FROM po_lines GROUP BY 1)
SELECT p.po_number, CAST(p.po_date AS DATE) AS po_date, p.plant_id, p.vendor_id, p.material_id,
       ROUND(100 * (p.unit_price / med.mp - 1), 1) AS variance_pct, ROUND(p.line_value - med.mp * p.quantity, 0) AS excess_value
FROM po_lines p JOIN med USING (material_id) WHERE p.unit_price > med.mp * 1.25 ORDER BY excess_value DESC""")
alerts_vendor = q("""SELECT vendor_id, COUNT(*) AS lines, ROUND(100 * AVG(CASE WHEN delay_days > 0 THEN 1 ELSE 0 END), 1) AS late_pct
FROM po_lines GROUP BY 1 HAVING COUNT(*) >= 50 AND late_pct > 30 ORDER BY late_pct DESC""")
alerts_conc = q("""WITH s AS (SELECT m.category, p.vendor_id, SUM(p.line_value) v FROM po_lines p JOIN materials m USING (material_id) GROUP BY ALL),
r AS (SELECT *, v / SUM(v) OVER (PARTITION BY category) share, ROW_NUMBER() OVER (PARTITION BY category ORDER BY v DESC) rn FROM s)
SELECT category, vendor_id, ROUND(100 * share, 1) AS share_pct FROM r WHERE rn = 1 AND share > 0.5 ORDER BY share_pct DESC""")
n_alerts = len(alerts_price) + len(alerts_vendor) + len(alerts_conc)

by_plant = q("""SELECT pl.plant_id, pl.region, SUM(p.line_value) / 1e5 AS spend_lakh FROM po_lines p JOIN plants pl USING (plant_id)
GROUP BY ALL ORDER BY spend_lakh DESC""")
by_vendor = q("SELECT vendor_id, SUM(line_value) / 1e5 AS spend_lakh FROM po_lines GROUP BY 1 ORDER BY 2 DESC LIMIT 12")
trend = q("""SELECT DATE_TRUNC('month', p.po_date) AS month, m.category, SUM(p.line_value) / 1e5 AS spend_lakh
FROM po_lines p JOIN materials m USING (material_id) GROUP BY ALL ORDER BY 1""")

f1 = px.bar(by_plant, x="plant_id", y="spend_lakh", color="region", title="Spend by plant (₹ lakh)",
            labels={"plant_id": "", "spend_lakh": "₹ lakh"})
f2 = px.bar(by_vendor.sort_values("spend_lakh"), x="spend_lakh", y="vendor_id", orientation="h",
            title="Top 12 vendors by spend (₹ lakh)", labels={"vendor_id": "", "spend_lakh": "₹ lakh"})
f2.update_traces(marker_color="#4f6bed")
f3 = px.area(trend, x="month", y="spend_lakh", color="category", title="Monthly spend by category (₹ lakh)",
             labels={"month": "", "spend_lakh": "₹ lakh", "category": ""})

qa_blocks, options = [], []
for i, ex in enumerate(copilot.EXAMPLES):
    plan = copilot.match(ex)
    df = q(plan.sql)
    options.append(f'<option value="qa{i}">{html.escape(ex)}</option>')
    qa_blocks.append(f'''<div class="qa" id="qa{i}" {'hidden' if i else ''}>
  <p class="ans">{html.escape(copilot.answer(plan, df))}</p>
  <p><b>{html.escape(plan.explain)}</b></p>{table(df)}
  <details open><summary>SQL used</summary><pre>{html.escape(plan.sql.strip())}</pre></details></div>''')

page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MT Central Intelligence · Demo</title>
<meta name="description" content="Procurement intelligence demo on synthetic data: KPIs, alerts and a SQL copilot.">
<script src="plotly.min.js"></script>
<style>
:root{{--bg:#f6f7fb;--panel:#fff;--ink:#1c1e24;--muted:#5d6472;--line:#e3e6ee;--accent:#4f6bed;--warn:#fff7e6;--warnb:#f5c26b;--warnt:#7a4b00;--ok:#e8f6ee;--okt:#14532d}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1200px;margin:0 auto;padding:24px 16px 60px}}
.demo{{background:var(--warn);border:1px solid var(--warnb);color:var(--warnt);padding:10px 14px;border-radius:8px;font-weight:600}}
h1{{margin:18px 0 2px;font-size:clamp(26px,4vw,38px)}}.sub{{color:var(--muted);margin:0 0 16px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}
.kpi{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}}.kpi span{{color:var(--muted);font-size:13px}}.kpi b{{display:block;font-size:28px}}
.tabs{{display:flex;gap:6px;flex-wrap:wrap;margin:22px 0 12px;border-bottom:1px solid var(--line)}}
.tabs button{{background:none;border:0;padding:10px 12px;font:inherit;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent}}
.tabs button.on{{color:var(--ink);border-color:var(--accent);font-weight:600}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px;margin-bottom:14px;min-width:0;overflow:hidden}}
.g2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}}
.tbl{{overflow:auto;max-height:420px;border:1px solid var(--line);border-radius:10px}}
table{{border-collapse:collapse;width:100%;font-size:13.5px}}th,td{{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}
th{{position:sticky;top:0;background:var(--panel);color:var(--muted)}}
select{{font:inherit;padding:8px;border-radius:8px;border:1px solid var(--line);width:100%;max-width:520px;background:var(--panel);color:var(--ink)}}
.ans{{background:var(--ok);color:var(--okt);padding:10px 12px;border-radius:8px;font-weight:600}}
pre{{background:#0f172a;color:#e2e8f0;padding:12px;border-radius:8px;overflow:auto;font-size:12.5px}}
.note{{color:var(--muted);font-size:13px}}h3{{margin:6px 0 10px}}a{{color:var(--accent)}}
</style></head><body><div class="wrap">
<div class="demo">Demo mode · synthetic data. Plants, vendors, materials and prices are generated; no company or SAP data is included. This page is a static snapshot of the Streamlit app.</div>
<h1>🏭 MT Central Intelligence</h1>
<p class="sub">Procurement intelligence across 15 plants · SAP extract → DuckDB → alerts + copilot → dashboard · <a href="https://github.com/gitswagata1/mt-central">code</a></p>
<div class="kpis">
<div class="kpi"><span>Total spend</span><b>₹{k.spend / 1e7:,.2f} Cr</b></div>
<div class="kpi"><span>PO lines</span><b>{int(k.lines):,}</b></div>
<div class="kpi"><span>Active vendors</span><b>{int(k.vendors)}</b></div>
<div class="kpi"><span>On-time delivery</span><b>{100 * k.on_time:.1f}%</b></div>
<div class="kpi"><span>Open alerts</span><b>{n_alerts}</b></div></div>
<div class="tabs" role="tablist">
<button class="on" data-t="dash">📊 Dashboard</button><button data-t="alerts">🚨 Alerts ({n_alerts})</button>
<button data-t="copilot">🤖 Copilot</button><button data-t="arch">🧭 Architecture</button></div>
<section id="dash"><div class="g2"><div class="panel">{chart(f1)}</div><div class="panel">{chart(f2)}</div></div>
<div class="panel">{chart(f3)}</div></section>
<section id="alerts" hidden>
<p class="note">The rules run as SQL. 18 price spikes were injected into the synthetic data on purpose, so the alerts can be checked against a known answer.</p>
<div class="panel"><h3>Price variance &gt; 25% vs material median · {len(alerts_price)}</h3>{table(alerts_price, 25)}</div>
<div class="g2"><div class="panel"><h3>Vendors late on &gt; 30% of lines · {len(alerts_vendor)}</h3>{table(alerts_vendor)}</div>
<div class="panel"><h3>Single vendor holds &gt; 50% of a category · {len(alerts_conc)}</h3>{table(alerts_conc)}</div></div></section>
<section id="copilot" hidden><div class="panel">
<p class="note">Copilot contract: question → SQL → DuckDB → answer from the returned rows only, with the SQL shown. Demo mode uses deterministic templates; this hosted page shows pre-computed answers for the example questions.</p>
<select id="qsel">{''.join(options)}</select>
{''.join(qa_blocks)}</div></section>
<section id="arch" hidden><div class="panel"><pre style="background:none;color:inherit">SAP ERP (PO / GR / vendor master)
        │  scheduled extract   (demo: generate_data.py → synthetic)
        ▼
DuckDB  ──►  SQL alert rules (price variance · late vendors · concentration)
        │
        ├──►  Copilot: question → SQL → DuckDB → answer + rows + SQL shown
        ▼
Dashboard (KPIs · spend by plant / vendor / category · alerts)</pre></div></section>
</div>
<script>
const fit = () => document.querySelectorAll('.js-plotly-plot').forEach(d => {{ if (d.offsetParent) Plotly.Plots.resize(d); }});
window.addEventListener('load', fit);
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {{
  document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('section').forEach(s => s.hidden = s.id !== b.dataset.t);
  fit();
}});
document.getElementById('qsel').onchange = e => document.querySelectorAll('.qa').forEach(d => d.hidden = d.id !== e.target.value);
</script></body></html>"""

DOCS.mkdir(exist_ok=True)
(DOCS / "index.html").write_text(page, encoding="utf-8")
shutil.copy(Path(plotly.__file__).parent / "package_data" / "plotly.min.js", DOCS / "plotly.min.js")
(DOCS / ".nojekyll").touch()
print("docs/ ready:", (DOCS / "index.html").stat().st_size // 1024, "KB")
