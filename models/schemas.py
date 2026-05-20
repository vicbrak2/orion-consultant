"""
Orion Consultant - Pydantic models for the API and MCP tools.

These schemas define the JSON contract between Java Bot <-> n8n <-> Orion.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any

from pydantic import BaseModel, Field


# ---- Enums -------------------------------------------------------


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
    VIGILANTE_AGENT = "vigilante_agent"

# ---- Request -----------------------------------------------------


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

    # Market context (legacy)
    current_volatility: float = Field(default=0.0, ge=0, examples=[120.5])
    trend_h1: Optional[str] = Field(default=None, examples=["bullish"])
    trend_h4: Optional[str] = Field(default=None, examples=["bullish"])

    # Enrichment: Trace & strategy
    trace_id: Optional[str] = Field(default=None, examples=["NA-1774987672997"])
    strategy_id: Optional[str] = Field(default=None, examples=["step_index_confluence_v1"])

    # Enrichment: FSM state
    fsm_phase: Optional[str] = Field(default=None, examples=["TREND"])
    step_index_type: Optional[str] = Field(default=None, examples=["CLASSIC"])
    current_clv: Optional[float] = Field(default=None, examples=[0.62])
    previous_clv: Optional[float] = Field(default=None, examples=[0.41])
    macro_structure_ok: Optional[bool] = Field(default=None, examples=[True])
    sar_adx_signal: Optional[int] = Field(default=None, examples=[1])
    sar_adx_blocking: Optional[bool] = Field(default=None, examples=[False])

    # Enrichment: Technical indicators (M15)
    adx_m15: Optional[float] = Field(default=None, examples=[28.5])
    plus_di_m15: Optional[float] = Field(default=None, examples=[31.2])
    minus_di_m15: Optional[float] = Field(default=None, examples=[14.8])
    atr_m15: Optional[float] = Field(default=None, examples=[48.0])
    adx_macro: Optional[float] = Field(default=None, examples=[28.5])
    plus_di_macro: Optional[float] = Field(default=None, examples=[31.2])
    minus_di_macro: Optional[float] = Field(default=None, examples=[14.8])
    atr_macro: Optional[float] = Field(default=None, examples=[48.0])
    range_to_atr: Optional[float] = Field(default=None, examples=[1.36])
    range_to_atr_micro: Optional[float] = Field(default=None, examples=[1.22])
    bb_kc_ratio: Optional[float] = Field(default=None, examples=[0.92])
    bb_kc_ratio_macro: Optional[float] = Field(default=None, examples=[0.82])

    # Enrichment: actual MTF source of truth
    trend_timeframe: Optional[str] = Field(default=None, examples=["H1"])
    macro_timeframe: Optional[str] = Field(default=None, examples=["M15"])
    micro_timeframe: Optional[str] = Field(default=None, examples=["M5"])
    trend_direction: Optional[str] = Field(default=None, examples=["BULLISH"])

    # Enrichment: Episode / bias
    bias: Optional[int] = Field(default=None, examples=[1])
    entry_window_open: Optional[bool] = Field(default=None, examples=[True])

    # Enrichment: Tactical confidence
    tactical_confidence: Optional[float] = Field(default=None, examples=[0.85])

    # Enrichment: Flexible context blocks
    decision_context: Optional[dict[str, Any]] = Field(default=None)
    analysis_context: Optional[dict[str, Any]] = Field(default=None)
    episode_summary: Optional[dict[str, Any]] = Field(default=None)
    episode_context: Optional[dict[str, Any]] = Field(default=None)
    account_context: Optional[dict[str, Any]] = Field(default=None)
    confirmations: Optional[dict[str, Any]] = Field(default=None)
    episode_events: Optional[list[dict[str, Any]]] = Field(default=None)
    episode_checkpoints: Optional[list[dict[str, Any]]] = Field(default=None)

    # Enrichment: Historical performance context
    # Populated by the Java bot from trade_results joined with trades.
    # Expected shape:
    #   {
    #     "pnl_history": [1.5, -0.8, 2.1, ...],  # PnL per closed trade (chronological)
    #     "n_trades": 42,                          # total closed trades
    #     "win_rate": 0.61,                        # optional override
    #     "lookback": 50                           # optional: how many recent trades included
    #   }
    performance_context: Optional[dict[str, Any]] = Field(default=None)

    # Metadata
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VigilanteRequest(BaseModel):
    """Incoming request for async Vigilante evaluation of an open episode."""
    symbol: str = Field(..., examples=["Step Index"])
    ticket_id: int = Field(..., examples=[123456789])
    direction: SignalDirection
    entry_price: float = Field(..., gt=0)
    current_price: float = Field(..., gt=0)
    current_volatility: float = Field(default=0.0, ge=0)
    unrealized_pnl: float = Field(default=0.0)
    duration_minutes: int = Field(..., ge=0)
    rsi_value: Optional[float] = Field(default=None)
    metadata: Optional[dict] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# ---- Responses ---------------------------------------------------


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
    version: str = "0.2.0"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---- Strategic Bias (H1 async macro mandate) ---------------------


class StrategicBiasMode(str, Enum):
    BUY_MODE = "BUY_MODE"
    SELL_MODE = "SELL_MODE"
    HOLD = "HOLD"


class StrategicBiasRequest(BaseModel):
    """Periodic H1 request from Java scheduler for macro directional bias.

    Does NOT include entry_price/SL/TP — this is a macro state snapshot,
    not a per-signal evaluation.
    """

    symbol: str = Field(..., examples=["Step Index"])
    equity: float = Field(..., gt=0, examples=[1000.0])
    balance: float = Field(..., gt=0, examples=[1050.0])
    current_volatility: float = Field(default=0.0, ge=0, examples=[150.0])

    # Macro trend state
    trend_h1: Optional[str] = Field(default=None, examples=["bullish"])
    trend_h4: Optional[str] = Field(default=None, examples=["bullish"])
    trend_direction: Optional[str] = Field(default=None, examples=["BULLISH"])
    macro_structure_ok: Optional[bool] = Field(default=None, examples=[True])

    # FSM / enrichment
    fsm_phase: Optional[str] = Field(default=None, examples=["TREND"])
    adx_macro: Optional[float] = Field(default=None, examples=[28.5])
    bb_kc_ratio_macro: Optional[float] = Field(default=None, examples=[0.82])
    current_clv: Optional[float] = Field(default=None, examples=[0.62])
    sar_adx_blocking: Optional[bool] = Field(default=None, examples=[False])
    range_to_atr: Optional[float] = Field(default=None, examples=[1.36])

    # Historical performance (optional — for lot fraction scaling)
    performance_context: Optional[dict[str, Any]] = Field(default=None)

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StrategicBiasResponse(BaseModel):
    """H1 directional mandate from Orion. Cached by the Java scheduler.

    The Java side must treat this as read-only until ``valid_until`` passes,
    at which point it triggers a new refresh in the background.
    """

    symbol: str
    bias: StrategicBiasMode
    confidence: float = Field(..., ge=0.0, le=1.0, examples=[0.82])
    reason: str
    max_lot_fraction: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="Scalar applied to normal lot size. 0.5 = half size, 1.0 = full.",
    )
    valid_until: datetime = Field(
        ...,
        description="UTC timestamp after which this bias should be refreshed.",
    )
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
