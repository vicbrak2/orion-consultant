#!/usr/bin/env python3
"""
Offline Feature Win-Rate Scorer
================================
Runs nightly (e.g. via cron or docker exec) to compute per-feature win rates
from closed trades and write them to the ``feature_win_rates`` table.

Orion's risk_manager reads that table (with a 1-hour in-memory cache) so every
consultation uses up-to-date historical signal quality without any model
retraining overhead.

Usage
-----
    # Direct
    python scripts/offline_scorer.py

    # Docker
    docker exec orion-consultant python scripts/offline_scorer.py

    # Cron (every day at 02:00 UTC)
    0 2 * * * docker exec orion-consultant python /app/scripts/offline_scorer.py >> /var/log/scorer.log 2>&1

Environment variables
---------------------
DATABASE_URL   postgres://user:pass@host:5432/dbname  (required)
SCORER_LOOKBACK_DAYS   int, default 90
SCORER_MIN_SAMPLES     int, default 15  (below this confidence < 1.0)
SCORER_DRY_RUN         1 = print rows but don't write to DB
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATABASE_URL: str = os.getenv("DATABASE_URL", "")
LOOKBACK_DAYS: int = int(os.getenv("SCORER_LOOKBACK_DAYS", "90"))
MIN_SAMPLES: int = int(os.getenv("SCORER_MIN_SAMPLES", "15"))
DRY_RUN: bool = os.getenv("SCORER_DRY_RUN", "0") == "1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | offline_scorer | %(message)s",
)
log = logging.getLogger("offline_scorer")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FeatureBucket:
    symbol: str
    feature_name: str
    feature_value: str
    win_count: int = 0
    loss_count: int = 0
    breakeven_count: int = 0
    avg_profit_pips: float | None = None
    avg_rr_realized: float | None = None

    @property
    def total_count(self) -> int:
        return self.win_count + self.loss_count + self.breakeven_count

    @property
    def win_rate(self) -> float:
        if self.total_count == 0:
            return 0.0
        return round(self.win_count / self.total_count, 4)

    @property
    def confidence(self) -> float:
        """Sample-size confidence: reaches 1.0 at MIN_SAMPLES trades."""
        return round(min(self.total_count / MIN_SAMPLES, 1.0), 4)


# ---------------------------------------------------------------------------
# ADX bucketing
# ---------------------------------------------------------------------------

def _adx_bucket(adx: float | None) -> str:
    """Discretize ADX into 4 regime labels used as feature_value."""
    if adx is None:
        return "unknown"
    if adx < 15:
        return "weak(<15)"
    if adx < 25:
        return "moderate(15-25)"
    if adx < 40:
        return "strong(25-40)"
    return "very_strong(>=40)"


# ---------------------------------------------------------------------------
# Queries — each returns List[dict] with keys: symbol, feature_value, outcome_class,
#           plus optional profit_pips / rr_realized for averaging.
# ---------------------------------------------------------------------------

# Base CTE reused by all queries
_BASE_CTE = """
WITH closed AS (
    SELECT
        t.symbol,
        t.direction,
        COALESCE(tr.outcome_class, 'UNKNOWN')           AS outcome_class,
        tr.profit_pips,
        tr.rr_realized,
        tr.entry_adx,
        tr.entry_regime,
        tr.orion_verdict,
        tr.exit_type,
        -- FSM phase stored in trades.decision_context JSONB
        t.decision_context->>'fsm_phase'                AS fsm_phase,
        -- entry_window_open from decision_context
        (t.decision_context->>'entry_window_open')::boolean AS entry_window_open,
        -- sar_adx_signal from decision_context
        (t.decision_context->>'sar_adx_signal')::integer    AS sar_adx_signal
    FROM trade_results tr
    JOIN trades t ON t.id = tr.trade_id
    WHERE t.status = 'CLOSED'
      AND t.close_timestamp >= NOW() - INTERVAL '{lookback} days'
      AND tr.outcome_class IN ('WIN', 'LOSS', 'BREAKEVEN')
)
"""

_FEATURE_QUERIES: dict[str, str] = {
    # Feature: FSM phase
    "fsm_phase": _BASE_CTE + """
        SELECT symbol, fsm_phase AS feature_value, outcome_class,
               profit_pips, rr_realized
        FROM closed
        WHERE fsm_phase IS NOT NULL
    """,

    # Feature: entry regime (TREND / RANGE / VOLATILE etc.)
    "entry_regime": _BASE_CTE + """
        SELECT symbol, entry_regime AS feature_value, outcome_class,
               profit_pips, rr_realized
        FROM closed
        WHERE entry_regime IS NOT NULL
    """,

    # Feature: Orion verdict at entry (APPROVE / REJECT / HOLD)
    "orion_verdict_at_entry": _BASE_CTE + """
        SELECT symbol, orion_verdict AS feature_value, outcome_class,
               profit_pips, rr_realized
        FROM closed
        WHERE orion_verdict IS NOT NULL
    """,

    # Feature: ADX strength bucket at entry
    "entry_adx_bucket": _BASE_CTE + """
        SELECT symbol,
               CASE
                   WHEN entry_adx < 15  THEN 'weak(<15)'
                   WHEN entry_adx < 25  THEN 'moderate(15-25)'
                   WHEN entry_adx < 40  THEN 'strong(25-40)'
                   ELSE                      'very_strong(>=40)'
               END AS feature_value,
               outcome_class, profit_pips, rr_realized
        FROM closed
        WHERE entry_adx IS NOT NULL
    """,

    # Feature: entry_window_open (true / false)
    "entry_window_open": _BASE_CTE + """
        SELECT symbol,
               entry_window_open::text AS feature_value,
               outcome_class, profit_pips, rr_realized
        FROM closed
        WHERE entry_window_open IS NOT NULL
    """,

    # Feature: sar_adx_signal bucket (0 / 1 / 2+)
    "sar_adx_signal": _BASE_CTE + """
        SELECT symbol,
               CASE
                   WHEN sar_adx_signal = 0 THEN '0'
                   WHEN sar_adx_signal = 1 THEN '1'
                   ELSE '2+'
               END AS feature_value,
               outcome_class, profit_pips, rr_realized
        FROM closed
        WHERE sar_adx_signal IS NOT NULL
    """,

    # Feature: exit type (SL / TP / MANUAL / TIMEOUT etc.)
    "exit_type": _BASE_CTE + """
        SELECT symbol, exit_type AS feature_value, outcome_class,
               profit_pips, rr_realized
        FROM closed
        WHERE exit_type IS NOT NULL AND exit_type <> ''
    """,

    # Feature: trade direction (BUY / SELL)
    "direction": _BASE_CTE + """
        SELECT symbol, direction AS feature_value, outcome_class,
               profit_pips, rr_realized
        FROM closed
    """,
}


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _aggregate_rows(
    feature_name: str,
    rows: list[dict],
) -> dict[tuple[str, str, str], FeatureBucket]:
    """Group raw rows into FeatureBucket objects keyed by (symbol, feature_name, value)."""
    buckets: dict[tuple[str, str, str], FeatureBucket] = {}

    profit_sum: dict[tuple, float] = {}
    rr_sum: dict[tuple, float] = {}
    profit_n: dict[tuple, int] = {}
    rr_n: dict[tuple, int] = {}

    for row in rows:
        symbol = row["symbol"] or "unknown"
        val = str(row["feature_value"] or "unknown").strip().lower()
        outcome = (row["outcome_class"] or "UNKNOWN").upper()
        key = (symbol, feature_name, val)

        if key not in buckets:
            buckets[key] = FeatureBucket(
                symbol=symbol, feature_name=feature_name, feature_value=val
            )

        b = buckets[key]
        if outcome == "WIN":
            b.win_count += 1
        elif outcome == "LOSS":
            b.loss_count += 1
        else:
            b.breakeven_count += 1

        pp = row.get("profit_pips")
        rr = row.get("rr_realized")
        if pp is not None:
            profit_sum[key] = profit_sum.get(key, 0.0) + float(pp)
            profit_n[key] = profit_n.get(key, 0) + 1
        if rr is not None:
            rr_sum[key] = rr_sum.get(key, 0.0) + float(rr)
            rr_n[key] = rr_n.get(key, 0) + 1

    # Compute averages
    for key, b in buckets.items():
        if profit_n.get(key, 0) > 0:
            b.avg_profit_pips = round(profit_sum[key] / profit_n[key], 4)
        if rr_n.get(key, 0) > 0:
            b.avg_rr_realized = round(rr_sum[key] / rr_n[key], 4)

    return buckets


# ---------------------------------------------------------------------------
# DB I/O
# ---------------------------------------------------------------------------

UPSERT_SQL = """
INSERT INTO feature_win_rates (
    symbol, feature_name, feature_value,
    win_count, loss_count, breakeven_count, total_count,
    win_rate, avg_profit_pips, avg_rr_realized,
    confidence, lookback_days, computed_at
) VALUES (
    %(symbol)s, %(feature_name)s, %(feature_value)s,
    %(win_count)s, %(loss_count)s, %(breakeven_count)s, %(total_count)s,
    %(win_rate)s, %(avg_profit_pips)s, %(avg_rr_realized)s,
    %(confidence)s, %(lookback_days)s, %(computed_at)s
)
ON CONFLICT (symbol, feature_name, feature_value)
DO UPDATE SET
    win_count        = EXCLUDED.win_count,
    loss_count       = EXCLUDED.loss_count,
    breakeven_count  = EXCLUDED.breakeven_count,
    total_count      = EXCLUDED.total_count,
    win_rate         = EXCLUDED.win_rate,
    avg_profit_pips  = EXCLUDED.avg_profit_pips,
    avg_rr_realized  = EXCLUDED.avg_rr_realized,
    confidence       = EXCLUDED.confidence,
    lookback_days    = EXCLUDED.lookback_days,
    computed_at      = EXCLUDED.computed_at
