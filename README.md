# Deploying LlamaIndex Workflows to AWS Bedrock AgentCore

This demo shows how to deploy a LlamaIndex Workflow to AWS Bedrock AgentCore
Runtime using the `llama-agents-agentcore` package.

## What This Contains

| File | Description |
|------|-------------|
| `simple_workflow.py` | A minimal LlamaIndex Workflow that gets deployed |
| `deploy.py` | Thin CLI wrapper around `AgentCoreDeployer` — build, invoke, destroy |
| `customer-iam-role.yaml` | CloudFormation template for the required IAM roles |
| `pyproject.toml` | Project dependencies and workflow registration |

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│  deploy.py  (uses AgentCoreDeployer from llama-agents-agentcore)│
│                                                                 │
│  deployer.deploy(project_dir=".")                               │
│    1. Read dependencies from pyproject.toml                     │
│    2. Generate Dockerfile + buildspec                           │
│    3. Zip source → upload to S3                                 │
│    4. CodeBuild (ARM64) → build & push image to ECR             │
│    5. Create/update AgentCore Runtime with ECR image             │
│    6. Wait for READY status                                     │
│                                                                 │
│  deployer.invoke(runtime_arn, {"input": "Hello!"})              │
│  deployer.destroy_from_metadata(runtime)                        │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Inside the container (AgentCore Runtime)                       │
│                                                                 │
│  python -m llama_agents.agentcore.main --run                    │
│    └─ Reads [tool.llamadeploy.workflows] from pyproject.toml    │
│    └─ Imports & wraps workflows in BedrockAgentCoreApp          │
│    └─ Serves on :8080                                           │
└─────────────────────────────────────────────────────────────────┘
```

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

## Using AgentCoreDeployer Directly

For custom scripts or integration into your own tooling:

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
| `llama-agents-agentcore` | Container entrypoint (workflow discovery) | Same package — `python -m llama_agents.agentcore.main --run` |
| User's coder session code | The workflow being deployed | `simple_workflow.py` |
