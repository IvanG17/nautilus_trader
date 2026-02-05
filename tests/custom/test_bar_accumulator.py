"""Tests for BarAccumulator and _bar_spec_to_interval_minutes."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest

from nautilus_schwab.data import BarAccumulator
from nautilus_schwab.data import _bar_spec_to_interval_minutes
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarSpecification
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AggregationSource
from nautilus_trader.model.enums import BarAggregation
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


VENUE = Venue("SCHWAB")
INSTRUMENT_ID = InstrumentId(Symbol("AAPL"), VENUE)


def _make_bar_type(step: int, aggregation: BarAggregation) -> BarType:
    spec = BarSpecification(step, aggregation, PriceType.LAST)
    return BarType(INSTRUMENT_ID, spec, AggregationSource.EXTERNAL)


def _ts_ns(year: int, month: int, day: int, hour: int, minute: int) -> int:
    """Create a nanosecond timestamp in US/Eastern time."""
    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    dt = datetime(year, month, day, hour, minute, tzinfo=eastern)
    return int(dt.timestamp() * 1_000_000_000)


# ---------------------------------------------------------------------------
# _bar_spec_to_interval_minutes tests
# ---------------------------------------------------------------------------


class TestBarSpecToIntervalMinutes:
    def test_1_minute(self):
        bt = _make_bar_type(1, BarAggregation.MINUTE)
        assert _bar_spec_to_interval_minutes(bt) == 1

    def test_5_minute(self):
        bt = _make_bar_type(5, BarAggregation.MINUTE)
        assert _bar_spec_to_interval_minutes(bt) == 5

    def test_15_minute(self):
        bt = _make_bar_type(15, BarAggregation.MINUTE)
        assert _bar_spec_to_interval_minutes(bt) == 15

    def test_30_minute(self):
        bt = _make_bar_type(30, BarAggregation.MINUTE)
        assert _bar_spec_to_interval_minutes(bt) == 30

    def test_1_hour(self):
        bt = _make_bar_type(1, BarAggregation.HOUR)
        assert _bar_spec_to_interval_minutes(bt) == 60

    def test_1_day(self):
        bt = _make_bar_type(1, BarAggregation.DAY)
        assert _bar_spec_to_interval_minutes(bt) == 1440


# ---------------------------------------------------------------------------
# BarAccumulator tests
# ---------------------------------------------------------------------------


class TestBarAccumulator5Min:
    """Test 5-minute bar aggregation."""

    @pytest.fixture
    def bar_type(self):
        return _make_bar_type(5, BarAggregation.MINUTE)

    @pytest.fixture
    def acc(self, bar_type):
        return BarAccumulator(bar_type, 5)

    def test_five_bars_emit_on_boundary(self, acc):
        """5 bars at :00-:04, bar at :05 triggers emission."""
        ts_init = 1

        # Feed bars at :00 through :04 — same bucket (bucket 0 for 5-min)
        for minute in range(5):
            ts = _ts_ns(2025, 6, 2, 9, 30 + minute)
            result = acc.update(
                Price.from_str(str(100.0 + minute)),
                Price.from_str(str(105.0 + minute)),
                Price.from_str(str(95.0)),
                Price.from_str(str(102.0 + minute)),
                1000 + minute * 100,
                ts,
                ts_init,
            )
            # All in same 5-min bucket (9:30-9:34 → bucket floor(570/5)=114)
            assert result is None

        # Bar at :35 crosses into next bucket → emits the :30-:34 bar
        ts_35 = _ts_ns(2025, 6, 2, 9, 35)
        result = acc.update(
            Price.from_str("110.0"),
            Price.from_str("115.0"),
            Price.from_str("109.0"),
            Price.from_str("112.0"),
            5000,
            ts_35,
            ts_init,
        )

        assert result is not None
        assert isinstance(result, Bar)
        # OHLCV from the first 5 bars
        assert float(str(result.open)) == 100.0  # first bar's open
        assert float(str(result.close)) == 106.0  # last bar's close (102+4)
        assert float(str(result.high)) == 109.0  # max(105,106,107,108,109)
        assert float(str(result.low)) == 95.0
        assert result.volume == Quantity.from_int(1000 + 1100 + 1200 + 1300 + 1400)

    def test_time_alignment_partial_bucket(self, acc):
        """Bars at :03, :04 then :05 emits a 2-bar bucket."""
        ts_init = 1

        # :03 and :04 are in the same 5-min bucket as :00-:04 (bucket 114 for 9:33/9:34)
        ts_33 = _ts_ns(2025, 6, 2, 9, 33)
        ts_34 = _ts_ns(2025, 6, 2, 9, 34)

        result = acc.update(
            Price.from_str("100.0"),
            Price.from_str("101.0"),
            Price.from_str("99.0"),
            Price.from_str("100.5"),
            1000,
            ts_33,
            ts_init,
        )
        assert result is None

        result = acc.update(
            Price.from_str("100.5"),
            Price.from_str("102.0"),
            Price.from_str("99.5"),
            Price.from_str("101.0"),
            2000,
            ts_34,
            ts_init,
        )
        assert result is None

        # :35 crosses boundary
        ts_35 = _ts_ns(2025, 6, 2, 9, 35)
        result = acc.update(
            Price.from_str("101.0"),
            Price.from_str("103.0"),
            Price.from_str("100.0"),
            Price.from_str("102.0"),
            3000,
            ts_35,
            ts_init,
        )

        assert result is not None
        assert float(str(result.open)) == 100.0  # first bar's open
        assert float(str(result.close)) == 101.0  # second bar's close
        assert float(str(result.high)) == 102.0  # max(101, 102)
        assert float(str(result.low)) == 99.0  # min(99, 99.5)
        assert result.volume == Quantity.from_int(3000)  # 1000 + 2000

    def test_gap_skips_empty_buckets(self, acc):
        """Bars at :01, :02, then :11 emits :00-:04 bucket, skips :05-:09."""
        ts_init = 1

        ts_31 = _ts_ns(2025, 6, 2, 9, 31)
        ts_32 = _ts_ns(2025, 6, 2, 9, 32)

        acc.update(
            Price.from_str("100.0"),
            Price.from_str("101.0"),
            Price.from_str("99.0"),
            Price.from_str("100.5"),
            1000,
            ts_31,
            ts_init,
        )
        acc.update(
            Price.from_str("100.5"),
            Price.from_str("102.0"),
            Price.from_str("98.0"),
            Price.from_str("101.0"),
            2000,
            ts_32,
            ts_init,
        )

        # Jump to :41 — skipping the :35-:39 bucket entirely
        ts_41 = _ts_ns(2025, 6, 2, 9, 41)
        result = acc.update(
            Price.from_str("105.0"),
            Price.from_str("106.0"),
            Price.from_str("104.0"),
            Price.from_str("105.5"),
            5000,
            ts_41,
            ts_init,
        )

        # Should emit the :30-:34 bucket
        assert result is not None
        assert float(str(result.open)) == 100.0
        assert float(str(result.close)) == 101.0
        assert float(str(result.high)) == 102.0
        assert float(str(result.low)) == 98.0
        assert result.volume == Quantity.from_int(3000)


class TestBarAccumulatorDaily:
    """Test daily bar aggregation."""

    @pytest.fixture
    def bar_type(self):
        return _make_bar_type(1, BarAggregation.DAY)

    @pytest.fixture
    def acc(self, bar_type):
        return BarAccumulator(bar_type, 1440)

    def test_date_transition_emits_previous_day(self, acc):
        """Date change emits the completed daily bar."""
        ts_init = 1

        # Monday bars
        for minute in range(3):
            ts = _ts_ns(2025, 6, 2, 9, 30 + minute)
            result = acc.update(
                Price.from_str(str(100.0 + minute)),
                Price.from_str(str(105.0 + minute)),
                Price.from_str(str(95.0 - minute)),
                Price.from_str(str(101.0 + minute)),
                1000 * (minute + 1),
                ts,
                ts_init,
            )
            assert result is None

        # Tuesday bar triggers Monday emission
        ts_tuesday = _ts_ns(2025, 6, 3, 9, 30)
        result = acc.update(
            Price.from_str("110.0"),
            Price.from_str("115.0"),
            Price.from_str("109.0"),
            Price.from_str("112.0"),
            5000,
            ts_tuesday,
            ts_init,
        )

        assert result is not None
        assert float(str(result.open)) == 100.0  # first Monday bar's open
        assert float(str(result.close)) == 103.0  # last Monday bar's close
        assert float(str(result.high)) == 107.0  # max(105, 106, 107)
        assert float(str(result.low)) == 93.0  # min(95, 94, 93)
        assert result.volume == Quantity.from_int(1000 + 2000 + 3000)

    def test_weekend_gap(self, acc):
        """Friday bar emitted when first Monday bar arrives."""
        ts_init = 1

        # Friday bar
        ts_friday = _ts_ns(2025, 5, 30, 15, 59)
        acc.update(
            Price.from_str("100.0"),
            Price.from_str("105.0"),
            Price.from_str("95.0"),
            Price.from_str("102.0"),
            1000,
            ts_friday,
            ts_init,
        )

        # Monday bar
        ts_monday = _ts_ns(2025, 6, 2, 9, 30)
        result = acc.update(
            Price.from_str("103.0"),
            Price.from_str("108.0"),
            Price.from_str("101.0"),
            Price.from_str("106.0"),
            2000,
            ts_monday,
            ts_init,
        )

        assert result is not None
        assert float(str(result.open)) == 100.0
        assert float(str(result.close)) == 102.0


class TestBarAccumulatorOHLCV:
    """Test OHLCV correctness."""

    @pytest.fixture
    def bar_type(self):
        return _make_bar_type(5, BarAggregation.MINUTE)

    @pytest.fixture
    def acc(self, bar_type):
        return BarAccumulator(bar_type, 5)

    def test_ohlcv_correctness(self, acc):
        """open=first open, high=max highs, low=min lows, close=last close, volume=sum."""
        ts_init = 1

        bars_data = [
            # (open, high, low, close, volume)
            ("100.0", "105.0", "98.0", "103.0", 1000),
            ("103.0", "108.0", "102.0", "106.0", 1500),
            ("106.0", "107.0", "99.0", "100.0", 2000),
        ]

        for i, (o, h, l, c, v) in enumerate(bars_data):
            ts = _ts_ns(2025, 6, 2, 9, 30 + i)
            acc.update(
                Price.from_str(o),
                Price.from_str(h),
                Price.from_str(l),
                Price.from_str(c),
                v,
                ts,
                ts_init,
            )

        # Flush to get the partial bar
        result = acc.flush()

        assert result is not None
        assert float(str(result.open)) == 100.0  # first open
        assert float(str(result.high)) == 108.0  # max(105, 108, 107)
        assert float(str(result.low)) == 98.0  # min(98, 102, 99)
        assert float(str(result.close)) == 100.0  # last close
        assert result.volume == Quantity.from_int(4500)  # 1000+1500+2000


class TestBarAccumulatorFlush:
    """Test flush behavior."""

    @pytest.fixture
    def bar_type(self):
        return _make_bar_type(5, BarAggregation.MINUTE)

    @pytest.fixture
    def acc(self, bar_type):
        return BarAccumulator(bar_type, 5)

    def test_flush_returns_partial_bar(self, acc):
        """Flush mid-bucket returns partial bar."""
        ts_init = 1
        ts = _ts_ns(2025, 6, 2, 9, 31)

        acc.update(
            Price.from_str("100.0"),
            Price.from_str("105.0"),
            Price.from_str("95.0"),
            Price.from_str("102.0"),
            1000,
            ts,
            ts_init,
        )

        result = acc.flush()
        assert result is not None
        assert float(str(result.open)) == 100.0
        assert float(str(result.close)) == 102.0
        assert result.volume == Quantity.from_int(1000)

    def test_flush_empty_returns_none(self, acc):
        """Flush on empty accumulator returns None."""
        result = acc.flush()
        assert result is None

    def test_flush_resets_state(self, acc):
        """After flush, accumulator state is reset."""
        ts_init = 1
        ts = _ts_ns(2025, 6, 2, 9, 31)

        acc.update(
            Price.from_str("100.0"),
            Price.from_str("105.0"),
            Price.from_str("95.0"),
            Price.from_str("102.0"),
            1000,
            ts,
            ts_init,
        )

        acc.flush()

        # Second flush should return None
        assert acc.flush() is None


class TestBarAccumulatorPassThrough:
    """Test that 1-min bar types don't create accumulators."""

    def test_1min_interval(self):
        bt = _make_bar_type(1, BarAggregation.MINUTE)
        interval = _bar_spec_to_interval_minutes(bt)
        assert interval == 1
        # interval <= 1 means no accumulator is created (handled in SchwabStreamManager)


