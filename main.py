"""
🌌 Orion Consultant — FastAPI Application.

REST API for n8n webhooks and external integrations.
MCP Server is mounted at /mcp for Streamable HTTP transport.

Usage:
    uvicorn main:app --reload --port 8090
"""

from __future__ import annotations

import json
import sys
import os
import logging
import asyncio
import hmac
from time import perf_counter
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError
from prometheus_fastapi_instrumentator import Instrumentator
# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agents.rag_client as rag_client

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from models.schemas import (
    SignalRequest,
    ExpertOpinion,
    CommitteeVerdict,
    HealthResponse,
    ExpertName,
    Verdict,
    VigilanteRequest,
    StrategicBiasMode,
    StrategicBiasRequest,
    StrategicBiasResponse,
)
from agents.risk_manager import evaluate_risk
from agents.trend_analyzer import evaluate_trend
from agents.pattern_expert import evaluate_pattern
from agents.vigilante_agent import evaluate_vigilante_episode
from utils.context_extractors import (
    get_analysis_context,
    get_confirmations,
    get_episode_checkpoints,
    get_episode_context,
    get_episode_events,
    get_nested,
)
from utils.win_rate_context import get_win_rate_context
from metrics import (
    AGENT_CHAT_LATENCY,
    AGENT_CHAT_REQUESTS_TOTAL,
    CONSULT_LATENCY,
    CONSULT_REQUESTS_TOTAL,
    CONSULT_VERDICTS_TOTAL,
    ENRICHMENT_FIELDS_PRESENT,
    ENRICHED_REQUESTS_TOTAL,
    EXPERT_CONFIDENCE,
    EXPERT_LATENCY,
    EXPERT_VERDICTS_TOTAL,
    JAVA_PROCESS_EVENT_TOTAL,
    LAST_CONSULT_TIMESTAMP,
    LEAN_REQUESTS_TOTAL,
    LLM_CALLS_TOTAL,
    N8N_HEALTHCHECK_TOTAL,
    NOTIFICATION_REQUESTS_TOTAL,
    ORION_INFO,
    RELAY_LATENCY,
    RELAY_REQUESTS_TOTAL,
    initialize_integration_metrics,
    track_enrichment,
    track_expert_opinion,
)

# Import the MCP server instance for mounting
from mcp_server import mcp as mcp_server_instance

# ── Logging ───────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("orion")


class _HealthMetricsFilter(logging.Filter):
    """Suppress uvicorn access-log lines for /health and /metrics endpoints.

    These are polled every few seconds by Docker and Prometheus and drown out
    meaningful committee/agent log lines.
    """

    _SKIP = frozenset(["/health", "/actuator/health", "/metrics"])

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(path in msg for path in self._SKIP)


# Apply to uvicorn access logger so /health and /metrics are silent
_uvicorn_access = logging.getLogger("uvicorn.access")
_uvicorn_access.addFilter(_HealthMetricsFilter())

# Build the MCP ASGI app once so its routes and lifespan can be attached
# to the main FastAPI app without double-prefixing the transport path.
mcp_http_app = mcp_server_instance.streamable_http_app()


# ── Lifespan ──────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp_http_app.router.lifespan_context(mcp_http_app):
        ORION_INFO.info({"version": app.version, "environment": os.getenv("SPRING_PROFILES_ACTIVE", "standalone")})
        initialize_integration_metrics()
        logger.info("🌌 Orion Consultant starting on port %d", settings.port)
        logger.info("🔌 MCP Server mounted at /mcp/ (StreamableHTTP)")
        logger.info("📡 REST API available at /api/v1/")
        logger.info("📖 Swagger docs at /docs")
        yield
        logger.info("🌌 Orion Consultant shutting down.")


# ── App ───────────────────────────────────────────────

