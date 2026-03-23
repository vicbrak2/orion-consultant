"""
📈 Trend Analyzer — Experto en estructura de tendencia multi-timeframe (H1/H4).

Evalúa dirección, fuerza y alineación de tendencia para validar la señal.
"""

from __future__ import annotations

from models.schemas import ExpertOpinion, ExpertName, Verdict, SignalDirection


# Alias directions for flexible input
_BULLISH = {"bullish", "bull", "up", "long", "alcista"}
_BEARISH = {"bearish", "bear", "down", "short", "bajista"}


def _parse_trend(trend: str | None) -> str:
    """Normalize trend input to 'bullish', 'bearish', or 'neutral'."""
    if trend is None:
        return "neutral"
    t = trend.strip().lower()
    if t in _BULLISH:
        return "bullish"
    if t in _BEARISH:
        return "bearish"
    return "neutral"


def evaluate_trend(
    direction: str,
    trend_h1: str | None = None,
    trend_h4: str | None = None,
    symbol: str = "EURUSD",
) -> ExpertOpinion:
    """
    Analyzes multi-timeframe trend alignment.

    Rules:
    - H1 and H4 must agree with the signal direction.
    - Full alignment → APPROVE with high confidence.
    - Partial alignment → HOLD with medium confidence.
    - Counter-trend → REJECT.
    """
    reasons: list[str] = []
    signal_dir = direction.strip().upper()

    h1 = _parse_trend(trend_h1)
    h4 = _parse_trend(trend_h4)

    # Map signal direction to expected trend
    if signal_dir == SignalDirection.BUY:
        expected = "bullish"
    elif signal_dir == SignalDirection.SELL:
        expected = "bearish"
    else:
        return ExpertOpinion(
            expert=ExpertName.TREND_ANALYZER,
            verdict=Verdict.REJECT,
            confidence=0.9,
            reason=f"Dirección de señal desconocida: {signal_dir}",
        )

    # ── Alignment scoring ─────────────────────────────
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

    # ── If no timeframe data is available ─────────────
    if total_frames == 0:
        return ExpertOpinion(
            expert=ExpertName.TREND_ANALYZER,
            verdict=Verdict.HOLD,
            confidence=0.4,
            reason="Sin datos de tendencia H1/H4. No se puede confirmar.",
        )

    # ── Verdict ───────────────────────────────────────
    ratio = alignment / total_frames

    if ratio >= 1.0:
        verdict = Verdict.APPROVE
        confidence = 0.90 if total_frames == 2 else 0.75
        reasons.append("Alineación completa multi-timeframe.")
    elif ratio >= 0.5:
        verdict = Verdict.HOLD
        confidence = 0.55
        reasons.append("Alineación parcial. Se recomienda esperar confirmación.")
    else:
        verdict = Verdict.REJECT
        confidence = 0.80
        reasons.append("Señal contra-tendencia detectada.")

    return ExpertOpinion(
        expert=ExpertName.TREND_ANALYZER,
        verdict=verdict,
        confidence=round(confidence, 2),
        reason=" | ".join(reasons),
    )
