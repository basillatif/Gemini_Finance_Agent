"""Generate a realistic sample revenue dataset for the Click Analytics ADK demo.

Output: revenue_daily.csv
Grain: one row per day x region x product_line x channel
Range: 2024-10-01 through 2026-09-30 (8 full quarters, so "last quarter" = Q3 2026)
"""
import csv
import math
import random
from datetime import date, timedelta

random.seed(42)

START = date(2024, 10, 1)
END = date(2026, 9, 30)

REGIONS = {  # region: (base daily revenue share, annual growth)
    "West": (1.30, 0.14),
    "Central": (0.85, 0.06),
    "East": (1.15, 0.09),
    "South": (0.70, 0.18),
}
PRODUCTS = {  # product_line: (avg unit price, volume weight)
    "Analytics Platform": (1200.0, 0.35),
    "Data Services": (450.0, 0.30),
    "AI Add-ons": (300.0, 0.20),
    "Support Plans": (150.0, 0.15),
}
CHANNELS = {  # channel: share of volume
    "Direct": 0.50,
    "Partner": 0.30,
    "Online": 0.20,
}
SEGMENT_BY_CHANNEL = {"Direct": "Enterprise", "Partner": "Mid-Market", "Online": "SMB"}

BASE_DAILY_UNITS = 40.0


def seasonality(d: date) -> float:
    """Q4 budget flush, slow summer, end-of-quarter push, weekend dip."""
    month_factor = {
        1: 0.85, 2: 0.90, 3: 1.05, 4: 0.95, 5: 1.00, 6: 1.08,
        7: 0.88, 8: 0.85, 9: 1.02, 10: 1.05, 11: 1.12, 12: 1.30,
    }[d.month]
    # last 10 days of a quarter-end month get a sales push
    if d.month in (3, 6, 9, 12) and d.day >= 21:
        month_factor *= 1.15
    weekday_factor = 0.45 if d.weekday() >= 5 else 1.0
    return month_factor * weekday_factor


def main() -> None:
    rows = []
    d = START
    while d <= END:
        years_in = (d - START).days / 365.0
        s = seasonality(d)
        for region, (share, growth) in REGIONS.items():
            g = math.pow(1 + growth, years_in)
            for product, (price, pweight) in PRODUCTS.items():
                # AI Add-ons ramp faster over time
                ramp = 1.0 + (0.6 * years_in if product == "AI Add-ons" else 0.0)
                for channel, cshare in CHANNELS.items():
                    mean_units = BASE_DAILY_UNITS * share * pweight * cshare * s * g * ramp
                    units = max(0, int(random.gauss(mean_units, mean_units * 0.25)))
                    if units == 0:
                        continue
                    # channel pricing: partners get a discount, online is list
                    discount = {"Direct": 0.08, "Partner": 0.15, "Online": 0.0}[channel]
                    unit_price = price * (1 - discount) * random.uniform(0.95, 1.05)
                    revenue = round(units * unit_price, 2)
                    cost = round(revenue * random.uniform(0.32, 0.42), 2)
                    rows.append({
                        "order_date": d.isoformat(),
                        "region": region,
                        "product_line": product,
                        "channel": channel,
                        "customer_segment": SEGMENT_BY_CHANNEL[channel],
                        "units": units,
                        "revenue_usd": revenue,
                        "cost_usd": cost,
                    })
        d += timedelta(days=1)

    with open("revenue_daily.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows):,} rows to revenue_daily.csv")


if __name__ == "__main__":
    main()
