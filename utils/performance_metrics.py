"""
📊 Performance Metrics — Cálculos históricos de performance sobre series de PnL.

Todas las funciones son puras (sin I/O, sin dependencias externas) y trabajan
sobre listas de floats que representan PnL por trade.

Uso en risk_manager:
    from utils.performance_metrics import compute_performance_summary
    summary = compute_performance_summary(pnl_list)
    # summary.sharpe, summary.var_95, summary.max_drawdown, summary.win_rate
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence


@dataclass(frozen=True)
class PerformanceSummary:
    """Resumen de métricas históricas de performance."""

    n_trades: int
    win_rate: float          # 0.0–1.0
    sharpe: float            # ratio anualizado (asume 1 trade/barra)
    var_95: float            # pérdida máxima al 95% de confianza (valor negativo)
    max_drawdown: float      # peor peak-to-trough como fracción (0.0–1.0)
    avg_pnl: float           # PnL promedio por trade
    total_pnl: float         # PnL acumulado
    insufficient_data: bool  # True si n_trades < MIN_TRADES para métricas confiables

    # Clasificaciones semánticas (calculadas en __post_init__ no aplica en frozen,
    # se calculan directamente en compute_performance_summary)
    sharpe_label: str = field(default="unknown")
    drawdown_label: str = field(default="unknown")


# Mínimo de trades para que las métricas sean estadísticamente útiles
MIN_TRADES = 10


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float], mean: float | None = None) -> float:
    if len(values) < 2:
        return 0.0
    mu = mean if mean is not None else _mean(values)
    variance = sum((x - mu) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)


def sharpe_ratio(pnl_list: list[float], *, risk_free: float = 0.0) -> float:
    """
    Sharpe ratio sobre la serie de PnL.

    No asume frecuencia temporal — trabaja en unidades de trade.
    risk_free es el PnL esperado sin riesgo por trade (default 0).

    Returns 0.0 si std == 0 o n < 2.
    """
    if len(pnl_list) < 2:
        return 0.0
    mu = _mean(pnl_list)
    sigma = _std(pnl_list, mu)
    if sigma == 0.0:
        return 0.0
    return (mu - risk_free) / sigma


def historical_var(pnl_list: list[float], *, confidence: float = 0.95) -> float:
    """
    VaR histórico al nivel de confianza dado.

    Retorna el cuantil (1 - confidence) de la distribución de PnL.
    Para confidence=0.95 retorna el percentil 5 — la pérdida que se supera
    solo en el 5% de los trades. Valor típicamente negativo.

    Returns 0.0 si la lista está vacía.
    """
    if not pnl_list:
        return 0.0
    sorted_pnl = sorted(pnl_list)
    index = int(math.floor((1.0 - confidence) * len(sorted_pnl)))
    index = max(0, min(index, len(sorted_pnl) - 1))
    return sorted_pnl[index]


def max_drawdown_series(pnl_list: list[float]) -> float:
    """
    Máximo drawdown (peak-to-trough) sobre la curva de equity acumulada.

    Retorna un valor entre 0.0 y 1.0 representando la mayor caída relativa
    desde un pico. Ejemplo: 0.15 = caída máxima del 15% desde un pico.

    Returns 0.0 si la serie tiene 0 o 1 elementos.
    """
    if len(pnl_list) < 2:
        return 0.0

    peak = 0.0
    cumulative = 0.0
    max_dd = 0.0

    for pnl in pnl_list:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        if peak > 0:
            dd = (peak - cumulative) / peak
            if dd > max_dd:
                max_dd = dd

    return max_dd


def _sharpe_label(sharpe: float) -> str:
    if sharpe >= 1.5:
        return "excellent"
    if sharpe >= 1.0:
        return "good"
    if sharpe >= 0.5:
        return "acceptable"
    if sharpe >= 0.0:
        return "poor"
    return "negative"


def _drawdown_label(max_dd: float) -> str:
    if max_dd <= 0.05:
        return "low"
    if max_dd <= 0.10:
        return "moderate"
    if max_dd <= 0.20:
        return "high"
    return "severe"


def compute_performance_summary(
    pnl_history: Sequence[float],
) -> PerformanceSummary:
    """
    Calcula todas las métricas de performance sobre una serie histórica de PnL.

    Args:
        pnl_history: Lista de PnL por trade (positivo = ganancia, negativo = pérdida).
                     Se espera en orden cronológico.

    Returns:
        PerformanceSummary con sharpe, var_95, max_drawdown, win_rate, etc.
        Si n_trades < MIN_TRADES, insufficient_data=True y las métricas son
        orientativas (no penalizan ni bonificar al risk_manager).
    """
    pnl_list = [float(x) for x in pnl_history]
    n = len(pnl_list)

    if n == 0:
        return PerformanceSummary(
            n_trades=0,
            win_rate=0.0,
            sharpe=0.0,
            var_95=0.0,
            max_drawdown=0.0,
            avg_pnl=0.0,
            total_pnl=0.0,
            insufficient_data=True,
            sharpe_label="unknown",
            drawdown_label="unknown",
        )

    wins = sum(1 for x in pnl_list if x > 0)
    win_rate = wins / n
    avg_pnl = _mean(pnl_list)
    total_pnl = sum(pnl_list)
    s = sharpe_ratio(pnl_list)
    v = historical_var(pnl_list)
    dd = max_drawdown_series(pnl_list)
    insufficient = n < MIN_TRADES

    return PerformanceSummary(
        n_trades=n,
        win_rate=round(win_rate, 4),
        sharpe=round(s, 4),
        var_95=round(v, 4),
        max_drawdown=round(dd, 4),
        avg_pnl=round(avg_pnl, 4),
        total_pnl=round(total_pnl, 4),
        insufficient_data=insufficient,
        sharpe_label=_sharpe_label(s),
        drawdown_label=_drawdown_label(dd),
    )
