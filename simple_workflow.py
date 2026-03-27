"""Simple LlamaIndex Workflow for AgentCore deployment.

This is the user's application code — the workflow that gets containerized
and deployed to AWS Bedrock AgentCore Runtime.
"""

from __future__ import annotations

from workflows import Context, Workflow, step
from workflows.events import Event, StartEvent, StopEvent


class ProcessEvent(Event):
    """Intermediate event carrying processed data between steps."""

    result: str


class SimpleWorkflow(Workflow):
    """A simple workflow that echoes back the input with a greeting.

    Steps:
        StartEvent → start() → ProcessEvent → finish() → StopEvent
    """

    @step
    async def start(self, ev: StartEvent, ctx: Context) -> ProcessEvent:
        """Process the input and create a greeting."""
        user_input = str(ev.get("input", "world"))
        await ctx.store.set("original_input", user_input)
        greeting = f"Hello, {user_input}!"
        return ProcessEvent(result=greeting)

    @step
    async def finish(self, ev: ProcessEvent, ctx: Context) -> StopEvent:
        """Return the final result."""
        original = await ctx.store.get("original_input")
        return StopEvent(
            result={
                "greeting": ev.result,
                "original_input": original,
                "message": "Workflow completed successfully!",
            }
        )


# App bundle for deployment — the entrypoint loads workflows from here.
class WorkflowApp:
    """Container for workflows exposed by this project."""

    workflows = {"simple": SimpleWorkflow()}


app = WorkflowApp()
