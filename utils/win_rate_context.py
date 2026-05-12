"""
win_rate_context.py
====================
Reads the ``feature_win_rates`` table (written nightly by offline_scorer.py)
and returns a structured context dict that Orion's risk_manager can use to
adjust its scoring based on historical win rates for the current market regime.

The table is cached in-memory for ``CACHE_TTL_SECONDS`` (default 3600 = 1h)
so each consultation does not hit the database.  Cache is refreshed
automatically when it expires.

Usage
-----
    from utils.win_rate_context import get_win_rate_context

    ctx = await get_win_rate_context(
        symbol="Step Index",
        fsm_phase="TREND",
        entry_regime="trending",
        orion_verdict="APPROVE",
        entry_adx=28.5,
        entry_window_open=True,
        sar_adx_signal=1,
    )
    # ctx = {
    #   "fsm_phase": {"win_rate": 0.63, "confidence": 0.87, "total_count": 41},
    #   "entry_regime": {"win_rate": 0.61, "confidence": 1.0, "total_count": 55},
    #   ...
    #   "summary": {"avg_win_rate": 0.62, "min_confidence": 0.87, "n_features": 3}
    # }

Environment
-----------
DATABASE_URL   postgres://...  (same as offline_scorer)
WIN_RATE_CACHE_TTL_SECONDS   int, default 3600
WIN_RATE_MIN_CONFIDENCE      float, default 0.4  (below → skip feature)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

log = logging.getLogger("orion.win_rate_context")

DATABASE_URL: str = os.getenv("DATABASE_URL", "")
CACHE_TTL_SECONDS: int = int(os.getenv("WIN_RATE_CACHE_TTL_SECONDS", "3600"))
MIN_CONFIDENCE: float = float(os.getenv("WIN_RATE_MIN_CONFIDENCE", "0.4"))

# ---------------------------------------------------------------------------
# In-memory cache
# Cache structure: { symbol -> { (feature_name, feature_value) -> row_dict } }
# ---------------------------------------------------------------------------
_cache: dict[str, dict[tuple[str, str], dict]] = {}
_cache_loaded_at: dict[str, float] = {}


def _is_cache_valid(symbol: str) -> bool:
    loaded = _cache_loaded_at.get(symbol, 0.0)
    return (time.monotonic() - loaded) < CACHE_TTL_SECONDS


def _load_cache(symbol: str) -> None:
    """Load all feature_win_rates rows for a symbol into the in-memory cache."""
    if not DATABASE_URL:
        log.debug("DATABASE_URL not set — win_rate_context disabled")
        _cache[symbol] = {}
        _cache_loaded_at[symbol] = time.monotonic()
        return

    try:
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(DATABASE_URL)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT feature_name, feature_value, win_rate,
                           confidence, total_count, avg_profit_pips, avg_rr_realized
                    FROM feature_win_rates
                    WHERE symbol = %s
                    """,
                    (symbol,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        bucket: dict[tuple[str, str], dict] = {}
        for row in rows:
            key = (row["feature_name"], str(row["feature_value"]).strip().lower())
            bucket[key] = {
                "win_rate": float(row["win_rate"]),
                "confidence": float(row["confidence"]),
                "total_count": int(row["total_count"]),
                "avg_profit_pips": float(row["avg_profit_pips"]) if row["avg_profit_pips"] is not None else None,
                "avg_rr_realized": float(row["avg_rr_realized"]) if row["avg_rr_realized"] is not None else None,
            }
        _cache[symbol] = bucket
        _cache_loaded_at[symbol] = time.monotonic()
        log.info("win_rate cache loaded: symbol=%s buckets=%d", symbol, len(bucket))

    except Exception as exc:
        log.warning("Failed to load win_rate cache for symbol=%s: %s", symbol, exc)
        _cache[symbol] = {}
        _cache_loaded_at[symbol] = time.monotonic()


def _lookup(symbol: str, feature_name: str, feature_value: str | None) -> dict | None:
    """Return a cached row or None if not found / low confidence."""
    if not feature_value:
        return None
    key = (feature_name, str(feature_value).strip().lower())
    row = _cache.get(symbol, {}).get(key)
    if row is None:
        return None
    if row["confidence"] < MIN_CONFIDENCE:
        log.debug(
            "Skipping low-confidence bucket: %s/%s/%s conf=%.2f",
            symbol, feature_name, feature_value, row["confidence"],
        )
        return None
    return row


def _adx_bucket(adx: float | None) -> str | None:
    if adx is None:
        return None
    if adx < 15:
        return "weak(<15)"
    if adx < 25:
        return "moderate(15-25)"
    if adx < 40:
        return "strong(25-40)"
    return "very_strong(>=40)"


def _sar_adx_bucket(signal: int | None) -> str | None:
    if signal is None:
        return None
    if signal == 0:
        return "0"
    if signal == 1:
        return "1"
    return "2+"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_win_rate_context(
    symbol: str,
    *,
    fsm_phase: str | None = None,
    entry_regime: str | None = None,
    orion_verdict: str | None = None,
    entry_adx: float | None = None,
    entry_window_open: bool | None = None,
    sar_adx_signal: int | None = None,
    direction: str | None = None,
) -> dict[str, Any]:
    """
    Build a win_rate_context dict for the given signal features.

    Returns an empty dict if the database is unavailable or the table is empty.
    Never raises — degrades gracefully.

    Each feature key maps to::

        {
          "win_rate": float,       # historical win rate for this bucket
          "confidence": float,     # 0.0-1.0 based on sample size
          "total_count": int,
          "avg_profit_pips": float | None,
          "avg_rr_realized": float | None,
        }

    Plus a ``"summary"`` key::

        {
          "avg_win_rate": float,   # mean across all present features
          "min_confidence": float,
          "n_features": int,
        }
    """
    if not _is_cache_valid(symbol):
        _load_cache(symbol)

    features: dict[str, tuple[str, str | None]] = {
        "fsm_phase":             ("fsm_phase",             fsm_phase),
        "entry_regime":          ("entry_regime",          entry_regime),
        "orion_verdict_at_entry":("orion_verdict_at_entry", orion_verdict),
        "entry_adx_bucket":      ("entry_adx_bucket",      _adx_bucket(entry_adx)),
        "entry_window_open":     ("entry_window_open",     str(entry_window_open).lower() if entry_window_open is not None else None),
        "sar_adx_signal":        ("sar_adx_signal",        _sar_adx_bucket(sar_adx_signal)),
        "direction":             ("direction",             direction),
    }

    ctx: dict[str, Any] = {}
    win_rates: list[float] = []
    confidences: list[float] = []

    for ctx_key, (feat_name, feat_val) in features.items():
        row = _lookup(symbol, feat_name, feat_val)
        if row is not None:
            ctx[ctx_key] = row
            win_rates.append(row["win_rate"])
            confidences.append(row["confidence"])

    if ctx:
        ctx["summary"] = {
            "avg_win_rate": round(sum(win_rates) / len(win_rates), 4),
            "min_confidence": round(min(confidences), 4),
            "n_features": len(win_rates),
        }

    return ctx


def invalidate_cache(symbol: str | None = None) -> None:
    """Force cache reload on next call. Pass None to invalidate all symbols."""
    if symbol:
        _cache_loaded_at.pop(symbol, None)
    else:
        _cache_loaded_at.clear()
