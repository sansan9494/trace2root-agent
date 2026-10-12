from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_health_ready_and_upload_rag(client):
    assert (await client.get("/api/v1/health")).status_code == 200
    home = await client.get("/")
    assert home.status_code == 200 and "评测看板" in home.text and "知识库管理" in home.text
    evaluation = await client.get("/api/v1/evaluations/latest")
    assert evaluation.status_code == 200 and {"rag", "agent", "ragas", "public_rag", "public_rca", "robustness"} <= set(evaluation.json())
    ready = (await client.get("/api/v1/ready")).json()
    assert ready["checks"]["sqlite"] is True
    assert ready["checks"]["mcp"]["status"] == "connected"
    assert {item["name"] for item in ready["checks"]["mcp"]["registered_tools"]} == {
        "query_prometheus_alarm", "query_metrics", "query_logs"
    }
    source = Path("sample_knowledge/payment-service/runbook.md")
    response = await client.post("/api/v1/upload_file", data={"service_name": "payment-service", "environment": "production", "document_type": "alert_runbook"}, files={"file": ("payment_runbook.md", source.read_bytes(), "text/markdown")})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "indexed"
    # Milvus Lite collections are released when an importer process exits; runtime
    # retrieval must explicitly load the collection again.
    from backend.app.main import knowledge
    knowledge.store.client.release_collection(collection_name=knowledge.store.collection)
    chat = await client.post("/api/v1/chat", json={"message": "支付服务高延迟如何排查？", "service_name": "payment-service", "environment": "production", "enable_tools": True})
    body = chat.json()
    assert chat.status_code == 200, chat.text
    assert any(call["tool_name"] == "query_internal_docs" for call in body["tool_calls"])
    assert body["sources"]
    assert body["tool_calls"][0]["summary"].startswith("Knowledge Agent")
    assert body["sources"][0]["knowledge_queries"]


@pytest.mark.asyncio
async def test_sse_has_ordered_incremental_events(client):
    async with client.stream("POST", "/api/v1/chat_stream", json={"message": "现在几点？", "environment": "production", "enable_tools": True}) as response:
        text = "".join([chunk async for chunk in response.aiter_text()])
    assert text.index("event: session") < text.index("event: status") < text.index("event: tool_start") < text.index("event: token") < text.index("event: done")
    assert text.count("event: token") >= 2


@pytest.mark.asyncio
async def test_ops_agent_replans_and_reports(client):
    response = await client.post("/api/v1/ai_ops", json={"trigger_type": "manual", "goal": "分析支付服务高延迟", "alert": {"alert_name": "HighLatency", "service_name": "payment-service", "environment": "production", "severity": "warning", "labels": {}, "annotations": {}}, "notify": True})
    task_id = response.json()["task_id"]
    body = None
    for _ in range(600):
        body = (await client.get(f"/api/v1/ai_ops/{task_id}")).json()
        if body["status"] in {"completed", "failed", "timed_out"}:
            break
        await asyncio.sleep(0.05)
    assert body and body["status"] == "completed", body
    decisions = body["report"]["replan_decisions"]
    assert any(item["action"] == "revise" for item in decisions)
    assert body["report"]["root_causes"][0]["confidence"] == "高"
    assert body["report"]["root_causes"][0]["supporting_evidence"]
    assert body["report"]["analysis_source"] == "deterministic_fallback"
    event_types = [item["type"] for item in body["events"]]
    assert "tool_start" in event_types and "report" in event_types and "notification" in event_types
    mcp_results = [
        item["payload"]["result"] for item in body["events"]
        if item["type"] == "tool_end" and item["payload"]["tool_name"] in {"query_metrics", "query_logs"}
    ]
    assert mcp_results and all(item["data"]["_tool_source"] == "mcp" for item in mcp_results)
    plan_event = next(item for item in body["events"] if item["type"] == "plan_created")
    assert plan_event["payload"]["decision_source"] == "deterministic_fallback"
    replan_events = [item for item in body["events"] if item["type"] == "replan"]
    assert replan_events and all(item["payload"]["decision_source"] == "deterministic_fallback" for item in replan_events)


