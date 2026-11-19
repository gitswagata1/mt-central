"""Offline procurement copilot: natural-language question -> SQL -> answer.

Demo mode uses deterministic templates, so every answer is reproducible and
shows the exact SQL and rows it came from. The production design puts an LLM
in place of `match()`, with the same contract: produce SQL, run it on DuckDB,
answer only from the returned rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CATEGORIES = ["Steel bar stock", "Bearings & seals", "Cutting tools", "Abrasives",
              "Lubricants & coolants", "Packaging", "Electrical spares", "Gauges & metrology"]

EXAMPLES = [
    "Top 5 vendors by spend",
    "Top 5 vendors by spend in PLANT-07",
    "Which vendors deliver late most often?",
    "Show price anomalies above 25%",
    "Monthly spend trend",
    "Spend by plant",
    "Spend on Cutting tools by vendor",
]


@dataclass
class Plan:
    intent: str
    sql: str
    explain: str


def _plant(q: str) -> str | None:
    m = re.search(r"plant[- ]?(\d{1,2})", q, re.I)
    return f"PLANT-{int(m.group(1)):02d}" if m else None


def _n(q: str, default: int = 5) -> int:
    m = re.search(r"\btop\s+(\d{1,2})\b", q, re.I)
    return int(m.group(1)) if m else default


def _category(q: str) -> str | None:
    ql = q.lower()
    for c in CATEGORIES:
        if c.lower().split(" ")[0] in ql:
            return c
    return None


def _pct(q: str, default: int = 25) -> int:
    m = re.search(r"(\d{1,3})\s*%", q)
    return int(m.group(1)) if m else default


def match(q: str) -> Plan | None:
    ql = q.lower()
    plant, cat = _plant(q), _category(q)
    where = []
    if plant:
        where.append(f"p.plant_id = '{plant}'")
    if cat:
        where.append(f"m.category = '{cat}'")
    w = ("WHERE " + " AND ".join(where)) if where else ""
    scope = " ".join(x for x in [f"in {plant}" if plant else "", f"for {cat}" if cat else ""] if x)

    if any(k in ql for k in ("late", "delay", "on-time", "on time", "deliver")):
        return Plan("late_vendors", f"""
SELECT v.vendor_id, v.vendor_name,
       COUNT(*) AS po_lines,
       ROUND(100.0 * AVG(CASE WHEN p.delay_days > 0 THEN 1 ELSE 0 END), 1) AS late_pct,
       ROUND(AVG(CASE WHEN p.delay_days > 0 THEN p.delay_days END), 1) AS avg_delay_days_when_late
FROM po_lines p JOIN vendors v USING (vendor_id) JOIN materials m USING (material_id)
{w}
GROUP BY ALL HAVING COUNT(*) >= 50
ORDER BY late_pct DESC LIMIT {_n(q, 8)}""", f"Vendors ranked by share of late PO lines {scope}".strip())

    if any(k in ql for k in ("anomal", "variance", "spike", "overpa", "price")):
        pct = _pct(q)
        return Plan("price_anomalies", f"""
WITH med AS (SELECT material_id, MEDIAN(unit_price) AS median_price FROM po_lines GROUP BY 1)
SELECT p.po_number, CAST(p.po_date AS DATE) AS po_date, p.plant_id, p.vendor_id, p.material_id, m.category,
       p.unit_price, ROUND(med.median_price, 2) AS median_price,
       ROUND(100 * (p.unit_price / med.median_price - 1), 1) AS variance_pct,
       ROUND(p.line_value - med.median_price * p.quantity, 0) AS excess_value
FROM po_lines p JOIN med USING (material_id) JOIN materials m USING (material_id)
WHERE p.unit_price > med.median_price * {1 + pct / 100}{' AND ' + ' AND '.join(where) if where else ''}
ORDER BY excess_value DESC LIMIT 15""", f"PO lines priced >{pct}% above the material's median {scope}".strip())

    if any(k in ql for k in ("trend", "month", "over time")):
        return Plan("trend", f"""
SELECT DATE_TRUNC('month', p.po_date) AS month, ROUND(SUM(p.line_value) / 1e5, 2) AS spend_lakh
FROM po_lines p JOIN materials m USING (material_id)
{w}
GROUP BY 1 ORDER BY 1""", f"Monthly spend (₹ lakh) {scope}".strip())

    if "plant" in ql and not plant:
        return Plan("by_plant", f"""
SELECT pl.plant_id, pl.region, ROUND(SUM(p.line_value) / 1e5, 1) AS spend_lakh, COUNT(*) AS po_lines
FROM po_lines p JOIN plants pl USING (plant_id) JOIN materials m USING (material_id)
{w}
GROUP BY ALL ORDER BY spend_lakh DESC""", f"Spend by plant (₹ lakh) {scope}".strip())

    if any(k in ql for k in ("vendor", "supplier", "spend", "top")):
        return Plan("top_vendors", f"""
SELECT v.vendor_id, v.vendor_name, ROUND(SUM(p.line_value) / 1e5, 1) AS spend_lakh,
       ROUND(100 * SUM(p.line_value) / SUM(SUM(p.line_value)) OVER (), 1) AS share_pct
FROM po_lines p JOIN vendors v USING (vendor_id) JOIN materials m USING (material_id)
{w}
GROUP BY ALL ORDER BY spend_lakh DESC LIMIT {_n(q)}""", f"Top vendors by spend (₹ lakh) {scope}".strip())
    return None


def answer(plan: Plan, df) -> str:
    if df.empty:
        return "No rows matched, so there is nothing to report."
    r = df.iloc[0]
    if plan.intent == "top_vendors":
        return (f"{r.vendor_id} leads with ₹{r.spend_lakh:,.1f} L ({r.share_pct}% of the filtered spend). "
                f"The top {len(df)} together hold {df.share_pct.sum():.1f}%.")
    if plan.intent == "late_vendors":
        return (f"{r.vendor_id} is late on {r.late_pct}% of its PO lines, "
                f"averaging {r.avg_delay_days_when_late} days late when it slips.")
    if plan.intent == "price_anomalies":
        return (f"{len(df)} lines shown. The largest is {r.po_number} ({r.material_id}) at "
                f"+{r.variance_pct}% vs median, about ₹{r.excess_value:,.0f} above the median-price cost.")
    if plan.intent == "trend":
        return (f"Spend went from ₹{df.spend_lakh.iloc[0]:,.1f} L in the first month to "
                f"₹{df.spend_lakh.iloc[-1]:,.1f} L in the last month of the window.")
    if plan.intent == "by_plant":
        return f"{r.plant_id} ({r.region}) has the highest spend: ₹{r.spend_lakh:,.1f} L across {r.po_lines:,} lines."
    return ""
