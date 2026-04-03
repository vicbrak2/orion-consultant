"""
🛡️ Risk Manager — Experto en drawdown, volatilidad y gestión de capital.

Evalúa si las condiciones de la cuenta permiten operar con seguridad.
Enriched: usa fsm_phase, sar_adx_blocking, range_to_atr y account_context.
"""

from __future__ import annotations

import json
import logging

import agents.rag_client as rag_client
from config import settings
from models.schemas import ExpertOpinion, ExpertName, Verdict
from utils.context_extractors import get_episode_checkpoints, get_episode_context

logger = logging.getLogger(__name__)


def _classic_risk_assessment(
    equity: float,
    balance: float,
    current_volatility: float,
    entry_price: float,
    stop_loss: float,
    symbol: str,
    *,
    fsm_phase: str | None = None,
    macro_structure_ok: bool | None = None,
    sar_adx_blocking: bool | None = None,
    range_to_atr: float | None = None,
    account_context: dict | None = None,
    episode_context: dict | None = None,
    episode_checkpoints: list[dict] | None = None,
) -> tuple[Verdict, float, str]:
    reasons: list[str] = []
    risk_score: float = 0.0

    if balance > 0:
        drawdown = (balance - equity) / balance
    else:
        drawdown = 0.0

    if drawdown > settings.max_drawdown:
        reasons.append(
            f"Drawdown ({drawdown:.2%}) excede el umbral máximo ({settings.max_drawdown:.2%})."
        )
        risk_score += 0.5
    elif drawdown > settings.max_drawdown * 0.7:
        reasons.append(
            f"Drawdown ({drawdown:.2%}) se acerca al umbral. Precaución."
        )
        risk_score += 0.2

    multiplier = 10.0 if "step" in symbol.lower() else 1.0
    adjusted_max_volatility = settings.max_volatility * multiplier

    if current_volatility > adjusted_max_volatility:
        reasons.append(
            f"Volatilidad ({current_volatility:.1f}) excede el máximo ({adjusted_max_volatility:.1f})."
        )
        risk_score += 0.4
    elif current_volatility > adjusted_max_volatility * 0.8:
        reasons.append(
            f"Volatilidad ({current_volatility:.1f}) elevada. Monitorear."
        )
        risk_score += 0.15

    if entry_price > 0 and stop_loss > 0:
        risk_distance = abs(entry_price - stop_loss)
        if risk_distance > entry_price * 0.02:
            reasons.append(
                f"Distancia de stop-loss ({risk_distance:.2f}) demasiado amplia."
            )
            risk_score += 0.2

    # ── Enrichment: SAR+ADX blocking ──────────────────────
    if sar_adx_blocking is True:
        reasons.append("SAR+ADX bloquea la entrada — señal técnica en contra.")
        risk_score += 0.35

    # ── Enrichment: range_to_atr (noise/gas) ──────────────
    if range_to_atr is not None:
        if range_to_atr < 0.8:
            reasons.append(f"range_to_atr bajo ({range_to_atr:.2f}) — mercado ruidoso, riesgo elevado.")
            risk_score += 0.15

    # ── Enrichment: open trades (account_context) ─────────
    if account_context is not None:
        open_trades = account_context.get("open_trades", 0)
        if isinstance(open_trades, (int, float)) and open_trades >= 2:
            reasons.append(f"Ya hay {int(open_trades)} trades abiertos. Exposición acumulada.")
            risk_score += 0.2

    episode_state = None
    if episode_context is not None:
        episode_state = episode_context.get("state")
        if isinstance(episode_state, str) and episode_state.upper() in {"DEGRADED", "INVALIDATED"}:
            reasons.append(f"Episodio en estado {episode_state}.")
            risk_score += 0.20

    checkpoints = [cp for cp in (episode_checkpoints or []) if isinstance(cp, dict)]
    if checkpoints:
        latest = checkpoints[-1]
        latest_pnl = latest.get("pnl")
        if isinstance(latest_pnl, (int, float)) and latest_pnl < 0:
            reasons.append(f"Checkpoint reciente con PnL negativo ({latest_pnl:.2f}).")
            risk_score += 0.10
        if latest.get("macro_structure_ok") is False:
            reasons.append("Checkpoint reciente sin estructura macro válida.")
            risk_score += 0.10

    # ── Enrichment: macro_structure + fsm_phase (reducer) ─
    # If FSM is in TREND phase with macro structure OK, reduce risk slightly
    fsm_bonus = 0.0
    if fsm_phase is not None and macro_structure_ok is not None:
        if fsm_phase.upper() == "TREND" and macro_structure_ok:
            fsm_bonus = -0.15
            reasons.append("FSM en TREND + estructura macro OK. Riesgo contextual reducido.")
        elif fsm_phase.upper() in ("SOLID", "GAS"):
            reasons.append(f"FSM en fase {fsm_phase.upper()} — sesgo neutral.")

    risk_score = max(0.0, risk_score + fsm_bonus)

    # ── Final verdict ─────────────────────────────────────
    if risk_score >= 0.5:
        verdict = Verdict.REJECT
        confidence = min(1.0, 0.6 + risk_score * 0.3)
    elif risk_score >= 0.2:
        verdict = Verdict.HOLD
        confidence = 0.6
    else:
        verdict = Verdict.APPROVE
        confidence = max(0.7, 1.0 - risk_score)
        if not reasons:
            reasons.append("Cuenta saludable. Riesgo bajo control.")

    return verdict, round(confidence, 2), " | ".join(reasons)


