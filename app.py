"""MT Central Intelligence: demo mode (synthetic data).   streamlit run app.py"""

from pathlib import Path
import time

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

import copilot

DB = Path(__file__).parent / "data" / "procurement_synthetic.duckdb"
T0 = time.perf_counter()

st.set_page_config(page_title="MT Central Intelligence · Demo", page_icon="🏭", layout="wide")
st.markdown("""<style>
.block-container{padding-top:3.2rem}
.demo{background:#fff7e6;border:1px solid #f5c26b;color:#7a4b00;padding:.55rem .9rem;border-radius:8px;font-weight:600}
div[data-testid="stMetric"]{background:#f7f8fb;border:1px solid #e6e8ef;border-radius:10px;padding:.7rem .9rem}
</style>""", unsafe_allow_html=True)


@st.cache_resource
def con():
    if not DB.exists():
        import generate_data
        generate_data.build()
    return duckdb.connect(str(DB), read_only=True)


@st.cache_data
def q(sql: str) -> pd.DataFrame:
    return con().execute(sql).df()


st.markdown('<div class="demo">Demo mode · synthetic data. Plants, vendors, materials and prices are generated '
            '(<code>generate_data.py</code>); no company or SAP data is included.</div>', unsafe_allow_html=True)
st.title("🏭 MT Central Intelligence")
st.caption("Procurement intelligence across 15 plants · SAP extract → DuckDB → alerts + copilot → Streamlit")

# ---------------- filters ----------------
plants = q("SELECT plant_id, region FROM plants ORDER BY plant_id")
with st.sidebar:
    st.header("Filters")
    regions = st.multiselect("Region", sorted(plants.region.unique()))
    plant_opts = plants[plants.region.isin(regions)].plant_id if regions else plants.plant_id
    sel_plants = st.multiselect("Plant", list(plant_opts))
    st.divider()
    st.caption("Data: 24,000 synthetic PO lines, Nov 2025 – Oct 2026, seeded (reproducible).")

scope = sel_plants or list(plant_opts)
in_list = ",".join(f"'{p}'" for p in scope)
F = f"p.plant_id IN ({in_list})"

# ---------------- KPIs ----------------
k = q(f"""
SELECT SUM(line_value) AS spend, COUNT(*) AS lines, COUNT(DISTINCT vendor_id) AS vendors,
       AVG(CASE WHEN delay_days <= 0 THEN 1 ELSE 0 END) AS on_time,
       AVG(CASE WHEN delay_days > 0 THEN delay_days END) AS avg_late
FROM po_lines p WHERE {F}""").iloc[0]

ALERT_PCT = 25
alerts_price = q(f"""
WITH med AS (SELECT material_id, MEDIAN(unit_price) AS mp FROM po_lines GROUP BY 1)
SELECT p.po_number, CAST(p.po_date AS DATE) AS po_date, p.plant_id, p.vendor_id, p.material_id,
       ROUND(100 * (p.unit_price / med.mp - 1), 1) AS variance_pct,
       ROUND(p.line_value - med.mp * p.quantity, 0) AS excess_value
FROM po_lines p JOIN med USING (material_id)
WHERE {F} AND p.unit_price > med.mp * {1 + ALERT_PCT / 100}
ORDER BY excess_value DESC""")
alerts_vendor = q(f"""
SELECT vendor_id, COUNT(*) AS lines, ROUND(100 * AVG(CASE WHEN delay_days > 0 THEN 1 ELSE 0 END), 1) AS late_pct
FROM po_lines p WHERE {F} GROUP BY 1 HAVING COUNT(*) >= 50 AND late_pct > 30 ORDER BY late_pct DESC""")
alerts_conc = q(f"""
WITH s AS (SELECT m.category, p.vendor_id, SUM(p.line_value) v FROM po_lines p JOIN materials m USING (material_id)
           WHERE {F} GROUP BY ALL),
     r AS (SELECT *, v / SUM(v) OVER (PARTITION BY category) AS share,
                  ROW_NUMBER() OVER (PARTITION BY category ORDER BY v DESC) rn FROM s)
SELECT category, vendor_id, ROUND(100 * share, 1) AS share_pct FROM r WHERE rn = 1 AND share > 0.5
ORDER BY share_pct DESC""")
n_alerts = len(alerts_price) + len(alerts_vendor) + len(alerts_conc)

c = st.columns(5)
c[0].metric("Total spend", f"₹{k.spend / 1e7:,.2f} Cr")
c[1].metric("PO lines", f"{int(k.lines):,}")
c[2].metric("Active vendors", int(k.vendors))
c[3].metric("On-time delivery", f"{100 * k.on_time:.1f}%", help=f"Late lines average {k.avg_late:.1f} days late")
c[4].metric("Open alerts", n_alerts)

