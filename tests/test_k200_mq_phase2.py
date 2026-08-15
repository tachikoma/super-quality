"""Focused regression tests for the K200MQ Phase 2 execution fixes."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import pytest
from k200_mq.backtest.portfolio_engine import PortfolioRebalanceEngine
from k200_mq.backtest.benchmark import build_price_return_benchmark
from k200_mq.config import K200MQConfig
from k200_mq.core.data import loader
from k200_mq.core.analysis.metrics import compute_cost_attribution
from k200_mq.data import universe as universe_module
from k200_mq.factors.momentum import MomentumFactor
from k200_mq.factors.regime import RegimeFactor
from k200_mq.main import (
    _build_run_manifest,
    _convert_financial_to_daily,
    _measure_financial_coverage,
    _save_results,
    _validate_first_rebalance_factor_readiness,
)


def _price_data(
    dates: list[pd.Timestamp],
    prices: dict[str, list[tuple[float, float]]],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for ticker, values in prices.items():
        for dt, (open_price, close_price) in zip(dates, values):
            rows.append({
                "ticker": ticker,
                "date": dt,
                "open": open_price,
                "high": max(open_price, close_price),
                "low": min(open_price, close_price),
                "close": close_price,
                "volume": 1_000_000.0,
                "mcap": 1_000_000.0,
            })
    return pd.DataFrame(rows).set_index(["ticker", "date"]).sort_index()


def _factors(dates: list[pd.Timestamp], tickers: list[str]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "ticker": ticker,
            "date": dt,
            "momentum_z": 1.0 if ticker == "A" else 0.0,
            "quality_z": 1.0 if ticker == "A" else 0.0,
        }
        for ticker in tickers
        for dt in dates
    ])


def _config(**overrides: object) -> K200MQConfig:
    values = {
        "INITIAL_CAPITAL": 1_000.0,
        "TOP_N": 1,
        "EXCLUDE_KOSPI_TOP_N": 0,
        "COMMISSION_RATE": 0.0,
        "TAX_RATE": 0.0,
        "SLIPPAGE": 0.0,
        "SL_STOP_LOSS": -0.15,
    }
    values.update(overrides)
    return K200MQConfig.model_validate(values)


def _run(
    price_data: pd.DataFrame,
    universe_rows: list[dict[str, object]],
    factor_tickers: list[str],
    **run_kwargs: object,
) -> dict[str, object]:
    dates = list(pd.DatetimeIndex(price_data.index.get_level_values("date")).unique())
    factors = _factors(dates, factor_tickers)
    return PortfolioRebalanceEngine(_config()).run(
        price_data,
        pd.DataFrame(),
        factors,
        pd.DataFrame(universe_rows),
        **run_kwargs,
    )


def _run_with_config(
    config: K200MQConfig,
    price_data: pd.DataFrame,
    universe_rows: list[dict[str, object]],
    factor_tickers: list[str],
    **run_kwargs: object,
) -> dict[str, object]:
    dates = list(pd.DatetimeIndex(price_data.index.get_level_values("date")).unique())
    return PortfolioRebalanceEngine(config).run(
        price_data,
        run_kwargs.pop("index_data", pd.DataFrame()),
        _factors(dates, factor_tickers),
        pd.DataFrame(universe_rows),
        **run_kwargs,
    )


def test_cache_assembly_filters_tickers_dates_and_duplicate_rows(monkeypatch, tmp_path) -> None:
    """Legacy date-only metadata cannot imply coverage for a missing ticker."""
    cache = loader.DataCache(str(tmp_path))
    cached = pd.DataFrame({
        "ticker": ["A", "A", "B", "C"],
        "date": pd.to_datetime([
            "2024-01-02", "2024-01-02", "2024-01-03", "2024-02-01",
        ]),
        "open": [1.0, 2.0, 3.0, 4.0],
        "high": [1.0, 2.0, 3.0, 4.0],
        "low": [1.0, 2.0, 3.0, 4.0],
        "close": [1.0, 2.0, 3.0, 4.0],
        "volume": [1.0] * 4,
        "mcap": [0.0] * 4,
    })
    cache.put("price_2024", cached)
    cache.put_json("price_meta", {
        "cache_version": 1,
        "years": {"2024": {"req_start": "2024-01-01", "req_end": "2024-01-31"}},
    })
    monkeypatch.setattr(loader, "_cache", cache)
    monkeypatch.setattr(loader, "get_krx_listings", lambda: pd.DataFrame({"ticker": []}))

    downloaded: list[list[str]] = []

    def fake_download(
        tickers: list[str],
        start: date,
        end: date,
        shares_map: dict[str, float],
    ) -> pd.DataFrame:
        del start, end, shares_map
        downloaded.append(tickers)
        return cached.iloc[[0, 1, 2]].assign(ticker=["A", "A", "C"])

    monkeypatch.setattr(loader, "_download_ticker_batch", fake_download)
    result = loader.get_price_data(["B"], "2024-01-01", "2024-01-31")
    assert downloaded == [["B"]]
    assert list(result.index.get_level_values("ticker")) == ["B"]
    assert result.index.is_unique
    assert result.index.get_level_values("date").min() >= pd.Timestamp("2024-01-01")
    assert result.index.get_level_values("date").max() <= pd.Timestamp("2024-01-31")

    downloaded.clear()
    result_with_missing = loader.get_price_data(["C"], "2024-01-01", "2024-01-31")
    assert downloaded == [["C"]]
    assert set(result_with_missing.index.get_level_values("ticker")) == {"C"}
    assert result_with_missing.index.is_unique


def test_partial_year_reload_merges_existing_tickers(monkeypatch, tmp_path) -> None:
    """A missing-ticker reload must not replace the existing yearly cache."""
    cache = loader.DataCache(str(tmp_path))
    dates = pd.to_datetime(["2024-01-02", "2024-01-03"])

    def rows(tickers: list[str], extra_date: str | None = None) -> pd.DataFrame:
        records = [
            {
                "ticker": ticker,
                "date": dt,
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 1.0,
                "mcap": 1.0,
            }
            for ticker in tickers
            for dt in dates
        ]
        if extra_date is not None:
            records.append({
                "ticker": tickers[-1],
                "date": pd.Timestamp(extra_date),
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 1.0,
                "mcap": 1.0,
            })
        return pd.DataFrame(records)

    cache.put("price_2024", rows(["A", "B"]))
    cache.put_json("price_meta", {
        "cache_version": 1,
        "years": {
            "2024": {
                "req_start": "2024-01-01",
                "req_end": "2024-01-31",
                "tickers": ["A", "B"],
            },
        },
    })
    monkeypatch.setattr(loader, "_cache", cache)
    monkeypatch.setattr(loader, "get_krx_listings", lambda: pd.DataFrame({"ticker": []}))

    downloaded: list[list[str]] = []

    def fake_download(
        tickers: list[str],
        start: date,
        end: date,
        shares_map: dict[str, float],
    ) -> pd.DataFrame:
        del start, end, shares_map
        downloaded.append(tickers)
        return rows(tickers, extra_date="2024-02-01")

    monkeypatch.setattr(loader, "_download_ticker_batch", fake_download)
    first = loader.get_price_data(["C"], "2024-01-01", "2024-01-31")
    assert downloaded == [["C"]]
    assert set(first.index.get_level_values("ticker")) == {"C"}

    merged = cache.get("price_2024")
    assert merged is not None
    assert set(merged["ticker"]) == {"A", "B", "C"}
    assert not merged.set_index(["ticker", "date"]).index.duplicated().any()
    metadata = cache.get_json("price_meta")
    assert metadata["years"]["2024"]["tickers"] == ["A", "B", "C"]

    def unexpected_download(*args: object, **kwargs: object) -> pd.DataFrame:
        raise AssertionError(f"unexpected cache reload: {args}, {kwargs}")

    monkeypatch.setattr(loader, "_download_ticker_batch", unexpected_download)
    result = loader.get_price_data(["A", "B", "C"], "2024-01-02", "2024-01-03")
    assert set(result.index.get_level_values("ticker")) == {"A", "B", "C"}
    assert len(result) == 6
    assert result.index.is_unique
    assert result.index.get_level_values("date").min() >= pd.Timestamp("2024-01-01")
    assert result.index.get_level_values("date").max() <= pd.Timestamp("2024-01-31")


def test_metadata_full_range_does_not_hide_late_ticker_start(monkeypatch, tmp_path) -> None:
    """A ticker's actual rows, not aggregate metadata, determine coverage."""
    cache = loader.DataCache(str(tmp_path))
    late_dates = pd.to_datetime(["2024-01-15", "2024-01-31"])
    late = pd.DataFrame({
        "ticker": ["LATE", "LATE"],
        "date": late_dates,
        "open": [1.0, 1.0],
        "high": [1.0, 1.0],
        "low": [1.0, 1.0],
        "close": [1.0, 1.0],
        "volume": [1.0, 1.0],
        "mcap": [0.0, 0.0],
    })
    cache.put("price_2024", late)
    cache.put_json("price_meta", {
        "cache_version": 1,
        "years": {
            "2024": {
                "req_start": "2024-01-01",
                "req_end": "2024-01-31",
                "tickers": ["LATE"],
            },
        },
    })
    monkeypatch.setattr(loader, "_cache", cache)
    monkeypatch.setattr(loader, "get_krx_listings", lambda: pd.DataFrame({"ticker": []}))

    calls: list[list[str]] = []

    def reload(
        tickers: list[str],
        start: date,
        end: date,
        shares_map: dict[str, float],
    ) -> pd.DataFrame:
        del start, end, shares_map
        calls.append(tickers)
        return late.assign(date=pd.to_datetime(["2024-01-02", "2024-01-31"]))

    monkeypatch.setattr(loader, "_download_ticker_batch", reload)
    result = loader.get_price_data(["LATE"], "2024-01-01", "2024-01-31")

    assert calls == [["LATE"]]
    assert result.index.get_level_values("date").min() == pd.Timestamp("2024-01-02")
    assert result.index.is_unique