app = FastAPI(
    title="Orion Consultant",
    description=(
        "Comité de Expertos para decisiones de trading en Step Index. "
        "Expone análisis de riesgo, tendencia y patrones vía REST y MCP."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

# CORS — allow n8n and local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Instrumentator(
    excluded_handlers=["/health", "/actuator/health"],
    should_group_status_codes=False,
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# ── API Key middleware ─────────────────────────────────
# Endpoints that don't require authentication (infra / observability)
_AUTH_EXEMPT = {"/health", "/actuator/health", "/metrics", "/docs", "/openapi.json", "/redoc"}


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Validate X-API-Key header on all non-exempt endpoints.

    If ``ORION_API_KEY`` is not set (empty string), authentication is skipped
    so local dev works without extra config. Set the variable in production.
    """
    if settings.api_key and request.url.path not in _AUTH_EXEMPT:
        provided = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(provided, settings.api_key):
            logger.warning(
                "auth_rejected path=%s remote=%s",
                request.url.path,
                request.client.host if request.client else "unknown",
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing X-API-Key"},
            )
    return await call_next(request)

# ── Attach MCP Server routes (StreamableHTTP) ────────
# n8n MCP Client connects to: http://orion-consultant:8090/mcp/
app.router.routes.extend(mcp_http_app.routes)


# ── Helpers ───────────────────────────────────────────


async def _build_committee_verdict(signal: SignalRequest) -> CommitteeVerdict:
    """Run all 3 experts in parallel, augmented with RAG."""
    track_enrichment(signal)
    rag_context = await rag_client.fetch_rag_memory(signal.symbol)
    analysis_context = get_analysis_context(signal)
    episode_context = get_episode_context(signal)
    episode_events = get_episode_events(signal)
    episode_checkpoints = get_episode_checkpoints(signal)
    confirmations = get_confirmations(signal)

    # Offline scorer context — resolved once per consultation from in-memory cache
    win_rate_ctx = get_win_rate_context(
        signal.symbol,
        fsm_phase=signal.fsm_phase,
        entry_regime=(
            signal.analysis_context.get("entry_regime")
            if isinstance(signal.analysis_context, dict) else None
        ),
        orion_verdict=None,  # not yet known at entry time
        entry_adx=signal.adx_m15,
        entry_window_open=signal.entry_window_open,
        sar_adx_signal=signal.sar_adx_signal,
        direction=signal.direction.value,
    )

    async def _measure_expert(expert: str, coro: Any) -> ExpertOpinion:
        started = perf_counter()
        opinion = await coro
        elapsed = perf_counter() - started
        EXPERT_LATENCY.labels(expert=expert).observe(elapsed)
        track_expert_opinion(opinion)
        reason_upper = opinion.reason.upper()
        llm_status = "fallback"
        if "[LLM]" in reason_upper or "[GROQ RAG]" in reason_upper:
            llm_status = "success"
        if settings.enable_expert_llm:
            LLM_CALLS_TOTAL.labels(expert=expert, status=llm_status).inc()
        return opinion

    opinions = await asyncio.gather(
        _measure_expert(
            "risk_manager",
            evaluate_risk(
                symbol=signal.symbol,
                equity=signal.equity,
                balance=signal.balance,
                current_volatility=signal.current_volatility,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                rag_context=rag_context,
                fsm_phase=signal.fsm_phase,
                macro_structure_ok=signal.macro_structure_ok,
                sar_adx_blocking=signal.sar_adx_blocking,
                range_to_atr=signal.range_to_atr,
                account_context=signal.account_context,
                episode_context=episode_context,
                episode_checkpoints=episode_checkpoints,
                performance_context=signal.performance_context,
                win_rate_context=win_rate_ctx,
            ),
        ),
        _measure_expert(
            "trend_analyzer",
            evaluate_trend(
                symbol=signal.symbol,
                direction=signal.direction.value,
                trend_h1=signal.trend_h1,
                trend_h4=signal.trend_h4,
                rag_context=rag_context,
                bias=signal.bias,
                current_clv=signal.current_clv,
                previous_clv=signal.previous_clv,
                entry_window_open=signal.entry_window_open,
                macro_structure_ok=signal.macro_structure_ok,
                trend_direction=signal.trend_direction,
                trend_timeframe=signal.trend_timeframe,
                macro_timeframe=signal.macro_timeframe,
                micro_timeframe=signal.micro_timeframe,
                analysis_context=analysis_context,
            ),
        ),
        _measure_expert(
            "pattern_expert",
            evaluate_pattern(
                symbol=signal.symbol,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                current_volatility=signal.current_volatility,
                direction=signal.direction.value,
                rag_context=rag_context,
                step_index_type=signal.step_index_type,
                bb_kc_ratio=signal.bb_kc_ratio,
                range_to_atr=signal.range_to_atr,
                sar_adx_signal=signal.sar_adx_signal,
                decision_context=signal.decision_context,
                range_to_atr_micro=signal.range_to_atr_micro,
                bb_kc_ratio_macro=signal.bb_kc_ratio_macro,
                analysis_context=analysis_context,
                confirmations=confirmations,
                episode_events=episode_events,
            ),
        ),
    )

    approved = sum(1 for o in opinions if o.verdict == Verdict.APPROVE)
    rejected = sum(1 for o in opinions if o.verdict == Verdict.REJECT)

    if approved >= 2:
        final = Verdict.APPROVE
        summary = f"{approved}/3 expertos aprobaron. Operación autorizada."
    elif rejected >= 2:
        final = Verdict.REJECT
        summary = f"{rejected}/3 expertos rechazaron. Operación denegada."
    else:
        final = Verdict.HOLD
        summary = "Sin consenso claro. Se recomienda esperar confirmación."

    return CommitteeVerdict(
        final_verdict=final,
        approved_count=approved,
        rejected_count=rejected,
        opinions=opinions,
        summary=summary,
    )


@app.post("/api/v1/vigilante-evaluation", response_model=ExpertOpinion, tags=["Vigilante"])
async def consult_vigilante(request: VigilanteRequest):
    """
    Evaluación estratégica asíncrona usando Groq + n8n PostgreSQL RAG Memory.
    Este endpoint consume la memoria histórica antes de devolver el dictamen.
    """
    try:
        logger.info(
            "🔎 Vigilante LLM request received for %s [PNL: %.2f]",
            request.symbol,
            request.unrealized_pnl,
        )
        opinion = await evaluate_vigilante_episode(request)
        logger.info("🛡️ Vigilante Verdict -> %s", opinion.verdict.value)
        return opinion
    except Exception as e:
        logger.error("Vigilante failed: %s", str(e))
        return ExpertOpinion(
            expert=ExpertName.VIGILANTE_AGENT,
            verdict=Verdict.HOLD,
            confidence=0.0,
            reason=f"System error: {str(e)}"
        )


def _join_url(base_url: str, path: str) -> str:
    """Join a base URL with a relative path.

    Absolute URLs in ``path`` are rejected to prevent SSRF: all outbound
    requests must go through the configured base URLs (n8n / Java bot), never
    to an arbitrary host supplied by the caller.
    """
    parsed = urlsplit(path.strip())
    if parsed.scheme or parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail=(
                "webhookPath must be a relative path, not an absolute URL. "
                "Absolute destinations are not permitted."
            ),
        )
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


async def _post_json(
    url: str,
    payload: Any,
    *,
    timeout: float,
) -> dict[str, Any]:
    """POST JSON and return the parsed response body."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


async def _post_without_response(
    url: str,
    payload: Any,
    *,
    timeout: float,
) -> None:
    """POST JSON and only validate the response status code."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()


async def _get_text(url: str, *, timeout: float) -> str:
    """GET a URL and return the response text."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def _coerce_json_object(payload: Any, *, nested_key: str | None = None) -> dict[str, Any]:
    """
    Accept either a raw JSON object, a JSON string, or an n8n-style wrapper.

    n8n webhook and HTTP Request nodes can send the original object at the
    top-level, under `body`, or as a JSON-stringified payload.
    """
    candidate = payload

    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid JSON string payload: {exc.msg}",
            ) from exc

    if not isinstance(candidate, dict):
        raise HTTPException(
            status_code=422,
            detail="Request body must be a JSON object.",
        )

    if nested_key and nested_key in candidate:
        candidate = candidate[nested_key]
        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid nested JSON string payload: {exc.msg}",
                ) from exc

    if not isinstance(candidate, dict):
        raise HTTPException(
            status_code=422,
            detail="Resolved payload must be a JSON object.",
        )

    return candidate


