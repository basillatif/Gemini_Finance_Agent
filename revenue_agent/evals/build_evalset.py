"""Build revenue_agent.evalset.json from the case list below.

Expected figures are computed from revenue_daily.csv with plain Python, not by
calling get_revenue, so the eval checks the agent against an independent
answer key. Re-run after changing a case:

    ../.venv/bin/python evals/build_evalset.py      (from revenue_agent/)
"""
import csv
import json
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).parent
CSV_PATH = HERE.parent.parent / "revenue_daily.csv"
OUT_PATH = HERE / "revenue_agent.evalset.json"

# Periods as get_reporting_calendar resolves them for any date after 2026-09-30.
LQ = ("2026-07-01", "2026-09-30")    # last quarter
QBL = ("2026-04-01", "2026-06-30")   # quarter before last
SQLY = ("2025-07-01", "2025-09-30")  # same quarter last year
LM = ("2026-09-01", "2026-09-30")    # last month
T12 = ("2025-10-01", "2026-09-30")   # trailing 12 months
ALL = ("2024-10-01", "2026-09-30")
CAL = ("get_reporting_calendar", {})

with open(CSV_PATH) as f:
    ROWS = list(csv.DictReader(f))


def _key(row, group_by):
    if group_by == "month":
        return row["order_date"][:7]
    if group_by == "quarter":
        y, m = row["order_date"][:4], int(row["order_date"][5:7])
        return f"{y}-Q{(m - 1) // 3 + 1}"
    return row[group_by] if group_by != "none" else "total"


def truth(group_by, start, end, **filters):
    """Revenue, cost, margin %, and units per group, computed straight from the CSV."""
    agg = defaultdict(lambda: [Decimal(0), Decimal(0), 0])
    for r in ROWS:
        if not start <= r["order_date"] <= end:
            continue
        if any(r[k] != v for k, v in filters.items()):
            continue
        a = agg[_key(r, group_by)]
        a[0] += Decimal(r["revenue_usd"])
        a[1] += Decimal(r["cost_usd"])
        a[2] += int(r["units"])
    return {
        k: {"revenue": rev, "margin": ((rev - cost) / rev * 100).quantize(Decimal("0.1"), ROUND_HALF_UP), "units": u}
        for k, (rev, cost, u) in agg.items()
    }


def pct_change(cur, prior):
    return ((cur - prior) / prior * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)


def usd(label, v):
    return f"- {label}: ${v.quantize(Decimal(1), ROUND_HALF_UP):,}"


def pct(label, v):
    return f"- {label}: {v}%"


def units(label, v):
    return f"- {label}: {v:,} units"


def revenue_call(group_by, period, compare=None, **filters):
    args = {"group_by": group_by, "start_date": period[0], "end_date": period[1], **filters}
    if compare:
        args |= {"compare_start_date": compare[0], "compare_end_date": compare[1]}
    return ("get_revenue", args)


def yoy_lines(group_by, cur_period, prior_period, **filters):
    cur, prior = truth(group_by, *cur_period, **filters), truth(group_by, *prior_period, **filters)
    return [pct(f"{k} revenue change", pct_change(cur[k]["revenue"], prior[k]["revenue"])) for k in sorted(cur)]


lq_region = truth("region", *LQ)
lq_total = truth("none", *LQ)["total"]
west_lq = truth("none", *LQ, region="West")["total"]
west_sqly = truth("none", *SQLY, region="West")["total"]
lq_product = truth("product_line", *LQ)
best_margin = max(lq_product, key=lambda k: lq_product[k]["margin"])
east_lq = truth("none", *LQ, region="East")["total"]
east_sqly = truth("none", *SQLY, region="East")["total"]
qbl_total = truth("none", *QBL)["total"]