def test_full_year_cache_accepts_observed_session_boundaries_and_reuses_cache(
    monkeypatch, tmp_path,
) -> None:
    """Jan 1/Dec 31 need not be rows when the observed daily range is complete."""
    cache = loader.DataCache(str(tmp_path))
    dates = pd.bdate_range("2024-01-02", "2024-12-31")
    cached = pd.DataFrame({
        "ticker": ["A"] * len(dates),
        "date": dates,
        "open": [1.0] * len(dates),
        "high": [1.0] * len(dates),
        "low": [1.0] * len(dates),
        "close": [1.0] * len(dates),
        "volume": [1.0] * len(dates),
        "mcap": [1.0] * len(dates),
    })
    cache.put("price_2024", cached)
    cache.put_json("price_meta", {
        "cache_version": 1,
        "years": {"2024": {
            "req_start": "2024-01-01",
            "req_end": "2024-12-31",
            "tickers": ["A"],
        }},
    })
    monkeypatch.setattr(loader, "_cache", cache)
    monkeypatch.setattr(loader, "get_krx_listings", lambda: pd.DataFrame({"ticker": []}))
    calls: list[list[str]] = []

    def unexpected_download(*args: object, **kwargs: object) -> pd.DataFrame:
        calls.append(list(args[0]))
        raise AssertionError("a complete observed-session cache must not download")

    monkeypatch.setattr(loader, "_download_ticker_batch", unexpected_download)
    first = loader.get_price_data(["A"], "2024-01-01", "2024-12-31")
    second = loader.get_price_data(["A"], "2024-01-01", "2024-12-31")
    assert calls == []
    assert first.index.get_level_values("date").min() == pd.Timestamp("2024-01-02")
    assert second.index.equals(first.index)


def test_material_interior_price_gap_triggers_reload(monkeypatch, tmp_path) -> None:
    cache = loader.DataCache(str(tmp_path))
    cached_dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-02-01"])
    cached = pd.DataFrame({
        "ticker": ["A"] * len(cached_dates),
        "date": cached_dates,
        "open": [1.0] * len(cached_dates),
        "high": [1.0] * len(cached_dates),
        "low": [1.0] * len(cached_dates),
        "close": [1.0] * len(cached_dates),
        "volume": [1.0] * len(cached_dates),
        "mcap": [1.0] * len(cached_dates),
    })
    cache.put("price_2024", cached)
    cache.put_json("price_meta", {
        "cache_version": 1,
        "years": {"2024": {"req_start": "2024-01-01", "req_end": "2024-02-01"}},
    })
    monkeypatch.setattr(loader, "_cache", cache)
    monkeypatch.setattr(loader, "get_krx_listings", lambda: pd.DataFrame({"ticker": []}))
    calls: list[list[str]] = []

    def reload(
        tickers: list[str],
        start: date,
        end: date,
        shares_map: dict[str, float],
    ) -> pd.DataFrame:
        del start, end, shares_map
        calls.append(tickers)
        dates = pd.bdate_range("2024-01-02", "2024-02-01")
        return pd.DataFrame({
            "ticker": [tickers[0]] * len(dates),
            "date": dates,
            "open": [1.0] * len(dates),
            "high": [1.0] * len(dates),
            "low": [1.0] * len(dates),
            "close": [1.0] * len(dates),
            "volume": [1.0] * len(dates),
            "mcap": [1.0] * len(dates),
        })

    monkeypatch.setattr(loader, "_download_ticker_batch", reload)
    result = loader.get_price_data(["A"], "2024-01-01", "2024-02-01")
    assert calls == [["A"]]
    assert result.index.get_level_values("date").min() == pd.Timestamp("2024-01-02")


