"""
🌌 Orion MCP Server — Exposes expert agents as MCP tools.

This is the entry point for the Model Context Protocol server.
n8n (or any MCP client) can discover and invoke these tools.

Usage:
    python mcp_server.py
    # or via the CLI:
    mcp run mcp_server:mcp
"""

from __future__ import annotations

import json
import sys
import os

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from agents.risk_manager import evaluate_risk
from agents.trend_analyzer import evaluate_trend
from agents.pattern_expert import evaluate_pattern
from models.schemas import Verdict

# ── MCP Server instance ──────────────────────────────

mcp = FastMCP(
    "Orion-Consultant",
    instructions=(
        "Comité de Expertos para decisiones de trading en Step Index. "
        "Provee análisis de riesgo, tendencia y patrones."
    ),
    host="0.0.0.0",
    streamable_http_path="/mcp/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "127.0.0.1",
            "127.0.0.1:*",
            "localhost",
            "localhost:*",
            "orion-consultant",
            "orion-consultant:*",
        ],
        allowed_origins=[
            "http://127.0.0.1",
            "http://127.0.0.1:*",
            "http://localhost",
            "http://localhost:*",
            "http://orion-consultant",
            "http://orion-consultant:*",
        ],
    ),
)


# ── Individual Expert Tools ──────────────────────────


@mcp.tool()
def validate_risk(
    equity: float,
    balance: float,
    current_volatility: float,
    entry_price: float = 0.0,
    stop_loss: float = 0.0,
) -> str:
    """
    🛡️ Risk Manager: Valida si el riesgo es aceptable para operar.

    Evalúa drawdown, volatilidad y distancia del stop-loss.
    Retorna un JSON con el veredicto, confianza y razón.
    """
    opinion = evaluate_risk(
        equity=equity,
        balance=balance,
        current_volatility=current_volatility,
        entry_price=entry_price,
        stop_loss=stop_loss,
    )
    return opinion.model_dump_json(indent=2)


@mcp.tool()
def analyze_trend(
    direction: str,
    trend_h1: str = "",
    trend_h4: str = "",
) -> str:
    """
    📈 Trend Analyzer: Analiza la estructura de tendencia multi-timeframe.

    Evalúa alineación entre la dirección de la señal y las tendencias H1/H4.
    Retorna un JSON con el veredicto, confianza y razón.
    """
    opinion = evaluate_trend(
        direction=direction,
        trend_h1=trend_h1 or None,
        trend_h4=trend_h4 or None,
    )
    return opinion.model_dump_json(indent=2)


@mcp.tool()
def detect_patterns(
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    current_volatility: float = 0.0,
    direction: str = "BUY",
) -> str:
    """
    🔍 Pattern Expert: Detecta patrones del Step Index.

    Analiza R:R ratio, zonas de spike, consolidaciones y geometría del trade.
    Retorna un JSON con el veredicto, confianza y razón.
    """
    opinion = evaluate_pattern(
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        current_volatility=current_volatility,
        direction=direction,
    )
    return opinion.model_dump_json(indent=2)


# ── Committee Tool (all 3 experts) ───────────────────


@mcp.tool()
def consult_committee(
    equity: float,
    balance: float,
    current_volatility: float,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    trend_h1: str = "",
    trend_h4: str = "",
) -> str:
    """
    🌌 Consulta al Comité de Expertos completo.

    Ejecuta los 3 agentes (Risk, Trend, Pattern) y consolida sus opiniones
    en un veredicto final. Utiliza votación por mayoría.

    Retorna un JSON con el veredicto final, conteo de votos,
    opiniones individuales y resumen.
    """
    # Gather opinions
    opinions = [
        evaluate_risk(
            equity=equity,
            balance=balance,
            current_volatility=current_volatility,
            entry_price=entry_price,
            stop_loss=stop_loss,
        ),
        evaluate_trend(
            direction=direction,
            trend_h1=trend_h1 or None,
            trend_h4=trend_h4 or None,
        ),
        evaluate_pattern(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            current_volatility=current_volatility,
            direction=direction,
        ),
    ]

    # Tally votes
    approved = sum(1 for o in opinions if o.verdict == Verdict.APPROVE)
    rejected = sum(1 for o in opinions if o.verdict == Verdict.REJECT)

    # Majority vote
    if approved >= 2:
        final = Verdict.APPROVE
        summary = f"{approved}/3 expertos aprobaron. Operación autorizada."
    elif rejected >= 2:
        final = Verdict.REJECT
        summary = f"{rejected}/3 expertos rechazaron. Operación denegada."
    else:
        final = Verdict.HOLD
        summary = "Sin consenso claro. Se recomienda esperar confirmación."

    result = {
        "final_verdict": final.value,
        "approved_count": approved,
        "rejected_count": rejected,
        "opinions": [json.loads(o.model_dump_json()) for o in opinions],
        "summary": summary,
    }

    return json.dumps(result, indent=2, ensure_ascii=False)


# ── Entry point ──────────────────────────────────────


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
