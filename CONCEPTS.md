# ADK and Google Cloud concepts map

Each concept in plain language, then where it shows up in this project.
`agent.py` means `revenue_agent/revenue_agent/agent.py`. Library paths are
under `.venv/lib/python3.12/site-packages/google/adk/` (ADK 2.11.0).

## ADK building blocks

### Agent (`LlmAgent`)
**What it is:** the unit of "an LLM with instructions and tools". You give
it a model, an instruction (system prompt), a description, and a list of
tools. ADK handles the loop: send the conversation to the model, run any tool
the model asks for, send the result back, repeat until the model answers in
text. `Agent` is an alias for `LlmAgent`.

**Here:** `root_agent = Agent(...)` at `agent.py:299`. ADK finds it by
name: `revenue_agent/__init__.py` imports `agent`, and the CLI looks for a
variable called `root_agent`.

### Instruction and description
**What they are:** the *instruction* is the system prompt, the rules the
model follows. The *description* is a one-line summary of what the agent is
for. Other agents use it to decide whether to hand work to this one, and
Gemini Enterprise shows it in its agent list.

**Here:** the six rules at `agent.py:307`; the description at
`agent.py:302`. The instruction is an f-string, so `DATA_START`/`DATA_END`
are filled in when the module loads.

### Tools and `FunctionTool`
**What they are:** functions the model can ask to run. If you pass a plain
Python function, ADK wraps it in a `FunctionTool`. It reads the function's
**name, type hints, and docstring** to build the schema the model sees, so
the docstring *is* the tool's prompt. When the model returns a function
call, ADK checks the arguments, calls your function, and sends the returned
dict back to the model.

**Here:** `tools=[get_reporting_calendar, get_revenue]` at `agent.py:338`.
Schema building: `tools/function_tool.py:130` (`_get_declaration`).
Invocation: `tools/function_tool.py:357` (`run_async`). Tools return
`{"status": "error", "message": ...}` instead of raising, so the model can
read the error and recover (e.g. "Mars is not a region").

### Runner
**What it is:** the engine that runs an agent for one user message. It
loads the session, adds the user's message as an event, runs the agent,
saves every event the agent produces, and streams those events back. `adk
run`, `adk web`, `adk eval`, and Agent Engine all create a Runner for you.

**Here:** no Runner in this repo's code; the CLI builds one. Entry point:
`runners.py:1114` (`Runner.run_async`). `adk run` calls it from
`cli/cli.py:226` (interactive) or `cli/cli.py:99` (input file).

### Session, Event, and State
**What they are:**
- A **Session** is one conversation: an id, the user id, and an ordered list
  of **Events**.
- An **Event** is anything that happened: the user's message, a model
  response, a function call, a function result. Each event records its
  author and a `state_delta`.
- **State** is a key-value scratchpad attached to the session (keys
  prefixed `user:` or `app:` are shared more widely). Agents and tools
  read/write it through `tool_context.state`.

**Here:** the agent writes no state; it relies on the event history for
follow-up questions. You can see real sessions in
`revenue_agent/revenue_agent/.adk/session.db` (the `events` table), which is
where `adk web` stored the runs used in NOTES.md.

### Session service
**What it is:** where sessions are stored. `InMemorySessionService` (lost on
restart), `DatabaseSessionService` (SQLite/Postgres via SQLAlchemy), or
`VertexAiSessionService` (managed by Agent Engine).

**Here:** `adk run --session_service_uri memory://` for the smoke test;
`adk web` uses SQLite under `.adk/`; Agent Engine would use Vertex AI
Sessions automatically (DEPLOY.md).

### Memory service (not used)
**What it is:** long-term memory *across* sessions (e.g. Vertex AI Memory
Bank), searched by a `load_memory` tool. Different from session history,
which only covers the current conversation.

**Here:** deliberately not used; see DECISIONS.md §7.

### LLM flow (the agent loop)
**What it is:** the internal loop inside an `LlmAgent`. Each "step" builds
an `LlmRequest` (instruction + history + tool schemas), calls the model, and
if the response contains function calls, runs them and loops again. It stops
when the model returns a final text answer.

**Here:** `flows/llm_flows/base_llm_flow.py:322` (`run_async`, the
`while True` loop), `:367` (`_run_one_step_async`), `:596`
(`_call_llm_async`). Function-call handling:
`flows/llm_flows/core/_function_call_postprocessor.py:71`. A typical
question in this project takes three steps (see WALKTHROUGH.md).

### Model connector (`Gemini`)
**What it is:** the class that turns an `LlmRequest` into a Gemini API call
and the reply back into events. It picks Vertex AI or the Gemini Developer
API from `GOOGLE_GENAI_USE_VERTEXAI`.

**Here:** `model=MODEL` (a string) at `agent.py:301`; ADK resolves it to
`models/google_llm.py:228` (`generate_content_async`). `.env` sets
`GOOGLE_GENAI_USE_VERTEXAI=TRUE`, so calls go to Vertex AI in
`GOOGLE_CLOUD_LOCATION`, billed to `GOOGLE_CLOUD_PROJECT`.