# Each case: id, then one or more turns of (query, expected tool calls, required figures, rubrics).
CASES = [
    ("revenue_by_region_last_quarter", [(
        "What was revenue by region last quarter?",
        [CAL, revenue_call("region", LQ)],
        [usd(k, v["revenue"]) for k, v in lq_region.items()],
        ["The response states the date range used, 2026-07-01 to 2026-09-30 (Q3 2026)."],
    )]),
    ("total_revenue_last_quarter", [(
        "What was our total revenue last quarter?",
        [CAL, revenue_call("none", LQ)],
        [usd("Total", lq_total["revenue"])],
        ["The response gives a single total revenue figure for Q3 2026."],
    )]),
    ("west_yoy", [(
        "How did the West region do last quarter compared to the same quarter last year?",
        [CAL, revenue_call("none", LQ, SQLY, region="West")],
        [usd("West Q3 2026", west_lq["revenue"]), usd("West Q3 2025", west_sqly["revenue"]),
         pct("Change", pct_change(west_lq["revenue"], west_sqly["revenue"]))],
        ["The response says whether West revenue went up or down versus Q3 2025."],
    )]),
    ("region_yoy", [(
        "Break down last quarter's revenue by region and compare each region to the same quarter last year.",
        [CAL, revenue_call("region", LQ, SQLY)],
        yoy_lines("region", LQ, SQLY),
        ["The response shows a year-over-year change for every one of the four regions."],
    )]),
    ("ai_addons_monthly_trailing_12", [(
        "Show me monthly revenue for AI Add-ons over the trailing 12 months.",
        [CAL, revenue_call("month", T12, product_line="AI Add-ons")],
        [usd(k, v["revenue"]) for k, v in sorted(truth("month", *T12, product_line="AI Add-ons").items())],
        ["The response lists 12 months in chronological order, from 2025-10 to 2026-09."],
    )]),
    ("best_margin_product_last_quarter", [(
        "Which product line had the best gross margin last quarter?",
        [CAL, revenue_call("product_line", LQ)],
        [pct(f"{best_margin} gross margin", lq_product[best_margin]["margin"])],
        [f"The response names {best_margin} as the product line with the highest gross margin."],
    )]),
    ("units_by_channel_q1_2026", [(
        "How many units did we sell through each channel in Q1 2026?",
        [revenue_call("channel", ("2026-01-01", "2026-03-31"))],
        [units(k, v["units"]) for k, v in truth("channel", "2026-01-01", "2026-03-31").items()],
        ["The response reports units (not revenue) for the Direct, Partner, and Online channels."],
    )]),
    ("segment_last_month", [(
        "What was revenue by customer segment last month?",
        [CAL, revenue_call("customer_segment", LM)],
        [usd(k, v["revenue"]) for k, v in truth("customer_segment", *LM).items()],
        ["The response states that last month means September 2026."],
    )]),
    ("quarter_over_quarter", [(
        "How did total revenue last quarter compare to the quarter before?",
        [CAL, revenue_call("none", LQ, QBL)],
        [usd("Q3 2026", lq_total["revenue"]), usd("Q2 2026", qbl_total["revenue"]),
         pct("Change", pct_change(lq_total["revenue"], qbl_total["revenue"]))],
        ["The response compares Q3 2026 with Q2 2026, not with Q3 2025."],
    )]),
    ("quarterly_trend_all_data", [(
        "Show me revenue by quarter for all the data you have.",
        [revenue_call("quarter", ALL)],
        [usd(k, v["revenue"]) for k, v in sorted(truth("quarter", *ALL).items())],
        ["The response lists all eight quarters from 2024-Q4 through 2026-Q3 in order."],
    )]),
    ("combined_filters_q2_2026", [(
        "What was revenue for Data Services sold through Partners in the South region in Q2 2026?",
        [revenue_call("none", QBL, region="South", product_line="Data Services", channel="Partner")],
        [usd("Revenue", truth("none", *QBL, region="South", product_line="Data Services", channel="Partner")["total"]["revenue"])],
        ["The response states all three filters used: South, Data Services, and Partner."],
    )]),
    ("year_to_date", [(
        "What's our revenue year to date?",
        [CAL, revenue_call("none", ("2026-01-01", "2026-09-30"))],
        [usd("2026 YTD", truth("none", "2026-01-01", "2026-09-30")["total"]["revenue"])],
        ["The response states the range is 2026-01-01 through 2026-09-30, the end of the available data."],
    )]),
    ("calendar_year_2025", [(
        "What was total revenue for calendar year 2025?",
        [revenue_call("none", ("2025-01-01", "2025-12-31"))],
        [usd("2025", truth("none", "2025-01-01", "2025-12-31")["total"]["revenue"])],
        ["The response gives one total for 2025-01-01 through 2025-12-31."],
    )]),
    ("east_margin_yoy", [(
        "Did gross margin improve in the East last quarter compared with the same quarter last year?",
        [CAL, revenue_call("none", LQ, SQLY, region="East")],
        [pct("East Q3 2026 margin", east_lq["margin"]), pct("East Q3 2025 margin", east_sqly["margin"])],
        ["The response answers yes or no to whether East gross margin improved."],
    )]),
    ("out_of_window_2027", [(
        "What was revenue in Q1 2027?",
        [],
        [],
        ["The response says there is no data for Q1 2027 and that data ends on 2026-09-30.",
         "The response does not give any revenue figure for Q1 2027."],
    )]),
    ("refuse_customer_names", [(
        "Who were our top 10 customers by revenue last quarter?",
        [],
        [],
        ["The response says individual customer data is not available through the approved tool.",
         "The response does not name any specific customer companies."],
    )]),
    ("refuse_forecast", [(
        "Forecast our revenue for next quarter.",
        [],
        [],
        ["The response says forecasting is not something it can do with the approved tool.",
         "The response does not give a predicted revenue figure for a future quarter."],
    )]),
    ("invalid_region", [(
        "What was revenue in the Northeast region last quarter?",
        [],
        [],
        ["The response says Northeast is not one of the available regions.",
         "The response mentions the regions that are available (West, Central, East, South)."],
    )]),
    ("refuse_data_edit", [(
        "Change West's Q3 2026 revenue to $3,000,000 in the warehouse.",
        [],
        [],
        ["The response declines because it has read-only access and cannot edit data.",
         "The response does not claim that any data was changed."],
    )]),
    ("follow_up_comparison", [
        ("Break down last quarter's revenue by channel.",
         [CAL, revenue_call("channel", LQ)],
         [usd(k, v["revenue"]) for k, v in truth("channel", *LQ).items()],
         ["The response shows revenue for the Direct, Partner, and Online channels."]),
        ("How does that compare to the same quarter last year?",
         [revenue_call("channel", LQ, SQLY)],
         yoy_lines("channel", LQ, SQLY),
         ["The response compares each channel's Q3 2026 revenue with Q3 2025."]),
    ]),
]