def test_metadata_missing_orphan_price_is_merged_not_replaced(monkeypatch, tmp_path) -> None:
    cache = loader.DataCache(str(tmp_path))
    existing = pd.DataFrame({
        "ticker": ["A", "A"],
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "open": [1.0, 1.0],
        "high": [1.0, 1.0],
        "low": [1.0, 1.0],
        "close": [1.0, 1.0],
        "volume": [1.0, 1.0],
        "mcap": [1.0, 1.0],
    })
    cache.put("price_2024", existing)
    monkeypatch.setattr(loader, "_cache", cache)
    monkeypatch.setattr(loader, "get_krx_listings", lambda: pd.DataFrame({"ticker": []}))

    def download(
        tickers: list[str],
        start: date,
        end: date,
        shares_map: dict[str, float],
    ) -> pd.DataFrame:
        del start, end, shares_map
        return existing.assign(ticker=tickers[0])

    monkeypatch.setattr(loader, "_download_ticker_batch", download)
    loader.get_price_data(["B"], "2024-01-01", "2024-01-03")
    merged = cache.get("price_2024")
    assert merged is not None
    assert set(merged["ticker"]) == {"A", "B"}


def test_save_results_writes_manifest_without_secret_config_fields(tmp_path) -> None:
    config = _config(
        OUTPUT_DIR=str(tmp_path),
        DART_API_KEY="super-secret-dart-key",
        KRX_PW="super-secret-password",
    )
    results = {
        "portfolio_snapshots": pd.DataFrame(),
        "trade_log": pd.DataFrame(),
        "daily_returns": pd.Series(dtype=float),
    }

    _save_results(results, config)

    manifest_path = tmp_path / "run_manifest.json"
    assert manifest_path.exists()
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert "super-secret-dart-key" not in manifest_text
    assert "super-secret-password" not in manifest_text
    assert "DART_API_KEY" not in manifest["config"]
    assert "KRX_PW" not in manifest["config"]
    assert manifest["execution"]["entry_policy"] == "next open"
    assert "missing DART mode" in manifest["limitations"]["dart"]


def test_default_lookback_has_enough_trading_rows_for_first_momentum_bar(monkeypatch) -> None:
    """The synthetic loader contract yields a first measured momentum row."""
    def fake_get_price(
        tickers: list[str], start: str | date, end: str | date,
    ) -> pd.DataFrame:
        dates = pd.bdate_range(start, end)
        rows = [
            {
                "ticker": ticker,
                "date": dt,
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0 + i,
                "volume": 1.0,
                "mcap": 1.0,
            }
            for ticker in tickers
            for i, dt in enumerate(dates)
        ]
        return pd.DataFrame(rows).set_index(["ticker", "date"])

    monkeypatch.setattr(loader, "get_price_data", fake_get_price)
    start = date(2024, 1, 2)
    backtest, warmup = loader.get_price_data_with_lookback(
        ["A"], start, date(2024, 2, 29),
    )

    assert len(warmup.loc["A"]) > 210
    assert warmup.index.get_level_values("date").max() < pd.Timestamp(start)
    full = pd.concat([warmup.reset_index(), backtest.reset_index()], ignore_index=True)
    momentum = MomentumFactor().compute(
        full, long_window=252, short_window=126, skip_days=42,
    )
    assert pd.Timestamp(start) in set(momentum["date"])


def test_first_rebalance_readiness_does_not_require_quality_rows() -> None:
    """Partial/disabled quality coverage must not block momentum readiness."""
    signal_date = pd.Timestamp("2024-01-31")
    universe = pd.DataFrame({"as_of": [signal_date], "ticker": ["A"]})
    factors = pd.DataFrame({
        "ticker": ["A"],
        "date": [signal_date],
        "momentum_z": [0.5],
    })

    _validate_first_rebalance_factor_readiness(
        universe,
        factors,
        pd.DatetimeIndex([signal_date]),
        _config(TOP_N=1),
    )


def test_rebalance_readiness_skips_unready_schedule_and_reports_first_ready_date() -> None:
    measured_dates = pd.bdate_range("2024-01-02", "2024-02-29")
    first_scheduled = pd.Timestamp("2024-01-31")
    ready_scheduled = pd.Timestamp("2024-02-29")
    universe = pd.DataFrame({
        "as_of": [first_scheduled, first_scheduled, ready_scheduled, ready_scheduled],
        "ticker": ["A", "B", "A", "B"],
    })
    factors = pd.DataFrame({
        "ticker": ["A", "B"],
        "date": [ready_scheduled, ready_scheduled],
        "momentum_z": [0.5, 0.25],
    })

    readiness = _validate_first_rebalance_factor_readiness(
        universe,
        factors,
        measured_dates,
        _config(TOP_N=2),
    )

    assert readiness["first_scheduled_rebalance"]["scheduled_date"] == "2024-01-31"
    assert readiness["first_ready_rebalance"]["scheduled_date"] == "2024-02-29"
    assert readiness["first_ready_rebalance"]["usable_ticker_count"] == 2
    assert readiness["first_ready_rebalance"]["required_ticker_count"] == 2
    assert readiness["measured_trading_readiness_date"] == "2024-02-29"
    assert readiness["skipped_not_ready_rebalances"] == [{
        "scheduled_date": "2024-01-31",
        "signal_date": "2024-01-31",
        "usable_ticker_count": 0,
        "universe_ticker_count": 2,
        "required_ticker_count": 2,
        "usable_tickers": [],
        "missing_tickers": ["A", "B"],
        "quality_required": False,
    }]


