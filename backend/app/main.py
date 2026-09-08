from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .agents.chat import ConversationAgent
from .agents.knowledge import KnowledgeAgent
from .agents.ops import OperationsAgent
from .config import get_settings
from .db import AlertFingerprint, ApprovalRequest, AuditEvent, ChatMessage, ChatSession, Document, DocumentChunk, OpsEvent, OpsTask, SessionLocal, SessionMemory, get_db, init_db, utcnow
from .knowledge import DocumentError, KnowledgeService
from .llm import ChatModel
from .mcp import mcp_status
from .schemas import Alert, ApprovalDecision, ChatRequest, ChatResponse, OpsCreateRequest
from .tools import ToolRegistry

settings = get_settings()
knowledge = KnowledgeService(settings)
model = ChatModel(settings)
knowledge_agent = KnowledgeAgent(settings, knowledge, model)
registry = ToolRegistry(settings, knowledge_agent)
chat_agent = ConversationAgent(settings, registry, model)
ops_agent = OperationsAgent(settings, registry, model)
running_tasks: set[asyncio.Task] = set()


def schedule(coro) -> None:
    task = asyncio.create_task(coro)
    running_tasks.add(task)
    task.add_done_callback(running_tasks.discard)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await asyncio.to_thread(knowledge.warmup)
    await registry.load_mcp_tools()
    yield
    for task in list(running_tasks):
        if not task.done():
            task.cancel()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception as exc:
        response = JSONResponse(status_code=500, content={"request_id": request_id, "error": {"code": "INTERNAL_ERROR", "message": str(exc)[:500], "retryable": False, "details": {}}})
    response.headers["x-request-id"] = request_id
    return response


@app.get("/", response_class=HTMLResponse)
async def home():
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/v1/health")
async def health(request: Request):
    return {"request_id": request.state.request_id, "status": "ok"}


@app.get("/api/v1/ready")
async def ready(request: Request, db: AsyncSession = Depends(get_db)):
    checks: dict = {"sqlite": False, "milvus_lite": False, "llm": False, "embedding": {"provider": settings.embedding_provider, "model": settings.embedding_model, "device": settings.embedding_device, "dimension": settings.embedding_dimension, "loaded": knowledge.embeddings._model is not None, "warmup_error": knowledge.warmup_error}, "reranker": {"provider": settings.reranker_provider, "model": settings.reranker_model, "device": settings.reranker_device, "loaded": knowledge.reranker.loaded, "last_error": knowledge.reranker.last_error}, "retrieval": {"strategy": settings.rag_retrieval_mode, "candidate_top_k": settings.rag_top_k, "final_top_n": settings.rag_top_n, "rrf_k": settings.rag_rrf_k}, "mcp": mcp_status(settings, registry), "tools": registry.describe()}
    try:
        await db.execute(text("SELECT 1")); checks["sqlite"] = True
    except Exception:
        pass
    try:
        await asyncio.to_thread(lambda: knowledge.store.client.has_collection(knowledge.store.collection)); checks["milvus_lite"] = True
    except Exception as exc:
        checks["milvus_error"] = str(exc)[:200]
    checks["llm"] = await model.available()
    checks["llm_mode"] = model.mode
    return {"request_id": request.state.request_id, "status": "ready" if checks["sqlite"] and checks["milvus_lite"] else "degraded", "checks": checks}


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics(db: AsyncSession = Depends(get_db)):
    tasks = await db.scalar(select(func.count()).select_from(OpsTask)) or 0
    docs = await db.scalar(select(func.count()).select_from(Document).where(Document.status == "indexed")) or 0
    return f"# TYPE trace2root_tasks_total gauge\ntrace2root_tasks_total {tasks}\n# TYPE trace2root_documents_indexed gauge\ntrace2root_documents_indexed {docs}\n"


@app.get("/api/v1/evaluations/latest")
async def latest_evaluations(request: Request):
    result_dir = Path("evaluation/results")

    def load(name: str) -> dict | None:
        target = result_dir / name
        if not target.is_file():
            return None
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    return {
        "request_id": request.state.request_id,
        "rag": load("latest.json"),
        "agent": load("latest_agent.json"),
        "ragas": load("latest_ragas.json"),
        "public_rag": load("latest_public_rag.json"),
        "public_rca": load("latest_public_rca.json"),
        "robustness": load("latest_robustness.json"),
    }


@app.post("/api/v1/upload_file")
async def upload_file(
    request: Request, file: UploadFile = File(...), service_name: str = Form(...), environment: str = Form("production"),
    document_type: str = Form("alert_runbook"), department_id: str = Form("ops"), tags: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    del tags
    try:
        document = await knowledge.ingest(db, file.filename or "", await file.read(), service_name, environment, document_type, department_id)
    except DocumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"request_id": request.state.request_id, "document_id": document.id, "status": document.status, "message": "文件已完成索引" if document.status == "indexed" else "文件已存在"}