"""


def _build_upsert_params(b: FeatureBucket, computed_at: datetime) -> dict:
    return {
        "symbol": b.symbol,
        "feature_name": b.feature_name,
        "feature_value": b.feature_value,
        "win_count": b.win_count,
        "loss_count": b.loss_count,
        "breakeven_count": b.breakeven_count,
        "total_count": b.total_count,
        "win_rate": b.win_rate,
        "avg_profit_pips": b.avg_profit_pips,
        "avg_rr_realized": b.avg_rr_realized,
        "confidence": b.confidence,
        "lookback_days": LOOKBACK_DAYS,
        "computed_at": computed_at,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> int:
    """Returns exit code: 0 = success, 1 = error."""
    if not DATABASE_URL:
        log.error("DATABASE_URL is not set. Aborting.")
        return 1

    log.info(
        "Starting offline scorer — lookback=%d days, min_samples=%d, dry_run=%s",
        LOOKBACK_DAYS, MIN_SAMPLES, DRY_RUN,
    )

    try:
        conn = psycopg2.connect(DATABASE_URL)
    except Exception as exc:
        log.error("Cannot connect to database: %s", exc)
        return 1

    computed_at = datetime.now(timezone.utc)
    total_buckets = 0
    total_written = 0

    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                for feature_name, query_template in _FEATURE_QUERIES.items():
                    query = query_template.replace("{lookback}", str(LOOKBACK_DAYS))
                    try:
                        cur.execute(query)
                        rows = cur.fetchall()
                    except Exception as exc:
                        log.warning("Query failed for feature=%s: %s", feature_name, exc)
                        conn.rollback()
                        continue

                    buckets = _aggregate_rows(feature_name, rows)
                    total_buckets += len(buckets)

                    for key, b in buckets.items():
                        if b.total_count == 0:
                            continue

                        params = _build_upsert_params(b, computed_at)

                        if DRY_RUN:
                            log.info(
                                "[DRY] %s/%s/%s  win_rate=%.3f  n=%d  conf=%.2f  "
                                "avg_pips=%s  avg_rr=%s",
                                b.symbol, b.feature_name, b.feature_value,
                                b.win_rate, b.total_count, b.confidence,
                                b.avg_profit_pips, b.avg_rr_realized,
                            )
                        else:
                            try:
                                cur.execute(UPSERT_SQL, params)
                                total_written += 1
                            except Exception as exc:
                                log.warning(
                                    "Upsert failed for %s/%s/%s: %s",
                                    b.symbol, b.feature_name, b.feature_value, exc,
                                )
                                conn.rollback()

                    log.info(
                        "feature=%s  buckets=%d  rows_processed=%d",
                        feature_name, len(buckets), len(rows),
                    )

    finally:
        conn.close()

    log.info(
        "Scorer complete — total_buckets=%d  written=%d  dry_run=%s  computed_at=%s",
        total_buckets, total_written, DRY_RUN, computed_at.isoformat(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
