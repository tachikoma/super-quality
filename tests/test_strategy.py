"""Tests for SuperQualityStrategy — buy/sell condition evaluation."""

from __future__ import annotations

import pandas as pd
import pytest

from super_quality.config import SuperQualityConfig
from super_quality.strategies.super_quality import SuperQualityStrategy


@pytest.fixture
def config() -> SuperQualityConfig:
    """Default config for tests (no DART_API_KEY needed)."""
    return SuperQualityConfig(DART_API_KEY="test")


@pytest.fixture
def strategy(config: SuperQualityConfig) -> SuperQualityStrategy:
    """Strategy instance with default config."""
    return SuperQualityStrategy(config)


# ═══════════════════════════════════════════════════════════════════════
# Buy conditions — evaluate_buy_conditions
# ═══════════════════════════════════════════════════════════════════════


def _make_buy_df(**overrides: dict) -> pd.DataFrame:
    """Create a minimal buy-condition DataFrame.

    All values are set so that all 8 conditions pass by default.
    Override specific columns via keyword arguments.
    """
    data = {
        "ticker": ["A", "B", "C"],
        "pbr_percentile": [10.0, 15.0, 5.0],          # A: ≤ 20
        "pbr": [2.0, 1.5, 3.0],                         # B: > 0
        "share_change_5mo_ago": [0, 0, 0],              # C: == 0
        "share_change_now": [0, 0, 0],                   # D: == 0
        "trailing_ni": [100.0, 200.0, 300.0],            # E: > 0
        "trailing_ocf": [50.0, 60.0, 70.0],              # F: > 0
        "mcap_percentile": [25.0, 30.0, 20.0],           # G: ≤ 40
        "buy_signal": [True, True, True],          # H: True
        "gpa_percentile": [80.0, 50.0, 90.0],
        "supply_percentile": [70.0, 60.0, 80.0],
    }
    data.update(overrides)
    return pd.DataFrame(data)


