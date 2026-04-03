"""
🔰 Pattern Expert — Experto en geometría de precio, volatilidad y setup.

Evalúa la calidad de la señal por la geometría de entrada (RR ratio, momentum).
Enriched: usa step_index_type, bb_kc_ratio, range_to_atr, sar_adx_signal, decision_context.
"""

from __future__ import annotations

import json
import logging

import agents.rag_client as rag_client
from config import settings
from models.schemas import ExpertOpinion, ExpertName, SignalDirection, Verdict
from utils.context_extractors import (
    get_confirmations,
    get_nested,
    get_pattern_name,
    get_snapshot,
)

logger = logging.getLogger(__name__)


def _classic_pattern_assessment(
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    current_volatility: float,
    direction: str,
    symbol: str,
    *,
    step_index_type: str | None = None,
    bb_kc_ratio: float | None = None,
    range_to_atr: float | None = None,
    sar_adx_signal: int | None = None,
    decision_context: dict | None = None,
    range_to_atr_micro: float | None = None,
    bb_kc_ratio_macro: float | None = None,
    analysis_context: dict | None = None,
    confirmations: dict | None = None,
    episode_events: list[dict] | None = None,
) -> tuple[Verdict, float, str]:
    reasons: list[str] = []
    pattern_score: float = 0.0

    signal_dir = (direction or "").strip().upper()
    if signal_dir == SignalDirection.BUY:
        reward = take_profit - entry_price
        risk = entry_price - stop_loss
    elif signal_dir == SignalDirection.SELL:
        reward = entry_price - take_profit
        risk = stop_loss - entry_price
    else:
        return Verdict.HOLD, 0.5, f"Dirección no reconocida: {signal_dir}"

    if risk <= 0 or reward <= 0:
        return Verdict.REJECT, 0.9, "Setup inválido: SL/TP mal ubicados respecto a dirección."

    rr_ratio = reward / risk

    if rr_ratio >= 2.0:
        pattern_score += 0.5
        reasons.append(f"RR excelente ({rr_ratio:.2f}).")
    elif rr_ratio >= 1.5:
        pattern_score += 0.3
        reasons.append(f"RR aceptable ({rr_ratio:.2f}).")
    elif rr_ratio >= 1.0:
        pattern_score += 0.1
        reasons.append(f"RR mínimo ({rr_ratio:.2f}).")
    else:
        pattern_score -= 0.3
        reasons.append(f"RR desfavorable ({rr_ratio:.2f}). Riesgo asimétrico negativo.")

    # Momentum via entry_price position
    if signal_dir == SignalDirection.BUY:
        momentum = (entry_price - stop_loss) / (take_profit - stop_loss) if (take_profit - stop_loss) > 0 else 0.5
    else:
        momentum = (stop_loss - entry_price) / (stop_loss - take_profit) if (stop_loss - take_profit) > 0 else 0.5

    if momentum > 0.65:
        reasons.append(f"Momentum fuerte ({momentum:.2f}). Entrada temprana.")
        pattern_score += 0.15
    elif momentum < 0.35:
        reasons.append(f"Momentum débil ({momentum:.2f}). Entrada tardía.")
        pattern_score -= 0.1

    # Volatility context
    multiplier = 10.0 if "step" in symbol.lower() else 1.0
    adjusted_high_vol = settings.max_volatility * multiplier * 0.7

    if current_volatility > adjusted_high_vol:
        reasons.append(f"Volatilidad alta ({current_volatility:.1f}). Patrón podría fallar.")
        pattern_score -= 0.15

    # Spike zone detection: tight SL + high volatility
    risk_pct = abs(entry_price - stop_loss) / entry_price if entry_price > 0 else 0
    if risk_pct < 0.005 and current_volatility > 150 * multiplier:
        reasons.append(
            f"Zona de spike: SL muy ajustado ({risk_pct:.3%}) con volatilidad {current_volatility:.1f}."
        )
        pattern_score -= 0.15

    # Consolidation detection: low volatility
    consolidation_threshold = 30.0 * multiplier
    if current_volatility < consolidation_threshold:
        reasons.append(
            f"Posible consolidación: volatilidad baja ({current_volatility:.1f})."
        )
        pattern_score += 0.10

    # ── Enrichment: bb_kc_ratio (squeeze state) ───────────
    if bb_kc_ratio is not None:
        if bb_kc_ratio < 0.85:
            reasons.append(f"BB/KC squeeze confirmado ({bb_kc_ratio:.2f}). Alta probabilidad de breakout.")
            pattern_score += 0.20
        elif bb_kc_ratio < 1.0:
            reasons.append(f"BB/KC pre-squeeze ({bb_kc_ratio:.2f}). Setup se comprime.")
            pattern_score += 0.10
        elif bb_kc_ratio > 1.3:
            reasons.append(f"BB/KC expandido ({bb_kc_ratio:.2f}). Breakout ya ocurrió, posible late entry.")
            pattern_score -= 0.10
    if bb_kc_ratio_macro is not None:
        if bb_kc_ratio_macro < 0.9:
            reasons.append(f"BB/KC macro confirma squeeze ({bb_kc_ratio_macro:.2f}).")
            pattern_score += 0.10
        elif bb_kc_ratio_macro > 1.3:
            reasons.append(f"BB/KC macro expandido ({bb_kc_ratio_macro:.2f}).")
            pattern_score -= 0.05

    # ── Enrichment: SAR+ADX signal ────────────────────────
    if sar_adx_signal is not None:
        if (signal_dir == "BUY" and sar_adx_signal > 0) or (signal_dir == "SELL" and sar_adx_signal < 0):
            reasons.append(f"SAR+ADX confirma dirección ({sar_adx_signal}).")
            pattern_score += 0.15
        elif sar_adx_signal == 0:
            reasons.append("SAR+ADX neutral.")
        else:
            reasons.append(f"SAR+ADX en contra ({sar_adx_signal}).")
            pattern_score -= 0.15

    # ── Enrichment: range_to_atr ──────────────────────────
    if range_to_atr is not None:
        if range_to_atr > 1.5:
            reasons.append(f"range_to_atr alto ({range_to_atr:.2f}). Buen momentum/gas.")
            pattern_score += 0.10
        elif range_to_atr < 0.8:
            reasons.append(f"range_to_atr bajo ({range_to_atr:.2f}). Mercado sin fuerza.")
            pattern_score -= 0.10
    if range_to_atr_micro is not None:
        if range_to_atr_micro > 1.2:
            reasons.append(f"range_to_atr micro confirma expansión ({range_to_atr_micro:.2f}).")
            pattern_score += 0.10
        elif range_to_atr_micro < 0.8:
            reasons.append(f"range_to_atr micro débil ({range_to_atr_micro:.2f}).")
            pattern_score -= 0.08

    # ── Enrichment: step_index_type ───────────────────────
    if step_index_type is not None:
        reasons.append(f"Tipo Step Index: {step_index_type}.")

    # ── Enrichment: decision_context (extra signal quality) ─
    signal_proxy = type(
        "S",
        (),
        {
            "decision_context": decision_context,
            "confirmations": confirmations,
            "analysis_context": analysis_context,
        },
    )()
    pattern_name = get_pattern_name(signal_proxy)
    if pattern_name:
        reasons.append(f"Patrón detectado: {pattern_name}.")
        pattern_score += 0.10

    confirmations = get_confirmations(type("S", (), {"confirmations": confirmations})())
    if confirmations.get("breakout_confirmed") is True:
        reasons.append("Breakout confirmado.")
        pattern_score += 0.10
    if confirmations.get("sar_adx_confirmed") is True:
        reasons.append("Confirmación SAR+ADX explícita.")
        pattern_score += 0.10
    if confirmations.get("clv_confirmed") is True:
        reasons.append("Confirmación CLV explícita.")
        pattern_score += 0.05

    macro_snapshot = get_snapshot(type("S", (), {"analysis_context": analysis_context})(), "macro_snapshot")
    micro_snapshot = get_snapshot(type("S", (), {"analysis_context": analysis_context})(), "micro_snapshot")
    if get_nested(macro_snapshot, "squeeze", "ratio") is not None and bb_kc_ratio_macro is None:
        ratio = get_nested(macro_snapshot, "squeeze", "ratio")
        reasons.append(f"Squeeze macro snapshot ({ratio:.2f}).")
        pattern_score += 0.05
    if get_nested(micro_snapshot, "noise", "rangeToAtr") is not None and range_to_atr_micro is None:
        ratio = get_nested(micro_snapshot, "noise", "rangeToAtr")
        if ratio > 1.2:
            reasons.append(f"Micro snapshot confirma gas ({ratio:.2f}).")
            pattern_score += 0.05

    recent_events = [event for event in (episode_events or []) if isinstance(event, dict)]
    if recent_events:
        last_event_type = str(recent_events[-1].get("type", "")).upper()
        if "INVALID" in last_event_type or "REJECT" in last_event_type:
            reasons.append(f"Evento reciente adverso: {last_event_type}.")
            pattern_score -= 0.10

    # ── Final verdict ─────────────────────────────────────
    if pattern_score >= 0.4:
        verdict = Verdict.APPROVE
        confidence = min(0.95, 0.7 + pattern_score * 0.2)
    elif pattern_score >= 0.15:
        verdict = Verdict.HOLD
        confidence = 0.55
    else:
        verdict = Verdict.REJECT
        confidence = max(0.5, 0.75 - pattern_score * 0.3)

    return verdict, round(confidence, 2), " | ".join(reasons) if reasons else "Sin razones."


