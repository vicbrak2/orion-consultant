"""
📊 Orion Consultant — Prometheus Metrics.

Custom business metrics for monitoring the Expert Committee pipeline.
HTTP-level metrics are auto-instrumented by prometheus-fastapi-instrumentator.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram, Gauge, Info

# ── Service info ──────────────────────────────────────

ORION_INFO = Info(
    "orion_consultant",
    "Orion Consultant service metadata",
)

# ── Request counters ──────────────────────────────────

CONSULT_REQUESTS_TOTAL = Counter(
    "orion_consult_requests_total",
    "Total /api/v1/consult requests received",
    ["symbol", "direction"],
)

CONSULT_VERDICTS_TOTAL = Counter(
    "orion_consult_verdicts_total",
    "Total committee verdicts by outcome",
    ["final_verdict"],
)

EXPERT_VERDICTS_TOTAL = Counter(
    "orion_expert_verdicts_total",
    "Individual expert verdicts by expert and outcome",
    ["expert", "verdict"],
)

# ── Enrichment tracking ──────────────────────────────

ENRICHED_REQUESTS_TOTAL = Counter(
    "orion_enriched_requests_total",
    "Requests that included enrichment fields (fsm_phase, bias, etc.)",
)

LEAN_REQUESTS_TOTAL = Counter(
    "orion_lean_requests_total",
    "Requests with no enrichment fields (backward-compatible base payloads)",
)

ENRICHMENT_FIELDS_PRESENT = Counter(
    "orion_enrichment_field_present_total",
    "How often each enrichment field is present in requests",
    ["field_name"],
)

# ── Latency histograms ───────────────────────────────

CONSULT_LATENCY = Histogram(
    "orion_consult_duration_seconds",
    "Time to process a /api/v1/consult request (full committee)",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

EXPERT_LATENCY = Histogram(
    "orion_expert_duration_seconds",
    "Time to evaluate a single expert",
    ["expert"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

LLM_CALL_LATENCY = Histogram(
    "orion_llm_call_duration_seconds",
    "Groq LLM call latency",
    ["expert"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# ── Confidence distribution ──────────────────────────

EXPERT_CONFIDENCE = Histogram(
    "orion_expert_confidence",
    "Distribution of expert confidence scores",
    ["expert", "verdict"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

# ── LLM health ───────────────────────────────────────

LLM_CALLS_TOTAL = Counter(
    "orion_llm_calls_total",
    "Total LLM calls attempted",
    ["expert", "status"],  # status: success, fallback
)

# ── Relay / integration metrics ──────────────────────

RELAY_REQUESTS_TOTAL = Counter(
    "orion_relay_requests_total",
    "Total relay requests handled by Orion",
    ["route", "target", "status"],
)

RELAY_LATENCY = Histogram(
    "orion_relay_duration_seconds",
    "Relay latency from Orion to downstream services",
    ["route", "target"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

N8N_HEALTHCHECK_TOTAL = Counter(
    "orion_n8n_healthcheck_total",
    "n8n health checks from Orion",
    ["status"],
)

JAVA_PROCESS_EVENT_TOTAL = Counter(
    "orion_java_process_event_total",
    "Forwarding results to Java /api/n8n/process-event",
    ["status"],
)

NOTIFICATION_REQUESTS_TOTAL = Counter(
    "orion_notification_requests_total",
    "Notification relay requests handled by Orion",
    ["notification_type", "status"],
)

AGENT_CHAT_REQUESTS_TOTAL = Counter(
    "orion_agent_chat_requests_total",
    "Agent chat requests proxied to n8n",
    ["status"],
)

AGENT_CHAT_LATENCY = Histogram(
    "orion_agent_chat_duration_seconds",
    "Latency of agent chat requests proxied to n8n",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0],
)

# ── Live gauges ───────────────────────────────────────

LAST_CONSULT_TIMESTAMP = Gauge(
    "orion_last_consult_timestamp_seconds",
    "Unix timestamp of the last consult request processed",
)


# ── Helper functions ──────────────────────────────────

_ENRICHMENT_FIELDS = [
    "trace_id", "strategy_id", "fsm_phase", "step_index_type",
    "current_clv", "previous_clv", "macro_structure_ok",
    "sar_adx_signal", "sar_adx_blocking", "adx_m15", "plus_di_m15",
    "minus_di_m15", "atr_m15", "range_to_atr", "bb_kc_ratio",
    "bias", "entry_window_open", "tactical_confidence",
    "decision_context", "episode_summary", "account_context",
]


def track_enrichment(signal) -> None:
    """Count which enrichment fields are present in a SignalRequest."""
    has_any = False
    for field in _ENRICHMENT_FIELDS:
        value = getattr(signal, field, None)
        if value is not None:
            has_any = True
            ENRICHMENT_FIELDS_PRESENT.labels(field_name=field).inc()
    if has_any:
        ENRICHED_REQUESTS_TOTAL.inc()
    else:
        LEAN_REQUESTS_TOTAL.inc()


def track_expert_opinion(opinion) -> None:
    """Record verdict, confidence, and counter for an expert opinion."""
    EXPERT_VERDICTS_TOTAL.labels(
        expert=opinion.expert.value,
        verdict=opinion.verdict.value,
    ).inc()
    EXPERT_CONFIDENCE.labels(
        expert=opinion.expert.value,
        verdict=opinion.verdict.value,
    ).observe(opinion.confidence)


def initialize_integration_metrics() -> None:
    """Create zero-valued relay series so Grafana does not render empty panels."""
    for route, target in [
        ("process_event", "java"),
        ("trigger_workflow", "n8n"),
        ("agent_chat", "n8n"),
        ("notification", "n8n"),
    ]:
        RELAY_LATENCY.labels(route=route, target=target)
        for status in ["success", "error", "fallback", "disabled"]:
            RELAY_REQUESTS_TOTAL.labels(route=route, target=target, status=status)

    for status in ["up", "down"]:
        N8N_HEALTHCHECK_TOTAL.labels(status=status)

    for status in ["success", "fallback"]:
        JAVA_PROCESS_EVENT_TOTAL.labels(status=status)

    for notification_type in [
        "trading-decision",
        "trade-executed",
        "trade-closed",
        "trading-error",
        "performance-metrics",
    ]:
        for status in ["success", "error"]:
            NOTIFICATION_REQUESTS_TOTAL.labels(
                notification_type=notification_type,
                status=status,
            )

    for status in ["success", "error", "disabled"]:
        AGENT_CHAT_REQUESTS_TOTAL.labels(status=status)
