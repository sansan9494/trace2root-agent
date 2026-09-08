from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import Settings


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    command: str
    args: list[str]
    cwd: str
    env: dict[str, str]

    def parameters(self) -> StdioServerParameters:
        merged_env = os.environ.copy()
        merged_env.update(self.env)
        return StdioServerParameters(command=self.command, args=self.args, cwd=self.cwd, env=merged_env)


def parse_mcp_servers(settings: Settings) -> list[MCPServerConfig]:
    try:
        values = json.loads(settings.mcp_servers)
    except json.JSONDecodeError as exc:
        raise ValueError(f"MCP_SERVERS 不是有效 JSON: {exc}") from exc
    if not isinstance(values, list):
        raise ValueError("MCP_SERVERS 必须是 JSON 数组")
    project_root = Path(__file__).resolve().parents[2]
    configs: list[MCPServerConfig] = []
    for value in values:
        if not isinstance(value, dict) or not value.get("name") or not value.get("command"):
            raise ValueError("每个 MCP Server 必须声明 name 和 command")
        cwd_value = Path(str(value.get("cwd", project_root)))
        if not cwd_value.is_absolute():
            cwd_value = project_root / cwd_value
        command = str(value["command"])
        if "/" in command and not Path(command).is_absolute():
            # Do not resolve the final executable symlink: resolving `.venv/bin/python`
            # to `/usr/bin/python` would discard the virtual environment prefix.
            command = str(cwd_value.resolve() / command)
        configs.append(MCPServerConfig(
            name=str(value["name"]), command=command,
            args=[str(item) for item in value.get("args", [])],
            cwd=str(cwd_value.resolve()),
            env={str(key): str(item) for key, item in value.get("env", {}).items()},
        ))
    return configs


async def discover_mcp_tools(server: MCPServerConfig) -> list[dict[str, Any]]:
    with open(os.devnull, "w", encoding="utf-8") as errlog:
        async with stdio_client(server.parameters(), errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [{
                    "name": tool.name,
                    "description": tool.description or f"MCP tool from {server.name}",
                    "input_schema": tool.inputSchema,
                    "annotations": tool.annotations.model_dump(mode="json") if tool.annotations else {},
                } for tool in result.tools]


async def call_mcp_tool(
    server: MCPServerConfig, tool_name: str, arguments: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    with open(os.devnull, "w", encoding="utf-8") as errlog:
        async with stdio_client(server.parameters(), errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
    blocks = [block.model_dump(mode="json") for block in result.content]
    if result.isError:
        message = "; ".join(str(block.get("text", "MCP tool error")) for block in blocks)
        raise RuntimeError(message)
    if isinstance(result.structuredContent, dict):
        data = dict(result.structuredContent)
    else:
        data = {"content": blocks}
        for block in blocks:
            if block.get("type") == "text":
                try:
                    parsed = json.loads(block.get("text", ""))
                    if isinstance(parsed, dict):
                        data = parsed
                        break
                except json.JSONDecodeError:
                    continue
    summary = str(data.pop("summary", f"MCP 工具 {tool_name} 调用成功"))
    data["_mcp_server"] = server.name
    return data, summary


def mcp_status(settings: Settings, registry: Any | None = None) -> dict[str, Any]:
    try:
        servers = parse_mcp_servers(settings)
    except ValueError as exc:
        return {"enabled": False, "configured_servers": [], "status": "invalid_configuration", "error": str(exc), "degraded_safe": True}
    report = getattr(registry, "mcp_report", {}) if registry is not None else {}
    connected = report.get("connected_servers", [])
    return {
        "enabled": bool(servers),
        "configured_servers": [item.name for item in servers],
        "connected_servers": connected,
        "registered_tools": report.get("registered_tools", []),
        "errors": report.get("errors", {}),
        "status": "connected" if servers and len(connected) == len(servers) else ("degraded" if servers else "disabled"),
        "degraded_safe": True,
    }
