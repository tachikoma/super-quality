"""Tests for the factor computation module."""

import numpy as np
import pandas as pd
import pytest

from super_quality.factors.base import Factor
from super_quality.factors.market_timing import KosdaqMAFactor
from super_quality.factors.quality import GPAFactor
from super_quality.factors.supply import RetailSupplyFactor
from super_quality.factors.value import MarketCapFactor, PBRFactor


# ── GPAFactor ────────────────────────────────────────────────────────


class TestGPAFactor:
    """GP/A factor tests."""

    def test_basic_computation(self):
        """Verify GP/A values for tickers with known inputs."""
        data = pd.DataFrame({
            "ticker": ["A", "B", "C"],
            "revenue": [1000, 500, 2000],
            "cogs": [600, 400, 1500],
            "total_assets": [5000, 2000, 10000],
        })
        factor = GPAFactor()
        result = factor.compute(data)

        assert "gpa" in result.columns
        assert "gpa_percentile" in result.columns

        # A: (1000 - 600) / 5000 = 0.08
        # B: (500 - 400) / 2000 = 0.05
        # C: (2000 - 1500) / 10000 = 0.05
        assert result.loc[result["ticker"] == "A", "gpa"].iloc[0] == 0.08
        assert result.loc[result["ticker"] == "B", "gpa"].iloc[0] == 0.05
        assert result.loc[result["ticker"] == "C", "gpa"].iloc[0] == 0.05

    def test_percentile_ranking_ascending(self):
        """Highest GP/A should receive the highest percentile."""
        data = pd.DataFrame({
            "ticker": ["A", "B", "C"],
            "revenue": [1000, 500, 2000],
            "cogs": [600, 400, 1500],
            "total_assets": [5000, 2000, 10000],
        })
        factor = GPAFactor()
        result = factor.compute(data)

        a_pct = result.loc[result["ticker"] == "A", "gpa_percentile"].iloc[0]
        b_pct = result.loc[result["ticker"] == "B", "gpa_percentile"].iloc[0]
        c_pct = result.loc[result["ticker"] == "C", "gpa_percentile"].iloc[0]

        # A (0.08) > B (0.05) and A (0.08) > C (0.05)
        assert a_pct > b_pct
        assert a_pct > c_pct

    def test_zero_assets(self):
        """total_assets == 0 should produce GP/A of 0 (not NaN, not inf)."""
        data = pd.DataFrame({
            "ticker": ["D"],
            "revenue": [100],
            "cogs": [50],
            "total_assets": [0],
        })
        factor = GPAFactor()
        result = factor.compute(data)

        assert result["gpa"].iloc[0] == 0.0

    def test_name_property(self):
        assert GPAFactor().name == "GPA"


# ── PBRFactor ────────────────────────────────────────────────────────


class TestPBRFactor:
    """PBR factor tests."""

    def test_basic_computation(self):
        """Verify PBR values for tickers with known inputs."""
        data = pd.DataFrame({
            "ticker": ["A", "B", "C"],
            "mcap": [100, 100, 100],
            "total_equity": [200, 50, 100],
        })
        factor = PBRFactor()
        result = factor.compute(data)

        assert "pbr" in result.columns
        assert "pbr_percentile" in result.columns

        # A: 100/200 = 0.5
        # B: 100/50  = 2.0
        # C: 100/100 = 1.0
        assert result.loc[result["ticker"] == "A", "pbr"].iloc[0] == 0.5
        assert result.loc[result["ticker"] == "B", "pbr"].iloc[0] == 2.0
        assert result.loc[result["ticker"] == "C", "pbr"].iloc[0] == 1.0

    def test_percentile_ranking_ascending(self):
        """Lowest PBR should receive the lowest percentile (ascending rank)."""
        data = pd.DataFrame({
            "ticker": ["A", "B", "C"],
            "mcap": [100, 100, 100],
            "total_equity": [200, 50, 100],
        })
        factor = PBRFactor()
        result = factor.compute(data)

        a_pct = result.loc[result["ticker"] == "A", "pbr_percentile"].iloc[0]
        b_pct = result.loc[result["ticker"] == "B", "pbr_percentile"].iloc[0]
        c_pct = result.loc[result["ticker"] == "C", "pbr_percentile"].iloc[0]

        # PBR ascending: 0.5 (A) < 1.0 (C) < 2.0 (B)
        assert a_pct < c_pct < b_pct

    def test_negative_equity_gives_nan(self):
        """Negative equity → PBR is NaN and excluded from ranking."""
        data = pd.DataFrame({
            "ticker": ["D"],
            "mcap": [100],
            "total_equity": [-50],
        })
        factor = PBRFactor()
        result = factor.compute(data)

        assert np.isnan(result["pbr"].iloc[0])
        assert np.isnan(result["pbr_percentile"].iloc[0])

    def test_zero_equity_gives_nan(self):
        """Zero equity → PBR is NaN and excluded from ranking."""
        data = pd.DataFrame({
            "ticker": ["E"],
            "mcap": [100],
            "total_equity": [0],
        })
        factor = PBRFactor()
        result = factor.compute(data)

        assert np.isnan(result["pbr"].iloc[0])
        assert np.isnan(result["pbr_percentile"].iloc[0])

    def test_name_property(self):
        assert PBRFactor().name == "PBR"


