from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import Settings
from ..db import ApprovalRequest, OpsEvent, OpsTask, SessionLocal, utcnow
from ..llm import ChatModel
from ..schemas import (
    IncidentAnalysis,
    InvestigationPlanDecision,
    OpsPlan,
    PlanStep,
    ReplanAction,
    ReplanDecision,
    ReplanProposal,
    ToolStepProposal,
)
from ..tools import ApprovalRequired, ToolRegistry


class OpsState(TypedDict, total=False):
    db: Any
    task_id: str
    goal: str
    alert: dict[str, Any]
    notify: bool
    plan: dict[str, Any]
    evidence: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    iterations: int
    outcome: str


class OperationsAgent:
    """Bounded LLM-directed Planner/Executor/Replanner operations agent."""

    READ_ONLY_INVESTIGATION_TOOLS = {
        "query_internal_docs", "query_prometheus_alarm", "query_metrics", "query_logs",
    }

    def __init__(self, settings: Settings, registry: ToolRegistry, model: ChatModel):
        self.settings, self.registry, self.model = settings, registry, model
        graph = StateGraph(OpsState)
        graph.add_node("planner", self._planner)
        graph.add_node("executor", self._executor)
        graph.add_node("replanner", self._replanner)
        graph.add_node("reporter", self._reporter)
        graph.add_edge(START, "planner")
        graph.add_edge("planner", "executor")
        graph.add_edge("executor", "replanner")
        graph.add_conditional_edges("replanner", self._route, {"execute": "executor", "report": "reporter", "stop": END})
        graph.add_edge("reporter", END)
        self.graph = graph.compile()

    async def _event(self, state: OpsState, event_type: str, payload: dict[str, Any]) -> None:
        state["db"].add(OpsEvent(task_id=state["task_id"], event_type=event_type, payload=payload))
        await state["db"].commit()

    def _tool_catalog(self) -> list[dict[str, Any]]:
        catalog = []
        for item in self.registry.describe():
            if item["name"] not in self.READ_ONLY_INVESTIGATION_TOOLS | {"restart_service"}:
                continue
            catalog.append({
                "name": item["name"], "description": item["description"],
                "risk_level": item["risk_level"], "read_only": item["read_only"],
                "arguments": sorted(item["input_schema"].get("properties", {})),
            })
        return catalog

    @staticmethod
    def _fallback_plan(alert: dict[str, Any], goal: str) -> dict[str, Any]:
        service, env = alert["service_name"], alert["environment"]
        steps = [
            {"title": "检索匹配的告警处理手册", "tool_name": "query_internal_docs", "arguments": {"query": f"{alert['alert_name']} {service} 排查步骤", "service_name": service, "environment": env}, "success_criteria": "找到与告警、服务和环境相关的处理依据"},
            {"title": "查询当前活跃告警", "tool_name": "query_prometheus_alarm", "arguments": {"service_name": service, "alert_name": alert["alert_name"], "environment": env}, "success_criteria": "确认告警是否活跃及影响范围"},
            {"title": "验证关键指标和依赖信号", "tool_name": "query_metrics", "arguments": {"service_name": service, "environment": env}, "success_criteria": "获得能够验证主要假设的实时指标"},
            {"title": "检查错误日志并寻找首个异常信号", "tool_name": "query_logs", "arguments": {"service_name": service, "environment": env}, "success_criteria": "获得与指标时间对齐的错误日志"},
        ]
        if "重启" in goal:
            steps.append({"title": "申请生产服务重启审批", "tool_name": "restart_service", "arguments": {"service_name": service, "environment": env, "reason": goal}, "success_criteria": "获得人工批准后方可进入安全演示执行"})
        return {"objective": goal, "hypotheses": ["应用自身异常", "资源或容量异常", "上游依赖异常", "近期变更引入回归"], "steps": steps}

    def _step_from_proposal(self, proposal: ToolStepProposal, state: OpsState, step_id: str, depends_on: list[str]) -> PlanStep | None:
        alert, goal, name = state["alert"], state["goal"], proposal.tool_name
        allowed = set(self.READ_ONLY_INVESTIGATION_TOOLS)
        if "重启" in goal:
            allowed.add("restart_service")
        if name not in allowed or name not in self.registry.tools:
            return None
        args = dict(proposal.arguments)
        fields = self.registry.tools[name].input_model.model_fields
        if "service_name" in fields:
            args["service_name"] = alert["service_name"]
        if "environment" in fields:
            args["environment"] = alert["environment"]
        if name == "query_internal_docs":
            args["query"] = str(args.get("query") or f"{alert['alert_name']} 排查步骤")[:500]
            args["top_k"] = min(int(args.get("top_k", 5)), 10)
            if args.get("document_type") not in {
                None, "alert_runbook", "service_manual", "incident_history",
            }:
                args["document_type"] = None
        if name == "query_prometheus_alarm":
            args.setdefault("alert_name", alert["alert_name"])
        if name == "query_metrics":
            # The MVP adapter exposes a controlled metric set. Ignore model-invented
            # metric names so an underpowered local model cannot create false gaps.
            args["metric_names"] = []
        if name == "query_logs" and not step_id.startswith("replan-"):
            # Start broad; Replanner-added follow-up steps may narrow by keyword.
            args["keyword"] = None
            args["trace_id"] = None
        if name == "restart_service":
            args["reason"] = goal
        try:
            validated = self.registry.tools[name].validate_arguments(args)
        except Exception:
            return None
        return PlanStep(step_id=step_id, title=proposal.title, tool_name=name, arguments=validated.model_dump(mode="json"), depends_on=depends_on, success_criteria=proposal.success_criteria)

    def _validated_steps(self, proposals: list[ToolStepProposal], state: OpsState, *, prefix: str, existing: list[PlanStep] | None = None) -> list[PlanStep]:
        existing = existing or []
        signatures = {(step.tool_name, json.dumps(step.arguments, sort_keys=True, ensure_ascii=False)) for step in existing}
        steps: list[PlanStep] = []
        previous = existing[-1].step_id if existing else None
        for index, proposal in enumerate(proposals[:8], start=1):
            step = self._step_from_proposal(proposal, state, f"{prefix}-{index}", [previous] if previous else [])
            if step is None:
                continue
            signature = (step.tool_name, json.dumps(step.arguments, sort_keys=True, ensure_ascii=False))
            if signature in signatures:
                continue
            signatures.add(signature)
            steps.append(step)
            previous = step.step_id
        return steps

    def _ensure_investigation_coverage(
        self, steps: list[PlanStep], state: OpsState, fallback: dict[str, Any]
    ) -> tuple[list[PlanStep], bool]:
        """Guarantee a minimum evidence path when a small model under-plans."""
        required = {"query_internal_docs", "query_metrics", "query_logs"}
        missing = required - {step.tool_name for step in steps}
        if not missing:
            return steps, False
        fallback_decision = InvestigationPlanDecision.model_validate(fallback)
        proposals = [item for item in fallback_decision.steps if item.tool_name in missing]
        additions = self._validated_steps(
            proposals, state, prefix="harness", existing=steps
        )
        return [*steps, *additions], bool(additions)

    async def _planner(self, state: OpsState) -> dict[str, Any]:
        task = await state["db"].get(OpsTask, state["task_id"])
        assert task
        task.status, task.started_at = "planning", utcnow()
        fallback = self._fallback_plan(state["alert"], state["goal"])
        raw = await self.model.generate_json(
            "你是OnCall调查Planner。根据告警、目标和工具目录提出有界的结构化调查计划。优先只读证据；不得虚构工具；不得把告警服务直接当作根因；生产写操作只有在用户目标明确要求时才能列入计划。",
            f"告警：{json.dumps(state['alert'], ensure_ascii=False)}\n目标：{state['goal']}\n工具目录：{json.dumps(self._tool_catalog(), ensure_ascii=False)}",
            InvestigationPlanDecision.model_json_schema(), fallback, max_tokens=900,
        )
        source = raw.pop("_decision_source", "unknown")
        try:
            decision = InvestigationPlanDecision.model_validate(raw)
        except Exception:
            decision = InvestigationPlanDecision.model_validate(fallback)
            source = "schema_validation_fallback"
        steps = self._validated_steps(decision.steps, state, prefix="step")
        if not steps:
            decision = InvestigationPlanDecision.model_validate(fallback)
            steps = self._validated_steps(decision.steps, state, prefix="step")
            source = "harness_fallback"
        else:
            steps, supplemented = self._ensure_investigation_coverage(steps, state, fallback)
            if supplemented:
                source = f"{source}+harness_coverage"
        plan = OpsPlan(task_id=state["task_id"], goal=decision.objective, assumptions=["先执行只读诊断", *decision.hypotheses[:6]], steps=steps, completion_criteria="至少两个独立信号支持同一根因，或明确列出证据冲突与未确认项", max_iterations=self.settings.ops_max_iterations)
        task.plan, task.status = plan.model_dump(mode="json"), "executing"
        await state["db"].commit()
        await self._event(state, "plan_created", {"plan": task.plan, "decision_source": source, "hypotheses": decision.hypotheses})
        return {"plan": task.plan, "evidence": [], "decisions": [], "iterations": 0, "outcome": "continue"}

    async def _executor(self, state: OpsState) -> dict[str, Any]:
        db, plan = state["db"], OpsPlan.model_validate(state["plan"])
        task = await db.get(OpsTask, state["task_id"])
        assert task
        if task.cancel_requested:
            return {"outcome": "cancelled"}
        completed = {step.step_id for step in plan.steps if step.status in {"completed", "failed", "skipped"}}
        step = next((item for item in plan.steps if item.status == "pending" and set(item.depends_on) <= completed), None)
        if step is None:
            return {"outcome": "finish", "plan": plan.model_dump(mode="json")}
        step.status = "running"
        task.status, task.plan = "executing", plan.model_dump(mode="json")
        await db.commit()
        await self._event(state, "tool_start", {"step_id": step.step_id, "tool_name": step.tool_name, "arguments": step.arguments})
        try:
            result = await self.registry.execute(db, step.tool_name, step.arguments, task_id=state["task_id"])
        except ApprovalRequired as exc:
            approval_id = str(uuid.uuid4())
            db.add(ApprovalRequest(id=approval_id, task_id=state["task_id"], action=exc.tool.name, risk_level=exc.tool.risk_level, reason=str(exc)))
            step.status = "pending"
            task.status, task.plan = "waiting_approval", plan.model_dump(mode="json")
            await db.commit()
            await self._event(state, "approval_required", {"approval_id": approval_id, "tool_name": exc.tool.name, "risk_level": exc.tool.risk_level})
            return {"plan": plan.model_dump(mode="json"), "outcome": "approval_required"}
        step.status = "completed" if result.success else "failed"
        step.result_summary = result.summary
        evidence = state.get("evidence", []) + [{"step_id": step.step_id, "tool_name": step.tool_name, "result": result.model_dump(mode="json")}]
        task.plan = plan.model_dump(mode="json")
        await db.commit()
        await self._event(state, "tool_end", {"step_id": step.step_id, "tool_name": step.tool_name, "result": result.model_dump(mode="json")})
        return {"plan": task.plan, "evidence": evidence, "iterations": state.get("iterations", 0) + 1, "outcome": "continue"}

    @staticmethod
    def _fallback_replan(state: OpsState, plan: OpsPlan) -> dict[str, Any]:
        latest = state.get("evidence", [])[-1] if state.get("evidence") else None
        pending = [step for step in plan.steps if step.status == "pending"]
        if latest and latest["tool_name"] == "query_metrics" and latest["result"]["success"]:
            values = latest["result"]["data"].get("latest", {})
            pool, upstream = values.get("db_pool_usage_ratio"), values.get("upstream_timeout_rate")
            if pool is not None and pool < 0.9 and upstream is not None and upstream > 0.1:
                return {"action": "revise", "reason": "连接池指标正常而上游超时率升高，新增上游日志验证步骤", "added_steps": [{"title": "验证上游超时日志", "tool_name": "query_logs", "arguments": {"service_name": state["alert"]["service_name"], "environment": state["alert"]["environment"], "keyword": "upstream timeout"}, "success_criteria": "用日志独立验证上游超时指标"}], "skipped_step_ids": [step.step_id for step in pending if step.tool_name == "query_logs"]}
        if latest and not latest["result"]["success"] and not pending:
            return {"action": "need_human", "reason": "关键工具失败且没有更多可执行步骤", "added_steps": [], "skipped_step_ids": []}
        if not pending or state.get("outcome") == "finish":
            return {"action": "finish", "reason": "计划中没有更多待执行步骤", "added_steps": [], "skipped_step_ids": []}
        return {"action": "continue", "reason": "仍有待执行的只读调查步骤", "added_steps": [], "skipped_step_ids": []}

    async def _replanner(self, state: OpsState) -> dict[str, Any]:
        db, plan = state["db"], OpsPlan.model_validate(state["plan"])
        task = await db.get(OpsTask, state["task_id"])
        assert task
        if state.get("outcome") in {"approval_required", "cancelled"}:
            if state["outcome"] == "cancelled":
                task.status, task.finished_at = "cancelled", utcnow()
                await db.commit()
            return {}
        task.status = "replanning"
        fallback = self._fallback_replan(state, plan)
        evidence_view = [{"step_id": item["step_id"], "tool_name": item["tool_name"], "success": item["result"]["success"], "summary": item["result"]["summary"], "data": json.dumps(item["result"].get("data", {}), ensure_ascii=False)[:1800]} for item in state.get("evidence", [])[-6:]]
        raw = await self.model.generate_json(
            "你是OnCall Replanner。观察工具结果后，只能选择continue、revise、finish或need_human。证据冲突时不得给高置信结论；工具失败不等于系统正常；新增步骤只能使用给定工具目录。",
            f"目标：{state['goal']}\n告警：{json.dumps(state['alert'], ensure_ascii=False)}\n当前计划：{json.dumps(plan.model_dump(mode='json'), ensure_ascii=False)}\n观察：{json.dumps(evidence_view, ensure_ascii=False)}\n工具目录：{json.dumps(self._tool_catalog(), ensure_ascii=False)}",
            ReplanProposal.model_json_schema(), fallback, max_tokens=700,
        )
        decision_source = raw.pop("_decision_source", "unknown")
        try:
            proposal = ReplanProposal.model_validate(raw)
        except Exception:
            proposal = ReplanProposal.model_validate(fallback)
            decision_source = "schema_validation_fallback"
        required_evidence_tools = {"query_internal_docs", "query_metrics", "query_logs"}
        observed_tools_before = {
            item["tool_name"] for item in state.get("evidence", [])
        }
        replacement_tools = {item.tool_name for item in proposal.added_steps}
        blocked_skips: list[str] = []
        for step in plan.steps:
            if step.step_id in proposal.skipped_step_ids and step.status == "pending":
                if (
                    step.tool_name in required_evidence_tools
                    and step.tool_name not in observed_tools_before
                    and step.tool_name not in replacement_tools
                ):
                    blocked_skips.append(step.step_id)
                    continue
                step.status, step.result_summary = "skipped", f"Replanner跳过：{proposal.reason}"
        added = self._validated_steps(proposal.added_steps, state, prefix=f"replan-{state.get('iterations', 0)}", existing=plan.steps)
        plan.steps.extend(added)
        pending = [step for step in plan.steps if step.status == "pending"]
        successful_signals = {item["tool_name"] for item in state.get("evidence", []) if item["result"]["success"] and item["result"].get("data")}
        diagnostic_signals = successful_signals & {"query_metrics", "query_logs"}
        observed_tools = {item["tool_name"] for item in state.get("evidence", [])}
        pending_required = any(
            step.status == "pending" and step.tool_name in required_evidence_tools
            and step.tool_name not in observed_tools
            for step in plan.steps
        )
        action, reason = proposal.action, proposal.reason
        if state.get("iterations", 0) >= self.settings.ops_max_iterations:
            action, reason = ReplanAction.NEED_HUMAN, "达到最大调查轮数，需要人工接管"
        elif action == ReplanAction.FINISH and pending and (len(diagnostic_signals) < 2 or pending_required):
            action, reason = ReplanAction.CONTINUE, "Harness要求知识、指标与日志均被观察且至少两个诊断信号成功，继续执行剩余只读步骤"
        elif action == ReplanAction.CONTINUE and not pending:
            action, reason = ReplanAction.FINISH, "没有更多待执行步骤，进入报告阶段"
        elif action == ReplanAction.REVISE and not added and pending:
            action, reason = ReplanAction.CONTINUE, "新增步骤未通过工具白名单或参数校验，继续安全计划"
        elif action == ReplanAction.REVISE and not added and not pending:
            action, reason = ReplanAction.FINISH, "没有有效新增步骤，基于现有证据生成报告"
        if blocked_skips:
            decision_source = f"{decision_source}+harness_guard"
        decision = ReplanDecision(action=action, reason=reason, added_steps=added, skipped_step_ids=[step.step_id for step in plan.steps if step.status == "skipped"])
        decisions = state.get("decisions", []) + [decision.model_dump(mode="json")]
        task.plan = plan.model_dump(mode="json")
        await db.commit()
        await self._event(state, "replan", {"decision": decision.model_dump(mode="json"), "decision_source": decision_source, "successful_signals": sorted(successful_signals), "plan": task.plan})
        outcome = "finish" if action in {ReplanAction.FINISH, ReplanAction.NEED_HUMAN} else action.value
        return {"plan": task.plan, "decisions": decisions, "outcome": outcome}

    @staticmethod
    def _route(state: OpsState) -> str:
        if state.get("outcome") in {"approval_required", "abort", "cancelled"}:
            return "stop"
        return "report" if state.get("outcome") == "finish" else "execute"

    @staticmethod
    def _fallback_analysis(state: OpsState) -> dict[str, Any]:
        service, evidence = state["alert"]["service_name"], state.get("evidence", [])
        successes = [item for item in evidence if item["result"]["success"]]
        failures = [item for item in evidence if not item["result"]["success"]]
        metric = next((item for item in successes if item["tool_name"] == "query_metrics"), None)
        logs = next((item for item in successes if item["tool_name"] == "query_logs" and item["result"]["data"].get("entries")), None)
        latest = metric["result"]["data"].get("latest", {}) if metric else {}
        clusters = logs["result"]["data"].get("clusters", {}) if logs else {}
        supporting = [f"{item['tool_name']}: {item['result']['summary']}" for item in successes[-5:]]
        contradicting = [f"{item['tool_name']}失败: {item['result']['summary']}" for item in failures[-4:]]
        upstream_rate = latest.get("upstream_timeout_rate")
        db_pool = latest.get("db_pool_usage_ratio")
        upstream_logs = clusters.get("upstream_timeout", 0)
        evidence_conflict = bool(upstream_logs) and (
            (upstream_rate is not None and upstream_rate <= 0.05)
            or (db_pool is not None and db_pool >= 0.9 and (upstream_rate or 0) <= 0.05)
        )
        if evidence_conflict:
            candidate, confidence, basis = f"{service} 存在相互冲突的资源与上游异常信号", "低", "指标主信号与日志主信号指向不同根因，当前不能完成交叉验证"
            contradicting.append(
                f"指标 upstream_timeout_rate={upstream_rate}，但日志出现 upstream_timeout={upstream_logs}"
            )
        elif (upstream_rate or 0) > 0.1 and upstream_logs:
            candidate, confidence, basis = f"{service} 的上游依赖超时", "高", "指标与日志两个独立信号均支持上游超时"
        elif (db_pool or 0) >= 0.9 and logs:
            candidate, confidence, basis = f"{service} 的数据库连接资源耗尽", "中", "连接池指标异常且存在同期错误日志，但仍需确认具体泄漏或慢查询来源"
        else:
            candidate, confidence, basis = f"{service} 异常，根因尚未形成交叉验证", "低", "当前成功证据不足两个独立信号，或信号之间存在冲突"
        return {
            "executive_summary": f"已完成对 {service} 的有界调查；{basis}。",
            "root_causes": [{
                "candidate": candidate,
                "confidence": confidence,
                "basis": basis,
                "supporting_evidence": supporting or ["当前没有直接支持单一根因的工具证据"],
                "contradicting_evidence": contradicting or ["未发现与该候选直接冲突的工具证据"],
            }],
            "recommendations": [{"action": "补充缺失的指标、日志或Trace并对齐首次异常时间", "risk_level": "L1"}, {"action": "联系相关依赖服务值班人员核查同期健康状态", "risk_level": "L2"}, {"action": "重启、回滚、扩容或流量切换必须经过人工审批", "risk_level": "L3"}],
            "unconfirmed": [] if confidence == "高" else ["尚缺少两个一致的独立信号支持单一根因"],
            "need_human": confidence != "高" or bool(failures),
        }

    async def _reporter(self, state: OpsState) -> dict[str, Any]:
        task = await state["db"].get(OpsTask, state["task_id"])
        assert task
        evidence, fallback = state.get("evidence", []), self._fallback_analysis(state)
        evidence_view = [{"tool_name": item["tool_name"], "success": item["result"]["success"], "summary": item["result"]["summary"], "data": json.dumps(item["result"].get("data", {}), ensure_ascii=False)[:2200]} for item in evidence[-8:]]
        raw = await self.model.generate_json(
            "你是OnCall根因分析Agent。只根据工具证据生成通用故障结论。每个候选必须分别列出支持证据和反对证据；证据冲突、工具失败或只有单一信号时不得给高置信度；不得声称已执行写操作。",
            f"告警：{json.dumps(state['alert'], ensure_ascii=False)}\n目标：{state['goal']}\nReplanner决策：{json.dumps(state.get('decisions', []), ensure_ascii=False)}\n工具证据：{json.dumps(evidence_view, ensure_ascii=False)}",
            IncidentAnalysis.model_json_schema(), fallback, max_tokens=1000,
        )
        analysis_source = raw.pop("_decision_source", "unknown")
        try:
            analysis = IncidentAnalysis.model_validate(raw)
        except Exception:
            analysis = IncidentAnalysis.model_validate(fallback)
            analysis_source = "schema_validation_fallback"
        for candidate in analysis.root_causes:
            candidate.supporting_evidence = [
                item.strip() for item in candidate.supporting_evidence if item.strip()
            ]
            candidate.contradicting_evidence = [
                item.strip() for item in candidate.contradicting_evidence if item.strip()
            ]
            if not candidate.supporting_evidence:
                candidate.supporting_evidence = ["当前没有直接支持该候选的工具证据"]
                candidate.confidence = "低"
            if not candidate.contradicting_evidence:
                candidate.contradicting_evidence = ["未发现与该候选直接冲突的工具证据"]
        report = {
            "task_id": state["task_id"], "trigger_type": task.trigger_type, "created_at": task.created_at.isoformat(),
            "alert_summary": f"{state['alert']['service_name']} / {state['alert']['alert_name']} / {state['alert']['severity']}",
            "service_name": state["alert"]["service_name"], "environment": state["alert"]["environment"],
            "plan": state["plan"], "replan_decisions": state.get("decisions", []), "evidence": evidence,
            "root_causes": [item.model_dump(mode="json") for item in analysis.root_causes],
            "executive_summary": analysis.executive_summary,
            "recommendations": [item.model_dump(mode="json") for item in analysis.recommendations],
            "unconfirmed": analysis.unconfirmed, "need_human": analysis.need_human,
            "tool_failures": [item for item in evidence if not item["result"]["success"]],
            "analysis_source": analysis_source,
            "duration_seconds": max(0, (utcnow() - (task.started_at or task.created_at)).total_seconds()),
        }
        task.report = report
        await state["db"].commit()
        await self._event(state, "report", {"report": report})
        if state.get("notify"):
            result = await self.registry.execute(state["db"], "send_msg", {"channel": "mock", "target": "oncall-demo", "title": "OnCall 分析报告", "body": analysis.executive_summary, "task_id": state["task_id"]}, task_id=state["task_id"])
            await self._event(state, "notification", {"result": result.model_dump(mode="json")})
        task.status, task.finished_at = "completed", utcnow()
        await state["db"].commit()
        return {"outcome": "completed"}

    async def run_task(self, task_id: str) -> None:
        async with SessionLocal() as db:
            task = await db.get(OpsTask, task_id)
            if task is None:
                return
            try:
                await asyncio.wait_for(self.graph.ainvoke({"db": db, "task_id": task.id, "goal": task.goal, "alert": task.alert, "notify": task.notify}), timeout=self.settings.task_timeout_seconds)
            except asyncio.TimeoutError:
                task.status, task.error, task.finished_at = "timed_out", "任务超过总超时", utcnow()
                await db.commit()
            except Exception as exc:
                task.status, task.error, task.finished_at = "failed", str(exc)[:1000], utcnow()
                db.add(OpsEvent(task_id=task.id, event_type="error", payload={"message": str(exc)[:500]}))
                await db.commit()
