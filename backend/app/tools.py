from __future__ import annotations

import asyncio
import json
import re
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generic, TypeVar
from zoneinfo import ZoneInfo

from jsonschema import validate as validate_json_schema
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .agents.knowledge import KnowledgeAgent
from .config import Settings
from .db import ToolExecution
from .schemas import ToolResult

InputT = TypeVar("InputT", bound=BaseModel)


class InternalDocsInput(BaseModel):
    query: str
    service_name: str | None = None
    environment: str | None = "production"
    document_type: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class AlarmInput(BaseModel):
    service_name: str
    alert_name: str | None = None
    environment: str = "production"
    minutes: int = Field(default=60, ge=1, le=1440)


class MetricsInput(BaseModel):
    service_name: str
    metric_names: list[str] = Field(default_factory=list, max_length=10)
    environment: str = "production"
    minutes: int = Field(default=30, ge=1, le=1440)
    max_points: int = Field(default=100, ge=1, le=1000)


class LogsInput(BaseModel):
    service_name: str
    environment: str = "production"
    keyword: str | None = None
    trace_id: str | None = None
    minutes: int = Field(default=30, ge=1, le=1440)
    limit: int = Field(default=50, ge=1, le=200)


class TimeInput(BaseModel):
    timezone: str = "Asia/Shanghai"


class SendMessageInput(BaseModel):
    channel: str = "mock"
    target: str
    title: str
    body: str
    task_id: str


class RestartServiceInput(BaseModel):
    service_name: str
    environment: str
    reason: str


class BaseTool(ABC, Generic[InputT]):
    name: str
    description: str
    input_model: type[InputT]
    risk_level: str = "L1"
    read_only: bool = True
    permissions: tuple[str, ...] = ("ops:read",)
    retries: int = 0
    source: str = "local"

    @abstractmethod
    async def run(self, args: InputT) -> tuple[dict[str, Any], str]: ...

    def validate_arguments(self, raw_arguments: dict[str, Any]) -> InputT:
        return self.input_model.model_validate(raw_arguments)


class MCPArguments(BaseModel):
    model_config = ConfigDict(extra="allow")


class MCPToolAdapter(BaseTool[BaseModel]):
    source = "mcp"

    def __init__(self, *, server, definition: dict[str, Any], fallback: BaseTool | None = None):
        self.server = server
        self.name = definition["name"]
        self.description = definition["description"]
        self.input_schema = definition.get("input_schema", {"type": "object"})
        self.fallback = fallback
        self.input_model = fallback.input_model if fallback else MCPArguments
        annotations = definition.get("annotations", {})
        self.read_only = bool(annotations.get("readOnlyHint", fallback.read_only if fallback else True))
        self.risk_level = fallback.risk_level if fallback else ("L1" if self.read_only else "L2")
        self.permissions = fallback.permissions if fallback else (("ops:read",) if self.read_only else ("ops:write",))
        self.retries = fallback.retries if fallback else 0

    def validate_arguments(self, raw_arguments: dict[str, Any]) -> BaseModel:
        validate_json_schema(instance=raw_arguments, schema=self.input_schema)
        return self.input_model.model_validate(raw_arguments)

    async def run(self, args: BaseModel) -> tuple[dict[str, Any], str]:
        from .mcp import call_mcp_tool

        try:
            return await call_mcp_tool(self.server, self.name, args.model_dump(mode="json"))
        except Exception:
            if self.fallback is None:
                raise
            data, summary = await self.fallback.run(args)
            data["_tool_source"] = "local_fallback"
            return data, f"MCP 不可用，已降级本地工具：{summary}"


class InternalDocsTool(BaseTool[InternalDocsInput]):
    name = "query_internal_docs"
    description = "检索企业内部运维知识库"
    input_model = InternalDocsInput

    def __init__(self, knowledge: KnowledgeAgent):
        self.knowledge = knowledge

    async def run(self, args: InternalDocsInput):
        data = await self.knowledge.run(
            args.query,
            args.service_name,
            args.environment,
            args.top_k,
            args.document_type,
        )
        hits = data["hits"]
        flagged = sum(bool(hit.get("security_flags")) for hit in hits)
        summary = f"Knowledge Agent 命中 {len(hits)} 个片段"
        if flagged:
            summary += f"，隔离 {flagged} 个疑似提示注入片段"
        data["security"] = {"flagged_chunks": flagged}
        return data, summary


