"""Revenue Agent: an ADK agent with one approved, read only BigQuery tool.

The model never writes SQL. It can only choose from the dimensions, filters,
and date ranges that engineering has approved below. That is the
"blessed tool" pattern: engineering owns the query, business users own
the questions.
"""
import os
import re
from datetime import date, timedelta
from decimal import Decimal

from google.adk.agents import Agent
from google.cloud import bigquery

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "click-ge-demo")
DATASET = os.getenv("BQ_DATASET", "finance_demo")
TABLE = os.getenv("BQ_TABLE", "revenue_daily")
MODEL = os.getenv("MODEL", "gemini-2.5-flash")

DATA_START = date(2024, 10, 1)
DATA_END = date(2026, 9, 30)

# Approved grouping dimensions. Keys are what the model may pass in;
# values are fixed SQL expressions written by engineering, never by the model.
APPROVED_GROUP_BY = {
    "none": None,
    "region": "region",
    "product_line": "product_line",
    "channel": "channel",
    "customer_segment": "customer_segment",
    "month": "FORMAT_DATE('%Y-%m', order_date)",
    "quarter": "CONCAT(CAST(EXTRACT(YEAR FROM order_date) AS STRING), '-Q', CAST(EXTRACT(QUARTER FROM order_date) AS STRING))",
}
APPROVED_REGIONS = {"West", "Central", "East", "South"}
APPROVED_PRODUCTS = {"Analytics Platform", "Data Services", "AI Add-ons", "Support Plans"}
APPROVED_CHANNELS = {"Direct", "Partner", "Online"}

MAX_BYTES_BILLED = 100 * 1024 * 1024  # 100 MB cap on every query
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_client = None


def _bq() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(project=PROJECT_ID)
    return _client


