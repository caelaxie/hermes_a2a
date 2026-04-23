"""Hermes A2A packaged plugin entrypoint."""

from __future__ import annotations

from .cli import handle_cli, setup_argparse
from .schemas import (
    A2A_CANCEL_TASK_SCHEMA,
    A2A_DELEGATE_SCHEMA,
    A2A_GET_TASK_SCHEMA,
    A2A_LIST_AGENTS_SCHEMA,
    A2A_STATUS_SCHEMA,
)
from .tools import (
    tool_a2a_cancel_task,
    tool_a2a_delegate,
    tool_a2a_get_task,
    tool_a2a_list_agents,
    tool_a2a_status,
)


def register(ctx) -> None:
    """Register Hermes tools and CLI commands."""
    ctx.register_tool(
        name="a2a_status",
        toolset="a2a",
        schema=A2A_STATUS_SCHEMA,
        handler=tool_a2a_status,
        description="Return local Hermes A2A bridge status and effective configuration.",
    )
    ctx.register_tool(
        name="a2a_list_agents",
        toolset="a2a",
        schema=A2A_LIST_AGENTS_SCHEMA,
        handler=tool_a2a_list_agents,
        description="List configured remote A2A agents known to Hermes.",
    )
    ctx.register_tool(
        name="a2a_get_task",
        toolset="a2a",
        schema=A2A_GET_TASK_SCHEMA,
        handler=tool_a2a_get_task,
        description="Get a local or remote A2A task snapshot by id.",
    )
    ctx.register_tool(
        name="a2a_cancel_task",
        toolset="a2a",
        schema=A2A_CANCEL_TASK_SCHEMA,
        handler=tool_a2a_cancel_task,
        description="Cancel a local or remote A2A task by id.",
    )
    ctx.register_tool(
        name="a2a_delegate",
        toolset="a2a",
        schema=A2A_DELEGATE_SCHEMA,
        handler=tool_a2a_delegate,
        description="Delegate a user message to a remote A2A agent.",
    )
    ctx.register_cli_command(
        name="a2a",
        help="Operate the Hermes A2A bridge",
        setup_fn=setup_argparse,
        handler_fn=handle_cli,
        description="CLI helpers for serving, inspecting, and delegating through the Hermes A2A bridge.",
    )
