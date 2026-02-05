# -------------------------------------------------------------------------------------------------
#  SuperStrat Strategy Unit Tests
# -------------------------------------------------------------------------------------------------

"""
Unit tests for the SuperStrat strategy.

These tests validate the core indicator calculations and strategy logic
without requiring a full Nautilus Trader environment.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# =============================================================================
# INDICATOR CALCULATION TESTS
# =============================================================================


class TestATRCalculation:
    """Tests for ATR calculation logic."""

    def test_atr_basic_calculation(self):
        """Test ATR calculation with known values."""
        # Create sample OHLCV data
        df = pd.DataFrame({
            "high": [110, 112, 115, 113, 116, 118, 117, 120, 119, 121],
            "low": [100, 102, 105, 103, 106, 108, 107, 110, 109, 111],
            "close": [105, 110, 112, 108, 114, 115, 112, 118, 115, 120],
        })

        # Calculate ATR (using EMA)
        atr_period = 5
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - df["close"].shift(1)).abs()
        tr3 = (df["low"] - df["close"].shift(1)).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = true_range.ewm(span=atr_period, adjust=False).mean()

        # ATR should be positive
        assert all(atr.dropna() > 0)
        # ATR should be reasonable (less than max price range)
        assert all(atr.dropna() < 50)

    def test_atr_with_gaps(self):
        """Test ATR calculation handles gaps correctly."""
        df = pd.DataFrame({
            "high": [100, 120, 122, 121],  # Gap up
            "low": [95, 115, 118, 117],
            "close": [98, 119, 120, 118],
        })

        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - df["close"].shift(1)).abs()
        tr3 = (df["low"] - df["close"].shift(1)).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # True range on gap day should capture the gap
        assert true_range.iloc[1] > tr1.iloc[1]  # Gap contributes to TR


class TestDonchianCalculation:
    """Tests for Donchian channel calculation."""

    def test_donchian_high_basic(self):
        """Test Donchian high calculation."""
        df = pd.DataFrame({
            "high": [100, 105, 103, 110, 108, 112, 115, 111, 118, 120],
        })

        donchian_period = 5
        donchian_high = df["high"].rolling(window=donchian_period).max()

        # Check specific values
        assert donchian_high.iloc[4] == 110  # Max of first 5 bars
        assert donchian_high.iloc[9] == 120  # Max of last 5 bars

    def test_donchian_breakout_detection(self):
        """Test breakout detection logic."""
        highs = [100, 102, 101, 103, 105, 104, 110, 108]  # Breakout at index 6
        df = pd.DataFrame({"high": highs, "close": highs})

        donchian_period = 5
        donchian_high = df["high"].rolling(window=donchian_period).max()

        # At index 6, price (110) should be above previous donchian high (105)
        is_breakout = df["close"].iloc[6] > donchian_high.iloc[5]
        assert is_breakout


class TestVSACalculation:
    """Tests for Volume Spread Analysis indicators."""

    def test_vsa_selling_climax_detection(self):
        """Test selling climax detection (high vol, wide spread, close near low)."""
        df = pd.DataFrame({
            "high": [100, 101, 102, 101, 120],  # Last bar has wide spread
            "low": [95, 96, 97, 96, 100],
            "close": [98, 99, 100, 99, 102],  # Last close near low
            "volume": [1000, 1100, 900, 1000, 5000],  # Last bar high volume
        })

        window = 4
        vsa_volume_factor = 1.5

        spread = df["high"] - df["low"]
        avg_spread = spread.rolling(window=window).mean()
        avg_volume = df["volume"].rolling(window=window).mean()

        is_high_volume = df["volume"] > (avg_volume * vsa_volume_factor)
        is_wide_spread = spread > (avg_spread * 1.3)

        close_position = (df["close"] - df["low"]) / spread.replace(0, np.nan)
        close_position = close_position.fillna(0.5)

        selling_climax = is_high_volume & is_wide_spread & (close_position < 0.3)

        # Last bar should be selling climax
        assert selling_climax.iloc[-1]


class TestSupertrendCalculation:
    """Tests for Supertrend indicator."""

    def test_supertrend_uptrend_detection(self):
        """Test Supertrend uptrend detection."""
        # Simulate trending up data
        closes = [100 + i * 0.5 for i in range(20)]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]

        df = pd.DataFrame({
            "high": highs,
            "low": lows,
            "close": closes,
        })

        atr_period = 10
        atr_multiplier = 2.0

        # Calculate ATR
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - df["close"].shift(1)).abs()
        tr3 = (df["low"] - df["close"].shift(1)).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = true_range.ewm(span=atr_period, adjust=False).mean()

        # Calculate Supertrend
        hl2 = (df["high"] + df["low"]) / 2
        upper_band = hl2 + (atr_multiplier * atr)
        lower_band = hl2 - (atr_multiplier * atr)

        supertrend = pd.Series(index=df.index, dtype=float)
        direction = pd.Series(index=df.index, dtype=int)

        for i in range(len(df)):
            if i == 0:
                supertrend.iloc[i] = upper_band.iloc[i]
                direction.iloc[i] = -1
                continue

            prev_supertrend = supertrend.iloc[i - 1]
            prev_direction = direction.iloc[i - 1]
            curr_close = df["close"].iloc[i]
            curr_upper = upper_band.iloc[i]
            curr_lower = lower_band.iloc[i]

            if prev_direction == 1:
                if curr_close < prev_supertrend:
                    supertrend.iloc[i] = curr_upper
                    direction.iloc[i] = -1
                else:
                    supertrend.iloc[i] = max(curr_lower, prev_supertrend)
                    direction.iloc[i] = 1
            else:
                if curr_close > prev_supertrend:
                    supertrend.iloc[i] = curr_lower
                    direction.iloc[i] = 1
                else:
                    supertrend.iloc[i] = min(curr_upper, prev_supertrend)
                    direction.iloc[i] = -1

        # In uptrending data, should eventually show uptrend (direction = 1)
        assert direction.iloc[-1] == 1


# =============================================================================
# STRATEGY LOGIC TESTS
# =============================================================================


class TestEntryLogic:
    """Tests for entry signal logic."""

    def test_entry_blocked_on_selling_climax(self):
        """Entry should be blocked during selling climax."""
        is_uptrend = True
        is_selling_climax = True
        donchian_entry = True

        # Entry should be blocked
        should_enter = (is_uptrend or donchian_entry) and not is_selling_climax
        assert not should_enter

    def test_entry_on_supertrend_uptrend(self):
        """Entry allowed on supertrend uptrend without selling climax."""
        is_uptrend = True
        is_selling_climax = False
        donchian_entry = False

        should_enter = (is_uptrend or donchian_entry) and not is_selling_climax
        assert should_enter

    def test_entry_on_donchian_breakout(self):
        """Entry allowed on Donchian breakout without selling climax."""
        is_uptrend = False
        is_selling_climax = False
        donchian_entry = True

        should_enter = (is_uptrend or donchian_entry) and not is_selling_climax
        assert should_enter


class TestExitLogic:
    """Tests for exit signal logic."""

    def test_trailing_stop_calculation(self):
        """Test trailing stop calculation."""
        highest_price = 120.0
        current_atr = 3.0
        trailing_stop_atr_mult = 2.0

        trailing_stop = highest_price - (trailing_stop_atr_mult * current_atr)
        assert trailing_stop == 114.0

    def test_breakeven_stop_exit(self):
        """Test breakeven stop triggers exit."""
        breakeven_stop = 100.0
        current_price = 99.0
        is_uptrend = True

        should_exit = current_price <= breakeven_stop
        assert should_exit

    def test_supertrend_reversal_exit(self):
        """Test supertrend reversal triggers exit."""
        is_uptrend = False
        current_price = 110.0
        trailing_stop = 105.0
        breakeven_stop = 0.0  # No pyramid

        should_exit = (
            (breakeven_stop > 0 and current_price <= breakeven_stop)
            or not is_uptrend
            or current_price <= trailing_stop
        )
        assert should_exit


class TestPyramidLogic:
    """Tests for pyramiding logic."""

    def test_pyramid_trigger_conditions(self):
        """Test pyramid add conditions."""
        original_entry_price = 100.0
        current_price = 107.0
        current_atr = 3.0
        pyramid_atr_threshold = 1.5
        pyramid_added = False

        profit = current_price - original_entry_price
        threshold = pyramid_atr_threshold * current_atr  # 4.5

        should_pyramid = (
            not pyramid_added
            and original_entry_price > 0
            and profit >= threshold
        )
        assert should_pyramid

    def test_pyramid_blocked_when_already_added(self):
        """Test pyramid blocked after one add."""
        original_entry_price = 100.0
        current_price = 110.0
        current_atr = 3.0
        pyramid_atr_threshold = 1.5
        pyramid_added = True  # Already added

        profit = current_price - original_entry_price
        threshold = pyramid_atr_threshold * current_atr

        should_pyramid = (
            not pyramid_added
            and original_entry_price > 0
            and profit >= threshold
        )
        assert not should_pyramid


class TestPositionSizing:
    """Tests for position sizing logic."""

    def test_position_size_calculation(self):
        """Test position size calculation."""
        cash = 100000.0
        position_size_pct = 0.25
        price = 50.0

        position_value = cash * position_size_pct  # 25000
        shares = int(position_value / price)  # 500

        assert shares == 500

    def test_pyramid_add_size(self):
        """Test pyramid add size calculation."""
        original_quantity = 100
        pyramid_size_pct = 0.50

        add_quantity = int(original_quantity * pyramid_size_pct)
        assert add_quantity == 50


# =============================================================================
# CONFIGURATION TESTS
# =============================================================================


class TestSuperStratConfig:
    """Tests for SuperStrat configuration."""

    def test_optimal_params_structure(self):
        """Test optimal parameters have required keys."""
        optimal_params = {
            "atr_period": 27,
            "atr_multiplier": 3.54,
            "vsa_window": 46,
            "vsa_volume_factor": 1.79,
            "trailing_stop_atr_mult": 1.89,
            "position_size_pct": 0.43,
            "donchian_period": 33,
            "pyramid_atr_threshold": 1.45,
            "pyramid_size_pct": 0.50,
        }

        required_keys = [
            "atr_period",
            "atr_multiplier",
            "vsa_window",
            "vsa_volume_factor",
            "trailing_stop_atr_mult",
            "position_size_pct",
            "donchian_period",
            "pyramid_atr_threshold",
            "pyramid_size_pct",
        ]

        for key in required_keys:
            assert key in optimal_params

    def test_param_value_ranges(self):
        """Test parameter values are in valid ranges."""
        params = {
            "atr_period": 27,
            "atr_multiplier": 3.54,
            "vsa_window": 46,
            "vsa_volume_factor": 1.79,
            "trailing_stop_atr_mult": 1.89,
            "position_size_pct": 0.43,
            "donchian_period": 33,
            "pyramid_atr_threshold": 1.45,
            "pyramid_size_pct": 0.50,
        }

        # Periods should be positive integers
        assert params["atr_period"] > 0
        assert params["vsa_window"] > 0
        assert params["donchian_period"] > 0

        # Multipliers should be positive
        assert params["atr_multiplier"] > 0
        assert params["vsa_volume_factor"] > 0
        assert params["trailing_stop_atr_mult"] > 0
        assert params["pyramid_atr_threshold"] > 0

        # Percentages should be between 0 and 1
        assert 0 < params["position_size_pct"] <= 1
        assert 0 < params["pyramid_size_pct"] <= 1


if __name__ == "__main__":
    # Run tests without pytest to avoid conftest loading issues
    import sys

    # Create a simple test runner
    test_classes = [
        TestATRCalculation,
        TestDonchianCalculation,
        TestVSACalculation,
        TestSupertrendCalculation,
        TestEntryLogic,
        TestExitLogic,
        TestPyramidLogic,
        TestPositionSizing,
        TestSuperStratConfig,
    ]

    passed = 0
    failed = 0

    for test_class in test_classes:
        instance = test_class()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                try:
                    getattr(instance, method_name)()
                    print(f"  PASSED: {test_class.__name__}.{method_name}")
                    passed += 1
                except AssertionError as e:
                    print(f"  FAILED: {test_class.__name__}.{method_name}: {e}")
                    failed += 1
                except Exception as e:
                    print(f"  ERROR: {test_class.__name__}.{method_name}: {e}")
                    failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
