"""CLI entrypoints for `hermes a2a ...`."""

from __future__ import annotations

import json
from argparse import Namespace

from .config import load_config
from .server import create_server
from .tools import (
    _service,
    get_status_payload,
    tool_a2a_cancel_task,
    tool_a2a_get_task,
    tool_a2a_list_agents,
)


def handle_cli(args: Namespace) -> None:
    """Dispatch `hermes a2a ...` commands."""
    command = getattr(args, "a2a_command", None)
    if command == "status":
        print(json.dumps(get_status_payload(), indent=2, sort_keys=True))
        return

    if command == "card":
        service = _service()
        try:
            print(json.dumps(service.agent_card(), indent=2, sort_keys=True))
        finally:
            service.close()
        return

    if command == "serve":
        config = load_config()
        if getattr(args, "host", None):
            config.host = args.host
        if getattr(args, "port", None):
            config.port = args.port
        server = create_server(config=config)
        try:
            print(
                json.dumps(
                    {
                        "status": "serving",
                        "base_url": server.base_url,
                        "rpc_url": server.service.config.rpc_url,
                        "card_url": server.service.config.card_url,
                    },
                    sort_keys=True,
                )
            )
            server.serve_forever()
        except KeyboardInterrupt:  # pragma: no cover - interactive path
            server.stop()
        return

    if command == "agents" and getattr(args, "agents_command", None) == "list":
        print(json.dumps(json.loads(tool_a2a_list_agents({})), indent=2, sort_keys=True))
        return

    if command == "task" and getattr(args, "task_command", None) == "get":
        print(
            json.dumps(
                json.loads(tool_a2a_get_task({"task_id": args.task_id})),
                indent=2,
                sort_keys=True,
            )
        )
        return

    if command == "task" and getattr(args, "task_command", None) == "cancel":
        print(
            json.dumps(
                json.loads(tool_a2a_cancel_task({"task_id": args.task_id})),
                indent=2,
                sort_keys=True,
            )
        )
        return

    print("Usage: hermes a2a {status|card|serve|agents list|task get|task cancel}")


def setup_argparse(subparser) -> None:
    """Register CLI subcommands under `hermes a2a`."""
    subs = subparser.add_subparsers(dest="a2a_command")
    subs.add_parser("status", help="Show plugin bridge status")
    subs.add_parser("card", help="Render the published agent card")

    serve = subs.add_parser("serve", help="Start the local A2A JSON-RPC + SSE server")
    serve.add_argument("--host", default="", help="Override the bind host")
    serve.add_argument("--port", default=0, type=int, help="Override the bind port")

    agents = subs.add_parser("agents", help="Inspect configured remote agents")
    agents_subs = agents.add_subparsers(dest="agents_command")
    agents_subs.add_parser("list", help="List configured remote agents")

    task = subs.add_parser("task", help="Inspect or control a task")
    task_subs = task.add_subparsers(dest="task_command")
    task_get = task_subs.add_parser("get", help="Get a task snapshot")
    task_get.add_argument("task_id")
    task_cancel = task_subs.add_parser("cancel", help="Cancel a task")
    task_cancel.add_argument("task_id")

    subparser.set_defaults(func=handle_cli)
