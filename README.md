# Deploying LlamaIndex Workflows to AWS Bedrock AgentCore

This demo shows how to deploy a LlamaIndex Workflow to AWS Bedrock AgentCore
Runtime using the `llama-agents-agentcore` package, with full WorkflowServer
capabilities exposed through a single AgentCore entrypoint.

## What This Contains

| File | Description |
|------|-------------|
| `workflow.py` | A minimal LlamaIndex Workflow that gets deployed |
| `deploy.py` | Thin CLI wrapper around `AgentCoreDeployer` — build, invoke, destroy |
| `customer-iam-role.yaml` | CloudFormation template for the required IAM roles |
| `pyproject.toml` | Project dependencies and workflow registration |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Client (boto3 invoke_agent_runtime)                                │
│                                                                     │
│  payload = {"action": "run", "workflow": "simple",                  │
│             "start_event": {"input": "Hello"}}                      │
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
│              └─ simple_workflow.py → SimpleWorkflow                  │
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
   - **Execution Role** — used by the AgentCore Runtime at runtime
3. **Python 3.12+**

## Quick Start

```bash
# Install dependencies
pip install boto3 llama-agents-agentcore[deploy]

# Deploy (replace with your role ARNs)
python deploy.py deploy \
  --deployment-role arn:aws:iam::123456789012:role/AgentCoreDeployRole \
  --execution-role arn:aws:iam::123456789012:role/AgentCoreExecutionRole \
  --region us-east-1

# Invoke the deployed workflow
python deploy.py invoke "Hello, world!"

# Clean up
python deploy.py destroy
```

## Request Examples

All examples use `context.session_id` as the implicit handler_id — no need to
pass `handler_id` explicitly.

### Run synchronously (default)

```python
# Client side (boto3)
resp = client.invoke_agent_runtime(
    agentRuntimeArn=arn,
    runtimeSessionId="sess-001",
    payload=json.dumps({
        "action": "run",
        "workflow": "simple",
        "start_event": {"input": "Alice"},
    }),
)
```

Response:
```json
{
  "handler_id": "sess-001",
  "session_id": "sess-001",
  "workflow_name": "simple",
  "status": "completed",
  "result": {
    "value": {"greeting": "Hello, Alice!", "original_input": "Alice"},
    "type": "StopEvent"
  }
}
```

Re-invoking the same session returns the cached result instantly.

### Run asynchronously + poll

```python
# Start (returns immediately)
invoke(session="sess-002", payload={"action": "run_nowait", "workflow": "simple", "start_event": {"input": "Bob"}})

# Poll (handler_id defaults to session_id)
invoke(session="sess-002", payload={"action": "get_result"})
```

### Human-in-the-loop

```python
# Start a review workflow
invoke(session="sess-003", payload={"action": "run_nowait", "workflow": "review", "start_event": {"document": "..."}})

# Send approval event (handler_id = session_id automatically)
invoke(session="sess-003", payload={
    "action": "send_event",
    "event": {"value": {"feedback": "Approved", "approved": True},
              "qualified_name": "my_module.HumanFeedbackEvent"},
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
result = deployer.invoke(runtime.arn, {"input": "Hello!"})
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
| User's coder session code | The workflow being deployed | `simple_workflow.py` |
