# AI Stock Agent — AWS AgentCore

An AI agent deployed on AWS AgentCore that answers questions about Amazon stock prices and financial reports using LangGraph (ReAct), FAISS RAG, and streaming responses.

---

## Architecture

```
User → Cognito (JWT) → AgentCore (FastAPI container)
                              │
                    LangGraph ReAct Agent
                    ├── retrieve_realtime_stock_price   (Alpha Vantage or yfinance fast_info)
                    ├── retrieve_historical_stock_price (Alpha Vantage or yfinance history)
                    └── retrieve_from_knowledge_base    (FAISS)
                              ├── Amazon 2024 Annual Report
                              ├── AMZN Q2 2025 Earnings
                              └── AMZN Q3 2025 Earnings

Config → AWS Secrets Manager (amzn-stock-agent/app-config)
Model  → Amazon Bedrock (us.anthropic.claude-3-5-haiku-20241022-v1:0)
Traces → Langfuse Cloud
```

---

## Prerequisites

- Python 3.11+
- Docker
- Terraform >= 1.5
- AWS CLI configured (`floba-dev` profile or equivalent)
- Langfuse Cloud account (free tier) — https://us.cloud.langfuse.com

---

## Deployment (AWS)

### Step 1 — Clone and configure environment

```bash
git clone <repo-url>
cd eflores-awsagentcore-tc
cp .env.example .env
# Fill in .env — see .env.example for all required values
# IMPORTANT: do NOT wrap values in quotes in .env
```

### Step 2 — Configure Langfuse keys in Terraform

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
# Edit terraform/terraform.tfvars and set:
#   langfuse_secret_key = "sk-lf-..."
#   langfuse_public_key = "pk-lf-..."
#   langfuse_host       = "https://us.cloud.langfuse.com"
```

### Step 3 — Deploy base infrastructure (ECR, Cognito, S3, IAM, Secrets Manager)

```bash
cd terraform
terraform init
terraform apply \
  -target=aws_ecr_repository.agent \
  -target=aws_ecr_lifecycle_policy.agent \
  -target=aws_s3_bucket.documents \
  -target=aws_s3_bucket_versioning.documents \
  -target=aws_s3_bucket_server_side_encryption_configuration.documents \
  -target=aws_s3_bucket_public_access_block.documents \
  -target=aws_cognito_user_pool.main \
  -target=aws_cognito_user_pool_client.main \
  -target=aws_iam_role.agentcore_execution \
  -target=aws_iam_role_policy.agentcore_permissions \
  -target=aws_secretsmanager_secret.app_config \
  -target=aws_secretsmanager_secret_version.app_config
```

### Step 4 — Copy Terraform outputs into .env

```bash
terraform output
# Copy cognito_user_pool_id    → COGNITO_USER_POOL_ID
# Copy cognito_app_client_id   → COGNITO_APP_CLIENT_ID
# Copy s3_bucket_name          → S3_BUCKET_NAME
```

### Step 5 — Ingest RAG documents and build FAISS index

```bash
cd ..
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
AWS_PROFILE=floba-dev python3 -m app.rag
# Downloads 3 PDFs and builds data/faiss_index/ (475 chunks)
```

### Step 6 — Build and push Docker image

> The FAISS index must exist in `data/faiss_index/` before building.

```bash
aws ecr get-login-password --region us-east-1 --profile floba-dev | \
  docker login --username AWS --password-stdin <ECR_URL>

docker build --platform linux/arm64 -t amzn-stock-agent .
docker tag amzn-stock-agent:latest <ECR_URL>:latest
docker push <ECR_URL>:latest
```

### Step 7 — Deploy AgentCore Runtime

```bash
cd terraform
terraform apply   # creates aws_bedrockagentcore_agent_runtime + endpoint
```

### Step 8 — Create Cognito test user

```bash
aws cognito-idp admin-create-user \
  --user-pool-id <COGNITO_USER_POOL_ID> \
  --username demo@example.com \
  --temporary-password 'Demo1234!$' \
  --message-action SUPPRESS \
  --region us-east-1 --profile floba-dev

aws cognito-idp admin-set-user-password \
  --user-pool-id <COGNITO_USER_POOL_ID> \
  --username demo@example.com \
  --password 'Demo1234!$' \
  --permanent \
  --region us-east-1 --profile floba-dev
```

> Or skip this step — the demo notebook creates the user automatically.

### Step 9 — Run the notebook

Open `notebooks/demo.ipynb` and run all cells top to bottom.

---

## Testing Locally

### Option A — FastAPI directly (no Docker)

```bash
source .venv/bin/activate
AWS_PROFILE=floba-dev uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Get a JWT and test:

