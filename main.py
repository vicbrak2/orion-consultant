"""
🌌 Orion Consultant — FastAPI Application.

REST API for n8n webhooks and external integrations.
MCP Server is mounted at /mcp for Streamable HTTP transport.

Usage:
    uvicorn main:app --reload --port 8100
"""

from __future__ import annotations

import sys
import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException, Response
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

# ── Attach MCP Server routes (StreamableHTTP) ────────
# n8n MCP Client connects to: http://orion-consultant:8100/mcp/
app.router.routes.extend(mcp_http_app.routes)


# ── Helpers ───────────────────────────────────────────


def _build_committee_verdict(signal: SignalRequest) -> CommitteeVerdict:
    """Run all 3 experts and consolidate the result."""

    opinions = [
        evaluate_risk(
            symbol=signal.symbol,
            equity=signal.equity,
            balance=signal.balance,
            current_volatility=signal.current_volatility,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
        ),
        evaluate_trend(
            symbol=signal.symbol,
            direction=signal.direction.value,
            trend_h1=signal.trend_h1,
            trend_h4=signal.trend_h4,
        ),
        evaluate_pattern(
            symbol=signal.symbol,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            current_volatility=signal.current_volatility,
            direction=signal.direction.value,
        ),
    ]

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
async def consult_committee(signal: SignalRequest):
    """
    Consult the full Expert Committee.

    Receives a trading signal from the Java bot via n8n, runs all 3 experts,
    and returns the consolidated verdict.
    """
    logger.info(
        "📥 Signal received: %s %s @ %.2f",
        signal.direction.value,
        signal.symbol,
        signal.entry_price,
    )

    verdict = _build_committee_verdict(signal)

    logger.info(
        "📤 Verdict: %s — %s",
        verdict.final_verdict.value,
        verdict.summary,
    )

    return verdict


@app.post(
    "/api/v1/consult/{expert_name}",
    response_model=ExpertOpinion,
    tags=["Individual Experts"],
)
async def consult_expert(expert_name: ExpertName, signal: SignalRequest):
    """
    Consult a single expert by name.

    Supported names: risk_manager, trend_analyzer, pattern_expert.
    """
    logger.info("📥 Individual consult: %s", expert_name.value)

    if expert_name == ExpertName.RISK_MANAGER:
        return evaluate_risk(
            symbol=signal.symbol,
            equity=signal.equity,
            balance=signal.balance,
            current_volatility=signal.current_volatility,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
        )
    elif expert_name == ExpertName.TREND_ANALYZER:
        return evaluate_trend(
            symbol=signal.symbol,
            direction=signal.direction.value,
            trend_h1=signal.trend_h1,
            trend_h4=signal.trend_h4,
        )
    elif expert_name == ExpertName.PATTERN_EXPERT:
        return evaluate_pattern(
            symbol=signal.symbol,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            current_volatility=signal.current_volatility,
            direction=signal.direction.value,
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
async def process_n8n_event(event: dict[str, Any]):
    """
    n8n-facing process-event facade.

    Orion owns the n8n integration boundary and forwards the final event to the
    configured upstream Java service, keeping n8n-specific coupling out of the
    central trading service.
    """
    logger.info("📨 n8n event received by Orion.")
    upstream_url = _join_url(settings.java_bot_url, settings.java_process_event_path)

    try:
        upstream_response = await _post_json(upstream_url, event, timeout=15.0)
    except httpx.HTTPError as exc:
        logger.exception("❌ Failed to forward n8n event to upstream Java service.")
        raise HTTPException(
            status_code=502,
            detail=f"Upstream Java event processing failed: {exc}",
        ) from exc

    return upstream_response


@app.post("/api/n8n/trigger-workflow", tags=["n8n"])
async def trigger_n8n_workflow(request: dict[str, Any]):
    """Trigger any n8n webhook path through Orion's facade."""
    webhook_path = str(request.get("webhookPath", "/webhook/default"))
    payload = request.get("payload", {})
    url = _join_url(settings.n8n_base_url, webhook_path)

    logger.info("🚀 Triggering n8n workflow via Orion: %s", webhook_path)

    try:
        result = await _post_json(url, payload, timeout=10.0)
    except httpx.HTTPError as exc:
        logger.exception("❌ Failed to trigger n8n workflow.")
        raise HTTPException(
            status_code=502,
            detail=f"n8n workflow trigger failed: {exc}",
        ) from exc

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

    webhook_url = _join_url(settings.n8n_base_url, settings.n8n_agent_chat_webhook_path)

    try:
        result = await _post_json(
            webhook_url,
            {"message": message},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        logger.exception("❌ Failed to proxy agent chat request to n8n.")
        raise HTTPException(
            status_code=502,
            detail=f"n8n agent webhook failed: {exc}",
        ) from exc

    return result


@app.get("/api/agent/health", tags=["n8n"])
async def agent_health():
    """Health check for the n8n-backed agent webhook."""
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
        "webhook_path": settings.n8n_agent_chat_webhook_path,
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
    }


async def _forward_notification(payload: dict[str, Any], webhook_path: str) -> Response:
    """Forward a NotificationPort-compatible payload to the configured n8n webhook."""
    url = _join_url(settings.n8n_base_url, webhook_path)

    try:
        await _post_without_response(
            url,
            payload,
            timeout=settings.notification_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        logger.exception("❌ Failed to forward notification to n8n: %s", webhook_path)
        raise HTTPException(
            status_code=502,
            detail=f"Notification forwarding failed for '{webhook_path}': {exc}",
        ) from exc

    return Response(status_code=202)


@app.post("/api/notifications/trading-decision", status_code=202, tags=["Notifications"])
async def notify_trading_decision(payload: dict[str, Any]):
    """Receive trading-decision notifications from the Java service and relay to n8n."""
    return await _forward_notification(
        payload,
        settings.n8n_trading_decision_webhook_path,
    )


@app.post("/api/notifications/trade-executed", status_code=202, tags=["Notifications"])
async def notify_trade_executed(payload: dict[str, Any]):
    """Receive trade-executed notifications from the Java service and relay to n8n."""
    return await _forward_notification(
        payload,
        settings.n8n_trade_executed_webhook_path,
    )


@app.post("/api/notifications/trade-closed", status_code=202, tags=["Notifications"])
async def notify_trade_closed(payload: dict[str, Any]):
    """Receive trade-closed notifications from the Java service and relay to n8n."""
    return await _forward_notification(
        payload,
        settings.n8n_trade_closed_webhook_path,
    )


@app.post("/api/notifications/trading-error", status_code=202, tags=["Notifications"])
async def notify_trading_error(payload: dict[str, Any]):
    """Receive trading errors from the Java service and relay to n8n."""
    return await _forward_notification(
        payload,
        settings.n8n_trading_error_webhook_path,
    )


@app.post("/api/notifications/performance-metrics", status_code=202, tags=["Notifications"])
async def notify_performance_metrics(payload: dict[str, Any]):
    """Receive performance metrics from the Java service and relay to n8n."""
    return await _forward_notification(
        payload,
        settings.n8n_performance_metrics_webhook_path,
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