### Callbacks and plugins (not used)
**What they are:** hooks ADK calls before/after the agent, model, or tool
runs (`before_model_callback`, `after_tool_callback`, …). Plugins are the
same hooks applied to every agent in an app. Typical uses: logging, guardrails,
caching, redacting PII.

**Here:** none. The guardrails live inside the tools instead (validation in
`get_revenue`, `agent.py:246–263`). An `after_tool_callback` for audit
logging is the first thing I'd add (README → Production considerations).

### `.env` loading
**What it is:** the ADK CLI loads the `.env` file in the agent folder
before importing the agent, so `os.getenv` works at import time.

**Here:** `cli/utils/envs.py:53` (`load_dotenv_for_agent`). That's why
`agent.py:17` can require `GOOGLE_CLOUD_PROJECT` at import. Plain scripts
like `test_tool.py` have to call `load_dotenv` themselves.

### Evals (`adk eval`)
**What they are:** ADK's test harness for agents. An **eval set** is a JSON
file of **eval cases**; each case is a conversation of **invocations**
(user message → expected tool calls → reference answer, optional rubrics).
`adk eval` runs the agent on each user message, then scores the actual
invocations with the **metrics** listed in `test_config.json`. Metrics can
be built in (trajectory match, ROUGE, LLM-judge rubrics, hallucination
check) or custom Python functions.

**Here:** `revenue_agent/evals/revenue_agent.evalset.json` (built by
`build_evalset.py`), `test_config.json` (metrics and thresholds), and
`metrics.py` (three custom metrics registered through `custom_metrics` /
`code_config`). Results land in
`revenue_agent/revenue_agent/.adk/eval_history/`; `summarize.py` turns them
into the tables in EVALS.md.

### `adk web`, `adk run`, `adk api_server`
**What they are:** local front ends for the same Runner. `run` is a
terminal chat; `web` is a FastAPI server plus a dev UI with an event and
trace inspector; `api_server` is the same API without the UI.

**Here:** `.claude/launch.json` starts `adk web` on port 8000. The repo's
`Agent_trace.png` and `Full_event_tab.png` are screenshots of the dev UI's
trace and event views.

## Google Cloud pieces

### Vertex AI (Gemini)
**What it is:** Google Cloud's managed AI platform; here, the endpoint that
serves Gemini. Billed per input and output token, to the project in `.env`.

**Here:** every model call. Auth is Application Default Credentials
(`gcloud auth application-default login` locally; the service account in
the cloud).

### BigQuery
**What it is:** Google's serverless data warehouse. On-demand queries are
billed by bytes scanned (10 MB minimum per query). It supports **query
parameters** (`@name` placeholders), **labels** on jobs, and a
**maximum_bytes_billed** cap that fails a query rather than overspending.

**Here:** `_query` at `agent.py:129`; parameters and cap at `agent.py:153–163`;
the table is created by `load_to_bigquery.sh`.

### Application Default Credentials (ADC)
**What it is:** the standard way Google client libraries find credentials:
your `gcloud` login locally, the attached service account in the cloud. No
keys in code.

**Here:** `bigquery.Client(project=PROJECT_ID)` at `agent.py:48` and the
Gemini client both use ADC, which is why no API key appears anywhere.

### Vertex AI Agent Engine
**What it is:** a managed runtime for agents. You hand it the ADK agent;
it packages it, runs it on autoscaling containers, provides managed Sessions
(and optionally Memory Bank), integrates with Cloud Trace, and gives you an
API endpoint governed by Vertex AI IAM. It can be registered in Gemini
Enterprise.

**Here:** not deployed. Plan, sizing, and cost in DEPLOY.md;
`.agent_engine_config.json` sets scale-to-zero and 1 vCPU / 2 GiB.

### Cloud Run (alternative)
**What it is:** a serverless container platform. `adk deploy cloud_run`
builds a container running `adk api_server`. More control (custom auth,
domains), but you bring your own session database.

**Here:** fallback option in DEPLOY.md.

### Cloud Trace / OpenTelemetry
**What it is:** distributed tracing. ADK emits OpenTelemetry spans for each
agent run, model call, and tool call; `--trace_to_cloud` exports them to
Cloud Trace.

**Here:** used locally through the `adk web` trace view
(`Agent_trace.png`); enabled in the deploy command in DEPLOY.md.

### IAM and service accounts
**What they are:** who can do what. A service account is a non-human
identity the agent runs as. Roles grant permissions at the project or
resource level.

**Here:** DEPLOY.md step 2 creates `revenue-agent@…` with
`bigquery.jobUser` (project), `bigquery.dataViewer` (one dataset), and
`aiplatform.user`. That's what makes the agent read-only regardless of what
the model asks for.
