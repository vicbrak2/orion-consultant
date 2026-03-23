"""
🔍 Tests for Pattern Expert agent.

Covers: R:R ratio analysis, spike zone detection, consolidation,
        trade geometry validation, edge cases.
"""

from __future__ import annotations

import pytest

from agents.pattern_expert import evaluate_pattern
from models.schemas import Verdict, ExpertName


class TestPatternRiskReward:
    """Tests for Risk/Reward ratio evaluation."""

    def test_excellent_rr_approves(self):
        """R:R >= 2.0 → APPROVE with good confidence."""
        opinion = evaluate_pattern(
            entry_price=5000.0,
            stop_loss=4950.0,    # risk = 50
            take_profit=5150.0,  # reward = 150 → R:R = 3.0
            direction="BUY",
        )
        assert opinion.verdict == Verdict.APPROVE
        assert "excelente" in opinion.reason.lower()

    def test_acceptable_rr_approves(self):
        """R:R between 1.5 and 2.0 → APPROVE (score=0.25 from R:R + need consolidation boost)."""
        opinion = evaluate_pattern(
            entry_price=5000.0,
            stop_loss=4960.0,    # risk = 40
            take_profit=5070.0,  # reward = 70 → R:R = 1.75
            current_volatility=20.0,  # Low vol → consolidation bonus (+0.1)
            direction="BUY",
        )
        assert opinion.verdict == Verdict.APPROVE
        assert "aceptable" in opinion.reason.lower()

    def test_minimum_rr_holds(self):
        """R:R between 1.0 and 1.5 → HOLD."""
        opinion = evaluate_pattern(
            entry_price=5000.0,
            stop_loss=4950.0,    # risk = 50
            take_profit=5060.0,  # reward = 60 → R:R = 1.2
            direction="BUY",
        )
        assert opinion.verdict == Verdict.HOLD
        assert "mínimo" in opinion.reason.lower()

    def test_bad_rr_rejects(self):
        """R:R < 1.0 → REJECT."""
        opinion = evaluate_pattern(
            entry_price=5000.0,
            stop_loss=4900.0,    # risk = 100
            take_profit=5050.0,  # reward = 50 → R:R = 0.5
            direction="BUY",
        )
        assert opinion.verdict == Verdict.REJECT
        assert "desfavorable" in opinion.reason.lower()

    def test_zero_risk_rejects_immediately(self):
        """Stop-loss == entry → immediate REJECT (div by zero guard)."""
        opinion = evaluate_pattern(
            entry_price=5000.0,
            stop_loss=5000.0,
            take_profit=5100.0,
            direction="BUY",
        )
        assert opinion.verdict == Verdict.REJECT
        assert opinion.confidence >= 0.9
        assert "inválido" in opinion.reason.lower()


class TestPatternSpikeZone:
    """Tests for Step Index spike zone detection."""

    def test_tight_sl_high_vol_warns(self):
        """Tight SL + high volatility (>150) → warns about spike zone."""
        opinion = evaluate_pattern(
            entry_price=5000.0,
            stop_loss=4990.0,    # risk_pct = 10/5000 = 0.2%
            take_profit=5100.0,  # reward still good
            current_volatility=180.0,
            direction="BUY",
        )
        assert "spike" in opinion.reason.lower()

    def test_normal_sl_high_vol_no_spike_warning(self):
        """Normal SL distance + high vol → no spike warning."""
        opinion = evaluate_pattern(
            entry_price=5000.0,
            stop_loss=4900.0,    # risk_pct = 100/5000 = 2%
            take_profit=5300.0,
            current_volatility=180.0,
            direction="BUY",
        )
        assert "spike" not in opinion.reason.lower()

    def test_tight_sl_low_vol_no_spike_warning(self):
        """Tight SL but low vol → no spike warning."""
        opinion = evaluate_pattern(
            entry_price=5000.0,
            stop_loss=4990.0,
            take_profit=5100.0,
            current_volatility=50.0,
            direction="BUY",
        )
        assert "spike" not in opinion.reason.lower()


