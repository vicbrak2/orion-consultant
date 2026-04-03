"""
🌌 Orion Consultant — FastAPI Application.

REST API for n8n webhooks and external integrations.
MCP Server is mounted at /mcp for Streamable HTTP transport.

Usage:
    uvicorn main:app --reload --port 8100
"""

from __future__ import annotations

import json
import sys
import os
import logging
import asyncio
from time import perf_counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import ValidationError
from prometheus_fastapi_instrumentator import Instrumentator
# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agents.rag_client as rag_client

from fastapi import Body, FastAPI, HTTPException, Response
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

# Build the MCP ASGI app once so its routes and lifespan can be attached
# to the main FastAPI app without double-prefixing the transport path.
mcp_http_app = mcp_server_instance.streamable_http_app()


# ── Lifespan ──────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp_http_app.router.lifespan_context(mcp_http_app):
        ORION_INFO.info({"version": app.version, "environment": os.getenv("SPRING_PROFILES_ACTIVE", "standalone")})
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

# ── Attach MCP Server routes (StreamableHTTP) ────────
# n8n MCP Client connects to: http://orion-consultant:8100/mcp/
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

    logger.info(
        "committee opinions trace=%s -> %s | %s | %s",
        signal.trace_id or "n/a",
        _format_expert_opinion(opinions[0]),
        _format_expert_opinion(opinions[1]),
        _format_expert_opinion(opinions[2]),
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
    """Join a base URL with a relative path or return absolute paths untouched."""
    if path.startswith("http://") or path.startswith("https://"):
        return path
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
        "confirmations": sorted(confirmations.keys()) if confirmations else [],
    }


def _format_expert_opinion(opinion: ExpertOpinion) -> str:
    return (
        f"{opinion.expert.value}={opinion.verdict.value}"
        f"({opinion.confidence:.2f}) reason={opinion.reason[:180]}"
    )


# ── Endpoints ─────────────────────────────────────────


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Health check endpoint."""
    return HealthResponse()


@app.get("/actuator/health", response_model=HealthResponse, tags=["System"])
async def actuator_health_check():
    """Spring Actuator compatible health alias for Java clients."""
    return HealthResponse()


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

    logger.info(
        "committee request trace=%s symbol=%s dir=%s entry=%.2f sl=%.2f tp=%.2f equity=%.2f balance=%.2f ctx=%s",
        signal.trace_id or "n/a",
        signal.symbol,
        signal.direction.value,
        signal.entry_price,
        signal.stop_loss,
        signal.take_profit,
        signal.equity,
        signal.balance,
        _signal_log_context(signal),
    )

    verdict = await _build_committee_verdict(signal)

    logger.info(
        "committee verdict trace=%s final=%s approved=%d rejected=%d summary=%s",
        signal.trace_id or "n/a",
        verdict.final_verdict.value,
        verdict.approved_count,
        verdict.rejected_count,
        verdict.summary,
    )
    CONSULT_VERDICTS_TOTAL.labels(final_verdict=verdict.final_verdict.value).inc()
    CONSULT_LATENCY.observe(perf_counter() - started)

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

    available = False
    try:
        await _get_text(
            _join_url(settings.n8n_base_url, settings.n8n_health_path),
            timeout=10.0,
        )
        available = True
    except httpx.HTTPError:
        available = False

    return {
        "agent_available": available,
        "agent_enabled": True,
        "webhook_path": settings.n8n_agent_chat_webhook_path,
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
    }


async def _forward_notification(
    payload: dict[str, Any],
    webhook_path: str,
    notification_type: str,
) -> Response:
    """Forward a NotificationPort-compatible payload to the configured n8n webhook."""
    url = _join_url(settings.n8n_base_url, webhook_path)
    started = perf_counter()
    logger.info(
        "notification relay type=%s target=%s payload_keys=%s",
        notification_type,
        url,
        sorted(payload.keys()) if isinstance(payload, dict) else [],
    )

    try:
        await _post_without_response(
            url,
            payload,
            timeout=settings.notification_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        NOTIFICATION_REQUESTS_TOTAL.labels(
            notification_type=notification_type,
            status="error",
        ).inc()
        RELAY_REQUESTS_TOTAL.labels(
            route="notification",
            target="n8n",
            status="error",
        ).inc()
        logger.exception("❌ Failed to forward notification to n8n: %s", webhook_path)
        raise HTTPException(
            status_code=502,
            detail=f"Notification forwarding failed for '{webhook_path}': {exc}",
        ) from exc
    finally:
        RELAY_LATENCY.labels(route="notification", target="n8n").observe(
            perf_counter() - started
        )

    RELAY_REQUESTS_TOTAL.labels(
        route="notification",
        target="n8n",
        status="success",
    ).inc()
    NOTIFICATION_REQUESTS_TOTAL.labels(
        notification_type=notification_type,
        status="success",
    ).inc()
    logger.info("notification relay success type=%s target=%s", notification_type, url)
    return Response(status_code=202)


@app.post("/api/notifications/trading-decision", status_code=202, tags=["Notifications"])
async def notify_trading_decision(payload: dict[str, Any]):
    """Receive trading-decision notifications from the Java service and relay to n8n."""
    return await _forward_notification(
        payload,
        settings.n8n_trading_decision_webhook_path,
        "trading-decision",
    )


@app.post("/api/notifications/trade-executed", status_code=202, tags=["Notifications"])
async def notify_trade_executed(payload: dict[str, Any]):
    """Receive trade-executed notifications from the Java service and relay to n8n."""
    return await _forward_notification(
        payload,
        settings.n8n_trade_executed_webhook_path,
        "trade-executed",
    )


@app.post("/api/notifications/trade-closed", status_code=202, tags=["Notifications"])
async def notify_trade_closed(payload: dict[str, Any]):
    """Receive trade-closed notifications from the Java service and relay to n8n."""
    return await _forward_notification(
        payload,
        settings.n8n_trade_closed_webhook_path,
        "trade-closed",
    )


@app.post("/api/notifications/trading-error", status_code=202, tags=["Notifications"])
async def notify_trading_error(payload: dict[str, Any]):
    """Receive trading errors from the Java service and relay to n8n."""
    return await _forward_notification(
        payload,
        settings.n8n_trading_error_webhook_path,
        "trading-error",
    )


@app.post("/api/notifications/performance-metrics", status_code=202, tags=["Notifications"])
async def notify_performance_metrics(payload: dict[str, Any]):
    """Receive performance metrics from the Java service and relay to n8n."""
    return await _forward_notification(
        payload,
        settings.n8n_performance_metrics_webhook_path,
        "performance-metrics",
    )


# ── Start function (for pyproject.toml script) ───────


def start():
    """Entry point for `orion-api` CLI script."""
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=False,
    )


if __name__ == "__main__":
    start()
