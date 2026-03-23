"""
🛡️ Tests for Risk Manager agent.

Covers: drawdown thresholds, volatility limits, stop-loss distance,
        warning zones, and score-based verdicts.
"""

from __future__ import annotations

import pytest

from agents.risk_manager import evaluate_risk
from models.schemas import Verdict, ExpertName


class TestRiskManagerVerdict:
    """Tests for the overall verdict logic."""

    def test_healthy_account_approves(self):
        """Low drawdown + low volatility → APPROVE."""
        opinion = evaluate_risk(
            equity=1000.0,
            balance=1000.0,  # No drawdown
            current_volatility=100.0,
        )
        assert opinion.verdict == Verdict.APPROVE
        assert opinion.expert == ExpertName.RISK_MANAGER
        assert opinion.confidence >= 0.7

    def test_high_drawdown_rejects(self):
        """Drawdown > 5% → REJECT."""
        opinion = evaluate_risk(
            equity=800.0,     # (1000-800)/1000 = 20% drawdown
            balance=1000.0,
            current_volatility=100.0,
        )
        assert opinion.verdict == Verdict.REJECT
        assert "Drawdown" in opinion.reason
        assert "excede" in opinion.reason

    def test_extreme_volatility_rejects(self):
        """Volatility > 200 + some drawdown → REJECT (score needs >= 0.5)."""
        opinion = evaluate_risk(
            equity=950.0,     # Small drawdown to push score over 0.5
            balance=1000.0,
            current_volatility=250.0,
        )
        assert opinion.verdict == Verdict.REJECT
        assert "Volatilidad" in opinion.reason

    def test_combined_high_risk_rejects(self):
        """High drawdown + high volatility → REJECT with high confidence."""
        opinion = evaluate_risk(
            equity=800.0,
            balance=1000.0,
            current_volatility=250.0,
        )
        assert opinion.verdict == Verdict.REJECT
        assert opinion.confidence >= 0.8

    def test_no_drawdown_with_zero_balance(self):
        """Edge case: balance = 0 should not crash (div by zero guard)."""
        opinion = evaluate_risk(
            equity=0.0,
            balance=0.0,
            current_volatility=50.0,
        )
        # Should not crash; drawdown = 0 when balance = 0
        assert opinion.verdict in (Verdict.APPROVE, Verdict.HOLD)


class TestRiskManagerWarningZones:
    """Tests for the intermediate warning zones."""

    def test_drawdown_warning_zone(self):
        """Drawdown between 3.5% and 5% → HOLD (warning zone is >70% of threshold)."""
        # 3.6% drawdown with default max_drawdown=5% → 72% of threshold
        opinion = evaluate_risk(
            equity=964.0,     # (1000-964)/1000 = 3.6%
            balance=1000.0,
            current_volatility=50.0,
        )
        assert opinion.verdict == Verdict.HOLD
        assert "Precaución" in opinion.reason

    def test_volatility_warning_zone(self):
        """Volatility between 160 and 200 → adds risk score but not immediate reject."""
        opinion = evaluate_risk(
            equity=1000.0,
            balance=1000.0,
            current_volatility=170.0,  # 85% of max 200
        )
        assert "Monitorear" in opinion.reason


class TestRiskManagerStopLoss:
    """Tests for the stop-loss distance evaluation."""

    def test_wide_stop_loss_adds_risk(self):
        """Stop-loss distance > 2% of entry adds risk."""
        opinion = evaluate_risk(
            equity=1000.0,
            balance=1000.0,
            current_volatility=50.0,
            entry_price=5000.0,
            stop_loss=4800.0,   # 200 / 5000 = 4% — exceeds 2%
        )
        assert "stop-loss" in opinion.reason.lower()

    def test_tight_stop_loss_no_extra_risk(self):
        """Stop-loss within 2% of entry should not add risk."""
        opinion = evaluate_risk(
            equity=1000.0,
            balance=1000.0,
            current_volatility=50.0,
            entry_price=5000.0,
            stop_loss=4960.0,   # 40/5000 = 0.8% — within 2%
        )
        assert opinion.verdict == Verdict.APPROVE

    def test_no_sl_provided(self):
        """When entry/SL not provided, skip SL check — don't penalize."""
        opinion = evaluate_risk(
            equity=1000.0,
            balance=1000.0,
            current_volatility=50.0,
            entry_price=0.0,
            stop_loss=0.0,
        )
        assert opinion.verdict == Verdict.APPROVE


class TestRiskManagerConfidence:
    """Tests for confidence calibration."""

    def test_approve_confidence_range(self):
        """APPROVE confidence should be between 0.7 and 1.0."""
        opinion = evaluate_risk(
            equity=1000.0, balance=1000.0, current_volatility=50.0
        )
        assert 0.7 <= opinion.confidence <= 1.0

    def test_reject_confidence_increases_with_score(self):
        """Higher risk score → higher rejection confidence."""
        mild = evaluate_risk(
            equity=940.0, balance=1000.0, current_volatility=210.0
        )
        severe = evaluate_risk(
            equity=700.0, balance=1000.0, current_volatility=300.0
        )
        assert severe.confidence >= mild.confidence