# ── MarketCapFactor ──────────────────────────────────────────────────


class TestMarketCapFactor:
    """Market-cap factor tests."""

    def test_basic_computation(self):
        """Verify mcap_percentile column exists and values are in [0, 100]."""
        data = pd.DataFrame({
            "ticker": ["A", "B", "C"],
            "mcap": [100, 200, 300],
        })
        factor = MarketCapFactor()
        result = factor.compute(data)

        assert "mcap_percentile" in result.columns
        assert all(0 <= v <= 100 for v in result["mcap_percentile"])

    def test_percentile_ranking_ascending(self):
        """Smaller market cap should get lower percentile."""
        data = pd.DataFrame({
            "ticker": ["A", "B", "C"],
            "mcap": [100, 200, 300],
        })
        factor = MarketCapFactor()
        result = factor.compute(data)

        a_pct = result.loc[result["ticker"] == "A", "mcap_percentile"].iloc[0]
        c_pct = result.loc[result["ticker"] == "C", "mcap_percentile"].iloc[0]
        assert a_pct < c_pct

    def test_name_property(self):
        assert MarketCapFactor().name == "MarketCap"


# ── KosdaqMAFactor ───────────────────────────────────────────────────


class TestKosdaqMAFactor:
    """KOSDAQ moving-average timing signal tests (MA20 trend filter).

    Price sequence used (26 rows):
        [100, 102, 104, 106, 108, 106, 104, 102, 100, 98,
          96,  94,  92,  94,  96,  93,
          95,  97,  99, 101, 103, 105, 107, 100,  95,  90]

    * Index 22 (close=107): close > MA20 (~100.5) → buy_signal=True
    * Index 25 (close=90):  close < MA3 & MA5     → sell_signal=True
    """

    @pytest.fixture
    def close_series(self):
        """26 rows: steady rise (indices 0-22), sharp drop (indices 23-25).

        Preserves original 16 values for backward-compatible MA checks at indices 2, 13.
        After 20-row warmup:
          - Index 22 (close=107): above MA20 → buy_signal=True
          - Index 25 (close=90):  below MA3 & MA5 → sell_signal=True
        """
        return pd.Series([
            100, 102, 104, 106, 108,  # 0-4
            106, 104, 102, 100,  98,  # 5-9
            96,  94,  92,  94,  96,   # 10-14
            93,                       # 15
            95,  97,  99, 101,        # 16-19: gradual recovery (warmup)
            103, 105, 107,            # 20-22: above MA20
            100,  95,  90,            # 23-25: sharp drop → sell
        ])

    def test_output_columns(self, close_series):
        """Verify all expected columns are present."""
        factor = KosdaqMAFactor()
        result = factor.compute(close_series)

        expected = ["date", "ma3", "ma5", "ma10", "ma20", "buy_signal", "sell_signal"]
        assert list(result.columns) == expected

    def test_first_20_rows_signals_false(self, close_series):
        """First 20 rows should have both signals forced to False (MA20 warmup)."""
        factor = KosdaqMAFactor()
        result = factor.compute(close_series)

        assert list(result["buy_signal"].iloc[:20]) == [False] * 20
        assert list(result["sell_signal"].iloc[:20]) == [False] * 20

    def test_buy_signal_ma20_condition(self, close_series):
        """close > MA20 after warmup → buy_signal = True.

        At index 22: close=107 > MA20 (~100.5) → buy_signal=True.
        """
        factor = KosdaqMAFactor()
        result = factor.compute(close_series)

        assert result["buy_signal"].iloc[22]
        assert not result["sell_signal"].iloc[22]

    def test_sell_signal_and_condition(self, close_series):
        """close < MA3 AND close < MA5 after warmup → sell_signal = True.

        At index 25: close=90 < MA3=95 AND close=90 < MA5=97.4.
        """
        factor = KosdaqMAFactor()
        result = factor.compute(close_series)

        assert result["sell_signal"].iloc[25]
        assert not result["buy_signal"].iloc[25]

    def test_sell_signal_excludes_ma10(self, close_series):
        """MA10 is NOT part of the sell condition."""
        factor = KosdaqMAFactor()
        result = factor.compute(close_series)

        assert result["sell_signal"].iloc[25]

    def test_ma_values(self, close_series):
        """Spot-check known MA values."""
        factor = KosdaqMAFactor()
        result = factor.compute(close_series)

        # Rolling(3): indices 0,1 are NaN; index 2 = (100+102+104)/3 = 102
        assert np.isnan(result["ma3"].iloc[0])
        assert np.isnan(result["ma3"].iloc[1])
        assert result["ma3"].iloc[2] == pytest.approx(102.0)

        # Index 13: MA3 = (94 + 92 + 94) / 3 = 93.333...
        assert result["ma3"].iloc[13] == pytest.approx(93.333, rel=1e-3)

        # Index 13: MA5 = (98 + 96 + 94 + 92 + 94) / 5 = 94.8
        assert result["ma5"].iloc[13] == pytest.approx(94.8, rel=1e-3)

    def test_name_property(self):
        assert KosdaqMAFactor().name == "KosdaqMA"


