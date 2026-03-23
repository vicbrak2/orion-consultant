"""
Shared fixtures for Orion Consultant tests.
"""

from __future__ import annotations

import sys
import os

import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.schemas import SignalRequest, SignalDirection


# ── Fixtures ──────────────────────────────────────────


@pytest.fixture
def healthy_buy_signal() -> SignalRequest:
    """A textbook healthy BUY signal — should pass all experts."""
    return SignalRequest(
        symbol="Step Index",
        direction=SignalDirection.BUY,
        entry_price=5432.10,
        stop_loss=5400.00,
        take_profit=5500.00,
        equity=1000.0,
        balance=1050.0,
        current_volatility=120.5,
        trend_h1="bullish",
        trend_h4="bullish",
    )


@pytest.fixture
def risky_signal() -> SignalRequest:
    """A signal with high drawdown and extreme volatility."""
    return SignalRequest(
        symbol="Step Index",
        direction=SignalDirection.BUY,
        entry_price=5432.10,
        stop_loss=5300.00,  # Wide stop
        take_profit=5500.00,
        equity=800.0,       # High drawdown: (1000-800)/1000 = 20%
        balance=1000.0,
        current_volatility=250.0,  # Above max (200)
        trend_h1="bearish",
        trend_h4="bearish",
    )


@pytest.fixture
def counter_trend_signal() -> SignalRequest:
    """A BUY signal with bearish trends — should be rejected by trend analyzer."""
    return SignalRequest(
        symbol="Step Index",
        direction=SignalDirection.BUY,
        entry_price=5432.10,
        stop_loss=5400.00,
        take_profit=5500.00,
        equity=1000.0,
        balance=1050.0,
        current_volatility=100.0,
        trend_h1="bearish",
        trend_h4="bearish",
    )


@pytest.fixture
def bad_geometry_buy() -> SignalRequest:
    """A BUY signal with inverted SL/TP — should be rejected by pattern expert."""
    return SignalRequest(
        symbol="Step Index",
        direction=SignalDirection.BUY,
        entry_price=5432.10,
        stop_loss=5500.00,   # SL above entry (invalid for BUY)
        take_profit=5400.00, # TP below entry (invalid for BUY)
        equity=1000.0,
        balance=1050.0,
        current_volatility=80.0,
    )


@pytest.fixture
def sell_signal() -> SignalRequest:
    """A healthy SELL signal."""
    return SignalRequest(
        symbol="Step Index",
        direction=SignalDirection.SELL,
        entry_price=5432.10,
        stop_loss=5470.00,
        take_profit=5350.00,
        equity=1000.0,
        balance=1050.0,
        current_volatility=90.0,
        trend_h1="bearish",
        trend_h4="bearish",
    )