async def evaluate_pattern(
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    current_volatility: float = 0.0,
    direction: str = "BUY",
    symbol: str = "EURUSD",
    rag_context: str = "",
    *,
    step_index_type: str | None = None,
    bb_kc_ratio: float | None = None,
    range_to_atr: float | None = None,
    sar_adx_signal: int | None = None,
    decision_context: dict | None = None,
    range_to_atr_micro: float | None = None,
    bb_kc_ratio_macro: float | None = None,
    analysis_context: dict | None = None,
    confirmations: dict | None = None,
    episode_events: list[dict] | None = None,
) -> ExpertOpinion:
    """
    Analyze geometry and setup quality.

    When `ORION_ENABLE_EXPERT_LLM=true`, the LLM refines the classic verdict.
    """
    classic_verdict, classic_confidence, classic_reason = _classic_pattern_assessment(
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        current_volatility=current_volatility,
        direction=direction,
        symbol=symbol,
        step_index_type=step_index_type,
        bb_kc_ratio=bb_kc_ratio,
        range_to_atr=range_to_atr,
        sar_adx_signal=sar_adx_signal,
        decision_context=decision_context,
        range_to_atr_micro=range_to_atr_micro,
        bb_kc_ratio_macro=bb_kc_ratio_macro,
        analysis_context=analysis_context,
        confirmations=confirmations,
        episode_events=episode_events,
    )

    if not settings.enable_expert_llm:
        return ExpertOpinion(
            expert=ExpertName.PATTERN_EXPERT,
            verdict=classic_verdict,
            confidence=classic_confidence,
            reason=classic_reason,
        )

    system_prompt = """
    Eres el PATTERN EXPERT del Orion Committee.
    Analizas la geometría de la señal y calidad del setup.
    Evalúa el veredicto clásico y el contexto RAG.
    La clave "decision" debe ser exactamente una de estas:
    APPROVE, REJECT, HOLD.
    Responde estrictamente un JSON puro.
    Ejemplo valido:
    {"decision": "APPROVE", "confidence": 0.85, "reason": "Justificacion breve."}
    """
    user_prompt = f"""
    === ESTADO GEOMÉTRICO ===
    Symbol: {symbol}
    Direction: {direction}
    Entry: {entry_price}, SL: {stop_loss}, TP: {take_profit}
    Volatility: {current_volatility}
    BB/KC ratio: {bb_kc_ratio}
    SAR+ADX signal: {sar_adx_signal}
    Range/ATR: {range_to_atr}
    Range/ATR micro: {range_to_atr_micro}
    Step type: {step_index_type}
    BB/KC macro: {bb_kc_ratio_macro}
    Confirmations: {confirmations}
    Analysis context: {analysis_context}
    Cálculo Clásico: {classic_verdict.value}
    Razones: {classic_reason}

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
            expert=ExpertName.PATTERN_EXPERT,
            verdict=verdict,
            confidence=round(confidence, 2),
            reason=f"[LLM] {reason}",
        )
    except Exception as exc:
        logger.warning("Pattern Expert LLM fallback triggered: %s", exc, exc_info=True)
        return ExpertOpinion(
            expert=ExpertName.PATTERN_EXPERT,
            verdict=classic_verdict,
            confidence=classic_confidence,
            reason=classic_reason,
        )