async def evaluate_risk(
    equity: float,
    balance: float,
    current_volatility: float,
    entry_price: float = 0.0,
    stop_loss: float = 0.0,
    symbol: str = "EURUSD",
    rag_context: str = "",
    *,
    fsm_phase: str | None = None,
    macro_structure_ok: bool | None = None,
    sar_adx_blocking: bool | None = None,
    range_to_atr: float | None = None,
    account_context: dict | None = None,
    episode_context: dict | None = None,
    episode_checkpoints: list[dict] | None = None,
) -> ExpertOpinion:
    """
    Validates whether it is safe to trade based on account health.

    When `ORION_ENABLE_EXPERT_LLM=true`, the classical assessment is sent to the
    LLM as context; otherwise the function stays fully deterministic.
    """
    classic_verdict, classic_confidence, classic_reason = _classic_risk_assessment(
        equity=equity,
        balance=balance,
        current_volatility=current_volatility,
        entry_price=entry_price,
        stop_loss=stop_loss,
        symbol=symbol,
        fsm_phase=fsm_phase,
        macro_structure_ok=macro_structure_ok,
        sar_adx_blocking=sar_adx_blocking,
        range_to_atr=range_to_atr,
        account_context=account_context,
        episode_context=episode_context,
        episode_checkpoints=episode_checkpoints,
    )

    if not settings.enable_expert_llm:
        return ExpertOpinion(
            expert=ExpertName.RISK_MANAGER,
            verdict=classic_verdict,
            confidence=classic_confidence,
            reason=classic_reason,
        )

    drawdown = (balance - equity) / balance if balance > 0 else 0.0
    system_prompt = """
    Eres el RISK MANAGER del Orion Committee.
    Analizas la salud de la cuenta y el riesgo operativo.
    Evalúa el veredicto clásico y el contexto RAG.
    La clave "decision" debe ser exactamente una de estas:
    APPROVE, REJECT, HOLD.
    Responde estrictamente un JSON puro.
    Ejemplo valido:
    {"decision": "HOLD", "confidence": 0.85, "reason": "Justificacion breve."}
    """
    user_prompt = f"""
    === ESTADO MATEMÁTICO DE LA CUENTA ===
    Symbol: {symbol}
    Drawdown: {drawdown:.2%}
    Volatilidad Actual: {current_volatility:.1f}
    FSM Phase: {fsm_phase}
    SAR+ADX Blocking: {sar_adx_blocking}
    Macro Structure OK: {macro_structure_ok}
    Range to ATR: {range_to_atr}
    Open Trades: {account_context.get('open_trades', 'N/A') if account_context else 'N/A'}
    Episode state: {episode_context.get('state', 'N/A') if episode_context else 'N/A'}
    Checkpoints: {episode_checkpoints}
    Cálculo Clásico: {classic_verdict.value}
    Razones Matemáticas: {classic_reason}

    === CONTEXTO HISTÓRICO RAG ===
    {rag_context}
    """

    try:
        client = rag_client.get_groq_client()
        response = await client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model="llama-3.1-8b-instant",
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        parsed = json.loads(response.choices[0].message.content)
        decision = str(parsed.get("decision", classic_verdict.value)).upper()
        verdict = Verdict(decision) if decision in {v.value for v in Verdict} else classic_verdict
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", classic_confidence))))
        reason = str(parsed.get("reason", classic_reason))
        return ExpertOpinion(
            expert=ExpertName.RISK_MANAGER,
            verdict=verdict,
            confidence=round(confidence, 2),
            reason=f"[LLM] {reason}",
        )
    except Exception as exc:
        logger.warning("Risk Manager LLM fallback triggered: %s", exc, exc_info=True)
        return ExpertOpinion(
            expert=ExpertName.RISK_MANAGER,
            verdict=classic_verdict,
            confidence=classic_confidence,
            reason=classic_reason,
        )
