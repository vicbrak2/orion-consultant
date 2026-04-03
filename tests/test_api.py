"""
🌌 Tests for the Committee (full pipeline via FastAPI).

Covers: committee endpoint, individual expert endpoint, health check,
        voting logic, and JSON contract validation.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from main import app
from models.schemas import Verdict


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


# ── Health Check ──────────────────────────────────────


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_returns_ok(self, client: TestClient):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "orion-consultant"
        assert "version" in data
        assert "timestamp" in data

    def test_metrics_endpoint_exposes_prometheus(self, client: TestClient):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "orion_consult_requests_total" in response.text
        assert "http_requests_total" in response.text or "http_request_duration" in response.text


# ── Committee Endpoint ────────────────────────────────


class TestCommitteeEndpoint:
    """Tests for POST /api/v1/consult."""

    def test_healthy_signal_returns_verdict(self, client: TestClient):
        """A valid signal should return a structured committee verdict."""
        payload = {
            "symbol": "Step Index",
            "direction": "BUY",
            "entry_price": 5432.10,
            "stop_loss": 5400.00,
            "take_profit": 5500.00,
            "equity": 1000.0,
            "balance": 1050.0,
            "current_volatility": 120.5,
            "trend_h1": "bullish",
            "trend_h4": "bullish",
        }
        response = client.post("/api/v1/consult", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert "final_verdict" in data
        assert data["final_verdict"] in ("APPROVE", "REJECT", "HOLD")
        assert "approved_count" in data
        assert "rejected_count" in data
        assert "opinions" in data
        assert len(data["opinions"]) == 3
        assert "summary" in data
        assert "timestamp" in data

    def test_risky_signal_multiple_rejects(self, client: TestClient):
        """High drawdown + counter-trend should get majority REJECT."""
        payload = {
            "symbol": "Step Index",
            "direction": "BUY",
            "entry_price": 5432.10,
            "stop_loss": 5300.00,
            "take_profit": 5500.00,
            "equity": 800.0,
            "balance": 1000.0,
            "current_volatility": 250.0,
            "trend_h1": "bearish",
            "trend_h4": "bearish",
        }
        response = client.post("/api/v1/consult", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert data["final_verdict"] == "REJECT"
        assert data["rejected_count"] >= 2

    def test_n8n_wrapped_signal_returns_verdict(self, client: TestClient):
        """n8n-style payloads wrapped under body should still validate."""
        payload = {
            "body": {
                "symbol": "Step Index",
                "direction": "BUY",
                "entry_price": 5432.10,
                "stop_loss": 5400.00,
                "take_profit": 5500.00,
                "equity": 1000.0,
                "balance": 1050.0,
                "current_volatility": 120.5,
                "trend_h1": "bullish",
                "trend_h4": "bullish",
            }
        }
        response = client.post("/api/v1/consult", json=payload)
        assert response.status_code == 200
        assert response.json()["final_verdict"] in ("APPROVE", "REJECT", "HOLD")

    def test_stringified_signal_returns_verdict(self, client: TestClient):
        """Stringified JSON payloads from n8n should be accepted."""
        payload = {
            "symbol": "Step Index",
            "direction": "BUY",
            "entry_price": 5432.10,
            "stop_loss": 5400.00,
            "take_profit": 5500.00,
            "equity": 1000.0,
            "balance": 1050.0,
            "current_volatility": 120.5,
            "trend_h1": "bullish",
            "trend_h4": "bullish",
        }
        response = client.post("/api/v1/consult", json=json.dumps(payload))
        assert response.status_code == 200
        assert response.json()["final_verdict"] in ("APPROVE", "REJECT", "HOLD")

    def test_missing_required_fields_returns_422(self, client: TestClient):
        """Missing required fields should return 422 Validation Error."""
        payload = {"symbol": "Step Index"}  # Missing direction, prices, etc.
        response = client.post("/api/v1/consult", json=payload)
        assert response.status_code == 422

    def test_invalid_direction_returns_422(self, client: TestClient):
        """Invalid direction enum value should return 422."""
        payload = {
            "symbol": "Step Index",
            "direction": "HODL",  # Not a valid SignalDirection
            "entry_price": 5432.10,
            "stop_loss": 5400.00,
            "take_profit": 5500.00,
            "equity": 1000.0,
            "balance": 1050.0,
        }
        response = client.post("/api/v1/consult", json=payload)
        assert response.status_code == 422


# ── Individual Expert Endpoint ────────────────────────


class TestIndividualExpertEndpoint:
    """Tests for POST /api/v1/consult/{expert_name}."""

    BASE_PAYLOAD = {
        "symbol": "Step Index",
        "direction": "BUY",
        "entry_price": 5432.10,
        "stop_loss": 5400.00,
        "take_profit": 5500.00,
        "equity": 1000.0,
        "balance": 1050.0,
        "current_volatility": 120.5,
        "trend_h1": "bullish",
        "trend_h4": "bullish",
    }

    @pytest.mark.parametrize("expert", ["risk_manager", "trend_analyzer", "pattern_expert"])
    def test_valid_expert_returns_opinion(self, client: TestClient, expert: str):
        """Each valid expert name should return a single opinion."""
        response = client.post(
            f"/api/v1/consult/{expert}", json=self.BASE_PAYLOAD
        )
        assert response.status_code == 200

        data = response.json()
        assert "expert" in data
        assert data["expert"] == expert
        assert "verdict" in data
        assert "confidence" in data
        assert "reason" in data

    def test_invalid_expert_returns_422(self, client: TestClient):
        """An unknown expert name should return 422."""
        response = client.post(
            "/api/v1/consult/psychic_advisor", json=self.BASE_PAYLOAD
        )
        assert response.status_code == 422


# ── JSON Contract Validation ─────────────────────────


class TestJSONContract:
    """Tests that the API JSON contract matches the schema docs."""

    def test_opinion_structure(self, client: TestClient):
        """Each opinion must have exactly: expert, verdict, confidence, reason."""
        payload = {
            "symbol": "Step Index",
            "direction": "BUY",
            "entry_price": 5000.0,
            "stop_loss": 4950.0,
            "take_profit": 5100.0,
            "equity": 1000.0,
            "balance": 1000.0,
            "current_volatility": 80.0,
            "trend_h1": "bullish",
            "trend_h4": "bullish",
        }
        response = client.post("/api/v1/consult", json=payload)
        data = response.json()

        for opinion in data["opinions"]:
            assert set(opinion.keys()) == {"expert", "verdict", "confidence", "reason"}

    def test_verdict_values_are_valid_enums(self, client: TestClient):
        """All verdict values should be valid enum members."""
        payload = {
            "symbol": "Step Index",
            "direction": "SELL",
            "entry_price": 5000.0,
            "stop_loss": 5050.0,
            "take_profit": 4900.0,
            "equity": 1000.0,
            "balance": 1000.0,
            "current_volatility": 80.0,
            "trend_h1": "bearish",
            "trend_h4": "bearish",
        }
        response = client.post("/api/v1/consult", json=payload)
        data = response.json()

        valid_verdicts = {"APPROVE", "REJECT", "HOLD"}
        assert data["final_verdict"] in valid_verdicts
        for opinion in data["opinions"]:
            assert opinion["verdict"] in valid_verdicts

    def test_confidence_within_bounds(self, client: TestClient):
        """All confidence values must be between 0.0 and 1.0."""
        payload = {
            "symbol": "Step Index",
            "direction": "BUY",
            "entry_price": 5000.0,
            "stop_loss": 4950.0,
            "take_profit": 5100.0,
            "equity": 1000.0,
            "balance": 1000.0,
        }
        response = client.post("/api/v1/consult", json=payload)
        data = response.json()

        for opinion in data["opinions"]:
            assert 0.0 <= opinion["confidence"] <= 1.0
