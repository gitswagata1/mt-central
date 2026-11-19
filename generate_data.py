"""Generate a SYNTHETIC, anonymised procurement dataset for demo mode.

No real company, vendor, material or price data is used. Names are placeholders
(PLANT-01, VEND-001, MAT-10001) and all numbers come from seeded random
distributions chosen to look like typical purchase-order data:
  * lognormal unit prices per material, small drift over time
  * vendor share per category follows a skewed (Dirichlet) split
  * delivery delay: most on time, a long tail of late deliveries
A few anomalies are injected on purpose so the alert engine has something to find.
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

SEED = 42
OUT = Path(__file__).parent / "data" / "procurement_synthetic.duckdb"

CATEGORIES = {
    "Steel bar stock": (180, 0.25), "Bearings & seals": (95, 0.4), "Cutting tools": (650, 0.5),
    "Abrasives": (420, 0.35), "Lubricants & coolants": (240, 0.3), "Packaging": (35, 0.3),
    "Electrical spares": (1300, 0.6), "Gauges & metrology": (2400, 0.5),
}
PLANT_REGIONS = ["West", "West", "West", "South", "South", "South", "North", "North",
                 "North", "East", "East", "West", "South", "North", "East"]


def build() -> None:
    rng = np.random.default_rng(SEED)
    plants = pd.DataFrame({"plant_id": [f"PLANT-{i:02d}" for i in range(1, 16)],
                           "region": PLANT_REGIONS,
                           "size_factor": rng.uniform(0.4, 1.8, 15).round(2)})
    vendors = pd.DataFrame({"vendor_id": [f"VEND-{i:03d}" for i in range(1, 41)],
                            "vendor_name": [f"Vendor {i:03d} (synthetic)" for i in range(1, 41)],
                            "reliability": rng.beta(8, 2, 40).round(3)})

    mats = []
    mid = 10001
    for cat, (base_price, sigma) in CATEGORIES.items():
        for _ in range(int(rng.integers(12, 22))):
            mats.append({"material_id": f"MAT-{mid}", "category": cat,
                         "base_price": round(float(rng.lognormal(np.log(base_price), sigma)), 2),
                         "uom": "KG" if cat in ("Steel bar stock", "Abrasives") else "EA"})
            mid += 1
    materials = pd.DataFrame(mats)

    # vendors serving each category, with skewed shares
    cat_vendors = {}
    for cat in CATEGORIES:
        k = int(rng.integers(3, 7))
        vs = rng.choice(vendors.vendor_id, size=k, replace=False)
        cat_vendors[cat] = (vs, rng.dirichlet(np.full(k, 0.8)))

    rows = []
    dates = pd.date_range("2025-11-01", "2026-10-31", freq="D")
    n = 24000
    m_idx = rng.integers(0, len(materials), n)
    p_idx = rng.choice(15, n, p=(plants.size_factor / plants.size_factor.sum()).to_numpy())
    d_idx = rng.integers(0, len(dates), n)
    rel = dict(zip(vendors.vendor_id, vendors.reliability))
    for i in range(n):
        m = materials.iloc[m_idx[i]]
        vs, w = cat_vendors[m.category]
        v = rng.choice(vs, p=w)
        d = dates[d_idx[i]]
        drift = 1 + 0.06 * (d_idx[i] / len(dates))
        price = m.base_price * drift * rng.lognormal(0, 0.06)
        qty = max(1, int(rng.lognormal(3.2, 0.9)))
        promised = int(rng.integers(7, 35))
        late = rng.random() > rel[v]
        delay = int(rng.gamma(2, 4)) if late else int(rng.integers(-2, 1))
        rows.append((f"PO-{450000 + i}", d.date(), plants.plant_id[p_idx[i]], v, m.material_id,
                     qty, round(price, 2), round(price * qty, 2), promised, delay))
    po = pd.DataFrame(rows, columns=["po_number", "po_date", "plant_id", "vendor_id", "material_id",
                                     "quantity", "unit_price", "line_value", "promised_days", "delay_days"])

    # injected anomalies (documented, so the demo is reproducible and honest)
    spike = po.sample(18, random_state=SEED).index
    po.loc[spike, "unit_price"] *= 1.45
    po.loc[spike, "line_value"] = (po.loc[spike, "unit_price"] * po.loc[spike, "quantity"]).round(2)

    OUT.parent.mkdir(exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    con = duckdb.connect(str(OUT))
    for name, df in {"plants": plants, "vendors": vendors, "materials": materials, "po_lines": po}.items():
        con.register("tmp_df", df)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM tmp_df")
        con.unregister("tmp_df")
    con.close()
    print(f"wrote {OUT} · {len(po):,} PO lines · {len(materials)} materials · 40 vendors · 15 plants")


if __name__ == "__main__":
    build()
