# KYC Verification Agent on AWS Bedrock AgentCore

Deploy an AI-powered KYC (Know Your Customer) document verification agent to
**AWS Bedrock AgentCore** in minutes. This demo shows how
[LlamaIndex Workflows](https://developers.llamaindex.ai/python/llamaagents/workflows/)
running on AgentCore can automate real-world compliance tasks — extracting
structured data from identity documents and cross-validating them with Claude.

## What It Does

1. **Document Extraction** — Three identity documents (Government ID, Utility
   Bill, Bank Statement) are processed *in parallel* through
   [LlamaParse Extract](https://developers.llamaindex.ai/python/cloud/llamaextract/getting_started/)
   to extract structured fields (name, address, account details).
2. **Cross-Validation** — Claude (via Bedrock) compares names and addresses
   across all three documents, handling abbreviations, formatting differences,
   and name ordering.
3. **KYC Decision** — Returns a structured **PASS / REVIEW / FAIL** verdict
   with per-check reasoning.

```
  Government ID ─┐
  Utility Bill  ──┼─→ LlamaParse Extract (parallel) ─→ Claude Cross-Validation ─→ KYC Decision
  Bank Statement ─┘
```

## Project Structure

| File | Purpose |
|------|---------|
| `workflow.py` | KYC workflow — LlamaParse extraction + Claude validation |
| `cli.py` | CLI to deploy, invoke, monitor, and tear down the agent |
| `customer-iam-role.yaml` | CloudFormation template for required IAM roles |
| `pyproject.toml` | Dependencies and workflow registration |
| `sample_docs/` | Sample PDFs for testing (driver's license, utility bill, bank statement) |

## Prerequisites

- **Python 3.10+** with [uv](https://docs.astral.sh/uv/) (or pip)
- **AWS credentials** configured (`aws configure` or environment variables)
- **IAM roles** from `customer-iam-role.yaml` deployed to the target account
- **LlamaCloud API key** — set `LLAMA_CLOUD_API_KEY` in `.env` (used by LlamaParse)

## Quick Start

```bash
# Install dependencies
uv sync

# Deploy to AgentCore
python cli.py deploy \
  --deployment-role arn:aws:iam::123456789012:role/AgentCoreDeployRole \
  --execution-role arn:aws:iam::123456789012:role/AgentCoreExecutionRole

# Run KYC verification
python cli.py invoke \
  --gov-id sample_docs/drivers_license.pdf \
  --utility-bill sample_docs/utility_bill.pdf \
  --bank-statement sample_docs/bank_statement.pdf

# Tear down
python cli.py destroy
```

## CLI Reference

All commands except `deploy` and `destroy` support `--local` to target a local
runtime at `localhost:8080` instead of the deployed agent.

| Command | Description |
|---------|-------------|
| `deploy` | Build container, push to ECR, create AgentCore Runtime |
| `invoke` | Run KYC workflow with document files |
| `status --handler-id ID` | Check handler status and result |
| `events --handler-id ID` | Retrieve recorded workflow events |
| `send-event --handler-id ID --event '{...}'` | Inject event into running workflow (human-in-the-loop) |
| `cancel --handler-id ID` | Cancel a running handler |
| `workflows` | List registered workflows |
| `handlers` | List all handlers (filter with `--workflow`, `--status`) |
| `destroy` | Tear down deployment and clean up AWS resources |

### Async workflow + polling

```bash
# Start without waiting
python cli.py invoke --no-wait \
  --gov-id sample_docs/drivers_license.pdf \
  --utility-bill sample_docs/utility_bill.pdf \
  --bank-statement sample_docs/bank_statement.pdf

# Poll for result
python cli.py status --handler-id <SESSION_ID>
```

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  cli.py  (or boto3 invoke_agent_runtime)                     │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  AgentCore Runtime (container)                               │
│                                                              │
│  BedrockAgentCoreApp  ←  single @app.entrypoint              │
│    │                                                         │
│    └─ AgentCoreService (WorkflowServer, SQLite store)        │
│         │                                                    │
│         └─ KYCWorkflow                                       │
│              ├─ start (fan-out 3 ExtractDocEvents)           │
│              ├─ extract_document ×3 (LlamaParse)             │
│              ├─ validate_documents (Claude via Bedrock)       │
│              └─ finalize → StopEvent with KYC decision       │
│                                                              │
│  /mnt/workspace/workflows.db  ← durable session storage     │
└──────────────────────────────────────────────────────────────┘
```

**Key points:**

- The `deploy` command builds the container with CodeBuild and deploys to AgentCore using the specified IAM roles.
- AgentCore exposes a single invoke endpoint per runtime. An `"action"` field in the payload routes to operations like `run`, `get_result`, `send_event`, etc.
- `context.session_id` is used as the default handler ID — re-invoking the same session returns cached results (completed), awaits (running), or starts fresh.
- SQLite state persists across session stop/resume via AgentCore session storage.

## Local Testing

Run the workflow locally without deploying to AgentCore:

```bash
# Requires LLAMA_CLOUD_API_KEY and AWS credentials (for Bedrock Claude)
python workflow.py
```

Uses the sample documents in `sample_docs/` and prints the KYC decision.

You can also launch the local AgentCore Runtime for testing:

```bash
uv run python -m llama_agents.agentcore.main --local
```

Then, you can call the CLI with `--local` to target the local runtime.

## IAM Roles

Deploy the CloudFormation stack in `customer-iam-role.yaml` to create:

- **Deployment Role** — used by CodeBuild to build and push containers to ECR
- **Execution Role** — assumed by the AgentCore Runtime (needs `bedrock:InvokeModel*` for Claude)
