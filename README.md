# Deploying a KYC Verification Workflow to AWS Bedrock AgentCore

This demo shows how to deploy a LlamaIndex Workflow to AWS Bedrock AgentCore
Runtime using the `llama-agents-agentcore` package, with full WorkflowServer
capabilities exposed through a single AgentCore entrypoint.

The example workflow performs **KYC (Know Your Customer) document verification**:
it extracts structured data from three identity documents in parallel using
LlamaParse, then cross-validates names and addresses with Claude via Bedrock.

## What This Contains

| File | Description |
|------|-------------|
| `workflow.py` | KYC verification workflow — LlamaParse extraction + Claude cross-validation |
| `deploy.py` | Thin CLI wrapper around `AgentCoreDeployer` — deploy, invoke, destroy |
| `customer-iam-role.yaml` | CloudFormation template for the required IAM roles |
| `pyproject.toml` | Project dependencies and workflow registration |
| `sample_docs/` | Sample KYC documents for local testing (driver's license, utility bill, bank statement) |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Client (boto3 invoke_agent_runtime)                                │
│                                                                     │
│  payload = {"action": "run", "workflow": "kyc",                     │
│             "start_event": {"documents": [...]}}                    │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  AgentCore Runtime (container)                                      │
│                                                                     │
│  agentcore_entrypoint.py                                            │
│    │                                                                │
│    ├─ BedrockAgentCoreApp  ←  single @app.entrypoint                │
│    │                                                                │
│    └─ WorkflowServer  ←  programmatic (no internal HTTP)            │
│         │                                                           │
│         ├─ SqliteWorkflowStore( /mnt/workspace/workflows.db )       │
│         │   └─ handlers, events, ticks, context state               │
│         │                                                           │
│         └─ Registered Workflows                                     │
│              └─ workflow.py → KYCWorkflow                            │
│                   ├─ start (fan-out 3 ExtractDocEvents)             │
│                   ├─ extract_document ×3 (LlamaParse)               │
│                   ├─ validate_documents (Claude via Bedrock)        │
│                   └─ finalize → StopEvent with KYC decision         │
│                                                                     │
│  /mnt/workspace/  ← AgentCore session storage (survives stop/resume)│
│    └─ workflows.db                                                  │
└─────────────────────────────────────────────────────────────────────┘
```

### Translation Layer

AgentCore exposes a single invoke function per runtime. The entrypoint
translates an `"action"` field in the payload into the corresponding
WorkflowServer operation:

| Action | WorkflowServer Operation | Description |
|--------|--------------------------|-------------|
| `run` | `service.start_workflow()` + `await_workflow()` | Run workflow synchronously |
| `run_nowait` | `service.start_workflow()` | Start workflow, return handler_id immediately |
| `get_result` | `service.load_handler()` | Poll handler status and result |
| `get_events` | `store.query_events()` | Retrieve recorded workflow events |
| `send_event` | `service.send_event()` | Inject event into running workflow (human-in-the-loop) |
| `cancel` | `service.cancel_handler()` | Cancel a running workflow |
| `list_workflows` | `server.get_workflows()` | List registered workflow names |
| `list_handlers` | `service.query_handlers()` | List handlers (filter by workflow/status) |
| _(omitted)_ | Same as `run` | Just pass `start_event` |

## Prerequisites

1. **AWS credentials** configured (`aws configure`)
2. **IAM roles** created in the target account (see `customer-iam-role.yaml`):
   - **Deployment Role** — used by CodeBuild to build/push containers to ECR
   - **Execution Role** — used by the AgentCore Runtime at runtime (needs `bedrock:InvokeModel*` for Claude)
3. **LlamaCloud API key** — set `LLAMA_CLOUD_API_KEY` (used by LlamaParse for document extraction)
4. **Python 3.10+**

## Quick Start

```bash
# Install dependencies
pip install boto3 llama-agents-agentcore

# Deploy (replace with your role ARNs)
python deploy.py deploy \
  --deployment-role arn:aws:iam::123456789012:role/AgentCoreDeployRole \
  --execution-role arn:aws:iam::123456789012:role/AgentCoreExecutionRole \
  --region us-east-1

# Invoke the deployed KYC workflow (payload is JSON)
python deploy.py invoke \
    --gov-id sample_docs/drivers_license.pdf \
    --utility-bill sample_docs/utility_bill.pdf \
    --bank-statement sample_docs/bank_statement.pdf

# Clean up
python deploy.py destroy
```

## Local Testing

You can run the KYC workflow locally without deploying to AgentCore:

```bash
# Requires LLAMA_CLOUD_API_KEY and AWS credentials (for Bedrock)
python workflow.py
```

This uses the sample documents in `sample_docs/` and prints the KYC decision
with all cross-document checks.

## Using AgentCoreDeployer Directly

```python
import boto3
from llama_agents.agentcore.deploy import AgentCoreDeployer

deployer = AgentCoreDeployer(
    session=boto3.Session(region_name="us-east-1"),
    deployment_role="arn:aws:iam::123456789012:role/AgentCoreDeployRole",
    execution_role="arn:aws:iam::123456789012:role/AgentCoreExecutionRole",
)

# Deploy — builds container, pushes to ECR, creates AgentCore Runtime
runtime = deployer.deploy(project_dir=".")

# Invoke
result = deployer.invoke(runtime.arn, {
    "action": "run",
    "workflow": "kyc",
    "start_event": {"documents": [...]},
})
print(result)

# Tear down
deployer.destroy_from_metadata(runtime)
```

