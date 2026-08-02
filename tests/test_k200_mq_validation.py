"""Tests for K200MQ independent subperiod robustness reporting."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from k200_mq import main as main_module
from k200_mq.core.analysis.metrics import PerformanceMetrics
from k200_mq.main import (
    _aggregate_subperiod_metrics,
    _build_parser,
    _print_fold_metrics,
    _subperiods,
)


def test_subperiods_are_fixed_and_last_period_is_as_of() -> None:
    periods = _subperiods(date(2026, 8, 2))

    assert periods == [
        ("2014-01-01", "2016-12-31", "Subperiod 1: 2014-2016"),
        ("2017-01-01", "2018-12-31", "Subperiod 2: 2017-2018"),
        ("2019-01-01", "2020-12-31", "Subperiod 3: 2019-2020"),
        ("2021-01-01", "2022-12-31", "Subperiod 4: 2021-2022"),
        ("2023-01-01", "2026-08-02", "Subperiod 5: 2023-2026"),
    ]


def test_aggregation_uses_geometric_mean_and_reports_worst_mdd() -> None:
    metrics = [
        {
            "valid": True,
            "total_return": 0.10,
            "sharpe": 0.50,
            "max_dd": -0.10,
            "win_rate": 50.0,
            "n_trades": 2,
        },
        {
            "valid": True,
            "total_return": 0.20,
            "sharpe": 0.75,
            "max_dd": -0.25,
            "win_rate": 60.0,
            "n_trades": 3,
        },
    ]

    aggregate = _aggregate_subperiod_metrics(metrics)

    expected_geo_mean = (1.10 * 1.20) ** (1 / 2) - 1
    assert aggregate["geo_mean_return"] == expected_geo_mean
    assert aggregate["worst_mdd"] == -0.25
    assert aggregate["mean_mdd"] == (-0.10 - 0.25) / 2
    assert aggregate["valid_folds"] == 2


def test_empty_and_invalid_folds_are_explicitly_invalid(capsys) -> None:
    empty = _print_fold_metrics("Subperiod 1", pd.Series(dtype=float), pd.DataFrame())
    invalid = _print_fold_metrics(
        "Subperiod 2",
        pd.Series([0.01, np.nan]),
        pd.DataFrame(),
    )

    assert empty["valid"] is False
    assert empty["status"] == "invalid"
    assert np.isnan(empty["total_return"])
    assert invalid["valid"] is False
    assert "missing" in invalid["reason"]
    assert "INVALID" in capsys.readouterr().out

    aggregate = _aggregate_subperiod_metrics([empty, invalid])
    assert aggregate["valid_folds"] == 0
    assert aggregate["invalid_folds"] == 2
    assert aggregate["geo_mean_return"] is None


def test_fold_cagr_and_sharpe_match_performance_metrics() -> None:
    returns = pd.Series(
        [0.01, -0.005, 0.002, 0.004],
        index=pd.bdate_range("2024-01-02", periods=4),
    )

    fold = _print_fold_metrics("Subperiod 1", returns, pd.DataFrame())
    expected = PerformanceMetrics(returns).compute_all()

    assert fold["cagr"] == expected["cagr"]
    assert fold["sharpe"] == expected["sharpe_ratio"]
    assert fold["max_dd"] == expected["max_drawdown"]


def test_robustness_terminology_and_walkforward_alias() -> None:
    parser = _build_parser()
    help_text = parser.format_help().lower()

    assert "subperiod robustness" in help_text
    assert parser.parse_args(["robustness"]).command == "robustness"
    assert parser.parse_args(["walkforward"]).command == "walkforward"


def test_robustness_does_not_reuse_stale_artifacts_after_empty_pipeline(
    monkeypatch, tmp_path,
) -> None:
    config = main_module._build_config(
        main_module._build_parser().parse_args(["robustness", "--output", str(tmp_path)])
    )
    period = ("2024-01-01", "2024-12-31", "Subperiod 1: 2024")
    fold_output = tmp_path / "subperiod_robustness" / "subperiod_1"
    fold_output.mkdir(parents=True)
    pd.DataFrame({
        "date": ["2024-01-02"],
        "daily_return": [0.25],
    }).to_csv(fold_output / "daily_returns.csv", index=False)
    pd.DataFrame({
        "return_pct": [0.50],
    }).to_csv(fold_output / "trade_log.csv", index=False)

    monkeypatch.setattr(main_module, "_subperiods", lambda: [period])
    monkeypatch.setattr(main_module, "_run_pipeline", lambda fold_config: None)

    main_module._run_subperiod_robustness(config)

    summary = pd.read_csv(tmp_path / "subperiod_robustness_summary.csv")
    assert summary.loc[0, "status"] == "invalid"
    assert bool(summary.loc[0, "valid"]) is False
    assert summary.loc[0, "n_trades"] == 0
    assert "missing" in summary.loc[0, "reason"]


def test_robustness_suppresses_intermediate_summary(monkeypatch, tmp_path, capsys) -> None:
    config = main_module._build_config(
        main_module._build_parser().parse_args(["robustness", "--output", str(tmp_path)])
    )
    period = ("2024-01-01", "2024-12-31", "Subperiod 1: 2024")
    returns = pd.Series(
        [0.01, -0.005, 0.002, 0.004],
        index=pd.bdate_range("2024-01-02", periods=4),
    )
    summary_calls: list[object] = []

    monkeypatch.setattr(main_module, "_subperiods", lambda: [period])
    monkeypatch.setattr(
        main_module,
        "_run_pipeline",
        lambda fold_config: {
            "daily_returns": returns,
            "trade_log": pd.DataFrame(),
        },
    )
    monkeypatch.setattr(
        main_module,
        "_print_summary",
        lambda results, fold_config: summary_calls.append(results),
    )

    main_module._run_subperiod_robustness(config)

    output = capsys.readouterr().out
    assert summary_calls == []
    assert "KOSPI 200 Momentum + Quality — 백테스트 결과" not in output
    assert "Sharpe: PerformanceMetrics 정의" in output