def test_rebalance_schedule_on_non_measured_day_uses_preceding_signal_date() -> None:
    measured_dates = pd.bdate_range("2024-01-02", "2024-01-08")
    scheduled_date = pd.Timestamp("2024-01-06")
    signal_date = pd.Timestamp("2024-01-05")
    universe = pd.DataFrame({"as_of": [scheduled_date], "ticker": ["A"]})
    factors = pd.DataFrame({
        "ticker": ["A"],
        "date": [signal_date],
        "momentum_z": [0.5],
    })

    readiness = _validate_first_rebalance_factor_readiness(
        universe,
        factors,
        measured_dates,
        _config(TOP_N=1),
    )

    assert readiness["first_scheduled_rebalance"] == {
        "scheduled_date": "2024-01-06",
        "signal_date": "2024-01-05",
    }
    assert readiness["measured_trading_readiness_date"] == "2024-01-05"


def test_first_rebalance_readiness_fails_without_momentum_rows() -> None:
    signal_date = pd.Timestamp("2024-01-31")
    universe = pd.DataFrame({"as_of": [signal_date], "ticker": ["A"]})

    with pytest.raises(RuntimeError, match="momentum"):
        _validate_first_rebalance_factor_readiness(
            universe,
            pd.DataFrame(columns=["ticker", "date", "momentum_z"]),
            pd.DatetimeIndex([signal_date]),
            _config(TOP_N=1),
        )


def test_first_rebalance_readiness_fails_when_positive_coverage_is_insufficient() -> None:
    signal_date = pd.Timestamp("2024-01-31")
    universe = pd.DataFrame({"as_of": [signal_date, signal_date], "ticker": ["A", "B"]})
    factors = pd.DataFrame({
        "ticker": ["A", "B"],
        "date": [signal_date, signal_date],
        "momentum_z": [0.5, float("nan")],
    })

    with pytest.raises(RuntimeError, match="1/2"):
        _validate_first_rebalance_factor_readiness(
            universe,
            factors,
            pd.DatetimeIndex([signal_date]),
            _config(TOP_N=2),
        )


def test_rebalance_readiness_fails_when_all_schedules_are_insufficient() -> None:
    scheduled_dates = pd.to_datetime(["2024-01-31", "2024-02-29"])
    universe = pd.DataFrame({
        "as_of": [scheduled_dates[0], scheduled_dates[0], scheduled_dates[1], scheduled_dates[1]],
        "ticker": ["A", "B", "A", "B"],
    })
    factors = pd.DataFrame({
        "ticker": ["A", "B", "A", "B"],
        "date": [scheduled_dates[0], scheduled_dates[0], scheduled_dates[1], scheduled_dates[1]],
        "momentum_z": [0.5, float("nan"), 0.5, float("nan")],
    })

    with pytest.raises(RuntimeError, match="2024-01-31=1/2, 2024-02-29=1/2"):
        _validate_first_rebalance_factor_readiness(
            universe,
            factors,
            pd.bdate_range("2024-01-02", "2024-02-29"),
            _config(TOP_N=2),
        )


def test_financial_coverage_uses_exact_scheduled_universe_snapshot() -> None:
    measured = pd.bdate_range("2024-01-02", "2024-02-29")
    scheduled = pd.Timestamp("2024-01-31")
    universe = pd.DataFrame({
        "as_of": [scheduled, scheduled, pd.Timestamp("2024-02-29")],
        "ticker": ["A", "B", "A"],
    })
    daily = pd.DataFrame({
        "ticker": ["A", "B", "C"],
        "date": [scheduled, scheduled, scheduled],
        "financial_six_fact_available": [True, False, True],
    })

    result = _measure_financial_coverage(
        universe, daily, measured, {"mode": "pit_filing_date", "pit_valid": True},
    )

    record = result["records"][0]
    assert record["available_tickers"] == ["A"]
    assert record["missing_tickers"] == ["B"]
    assert record["universe_ticker_count"] == 2


def test_financial_coverage_is_bounded_by_signal_date() -> None:
    measured = pd.bdate_range("2024-01-02", "2024-02-29")
    universe = pd.DataFrame({
        "as_of": [pd.Timestamp("2024-01-31")],
        "ticker": ["A"],
    })
    daily = pd.DataFrame({
        "ticker": ["A", "A"],
        "date": [pd.Timestamp("2024-02-01"), pd.Timestamp("2024-02-29")],
        "financial_six_fact_available": [True, True],
    })

    before = _measure_financial_coverage(universe, daily, measured)
    assert before["records"][0]["six_fact_available_ticker_count"] == 0

    at_signal = _measure_financial_coverage(
        universe,
        daily.assign(date=pd.to_datetime(["2024-01-31", "2024-02-29"])),
        measured,
    )
    assert at_signal["records"][0]["six_fact_available_ticker_count"] == 1


def test_financial_coverage_uses_latest_historical_source_report_state() -> None:
    scheduled = pd.Timestamp("2024-01-31")
    universe = pd.DataFrame({"as_of": [scheduled], "ticker": ["A"]})
    daily = pd.DataFrame({
        "ticker": ["A", "A"],
        "date": [pd.Timestamp("2024-01-30"), scheduled],
        "financial_six_fact_available": [True, False],
    })

    result = _measure_financial_coverage(
        universe, daily, pd.DatetimeIndex([scheduled]),
    )
    record = result["records"][0]
    assert record["six_fact_available_ticker_count"] == 0
    assert record["six_fact_source_coverage_ratio"] == 0.0
    assert record["available_tickers"] == []
    assert record["missing_tickers"] == ["A"]


