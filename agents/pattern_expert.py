"""
🔍 Pattern Expert — Experto en patrones del Step Index.

Detecta spikes, consolidaciones, rupturas y condiciones anómalas
específicas del instrumento Step Index.
"""

from __future__ import annotations

from models.schemas import ExpertOpinion, ExpertName, Verdict


def evaluate_pattern(
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    current_volatility: float = 0.0,
    direction: str = "BUY",
    symbol: str = "EURUSD",
) -> ExpertOpinion:
    """
    Evaluates Step Index-specific patterns and trade geometry.

    Rules:
    - Risk/Reward ratio must be at least 1.5:1.
    - Stop-loss distance signals potential spike zones.
    - Volatility in context of the pattern is assessed.
    """
    reasons: list[str] = []
    pattern_score: float = 0.0

    # ── Risk / Reward ratio ───────────────────────────
    risk = abs(entry_price - stop_loss)
    reward = abs(take_profit - entry_price)

    if risk == 0:
        return ExpertOpinion(
            expert=ExpertName.PATTERN_EXPERT,
            verdict=Verdict.REJECT,
            confidence=0.95,
            reason="Stop-loss idéntico al precio de entrada. Trade inválido.",
        )

    rr_ratio = reward / risk

    if rr_ratio >= 2.0:
        reasons.append(f"R:R excelente ({rr_ratio:.2f}:1).")
        pattern_score += 0.4
    elif rr_ratio >= 1.5:
        reasons.append(f"R:R aceptable ({rr_ratio:.2f}:1).")
        pattern_score += 0.25
    elif rr_ratio >= 1.0:
        reasons.append(f"R:R mínimo ({rr_ratio:.2f}:1). Considerar mejorar.")
        pattern_score += 0.1
    else:
        reasons.append(f"R:R desfavorable ({rr_ratio:.2f}:1). Trade rechazado.")
        pattern_score -= 0.4

    # ── Spike zone detection (Step Index specific) ────
    # In Step Index, sudden price jumps (spikes) are common.
    # A very tight stop loss in high volatility suggests the trade
    # is in a spike zone.
    multiplier = 10.0 if "step" in symbol.lower() else 1.0

    if current_volatility > 0:
        risk_pct = (risk / entry_price) * 100

        if current_volatility > (150 * multiplier) and risk_pct < 0.5:
            reasons.append(
                "⚠️ Stop-loss muy ajustado en zona de alta volatilidad. "
                "Alto riesgo de spike."
            )
            pattern_score -= 0.3
        elif current_volatility > (100 * multiplier) and risk_pct < 0.3:
            reasons.append(
                "Stop-loss extremadamente ajustado. Posible zona de spike."
            )
            pattern_score -= 0.2

    # ── Consolidation pattern check ───────────────────
    # If volatility is very low, the market may be in consolidation
    if 0 < current_volatility < (30 * multiplier):
        reasons.append(
            "Volatilidad baja — posible consolidación. "
            "Ruptura esperada, monitorear dirección."
        )
        pattern_score += 0.1  # Consolidation breakouts can be favorable

    # ── Trade geometry validation ─────────────────────
    dir_upper = direction.strip().upper()
    if dir_upper == "BUY":
        if stop_loss >= entry_price:
            reasons.append("Stop-loss por encima del entry en BUY. Inválido.")
            pattern_score -= 0.5
        if take_profit <= entry_price:
            reasons.append("Take-profit por debajo del entry en BUY. Inválido.")
            pattern_score -= 0.5
    elif dir_upper == "SELL":
        if stop_loss <= entry_price:
            reasons.append("Stop-loss por debajo del entry en SELL. Inválido.")
            pattern_score -= 0.5
        if take_profit >= entry_price:
            reasons.append("Take-profit por encima del entry en SELL. Inválido.")
            pattern_score -= 0.5

    # ── Final verdict ─────────────────────────────────
    if pattern_score >= 0.3:
        verdict = Verdict.APPROVE
        confidence = min(1.0, 0.65 + pattern_score * 0.25)
    elif pattern_score >= 0.0:
        verdict = Verdict.HOLD
        confidence = 0.55
    else:
        verdict = Verdict.REJECT
        confidence = min(1.0, 0.7 + abs(pattern_score) * 0.2)

    if not reasons:
        reasons.append("Análisis de patrón completado sin señales fuertes.")

    return ExpertOpinion(
        expert=ExpertName.PATTERN_EXPERT,
        verdict=verdict,
        confidence=round(confidence, 2),
        reason=" | ".join(reasons),
    )