@app.get("/api/v1/documents")
async def documents(request: Request, db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Document).order_by(Document.created_at.desc()))).all()
    return {"request_id": request.state.request_id, "documents": [{"id": d.id, "filename": d.filename, "service_name": d.service_name, "environment": d.environment, "document_type": d.document_type, "status": d.status, "version": d.version, "chunk_count": d.chunk_count, "error": d.error, "created_at": d.created_at.isoformat()} for d in rows]}


@app.delete("/api/v1/documents/{document_id}")
async def delete_document(document_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    document = await db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "文档不存在")
    await asyncio.to_thread(knowledge.store.delete_document, document_id)
    document.status = "deleted"
    await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    await db.commit()
    return {"request_id": request.state.request_id, "status": "deleted"}


@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request, db: AsyncSession = Depends(get_db)):
    return await chat_agent.run(db, payload, request.state.request_id)


def sse(event: str, data: dict) -> str:
    return f"event: {event}\nid: {data['event_id']}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@app.post("/api/v1/chat_stream")
async def chat_stream(payload: ChatRequest, request: Request):
    request_id = request.state.request_id
    session_id = payload.session_id or str(uuid.uuid4())
    payload.session_id = session_id

    async def generate() -> AsyncIterator[str]:
        counter = 0
        def event(name: str, body: dict) -> str:
            nonlocal counter
            counter += 1
            return sse(name, {"request_id": request_id, "event_id": f"evt-{counter}", "timestamp": datetime.now(UTC).isoformat(), **body})
        yield event("session", {"session_id": session_id})
        yield event("status", {"stage": "agent_running"})
        try:
            async with SessionLocal() as db:
                response = await chat_agent.run(db, payload, request_id)
            for call in response.tool_calls:
                yield event("tool_start", {"tool_name": call.tool_name, "arguments": call.arguments})
                yield event("tool_end", {"tool_name": call.tool_name, "success": call.success, "summary": call.summary, "duration_ms": call.duration_ms})
            for index in range(0, len(response.answer), 16):
                if await request.is_disconnected():
                    return
                yield event("token", {"content": response.answer[index:index+16]})
                await asyncio.sleep(0)
            yield event("sources", {"sources": response.sources})
            yield event("done", {"finish_reason": response.finish_reason})
        except Exception as exc:
            yield event("error", {"message": str(exc)[:500]})
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def create_ops_task(db: AsyncSession, payload: OpsCreateRequest) -> OpsTask:
    task = OpsTask(id=str(uuid.uuid4()), trigger_type=payload.trigger_type, goal=payload.goal, alert=payload.alert.model_dump(mode="json"), notify=payload.notify, status="queued")
    db.add(task)
    await db.commit()
    return task


@app.post("/api/v1/ai_ops")
async def ai_ops(payload: OpsCreateRequest, request: Request, db: AsyncSession = Depends(get_db)):
    task = await create_ops_task(db, payload)
    schedule(ops_agent.run_task(task.id))
    return {"request_id": request.state.request_id, "task_id": task.id, "status": "queued"}


@app.get("/api/v1/ai_ops")
async def list_ops(request: Request, db: AsyncSession = Depends(get_db)):
    tasks = (await db.scalars(select(OpsTask).order_by(OpsTask.created_at.desc()).limit(100))).all()
    return {"request_id": request.state.request_id, "tasks": [{"task_id": t.id, "goal": t.goal, "status": t.status, "alert": t.alert, "created_at": t.created_at.isoformat()} for t in tasks]}


