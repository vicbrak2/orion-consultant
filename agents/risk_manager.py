"""
🛡️ Risk Manager — Experto en drawdown, volatilidad y gestión de capital.

Evalúa si las condiciones de la cuenta permiten operar con seguridad.
"""

from __future__ import annotations

from config import settings
from models.schemas import ExpertOpinion, ExpertName, Verdict


def evaluate_risk(
    equity: float,
    balance: float,
    current_volatility: float,
    entry_price: float = 0.0,
    stop_loss: float = 0.0,
    symbol: str = "EURUSD",
) -> ExpertOpinion:
    """
    Validates whether it is safe to trade based on account health.

    Rules:
    - Drawdown must be below the configured threshold (default 5%).
    - Volatility must be below the configured max (default 200).
    - Risk/reward ratio is evaluated if entry & stop-loss are provided.
    """
    reasons: list[str] = []
    risk_score: float = 0.0

    # ── Drawdown check ────────────────────────────────
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

    # ── Volatility check ──────────────────────────────
    # Adjust volatility threshold for Synthetic Indices like Step Index
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

    # ── Risk/Reward ratio ─────────────────────────────
    if entry_price > 0 and stop_loss > 0:
        risk_distance = abs(entry_price - stop_loss)
        if risk_distance > entry_price * 0.02:  # More than 2% of entry
            reasons.append(
                f"Distancia de stop-loss ({risk_distance:.2f}) demasiado amplia."
            )
            risk_score += 0.2

    # ── Verdict ───────────────────────────────────────
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

    return ExpertOpinion(
        expert=ExpertName.RISK_MANAGER,
        verdict=verdict,
        confidence=round(confidence, 2),
        reason=" | ".join(reasons),
    )