class TestEvaluateBuyConditions:
    """Tests for ``evaluate_buy_conditions``."""

    def test_all_conditions_pass(self, strategy: SuperQualityStrategy) -> None:
        """All 8 conditions pass → all_buy_conditions = True for all tickers."""
        df = _make_buy_df()
        result = strategy.evaluate_buy_conditions(df)

        assert "all_buy_conditions" in result.columns
        assert list(result["all_buy_conditions"]) == [True, True, True]

        # Each individual condition column should also be present
        for col in ("a_pass", "b_pass", "c_pass", "d_pass",
                    "e_pass", "f_pass", "g_pass", "h_pass"):
            assert col in result.columns
            assert list(result[col]) == [True, True, True]

    def test_condition_a_fails_high_pbr_percentile(
        self, strategy: SuperQualityStrategy,
    ) -> None:
        """PBR percentile > 20% → A fails → all_buy_conditions = False."""
        df = _make_buy_df(pbr_percentile=[25.0, 30.0, 10.0])
        result = strategy.evaluate_buy_conditions(df)

        assert list(result["a_pass"]) == [False, False, True]
        assert list(result["all_buy_conditions"]) == [False, False, True]

    def test_condition_b_fails_zero_pbr(
        self, strategy: SuperQualityStrategy,
    ) -> None:
        """PBR <= 0 → B fails."""
        df = _make_buy_df(pbr=[0.0, -1.0, 2.0])
        result = strategy.evaluate_buy_conditions(df)

        assert list(result["b_pass"]) == [False, False, True]
        assert list(result["all_buy_conditions"]) == [False, False, True]

    def test_condition_c_fails_share_change(
        self, strategy: SuperQualityStrategy,
    ) -> None:
        """share_change_5mo_ago != 0 → C fails. With 2+ secondary failures, all_buy=False."""
        df = _make_buy_df(
            share_change_5mo_ago=[1, 0, -1],
            mcap_percentile=[50.0, 30.0, 50.0],
            buy_signal=[False, True, False],
        )
        result = strategy.evaluate_buy_conditions(df)

        assert list(result["c_pass"]) == [False, True, False]
        assert list(result["all_buy_conditions"]) == [False, True, False]

    def test_condition_d_fails_share_change_now(
        self, strategy: SuperQualityStrategy,
    ) -> None:
        """share_change_now != 0 → D fails. With 2+ secondary failures, all_buy=False."""
        df = _make_buy_df(
            share_change_now=[0, 1, 0],
            mcap_percentile=[25.0, 50.0, 50.0],
            buy_signal=[True, False, True],
        )
        result = strategy.evaluate_buy_conditions(df)

        assert list(result["d_pass"]) == [True, False, True]
        assert list(result["all_buy_conditions"]) == [True, False, True]

    def test_condition_e_fails_negative_ni(
        self, strategy: SuperQualityStrategy,
    ) -> None:
        """trailing_ni <= 0 → E fails."""
        df = _make_buy_df(trailing_ni=[100.0, 0.0, -50.0])
        result = strategy.evaluate_buy_conditions(df)

        assert list(result["e_pass"]) == [True, False, False]
        assert list(result["all_buy_conditions"]) == [True, False, False]

    def test_condition_f_fails_negative_ocf(
        self, strategy: SuperQualityStrategy,
    ) -> None:
        """trailing_ocf <= 0 → F fails."""
        df = _make_buy_df(trailing_ocf=[50.0, 0.0, -10.0])
        result = strategy.evaluate_buy_conditions(df)

        assert list(result["f_pass"]) == [True, False, False]
        assert list(result["all_buy_conditions"]) == [True, False, False]

    def test_condition_g_fails_high_mcap(
        self, strategy: SuperQualityStrategy,
    ) -> None:
        """mcap_percentile > 40% → G fails. With 2+ secondary failures, all_buy=False."""
        df = _make_buy_df(
            mcap_percentile=[25.0, 50.0, 35.0],
            share_change_5mo_ago=[0, 1, 1],
            share_change_now=[0, 1, 1],
        )
        result = strategy.evaluate_buy_conditions(df)

        assert list(result["g_pass"]) == [True, False, True]
        assert list(result["all_buy_conditions"]) == [True, False, True]

    def test_condition_h_fails_no_buy_signal(
        self, strategy: SuperQualityStrategy,
    ) -> None:
        """buy_signal = False → H fails. With 2+ secondary failures, all_buy=False."""
        df = _make_buy_df(
            buy_signal=[True, False, True],
            share_change_5mo_ago=[0, 1, 0],
            share_change_now=[0, 1, 0],
        )
        result = strategy.evaluate_buy_conditions(df)

        assert list(result["h_pass"]) == [True, False, True]
        assert list(result["all_buy_conditions"]) == [True, False, True]

    def test_priority_ranking(self, strategy: SuperQualityStrategy) -> None:
        """priority_score = gpa_percentile + supply_percentile.

        Only qualifying stocks get a non-zero priority_score.
        Ticker C (90+80=170) > Ticker A (80+70=150) > Ticker B (50+60=110).
        """
        df = _make_buy_df()
        result = strategy.evaluate_buy_conditions(df)

        scores = result.set_index("ticker")["priority_score"]
        assert scores["A"] == 150.0
        assert scores["B"] == 110.0
        assert scores["C"] == 170.0

        # Check descending order
        sorted_scores = result.sort_values("priority_score", ascending=False)
        assert list(sorted_scores["ticker"]) == ["C", "A", "B"]

    def test_priority_score_zero_for_failed_conditions(
        self, strategy: SuperQualityStrategy,
    ) -> None:
        """Stocks that fail any condition get priority_score = 0.0."""
        df = _make_buy_df(pbr_percentile=[10.0, 50.0, 5.0],  # B fails on PBR>0 check... wait
                          pbr=[2.0, -1.0, 3.0])  # B fails on ticker B
        result = strategy.evaluate_buy_conditions(df)

        scores = result.set_index("ticker")["priority_score"]
        assert scores["A"] > 0  # passes all
        assert scores["B"] == 0.0  # fails B
        assert scores["C"] > 0  # passes all

    def test_empty_dataframe(self, strategy: SuperQualityStrategy) -> None:
        """Empty input → empty output with expected columns."""
        df = _make_buy_df().iloc[0:0]
        result = strategy.evaluate_buy_conditions(df)
        assert len(result) == 0
        expected_cols = [
            "ticker", "a_pass", "b_pass", "c_pass", "d_pass",
            "e_pass", "f_pass", "g_pass", "h_pass",
            "all_buy_conditions", "priority_score",
        ]
        for col in expected_cols:
            assert col in result.columns


