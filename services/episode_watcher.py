"""
Episode Watcher — background scheduler that polls strategy_episodes for changes.

Architecture
------------
Runs as a single asyncio.Task inside the FastAPI lifespan (started on startup,
cancelled on shutdown). Every ``interval_sec`` seconds it queries Postgres for:

  1. Episodes updated since the last poll (watermark-based incremental load).
  2. Current open-position count per symbol.
  3. Win/loss stats over the last 20 closed episodes per symbol.

The result is stored in a per-symbol ``EpisodeSnapshot`` cache that is readable
from anywhere in the Orion process. The ``_compute_strategic_bias`` function in
``main.py`` uses this cache to enrich LLM prompts with live DB context instead
of relying on the n8n RAG webhook.

Resilience
----------
- Any DB error is logged and swallowed; the watcher resumes on the next tick.
- The pool is None-safe: if Postgres is unreachable at startup the watcher
  never starts, and callers receive None from ``get_snapshot()``.
- Works with ``--workers N``: each worker runs its own watcher (acceptable
  redundancy for a read-only polling pattern).

Public API
----------
    from services.episode_watcher import get_snapshot, EpisodeSnapshot, start, stop

    snap = get_snapshot("Step Index")   # None if never polled
    if snap:
        print(snap.as_rag_context())    # formatted for LLM prompt injection
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("orion.episode_watcher")

# ---------------------------------------------------------------------------
# Snapshot model
# ---------------------------------------------------------------------------


@dataclass
class EpisodeSnapshot:
    """Cached episode state for a single trading symbol."""

    symbol: str
    open_count: int = 0
    last_state: str = "UNKNOWN"
    last_action: str | None = None
    last_outcome: str = "UNKNOWN"
    last_realized_pnl: float | None = None
    recent_wins: int = 0
    recent_losses: int = 0
    recent_total: int = 0
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def win_rate(self) -> float | None:
        total = self.recent_wins + self.recent_losses
        return round(self.recent_wins / total, 2) if total > 0 else None

    def as_rag_context(self) -> str:
        """One-paragraph summary suitable for LLM prompt injection."""
        wr = self.win_rate
        wr_str = f"{wr:.0%} ({self.recent_wins}W/{self.recent_losses}L, last {self.recent_total})" if wr is not None else "n/a"
        pnl_str = f"{self.last_realized_pnl:+.2f}" if self.last_realized_pnl is not None else "n/a"
        return (
            f"[Episode DB] {self.symbol}: "
            f"open={self.open_count} | "
            f"last_state={self.last_state} last_action={self.last_action} | "
            f"last_outcome={self.last_outcome} pnl={pnl_str} | "
            f"win_rate={wr_str}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "open_count": self.open_count,
            "last_state": self.last_state,
            "last_action": self.last_action,
            "last_outcome": self.last_outcome,
            "last_realized_pnl": self.last_realized_pnl,
            "recent_wins": self.recent_wins,
            "recent_losses": self.recent_losses,
            "recent_total": self.recent_total,
            "win_rate": self.win_rate,
            "last_updated": self.last_updated.isoformat(),
        }


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_snapshots: dict[str, EpisodeSnapshot] = {}
_watermark: datetime = datetime(1970, 1, 1, tzinfo=timezone.utc)
_task: asyncio.Task | None = None


def get_snapshot(symbol: str) -> EpisodeSnapshot | None:
    """Return the cached snapshot for *symbol*, or None if never polled."""
    return _snapshots.get(symbol)


def get_all_snapshots() -> dict[str, dict[str, Any]]:
    """Return all cached snapshots as plain dicts (for the status endpoint)."""
    return {sym: snap.as_dict() for sym, snap in _snapshots.items()}


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------

_UPDATED_SINCE_SQL = """
    SELECT symbol, state, action, outcome_class, realized_pnl, updated_at
    FROM strategy_episodes
    WHERE updated_at > $1
      AND symbol IS NOT NULL
    ORDER BY updated_at ASC
"""

_OPEN_COUNTS_SQL = """
    SELECT symbol, COUNT(*) AS cnt
    FROM strategy_episodes
    WHERE state = 'ACTIVE'
      AND symbol IS NOT NULL
    GROUP BY symbol
