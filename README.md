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
| _(omitted)_ | Same as `run` | Backwards compatible — just pass `start_event` |

### Session ID as Handler ID

`context.session_id` (provided by AgentCore on every invoke) is used as the
**default handler_id** for all operations. This gives you:

- **1:1 session ↔ handler mapping** — each session owns one workflow handler
- **Idempotent invocations** — re-invoking the same session returns the cached
  result (completed), awaits (running), or starts fresh (failed/cancelled)
- **No explicit IDs needed** — callers just invoke, the session_id handles
  correlation automatically

For advanced cases (multiple concurrent workflows in one session), pass an
explicit `"handler_id"` in the payload to override.

```
 Session "sess-001"                    Session "sess-002"
 ┌──────────────────┐                  ┌──────────────────┐
 │ invoke #1: run   │                  │ invoke #1: run   │
 │  handler = sess-001                 │  handler = sess-002
 │  → starts workflow│                 │  → starts workflow│
 │                   │                 │                   │
 │ invoke #2: run   │                  │  (session stops)  │
 │  handler = sess-001                 │  SQLite flushed   │
 │  → awaits existing│                 │                   │
 │                   │                 │ invoke #2: run   │
 │ invoke #3: run   │                  │  handler = sess-002
 │  handler = sess-001                 │  → resumed from   │
 │  → returns cached │                 │    persisted ticks │
 └──────────────────┘                  └──────────────────┘
```

### Durable State via Session Storage

When AgentCore session storage is configured, the SQLite database lives on a
persistent mount (`/mnt/workspace/workflows.db`). This gives you:

- **Handler persistence** — running/completed handler records survive stop/resume
- **Event replay** — all streamed events are recorded and queryable after restart
- **Tick-based reconstruction** — WorkflowServer can reconstruct in-flight workflow
  state from persisted ticks without re-executing steps
- **Context store durability** — `ctx.store.set()`/`get()` values are in SQLite, not memory

The lifecycle:

1. First invoke → new compute, empty `/mnt/workspace`, fresh `workflows.db`
2. Workflow runs → handlers, events, ticks written to SQLite
3. Session stops → compute terminates, storage flushed to durable S3
4. Session resumes → new compute, `/mnt/workspace` restored, `WorkflowServer.start()` resumes incomplete handlers

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

## Request Examples

All examples use `context.session_id` as the implicit handler_id — no need to
pass `handler_id` explicitly.

### Run KYC verification synchronously

Each document in the `documents` array requires `file_b64` (base64-encoded file
content), `file_name`, and `doc_type` (`government_id`, `utility_bill`, or
`bank_statement`).

```python
import base64, json
from pathlib import Path

# Encode documents
def encode_doc(path, doc_type):
    return {
        "file_b64": base64.b64encode(Path(path).read_bytes()).decode(),
        "file_name": Path(path).name,
        "doc_type": doc_type,
    }

documents = [
    encode_doc("drivers_license.pdf", "government_id"),
    encode_doc("utility_bill.pdf", "utility_bill"),
    encode_doc("bank_statement.pdf", "bank_statement"),
]

resp = client.invoke_agent_runtime(
    agentRuntimeArn=arn,
    runtimeSessionId="1234-abcd-5678-efgh",
    payload=json.dumps({
        "action": "run",
        "workflow": "kyc",
        "start_event": {"documents": documents},
    }),
)
```

Response:
```json
{
  "handler_id": "sess-001",
  "session_id": "sess-001",
  "workflow_name": "kyc",
  "status": "completed",
  "result": {
    "value": {
      "decision": "PASS",
      "decision_reasoning": "All name and address checks passed across all three documents.",
      "checks": [
        {
          "check_name": "Name Match: ID vs Utility Bill",
          "doc_a_label": "Government ID",
          "doc_a_value": "ANDREW SAMPLE",
          "doc_b_label": "Utility Bill",
          "doc_b_value": "Andrew Sample",
          "passed": true,
          "reasoning": "Names match (case-insensitive).",
          "check_type": "name"
        }
      ],
      "extraction_results": { "..." : "..." }
    },
    "type": "StopEvent"
  }
}
```

Re-invoking the same session returns the cached result instantly.

### Run asynchronously + poll

```python
# Start (returns immediately)
invoke(session="sess-002", payload={
    "action": "run_nowait",
    "workflow": "kyc",
    "start_event": {"documents": documents},
})

# Poll (handler_id defaults to session_id)
invoke(session="sess-002", payload={"action": "get_result"})
```

### Human-in-the-loop

```python
# Start a review workflow
invoke(session="sess-003", payload={
    "action": "run_nowait",
    "workflow": "review",
    "start_event": {"document": "..."},
})

# Send approval event (handler_id = session_id automatically)
invoke(session="sess-003", payload={
    "action": "send_event",
    "event": {
        "value": {"feedback": "Approved", "approved": True},
        "qualified_name": "my_module.HumanFeedbackEvent",
    },
})
```

### Resume after session stop/restart

```python
# Stop the session
client.stop_runtime_session(agentRuntimeArn=arn, runtimeSessionId="sess-003")

# ... hours later ...

# Resume — new compute, but SQLite restored from session storage.
# WorkflowServer.start() reconstructs in-flight workflow from persisted ticks.
invoke(session="sess-003", payload={"action": "get_result"})

# Or check what events were recorded before the stop
invoke(session="sess-003", payload={"action": "get_events", "after_sequence": 0})
```

### Multiple workflows in one session

```python
# Override handler_id to run multiple workflows in a single session
invoke(session="sess-004", payload={
    "action": "run_nowait",
    "workflow": "extract",
    "handler_id": "sess-004:extract",
    "start_event": {"file_id": "doc-123"},
})
invoke(session="sess-004", payload={
    "action": "run_nowait",
    "workflow": "classify",
    "handler_id": "sess-004:classify",
    "start_event": {"file_id": "doc-123"},
})
```

## Local Testing

You can run the KYC workflow locally without deploying to AgentCore:

```bash
# Requires LLAMA_CLOUD_API_KEY and AWS credentials (for Bedrock)
python workflow.py
```

This uses the sample documents in `sample_docs/` and prints the KYC decision
with all cross-document checks.

## Session Storage Configuration

To enable durable state, add `filesystemConfigurations` when creating
the AgentCore Runtime:

```python
client.create_agent_runtime(
    agentRuntimeName="my-workflow-agent",
    roleArn=execution_role_arn,
    agentRuntimeArtifact={
        "containerConfiguration": {
            "containerUri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent:latest"
        }
    },
    filesystemConfigurations=[
        {
            "sessionStorage": {
                "mountPath": "/mnt/workspace"
            }
        }
    ],
)
```

The entrypoint reads `SESSION_STORAGE_PATH` (default `/mnt/workspace`) and
places `workflows.db` there automatically.

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

## How It Maps to LlamaCloud

| LlamaCloud Component | What It Does | Demo Equivalent |
|---|---|---|
| `agentcore_deployer.py` | AWS orchestration (CodeBuild, ECR, Runtime) | `AgentCoreDeployer` class |
| `agentcore_deploy.py` | Temporal activity (role assumption, workspace) | `deploy.py` CLI |
| `llama-agents-agentcore` | Container entrypoint (workflow discovery) | `agentcore_entrypoint.py` |
| User's coder session code | The workflow being deployed | `workflow.py` |