@pytest.mark.asyncio
async def test_alert_webhook_deduplicates(client):
    payload = {"alerts": [{"status": "firing", "fingerprint": "same-alert", "labels": {"alertname": "HighLatency", "service_name": "payment-service", "environment": "production", "severity": "warning"}, "annotations": {}}]}
    first = (await client.post("/api/v1/webhooks/alertmanager", json=payload)).json()
    second = (await client.post("/api/v1/webhooks/alertmanager", json=payload)).json()
    assert len(first["created_task_ids"]) == 1
    assert second["duplicate_task_ids"] == first["created_task_ids"]


@pytest.mark.asyncio
async def test_high_risk_step_waits_for_approval(client):
    response = await client.post("/api/v1/ai_ops", json={"trigger_type": "manual", "goal": "排查后重启支付服务", "alert": {"alert_name": "HighLatency", "service_name": "payment-service", "environment": "production", "severity": "critical", "labels": {}, "annotations": {}}, "notify": False})
    task_id = response.json()["task_id"]
    body = None
    for _ in range(600):
        body = (await client.get(f"/api/v1/ai_ops/{task_id}")).json()
        if body["status"] in {"waiting_approval", "failed"}:
            break
        await asyncio.sleep(0.05)
    assert body and body["status"] == "waiting_approval", body
    approval_events = [event for event in body["events"] if event["type"] == "approval_required"]
    assert approval_events and approval_events[0]["payload"]["risk_level"] == "L3"


@pytest.mark.asyncio
async def test_replanner_cannot_skip_or_finish_before_explicit_restart_approval(client, monkeypatch):
    from backend.app.main import ops_agent

    async def adversarial_replanner(system, prompt, schema, fallback, *, max_tokens=700):
        del prompt, schema, max_tokens
        if "调查Planner" in system:
            return {**fallback, "_decision_source": "test_planner"}
        return {
            "action": "finish",
            "reason": "模型试图提前结束并跳过审批步骤",
            "added_steps": [],
            "skipped_step_ids": ["harness-1"],
            "_decision_source": "test_replanner",
        }

    monkeypatch.setattr(ops_agent.model, "generate_json", adversarial_replanner)
    response = await client.post("/api/v1/ai_ops", json={
        "trigger_type": "manual",
        "goal": "排查支付服务高延迟，确认根因后重启支付服务",
        "alert": {
            "alert_name": "HighLatency", "service_name": "payment-service",
            "environment": "production", "severity": "critical",
            "labels": {}, "annotations": {},
        },
        "notify": False,
    })
    task_id = response.json()["task_id"]
    body = None
    for _ in range(600):
        body = (await client.get(f"/api/v1/ai_ops/{task_id}")).json()
        if body["status"] in {"waiting_approval", "failed"}:
            break
        await asyncio.sleep(0.05)

    assert body and body["status"] == "waiting_approval", body
    restart_step = next(
        step for step in body["plan"]["steps"]
        if step["tool_name"] == "restart_service"
    )
    assert restart_step["status"] == "pending"
    assert all(
        "harness-1" not in event["payload"]["decision"]["skipped_step_ids"]
        for event in body["events"] if event["type"] == "replan"
    )
    assert any(event["type"] == "approval_required" for event in body["events"])


@pytest.mark.asyncio
async def test_memory_summary_and_no_evidence_refusal(client):
    session_id = None
    for index in range(7):
        response = await client.post("/api/v1/chat", json={"session_id": session_id, "message": f"第 {index} 轮：保持 payment-service production 只读约束", "service_name": "payment-service", "environment": "production", "enable_tools": False})
        assert response.status_code == 200
        session_id = response.json()["session_id"]
    sessions = (await client.get("/api/v1/sessions")).json()["sessions"]
    target = next(item for item in sessions if item["id"] == session_id)
    assert "payment-service" in target["summary"] and "production" in target["summary"]
    detail = (await client.get(f"/api/v1/sessions/{session_id}")).json()
    assert detail["memory"]["summarized_message_count"] > 0
    assert len(detail["messages"]) == 14

    response = await client.post("/api/v1/chat", json={"message": "不存在服务的发布手册是什么？", "service_name": "missing-service", "environment": "production", "enable_tools": True})
    assert "当前知识库未提供足够信息" in response.json()["answer"]
