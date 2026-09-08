from __future__ import annotations

import pytest

from backend.app.config import get_settings
from backend.app.mcp import call_mcp_tool, discover_mcp_tools, parse_mcp_servers


@pytest.mark.asyncio
async def test_stdio_mcp_discovery_and_call():
    servers = parse_mcp_servers(get_settings())
    assert [server.name for server in servers] == ["mock-ops"]
    definitions = await discover_mcp_tools(servers[0])
    assert {item["name"] for item in definitions} == {
        "query_prometheus_alarm", "query_metrics", "query_logs"
    }
    data, summary = await call_mcp_tool(
        servers[0], "query_logs",
        {"service_name": "payment-service", "environment": "production", "keyword": "upstream timeout"},
    )
    assert data["adapter"] == "mcp_mock"
    assert data["_mcp_server"] == "mock-ops"
    assert data["clusters"]["upstream_timeout"] == 2
    assert "通过 MCP" in summary
    assert all("secret-demo-token" not in item["message"] for item in data["entries"])