def _parse_signal_request(payload: Any) -> SignalRequest:
    try:
        return SignalRequest.model_validate(_coerce_json_object(payload, nested_key="body"))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def _signal_log_context(signal: SignalRequest) -> dict[str, Any]:
    episode_context = get_episode_context(signal)
    confirmations = get_confirmations(signal)
    return {
        "trace_id": signal.trace_id or "n/a",
        "symbol": signal.symbol,
        "direction": signal.direction.value,
        "strategy_id": signal.strategy_id,
        "fsm_phase": signal.fsm_phase,
        "trend_tf": signal.trend_timeframe,
        "macro_tf": signal.macro_timeframe,
        "micro_tf": signal.micro_timeframe,
        "trend_direction": signal.trend_direction,
        "sar_adx_signal": signal.sar_adx_signal,
        "bias": signal.bias,
        "entry_window_open": signal.entry_window_open,
        "episode_state": episode_context.get("state"),
        "confirmations": _confirmed_names(confirmations),
    }


def _confirmed_names(confirmations: dict[str, Any]) -> list[str]:
    return sorted(
        key
        for key, value in confirmations.items()
        if isinstance(value, bool) and value
    )


def _format_expert_opinion(opinion: ExpertOpinion) -> str:
    return (
        f"{opinion.expert.value}={opinion.verdict.value}"
        f"({opinion.confidence:.2f}) reason={opinion.reason[:180]}"
    )