@app.get("/api/v1/ai_ops/{task_id}")
async def get_ops(task_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    task = await db.get(OpsTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    events = (await db.scalars(select(OpsEvent).where(OpsEvent.task_id == task_id).order_by(OpsEvent.id))).all()
    return {"request_id": request.state.request_id, "task_id": task.id, "status": task.status, "goal": task.goal, "alert": task.alert, "plan": task.plan, "events": [{"id": e.id, "type": e.event_type, "payload": e.payload, "created_at": e.created_at.isoformat()} for e in events], "report": task.report, "error": task.error, "created_at": task.created_at.isoformat(), "started_at": task.started_at.isoformat() if task.started_at else None, "finished_at": task.finished_at.isoformat() if task.finished_at else None}


@app.get("/api/v1/ai_ops/{task_id}/events")
async def ops_events(task_id: str, request: Request, after_id: int = 0):
    async def generate():
        cursor = after_id
        for _ in range(120):
            async with SessionLocal() as db:
                rows = (await db.scalars(select(OpsEvent).where(OpsEvent.task_id == task_id, OpsEvent.id > cursor).order_by(OpsEvent.id))).all()
                task = await db.get(OpsTask, task_id)
            for row in rows:
                cursor = row.id
                yield sse(row.event_type, {"request_id": request.state.request_id, "event_id": str(row.id), "timestamp": row.created_at.isoformat(), **row.payload})
            if task and task.status in {"completed", "failed", "cancelled", "timed_out", "waiting_approval"}:
                yield sse("done", {"request_id": request.state.request_id, "event_id": f"done-{cursor}", "timestamp": datetime.now(UTC).isoformat(), "status": task.status})
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.5)
    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/v1/ai_ops/{task_id}/cancel")
async def cancel_ops(task_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    task = await db.get(OpsTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    task.cancel_requested = True
    await db.commit()
    return {"request_id": request.state.request_id, "status": "cancellation_requested"}


@app.post("/api/v1/approvals/{approval_id}")
async def decide_approval(approval_id: str, payload: ApprovalDecision, request: Request, db: AsyncSession = Depends(get_db)):
    approval = await db.get(ApprovalRequest, approval_id)
    if not approval or approval.status != "pending":
        raise HTTPException(404, "待审批请求不存在")
    approval.status = "approved" if payload.approved else "rejected"
    task = await db.get(OpsTask, approval.task_id)
    if task:
        task.status = "completed"
        task.finished_at = utcnow()
        task.report = {"task_id": task.id, "need_human": True, "approval": approval.status, "recommendations": ["审批通过但轻量演示模式仍不执行真实生产重启" if payload.approved else "操作已拒绝；保留只读分析与人工处置建议"], "root_causes": [], "evidence": []}
        db.add(OpsEvent(task_id=task.id, event_type="approval_decided", payload={"approval_id": approval.id, "approved": payload.approved, "reason": payload.reason}))
    db.add(AuditEvent(id=str(uuid.uuid4()), actor="demo-user", action="approval_decision", resource=approval.id, outcome=approval.status, details={"reason": payload.reason}))
    await db.commit()
    return {"request_id": request.state.request_id, "approval_id": approval.id, "status": approval.status}


@app.post("/api/v1/webhooks/alertmanager")
async def alertmanager(payload: dict, request: Request, db: AsyncSession = Depends(get_db)):
    created, duplicates = [], []
    for raw in payload.get("alerts", []):
        labels = raw.get("labels", {})
        alert = Alert(alert_name=labels.get("alertname", "UnknownAlert"), service_name=labels.get("service_name", labels.get("service", "unknown-service")), environment=labels.get("environment", "production"), severity=labels.get("severity", "warning"), labels=labels, annotations=raw.get("annotations", {}), starts_at=raw.get("startsAt") or None)
        key = raw.get("fingerprint") or hashlib.sha256(json.dumps({"alert": alert.alert_name, "service": alert.service_name, "environment": alert.environment, "labels": labels}, sort_keys=True).encode()).hexdigest()
        existing = await db.get(AlertFingerprint, key)
        if existing and existing.active:
            duplicates.append(existing.task_id); continue
        req = OpsCreateRequest(trigger_type="alertmanager", goal=f"分析 {alert.service_name} 的 {alert.alert_name} 告警", alert=alert)
        task = await create_ops_task(db, req)
        db.add(AlertFingerprint(fingerprint=key, task_id=task.id, active=True)); await db.commit()
        schedule(ops_agent.run_task(task.id)); created.append(task.id)
    return {"request_id": request.state.request_id, "created_task_ids": created, "duplicate_task_ids": duplicates}


@app.get("/api/v1/sessions")
async def sessions(request: Request, db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(ChatSession).order_by(ChatSession.updated_at.desc()))).all()
    return {"request_id": request.state.request_id, "sessions": [{"id": x.id, "service_name": x.service_name, "environment": x.environment, "summary": x.summary, "updated_at": x.updated_at.isoformat()} for x in rows]}


@app.get("/api/v1/sessions/{session_id}")
async def session_detail(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    session = await db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    memory = await db.get(SessionMemory, session_id)
    messages = (await db.scalars(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at))).all()
    return {
        "request_id": request.state.request_id,
        "session": {"id": session.id, "service_name": session.service_name, "environment": session.environment, "summary": session.summary},
        "memory": {"summary": memory.summary, "summarized_message_count": memory.summarized_message_count, "key_facts": memory.key_facts, "unresolved_questions": memory.unresolved_questions} if memory else None,
        "messages": [{"role": item.role, "content": item.content, "tool_name": item.tool_name, "created_at": item.created_at.isoformat()} for item in messages],
    }


@app.delete("/api/v1/sessions/{session_id}")
async def delete_session(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    session = await db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    await db.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
    await db.execute(delete(SessionMemory).where(SessionMemory.session_id == session_id))
    await db.delete(session); await db.commit()
    return {"request_id": request.state.request_id, "status": "deleted"}
