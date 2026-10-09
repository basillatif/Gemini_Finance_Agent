#!/usr/bin/env bash
# Load the sample revenue data into BigQuery for the Click Analytics demo.
# Usage: bash load_to_bigquery.sh
set -euo pipefail

PROJECT_ID="click-ge-demo"
DATASET="finance_demo"
TABLE="revenue_daily"
LOCATION="US"

gcloud config set project "$PROJECT_ID"
gcloud services enable bigquery.googleapis.com

# 1. Generate the CSV (skip if it already exists)
[ -f revenue_daily.csv ] || python3 generate_revenue.py

# 2. Create the dataset (ignore error if it already exists)
bq --location="$LOCATION" mk --dataset \
  --description "Sample revenue data for the ADK + Gemini Enterprise demo" \
  "$PROJECT_ID:$DATASET" || echo "Dataset already exists, continuing"

# 3. Load the table, replacing any previous load
bq load --replace --source_format=CSV --skip_leading_rows=1 \
  "$PROJECT_ID:$DATASET.$TABLE" revenue_daily.csv \
  order_date:DATE,region:STRING,product_line:STRING,channel:STRING,customer_segment:STRING,units:INTEGER,revenue_usd:NUMERIC,cost_usd:NUMERIC

# 4. Sanity check: revenue by region for last quarter (Q3 2026)
bq query --use_legacy_sql=false "
SELECT region, ROUND(SUM(revenue_usd), 0) AS revenue_usd
FROM \`$PROJECT_ID.$DATASET.$TABLE\`
WHERE order_date BETWEEN '2026-07-01' AND '2026-09-30'
GROUP BY region
ORDER BY revenue_usd DESC"
