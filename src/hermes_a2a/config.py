"""Configuration helpers for the Hermes A2A plugin."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class RemoteAgentPreset:
    """Configured remote A2A agent target."""

    alias: str
    url: str
    description: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class A2APluginConfig:
    """Resolved plugin configuration."""

    plugin_name: str = "a2a"
    version: str = "0.1.0"
    host: str = "127.0.0.1"
    port: int = 8000
    public_base_url: str = ""
    bearer_token: str = ""
    store_path: str = ""
    exported_skills: list[str] = field(default_factory=list)
    remote_agents: list[RemoteAgentPreset] = field(default_factory=list)
    default_timeout_seconds: float = 10.0
    allow_runtime_write: bool = True

    @property
    def resolved_public_base_url(self) -> str:
        if self.public_base_url:
            return self.public_base_url.rstrip("/")
        return f"http://{self.host}:{self.port}"

    @property
    def rpc_url(self) -> str:
        return f"{self.resolved_public_base_url}/rpc"

    @property
    def card_url(self) -> str:
        return f"{self.resolved_public_base_url}/.well-known/agent-card.json"

    @property
    def resolved_store_path(self) -> str:
        if self.store_path:
            return str(Path(self.store_path).expanduser())
        return str((Path.cwd() / ".a2a" / "state.db").resolve())

    def status_dict(self) -> dict:
        return {
            "plugin": self.plugin_name,
            "version": self.version,
            "config": {
                "host": self.host,
                "port": self.port,
                "public_base_url": self.resolved_public_base_url,
                "rpc_url": self.rpc_url,
                "card_url": self.card_url,
                "store_path": self.resolved_store_path,
                "bearer_token_present": bool(self.bearer_token),
                "exported_skills": list(self.exported_skills),
                "remote_agents": [
                    {
                        "alias": agent.alias,
                        "url": agent.url,
                        "description": agent.description,
                        "headers_present": bool(agent.headers),
                    }
                    for agent in self.remote_agents
                ],
            },
        }


def _parse_exported_skills(raw_value: str) -> list[str]:
    if not raw_value.strip():
        return []
    return [value.strip() for value in raw_value.split(",") if value.strip()]


def _parse_remote_agents(raw_value: str) -> list[RemoteAgentPreset]:
    if not raw_value.strip():
        return []

    decoded = json.loads(raw_value)
    agents: list[RemoteAgentPreset] = []

    if isinstance(decoded, dict):
        iterator = [
            {
                "alias": alias,
                **(details if isinstance(details, dict) else {"url": str(details)}),
            }
            for alias, details in decoded.items()
        ]
    elif isinstance(decoded, list):
        iterator = decoded
    else:
        raise ValueError("A2A_REMOTE_AGENTS_JSON must decode to an object or list")

    for item in iterator:
        if not isinstance(item, dict):
            raise ValueError("Each remote agent preset must be an object")
        alias = str(item.get("alias", "")).strip()
        url = str(item.get("url", "")).strip()
        if not alias or not url:
            raise ValueError("Each remote agent preset needs alias and url")
        headers = item.get("headers") or {}
        if not isinstance(headers, dict):
            raise ValueError("Remote agent headers must be a JSON object")
        agents.append(
            RemoteAgentPreset(
                alias=alias,
                url=url.rstrip("/"),
                description=str(item.get("description", "")).strip(),
                headers={str(key): str(value) for key, value in headers.items()},
            )
        )
    return agents


def load_config() -> A2APluginConfig:
    """Resolve plugin configuration from environment variables."""
    host = os.getenv("A2A_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("A2A_PORT", "8000").strip() or "8000")
    remote_agents = _parse_remote_agents(os.getenv("A2A_REMOTE_AGENTS_JSON", "").strip())
    exported_skills = _parse_exported_skills(os.getenv("A2A_EXPORTED_SKILLS", "").strip())
    return A2APluginConfig(
        host=host,
        port=port,
        public_base_url=os.getenv("A2A_PUBLIC_BASE_URL", "").strip(),
        bearer_token=os.getenv("A2A_BEARER_TOKEN", "").strip(),
        store_path=os.getenv("A2A_STORE_PATH", "").strip(),
        exported_skills=exported_skills,
        remote_agents=remote_agents,
        default_timeout_seconds=float(
            os.getenv("A2A_DEFAULT_TIMEOUT_SECONDS", "10").strip() or "10"
        ),
        allow_runtime_write=_truthy(os.getenv("A2A_ALLOW_RUNTIME_WRITE", "true")),
    )