tab_dash, tab_alerts, tab_copilot, tab_arch = st.tabs(["📊 Dashboard", f"🚨 Alerts ({n_alerts})", "🤖 Copilot", "🧭 Architecture"])

# ---------------- dashboard ----------------
with tab_dash:
    a, b = st.columns(2)
    by_plant = q(f"""SELECT pl.plant_id, pl.region, SUM(p.line_value) / 1e5 AS spend_lakh
                     FROM po_lines p JOIN plants pl USING (plant_id) WHERE {F} GROUP BY ALL ORDER BY spend_lakh DESC""")
    fig = px.bar(by_plant, x="plant_id", y="spend_lakh", color="region", title="Spend by plant (₹ lakh)",
                 labels={"plant_id": "", "spend_lakh": "₹ lakh"})
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=50, b=10), legend_title_text="")
    a.plotly_chart(fig, use_container_width=True)

    by_vendor = q(f"""SELECT vendor_id, SUM(line_value) / 1e5 AS spend_lakh FROM po_lines p WHERE {F}
                      GROUP BY 1 ORDER BY 2 DESC LIMIT 12""")
    fig = px.bar(by_vendor.sort_values("spend_lakh"), x="spend_lakh", y="vendor_id", orientation="h",
                 title="Top 12 vendors by spend (₹ lakh)", labels={"vendor_id": "", "spend_lakh": "₹ lakh"})
    fig.update_traces(marker_color="#4f6bed")
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=50, b=10))
    b.plotly_chart(fig, use_container_width=True)

    trend = q(f"""SELECT DATE_TRUNC('month', p.po_date) AS month, m.category, SUM(p.line_value) / 1e5 AS spend_lakh
                  FROM po_lines p JOIN materials m USING (material_id) WHERE {F} GROUP BY ALL ORDER BY 1""")
    fig = px.area(trend, x="month", y="spend_lakh", color="category", title="Monthly spend by category (₹ lakh)",
                  labels={"month": "", "spend_lakh": "₹ lakh", "category": ""})
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=50, b=10))
    st.plotly_chart(fig, use_container_width=True)

# ---------------- alerts ----------------
with tab_alerts:
    st.caption("Rules run as SQL on every refresh. "
               "18 price spikes were injected into the synthetic data on purpose so the rules have something to find.")
    st.subheader(f"Price variance > {ALERT_PCT}% vs material median · {len(alerts_price)}")
    st.dataframe(alerts_price.head(25), hide_index=True, use_container_width=True)
    l, r = st.columns(2)
    l.subheader(f"Vendors late on > 30% of lines · {len(alerts_vendor)}")
    l.dataframe(alerts_vendor, hide_index=True, use_container_width=True)
    r.subheader(f"Single vendor holds > 50% of a category · {len(alerts_conc)}")
    r.dataframe(alerts_conc, hide_index=True, use_container_width=True)

# ---------------- copilot ----------------
with tab_copilot:
    st.caption("Offline copilot: question → SQL → DuckDB → answer from the returned rows only. "
               "Demo mode uses deterministic templates; the SQL and data behind every answer are shown.")
    ex = st.selectbox("Try an example", copilot.EXAMPLES, index=1)
    question = st.text_input("Ask about procurement", value=ex)
    plan = copilot.match(question)
    if plan is None:
        st.warning("Demo-mode copilot can't answer that yet. Try one of the examples (vendors, plants, late deliveries, "
                   "price anomalies, monthly trend, a category).")
    else:
        df = q(plan.sql)
        st.success(copilot.answer(plan, df))
        st.markdown(f"**{plan.explain}**")
        st.dataframe(df, hide_index=True, use_container_width=True)
        with st.expander("SQL used", expanded=True):
            st.code(plan.sql.strip(), language="sql")

# ---------------- architecture ----------------
with tab_arch:
    st.markdown("""
```
SAP ERP (PO / GR / vendor master)
        │  scheduled extract (demo: generate_data.py → synthetic)
        ▼
DuckDB  ──►  SQL alert rules (price variance · late vendors · concentration)
        │
        ├──►  Copilot: question → SQL → DuckDB → answer + rows + SQL shown
        ▼
Streamlit dashboard (KPIs · spend by plant/vendor · trends · alerts)
```
""")
    st.caption("Production connects to SAP extracts; this demo build ships only synthetic data.")

st.caption(f"Page computed in {time.perf_counter() - T0:.2f} s")
