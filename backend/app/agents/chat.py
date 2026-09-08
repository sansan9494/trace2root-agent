from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db import ChatMessage, ChatSession, SessionMemory
from ..llm import ChatModel
from ..schemas import ChatRequest, ChatResponse, ToolCallView
from ..tools import ToolRegistry


class ChatState(TypedDict, total=False):
    db: AsyncSession
    request: ChatRequest
    session_id: str
    history: list[dict[str, str]]
    pending: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    iterations: int
    answer: str
    message_id: str
    finish_reason: str


def infer_service(message: str, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    mapping = {"支付": "payment-service", "订单": "order-service", "用户": "user-service"}
    return next((service for word, service in mapping.items() if word in message), None)


class ConversationAgent:
    """LangGraph ReAct-style observe/act loop with server-side memory."""

    def __init__(self, settings: Settings, registry: ToolRegistry, model: ChatModel):
        self.settings, self.registry, self.model = settings, registry, model
        graph = StateGraph(ChatState)
        graph.add_node("load_memory", self._load_memory)
        graph.add_node("decide", self._decide)
        graph.add_node("act", self._act)
        graph.add_node("answer", self._answer)
        graph.add_edge(START, "load_memory")
        graph.add_edge("load_memory", "decide")
        graph.add_conditional_edges("decide", self._route, {"act": "act", "answer": "answer"})
        graph.add_edge("act", "decide")
        graph.add_edge("answer", END)
        self.graph = graph.compile()

    async def _load_memory(self, state: ChatState) -> dict[str, Any]:
        db, request = state["db"], state["request"]
        session_id = request.session_id or str(uuid.uuid4())
        session = await db.get(ChatSession, session_id)
        if session is None:
            session = ChatSession(id=session_id, service_name=request.service_name, environment=request.environment)
            db.add(session)
        session.service_name = session.service_name or infer_service(request.message, request.service_name)
        session.environment = session.environment or request.environment
        session.updated_at = datetime.now(UTC)
        memory = await db.get(SessionMemory, session_id)
        if memory is None:
            memory = SessionMemory(session_id=session_id)
            db.add(memory)
        db.add(ChatMessage(id=str(uuid.uuid4()), session_id=session_id, role="user", content=request.message))
        await db.commit()
        rows = list((await db.scalars(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at))).all())
        recent_count = self.settings.memory_recent_messages
        if len(rows) > self.settings.memory_summary_threshold:
            early = rows[:-recent_count]
            unsummarized = early[memory.summarized_message_count:]
            if unsummarized:
                transcript = "\n".join(f"{m.role}: {m.content[:500]}" for m in unsummarized)
                fallback = "；".join(f"{m.role}:{m.content[:120]}" for m in early[-8:])
                summary = await self.model.generate(
                    "你是会话记忆压缩器。保留服务、环境、症状、已验证事实、结论和未解决问题；不要补充原文没有的信息；200字以内。",
                    f"已有摘要：{memory.summary}\n新增对话：\n{transcript}",
                    fallback,
                )
                memory.summary = summary
                memory.summarized_message_count = len(early)
                memory.key_facts = self._extract_key_facts(early)
            session.summary = (
                f"服务={session.service_name or infer_service(request.message, None) or '未指定'}；"
                f"环境={session.environment or request.environment}；历史摘要={memory.summary}"
            )
            await db.commit()
        recalled = self._recall_messages(request.message, rows[:-recent_count])
        system_memory = session.summary
        if recalled:
            system_memory += "\n与当前问题相关的历史片段：\n" + "\n".join(recalled)
        history = ([{"role": "system", "content": system_memory}] if system_memory else []) + [{"role": m.role, "content": m.content} for m in rows[-recent_count:]]
        return {"session_id": session_id, "history": history, "pending": [], "tool_results": [], "iterations": 0}

    @staticmethod
    def _terms(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9_.-]+|[\u4e00-\u9fff]{2,}", text.lower()))

    def _recall_messages(self, query: str, rows: list[ChatMessage]) -> list[str]:
        query_terms = self._terms(query)
        scored = []
        for message in rows:
            overlap = len(query_terms & self._terms(message.content))
            if overlap:
                scored.append((overlap, message.created_at, f"{message.role}: {message.content[:300]}"))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored[:self.settings.memory_recall_messages]]

    @staticmethod
    def _extract_key_facts(rows: list[ChatMessage]) -> list[str]:
        markers = ("服务", "环境", "告警", "根因", "错误", "超时", "结论")
        facts = [m.content[:200] for m in rows if any(marker in m.content for marker in markers)]
        return facts[-8:]

    def _initial_actions(self, request: ChatRequest) -> list[dict[str, Any]]:
        if not request.enable_tools:
            return []
        text = request.message
        service = infer_service(text, request.service_name)
        env = request.environment
        actions: list[dict[str, Any]] = []
        if any(word in text for word in ("怎么", "如何", "排查", "手册", "为什么")):
            actions.append({"name": "query_internal_docs", "arguments": {"query": text, "service_name": service, "environment": env, "top_k": 5}})
        if "告警" in text and service:
            actions.append({"name": "query_prometheus_alarm", "arguments": {"service_name": service, "environment": env}})
        if any(word in text.lower() for word in ("指标", "延迟", "错误率")) and service:
            actions.append({"name": "query_metrics", "arguments": {"service_name": service, "environment": env}})
        if "日志" in text and service:
            actions.append({"name": "query_logs", "arguments": {"service_name": service, "environment": env}})
        if any(word in text for word in ("几点", "时间", "现在")) and not service:
            actions.append({"name": "get_current_time", "arguments": {"timezone": "Asia/Shanghai"}})
        return actions

    async def _decide(self, state: ChatState) -> dict[str, Any]:
        iterations = state.get("iterations", 0)
        pending = state.get("pending", [])
        if iterations == 0 and not pending:
            pending = self._initial_actions(state["request"])
        if iterations >= self.settings.chat_max_iterations:
            return {"pending": [], "finish_reason": "max_iterations"}
        return {"pending": pending}

    def _route(self, state: ChatState) -> str:
        return "act" if state.get("pending") else "answer"

    async def _act(self, state: ChatState) -> dict[str, Any]:
        call, *remaining = state["pending"]
        result = await self.registry.execute(state["db"], call["name"], call["arguments"], session_id=state["session_id"])
        item = {"name": call["name"], "arguments": call["arguments"], "result": result.model_dump(mode="json")}
        state["db"].add(ChatMessage(id=str(uuid.uuid4()), session_id=state["session_id"], role="tool", content=result.summary, tool_name=call["name"], metadata_json=item))
        await state["db"].commit()
        return {"pending": remaining, "tool_results": state.get("tool_results", []) + [item], "iterations": state.get("iterations", 0) + 1}

    async def _answer(self, state: ChatState) -> dict[str, Any]:
        results = state.get("tool_results", [])
        sources: list[str] = []
        evidence: list[str] = []
        for item in results:
            result = item["result"]
            evidence.append(f"{item['name']}: {result['summary']}")
            for hit in result.get("data", {}).get("hits", []):
                sources.append(f"{hit['title']} [{hit['chunk_id']}]: {hit['content'][:260]}")
        if not results:
            fallback = "这是 Trace2Root 迹因智能体。请指定服务、环境或希望查询的告警、指标、日志或知识手册。"
        elif all(not item["result"]["success"] for item in results):
            fallback = "查询工具均未成功，无法形成可靠结论。请检查依赖状态后重试。"
        elif any(item["name"] == "query_internal_docs" and not item["result"]["data"].get("hits") for item in results):
            fallback = "当前知识库未提供足够信息。已完成的实时查询：" + "；".join(evidence)
        else:
            fallback = "已基于实际工具结果完成查询。\n\n" + "\n".join(f"- {line}" for line in evidence)
            if sources:
                fallback += "\n\n关键知识依据：\n" + "\n".join(f"- {line}" for line in sources[:3])
        system = (Path(__file__).parents[1] / "prompts" / "system_v1.txt").read_text(encoding="utf-8")
        prompt = f"用户问题：{state['request'].message}\n公开证据：{json.dumps(results, ensure_ascii=False, default=str)}"
        answer = await self.model.generate(system, prompt, fallback)
        message_id = str(uuid.uuid4())
        state["db"].add(ChatMessage(id=message_id, session_id=state["session_id"], role="assistant", content=answer))
        await state["db"].commit()
        return {"answer": answer, "finish_reason": state.get("finish_reason", "completed"), "message_id": message_id}

    async def run(self, db: AsyncSession, request: ChatRequest, request_id: str) -> ChatResponse:
        state = await self.graph.ainvoke({"db": db, "request": request})
        views = []
        sources = []
        for item in state.get("tool_results", []):
            result = item["result"]
            started, finished = datetime.fromisoformat(result["started_at"]), datetime.fromisoformat(result["finished_at"])
            views.append(ToolCallView(tool_name=item["name"], arguments=item["arguments"], success=result["success"], summary=result["summary"], duration_ms=int((finished-started).total_seconds()*1000)))
            sources.extend(result.get("data", {}).get("hits", []))
        return ChatResponse(request_id=request_id, session_id=state["session_id"], message_id=state["message_id"], answer=state["answer"], sources=sources, tool_calls=views, finish_reason=state.get("finish_reason", "completed"))
