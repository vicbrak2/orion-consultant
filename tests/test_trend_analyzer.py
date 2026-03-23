"""
📈 Tests for Trend Analyzer agent.

Covers: multi-timeframe alignment, flexible input parsing,
        counter-trend detection, neutral/no-data handling.
"""

from __future__ import annotations

import pytest

from agents.trend_analyzer import evaluate_trend
from models.schemas import Verdict, ExpertName


class TestTrendAlignment:
    """Tests for H1/H4 alignment logic."""

    def test_full_alignment_buy(self):
        """BUY + bullish H1 + bullish H4 → APPROVE."""
        opinion = evaluate_trend(
            direction="BUY",
            trend_h1="bullish",
            trend_h4="bullish",
        )
        assert opinion.verdict == Verdict.APPROVE
        assert opinion.confidence >= 0.85
        assert "completa" in opinion.reason.lower()

    def test_full_alignment_sell(self):
        """SELL + bearish H1 + bearish H4 → APPROVE."""
        opinion = evaluate_trend(
            direction="SELL",
            trend_h1="bearish",
            trend_h4="bearish",
        )
        assert opinion.verdict == Verdict.APPROVE
        assert opinion.confidence >= 0.85

    def test_partial_alignment(self):
        """BUY + bullish H1 + bearish H4 → HOLD."""
        opinion = evaluate_trend(
            direction="BUY",
            trend_h1="bullish",
            trend_h4="bearish",
        )
        assert opinion.verdict == Verdict.HOLD
        assert "parcial" in opinion.reason.lower()

    def test_counter_trend_both(self):
        """BUY + bearish H1 + bearish H4 → REJECT."""
        opinion = evaluate_trend(
            direction="BUY",
            trend_h1="bearish",
            trend_h4="bearish",
        )
        assert opinion.verdict == Verdict.REJECT
        assert opinion.confidence >= 0.7

    def test_single_timeframe_aligned(self):
        """BUY + bullish H1 + neutral H4 → APPROVE with lower confidence."""
        opinion = evaluate_trend(
            direction="BUY",
            trend_h1="bullish",
            trend_h4=None,
        )
        assert opinion.verdict == Verdict.APPROVE
        assert opinion.confidence < 0.90  # Lower than full alignment


class TestTrendNoData:
    """Tests for missing timeframe data."""

    def test_no_trends_at_all(self):
        """No H1/H4 data → HOLD with low confidence."""
        opinion = evaluate_trend(
            direction="BUY",
            trend_h1=None,
            trend_h4=None,
        )
        assert opinion.verdict == Verdict.HOLD
        assert opinion.confidence <= 0.5
        assert "Sin datos" in opinion.reason

    def test_neutral_trends_treated_as_no_data(self):
        """Explicit 'neutral' trends → same as no data (HOLD)."""
        opinion = evaluate_trend(
            direction="SELL",
            trend_h1="neutral",
            trend_h4="neutral",
        )
        assert opinion.verdict == Verdict.HOLD


class TestTrendFlexibleInput:
    """Tests for flexible input parsing (aliases)."""

    @pytest.mark.parametrize("alias", ["bullish", "bull", "up", "long", "alcista"])
    def test_bullish_aliases(self, alias: str):
        """All bullish aliases should be recognized."""
        opinion = evaluate_trend(
            direction="BUY",
            trend_h1=alias,
            trend_h4=alias,
        )
        assert opinion.verdict == Verdict.APPROVE

    @pytest.mark.parametrize("alias", ["bearish", "bear", "down", "short", "bajista"])
    def test_bearish_aliases(self, alias: str):
        """All bearish aliases should be recognized."""
        opinion = evaluate_trend(
            direction="SELL",
            trend_h1=alias,
            trend_h4=alias,
        )
        assert opinion.verdict == Verdict.APPROVE

    def test_case_insensitive(self):
        """Input should be case-insensitive."""
        opinion = evaluate_trend(
            direction="BUY",
            trend_h1="BULLISH",
            trend_h4="Bullish",
        )
        assert opinion.verdict == Verdict.APPROVE

    def test_whitespace_handling(self):
        """Leading/trailing whitespace should be trimmed."""
        opinion = evaluate_trend(
            direction="BUY",
            trend_h1="  bullish  ",
            trend_h4="bullish",
        )
        assert opinion.verdict == Verdict.APPROVE


class TestTrendInvalidDirection:
    """Tests for unknown signal directions."""

    def test_unknown_direction_rejects(self):
        """An unrecognized signal direction → REJECT."""
        opinion = evaluate_trend(
            direction="HODL",
            trend_h1="bullish",
            trend_h4="bullish",
        )
        assert opinion.verdict == Verdict.REJECT
        assert "desconocida" in opinion.reason.lower()


class TestTrendExpertMetadata:
    """Tests for correct expert metadata."""

    def test_expert_name(self):
        """Expert name should always be TREND_ANALYZER."""
        opinion = evaluate_trend(direction="BUY", trend_h1="bullish")
        assert opinion.expert == ExpertName.TREND_ANALYZER
