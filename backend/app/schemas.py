from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Alert(BaseModel):
    alert_name: str
    service_name: str
    environment: str = "production"
    severity: str = "warning"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime | None = None


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=8000)
    service_name: str | None = None
    environment: str = "production"
    enable_tools: bool = True


class ToolCallView(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    success: bool
    summary: str
    duration_ms: int


class ChatResponse(BaseModel):
    request_id: str
    session_id: str
    message_id: str
    answer: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[ToolCallView] = Field(default_factory=list)
    finish_reason: str = "completed"


class OpsCreateRequest(BaseModel):
    trigger_type: str = "manual"
    goal: str = Field(min_length=1, max_length=4000)
    alert: Alert
    notify: bool = False


class PlanStep(BaseModel):
    step_id: str
    title: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    success_criteria: str
    result_summary: str | None = None


class OpsPlan(BaseModel):
    task_id: str
    goal: str
    assumptions: list[str] = Field(default_factory=list)
    steps: list[PlanStep]
    completion_criteria: str
    max_iterations: int = Field(default=12, ge=1, le=30)


class ReplanAction(StrEnum):
    CONTINUE = "continue"
    REVISE = "revise"
    FINISH = "finish"
    NEED_HUMAN = "need_human"
    ABORT = "abort"


class ReplanDecision(BaseModel):
    action: ReplanAction
    reason: str
    added_steps: list[PlanStep] = Field(default_factory=list)
    skipped_step_ids: list[str] = Field(default_factory=list)


class ToolStepProposal(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    tool_name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    success_criteria: str = Field(min_length=1, max_length=240)


class InvestigationPlanDecision(BaseModel):
    objective: str = Field(min_length=1, max_length=300)
    hypotheses: list[str] = Field(default_factory=list, max_length=6)
    steps: list[ToolStepProposal] = Field(default_factory=list, max_length=8)


class ReplanProposal(BaseModel):
    action: ReplanAction
    reason: str = Field(min_length=1, max_length=400)
    added_steps: list[ToolStepProposal] = Field(default_factory=list, max_length=3)
    skipped_step_ids: list[str] = Field(default_factory=list, max_length=8)


class KnowledgePlanDecision(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=3)
    rationale: str = Field(min_length=1, max_length=300)


class KnowledgeReviewDecision(BaseModel):
    sufficient: bool
    reason: str = Field(min_length=1, max_length=300)
    follow_up_query: str | None = Field(default=None, max_length=500)


class RootCauseCandidate(BaseModel):
    candidate: str = Field(min_length=1, max_length=240)
    confidence: Literal["低", "中", "高"]
    basis: str = Field(min_length=1, max_length=500)
    supporting_evidence: list[str] = Field(min_length=1, max_length=6)
    contradicting_evidence: list[str] = Field(min_length=1, max_length=6)


class Recommendation(BaseModel):
    action: str = Field(min_length=1, max_length=300)
    risk_level: Literal["L0", "L1", "L2", "L3", "L4"]


class IncidentAnalysis(BaseModel):
    executive_summary: str = Field(min_length=1, max_length=500)
    root_causes: list[RootCauseCandidate] = Field(min_length=1, max_length=4)
    recommendations: list[Recommendation] = Field(default_factory=list, max_length=6)
    unconfirmed: list[str] = Field(default_factory=list, max_length=6)
    need_human: bool = True


class ApprovalDecision(BaseModel):
    approved: bool
    reason: str = ""


class ToolResult(BaseModel):
    success: bool
    tool_name: str
    data: dict[str, Any] = Field(default_factory=dict)
    summary: str
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime
    trace_id: str
