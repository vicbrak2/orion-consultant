"""
Tests for Orion's n8n integration facade.
"""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

import main
from main import app
from config import settings


def test_n8n_health_returns_up():
    client = TestClient(app)
    response = client.get("/api/n8n/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "UP"
    assert data["service"] == "orion-consultant"
    assert "timestamp" in data


def test_actuator_health_alias_matches_health_contract():
    client = TestClient(app)
    response = client.get("/actuator/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "orion-consultant"


def test_process_event_proxies_to_java(monkeypatch):
    client = TestClient(app)

    async def fake_post_json(url: str, payload, *, timeout: float):
        assert url.endswith("/api/n8n/process-event")
        assert payload["action"] == "EXECUTE"
        assert timeout == 15.0
        return {"processed": True, "status": "completed", "eventId": "evt-1"}

    monkeypatch.setattr(main, "_post_json", fake_post_json)

    response = client.post(
        "/api/n8n/process-event",
        json={"action": "EXECUTE", "signal": {"symbol": "Step Index"}},
    )

    assert response.status_code == 200
    assert response.json()["processed"] is True


def test_process_event_returns_accepted_when_java_fails(monkeypatch):
    client = TestClient(app)

    async def fake_post_json(url: str, payload, *, timeout: float):
        raise httpx.ConnectError("java offline")

    monkeypatch.setattr(main, "_post_json", fake_post_json)

    response = client.post(
        "/api/n8n/process-event",
        json={"action": "EXECUTE", "signal": {"symbol": "Step Index"}},
    )

    assert response.status_code == 202
    data = response.json()
    assert data["processed"] is False
    assert data["forwarded"] is False
    assert data["status"] == "accepted_but_not_forwarded"


def test_trigger_workflow_calls_n8n_webhook(monkeypatch):
    client = TestClient(app)

    async def fake_post_json(url: str, payload, *, timeout: float):
        assert url.endswith("/webhook/custom-flow")
        assert payload == {"foo": "bar"}
        assert timeout == 10.0
        return {"ok": True}

    monkeypatch.setattr(main, "_post_json", fake_post_json)

    response = client.post(
        "/api/n8n/trigger-workflow",
        json={"webhookPath": "/webhook/custom-flow", "payload": {"foo": "bar"}},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_trigger_workflow_rejects_absolute_webhook_url():
    client = TestClient(app)

    response = client.post(
        "/api/n8n/trigger-workflow",
        json={"webhookPath": "https://example.com/webhook", "payload": {"foo": "bar"}},
    )

    assert response.status_code == 400


def test_trigger_workflow_rejects_protocol_relative_webhook_url():
    client = TestClient(app)

    response = client.post(
        "/api/n8n/trigger-workflow",
        json={"webhookPath": "//example.com/webhook", "payload": {"foo": "bar"}},
    )

    assert response.status_code == 400


def test_n8n_status_reports_availability(monkeypatch):
    client = TestClient(app)

    async def fake_get_text(url: str, *, timeout: float):
        assert url.endswith("/healthz")
        assert timeout == 10.0
        return "ok"

    monkeypatch.setattr(main, "_get_text", fake_get_text)

    response = client.get("/api/n8n/n8n-status")

    assert response.status_code == 200
    assert response.json()["n8n_available"] is True


def test_agent_chat_uses_n8n_agent_webhook(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(settings, "n8n_agent_chat_enabled", True)

    async def fake_post_json(url: str, payload, *, timeout: float):
        assert url.endswith("/webhook/agent-chat")
        assert payload == {"message": "hola"}
        assert timeout == 20.0
        return {"reply": "ok"}

    monkeypatch.setattr(main, "_post_json", fake_post_json)

    response = client.post("/api/agent/chat", json={"message": "hola"})

    assert response.status_code == 200
    assert response.json() == {"reply": "ok"}


def test_agent_chat_requires_message():
    client = TestClient(app)

    response = client.post("/api/agent/chat", json={})

    assert response.status_code == 422


def test_agent_chat_returns_disabled_fallback(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(settings, "n8n_agent_chat_enabled", False)

    response = client.post("/api/agent/chat", json={"message": "hola"})

    assert response.status_code == 200
    assert response.json()["disabled"] is True
    assert response.json()["agent"] == "orion-local-fallback"


def test_notification_endpoints_forward_to_n8n(monkeypatch):
    client = TestClient(app)
    calls = []

    async def fake_post_without_response(url: str, payload, *, timeout: float):
        calls.append((url, payload, timeout))

    monkeypatch.setattr(main, "_post_without_response", fake_post_without_response)

    response = client.post(
        "/api/notifications/trading-decision",
        json={"symbol": "Step Index", "decision": "BUY"},
    )

    assert response.status_code == 202
    assert calls == [
        ("http://localhost:5678/webhook/trading-decision", {"symbol": "Step Index", "decision": "BUY"}, 10.0)
    ]


def test_trading_error_notification_forwards_to_expected_webhook(monkeypatch):
    client = TestClient(app)
    captured = {}

    async def fake_post_without_response(url: str, payload, *, timeout: float):
        captured["url"] = url
        captured["payload"] = payload
        captured["timeout"] = timeout

    monkeypatch.setattr(main, "_post_without_response", fake_post_without_response)

    response = client.post(
        "/api/notifications/trading-error",
        json={
            "errorType": "ORDER_REJECTED",
            "message": "Broker rejected order",
            "context": {"ticket": "T-1"},
        },
    )

    assert response.status_code == 202
    assert captured["url"].endswith("/webhook/trading-error")
    assert captured["payload"]["errorType"] == "ORDER_REJECTED"
    assert captured["timeout"] == 10.0
