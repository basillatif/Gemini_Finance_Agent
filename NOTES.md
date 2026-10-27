# Audit notes

Snapshot of the repo as of 2026-10-26, before the portfolio work.

## What the agent does

The Revenue Agent answers finance questions such as "what was revenue by region
last quarter?" or "how did West do year over year?" against a BigQuery table of
daily revenue. It is a demo of the **"approved tool" pattern**: the model never
writes SQL. It can only call two Python functions, and those functions accept
only values from fixed lists that engineering has signed off on.

## Agent structure

- **One agent, no sub-agents.** `root_agent` is a single ADK `Agent` (an
  `LlmAgent`) in `revenue_agent/revenue_agent/agent.py`. There is no router,
  no orchestrator, and no multi-agent handoff.
- **Model:** `gemini-2.5-flash` on Vertex AI (`GOOGLE_GENAI_USE_VERTEXAI=TRUE`),
  overridable through the `MODEL` env var.
- **Instruction:** a six-rule system prompt: use the calendar tool for
  relative dates, respect the data window, stay inside the approved
  dimensions, use the comparison arguments, follow a fixed answer format, and
  never invent numbers or do arithmetic.
- **State and memory:** none written by the agent. Conversation history is
  kept by whichever ADK session service runs it (in memory for `adk run`,
  SQLite at `revenue_agent/revenue_agent/.adk/session.db` for `adk web`).
  There is no long-term memory service.
- **Callbacks / plugins:** none.
- **MCP:** none. Both tools are plain Python functions that ADK wraps as
  `FunctionTool`s automatically; no `McpToolset` or MCP server is involved.

## Tools

| Tool | What it does | Touches |
|---|---|---|
| `get_reporting_calendar()` | Turns "last quarter", "last month", "trailing 12 months", "same quarter last year" into exact dates, anchored to the data window | Nothing (pure Python) |
| `get_revenue(group_by, start_date, end_date, region, product_line, channel, compare_start_date, compare_end_date)` | Checks every argument against approved lists, runs a parameterized BigQuery query, returns rows + totals, and optionally a period-over-period comparison | BigQuery, read only, 100 MB billing cap per query |

Guardrails inside `get_revenue`:
- `group_by` keys map to SQL expressions written in code; the model picks a key, not SQL.
- Filters are validated against allowlists, then passed as **query parameters**, so no string is ever spliced into SQL from model output.
- Dates are checked for format, order, and overlap with the data window before any query runs.
- `maximum_bytes_billed` caps each query at 100 MB; job labels (`app`, `tool`) make cost attributable.
- Totals and % changes are computed in Python with `Decimal`, so the model copies numbers instead of doing math.

## Data

- `generate_revenue.py` builds 31,813 synthetic rows (fixed seed 42): one row
  per day × 4 regions × 4 product lines × 3 channels, 2024-10-01 to
  2026-09-30, with seasonality and per-region growth.
- `load_to_bigquery.sh` loads it to `click-ge-demo.finance_demo.revenue_daily`.
- `revenue_daily.csv` is committed so results can be checked without BigQuery.

## Tests

`revenue_agent/test_tool.py` calls the tools directly against BigQuery (no LLM),
checks Q3 2026 regional totals and South's +32.0% YoY, and checks that bad
inputs are rejected.

## Things found during the audit

1. **Broken venv.** `.venv` pointed to Homebrew Python 3.13, which is no longer
   installed, so every `.venv/bin/*` command failed. Rebuilt on Python 3.12.15
   (`.venv-broken-py313/` is the old one, safe to delete).
2. **History of a wrong total.** The SQLite session log shows an earlier run
   reporting last quarter's total as $8,180,691; the real figure is
   $8,180,591. That is why the tool now computes totals itself (commit
   `18f3688`).
3. **Calendar gaps.** The calendar tool has no "year to date", "this year" or
   "last year" periods, although the prompt asks the model to never guess dates.
   (Confirmed by the baseline eval and fixed; see EVALS.md.)
4. **Duplicated allowlists.** The approved values appear in three places
   (Python sets, the tool docstring, the prompt) and can drift apart.
5. **Data window is hard-coded** (`DATA_START`/`DATA_END`) rather than read
   from the table.
6. **`.env` was committed** to git; it has been removed from history (no keys
   were in it, only project/model settings).