def _compute_rr(entry: float, sl: float, tp: float) -> float | None:
    """Risk/reward ratio. Returns None when SL == entry (division by zero)."""
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk == 0:
        return None
    return round(reward / risk, 2)


def _dominant_blockers(signal: SignalRequest, opinions: list[ExpertOpinion]) -> list[str]:
    """Collect explicit blocking reasons from deterministic gates and REJECT opinions."""
    blockers: list[str] = []
    if not signal.entry_window_open:
        blockers.append("entry_window_closed")
    if signal.sar_adx_signal is not None and signal.sar_adx_signal == 0:
        blockers.append("sar_adx_signal_zero")
    if signal.sar_adx_blocking:
        blockers.append("sar_adx_blocking")
    if signal.macro_structure_ok is False:
        blockers.append("macro_structure_not_ok")
    sl_tp_valid = (
        signal.stop_loss > 0
        and signal.take_profit > 0
        and signal.stop_loss != signal.entry_price
        and signal.take_profit != signal.entry_price
    )
    if not sl_tp_valid:
        blockers.append("sl_tp_invalid")
    for op in opinions:
        if op.verdict.value == "REJECT":
            # Extract a short label from the reason (first ~40 chars, no whitespace mess)
            short = op.reason.strip()[:60].replace("\n", " ")
            blockers.append(f"{op.expert.value}:{short}")
    return blockers


def _voting_policy(approved: int, rejected: int, final_verdict: str) -> str:
    """Describe the voting policy that produced the final verdict."""
    total = approved + rejected
    hold_count = 3 - total
    if approved >= 2:
        return f"majority_approve({approved}/3)"
    if rejected >= 2:
        return f"majority_reject({rejected}/3)"
    if hold_count >= 2:
        return "majority_hold"
    return f"split_{approved}A_{rejected}R_{hold_count}H->{final_verdict}"


