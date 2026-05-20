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

    @pytest.mark.asyncio

    async def test_full_alignment_buy(self):
        """BUY + bullish H1 + bullish H4 → APPROVE."""
        opinion = await evaluate_trend(
            direction="BUY",
            trend_h1="bullish",
            trend_h4="bullish",
        )
        assert opinion.verdict == Verdict.APPROVE
        assert opinion.confidence >= 0.85
        assert "completa" in opinion.reason.lower()

    @pytest.mark.asyncio

    async def test_full_alignment_sell(self):
        """SELL + bearish H1 + bearish H4 → APPROVE."""
        opinion = await evaluate_trend(
            direction="SELL",
            trend_h1="bearish",
            trend_h4="bearish",
        )
        assert opinion.verdict == Verdict.APPROVE
        assert opinion.confidence >= 0.85

    @pytest.mark.asyncio

    async def test_partial_alignment(self):
        """BUY + bullish H1 + bearish H4 → HOLD."""
        opinion = await evaluate_trend(
            direction="BUY",
            trend_h1="bullish",
            trend_h4="bearish",
        )
        assert opinion.verdict == Verdict.HOLD
        assert "parcial" in opinion.reason.lower()

    @pytest.mark.asyncio

    async def test_counter_trend_both(self):
        """BUY + bearish H1 + bearish H4 → REJECT."""
        opinion = await evaluate_trend(
            direction="BUY",
            trend_h1="bearish",
            trend_h4="bearish",
        )
        assert opinion.verdict == Verdict.REJECT
        assert opinion.confidence >= 0.7

    @pytest.mark.asyncio

    async def test_single_timeframe_aligned(self):
        """BUY + bullish H1 + neutral H4 → APPROVE with lower confidence."""
        opinion = await evaluate_trend(
            direction="BUY",
            trend_h1="bullish",
            trend_h4=None,
        )
        assert opinion.verdict == Verdict.APPROVE
        assert opinion.confidence < 0.90  # Lower than full alignment


class TestTrendNoData:
    """Tests for missing timeframe data."""

    @pytest.mark.asyncio

    async def test_no_trends_at_all(self):
        """No H1/H4 data → HOLD with low confidence."""
        opinion = await evaluate_trend(
            direction="BUY",
            trend_h1=None,
            trend_h4=None,
        )
        assert opinion.verdict == Verdict.HOLD
        assert opinion.confidence <= 0.5
        assert "Sin datos" in opinion.reason

    @pytest.mark.asyncio

    async def test_neutral_trends_treated_as_no_data(self):
        """Explicit 'neutral' trends → same as no data (HOLD)."""
        opinion = await evaluate_trend(
            direction="SELL",
            trend_h1="neutral",
            trend_h4="neutral",
        )
        assert opinion.verdict == Verdict.HOLD


class TestTrendFlexibleInput:
    """Tests for flexible input parsing (aliases)."""

    @pytest.mark.parametrize("alias", ["bullish", "bull", "up", "long", "alcista"])
    @pytest.mark.asyncio
    async def test_bullish_aliases(self, alias: str):
        """All bullish aliases should be recognized."""
        opinion = await evaluate_trend(
            direction="BUY",
            trend_h1=alias,
            trend_h4=alias,
        )
        assert opinion.verdict == Verdict.APPROVE

    @pytest.mark.parametrize("alias", ["bearish", "bear", "down", "short", "bajista"])
    @pytest.mark.asyncio
    async def test_bearish_aliases(self, alias: str):
        """All bearish aliases should be recognized."""
        opinion = await evaluate_trend(
            direction="SELL",
            trend_h1=alias,
            trend_h4=alias,
        )
        assert opinion.verdict == Verdict.APPROVE

    @pytest.mark.asyncio

    async def test_case_insensitive(self):
        """Input should be case-insensitive."""
        opinion = await evaluate_trend(
            direction="BUY",
            trend_h1="BULLISH",
            trend_h4="Bullish",
        )
        assert opinion.verdict == Verdict.APPROVE

    @pytest.mark.asyncio

    async def test_whitespace_handling(self):
        """Leading/trailing whitespace should be trimmed."""
        opinion = await evaluate_trend(
            direction="BUY",
            trend_h1="  bullish  ",
            trend_h4="bullish",
        )
        assert opinion.verdict == Verdict.APPROVE