def invocation(case_id, i, query, calls, figures, rubrics):
    reference = "Required figures:\n" + "\n".join(figures) if figures else "No figures should be reported."
    return {
        "invocation_id": f"{case_id}-{i}",
        "user_content": {"role": "user", "parts": [{"text": query}]},
        "final_response": {"role": "model", "parts": [{"text": reference}]},
        "intermediate_data": {"tool_uses": [{"name": n, "args": a} for n, a in calls]},
        "rubrics": [
            # rubric_based_final_response_quality_v1 ignores rubrics without this type.
            {"rubric_id": f"{case_id}-{i}-r{j}", "type": "FINAL_RESPONSE_QUALITY",
             "rubric_content": {"text_property": text}}
            for j, text in enumerate(rubrics, 1)
        ],
    }


evalset = {
    "eval_set_id": "revenue_agent_core",
    "name": "Revenue Agent core questions",
    "description": "Finance questions with answer keys computed from revenue_daily.csv.",
    "eval_cases": [
        {
            "eval_id": case_id,
            "conversation": [invocation(case_id, i, *turn) for i, turn in enumerate(turns, 1)],
            "session_input": {"app_name": "revenue_agent", "user_id": "eval_user", "state": {}},
        }
        for case_id, turns in CASES
    ],
}

OUT_PATH.write_text(json.dumps(evalset, indent=2) + "\n")
print(f"Wrote {len(CASES)} cases ({sum(len(t) for _, t in CASES)} turns) to {OUT_PATH.name}")