# ── Endpoints ─────────────────────────────────────────


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Health check endpoint."""
    return HealthResponse()


@app.get("/actuator/health", response_model=HealthResponse, tags=["System"])
async def actuator_health_check():
    """Spring Actuator compatible health alias for Java clients."""
    return HealthResponse()


# ── Strategic Bias ─────────────────────────────────────────────────


async def _compute_strategic_bias(req: StrategicBiasRequest) -> StrategicBiasResponse:
    """Evaluate macro directional bias at H1 frequency.

    Runs risk_manager + trend_analyzer for BUY and SELL in parallel.
    No pattern_expert — this is a macro state evaluation, not a per-signal one.
    """
    rag_context = await rag_client.fetch_rag_memory(req.symbol)

    # 1. Risk gate — is it safe to trade at all?
    risk_opinion = await evaluate_risk(
        equity=req.equity,
        balance=req.balance,
        current_volatility=req.current_volatility,
        symbol=req.symbol,
        rag_context=rag_context,
        fsm_phase=req.fsm_phase,
        macro_structure_ok=req.macro_structure_ok,
        sar_adx_blocking=req.sar_adx_blocking,
        range_to_atr=req.range_to_atr,
        performance_context=req.performance_context,
    )

    valid_until = datetime.now(timezone.utc) + timedelta(hours=1)

    if risk_opinion.verdict == Verdict.REJECT:
        return StrategicBiasResponse(
            symbol=req.symbol,
            bias=StrategicBiasMode.HOLD,
            confidence=risk_opinion.confidence,
            reason=f"Risk gate blocked: {risk_opinion.reason}",
            max_lot_fraction=0.0,
            valid_until=valid_until,
        )

    # Lot fraction: HOLD from risk → cautious (0.5), APPROVE → full (1.0)
    lot_fraction = 1.0 if risk_opinion.verdict == Verdict.APPROVE else 0.5

    # 2. Evaluate both directions in parallel
    buy_trend, sell_trend = await asyncio.gather(
        evaluate_trend(
            direction="BUY",
            trend_h1=req.trend_h1,
            trend_h4=req.trend_h4,
            symbol=req.symbol,
            rag_context=rag_context,
            bias=1,
            current_clv=req.current_clv,
            macro_structure_ok=req.macro_structure_ok,
            trend_direction=req.trend_direction,
        ),
        evaluate_trend(
            direction="SELL",
            trend_h1=req.trend_h1,
            trend_h4=req.trend_h4,
            symbol=req.symbol,
            rag_context=rag_context,
            bias=-1,
            current_clv=req.current_clv,
            macro_structure_ok=req.macro_structure_ok,
            trend_direction=req.trend_direction,
        ),
    )

    buy_ok = buy_trend.verdict == Verdict.APPROVE
    sell_ok = sell_trend.verdict == Verdict.APPROVE

    if buy_ok and not sell_ok:
        return StrategicBiasResponse(
            symbol=req.symbol,
            bias=StrategicBiasMode.BUY_MODE,
            confidence=round(min(buy_trend.confidence, 0.95), 2),
            reason=f"Macro BUY: {buy_trend.reason}",
            max_lot_fraction=lot_fraction,
            valid_until=valid_until,
        )

    if sell_ok and not buy_ok:
        return StrategicBiasResponse(
            symbol=req.symbol,
            bias=StrategicBiasMode.SELL_MODE,
            confidence=round(min(sell_trend.confidence, 0.95), 2),
            reason=f"Macro SELL: {sell_trend.reason}",
            max_lot_fraction=lot_fraction,
            valid_until=valid_until,
        )

    return StrategicBiasResponse(
        symbol=req.symbol,
        bias=StrategicBiasMode.HOLD,
        confidence=0.55,
        reason=(
            f"No directional edge. BUY({buy_trend.verdict.value}): {buy_trend.reason} | "
            f"SELL({sell_trend.verdict.value}): {sell_trend.reason}"
        ),
        max_lot_fraction=0.0,
        valid_until=valid_until,
    )


@app.post("/api/v1/strategic-bias", response_model=StrategicBiasResponse, tags=["Strategic"])
async def get_strategic_bias(request: StrategicBiasRequest):
    """H1 macro directional mandate for Java scheduler.

    Called periodically (not per tick) by the Java bot to pre-approve a
    directional bias. The result is cached in Java and used to fast-gate
    incoming tactical signals without a synchronous Orion call.
    """
    try:
        return await _compute_strategic_bias(request)
    except Exception as exc:
        logger.error("strategic-bias failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/v1/consult", response_model=CommitteeVerdict, tags=["Committee"])
async def consult_committee(payload: Any = Body(...)):
    """
    Consult the full Expert Committee.

    Receives a trading signal from the Java bot via n8n, runs all 3 experts,
    and returns the consolidated verdict.
    """
    started = perf_counter()
    signal = _parse_signal_request(payload)
    LAST_CONSULT_TIMESTAMP.set(datetime.now(timezone.utc).timestamp())
    CONSULT_REQUESTS_TOTAL.labels(
        symbol=signal.symbol,
        direction=signal.direction.value,
    ).inc()

    verdict = await _build_committee_verdict(signal)
    latency_ms = round((perf_counter() - started) * 1000)

    rr = _compute_rr(signal.entry_price, signal.stop_loss, signal.take_profit)
    votes = {op.expert.value: op.verdict.value for op in verdict.opinions}
    policy = _voting_policy(verdict.approved_count, verdict.rejected_count, verdict.final_verdict.value)
    blockers = _dominant_blockers(signal, verdict.opinions)

    # Deterministic pre-LLM gate summary (logged inline for fast triage)
    sl_tp_valid = (
        signal.stop_loss > 0
        and signal.take_profit > 0
        and signal.stop_loss != signal.entry_price
        and signal.take_profit != signal.entry_price
    )
    logger.info(
        "committee.verdict %s",
        json.dumps(
            {
                "event": "committee.verdict",
                "trace_id": signal.trace_id or "n/a",
                "symbol": signal.symbol,
                "side": signal.direction.value,
                "entry": signal.entry_price,
                "sl": signal.stop_loss,
                "tp": signal.take_profit,
                "rr": rr,
                "equity": signal.equity,
                "fsm_phase": signal.fsm_phase,
                "entry_window_open": signal.entry_window_open,
                "sar_adx_signal": signal.sar_adx_signal,
                "sar_adx_blocking": signal.sar_adx_blocking,
                "macro_structure_ok": signal.macro_structure_ok,
                "sl_tp_valid": sl_tp_valid,
                "votes": votes,
                "final": verdict.final_verdict.value,
                "policy": policy,
                "dominant_blockers": blockers,
                "latency_ms": latency_ms,
            },
            ensure_ascii=False,
        ),
    )

    CONSULT_VERDICTS_TOTAL.labels(final_verdict=verdict.final_verdict.value).inc()
    CONSULT_LATENCY.observe(latency_ms / 1000)

    return verdict


@app.post(
    "/api/v1/consult/{expert_name}",
    response_model=ExpertOpinion,
    tags=["Individual Experts"],
)
async def consult_expert(expert_name: ExpertName, signal: Any = Body(...)):
    """
    Consult a single expert by name.

    Supported names: risk_manager, trend_analyzer, pattern_expert.
    """
    signal = _parse_signal_request(signal)

    logger.info(
        "individual consult expert=%s trace=%s symbol=%s dir=%s ctx=%s",
        expert_name.value,
        signal.trace_id or "n/a",
        signal.symbol,
        signal.direction.value,
        _signal_log_context(signal),
    )

    rag_context = await rag_client.fetch_rag_memory(signal.symbol)
    
    if expert_name == ExpertName.RISK_MANAGER:
        return await evaluate_risk(
            symbol=signal.symbol,
            equity=signal.equity,
            balance=signal.balance,
            current_volatility=signal.current_volatility,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            rag_context=rag_context,
            fsm_phase=signal.fsm_phase,
            macro_structure_ok=signal.macro_structure_ok,
            sar_adx_blocking=signal.sar_adx_blocking,
            range_to_atr=signal.range_to_atr,
            account_context=signal.account_context,
            performance_context=signal.performance_context,
        )
    elif expert_name == ExpertName.TREND_ANALYZER:
        return await evaluate_trend(
            symbol=signal.symbol,
            direction=signal.direction.value,
            trend_h1=signal.trend_h1,
            trend_h4=signal.trend_h4,
            rag_context=rag_context,
            bias=signal.bias,
            current_clv=signal.current_clv,
            previous_clv=signal.previous_clv,
            entry_window_open=signal.entry_window_open,
            macro_structure_ok=signal.macro_structure_ok,
        )
    elif expert_name == ExpertName.PATTERN_EXPERT:
        return await evaluate_pattern(
            symbol=signal.symbol,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            current_volatility=signal.current_volatility,
            direction=signal.direction.value,
            rag_context=rag_context,
            step_index_type=signal.step_index_type,
            bb_kc_ratio=signal.bb_kc_ratio,
            range_to_atr=signal.range_to_atr,
            sar_adx_signal=signal.sar_adx_signal,
            decision_context=signal.decision_context,
        )
    else:
        raise HTTPException(status_code=404, detail=f"Expert '{expert_name}' not found")


@app.get("/api/n8n/health", tags=["n8n"])
async def n8n_health():
    """Health check endpoint dedicated to n8n workflows."""
    return {
        "status": "UP",
        "service": "orion-consultant",
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
    }


@app.post("/api/n8n/enrich", tags=["n8n"])
async def enrich_data(data: dict[str, Any]):
    """Simple enrichment endpoint for n8n HTTP Request nodes."""
    logger.info("🔄 n8n enrich request received.")
    return {
        "original": data,
        "enriched": True,
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "source": "orion-consultant",
    }


@app.post("/api/n8n/process-event", tags=["n8n"])
async def process_n8n_event(event: Any = Body(...)):
    """
    n8n-facing process-event facade.

    Orion owns the n8n integration boundary and forwards the final event to the
    configured upstream Java service, keeping n8n-specific coupling out of the
    central trading service.
    """
    event_payload = _coerce_json_object(event)
    started = perf_counter()
    upstream_url = _join_url(settings.java_bot_url, settings.java_process_event_path)

    logger.info(
        "process-event received upstream=%s action=%s trace=%s symbol=%s",
        upstream_url,
        event_payload.get("action"),
        get_nested(event_payload, "signal", "traceId") or get_nested(event_payload, "signal", "trace_id"),
        get_nested(event_payload, "signal", "symbol"),
    )

    try:
        upstream_response = await _post_json(upstream_url, event_payload, timeout=15.0)
    except httpx.HTTPError as exc:
        JAVA_PROCESS_EVENT_TOTAL.labels(status="fallback").inc()
        RELAY_REQUESTS_TOTAL.labels(
            route="process_event",
            target="java",
            status="fallback",
        ).inc()
        logger.warning(
            "process-event fallback upstream=%s action=%s detail=%s",
            upstream_url,
            event_payload.get("action"),
            str(exc),
        )
        return JSONResponse(
            status_code=202,
            content={
                "processed": False,
                "forwarded": False,
                "status": "accepted_but_not_forwarded",
                "upstream": upstream_url,
                "detail": str(exc),
            },
        )
    finally:
        RELAY_LATENCY.labels(route="process_event", target="java").observe(
            perf_counter() - started
        )

    JAVA_PROCESS_EVENT_TOTAL.labels(status="success").inc()
    RELAY_REQUESTS_TOTAL.labels(route="process_event", target="java", status="success").inc()
    logger.info(
        "process-event success upstream=%s status=%s response_keys=%s",
        upstream_url,
        upstream_response.get("status"),
        sorted(upstream_response.keys()) if isinstance(upstream_response, dict) else [],
    )
    return upstream_response


@app.post("/api/n8n/trigger-workflow", tags=["n8n"])
async def trigger_n8n_workflow(request: dict[str, Any]):
    """Trigger any n8n webhook path through Orion's facade."""
    webhook_path = str(request.get("webhookPath", "/webhook/default"))
    payload = request.get("payload", {})
    url = _join_url(settings.n8n_base_url, webhook_path)
    started = perf_counter()

    logger.info(
        "trigger-workflow path=%s target=%s payload_keys=%s",
        webhook_path,
        url,
        sorted(payload.keys()) if isinstance(payload, dict) else [],
    )

    try:
        result = await _post_json(url, payload, timeout=10.0)
    except httpx.HTTPError as exc:
        RELAY_REQUESTS_TOTAL.labels(
            route="trigger_workflow",
            target="n8n",
            status="error",
        ).inc()
        logger.exception("❌ Failed to trigger n8n workflow.")
        raise HTTPException(
            status_code=502,
            detail=f"n8n workflow trigger failed: {exc}",
        ) from exc
    finally:
        RELAY_LATENCY.labels(route="trigger_workflow", target="n8n").observe(
            perf_counter() - started
        )

    RELAY_REQUESTS_TOTAL.labels(
        route="trigger_workflow",
        target="n8n",
        status="success",
    ).inc()
    logger.info(
        "trigger-workflow success path=%s result_keys=%s",
        webhook_path,
        sorted(result.keys()) if isinstance(result, dict) else [],
    )
    return result


