from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class IntentKind(str, Enum):
    RESEARCH = "research"
    WRITING = "writing"
    TRANSFORMATION = "transformation"
    PUBLISHING = "publishing"
    AUTOMATION = "automation"
    ORCHESTRATION = "orchestration"
    DEBUGGING = "debugging"
    ANALYSIS = "analysis"
    CONTENT_GENERATION = "content_generation"
    STRUCTURED_OUTPUT = "structured_output"
    CONVERSATION = "conversation"


class ExecutionClass(str, Enum):
    SIMPLE_REASONING = "simple_reasoning"
    SINGLE_SKILL = "single_skill"
    MULTI_SKILL_PIPELINE = "multi_skill_pipeline"
    RISKY_ACTION = "risky_action"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    REQUIRES_VALIDATION = "requires_validation"
    REQUIRES_FALLBACK = "requires_fallback"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CompletionState(str, Enum):
    FINISHED = "finished"
    PARTIALLY_FINISHED = "partially_finished"
    FAILED = "failed"
    NEEDS_RETRY = "needs_retry"
    NEEDS_HUMAN_CONFIRMATION = "needs_human_confirmation"


class TaskIntent(BaseModel):
    raw_request: str
    objective: str
    primary_intent: IntentKind
    intents: list[IntentKind] = Field(default_factory=list)
    task_type: str = "general"
    hidden_intent: str = ""
    requested_output: str = "clean final response"
    execution_classes: list[ExecutionClass] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    notes: list[str] = Field(default_factory=list)


class PlanStep(BaseModel):
    id: str
    action: str
    skill: str | None = None
    tool: str | None = None
    input_spec: dict[str, Any] = Field(default_factory=dict)
    expected_output: str
    validation: str
    risk_note: str = ""
    fallback_skill: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    retry_limit: int = 2
    stop_on_failure: bool = True
    rollback_on_failure: bool = False


class ExecutionPlan(BaseModel):
    objective: str
    task_type: str
    risk_level: RiskLevel
    steps: list[PlanStep] = Field(default_factory=list)
    fallbacks: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)


class StepExecutionResult(BaseModel):
    step_id: str
    skill: str | None = None
    tool: str | None = None
    status: CompletionState
    output: Any = None
    evidence: list[str] = Field(default_factory=list)
    validation_status: CompletionState = CompletionState.FAILED
    validation_notes: list[str] = Field(default_factory=list)
    attempts: int = 1
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    trace: list[str] = Field(default_factory=list)
    rolled_back: bool = False
    rollback_notes: list[str] = Field(default_factory=list)
    agent_reviews: list[str] = Field(default_factory=list)
    error: str = ""


class AgentReview(BaseModel):
    agent: str
    status: CompletionState
    notes: list[str] = Field(default_factory=list)
    confidence: float | None = None


class OperatorResult(BaseModel):
    objective: str
    approach_taken: list[str] = Field(default_factory=list)
    result: str
    validation_status: CompletionState
    risks_or_limitations: list[str] = Field(default_factory=list)
    best_next_action: str
    intent: TaskIntent
    plan: ExecutionPlan
    step_results: list[StepExecutionResult] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    mission_trace: list[str] = Field(default_factory=list)
    mission_id: str = ""
    audit_log_path: str = ""
    resumed_from_step: str | None = None
    agent_reviews: list[AgentReview] = Field(default_factory=list)
    provider_telemetry: dict[str, Any] = Field(default_factory=dict)