"""

_WIN_LOSS_SQL = """
    WITH ranked AS (
        SELECT
            symbol,
            outcome_class,
            ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY closed_at DESC NULLS LAST) AS rn
        FROM strategy_episodes
        WHERE state = 'CLOSED'
          AND symbol IS NOT NULL
          AND outcome_class IS NOT NULL
    )
    SELECT
        symbol,
        SUM(CASE WHEN outcome_class = 'WIN'  THEN 1 ELSE 0 END)::int AS wins,
        SUM(CASE WHEN outcome_class = 'LOSS' THEN 1 ELSE 0 END)::int AS losses,
        COUNT(*)::int AS total
    FROM ranked
    WHERE rn <= 20
    GROUP BY symbol
"""


async def _refresh(pool: Any) -> None:
    """One poll cycle — fetch changes and update the in-memory cache."""
    global _watermark

    try:
        async with pool.acquire() as conn:
            changed_rows = await conn.fetch(_UPDATED_SINCE_SQL, _watermark)
            open_rows = await conn.fetch(_OPEN_COUNTS_SQL)
            wl_rows = await conn.fetch(_WIN_LOSS_SQL)
    except Exception as exc:
        logger.warning("episode_watcher DB query failed: %s", exc)
        return

    # Index helper queries
    open_by_sym: dict[str, int] = {r["symbol"]: r["cnt"] for r in open_rows}
    wl_by_sym: dict[str, tuple[int, int, int]] = {
        r["symbol"]: (r["wins"], r["losses"], r["total"]) for r in wl_rows
    }

    if not changed_rows and not open_rows:
        return  # nothing to do

    # Always update open_count for all known symbols (it changes every tick)
    for sym in set(list(open_by_sym) + list(_snapshots)):
        snap = _snapshots.setdefault(sym, EpisodeSnapshot(symbol=sym))
        snap.open_count = open_by_sym.get(sym, 0)

    # Process episode changes
    new_watermark = _watermark
    for row in changed_rows:
        sym = row["symbol"]
        snap = _snapshots.setdefault(sym, EpisodeSnapshot(symbol=sym))

        prev_state = snap.last_state
        snap.last_state = row["state"]
        snap.last_action = row["action"]
        snap.last_outcome = row["outcome_class"] or "UNKNOWN"
        snap.last_realized_pnl = row["realized_pnl"]
        snap.open_count = open_by_sym.get(sym, snap.open_count)

        wins, losses, total = wl_by_sym.get(sym, (0, 0, 0))
        snap.recent_wins = wins
        snap.recent_losses = losses
        snap.recent_total = total
        snap.last_updated = datetime.now(timezone.utc)

        # Advance watermark
        row_ts: datetime = row["updated_at"]
        if row_ts and row_ts > new_watermark:
            new_watermark = row_ts

        # Log state transitions
        if prev_state != snap.last_state:
            logger.info(
                "episode_transition symbol=%s %s→%s action=%s outcome=%s pnl=%s wr=%s",
                sym,
                prev_state,
                snap.last_state,
                snap.last_action,
                snap.last_outcome,
                f"{snap.last_realized_pnl:+.2f}" if snap.last_realized_pnl is not None else "n/a",
                f"{snap.win_rate:.0%}" if snap.win_rate is not None else "n/a",
            )
        else:
            logger.debug(
                "episode_update symbol=%s state=%s open=%d wr=%s",
                sym,
                snap.last_state,
                snap.open_count,
                f"{snap.win_rate:.0%}" if snap.win_rate is not None else "n/a",
            )

    _watermark = new_watermark


# ---------------------------------------------------------------------------
# Lifecycle helpers called from main.py lifespan
# ---------------------------------------------------------------------------


async def _run_loop(pool: Any, interval_sec: int) -> None:
    """Inner loop: poll, sleep, repeat until cancelled."""
    logger.info("episode_watcher started (interval=%ds)", interval_sec)
    # First tick immediately so the cache is warm before the first request
    await _refresh(pool)
    while True:
        await asyncio.sleep(interval_sec)
        await _refresh(pool)


def start(pool: Any, interval_sec: int = 30) -> asyncio.Task | None:
    """Start the watcher as a background asyncio task.

    Returns the Task so the lifespan can cancel it on shutdown.
    Returns None if pool is None (watcher disabled).
    """
    global _task
    if pool is None:
        logger.info("episode_watcher disabled (no DB pool)")
        return None
    _task = asyncio.create_task(
        _run_loop(pool, interval_sec),
        name="episode_watcher",
    )
    return _task


async def stop() -> None:
    """Cancel the background task and await its termination."""
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    logger.info("episode_watcher stopped")