@app.get("/api/n8n/n8n-status", tags=["n8n"])
async def n8n_status():
    """Check if the configured n8n instance is reachable."""
    health_url = _join_url(settings.n8n_base_url, settings.n8n_health_path)

    try:
        await _get_text(health_url, timeout=10.0)
        available = True
    except httpx.HTTPError:
        available = False

    N8N_HEALTHCHECK_TOTAL.labels(status="up" if available else "down").inc()
    logger.info("n8n-status available=%s health_url=%s", available, health_url)

    return {
        "n8n_available": available,
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
    }


@app.post("/api/agent/chat", tags=["n8n"])
async def agent_chat(request: dict[str, Any]):
    """Proxy chat requests to an n8n AI agent webhook."""
    message = str(request.get("message", "")).strip()
    if not message:
        raise HTTPException(status_code=422, detail="Field 'message' is required")

    if not settings.n8n_agent_chat_enabled:
        AGENT_CHAT_REQUESTS_TOTAL.labels(status="disabled").inc()
        RELAY_REQUESTS_TOTAL.labels(route="agent_chat", target="n8n", status="disabled").inc()
        logger.info(
            "agent-chat disabled path=%s message_len=%d",
            settings.n8n_agent_chat_webhook_path,
            len(message),
        )
        return {
            "success": False,
            "agent": "orion-local-fallback",
            "message": message,
            "reply": "n8n agent chat is disabled in this deployment.",
            "disabled": True,
        }

    webhook_url = _join_url(settings.n8n_base_url, settings.n8n_agent_chat_webhook_path)
    started = perf_counter()
    logger.info(
        "agent-chat proxy target=%s message_len=%d",
        webhook_url,
        len(message),
    )

    try:
        result = await _post_json(
            webhook_url,
            {"message": message},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        AGENT_CHAT_REQUESTS_TOTAL.labels(status="error").inc()
        RELAY_REQUESTS_TOTAL.labels(route="agent_chat", target="n8n", status="error").inc()
        logger.exception("❌ Failed to proxy agent chat request to n8n.")
        raise HTTPException(
            status_code=502,
            detail=f"n8n agent webhook failed: {exc}",
        ) from exc
    finally:
        AGENT_CHAT_LATENCY.observe(perf_counter() - started)

    AGENT_CHAT_REQUESTS_TOTAL.labels(status="success").inc()
    RELAY_REQUESTS_TOTAL.labels(route="agent_chat", target="n8n", status="success").inc()
    logger.info(
        "agent-chat success target=%s response_keys=%s",
        webhook_url,
        sorted(result.keys()) if isinstance(result, dict) else [],
    )
    return result


@app.post("/api/notifications/trading-decision", status_code=202, tags=["n8n"])
async def notify_trading_decision(payload: dict[str, Any]):
    """Forward trading-decision notifications to the configured n8n webhook."""
    webhook_url = _join_url(settings.n8n_base_url, "/webhook/trading-decision")
    try:
        await _post_without_response(webhook_url, payload, timeout=10.0)
    except httpx.HTTPError as exc:
        logger.exception("Failed to forward trading-decision notification to n8n.")
        raise HTTPException(status_code=502, detail=f"n8n notification webhook failed: {exc}") from exc
    return {"accepted": True}


@app.post("/api/notifications/trading-error", status_code=202, tags=["n8n"])
async def notify_trading_error(payload: dict[str, Any]):
    """Forward trading-error notifications to the configured n8n webhook."""
    webhook_url = _join_url(settings.n8n_base_url, "/webhook/trading-error")
    try:
        await _post_without_response(webhook_url, payload, timeout=10.0)
    except httpx.HTTPError as exc:
        logger.exception("Failed to forward trading-error notification to n8n.")
        raise HTTPException(status_code=502, detail=f"n8n notification webhook failed: {exc}") from exc
    return {"accepted": True}


@app.get("/api/agent/health", tags=["n8n"])
async def agent_health():
    """Health check for the n8n-backed agent webhook."""
    if not settings.n8n_agent_chat_enabled:
        return {
            "agent_available": False,
            "agent_enabled": False,
            "webhook_path": settings.n8n_agent_chat_webhook_path,
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