class TestBarAccumulatorTsEvent:
    """Test ts_event is from the last bar in the bucket."""

    def test_ts_event_is_last_bar(self):
        bar_type = _make_bar_type(5, BarAggregation.MINUTE)
        acc = BarAccumulator(bar_type, 5)
        ts_init = 1

        ts_30 = _ts_ns(2025, 6, 2, 9, 30)
        ts_34 = _ts_ns(2025, 6, 2, 9, 34)

        acc.update(
            Price.from_str("100.0"),
            Price.from_str("101.0"),
            Price.from_str("99.0"),
            Price.from_str("100.5"),
            1000,
            ts_30,
            ts_init,
        )
        acc.update(
            Price.from_str("100.5"),
            Price.from_str("102.0"),
            Price.from_str("98.0"),
            Price.from_str("101.0"),
            2000,
            ts_34,
            ts_init,
        )

        # Trigger boundary cross
        ts_35 = _ts_ns(2025, 6, 2, 9, 35)
        result = acc.update(
            Price.from_str("101.0"),
            Price.from_str("103.0"),
            Price.from_str("100.0"),
            Price.from_str("102.0"),
            3000,
            ts_35,
            ts_init,
        )

        assert result is not None
        assert result.ts_event == ts_34  # last bar in the emitted bucket