```bash
TOKEN=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=demo@example.com,PASSWORD='Demo1234!$' \
  --client-id <COGNITO_APP_CLIENT_ID> \
  --region us-east-1 --profile floba-dev \
  --query 'AuthenticationResult.AccessToken' --output text)

curl -X POST http://localhost:8000/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the stock price for Amazon right now?"}' \
  --no-buffer
```

### Option B — Docker locally

```bash
docker build -t amzn-stock-agent .

docker run --rm -p 8000:8000 \
  --env-file .env \
  -e AWS_PROFILE=floba-dev \
  -v ~/.aws:/root/.aws:ro \
  amzn-stock-agent
```

Then use the same `curl` command above against `http://localhost:8000`.

> **Note:** All values in `.env` must be unquoted.
> `COGNITO_USER_POOL_ID=us-east-1_xxxx` ✅
> `COGNITO_USER_POOL_ID="us-east-1_xxxx"` ❌ (breaks JWKS URL)

### Option C — Test AgentCore invocation directly

```python
import boto3, json, uuid

agentcore = boto3.client('bedrock-agentcore', region_name='us-east-1')
response = agentcore.invoke_agent_runtime(
    agentRuntimeArn='arn:aws:bedrock-agentcore:us-east-1:<account>:runtime/<runtime-id>',
    runtimeSessionId=str(uuid.uuid4()),  # must be 33+ chars
    payload=json.dumps({'query': 'What is the AMZN stock price?', 'token': JWT_TOKEN}),
)
for chunk in response['response'].iter_chunks():
    print(chunk.decode('utf-8'), end='', flush=True)
```

---

## API Reference

### GET /health

```json
{ "status": "ok" }
```

### POST /query

**Auth (one of):**
- Header: `Authorization: Bearer <cognito_jwt>`
- Body field: `"token": "<cognito_jwt>"` (for AgentCore invoke_agent_runtime)

**Request:**
```json
{ "query": "What is the stock price for Amazon right now?" }
```

**Response:** `text/event-stream` — streamed text chunks including tool call notifications.

---

## Project Structure

```
app/
  main.py         # FastAPI app — POST /query (dual auth), GET /health
  agent.py        # LangGraph ReAct agent, Langfuse tracing, astream_events
  tools.py        # yfinance tools (fast_info for real-time, history for historical)
  rag.py          # PDF download, FAISS build/load, retriever tool
  auth.py         # Cognito JWKS fetch + RS256 JWT validation (python-jose)
  config.py       # Loads config from Secrets Manager (AgentCore) or .env (local)
data/
  amazon_reports/ # Downloaded PDFs (git-ignored)
  faiss_index/    # Generated FAISS index (git-ignored, must exist before docker build)
notebooks/
  demo.ipynb      # End-to-end demo — auth, 5 queries, Langfuse traces
terraform/
  main.tf               # ECR, S3, Cognito, IAM, Secrets Manager
  variables.tf
  outputs.tf
  agentcore_runtime.tf  # AgentCore Runtime + DEFAULT endpoint
  terraform.tfvars      # Langfuse keys (git-ignored)
Dockerfile
requirements.txt
.env.example
```

---

## Environment Variables

| Variable | Source | Description |
|---|---|---|
| `AWS_REGION` | Manual | AWS region (us-east-1) |
| `AWS_PROFILE` | Manual | AWS CLI profile (floba-dev) |
| `COGNITO_USER_POOL_ID` | `terraform output` | Cognito user pool ID |
| `COGNITO_APP_CLIENT_ID` | `terraform output` | Cognito app client ID |
| `S3_BUCKET_NAME` | `terraform output` | S3 bucket for documents |
| `BEDROCK_MODEL_ID` | Default | `us.anthropic.claude-3-5-haiku-20241022-v1:0` |
| `LANGFUSE_SECRET_KEY` | Langfuse dashboard | Secret key |
| `LANGFUSE_PUBLIC_KEY` | Langfuse dashboard | Public key |
| `LANGFUSE_HOST` | Langfuse dashboard | `https://us.cloud.langfuse.com` |
| `AGENT_ENDPOINT` | AWS Console / `terraform output` | AgentCore runtime ARN (notebook only) |

> Inside the AgentCore container all variables are loaded automatically from
> AWS Secrets Manager (`amzn-stock-agent/app-config`). No `.env` file needed in production.