def test_converter_tracks_complete_six_facts_and_literal_zero() -> None:
    dates = pd.bdate_range("2024-03-01", "2024-04-02")
    financial = pd.DataFrame({
        "ticker": ["ZERO", "MISSING", "NONFINITE"],
        "year": [2024, 2024, 2024],
        "quarter": [1, 1, 1],
        "net_income": [0.0, 0.0, 0.0],
        "total_equity": [0.0, 0.0, 0.0],
        "total_assets": [0.0, 0.0, 0.0],
        "revenue": [0.0, None, 0.0],
        "cogs": [0.0, 0.0, float("inf")],
        "operating_cf": [0.0, 0.0, 0.0],
    })

    daily = _convert_financial_to_daily(financial, dates)
    available = daily.groupby("ticker")["financial_six_fact_available"].last()
    assert bool(available["ZERO"]) is True
    assert bool(available["MISSING"]) is False
    assert bool(available["NONFINITE"]) is False
    assert "gross_profit_proxy" in daily.columns
    assert "operating_income" not in daily.columns

    proxy_financial = pd.DataFrame({
        "ticker": ["POSITIVE", "FLOORED"],
        "year": [2024, 2024],
        "quarter": [1, 1],
        "net_income": [1.0, 1.0],
        "total_equity": [1.0, 1.0],
        "total_assets": [1.0, 1.0],
        "revenue": [100.0, 100.0],
        "cogs": [40.0, 120.0],
        "operating_cf": [1.0, 1.0],
    })
    proxy_daily = _convert_financial_to_daily(proxy_financial, dates)
    proxy_values = proxy_daily.groupby("ticker")["gross_profit_proxy"].last()
    assert proxy_values["POSITIVE"] == pytest.approx(max(100.0 - 40.0, 0.0))
    assert proxy_values["FLOORED"] == pytest.approx(max(100.0 - 120.0, 0.0))
    zero_before_record = daily.query("ticker == 'ZERO' and date < '2024-03-28'")
    assert not zero_before_record["financial_six_fact_available"].any()


def test_converter_resets_availability_after_later_incomplete_source_report() -> None:
    dates = pd.bdate_range("2024-03-28", "2024-07-01")
    financial = pd.DataFrame({
        "ticker": ["A", "A"],
        "year": [2024, 2024],
        "quarter": [1, 2],
        "net_income": [1.0, 2.0],
        "total_equity": [1.0, 2.0],
        "total_assets": [1.0, 2.0],
        "revenue": [1.0, 2.0],
        "cogs": [1.0, 2.0],
        "operating_cf": [1.0, None],
    })

    daily = _convert_financial_to_daily(financial, dates)
    available = daily.set_index("date")["financial_six_fact_available"]

    assert bool(available.loc["2024-03-28"]) is True
    assert bool(available.loc["2024-06-27"]) is True
    assert bool(available.loc["2024-06-28"]) is False
    assert bool(available.loc["2024-07-01"]) is False


def test_converter_restores_availability_after_later_complete_source_report() -> None:
    dates = pd.bdate_range("2024-03-28", "2024-07-01")
    financial = pd.DataFrame({
        "ticker": ["A", "A"],
        "year": [2024, 2024],
        "quarter": [1, 2],
        "net_income": [1.0, 2.0],
        "total_equity": [1.0, 2.0],
        "total_assets": [1.0, 2.0],
        "revenue": [1.0, 2.0],
        "cogs": [1.0, 2.0],
        "operating_cf": [None, 2.0],
    })

    daily = _convert_financial_to_daily(financial, dates)
    available = daily.set_index("date")["financial_six_fact_available"]

    assert bool(available.loc["2024-03-28"]) is False
    assert bool(available.loc["2024-06-27"]) is False
    assert bool(available.loc["2024-06-28"]) is True
    assert bool(available.loc["2024-07-01"]) is True


def test_financial_coverage_non_pit_has_zero_pit_valid_count() -> None:
    scheduled = pd.Timestamp("2024-01-31")
    universe = pd.DataFrame({"as_of": [scheduled], "ticker": ["A"]})
    daily = pd.DataFrame({
        "ticker": ["A"],
        "date": [scheduled],
        "financial_six_fact_available": [True],
    })

    result = _measure_financial_coverage(
        universe, daily, pd.DatetimeIndex([scheduled]),
        {"mode": "non_pit_fiscal_period", "pit_valid": False},
    )
    record = result["records"][0]
    assert record["six_fact_available_ticker_count"] == 1
    assert record["pit_valid_ticker_count"] == 0
    assert record["six_fact_source_coverage_ratio"] == 1.0
    assert record["pit_valid_financial_coverage_ratio"] == 0.0
    assert record["financial_coverage_ratio"] == 0.0
    assert record["financial_pit_valid"] is False
    assert result["mode"] == "bounded_no_lookahead_six_fact_non_pit"


def test_financial_coverage_manifest_schema_reconciles_tickers() -> None:
    coverage = {
        "mode": "bounded_no_lookahead_six_fact",
        "records": [{
            "scheduled_date": "2024-01-31",
            "signal_date": "2024-01-31",
            "universe_ticker_count": 2,
            "six_fact_available_ticker_count": 1,
            "pit_valid_ticker_count": 0,
            "six_fact_source_coverage_ratio": 0.5,
            "pit_valid_financial_coverage_ratio": 0.0,
            "financial_coverage_ratio": 0.0,
            "available_tickers": ["A"],
            "missing_tickers": ["B"],
            "financial_pit_valid": False,
            "data_mode": "non_pit_fiscal_period",
        }],
    }
    manifest = _build_run_manifest(_config(), {"financial_coverage": coverage})
    record = manifest["financial_coverage"]["records"][0]
    assert len(record["available_tickers"]) + len(record["missing_tickers"]) == (
        record["universe_ticker_count"]
    )


def test_lookback_is_half_open_and_excludes_measurement_start(monkeypatch) -> None:
    calls: list[tuple[date, date]] = []

    def fake_get_price(
        tickers: list[str], start: str | date, end: str | date,
    ) -> pd.DataFrame:
        del tickers
        start_date = pd.Timestamp(start).date()
        end_date = pd.Timestamp(end).date()
        calls.append((start_date, end_date))
        return pd.DataFrame(
            {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
             "volume": [1.0], "mcap": [1.0]},
            index=pd.MultiIndex.from_tuples(
                [("A", pd.Timestamp(start_date))], names=["ticker", "date"],
            ),
        )

    monkeypatch.setattr(loader, "get_price_data", fake_get_price)
    start = date(2024, 2, 1)
    backtest, warmup = loader.get_price_data_with_lookback(["A"], start, date(2024, 2, 5), 5)
    assert calls[0] == (start, date(2024, 2, 5))
    assert calls[1][1] == start - timedelta(days=1)
    assert pd.Timestamp(start) not in warmup.index.get_level_values("date")
    assert pd.Timestamp(start) in backtest.index.get_level_values("date")
    assert backtest.index.intersection(warmup.index).empty


def test_regime_warmup_is_unknown_not_bearish() -> None:
    dates = pd.date_range("2024-01-01", periods=4, freq="B")
    result = RegimeFactor().compute(
        pd.DataFrame({"date": dates, "close": [100.0, 101.0, 102.0, 103.0]}),
        ma_period=3,
        min_return_days=1,
        reduction=0.5,
    )
    assert result.loc[:1, "regime"].isna().all()
    assert result.loc[:1, "position_scale"].isna().all()
    assert bool(result.loc[3, "regime"])


