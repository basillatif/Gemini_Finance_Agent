# Design decisions

For each major choice: what was chosen, the realistic alternatives, the
tradeoffs, and whether I'd change it for a production client. Line numbers
refer to `revenue_agent/revenue_agent/agent.py`.

---

## 1. Agent structure: one LlmAgent, no sub-agents

**Chosen:** a single `Agent` (`root_agent`, line 299) with two tools. No
router, no planner, no multi-agent handoff.

**Alternatives**
- *Router + specialists:* a coordinator that hands off to a "revenue agent",
  a "cost agent", a "forecast agent" (ADK `sub_agents` / `AgentTool`).
- *Workflow agents:* a `SequentialAgent` that always runs "resolve dates →
  query → format".
- *Plan-and-execute:* a planner that writes a multi-step plan first.

**Tradeoffs.** One agent with one data domain is the simplest thing that
works. Each handoff adds a model call (latency and cost) and a place to route
wrongly. The baseline eval shows the model already picks the right tools in
order 20/21 times without help. A fixed `SequentialAgent` would force a
calendar call even for "Q1 2026", which needs no calendar.

**For a production client:** keep it single-agent until there is a second
data domain (e.g. pipeline/CRM or headcount) with its own tools and rules.
At that point, add a thin router with one sub-agent per domain, so each
prompt and allowlist stays small.

---

## 2. Tool design: one parameterized "approved" query instead of text-to-SQL

**Chosen:** `get_revenue` (line 205) takes a `group_by` key, optional filters,
and dates. Each key maps to a SQL expression written in code
(`APPROVED_GROUP_BY`, line 29). Filters are checked against allowlists
(lines 256–263) and passed as BigQuery **query parameters** (lines 153–163).

**Alternatives**
- *Text-to-SQL:* let Gemini write SQL (possibly with the BigQuery toolset in
  ADK, or an MCP BigQuery server).
- *Many narrow tools:* `revenue_by_region()`, `revenue_by_month()`, …
- *A semantic layer:* Looker / dbt metrics, with the agent calling the
  metric API.

**Tradeoffs.** Text-to-SQL handles questions nobody anticipated, but it
can't be fully governed: you can't list every query it might run, and it
can be confidently wrong in ways that are hard to spot (wrong join, wrong
date filter). The approved tool can only answer what it was built for. In
return, every possible query is known and reviewable, injection-proof
(parameters, not string building), and cost-capped. Many narrow tools
would bloat the tool list the model chooses from. One tool with an enum-like
`group_by` keeps the choice simple.

**For a production client:** keep this pattern, but generate the allowlists
from one source. Today they appear in three places (Python sets, the tool
docstring, and prompt rule 3), which can drift apart. I'd use Python `Literal`
types or `Enum`s so ADK puts them in the function schema as enums, and build
the prompt text from the same constants. With a real semantic layer, I'd
have the tool call it instead of holding SQL.

---

## 3. Deterministic math in the tool, not the model

**Chosen:** totals (`_totals`, line 170) and period-over-period changes
(`_change`, line 181) are computed in Python with `Decimal`. The prompt
(rule 6) forbids the model from doing arithmetic.

**Alternatives:** let the model add up rows; or return raw rows and use a
code-execution tool for math.

**Tradeoffs.** LLMs are unreliable at adding many 7-digit numbers. The
session history in this repo shows a real case (a quarterly total off by
$100) from before this change. Code execution would work too, but it adds a
sandbox and a model call. The cost of this choice is that the tool must
anticipate which derived numbers people want (change %, margin points). Any
question that needs a new derived number needs a code change.

**For a production client:** keep it. Back it with the `grounded_figures`
eval (every figure in an answer must appear in a tool result) so a
regression is caught.

---

## 4. Model choice: Gemini 2.5 Flash

**Chosen:** `gemini-2.5-flash` (line 22), overridable with `MODEL`.

**Alternatives:** Gemini 2.5 Pro; Flash-Lite; a non-Google model through
ADK's LiteLLM support.

**Tradeoffs.** The hard reasoning (dates, SQL, math) lives in code, so the
model only has to pick a tool, fill a few arguments, and format a table.
Flash does that reliably at about half a cent per question, with 1.5–13 s
end-to-end latency per turn in the baseline eval. Pro would cost ~4× more per token and add
latency for little gain here. Flash-Lite is worth an eval run: if it passes
the same 20 cases, it's cheaper still.

**For a production client:** pin a specific model version rather than the
alias, so a model update can't silently change behavior. Re-run the evals
before changing it.

---

## 5. Prompt design: six numbered rules plus rich tool docstrings

**Chosen:** a short instruction with six numbered rules (line 307), and long
docstrings on each tool, which ADK turns into the function descriptions the
model sees.

