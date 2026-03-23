"""
Orion Consultant — Centralized configuration via environment variables.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    # ── Server ────────────────────────────────────────
    host: str = Field(default="0.0.0.0", alias="ORION_HOST")
    port: int = Field(default=8100, alias="ORION_PORT")
    log_level: str = Field(default="info", alias="ORION_LOG_LEVEL")

    # ── Risk thresholds ───────────────────────────────
    max_drawdown: float = Field(default=0.05, alias="ORION_MAX_DRAWDOWN")
    max_volatility: float = Field(default=200.0, alias="ORION_MAX_VOLATILITY")

    # ── External services ─────────────────────────────
    n8n_base_url: str = Field(
        default="http://localhost:5678",
        alias="ORION_N8N_BASE_URL",
    )
    n8n_webhook_url: str = Field(
        default="http://localhost:5678/webhook",
        alias="ORION_N8N_WEBHOOK_URL",
    )
    n8n_health_path: str = Field(
        default="/healthz",
        alias="ORION_N8N_HEALTH_PATH",
    )
    n8n_agent_chat_webhook_path: str = Field(
        default="/webhook/agent-chat",
        alias="ORION_N8N_AGENT_CHAT_WEBHOOK_PATH",
    )
    n8n_trading_decision_webhook_path: str = Field(
        default="/webhook/trading-decision",
        alias="ORION_N8N_TRADING_DECISION_WEBHOOK_PATH",
    )
    n8n_trade_executed_webhook_path: str = Field(
        default="/webhook/trade-executed",
        alias="ORION_N8N_TRADE_EXECUTED_WEBHOOK_PATH",
    )
    n8n_trade_closed_webhook_path: str = Field(
        default="/webhook/trade-closed",
        alias="ORION_N8N_TRADE_CLOSED_WEBHOOK_PATH",
    )
    n8n_trading_error_webhook_path: str = Field(
        default="/webhook/trading-error",
        alias="ORION_N8N_TRADING_ERROR_WEBHOOK_PATH",
    )
    n8n_performance_metrics_webhook_path: str = Field(
        default="/webhook/performance-metrics",
        alias="ORION_N8N_PERFORMANCE_METRICS_WEBHOOK_PATH",
    )
    notification_timeout_seconds: float = Field(
        default=10.0,
        alias="ORION_NOTIFICATION_TIMEOUT_SECONDS",
    )
    java_bot_url: str = Field(
        default="http://localhost:8080",
        alias="ORION_JAVA_BOT_URL",
    )
    java_process_event_path: str = Field(
        default="/api/n8n/process-event",
        alias="ORION_JAVA_PROCESS_EVENT_PATH",
    )

    # ── MCP ───────────────────────────────────────────
    mcp_transport: str = Field(default="stdio", alias="ORION_MCP_TRANSPORT")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "populate_by_name": True,
        "extra": "ignore",
    }


# Singleton — importar como `from config import settings`
settings = Settings()
