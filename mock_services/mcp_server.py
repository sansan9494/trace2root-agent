from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

DATA_DIR = Path(__file__).resolve().parent / "data"
mcp = FastMCP(
    "oncall-mock-ops",
    instructions="只读的本地 OnCall 演示工具，通过 MCP stdio 暴露告警、指标和脱敏日志。",
    log_level="ERROR",
)


def read_json(filename: str) -> Any:
    return json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))


def redact(text: str) -> str:
    text = re.sub(r"(?i)(token|api[_-]?key|password)=\S+", r"\1=***", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "***@***", text)
    return re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "1**********", text)


@mcp.tool(description="查询 Prometheus/Alertmanager Mock 活跃告警；只读。")
def query_prometheus_alarm(
    service_name: str,
    alert_name: str | None = None,
    environment: str = "production",
    minutes: int = 60,
) -> dict[str, Any]:
    if not 1 <= minutes <= 1440:
        raise ValueError("minutes 必须在 1 到 1440 之间")
    items = [item for item in read_json("alerts.json") if item["service_name"] == service_name and item["environment"] == environment]
    if alert_name:
        items = [item for item in items if item["alert_name"].lower() == alert_name.lower()]
    return {"alerts": items, "query_window_minutes": minutes, "adapter": "mcp_mock", "summary": f"通过 MCP 发现 {len(items)} 条活跃告警"}


@mcp.tool(description="查询受控的 Mock Prometheus 指标集合；只读。")
def query_metrics(
    service_name: str,
    metric_names: list[str] | None = None,
    environment: str = "production",
    minutes: int = 30,
    max_points: int = 100,
) -> dict[str, Any]:
    if not 1 <= minutes <= 1440 or not 1 <= max_points <= 1000:
        raise ValueError("查询范围或返回点数超限")
    service_data = read_json("metrics.json").get(service_name, {})
    names = metric_names or list(service_data)
    if len(names) > 10:
        raise ValueError("metric_names 最多 10 项")
    series = {name: service_data[name][:max_points] for name in names if name in service_data}
    latest = {name: values[-1]["value"] for name, values in series.items() if values}
    return {"series": series, "latest": latest, "environment": environment, "adapter": "mcp_mock", "summary": f"通过 MCP 返回 {len(series)} 个指标序列：{latest}"}


@mcp.tool(description="查询并脱敏 Mock 应用日志；只读。")
def query_logs(
    service_name: str,
    environment: str = "production",
    keyword: str | None = None,
    trace_id: str | None = None,
    minutes: int = 30,
    limit: int = 50,
) -> dict[str, Any]:
    if not 1 <= minutes <= 1440 or not 1 <= limit <= 200:
        raise ValueError("查询范围或返回条数超限")
    items = [item for item in read_json("logs.json") if item["service_name"] == service_name and item["environment"] == environment]
    if keyword:
        items = [item for item in items if keyword.lower() in item["message"].lower()]
    if trace_id:
        items = [item for item in items if item["trace_id"] == trace_id]
    items = [{**item, "message": redact(item["message"])} for item in items[:limit]]
    clusters: dict[str, int] = {}
    for item in items:
        key = "upstream_timeout" if "upstream timeout" in item["message"].lower() else item["level"].lower()
        clusters[key] = clusters.get(key, 0) + 1
    return {"entries": items, "clusters": clusters, "adapter": "mcp_mock", "summary": f"通过 MCP 命中 {len(items)} 条日志，聚类 {clusters}"}


if __name__ == "__main__":
    mcp.run(transport="stdio")
