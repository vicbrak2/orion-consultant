"""
📈 Trend Analyzer — Experto en estructura de tendencia multi-timeframe (H1/H4).

Evalúa dirección, fuerza y alineación de tendencia para validar la señal.
Enriched: usa bias, CLV, entry_window_open y macro_structure_ok cuando están disponibles.
"""

from __future__ import annotations

import json
import logging

import agents.rag_client as rag_client
from config import settings
from models.schemas import ExpertOpinion, ExpertName, SignalDirection, Verdict
from utils.context_extractors import get_analysis_context, get_snapshot

logger = logging.getLogger(__name__)

_BULLISH = {"bullish", "bull", "up", "long", "alcista"}
_BEARISH = {"bearish", "bear", "down", "short", "bajista"}


def _parse_trend(trend: str | None) -> str:
    """Normalize trend aliases to bullish, bearish or neutral."""
    if trend is None:
        return "neutral"
    normalized = trend.strip().lower()
    if normalized in _BULLISH:
        return "bullish"
    if normalized in _BEARISH:
        return "bearish"
    return "neutral"


def _parse_snapshot_trend(snapshot: dict | None) -> str:
    if not isinstance(snapshot, dict):
        return "neutral"
    adx = snapshot.get("adx")
    if not isinstance(adx, dict):
        return "neutral"
    plus_di = adx.get("plus")
    minus_di = adx.get("minus")
    if plus_di is None or minus_di is None:
        return "neutral"
    if plus_di > minus_di:
        return "bullish"
    if minus_di > plus_di:
        return "bearish"
    return "neutral"


