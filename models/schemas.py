"""
Orion Consultant — Pydantic models for the API and MCP tools.

These schemas define the JSON contract between Java Bot ↔ n8n ↔ Orion.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────


class SignalDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Verdict(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    HOLD = "HOLD"


class ExpertName(str, Enum):
    RISK_MANAGER = "risk_manager"
    TREND_ANALYZER = "trend_analyzer"
    PATTERN_EXPERT = "pattern_expert"


# ── Request ───────────────────────────────────────────


class SignalRequest(BaseModel):
    """Incoming trading signal from the Java bot via n8n."""

    symbol: str = Field(..., examples=["Step Index"])
    direction: SignalDirection
    entry_price: float = Field(..., gt=0, examples=[5432.10])
    stop_loss: float = Field(..., gt=0, examples=[5400.00])
    take_profit: float = Field(..., gt=0, examples=[5500.00])

    # Account state
    equity: float = Field(..., gt=0, examples=[1000.0])
    balance: float = Field(..., gt=0, examples=[1050.0])

    # Market context
    current_volatility: float = Field(default=0.0, ge=0, examples=[120.5])
    trend_h1: Optional[str] = Field(default=None, examples=["bullish"])
    trend_h4: Optional[str] = Field(default=None, examples=["bullish"])

    # Metadata
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Responses ─────────────────────────────────────────


class ExpertOpinion(BaseModel):
    """Individual opinion from one expert agent."""

    expert: ExpertName
    verdict: Verdict
    confidence: float = Field(..., ge=0.0, le=1.0, examples=[0.85])
    reason: str = Field(..., examples=["Drawdown is within acceptable limits."])


class CommitteeVerdict(BaseModel):
    """Consolidated verdict from the full committee."""

    final_verdict: Verdict
    approved_count: int = Field(..., ge=0)
    rejected_count: int = Field(..., ge=0)
    opinions: list[ExpertOpinion]
    summary: str = Field(..., examples=["2/3 experts approved. Proceeding with caution."])
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    service: str = "orion-consultant"
    version: str = "0.1.0"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