class MockDataTool(BaseTool[InputT]):
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def read_json(self, name: str) -> Any:
        return json.loads((self.data_dir / name).read_text(encoding="utf-8"))


class AlarmTool(MockDataTool[AlarmInput]):
    name = "query_prometheus_alarm"
    description = "查询 Prometheus/Alertmanager 活跃告警（Mock 适配器）"
    input_model = AlarmInput

    async def run(self, args: AlarmInput):
        items = [x for x in self.read_json("alerts.json") if x["service_name"] == args.service_name and x["environment"] == args.environment]
        if args.alert_name:
            items = [x for x in items if x["alert_name"].lower() == args.alert_name.lower()]
        return {"alerts": items, "query_window_minutes": args.minutes, "adapter": "mock"}, f"发现 {len(items)} 条活跃告警"


class MetricsTool(MockDataTool[MetricsInput]):
    name = "query_metrics"
    description = "查询受控指标集合（Mock Prometheus 适配器）"
    input_model = MetricsInput

    async def run(self, args: MetricsInput):
        service_data = self.read_json("metrics.json").get(args.service_name, {})
        names = args.metric_names or list(service_data)
        series = {name: service_data[name][: args.max_points] for name in names if name in service_data}
        latest = {name: values[-1]["value"] for name, values in series.items() if values}
        return {"series": series, "latest": latest, "adapter": "mock"}, f"返回 {len(series)} 个指标序列：{latest}"


def redact(text: str) -> str:
    text = re.sub(r"(?i)(token|api[_-]?key|password)=\S+", r"\1=***", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "***@***", text)
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "1**********", text)
    return text


class LogsTool(MockDataTool[LogsInput]):
    name = "query_logs"
    description = "查询并脱敏应用日志（Mock 日志平台适配器）"
    input_model = LogsInput

    async def run(self, args: LogsInput):
        items = [x for x in self.read_json("logs.json") if x["service_name"] == args.service_name and x["environment"] == args.environment]
        if args.keyword:
            items = [x for x in items if args.keyword.lower() in x["message"].lower()]
        if args.trace_id:
            items = [x for x in items if x["trace_id"] == args.trace_id]
        items = [{**x, "message": redact(x["message"])} for x in items[: args.limit]]
        clusters: dict[str, int] = {}
        for item in items:
            key = "upstream_timeout" if "upstream timeout" in item["message"].lower() else item["level"].lower()
            clusters[key] = clusters.get(key, 0) + 1
        return {"entries": items, "clusters": clusters, "adapter": "mock"}, f"命中 {len(items)} 条日志，聚类 {clusters}"


class TimeTool(BaseTool[TimeInput]):
    name = "get_current_time"
    description = "查询指定时区当前时间"
    input_model = TimeInput
    risk_level = "L0"

    async def run(self, args: TimeInput):
        now = datetime.now(ZoneInfo(args.timezone))
        return {"timezone": args.timezone, "iso_time": now.isoformat()}, f"当前时间 {now.isoformat()}"


class SendMessageTool(BaseTool[SendMessageInput]):
    name = "send_msg"
    description = "发送通知（首版写入 Mock 渠道）"
    input_model = SendMessageInput
    risk_level = "L2"
    read_only = False
    permissions = ("ops:notify",)

    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(self, args: SendMessageInput):
        if args.channel != "mock" or args.target not in self.settings.mock_notification_allowlist.split(","):
            raise PermissionError("通知目标不在 Mock allowlist 中")
        message_id = f"mock-{uuid.uuid4()}"
        return {"message_id": message_id, "status": "sent", "adapter": "mock"}, f"Mock 消息已发送：{message_id}"


class RestartServiceTool(BaseTool[RestartServiceInput]):
    name = "restart_service"
    description = "重启服务（演示审批门；首版永不执行真实操作）"
    input_model = RestartServiceInput
    risk_level = "L3"
    read_only = False
    permissions = ("ops:write",)

    async def run(self, args: RestartServiceInput):
        return {"executed": False}, "安全演示模式不执行真实重启"


class ApprovalRequired(Exception):
    def __init__(self, tool: BaseTool, arguments: dict[str, Any]):
        self.tool = tool
        self.arguments = arguments
        super().__init__(f"工具 {tool.name} ({tool.risk_level}) 需要人工审批")


class ToolRegistry:
    def __init__(self, settings: Settings, knowledge: KnowledgeAgent):
        data_dir = Path(__file__).resolve().parents[2] / "mock_services" / "data"
        instances: list[BaseTool] = [
            InternalDocsTool(knowledge), AlarmTool(data_dir), MetricsTool(data_dir), LogsTool(data_dir),
            TimeTool(), SendMessageTool(settings), RestartServiceTool(),
        ]
        self.tools = {tool.name: tool for tool in instances}
        self.local_tools = dict(self.tools)
        self.settings = settings
        self.mcp_report: dict[str, Any] = {"connected_servers": [], "registered_tools": [], "errors": {}}

    async def load_mcp_tools(self) -> dict[str, Any]:
        from .mcp import discover_mcp_tools, parse_mcp_servers

        self.tools = dict(self.local_tools)
        report: dict[str, Any] = {"connected_servers": [], "registered_tools": [], "errors": {}}
        try:
            servers = parse_mcp_servers(self.settings)
        except ValueError as exc:
            report["errors"]["configuration"] = str(exc)
            self.mcp_report = report
            return report
        for server in servers:
            try:
                definitions = await asyncio.wait_for(discover_mcp_tools(server), timeout=self.settings.tool_timeout_seconds)
                report["connected_servers"].append(server.name)
                for definition in definitions:
                    fallback = self.local_tools.get(definition["name"])
                    adapter = MCPToolAdapter(server=server, definition=definition, fallback=fallback)
                    self.tools[adapter.name] = adapter
                    report["registered_tools"].append({"name": adapter.name, "server": server.name, "overrides_local": fallback is not None})
            except Exception as exc:
                report["errors"][server.name] = str(exc)[:500]
        self.mcp_report = report
        return report

    def describe(self) -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description, "input_schema": t.input_model.model_json_schema(), "risk_level": t.risk_level, "read_only": t.read_only, "source": t.source} for t in self.tools.values()]

    async def execute(
        self,
        db: AsyncSession,
        name: str,
        raw_arguments: dict[str, Any],
        *,
        task_id: str | None = None,
        session_id: str | None = None,
        approved: bool = False,
    ) -> ToolResult:
        if name not in self.tools:
            raise ValueError(f"未注册工具: {name}")
        tool = self.tools[name]
        if tool.risk_level in {"L3", "L4"} and not approved:
            raise ApprovalRequired(tool, raw_arguments)
        args = tool.validate_arguments(raw_arguments)
        started = datetime.now(UTC)
        trace_id = str(uuid.uuid4())
        error: Exception | None = None
        data: dict[str, Any] = {}
        summary = ""
        for attempt in range(tool.retries + 1):
            try:
                data, summary = await asyncio.wait_for(tool.run(args), timeout=self.settings.tool_timeout_seconds)
                data.setdefault("_tool_source", tool.source)
                error = None
                break
            except Exception as exc:
                error = exc
                if attempt < tool.retries:
                    continue
        finished = datetime.now(UTC)
        result = ToolResult(
            success=error is None, tool_name=name, data=data, summary=summary or f"工具失败: {error}",
            error_code=type(error).__name__.upper() if error else None,
            error_message=str(error)[:500] if error else None, started_at=started, finished_at=finished, trace_id=trace_id,
        )
        db.add(ToolExecution(
            id=trace_id, task_id=task_id, session_id=session_id, tool_name=name,
            arguments=args.model_dump(mode="json"), result=result.model_dump(mode="json"), success=result.success,
            risk_level=tool.risk_level, started_at=started, finished_at=finished,
        ))
        await db.commit()
        return result
