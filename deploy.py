#!/usr/bin/env python3
"""Deploy a LlamaIndex Workflow to AWS Bedrock AgentCore.

Uses ``AgentCoreDeployer`` from the ``llama-agents-agentcore`` package
to handle the full lifecycle: build, push, deploy, invoke, destroy.

Usage:
    python deploy.py deploy --deployment-role <ARN> --execution-role <ARN>
    python deploy.py invoke --gov-id doc.pdf --utility-bill doc.pdf --bank-statement doc.pdf
    python deploy.py destroy
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import time
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
    session = boto3.Session(region_name=args.region, profile_name=args.profile)
    deployer = AgentCoreDeployer(
        session=session,
        deployment_role=args.deployment_role,
        execution_role=args.execution_role,
    )

    runtime = deployer.deploy(project_dir=PROJECT_DIR)
    _save_metadata(runtime)

    print(f"\nDeployed successfully!")
    print(f"  Runtime: {runtime.name}")
    print(f"  ARN:     {runtime.arn}")
    print(f"\nTest with:")
    print(f"  python deploy.py invoke \\")
    print(f"    --gov-id sample_docs/drivers_license.pdf \\")
    print(f"    --utility-bill sample_docs/utility_bill.pdf \\")
    print(f"    --bank-statement sample_docs/bank_statement.pdf")


def _encode_doc(file_path: str, doc_type: str) -> dict:
    """Read a file from disk and return a KYCDocument-shaped dict."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return {
        "file_b64": base64.b64encode(path.read_bytes()).decode(),
        "file_name": path.name,
        "doc_type": doc_type,
    }


def cmd_invoke(args: argparse.Namespace) -> None:
    """Invoke the deployed workflow with KYC documents."""
    meta = _load_metadata()
    deployer = AgentCoreDeployer(
        session=boto3.Session(region_name=meta.region, profile_name=args.profile),
        deployment_role="",  # not needed for invoke
        execution_role="",
    )

    payload = {
        "action": "run_nowait",
        "start_event": {
            "documents": [
                _encode_doc(args.gov_id, "government_id"),
                _encode_doc(args.utility_bill, "utility_bill"),
                _encode_doc(args.bank_statement, "bank_statement"),
            ]
        },
    }
    if args.workflow:
        payload["workflow"] = args.workflow

    result = deployer.invoke(meta.arn, payload, session_id=args.session_id)
    session_id = result.get("session_id")
    handler_id = result.get("handler_id")

    if result.get("status") not in ("running", "completed"):
        print(json.dumps(result, indent=2))
        return

    # Poll until the workflow reaches a terminal state.
    poll_interval = args.poll_interval
    while result.get("status") == "running":
        print(f"  Status: running (polling every {poll_interval}s) ...")
        time.sleep(poll_interval)
        result = deployer.invoke(
            meta.arn,
            {"action": "get_result", "handler_id": handler_id},
            session_id=session_id,
        )

    print(json.dumps(result, indent=2))
    if session_id:
        print(f"\nSession ID: {session_id}")
        print("  Re-use with: --session-id", session_id)


def cmd_destroy(args: argparse.Namespace) -> None:
    """Tear down the deployment and clean up AWS resources."""
    meta = _load_metadata()
    deployer = AgentCoreDeployer(
        session=boto3.Session(region_name=meta.region, profile_name=args.profile),
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
    deploy_p.add_argument("--profile", default=None, help="AWS CLI profile name (e.g. 'dev')")

    invoke_p = sub.add_parser("invoke", help="Invoke deployed KYC workflow with document files")
    invoke_p.add_argument("--gov-id", required=True, help="Path to Government ID PDF")
    invoke_p.add_argument("--utility-bill", required=True, help="Path to Utility Bill PDF")
    invoke_p.add_argument("--bank-statement", required=True, help="Path to Bank Statement PDF")
    invoke_p.add_argument("--workflow", help="Specific workflow name")
    invoke_p.add_argument("--session-id", default=None, help="Session ID to continue a previous session")
    invoke_p.add_argument("--poll-interval", type=int, default=5, help="Seconds between status polls (default: 5)")
    invoke_p.add_argument("--profile", default=None, help="AWS CLI profile name")

    destroy_p = sub.add_parser("destroy", help="Destroy deployment and clean up")
    destroy_p.add_argument("--profile", default=None, help="AWS CLI profile name")

    args = parser.parse_args()
    {"deploy": cmd_deploy, "invoke": cmd_invoke, "destroy": cmd_destroy}[args.command](args)


if __name__ == "__main__":
    main()