def test_regime_preserves_main_style_dates_and_index_dates() -> None:
    dates = pd.date_range("2024-01-02", periods=6, freq="B")
    index_raw = pd.DataFrame(
        {"close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]},
        index=pd.Index(dates, name="date"),
    )

    # This mirrors main.py: get_market_index returns a date index, then the
    # pipeline calls reset_index before constructing the measured map.
    main_style = RegimeFactor().compute(
        index_raw.reset_index(), ma_period=3, min_return_days=1, reduction=0.5,
    )
    measured_dates = dates[2:]
    measured = main_style[main_style["date"].isin(measured_dates)].dropna(
        subset=["regime", "position_scale"],
    )
    measured_map = measured.set_index("date")["position_scale"].to_dict()
    assert measured_map
    assert set(measured_map) == set(measured_dates)

    indexed = RegimeFactor().compute(index_raw, ma_period=3, min_return_days=1)
    pd.testing.assert_index_equal(pd.DatetimeIndex(indexed["date"]), dates.rename("date"))


def test_factor_and_regime_warmup_are_ready_at_measured_start() -> None:
    dates = pd.date_range("2023-01-02", periods=8, freq="B")
    price = pd.DataFrame({
        "ticker": ["A"] * len(dates),
        "date": dates,
        "close": [100.0 + i for i in range(len(dates))],
    })
    momentum = MomentumFactor().compute(
        price, long_window=5, short_window=3, skip_days=1,
    )
    regime = RegimeFactor().compute(
        pd.DataFrame({"date": dates, "close": [100.0 + i for i in range(len(dates))]}),
        ma_period=4,
        min_return_days=2,
    )

    measured_start = dates[5]
    assert momentum["date"].min() >= dates[4]
    measured_momentum = momentum[momentum["date"] >= measured_start]
    measured_regime = regime[regime["date"] >= measured_start]
    assert not measured_momentum.empty
    assert measured_regime["regime"].notna().all()
    assert measured_regime["position_scale"].notna().all()


def test_month_end_weekend_maps_to_one_signal(monkeypatch) -> None:
    monkeypatch.setattr(
        universe_module,
        "get_kospi200_constituents",
        lambda as_of: ["A"],
    )
    history = universe_module.get_kospi200_history(
        date(2023, 9, 1), date(2023, 9, 30), "M",
    )
    assert history["as_of"].tolist() == [date(2023, 9, 29)]
    assert history["as_of"].nunique() == 1


def test_engine_month_end_holiday_maps_signal_to_prior_bar() -> None:
    dates = pd.to_datetime(["2023-09-28", "2023-10-04", "2023-10-05"])
    prices = _price_data(dates, {"A": [(10.0, 10.0)] * len(dates)})
    factors = _factors(list(dates), ["A"])
    result = PortfolioRebalanceEngine(_config()).run(
        prices,
        pd.DataFrame(),
        factors,
        pd.DataFrame([{"as_of": date(2023, 9, 30), "ticker": "A"}]),
    )
    trades = result["trade_log"]
    assert len(trades) == 1
    assert trades.iloc[0]["signal_date"] == dates[0]
    assert trades.iloc[0]["execution_date"] == dates[1]


def test_entry_uses_next_open_not_previous_or_same_close() -> None:
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    prices = _price_data(dates, {"A": [(10.0, 11.0), (20.0, 21.0), (30.0, 31.0)]})
    result = _run(
        prices,
        [{"as_of": dates[0], "ticker": "A"}],
        ["A"],
    )
    trades = result["trade_log"]
    assert len(trades) == 1
    assert trades.iloc[0]["buy_price"] == 20.0
    assert trades.iloc[0]["signal_date"] == dates[0]
    assert trades.iloc[0]["execution_date"] == dates[1]
    assert trades.iloc[0]["entry_date"] == dates[1]


def test_engine_keeps_snapshots_flat_until_active_trading_start() -> None:
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    prices = _price_data(dates, {"A": [(10.0, 10.0)] * len(dates)})
    result = PortfolioRebalanceEngine(_config()).run(
        prices,
        pd.DataFrame(),
        _factors(list(dates), ["A"]),
        pd.DataFrame([
            {"as_of": dates[0], "ticker": "A"},
            {"as_of": dates[2], "ticker": "A"},
        ]),
        active_trading_start=dates[2],
    )

    snapshots = result["portfolio_snapshots"]
    assert (snapshots.loc[snapshots["date"] < dates[2], "num_positions"] == 0).all()
    assert result["trade_log"]["signal_date"].min() == dates[2]
    assert result["trade_log"]["execution_date"].min() == dates[3]


def test_rebalance_exit_uses_next_open_and_sells_before_buying() -> None:
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    prices = _price_data(
        dates,
        {
            "A": [(10.0, 10.0), (10.0, 10.0), (10.0, 10.0), (70.0, 70.0)],
            "B": [(10.0, 10.0), (10.0, 10.0), (10.0, 10.0), (10.0, 10.0)],
        },
    )
    # Make B the selected ticker from the second signal onwards.
    factors = _factors(list(dates), ["A", "B"])
    factors.loc[(factors["ticker"] == "B") & (factors["date"] == dates[2]), "momentum_z"] = 2.0
    factors.loc[(factors["ticker"] == "B") & (factors["date"] == dates[2]), "quality_z"] = 2.0
    factors.loc[(factors["ticker"] == "A") & (factors["date"] == dates[2]), "momentum_z"] = 0.0
    factors.loc[(factors["ticker"] == "A") & (factors["date"] == dates[2]), "quality_z"] = 0.0
    result = PortfolioRebalanceEngine(_config()).run(
        prices,
        pd.DataFrame(),
        factors,
        pd.DataFrame([
            {"as_of": dates[0], "ticker": "A"},
            {"as_of": dates[2], "ticker": "B"},
        ]),
    )
    trades = result["trade_log"]
    assert trades.iloc[1]["ticker"] == "A"
    assert trades.iloc[1]["sell_price"] == 70.0
    assert trades.iloc[1]["execution_date"] == dates[3]
    assert trades.iloc[2]["ticker"] == "B"
    assert trades.iloc[2]["buy_price"] == 10.0


def test_stop_loss_gap_executes_at_next_open() -> None:
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    prices = _price_data(
        dates,
        {"A": [(10.0, 10.0), (20.0, 20.0), (19.0, 16.0), (5.0, 5.0)]},
    )
    result = _run(prices, [{"as_of": dates[0], "ticker": "A"}], ["A"])
    exits = result["trade_log"].query("exit_reason == 'stop_loss'")
    assert len(exits) == 1
    assert exits.iloc[0]["sell_price"] == 5.0
    assert exits.iloc[0]["execution_date"] == dates[3]


def test_final_bar_signal_does_not_create_trade() -> None:
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    prices = _price_data(dates, {"A": [(10.0, 10.0)] * 3})
    result = _run(
        prices,
        [{"as_of": dates[-1], "ticker": "A"}],
        ["A"],
    )
    assert result["trade_log"].empty


def test_warmup_rows_do_not_change_measured_results() -> None:
    warmup_dates = pd.date_range("2024-01-01", periods=2, freq="B")
    measured_dates = pd.date_range("2024-01-03", periods=3, freq="B")
    all_dates = list(warmup_dates) + list(measured_dates)
    prices = _price_data(
        all_dates,
        {"A": [(9.0, 9.0), (9.0, 9.0), (10.0, 10.0), (20.0, 20.0), (20.0, 20.0)]},
    )
    universe_rows = [{"as_of": measured_dates[0], "ticker": "A"}]
    full = _run(
        prices,
        universe_rows,
        ["A"],
        measured_start=measured_dates[0],
        measured_end=measured_dates[-1],
    )
    measured = _run(
        prices.loc[("A", measured_dates), :],
        universe_rows,
        ["A"],
    )
    pd.testing.assert_frame_equal(
        full["portfolio_snapshots"].reset_index(drop=True),
        measured["portfolio_snapshots"].reset_index(drop=True),
    )
    assert full["trade_log"]["execution_date"].tolist() == measured["trade_log"]["execution_date"].tolist()


def test_unchanged_selection_resizes_down_when_regime_scale_changes() -> None:
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    prices = _price_data(dates, {"A": [(10.0, 10.0)] * len(dates)})
    factors = _factors(list(dates), ["A"])
    config = _config(COMMISSION_RATE=0.01, TAX_RATE=0.005, SLIPPAGE=0.01)
    result = PortfolioRebalanceEngine(config).run(
        prices,
        pd.DataFrame(),
        factors,
        pd.DataFrame([
            {"as_of": dates[0], "ticker": "A"},
            {"as_of": dates[2], "ticker": "A"},
        ]),
        regime_scale_map={dates[0]: 1.0, dates[2]: 0.5},
    )
    trades = result["trade_log"]
    buys = trades[trades["exit_date"].isna()]
    sells = trades[trades["sell_price"].notna()]
    assert len(buys) == 1
    assert len(sells) == 1
    assert sells.iloc[0]["exit_reason"] == "rebalance"
    assert sells.iloc[0]["execution_date"] == dates[3]
    assert sells.iloc[0]["shares"] > 0
    assert result["portfolio_snapshots"]["cash"].min() >= 0.0


def test_nonzero_costs_make_resize_order_independent() -> None:
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    prices = _price_data(
        dates,
        {
            "A": [(10.0, 10.0)] * len(dates),
            "B": [(20.0, 20.0)] * len(dates),
        },
    )
    factors = _factors(list(dates), ["A", "B"])
    config = _config(
        TOP_N=2,
        COMMISSION_RATE=0.01,
        TAX_RATE=0.005,
        SLIPPAGE=0.01,
    )
    universe = pd.DataFrame([
        {"as_of": dates[0], "ticker": "A"},
        {"as_of": dates[0], "ticker": "B"},
    ])
    first = PortfolioRebalanceEngine(config).run(
        prices, pd.DataFrame(), factors, universe,
    )
    second = PortfolioRebalanceEngine(config).run(
        prices, pd.DataFrame(), factors.iloc[::-1].reset_index(drop=True), universe,
    )
    pd.testing.assert_series_equal(
        first["portfolio_snapshots"]["cash"],
        second["portfolio_snapshots"]["cash"],
        check_names=False,
    )
    assert first["portfolio_snapshots"]["cash"].min() >= 0.0
    assert first["trade_log"]["ticker"].tolist() == second["trade_log"]["ticker"].tolist()
    assert first["trade_log"]["shares"].tolist() == second["trade_log"]["shares"].tolist()


def test_stop_loss_does_not_rebuy_on_same_execution_date() -> None:
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    prices = _price_data(
        dates,
        {"A": [(10.0, 10.0), (10.0, 10.0), (5.0, 8.0), (5.0, 5.0)]},
    )
    result = _run(
        prices,
        [{"as_of": dates[0], "ticker": "A"}, {"as_of": dates[2], "ticker": "A"}],
        ["A"],
    )
    trades = result["trade_log"]
    assert len(trades) == 2
    assert trades.iloc[-1]["exit_reason"] == "stop_loss"
    assert trades.iloc[-1]["execution_date"] == dates[3]
    assert not (trades["exit_date"].isna() & (trades["execution_date"] == dates[3])).any()


def test_missing_next_open_skips_new_order_without_inventing_price() -> None:
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    # B has no bar on the next open; the documented policy is a no-fill.
    prices = _price_data(dates, {"A": [(10.0, 10.0)] * len(dates)})
    factors = _factors(list(dates), ["B"])
    result = PortfolioRebalanceEngine(_config()).run(
        prices,
        pd.DataFrame(),
        factors,
        pd.DataFrame([{"as_of": dates[0], "ticker": "B"}]),
    )
    assert result["trade_log"].empty
    assert result["portfolio_snapshots"]["cash"].eq(1_000.0).all()


def test_exact_buy_and_partial_resize_cost_fields_reconcile() -> None:
    dates = pd.date_range("2024-02-01", periods=5, freq="B")
    prices = _price_data(dates, {"A": [(10.0, 10.0)] * len(dates)})
    config = _config(
        MAX_POSITION_WEIGHT=1.0,
        COMMISSION_RATE=0.01,
        SLIPPAGE=0.02,
        TAX_RATE=0.03,
    )
    result = _run_with_config(
        config,
        prices,
        [{"as_of": dates[0], "ticker": "A"}, {"as_of": dates[2], "ticker": "A"}],
        ["A"],
        regime_scale_map={dates[0]: 1.0, dates[2]: 0.5},
    )

    trades = result["trade_log"]
    buy = trades[trades["sell_price"].isna()].iloc[0]
    sell = trades[trades["sell_price"].notna()].iloc[0]
    assert buy["shares"] == 92
    assert buy["entry_notional"] == pytest.approx(920.0)
    assert buy["entry_commission"] == pytest.approx(9.2)
    assert buy["entry_slippage"] == pytest.approx(18.4)
    assert buy["total_cost"] == pytest.approx(27.6)
    assert sell["shares"] == 44
    assert sell["exit_notional"] == pytest.approx(440.0)
    assert sell["exit_commission"] == pytest.approx(4.4)
    assert sell["exit_slippage"] == pytest.approx(8.8)
    assert sell["exit_tax"] == pytest.approx(13.2)
    assert sell["total_cost"] == pytest.approx(26.4)

    attribution = compute_cost_attribution(
        trades,
        snapshots=result["portfolio_snapshots"],
        initial_capital=1_000.0,
    )
    assert attribution["commission"] == pytest.approx(13.6)
    assert attribution["slippage"] == pytest.approx(27.2)
    assert attribution["tax"] == pytest.approx(13.2)
    assert attribution["total_cost"] == pytest.approx(54.0)
    assert result["execution_stats"]["total_cost"] == pytest.approx(54.0)
    assert result["portfolio_snapshots"]["cumulative_cost"].iloc[-1] == pytest.approx(54.0)
    assert trades["total_cost"].sum() == pytest.approx(54.0)


def test_stop_loss_cost_fields_and_total() -> None:
    dates = pd.date_range("2024-03-01", periods=4, freq="B")
    prices = _price_data(
        dates,
        {"A": [(10.0, 10.0), (10.0, 10.0), (8.0, 8.0), (7.0, 7.0)]},
    )
    config = _config(
        MAX_POSITION_WEIGHT=1.0,
        COMMISSION_RATE=0.01,
        SLIPPAGE=0.02,
        TAX_RATE=0.03,
    )
    result = _run_with_config(
        config,
        prices,
        [{"as_of": dates[0], "ticker": "A"}],
        ["A"],
    )

    stop = result["trade_log"].query("exit_reason == 'stop_loss'").iloc[0]
    assert stop["shares"] == 92
    assert stop["exit_notional"] == pytest.approx(644.0)
    assert stop["exit_commission"] == pytest.approx(6.44)
    assert stop["exit_slippage"] == pytest.approx(12.88)
    assert stop["exit_tax"] == pytest.approx(19.32)
    assert stop["total_cost"] == pytest.approx(38.64)
    assert result["execution_stats"]["total_cost"] == pytest.approx(66.24)
    assert result["portfolio_snapshots"]["cumulative_cost"].iloc[-1] == pytest.approx(66.24)


def test_benchmark_is_available_with_regime_disabled_and_preserves_source() -> None:
    dates = pd.date_range("2024-04-01", periods=4, freq="B")
    prices = _price_data(dates, {"A": [(10.0, 10.0)] * len(dates)})
    index = pd.DataFrame(
        {"close": [100.0, 105.0, 110.0, 120.0]},
        index=dates,
    )
    config = _config(REGIME_FILTER_ENABLED=False, MARKET_INDEX_TICKER="KPI200")
    result = _run_with_config(
        config,
        prices,
        [{"as_of": dates[0], "ticker": "A"}],
        ["A"],
        index_data=index,
    )
    assert result["benchmark"]["available"] is True
    assert result["benchmark"]["source"] == "KPI200"
    assert result["benchmark"]["source_ticker"] == "KPI200"
    assert result["benchmark"]["is_kpi200"] is True
    assert result["benchmark"]["type"] == "price_return"

    custom_config = config.model_copy(update={"MARKET_INDEX_TICKER": "KS11"})
    custom = _run_with_config(
        custom_config,
        prices,
        [{"as_of": dates[0], "ticker": "A"}],
        ["A"],
        index_data=index,
    )
    assert custom["benchmark"]["source"] == "KS11"
    assert custom["benchmark"]["benchmark_source"] == "KS11"
    assert custom["benchmark"]["is_kpi200"] is False
    assert "KPI200" not in custom["benchmark"]["description"]
    manifest = _build_run_manifest(custom_config, {"benchmark": custom["benchmark"]})
    assert manifest["benchmark"]["source"] == "KS11"
    assert manifest["benchmark"]["source_type"] == "configured_market_index"


def test_benchmark_clipping_excludes_observations_outside_measured_range() -> None:
    dates = pd.date_range("2024-05-01", periods=5, freq="B")
    index = pd.DataFrame(
        {"close": [90.0, 100.0, 110.0, 121.0, 150.0]},
        index=dates,
    )
    returns = build_price_return_benchmark(
        index,
        measured_start=dates[1],
        measured_end=dates[3],
    )
    assert returns.index.tolist() == [dates[2], dates[3]]
    assert returns.tolist() == pytest.approx([0.10, 0.10])


def test_engine_infers_benchmark_bounds_from_measured_price_dates() -> None:
    price_dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    prices = _price_data(price_dates, {"A": [(10.0, 10.0)] * len(price_dates)})
    index_dates = pd.date_range("2024-01-01", periods=5, freq="D")
    index = pd.DataFrame(
        {"close": [90.0, 100.0, 110.0, 121.0, 150.0]},
        index=index_dates,
    )

    result = _run_with_config(
        _config(REGIME_FILTER_ENABLED=False),
        prices,
        [{"as_of": price_dates[0], "ticker": "A"}],
        ["A"],
        index_data=index,
    )

    benchmark = result["benchmark_returns"]
    assert benchmark.index.tolist() == [price_dates[1], price_dates[2]]
    assert benchmark.tolist() == pytest.approx([0.10, 0.10])
    assert pd.Timestamp("2024-01-01") not in benchmark.index
    assert pd.Timestamp("2024-01-05") not in benchmark.index


def test_empty_cost_attribution_consumes_snapshot_cost_and_is_safe() -> None:
    snapshots = pd.DataFrame({
        "cumulative_cost": [0.0, 12.5],
        "executed_turnover": [0.0, 250.0],
    })
    attribution = compute_cost_attribution(
        pd.DataFrame(), snapshots=snapshots, initial_capital=1_000.0,
    )
    assert attribution["total_cost"] == pytest.approx(12.5)
    assert attribution["cost_fraction_initial_capital"] == pytest.approx(0.0125)
    assert attribution["total_turnover"] == pytest.approx(250.0)
    assert compute_cost_attribution()["total_cost"] == 0.0
