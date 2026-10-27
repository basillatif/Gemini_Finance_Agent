# Deployment plan (not deployed)

**Status: waiting for approval.** Nothing in this file has been run. Every
command below either creates billable resources or changes IAM, so run them
yourself, or approve each step explicitly.

**Target:** Vertex AI Agent Engine (preferred), with Cloud Run as the fallback.
Project `click-ge-demo`, region `us-central1`, ADK 2.11.0.

## Why Agent Engine

| | Agent Engine | Cloud Run |
|---|---|---|
| What you deploy | The ADK agent object; Google runs the server | A container with `adk api_server` |
| Sessions | Managed (Vertex AI Sessions), no database to run | You bring one (Cloud SQL / AlloyDB / Firestore), or lose history on restart |
| Gemini Enterprise / Agentspace registration | Direct | Possible, more wiring |
| Tracing | `--trace_to_cloud` to Cloud Trace | Same flag, plus normal Cloud Run logs |
| Scale to zero | Yes, if `min_instances: 0` (default is 1) | Yes, by default |
| Control over HTTP / auth | Less (Vertex AI IAM only) | Full (IAP, custom domains, your own API) |

For a finance-team assistant that will be surfaced in Gemini Enterprise,
Agent Engine is less to operate. Cloud Run wins if you need a custom public
API or a UI on the same service.

## Steps: Agent Engine

### 1. One-time project setup

Enable the APIs:
```bash
gcloud services enable aiplatform.googleapis.com bigquery.googleapis.com cloudtrace.googleapis.com cloudbuild.googleapis.com
```

### 2. Least-privilege service account

By default the agent runs as the Vertex AI Reasoning Engine Service Agent. Give
it only what the tool needs: read one dataset and run query jobs.

Create the service account:
```bash
gcloud iam service-accounts create revenue-agent --display-name "Revenue Agent runtime"
```

Let it run BigQuery jobs (billed to this project):
```bash
gcloud projects add-iam-policy-binding click-ge-demo --member "serviceAccount:revenue-agent@click-ge-demo.iam.gserviceaccount.com" --role roles/bigquery.jobUser
```

Let it read only the `finance_demo` dataset (not every dataset in the project):
```bash
bq add-iam-policy-binding --member "serviceAccount:revenue-agent@click-ge-demo.iam.gserviceaccount.com" --role roles/bigquery.dataViewer click-ge-demo:finance_demo
```

Let it call Gemini:
```bash
gcloud projects add-iam-policy-binding click-ge-demo --member "serviceAccount:revenue-agent@click-ge-demo.iam.gserviceaccount.com" --role roles/aiplatform.user
```

Then set `"service_account"` in `.agent_engine_config.json` to this account
(see step 3). If you skip this step, grant the two BigQuery roles to the
Reasoning Engine Service Agent instead.

### 3. Size the instance down before the first deploy

The defaults are **1 always-on instance with 4 vCPU and 4 GiB**, which costs
about $250–320/month even with zero traffic. This agent is mostly waiting on
Gemini and BigQuery, so 1 vCPU / 2 GiB is plenty.
`revenue_agent/revenue_agent/.agent_engine_config.json` (created, not yet used):

```json
{
  "min_instances": 0,
  "max_instances": 5,
  "resource_limits": {"cpu": "1", "memory": "2Gi"},
  "container_concurrency": 3
}
```

`min_instances: 0` means no idle cost but a cold start of several seconds on
the first request after a quiet period. Use `1` for a live demo.

### 4. Gate on evals, then deploy

Deploy only if the evals pass (see EVALS.md):
```bash
cd revenue_agent && PYTHONPATH=. ../.venv/bin/adk eval revenue_agent evals/revenue_agent.evalset.json --config_file_path evals/test_config.json
```

Deploy:
```bash
cd revenue_agent && ../.venv/bin/adk deploy agent_engine --project click-ge-demo --region us-central1 --display_name "Revenue Agent" --description "Approved, read-only revenue Q&A over BigQuery" --trace_to_cloud revenue_agent
```

