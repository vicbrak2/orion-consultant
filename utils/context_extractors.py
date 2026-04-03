from __future__ import annotations

from typing import Any


def get_nested(data: dict[str, Any] | None, *path: str) -> Any:
    current: Any = data or {}
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def get_analysis_context(signal: Any) -> dict[str, Any]:
    ctx = getattr(signal, "analysis_context", None)
    return ctx if isinstance(ctx, dict) else {}


def get_episode_context(signal: Any) -> dict[str, Any]:
    ctx = getattr(signal, "episode_context", None)
    if isinstance(ctx, dict):
        return ctx
    ctx = getattr(signal, "episode_summary", None)
    return ctx if isinstance(ctx, dict) else {}


def get_episode_events(signal: Any) -> list[dict[str, Any]]:
    events = getattr(signal, "episode_events", None)
    if isinstance(events, list):
        return [event for event in events if isinstance(event, dict)]
    return []


def get_episode_checkpoints(signal: Any) -> list[dict[str, Any]]:
    checkpoints = getattr(signal, "episode_checkpoints", None)
    if isinstance(checkpoints, list):
        return [checkpoint for checkpoint in checkpoints if isinstance(checkpoint, dict)]
    return []


def get_confirmations(signal: Any) -> dict[str, Any]:
    confirmations = getattr(signal, "confirmations", None)
    return confirmations if isinstance(confirmations, dict) else {}


def get_snapshot(signal: Any, name: str) -> dict[str, Any]:
    ctx = get_analysis_context(signal)
    snapshot = ctx.get(name)
    return snapshot if isinstance(snapshot, dict) else {}


def get_decision_context(signal: Any) -> dict[str, Any]:
    ctx = getattr(signal, "decision_context", None)
    return ctx if isinstance(ctx, dict) else {}


def get_pattern_name(signal: Any) -> str | None:
    decision_context = get_decision_context(signal)
    confirmations = get_confirmations(signal)
    analysis_context = get_analysis_context(signal)
    return (
        decision_context.get("pattern")
        or get_nested(decision_context, "_strategy_context", "pattern")
        or confirmations.get("pattern_name")
        or get_nested(analysis_context, "analysis", "pattern")
    )