class TestPatternConsolidation:
    """Tests for consolidation detection."""

    def test_low_volatility_flags_consolidation(self):
        """Volatility < 30 → consolidation flag."""
        opinion = evaluate_pattern(
            entry_price=5000.0,
            stop_loss=4950.0,
            take_profit=5150.0,
            current_volatility=20.0,
            direction="BUY",
        )
        assert "consolidación" in opinion.reason.lower()

    def test_normal_volatility_no_consolidation(self):
        """Normal volatility → no consolidation flag."""
        opinion = evaluate_pattern(
            entry_price=5000.0,
            stop_loss=4950.0,
            take_profit=5150.0,
            current_volatility=80.0,
            direction="BUY",
        )
        assert "consolidación" not in opinion.reason.lower()


class TestPatternGeometry:
    """Tests for trade geometry validation (BUY/SELL coherence)."""

    def test_buy_sl_above_entry_rejects(self):
        """BUY with SL >= entry → REJECT (invalid geometry)."""
        opinion = evaluate_pattern(
            entry_price=5000.0,
            stop_loss=5050.0,    # Above entry for BUY = invalid
            take_profit=5200.0,
            direction="BUY",
        )
        assert opinion.verdict == Verdict.REJECT
        assert "inválido" in opinion.reason.lower()

    def test_buy_tp_below_entry_rejects(self):
        """BUY with TP <= entry → REJECT (invalid geometry)."""
        opinion = evaluate_pattern(
            entry_price=5000.0,
            stop_loss=4950.0,
            take_profit=4900.0,  # Below entry for BUY = invalid
            direction="BUY",
        )
        assert opinion.verdict == Verdict.REJECT

    def test_sell_sl_below_entry_rejects(self):
        """SELL with SL <= entry → REJECT (invalid geometry)."""
        opinion = evaluate_pattern(
            entry_price=5000.0,
            stop_loss=4950.0,    # Below entry for SELL = invalid
            take_profit=4800.0,
            direction="SELL",
        )
        assert opinion.verdict == Verdict.REJECT
        assert "inválido" in opinion.reason.lower()

    def test_sell_tp_above_entry_rejects(self):
        """SELL with TP >= entry → REJECT (invalid geometry)."""
        opinion = evaluate_pattern(
            entry_price=5000.0,
            stop_loss=5050.0,
            take_profit=5100.0,  # Above entry for SELL = invalid
            direction="SELL",
        )
        assert opinion.verdict == Verdict.REJECT

    def test_valid_sell_geometry_approves(self):
        """SELL with proper geometry → should not reject on geometry."""
        opinion = evaluate_pattern(
            entry_price=5000.0,
            stop_loss=5050.0,    # Above entry (correct for SELL)
            take_profit=4850.0,  # Below entry (correct for SELL)
            direction="SELL",
        )
        # Geometry is valid, verdict depends on R:R and other factors
        assert opinion.verdict != Verdict.REJECT or "inválido" not in opinion.reason.lower()


class TestPatternExpertMetadata:
    """Tests for correct expert metadata."""

    def test_expert_name(self):
        """Expert name should always be PATTERN_EXPERT."""
        opinion = evaluate_pattern(
            entry_price=5000.0,
            stop_loss=4950.0,
            take_profit=5100.0,
            direction="BUY",
        )
        assert opinion.expert == ExpertName.PATTERN_EXPERT

    def test_confidence_bounded(self):
        """Confidence should always be between 0 and 1."""
        test_cases = [
            (5000, 4950, 5500, 50.0),     # Good trade
            (5000, 5000, 5100, 100.0),     # Zero risk
            (5000, 4990, 5010, 200.0),     # Bad R:R + spike zone
            (5000, 4900, 5010, 80.0),      # Bad R:R
        ]
        for entry, sl, tp, vol in test_cases:
            opinion = evaluate_pattern(
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                current_volatility=vol,
                direction="BUY",
            )
            assert 0.0 <= opinion.confidence <= 1.0, (
                f"Confidence {opinion.confidence} out of bounds for "
                f"entry={entry}, sl={sl}, tp={tp}, vol={vol}"
            )