def _classic_trend_assessment(
    direction: str,
    trend_h1: str | None,
    trend_h4: str | None,
    *,
    bias: int | None = None,
    current_clv: float | None = None,
    previous_clv: float | None = None,
    entry_window_open: bool | None = None,
    macro_structure_ok: bool | None = None,
    trend_direction: str | None = None,
    trend_timeframe: str | None = None,
    macro_timeframe: str | None = None,
    micro_timeframe: str | None = None,
    analysis_context: dict | None = None,
) -> tuple[Verdict, float, str]:
    reasons: list[str] = []
    signal_dir = (direction or "").strip().upper()

    trend_snapshot = get_snapshot(type("S", (), {"analysis_context": analysis_context})(), "trend_snapshot")
    macro_snapshot = get_snapshot(type("S", (), {"analysis_context": analysis_context})(), "macro_snapshot")
    micro_snapshot = get_snapshot(type("S", (), {"analysis_context": analysis_context})(), "micro_snapshot")

    effective_h1 = trend_h1
    effective_h4 = trend_h4
    parsed_trend_direction = _parse_trend(trend_direction)
    snapshot_trend = _parse_snapshot_trend(trend_snapshot)
    macro_snapshot_trend = _parse_snapshot_trend(macro_snapshot)
    micro_snapshot_trend = _parse_snapshot_trend(micro_snapshot)

    if parsed_trend_direction != "neutral":
        effective_h4 = trend_direction
        reasons.append(
            f"Trend source={trend_timeframe or 'unknown'} confirma {parsed_trend_direction}."
        )
    if effective_h1 is None and macro_snapshot_trend != "neutral":
        effective_h1 = macro_snapshot_trend
        reasons.append(
            f"Macro snapshot ({macro_timeframe or 'macro'}) sugiere {macro_snapshot_trend}."
        )
    if effective_h4 is None and snapshot_trend != "neutral":
        effective_h4 = snapshot_trend
        reasons.append(
            f"Trend snapshot ({trend_timeframe or 'trend'}) sugiere {snapshot_trend}."
        )
    if micro_snapshot_trend != "neutral":
        reasons.append(
            f"Micro snapshot ({micro_timeframe or 'micro'}) está {micro_snapshot_trend}."
        )

    h1 = _parse_trend(effective_h1)
    h4 = _parse_trend(effective_h4)

    if signal_dir == SignalDirection.BUY:
        expected = "bullish"
    elif signal_dir == SignalDirection.SELL:
        expected = "bearish"
    else:
        return Verdict.REJECT, 0.9, f"Dirección de señal desconocida: {signal_dir}"

    alignment = 0
    total_frames = 0

    if h1 != "neutral":
        total_frames += 1
        if h1 == expected:
            alignment += 1
            reasons.append(f"H1 alineado ({h1}).")
        else:
            reasons.append(f"H1 en contra ({h1} vs esperado {expected}).")

    if h4 != "neutral":
        total_frames += 1
        if h4 == expected:
            alignment += 1
            reasons.append(f"H4 alineado ({h4}).")
        else:
            reasons.append(f"H4 en contra ({h4} vs esperado {expected}).")

    # ── Enrichment: bias confirmation ─────────────────────
    bias_aligned = False
    if bias is not None:
        if (signal_dir == "BUY" and bias > 0) or (signal_dir == "SELL" and bias < 0):
            bias_aligned = True
            reasons.append(f"Bias confirma dirección ({bias}).")
        elif bias == 0:
            reasons.append("Bias neutral.")
        else:
            reasons.append(f"Bias contradice dirección ({bias}).")

    # ── Enrichment: CLV momentum ──────────────────────────
    clv_supporting = False
    # CLV threshold raised to 0.70/0.30 to filter weak closes in Step Index.
    # A BUY close must be in the upper 30% of the candle's range; SELL in the lower 30%.
    CLV_BUY_THRESHOLD = 0.70
    CLV_SELL_THRESHOLD = 0.30

    if current_clv is not None:
        if signal_dir == "BUY" and current_clv >= CLV_BUY_THRESHOLD:
            clv_supporting = True
            reasons.append(f"CLV confirma BUY fuerte ({current_clv:.2f} >= {CLV_BUY_THRESHOLD}).")
        elif signal_dir == "SELL" and current_clv <= CLV_SELL_THRESHOLD:
            clv_supporting = True
            reasons.append(f"CLV confirma SELL fuerte ({current_clv:.2f} <= {CLV_SELL_THRESHOLD}).")
        elif CLV_SELL_THRESHOLD < current_clv < CLV_BUY_THRESHOLD:
            reasons.append(f"CLV débil ({current_clv:.2f}) — cierre sin convicción direccional.")
        else:
            reasons.append(
                f"CLV no confirma {signal_dir} ({current_clv:.2f}) con umbrales "
                f"BUY>={CLV_BUY_THRESHOLD} / SELL<={CLV_SELL_THRESHOLD}."
            )
        if previous_clv is not None:
            delta = current_clv - previous_clv
            if abs(delta) > 0.1:
                reasons.append(
                    f"CLV cambió {delta:+.2f} vs prev {previous_clv:.2f}, "
                    "pero no reemplaza la confirmación direccional estricta."
                )

    # ── Enrichment: entry window ──────────────────────────
    if entry_window_open is not None:
        if entry_window_open:
            reasons.append("Ventana de entrada abierta.")
        else:
            reasons.append("Ventana de entrada cerrada — señal tardía.")

    # ── Enrichment: macro structure ───────────────────────
    if macro_structure_ok is not None:
        if macro_structure_ok:
            reasons.append("Estructura macro confirmada.")
        else:
            reasons.append("Estructura macro NO confirmada.")

    # ── Decision logic ────────────────────────────────────
    if total_frames == 0:
        # No H1/H4 data — but enrichment can still provide conviction
        if bias_aligned and clv_supporting and macro_structure_ok:
            return Verdict.APPROVE, 0.70, "Sin H1/H4 pero bias+CLV+estructura confirman. | " + " | ".join(reasons)
        if bias_aligned and clv_supporting:
            return Verdict.HOLD, 0.55, "Sin H1/H4 pero bias+CLV parcialmente confirman. | " + " | ".join(reasons)
        return Verdict.HOLD, 0.4, "Sin datos de tendencia H1/H4. No se puede confirmar. | " + " | ".join(reasons)

    ratio = alignment / total_frames

    # Full alignment
    if ratio >= 1.0:
        confidence = 0.90 if total_frames == 2 else 0.75
        # Boost confidence with enrichment
        if bias_aligned:
            confidence = min(1.0, confidence + 0.05)
        if clv_supporting:
            confidence = min(1.0, confidence + 0.03)
        if macro_structure_ok:
            confidence = min(1.0, confidence + 0.02)
        reasons.append("Alineación completa multi-timeframe.")
        return Verdict.APPROVE, round(confidence, 2), " | ".join(reasons)

    # Partial alignment
    if ratio >= 0.5:
        # Enrichment can promote HOLD → APPROVE
        if bias_aligned and clv_supporting and macro_structure_ok:
            reasons.append("Alineación parcial pero bias+CLV+estructura confirman. Aprobado.")
            return Verdict.APPROVE, 0.72, " | ".join(reasons)
        reasons.append("Alineación parcial. Se recomienda esperar confirmación.")
        return Verdict.HOLD, 0.55, " | ".join(reasons)

    # Counter-trend
    # Entry window closed adds to rejection confidence
    confidence = 0.80
    if entry_window_open is False:
        confidence = min(1.0, confidence + 0.05)
    reasons.append("Señal contra-tendencia detectada.")
    return Verdict.REJECT, round(confidence, 2), " | ".join(reasons)


