"""Test the revenue tool directly against BigQuery, with no LLM involved.

Run from the folder that contains revenue_agent/:
    python test_tool.py

Expected for last quarter by region:
    West 2,864,084 | East 2,256,634 | South 1,536,222 | Central 1,523,650
"""
import json

from revenue_agent.agent import get_reporting_calendar, get_revenue

cal = get_reporting_calendar()
print("Calendar:", json.dumps(cal["periods"]["last_quarter"]))

lq = cal["periods"]["last_quarter"]
result = get_revenue("region", lq["start_date"], lq["end_date"])
print(json.dumps(result, indent=2))

expected = {"West": 2864084, "East": 2256634, "South": 1536222, "Central": 1523650}
got = {r["region"]: round(r["revenue_usd"]) for r in result["rows"]}
print("PASS" if got == expected else f"MISMATCH: {got}")

# Guardrail checks: these must be rejected without running a query.
print(get_revenue("customer_name", "2026-07-01", "2026-09-30")["status"])  # error
print(get_revenue("region", "2026-07-01", "2026-09-30", region="Mars")["status"])  # error
print(get_revenue("region", "2027-01-01", "2027-03-31")["status"])  # error

# Totals and period-over-period change are computed by the tool, not the model.
assert round(result["totals"]["revenue_usd"], 2) == 8180590.74, result["totals"]
yoy = get_revenue(
    "region", lq["start_date"], lq["end_date"],
    compare_start_date=cal["periods"]["same_quarter_last_year"]["start_date"],
    compare_end_date=cal["periods"]["same_quarter_last_year"]["end_date"],
)
print(json.dumps(yoy["comparison"], indent=2))
south = next(r for r in yoy["rows"] if r["region"] == "South")
print("South YoY:", south["change"]["revenue_change_pct"])
assert south["change"]["revenue_change_pct"] == 32.0, south["change"]
print(get_revenue("region", "2026-07-01", "2026-09-30", compare_start_date="2025-07-01")["status"])  # error