def _quarter_bounds(d: date) -> tuple[date, date]:
    q_start_month = 3 * ((d.month - 1) // 3) + 1
    start = date(d.year, q_start_month, 1)
    next_q = date(d.year + (q_start_month + 3 > 12), (q_start_month + 2) % 12 + 1, 1)
    return start, next_q - timedelta(days=1)


def get_reporting_calendar() -> dict:
    """Returns today's date, the data coverage window, and the date ranges for
    common finance periods. Call this first whenever the user says something
    relative like "last quarter", "this year", or "last month".

    Returns:
        dict: today, data_start, data_end, and named periods with start_date
        and end_date in YYYY-MM-DD format.
    """
    today = date.today()
    anchor = min(today, DATA_END)

    cur_q_start, _ = _quarter_bounds(anchor)
    if anchor == DATA_END or today > DATA_END:
        last_q_start, last_q_end = _quarter_bounds(DATA_END)
    else:
        last_q_end = cur_q_start - timedelta(days=1)
        last_q_start, _ = _quarter_bounds(last_q_end)
    prev_q_end = last_q_start - timedelta(days=1)
    prev_q_start, _ = _quarter_bounds(prev_q_end)

    last_m_end = date(anchor.year, anchor.month, 1) - timedelta(days=1) if anchor != DATA_END else DATA_END
    last_m_start = date(last_m_end.year, last_m_end.month, 1)

    def p(s, e):
        return {"start_date": s.isoformat(), "end_date": e.isoformat()}

    return {
        "today": today.isoformat(),
        "data_start": DATA_START.isoformat(),
        "data_end": DATA_END.isoformat(),
        "periods": {
            "last_quarter": p(last_q_start, last_q_end),
            "quarter_before_last": p(prev_q_start, prev_q_end),
            "same_quarter_last_year": p(
                date(last_q_start.year - 1, last_q_start.month, 1),
                date(last_q_end.year - 1, last_q_end.month, last_q_end.day),
            ),
            "last_month": p(last_m_start, last_m_end),
            "trailing_12_months": p(
                date(last_q_end.year - 1, last_q_end.month, last_q_end.day) + timedelta(days=1),
                last_q_end,
            ),
            "all_data": p(DATA_START, DATA_END),
        },
    }


def get_revenue(
    group_by: str,
    start_date: str,
    end_date: str,
    region: str = "",
    product_line: str = "",
    channel: str = "",
) -> dict:
    """Approved read only revenue query against the finance data warehouse.

    Returns revenue, cost, gross margin, and units for a date range,
    optionally grouped by one approved dimension and filtered.

    Args:
        group_by: One of "none", "region", "product_line", "channel",
            "customer_segment", "month", "quarter".
        start_date: Inclusive start date, YYYY-MM-DD.
        end_date: Inclusive end date, YYYY-MM-DD.
        region: Optional filter. One of West, Central, East, South. Empty for all.
        product_line: Optional filter. One of Analytics Platform, Data Services,
            AI Add-ons, Support Plans. Empty for all.
        channel: Optional filter. One of Direct, Partner, Online. Empty for all.

    Returns:
        dict: status, the query parameters used, and rows with revenue_usd,
        cost_usd, gross_margin_pct, and units. Rows are sorted by revenue
        descending, or chronologically when grouped by month or quarter.
    """
    # Validate every input against the approved lists before touching BigQuery.
    if group_by not in APPROVED_GROUP_BY:
        return {"status": "error", "message": f"group_by must be one of {sorted(APPROVED_GROUP_BY)}."}
    for name, value in (("start_date", start_date), ("end_date", end_date)):
        if not DATE_RE.match(value or ""):
            return {"status": "error", "message": f"{name} must be YYYY-MM-DD, got '{value}'."}
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if start > end:
        return {"status": "error", "message": "start_date must be on or before end_date."}
    if end < DATA_START or start > DATA_END:
        return {
            "status": "error",
            "message": f"No data in that range. Data covers {DATA_START} to {DATA_END}.",
        }
    for name, value, allowed in (
        ("region", region, APPROVED_REGIONS),
        ("product_line", product_line, APPROVED_PRODUCTS),
        ("channel", channel, APPROVED_CHANNELS),
    ):
        if value and value not in allowed:
            return {"status": "error", "message": f"{name} must be one of {sorted(allowed)} or empty."}

    dim_sql = APPROVED_GROUP_BY[group_by]
    select_dim = f"{dim_sql} AS {group_by}," if dim_sql else ""
    group_clause = "GROUP BY 1" if dim_sql else ""
    if group_by in ("month", "quarter"):
        order_clause = "ORDER BY 1"
    else:
        order_clause = "ORDER BY revenue_usd DESC"

    sql = f"""
        SELECT
          {select_dim}
          ROUND(SUM(revenue_usd), 2) AS revenue_usd,
          ROUND(SUM(cost_usd), 2) AS cost_usd,
          ROUND(SAFE_DIVIDE(SUM(revenue_usd) - SUM(cost_usd), SUM(revenue_usd)) * 100, 1) AS gross_margin_pct,
          SUM(units) AS units
        FROM `{PROJECT_ID}.{DATASET}.{TABLE}`
        WHERE order_date BETWEEN @start_date AND @end_date
          AND (@region = '' OR region = @region)
          AND (@product_line = '' OR product_line = @product_line)
          AND (@channel = '' OR channel = @channel)
        {group_clause}
        {order_clause}
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("start_date", "DATE", start),
            bigquery.ScalarQueryParameter("end_date", "DATE", end),
            bigquery.ScalarQueryParameter("region", "STRING", region),
            bigquery.ScalarQueryParameter("product_line", "STRING", product_line),
            bigquery.ScalarQueryParameter("channel", "STRING", channel),
        ],
        maximum_bytes_billed=MAX_BYTES_BILLED,
        labels={"app": "revenue-agent", "tool": "get-revenue"},
    )
    rows = _bq().query(sql, job_config=job_config).result()

    def clean(v):
        return float(v) if isinstance(v, Decimal) else v

    return {
        "status": "success",
        "query": {
            "group_by": group_by,
            "start_date": start_date,
            "end_date": end_date,
            "filters": {k: v for k, v in (("region", region), ("product_line", product_line), ("channel", channel)) if v},
        },
        "rows": [{k: clean(v) for k, v in dict(r).items()} for r in rows],
    }


root_agent = Agent(
    name="revenue_agent",
    model=MODEL,
    description=(
        "Answers revenue, cost, margin, and units questions from the company "
        "finance data warehouse, broken down by region, product line, channel, "
        "customer segment, month, or quarter."
    ),
    instruction=f"""
You are the Revenue Agent for the finance team. You answer questions about
revenue, cost, gross margin, and units using only the get_revenue tool.

Rules:
1. When the user mentions a relative period ("last quarter", "last month",
   "year over year", "trailing 12 months"), call get_reporting_calendar first
   and use the exact start_date and end_date it returns. Never guess dates.
2. Data covers {DATA_START} through {DATA_END}. If asked about dates outside
   that window, say so plainly.
3. You can only group by: region, product_line, channel, customer_segment,
   month, quarter, or none. You can filter by region, product_line, and
   channel. If someone asks for something outside that (customer names,
   individual deals, forecasts, editing data), say it is not in the approved
   tool and suggest the closest thing you can answer.
4. For comparisons (for example this quarter versus the same quarter last
   year), call get_revenue once per period and compute the change yourself.
5. Answer in this format: a one sentence headline answer, then a short
   table, then one line stating the exact date range and filters used.
   Format money as $1,234,567 (no cents for totals over $10,000).
6. Never invent numbers. Every figure must come from a tool result.
""",
    tools=[get_reporting_calendar, get_revenue],
)