`adk deploy` packages `revenue_agent/revenue_agent/` with its
`requirements.txt` and reads `.env` for environment variables. Agent Engine
sets `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` itself; `BQ_DATASET`,
`BQ_TABLE` and `MODEL` come from `.env`. The command prints the resource
name `projects/…/reasoningEngines/<ID>`; save it.

### 5. Smoke test the deployed agent

Run one query through the remote agent with the Vertex AI SDK:

```python
import vertexai
client = vertexai.Client(project="click-ge-demo", location="us-central1")
agent = client.agent_engines.get(name="projects/click-ge-demo/locations/us-central1/reasoningEngines/<ID>")
for event in agent.stream_query(user_id="smoke", message="What was revenue by region last quarter?"):
    print(event)
```

Expect West $2,864,084 / East $2,256,634 / South $1,536,222 / Central $1,523,650.

### 6. Optional: register in Gemini Enterprise

In the Gemini Enterprise console: Agents → Add agent → Custom agent via Agent
Engine, and paste the reasoning engine resource name.

### 7. Rollback / teardown

Delete the deployed agent (stops all runtime and Sessions charges):
```bash
gcloud ai reasoning-engines delete <ID> --region us-central1
```

## Steps: Cloud Run (fallback)

```bash
cd revenue_agent && ../.venv/bin/adk deploy cloud_run --project click-ge-demo --region us-central1 --service_name revenue-agent --trace_to_cloud revenue_agent
```

Then make it private (no `--allow-unauthenticated`) and put IAP or an API
gateway in front. Add `--session_service_uri` pointing at a Cloud SQL Postgres
instance if conversation history must survive restarts; that database adds
about $10–30/month for the smallest instance.

## Estimated monthly cost

Assumptions: 1,000 questions/month (a small finance team), ~3 Gemini calls
per question (~11k input + ~1k output tokens in total), ~1–2 BigQuery
queries per question, ~6 session events per question.

| Item | Rate used | Agent Engine, min 0 | Agent Engine, min 1 (1 vCPU / 2 GiB) | Cloud Run |
|---|---|---|---|---|
| Runtime | $0.0864–0.0994 / vCPU-hr, $0.0090–0.0105 / GiB-hr; 50 vCPU-hr + 100 GiB-hr free | ~$0 (active time fits the free tier) | ~$70–80 | ~$0 (fits free tier) |
| Gemini 2.5 Flash | ~$0.30 / 1M input, ~$2.50 / 1M output tokens | ~$6 | ~$6 | ~$6 |
| BigQuery | $6.25 / TiB scanned, 10 MiB minimum per query; 1 TiB/month free | $0 (≈15 GiB/month) | $0 | $0 |
| Sessions | $0.25 / 1,000 events | ~$1.50 | ~$1.50 | $0 in memory, or ~$10–30 for Cloud SQL |
| Cloud Trace / Logging | First 2.5M spans and 50 GiB logs free | $0 | $0 | $0 |
| **Total** | | **≈ $8 / month** | **≈ $80–90 / month** | **≈ $6–36 / month** |

The main cost risk is the **default sizing** (4 vCPU, 1 instance always on,
≈ $250–320/month), not traffic. Gemini cost grows roughly linearly: 10,000
questions/month ≈ $60.

**Check before relying on these numbers.** Google's 2025 release notes list
Agent Engine at $0.0994/vCPU-hr and $0.0105/GiB-hr; newer third-party
summaries list $0.0864 and $0.0090. The Sessions rate and free tier come
from third-party summaries. Confirm current rates on the
[Vertex AI pricing page](https://cloud.google.com/vertex-ai/pricing) and set a
budget alert:

```bash
gcloud billing budgets create --billing-account <BILLING_ACCOUNT_ID> --display-name "revenue-agent" --budget-amount 50USD --threshold-rule percent=0.5 --threshold-rule percent=0.9
```

Sources: [Agent Builder release notes](https://docs.cloud.google.com/agent-builder/release-notes),
[nOps Vertex AI pricing guide](https://www.nops.io/?p=32024),
[Finout Vertex AI pricing](https://finout.io/blog/top-16-vertex-services-in-2026).
