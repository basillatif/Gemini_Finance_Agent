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
