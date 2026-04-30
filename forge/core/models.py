"""
Core data models for FORGE.
Everything is typed. Everything is validated. No surprises.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
#  Enumerations
# ─────────────────────────────────────────────

class TaskType(str, Enum):
    """What kind of task is this?"""
    GENERAL    = "general"
    CODE       = "code"
    MATH       = "math"
    RESEARCH   = "research"
    CREATIVE   = "creative"
    REASONING  = "reasoning"
    VISION     = "vision"
    FAST       = "fast"       # speed-critical, sacrifices depth


class ModelTier(str, Enum):
    """How powerful is this model?"""
    ULTRA  = "ultra"    # 70B+ frontier models
    PRO    = "pro"      # 13B–70B strong models
    BASE   = "base"     # 7B–13B solid models
    FAST   = "fast"     # <7B or distilled — pure speed


class ProviderStatus(str, Enum):
    ONLINE  = "online"
    SLOW    = "slow"
    QUOTA   = "quota_exhausted"
    OFFLINE = "offline"


# ─────────────────────────────────────────────
#  Model Definition
# ─────────────────────────────────────────────

class ModelSpec(BaseModel):
    """Full specification of a single model."""
    id: str                                  # e.g. "llama-3.3-70b-versatile"
    provider: str                            # e.g. "groq"
    display_name: str
    tier: ModelTier         = ModelTier.BASE
    context_window: int     = 8_192
    max_output_tokens: int  = 4_096
    free: bool              = True
    supports_vision: bool   = False
    supports_tools: bool    = True
    strong_at: list[TaskType] = Field(default_factory=list)
    tags: list[str]           = Field(default_factory=list)

    class Config:
        frozen = True


# ─────────────────────────────────────────────
#  Live Intelligence Score
# ─────────────────────────────────────────────

class ModelScore(BaseModel):
    """
    Real-time score for a model. Updated after every call.
    Score drives the Smart Selector — this IS the intelligence.
    """
    model_id:        str
    provider:        str
    status:          ProviderStatus = ProviderStatus.ONLINE

    # Performance metrics (exponential moving average)
    latency_ms:      float = 1500.0   # avg response latency
    success_rate:    float = 1.0      # 0.0 – 1.0
    quality_score:   float = 0.75     # heuristic quality rating
    tokens_per_sec:  float = 50.0     # throughput

    # Quota tracking
    tokens_used_today:    int = 0
    tokens_limit_daily:   int = 0      # 0 = unlimited
    requests_used_today:  int = 0
    requests_limit_daily: int = 0      # 0 = unlimited
    quota_reset_at:       float = 0.0  # unix timestamp

    # History
    total_calls:    int   = 0
    total_failures: int   = 0
    last_used_at:   float = 0.0
    last_error:     str   = ""

    @property
    def quota_fraction(self) -> float:
        """How much quota is left? 1.0 = full, 0.0 = empty."""
        if self.tokens_limit_daily == 0:
            return 1.0
        used = self.tokens_used_today / self.tokens_limit_daily
        return max(0.0, 1.0 - used)

    @property
    def composite_score(self) -> float:
        """
        The single number FORGE uses to rank models for a task.
        Weights: quality 40% · quota 30% · speed 20% · reliability 10%
        """
        if self.status in (ProviderStatus.QUOTA, ProviderStatus.OFFLINE):
            return 0.0
        speed   = min(1.0, 500.0 / max(self.latency_ms, 50))
        return (
            self.quality_score   * 0.40 +
            self.quota_fraction  * 0.30 +
            speed                * 0.20 +
            self.success_rate    * 0.10
        )

    def record_success(self, latency_ms: float, tokens_used: int) -> None:
        alpha = 0.15   # EMA smoothing factor
        self.latency_ms     = (1 - alpha) * self.latency_ms + alpha * latency_ms
        self.success_rate   = (1 - alpha) * self.success_rate + alpha * 1.0
        self.tokens_used_today  += tokens_used
        self.requests_used_today += 1
        self.total_calls    += 1
        self.last_used_at   = time.time()

    def record_failure(self, error: str) -> None:
        alpha = 0.15
        self.success_rate = (1 - alpha) * self.success_rate + alpha * 0.0
        self.total_failures += 1
        self.total_calls    += 1
        self.last_error     = error


# ─────────────────────────────────────────────
#  Message & Response
# ─────────────────────────────────────────────

class Message(BaseModel):
    role:    str             # "user" | "assistant" | "system" | "tool"
    content: str | list[Any]
    name:    str | None = None


class ForgeResponse(BaseModel):
    """Everything FORGE returns from a model call."""
    content:        str
    model_id:       str
    provider:       str
    latency_ms:     float
    input_tokens:   int   = 0
    output_tokens:  int   = 0
    finish_reason:  str   = "stop"
    score_used:     float = 0.0   # composite score at time of selection
    routing_telemetry: dict[str, Any] = Field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens
