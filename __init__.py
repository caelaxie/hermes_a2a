"""Hermes A2A plugin registration."""

from __future__ import annotations

import json

from .schemas import A2A_STATUS_SCHEMA
from .tools import get_status_payload, tool_a2a_status


def _handle_cli(args) -> None:
    """Handle `hermes a2a ...` commands."""
    subcommand = getattr(args, "a2a_command", None)
    if subcommand == "status":
        print(json.dumps(get_status_payload(), indent=2, sort_keys=True))
        return

    print("Usage: hermes a2a status")


def _setup_argparse(subparser) -> None:
    """Register CLI subcommands under `hermes a2a`."""
    subs = subparser.add_subparsers(dest="a2a_command")
    subs.add_parser("status", help="Show plugin scaffold status")
    subparser.set_defaults(func=_handle_cli)


def register(ctx) -> None:
    """Register plugin tools and CLI commands with Hermes."""
    ctx.register_tool(
        name="a2a_status",
        toolset="a2a",
        schema=A2A_STATUS_SCHEMA,
        handler=tool_a2a_status,
        description="Return deployment and configuration status for the Hermes A2A plugin scaffold.",
    )
    ctx.register_cli_command(
        name="a2a",
        help="Inspect the Hermes A2A plugin scaffold",
        setup_fn=_setup_argparse,
        handler_fn=_handle_cli,
        description="CLI helpers for verifying the Hermes A2A plugin scaffold after installation.",
    )