**Alternatives:** a long persona-style prompt; few-shot examples; structured
output (a JSON schema for the answer).

**Tradeoffs.** Numbered rules are easy to audit and to tie to eval cases
("rule 1: never guess dates"). Docstrings put the guidance next to the
tool, where the model reads it when picking arguments. The eval run showed
the weak spot: **the prompt and the tool must agree.** The calendar docstring
promised "this year" but the tool returned no such period, so the model gave
up on "year to date". Few-shot examples would help formatting but cost
tokens on every call.

**For a production client:** keep the rules, and let the evals drive changes
to them. If answers feed another system, I'd use `output_schema` (structured
JSON) instead of a Markdown table.

---

## 6. Dates: a calendar tool anchored to the end of the data

**Chosen:** `get_reporting_calendar` (line 62) returns named periods. It
anchors to `min(today, DATA_END)`, so "last quarter" means the last full
quarter *with data*.

**Alternatives:** let the model compute dates from "today"; give the model
today's date in the prompt; a fiscal-calendar table in BigQuery.

**Tradeoffs.** Models are unreliable at quarter math, so moving it to code
is the right call. Anchoring to the data end suits a static demo dataset.
Real users asking in October 2026 get Q3 2026, which is correct. But in 2027
the same logic would still answer "last quarter" with Q3 2026, and "year to
date" with 2026, which is wrong for a live warehouse.

**For a production client — change it:**
- Read `DATA_START`/`DATA_END` from the table (`MIN`/`MAX(order_date)`,
  cached) instead of hard-coding them (lines 24–25).
- Anchor periods to today, and report "no data yet" for empty periods.
- Support a fiscal year if the client's differs from the calendar year.
- `same_period_last_year` would raise on Feb 29; it can't happen with this
  dataset's window, but a live calendar needs a guard.

---

## 7. State and memory

**Chosen:** the agent writes no session state and uses no memory service.
Conversation history is whatever the ADK session service keeps: in-memory for
`adk run`, SQLite (`.adk/session.db`) for `adk web`, Vertex AI Sessions on
Agent Engine. Follow-ups like "how does that compare to last year?" work
because the previous turn's tool results are in the history the model sees.

**Alternatives**
- *Session state:* save the last query parameters in `tool_context.state`, so
  follow-ups can reuse them explicitly.
- *Memory Bank:* long-term memory of user preferences ("I'm the West
  regional manager").
- *Stateless:* no history at all.

**Tradeoffs.** Relying on history is zero code and passed the multi-turn
eval case. The cost is tokens: every turn resends previous tool results, and
a long session with many monthly breakdowns grows quickly. Explicit state
would be more reliable for follow-ups and cheaper per turn, but adds code.

**For a production client:** keep history; add context compaction or a
turn limit for long sessions; consider storing the user's default region or
segment in state if users repeat it. I would not add Memory Bank for a
finance Q&A tool without a clear need: remembered "facts" about numbers go
stale, and storing them creates a data-retention question.

---

## 8. Data access and safety limits

**Chosen:** `maximum_bytes_billed` = 100 MB per query (line 42), job labels,
a lazily created shared BigQuery client (line 48), Application Default
Credentials.

**Alternatives:** BigQuery row-level security or authorized views; per-user
OAuth so queries run as the end user; a reservation instead of on-demand
pricing.

**Tradeoffs.** A byte cap plus labels are cheap and make cost visible. ADC
means the agent sees everything its service account can see. That's fine for
one shared finance dataset, but not when some users must not see some
regions.

**For a production client:** run as a dedicated service account with
dataset-level read only (see DEPLOY.md). If access differs by user, use
row-level security keyed on the caller's identity, passed through ADK's
auth support, so the restriction doesn't depend on the prompt.

---

## 9. Evals: deterministic metrics first, LLM judge for behavior

**Chosen:** ADK `adk eval` with three custom deterministic metrics (tool
arguments, required figures, grounded figures) plus ADK's
`rubric_based_final_response_quality_v1` judge for behavior. The answer key
is computed from the CSV by separate code.

**Alternatives:** only ADK's built-in metrics (`tool_trajectory_avg_score`,
`response_match_score`); only LLM-as-judge; unit tests on the tools only.

**Tradeoffs.** The built-in trajectory metric requires an exact argument
match, so `region=""` vs. no `region` argument fails a correct call. ROUGE
response matching doesn't care whether a number is right. Deterministic
checks are free, repeatable, and catch exactly the failures that matter in
finance (wrong or invented numbers). The judge handles what code can't easily
check ("did it decline politely and offer an alternative?"). That costs
a few cents per run, and the judge's scores vary somewhat from run to run.

**For a production client:** run the deterministic metrics in CI on every
prompt or tool change; run the judge before releases; add cases from real
user questions that failed.