# ═══════════════════════════════════════════════════════════════════════
# Sell conditions — evaluate_sell_conditions
# ═══════════════════════════════════════════════════════════════════════


class TestEvaluateSellConditions:
    """Tests for ``evaluate_sell_conditions``."""

    @pytest.fixture
    def position(self) -> dict:
        """A sample open position."""
        return {
            "ticker": "A",
            "shares": 100,
            "entry_price": 10000.0,
            "entry_date": pd.Timestamp("2024-01-02"),
        }

    def test_stop_loss_triggers(self, strategy: SuperQualityStrategy, position: dict) -> None:
        """Return <= -20% → 'stop_loss'."""
        result = strategy.evaluate_sell_conditions(
            position=position,
            current_price=7900.0,  # -21%
            entry_price=10000.0,
            hold_days=3,
        )
        assert result == "stop_loss"

    def test_stop_loss_edge(self, strategy: SuperQualityStrategy, position: dict) -> None:
        """Below -20% → 'stop_loss' (<= threshold)."""
        result = strategy.evaluate_sell_conditions(
            position=position,
            current_price=7999.0,  # -20.01% (clearly <= -20%)
            entry_price=10000.0,
            hold_days=3,
        )
        assert result == "stop_loss"

    def test_stop_loss_not_triggered(self, strategy: SuperQualityStrategy, position: dict) -> None:
        """Return -19% → stop-loss NOT triggered (above -20%)."""
        result = strategy.evaluate_sell_conditions(
            position=position,
            current_price=8100.0,  # -19%
            entry_price=10000.0,
            hold_days=3,
        )
        assert result != "stop_loss"

    def test_expiry_triggers(self, strategy: SuperQualityStrategy, position: dict) -> None:
        """hold_days >= 20 → 'expiry'."""
        result = strategy.evaluate_sell_conditions(
            position=position,
            current_price=10100.0,  # +1% (above stop-loss)
            entry_price=10000.0,
            hold_days=20,
        )
        assert result == "expiry"

    def test_expiry_edge(self, strategy: SuperQualityStrategy, position: dict) -> None:
        """hold_days = 19 → NOT yet expiry."""
        result = strategy.evaluate_sell_conditions(
            position=position,
            current_price=10100.0,
            entry_price=10000.0,
            hold_days=19,
        )
        assert result is None

    def test_no_sell_signal(self, strategy: SuperQualityStrategy, position: dict) -> None:
        """No conditions met → None."""
        result = strategy.evaluate_sell_conditions(
            position=position,
            current_price=10500.0,  # +5%
            entry_price=10000.0,
            hold_days=2,
        )
        assert result is None

    def test_stop_loss_priority_over_expiry(
        self, strategy: SuperQualityStrategy, position: dict,
    ) -> None:
        """stop_loss (-21%) takes priority over expiry (hold_days=20)."""
        result = strategy.evaluate_sell_conditions(
            position=position,
            current_price=7900.0,  # -21%
            entry_price=10000.0,
            hold_days=20,
        )
        assert result == "stop_loss"


# ═══════════════════════════════════════════════════════════════════════
# Smoke / import
# ═══════════════════════════════════════════════════════════════════════


def test_strategy_importable() -> None:
    """SuperQualityStrategy can be imported and instantiated."""
    cfg = SuperQualityConfig(DART_API_KEY="test")
    st = SuperQualityStrategy(cfg)
    assert isinstance(st, SuperQualityStrategy)
    assert st.config.PBR_PERCENTILE == 0.20
    assert st.config.MAX_HOLD_DAYS == 20