# ── RetailSupplyFactor ───────────────────────────────────────────────


class TestRetailSupplyFactor:
    """Retail supply factor tests."""

    @pytest.fixture
    def sample_data(self):
        """Two tickers with 6 trading days each."""
        dates = pd.date_range("2024-01-01", periods=6, freq="D")
        return pd.DataFrame({
            "ticker": ["A"] * 6 + ["B"] * 6,
            "date": list(dates) * 2,
            "retail_net_buy": [10, 20, 30, 40, 50, 60]
            + [100, 200, 300, 400, 500, 600],
        })

    def test_output_columns(self, sample_data):
        """Verify expected output columns exist."""
        factor = RetailSupplyFactor(supply_days=5)
        result = factor.compute(sample_data)

        assert "supply_score" in result.columns
        assert "supply_percentile" in result.columns

    def test_supply_score_values(self, sample_data):
        """Verify rolling 5-day sum for each ticker on the last available date."""
        factor = RetailSupplyFactor(supply_days=5)
        result = factor.compute(sample_data)

        # A: last 5 days = 20 + 30 + 40 + 50 + 60 = 200
        # B: last 5 days = 200 + 300 + 400 + 500 + 600 = 2000
        # 전체 행을 반환하므로 마지막 날짜 기준으로 검증
        last_date = result["date"].max()
        a_score = result.loc[
            (result["ticker"] == "A") & (result["date"] == last_date), "supply_score"
        ].iloc[0]
        b_score = result.loc[
            (result["ticker"] == "B") & (result["date"] == last_date), "supply_score"
        ].iloc[0]
        assert a_score == 200
        assert b_score == 2000

    def test_percentile_ranking_ascending(self, sample_data):
        """Higher supply score → higher percentile (ascending rank) on same date."""
        factor = RetailSupplyFactor(supply_days=5)
        result = factor.compute(sample_data)

        last_date = result["date"].max()
        a_pct = result.loc[
            (result["ticker"] == "A") & (result["date"] == last_date), "supply_percentile"
        ].iloc[0]
        b_pct = result.loc[
            (result["ticker"] == "B") & (result["date"] == last_date), "supply_percentile"
        ].iloc[0]

        # B (2000) > A (200) → B percentile > A percentile
        assert b_pct > a_pct
        assert 0 <= a_pct <= 100
        assert 0 <= b_pct <= 100

    def test_insufficient_data_excluded(self):
        """Tickers with fewer than supply_days of data still returned but with NaN scores."""
        dates = pd.date_range("2024-01-01", periods=3, freq="D")
        data = pd.DataFrame({
            "ticker": ["A"] * 3,
            "date": list(dates),
            "retail_net_buy": [10, 20, 30],
        })
        factor = RetailSupplyFactor(supply_days=5)
        result = factor.compute(data)

        # min_periods 미달 → supply_score는 NaN (전체 행은 반환됨)
        assert len(result) == 3
        assert result["supply_score"].isna().all()

    def test_configurable_window(self, sample_data):
        """Supply window should be configurable via constructor."""
        factor = RetailSupplyFactor(supply_days=3)
        result = factor.compute(sample_data)

        last_date = result["date"].max()
        # A: last 3 days = 40 + 50 + 60 = 150
        a_score = result.loc[
            (result["ticker"] == "A") & (result["date"] == last_date), "supply_score"
        ].iloc[0]
        assert a_score == 150

        # B: last 3 days = 400 + 500 + 600 = 1500
        b_score = result.loc[
            (result["ticker"] == "B") & (result["date"] == last_date), "supply_score"
        ].iloc[0]
        assert b_score == 1500

    def test_name_property(self):
        assert RetailSupplyFactor().name == "RetailSupply"


# ── Factor ABC ───────────────────────────────────────────────────────


class TestFactorABC:
    """Factor abstract base class tests."""

    def test_cannot_instantiate_abc(self):
        """Factor ABC cannot be instantiated directly."""
        with pytest.raises(TypeError):
            Factor()  # type: ignore[abstract]

    def test_concrete_factor_is_factor(self):
        """All concrete factors are instances of Factor."""
        factors = [
            PBRFactor(),
            MarketCapFactor(),
            GPAFactor(),
            KosdaqMAFactor(),
            RetailSupplyFactor(),
        ]
        for f in factors:
            assert isinstance(f, Factor)
