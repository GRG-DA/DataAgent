"""Generate deterministic sample datasets for testing DataAgent.

Writes CSV/JSON fixtures into data/raw/. Uses stdlib only (no pandas needed)
and a fixed seed so the data is reproducible across runs.

Run:  python scripts/make_sample_data.py [--rows 500] [--out data/raw]
"""

import argparse
import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 42
START_DATE = date(2026, 1, 1)

REGIONS = ["North", "South", "East", "West"]
SEGMENTS = ["SMB", "Mid-Market", "Enterprise"]
CATEGORIES = {
    "Electronics": [("Wireless Mouse", 24.99), ("USB-C Hub", 39.99), ("Mechanical Keyboard", 89.99), ("4K Webcam", 59.99)],
    "Office": [("Standing Desk Mat", 45.00), ("Ergonomic Chair", 199.00), ("Monitor Arm", 54.50), ("Desk Lamp", 31.25)],
    "Software": [("Analytics License (mo)", 49.00), ("Backup Plan (mo)", 12.00), ("API Overage Pack", 20.00), ("Pro Support (mo)", 99.00)],
}
FIRST = ["Asha", "Ben", "Chen", "Dia", "Ravi", "Lena", "Omar", "Kira", "Noah", "Mei", "Arun", "Zoya"]
LAST = ["Kumar", "Ng", "Park", "Volkov", "Iyer", "Schmidt", "Haddad", "Okafor", "Reyes", "Tan"]
CITIES = [("Pune", 5), ("Bengaluru", 6), ("Mumbai", 7), ("Delhi", 8), ("Chennai", 9), ("Hyderabad", 10)]
TICKET_SUBJECTS = ["Login loop after password reset", "Dashboard chart renders blank", "CSV import fails on large file",
                   "Scheduled export not delivered", "Slow query on big table", "Cannot invite teammate",
                   "Billing receipt missing", "API key auth returns 401"]


def _daterange_weights(days: int) -> list[date]:
    return [START_DATE + timedelta(days=i) for i in range(days)]


def make_customers(n: int, rng: random.Random) -> list[dict]:
    rows = []
    for cid in range(1, n + 1):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        city, _ = rng.choice(CITIES)
        rows.append({
            "customer_id": f"C{cid:04d}",
            "customer_name": f"{first} {last}",
            "segment": rng.choices(SEGMENTS, weights=[60, 30, 10])[0],
            "region": rng.choice(REGIONS),
            "city": city,
            "signup_date": (START_DATE - timedelta(days=rng.randint(1, 730))).isoformat(),
        })
    return rows


def make_sales(n: int, customers: list[dict], rng: random.Random) -> list[dict]:
    """Revenue == units * unit_price * (1 - discount_pct) by construction."""
    rows = []
    for oid in range(10_001, 10_001 + n):
        cust = rng.choice(customers)
        category = rng.choice(list(CATEGORIES))
        product, unit_price = rng.choice(CATEGORIES[category])
        units = rng.choices([1, 2, 3, 5, 10], weights=[40, 25, 15, 12, 8])[0]
        discount = rng.choices([0, 5, 10, 20], weights=[60, 20, 12, 8])[0]
        day = rng.choice(_daterange_weights(270))
        month_quarter = max(0.75, 1 - 0.0022 * (day - START_DATE).days)
        rows.append({
            "order_id": oid,
            "order_date": day.isoformat(),
            "customer_id": cust["customer_id"],
            "product": product,
            "category": category,
            "region": cust["region"],
            "units": units,
            "unit_price": round(unit_price * month_quarter, 2),
            "discount_pct": discount,
            "revenue": round(units * unit_price * month_quarter * (1 - discount / 100), 2),
        })
    rows.sort(key=lambda r: (r["order_date"], r["order_id"]))
    return rows


def make_tickets(n: int, customers: list[dict], rng: random.Random) -> list[dict]:
    rows = []
    status_weights = [50, 25, 15, 10]
    for tid in range(1, n + 1):
        cust = rng.choice(customers)
        day = rng.choice(_daterange_weights(180) + _daterange_weights(180)[1:])
        created = date(2026, 1, 1) + timedelta(days=rng.randint(0, 270))
        rows.append({
            "ticket_id": f"T{tid:05d}",
            "created_at": f"{created.isoformat()} {rng.randint(6, 21):02d}:{rng.randint(0, 59):02d}",
            "customer_id": cust["customer_id"],
            "subject": rng.choice(TICKET_SUBJECTS),
            "priority": rng.choice(["low", "medium", "high", "critical"]),
            "status": rng.choices(["open", "resolved", "pending", "closed"], weights=status_weights)[0],
            "response_hours": round(rng.uniform(0.2, 72.0), 1),
        })
    return rows


COST_RATIO = {"Software": 0.35, "Electronics": 0.60, "Office": 0.50}
CHANNELS = ["Organic Search", "Paid Ads", "Email", "Referral"]
CONV_RATE = {"Organic Search": 0.025, "Paid Ads": 0.032, "Email": 0.055, "Referral": 0.038}
WEEKDAY_MULT = {0: 1.15, 1: 1.10, 2: 1.10, 3: 1.05, 4: 1.00, 5: 0.55, 6: 0.50}