async def evaluate_trend(
    direction: str,
    trend_h1: str | None = None,
    trend_h4: str | None = None,
    symbol: str = "EURUSD",
    rag_context: str = "",
    *,
    bias: int | None = None,
    current_clv: float | None = None,
    previous_clv: float | None = None,
    entry_window_open: bool | None = None,
    macro_structure_ok: bool | None = None,
    trend_direction: str | None = None,
    trend_timeframe: str | None = None,
    macro_timeframe: str | None = None,
    micro_timeframe: str | None = None,
    analysis_context: dict | None = None,
) -> ExpertOpinion:
    """
    Analyze multi-timeframe alignment.

    The classical rules remain the default behavior. LLM enhancement is only
    used when `ORION_ENABLE_EXPERT_LLM=true`.
    """
    classic_verdict, classic_confidence, classic_reason = _classic_trend_assessment(
        direction=direction,
        trend_h1=trend_h1,
        trend_h4=trend_h4,
        bias=bias,
        current_clv=current_clv,
        previous_clv=previous_clv,
        entry_window_open=entry_window_open,
        macro_structure_ok=macro_structure_ok,
        trend_direction=trend_direction,
        trend_timeframe=trend_timeframe,
        macro_timeframe=macro_timeframe,
        micro_timeframe=micro_timeframe,
        analysis_context=analysis_context,
    )

    if not settings.enable_expert_llm:
        return ExpertOpinion(
            expert=ExpertName.TREND_ANALYZER,
            verdict=classic_verdict,
            confidence=classic_confidence,
            reason=classic_reason,
        )

    system_prompt = """
    Eres el TREND ANALYZER del Orion Committee.
    Analizas la dirección del mercado y su fuerza.
    Evalúa el veredicto clásico y el contexto RAG.
    La clave "decision" debe ser exactamente una de estas:
    APPROVE, REJECT, HOLD.
    Responde estrictamente un JSON puro.
    Ejemplo valido:
    {"decision": "APPROVE", "confidence": 0.85, "reason": "Justificacion breve."}
    """
    user_prompt = f"""
    === ESTADO MATEMÁTICO DE TENDENCIA ===
    Symbol: {symbol}
    Direction: {direction}
    Trend H1: {trend_h1}
    Trend H4: {trend_h4}
    Trend timeframe: {trend_timeframe}
    Macro timeframe: {macro_timeframe}
    Micro timeframe: {micro_timeframe}
    Trend direction: {trend_direction}
    Bias: {bias}
    CLV actual: {current_clv}
    CLV previo: {previous_clv}
    Entry Window: {entry_window_open}
    Macro Structure OK: {macro_structure_ok}
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
            expert=ExpertName.TREND_ANALYZER,
            verdict=verdict,
            confidence=round(confidence, 2),
            reason=f"[LLM] {reason}",
        )
    except Exception as exc:
        logger.warning("Trend Analyzer LLM fallback triggered: %s", exc, exc_info=True)
        return ExpertOpinion(
            expert=ExpertName.TREND_ANALYZER,
            verdict=classic_verdict,
            confidence=classic_confidence,
            reason=classic_reason,
        )
