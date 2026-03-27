#!/usr/bin/env python3
"""Deploy a LlamaIndex Workflow to AWS Bedrock AgentCore.

Uses ``AgentCoreDeployer`` from the ``llama-agents-agentcore`` package
to handle the full lifecycle: build, push, deploy, invoke, destroy.

Usage:
    python deploy.py deploy --deployment-role <ARN> --execution-role <ARN>
    python deploy.py invoke "Hello, world!"
    python deploy.py destroy
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import boto3

from llama_agents.agentcore.deploy import AgentCoreDeployer, DeployedRuntime

PROJECT_DIR = Path(__file__).parent
METADATA_FILE = PROJECT_DIR / ".agentcore" / "deployment.json"


def _save_metadata(runtime: DeployedRuntime) -> None:
    """Persist deployment metadata for invoke/destroy."""
    METADATA_FILE.parent.mkdir(exist_ok=True)
    METADATA_FILE.write_text(json.dumps(runtime.to_dict(), indent=2))


def _load_metadata() -> DeployedRuntime:
    """Load deployment metadata from a previous deploy."""
    if not METADATA_FILE.exists():
        raise FileNotFoundError("No deployment found. Run 'deploy' first.")
    return DeployedRuntime.from_dict(json.loads(METADATA_FILE.read_text()))


def cmd_deploy(args: argparse.Namespace) -> None:
    """Build, push, and deploy the workflow to AgentCore."""
    deployer = AgentCoreDeployer(
        session=boto3.Session(region_name=args.region),
        deployment_role=args.deployment_role,
        execution_role=args.execution_role,
    )

    runtime = deployer.deploy(project_dir=PROJECT_DIR)
    _save_metadata(runtime)

    print(f"\nDeployed successfully!")
    print(f"  Runtime: {runtime.name}")
    print(f"  ARN:     {runtime.arn}")
    print(f"\nTest with: python deploy.py invoke 'Hello, world!'")


def cmd_invoke(args: argparse.Namespace) -> None:
    """Invoke the deployed workflow."""
    meta = _load_metadata()
    deployer = AgentCoreDeployer(
        session=boto3.Session(region_name=meta.region),
        deployment_role="",  # not needed for invoke
        execution_role="",
    )

    payload = {"input": args.prompt}
    if args.workflow:
        payload["workflow"] = args.workflow

    result = deployer.invoke(meta.arn, payload)
    print(json.dumps(result, indent=2))


def cmd_destroy(args: argparse.Namespace) -> None:
    """Tear down the deployment and clean up AWS resources."""
    meta = _load_metadata()
    deployer = AgentCoreDeployer(
        session=boto3.Session(region_name=meta.region),
        deployment_role="",  # not needed for destroy
        execution_role="",
    )

    deployer.destroy_from_metadata(meta)
    print("Cleanup complete.")


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Deploy LlamaIndex Workflows to AWS Bedrock AgentCore"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    deploy_p = sub.add_parser("deploy", help="Deploy workflow to AgentCore")
    deploy_p.add_argument("--deployment-role", required=True, help="IAM role ARN for CodeBuild")
    deploy_p.add_argument("--execution-role", required=True, help="IAM role ARN for AgentCore Runtime")
    deploy_p.add_argument("--region", default="us-east-1", help="AWS region")

    invoke_p = sub.add_parser("invoke", help="Invoke deployed workflow")
    invoke_p.add_argument("prompt", help="Input prompt")
    invoke_p.add_argument("--workflow", help="Specific workflow name")

    sub.add_parser("destroy", help="Destroy deployment and clean up")

    args = parser.parse_args()
    {"deploy": cmd_deploy, "invoke": cmd_invoke, "destroy": cmd_destroy}[args.command](args)


if __name__ == "__main__":
    main()
