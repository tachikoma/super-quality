"""Focused regression tests for the K200MQ Phase 2 execution fixes."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import pytest
from k200_mq.backtest.portfolio_engine import PortfolioRebalanceEngine
from k200_mq.config import K200MQConfig
from k200_mq.core.data import loader
from k200_mq.data import universe as universe_module
from k200_mq.factors.momentum import MomentumFactor
from k200_mq.factors.regime import RegimeFactor
from k200_mq.main import _save_results, _validate_first_rebalance_factor_readiness


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