class TestTrendInvalidDirection:
    """Tests for unknown signal directions."""

    @pytest.mark.asyncio

    async def test_unknown_direction_rejects(self):
        """An unrecognized signal direction → REJECT."""
        opinion = await evaluate_trend(
            direction="HODL",
            trend_h1="bullish",
            trend_h4="bullish",
        )
        assert opinion.verdict == Verdict.REJECT
        assert "desconocida" in opinion.reason.lower()


class TestTrendExpertMetadata:
    """Tests for correct expert metadata."""

    @pytest.mark.asyncio

    async def test_expert_name(self):
        """Expert name should always be TREND_ANALYZER."""
        opinion = await evaluate_trend(direction="BUY", trend_h1="bullish")
        assert opinion.expert == ExpertName.TREND_ANALYZER


class TestTrendEnrichment:
    """Tests for enriched context fields (bias, CLV, macro_structure, entry_window)."""

    @pytest.mark.asyncio
    async def test_partial_alignment_promoted_by_enrichment(self):
        """Partial H1/H4 alignment + bias+CLV+macro → APPROVE instead of HOLD."""
        opinion = await evaluate_trend(
            direction="BUY",
            trend_h1="bullish",
            trend_h4="bearish",
            bias=1,
            current_clv=0.72,
            previous_clv=0.41,
            entry_window_open=True,
            macro_structure_ok=True,
        )
        assert opinion.verdict == Verdict.APPROVE
        assert "bias" in opinion.reason.lower() or "clv" in opinion.reason.lower()

    @pytest.mark.asyncio
    async def test_full_alignment_boosted_by_enrichment(self):
        """Full alignment + enrichment → higher confidence."""
        base = await evaluate_trend(direction="BUY", trend_h1="bullish", trend_h4="bullish")
        enriched = await evaluate_trend(
            direction="BUY",
            trend_h1="bullish",
            trend_h4="bullish",
            bias=1,
            current_clv=0.72,
            macro_structure_ok=True,
        )
        assert enriched.verdict == Verdict.APPROVE
        assert enriched.confidence >= base.confidence

    @pytest.mark.asyncio
    async def test_no_data_with_bias_clv_macro_partial_recovery(self):
        """No H1/H4 trend data but bias+CLV+macro → recovered to APPROVE."""
        opinion = await evaluate_trend(
            direction="BUY",
            trend_h1=None,
            trend_h4=None,
            bias=1,
            current_clv=0.75,
            macro_structure_ok=True,
        )
        assert opinion.verdict == Verdict.APPROVE
        assert opinion.confidence >= 0.6

    @pytest.mark.asyncio
    async def test_neutral_clv_does_not_promote_bias_macro(self):
        """CLV between 0.30 and 0.70 is noise and must not confirm direction."""
        opinion = await evaluate_trend(
            direction="BUY",
            trend_h1=None,
            trend_h4=None,
            bias=1,
            current_clv=0.62,
            previous_clv=0.45,
            macro_structure_ok=True,
        )
        assert opinion.verdict == Verdict.HOLD
        assert "sin convicción" in opinion.reason

    @pytest.mark.asyncio
    async def test_contrary_clv_delta_does_not_confirm_direction(self):
        """A BUY close in the lower 30% cannot be rescued by positive CLV delta."""
        opinion = await evaluate_trend(
            direction="BUY",
            trend_h1=None,
            trend_h4=None,
            bias=1,
            current_clv=0.25,
            previous_clv=0.10,
            macro_structure_ok=True,
        )
        assert opinion.verdict == Verdict.HOLD
        assert "no reemplaza" in opinion.reason

    @pytest.mark.asyncio
    async def test_entry_window_closed_adds_to_rejection(self):
        """Counter-trend + entry_window_open=False → higher rejection confidence."""
        base = await evaluate_trend(direction="BUY", trend_h1="bearish", trend_h4="bearish")
        with_closed_window = await evaluate_trend(
            direction="BUY",
            trend_h1="bearish",
            trend_h4="bearish",
            entry_window_open=False,
        )
        assert with_closed_window.verdict == Verdict.REJECT
        assert with_closed_window.confidence >= base.confidence