def make_monthly_kpis(sales: list[dict], rng: random.Random) -> list[dict]:
    """Aggregate order-level sales into month x region x category KPIs.

    Margin comes from COST_RATIO per category; targets carry +/- jitter so
    dashboards get a mix of beats and misses.
    """
    agg: dict[tuple, dict] = {}
    for s in sales:
        key = (s["order_date"][:7], s["region"], s["category"])
        a = agg.setdefault(key, {"orders": 0, "units": 0, "revenue": 0.0})
        a["orders"] += 1
        a["units"] += s["units"]
        a["revenue"] += s["revenue"]

    rows = []
    for (month, region, product_cat), a in sorted(agg.items()):
        revenue = round(a["revenue"], 2)
        cost = round(revenue * COST_RATIO[product_cat], 2)
        target = round(revenue * (1 + rng.uniform(-0.08, 0.15)) + 50, 2)
        rows.append({
            "month": month, "region": region, "category": product_cat,
            "orders": a["orders"], "units": a["units"], "revenue": revenue,
            "cost": cost, "profit": round(revenue - cost, 2),
            "margin_pct": round((revenue - cost) / revenue * 100, 1),
            "target": target,
            "attainment_pct": round(revenue / target * 100, 1),
        })
    return rows


def make_daily_traffic(days: int, rng: random.Random) -> list[dict]:
    """Daily web/traffic KPIs per channel with weekly seasonality, growth trend,
    and a Paid Ads decline from mid-June. Series: date, channel, visitors,
    signups, purchases, revenue."""
    base_visitors = {"Organic Search": 1400, "Paid Ads": 700, "Email": 450, "Referral": 300}
    rows = []
    for day_index, day in enumerate(_daterange_weights(days)):
        for channel in CHANNELS:
            v = base_visitors[channel] * (1 + 0.004 * day_index)
            v *= WEEKDAY_MULT[day.weekday()]
            if channel == "Paid Ads" and day >= date(2026, 6, 15):
                v *= max(0.45, 1 - 0.005 * (day - date(2026, 6, 15)).days)
            visitors = max(10, int(v * rng.uniform(0.9, 1.1)))
            signups = int(visitors * CONV_RATE[channel] * rng.uniform(0.85, 1.15))
            purchases = max(1, int(signups * rng.uniform(0.28, 0.42)))
            aov = round(rng.uniform(62, 104), 2)
            rows.append({
                "date": day.isoformat(), "channel": channel,
                "visitors": visitors, "signups": signups, "purchases": purchases,
                "revenue": round(purchases * aov, 2),
            })
    rows.sort(key=lambda r: (r["date"], r["channel"]))
    return rows


def write_xlsx(path: Path, sheets: dict[str, list[dict]]) -> bool:
    """Bonus artifact: multi-sheet Excel to exercise the .xlsx ETL path.
    Returns False (with no file written) when openpyxl is unavailable."""
    try:
        from openpyxl import Workbook
    except ImportError:
        return False
    wb = Workbook()
    default = wb.active
    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(title=sheet_name)
        ws.append(list(rows[0]))
        for row in rows:
            ws.append([row[k] for k in rows[0]])
    wb.remove(default)
    wb.save(path)
    return True


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=300, help="sales rows to generate")
    parser.add_argument("--customers", type=int, default=25, help="customer rows")
    parser.add_argument("--tickets", type=int, default=40, help="support ticket rows")
    parser.add_argument("--out", default="data/raw", help="output directory")
    args = parser.parse_args()

    out = Path(args.out)
    rng = random.Random(SEED)
    customers = make_customers(args.customers, rng)
    sales = make_sales(args.rows, customers, rng)
    tickets = make_tickets(args.tickets, customers, random.Random(SEED + 1))
    kpis = make_monthly_kpis(sales, rng)
    traffic = make_daily_traffic(270, rng)

    write_csv(out / "sample_customers.csv", customers)
    write_csv(out / "sample_sales.csv", sales)
    write_csv(out / "sample_monthly_kpis.csv", kpis)
    write_csv(out / "sample_daily_traffic.csv", traffic)

    tickets_path = out / "sample_support_tickets.json"
    tickets_path.write_text(json.dumps(tickets, indent=2), encoding="utf-8")

    if write_xlsx(out / "sample_sales.xlsx", {"sales": sales, "customers": customers}):
        print(f"wrote {args.out}\\sample_sales.xlsx              (sheets: sales, customers)")
    n_rev = sum(s["revenue"] for s in sales)
    print(f"wrote {args.out}\\sample_customers.csv        ({len(customers)} rows)")
    print(f"wrote {args.out}\\sample_sales.csv            ({len(sales)} rows, total revenue {n_rev:,.2f})")
    print(f"wrote {args.out}\\sample_monthly_kpis.csv     ({len(kpis)} rows, {min(k['month'] for k in kpis)}..{max(k['month'] for k in kpis)})")
    print(f"wrote {args.out}\\sample_daily_traffic.csv    ({len(traffic)} rows)")
    print(f"wrote {args.out}\\sample_support_tickets.json ({len(tickets)} tickets)")


if __name__ == "__main__":
    main()
