"""Tests for the CLI wiring around the mechanical true walk-forward run."""

from __future__ import annotations

from datetime import date
import json

import numpy as np
import pandas as pd
import pytest

from k200_mq import main as main_module
from k200_mq.config import K200MQConfig
from k200_mq.validation.prepared import PreparedK200MQInputs
from k200_mq.validation.runner import WalkForwardResult


def _prepared(output_dir: str, *, strict: bool = False) -> PreparedK200MQInputs:
    config = K200MQConfig(
        OUTPUT_DIR=output_dir,
        EXCLUDE_KOSPI_TOP_N=0,
        REGIME_FILTER_ENABLED=False,
        STRICT_PIT_VALIDATION=strict,
    )
    return PreparedK200MQInputs(
        price_data=pd.DataFrame(),
        factor_data=pd.DataFrame(),
        index_data=pd.DataFrame(),
        universe_history=pd.DataFrame(),
        measured_start=date(2015, 1, 1),
        measured_end=date(2024, 12, 31),
        warmup_start=date(2014, 1, 1),
        warmup_end=date(2014, 12, 31),
        active_trading_start=date(2015, 1, 1),
        runtime_config=config,
    )


def _engine_result(start: date) -> dict[str, object]:
    dates = pd.bdate_range(start, periods=6)
    returns = pd.Series([0.01, -0.005, 0.002, 0.004, 0.003, -0.001], index=dates)
    trade_log = pd.DataFrame({"return_pct": np.full(5, 0.01)})
    return {
        "daily_returns": returns,
        "trade_log": trade_log,
        "portfolio_snapshots": pd.DataFrame({"date": dates, "nav": 100.0}),
    }


def test_interval_metric_extraction_uses_performance_metrics() -> None:
    returns = pd.Series(
        [0.01, -0.005, 0.002, 0.004],
        index=pd.bdate_range("2024-01-02", periods=4),
    )
    result = {
        "daily_returns": returns,
        "trade_log": pd.DataFrame({"return_pct": [0.1, -0.02, 0.03]}),
    }

    evaluation = main_module._evaluate_interval_result(result)
    expected = main_module.PerformanceMetrics(returns).compute_all()

    assert evaluation["valid"] is True
    assert evaluation["status"] == "valid"
    assert evaluation["train_sharpe"] == expected["sharpe_ratio"]
    assert evaluation["n_exits"] == 3
    assert evaluation["metrics"]["cagr"] == expected["cagr"]


def test_interval_metric_extraction_counts_completed_test_trades() -> None:
    returns = pd.Series(
        [0.01, -0.005, 0.002, 0.004],
        index=pd.bdate_range("2024-01-02", periods=4),
    )
    trade_log = pd.DataFrame({
        "entry_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "exit_date": pd.to_datetime(["2024-01-04", "2024-01-05"]),
        "return_pct": [0.10, -0.02],
        "hold_days": [2, 2],
    })

    evaluation = main_module._evaluate_interval_result({
        "daily_returns": returns,
        "trade_log": trade_log,
    })

    assert evaluation["valid"] is True
    assert evaluation["n_exits"] == 2
    assert evaluation["metrics"]["total_trades"] == 2
    assert evaluation["metrics"]["avg_hold_days"] == 2.0


def test_true_walkforward_prepares_once_and_serializes_artifacts(monkeypatch, tmp_path) -> None:
    config = main_module._build_config(
        main_module._build_parser().parse_args(
            ["true-walkforward", "--output", str(tmp_path)]
        )
    )
    prepared = _prepared(str(tmp_path))
    prepare_calls: list[tuple[object, object, object]] = []
    engine_calls: list[tuple[str, date, date]] = []

    def prepare(config_arg, *, overall_start, overall_end, warmup_days):
        prepare_calls.append((overall_start, overall_end, warmup_days))
        return prepared

    def execute(prepared_arg, candidate_config, *, measured_start, measured_end,
                active_trading_start):
        assert prepared_arg is prepared
        assert active_trading_start == prepared.active_trading_start
        engine_calls.append((candidate_config["phase"], measured_start, measured_end))
        return _engine_result(measured_start)

    monkeypatch.setattr(main_module, "prepare_k200mq_inputs", prepare)
    monkeypatch.setattr(main_module, "execute_engine_interval", execute)

    result = main_module._run_true_walkforward(config)

    assert prepare_calls == [(date(2015, 1, 1), date(2024, 12, 31), 252)]
    assert len(engine_calls) == 5 * 16 + 5
    assert all(phase == "train" for phase, _, _ in engine_calls[:80])
    assert all(phase == "test" for phase, _, _ in engine_calls[80:])
    assert result.classification == main_module.MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT

    artifact_dir = tmp_path / "true_walkforward"
    with (artifact_dir / "selection_and_folds.json").open(encoding="utf-8") as selection_file:
        selection = json.load(selection_file)
    assert selection["classification"] == "mechanical_expanding_walk_forward_non_pit"
    assert len(selection["folds"]) == 5
    assert len(selection["folds"][0]["train_scores"]) == 16
    assert len(selection["selected_config_hashes_by_fold"]) == 5
    assert selection["limitations"]
    assert (artifact_dir / "oos_returns.csv").exists()
    summary = pd.read_csv(artifact_dir / "summary.csv")
    assert list(summary["train_start"]) == ["2015-01-01"] * 5
    assert list(summary["test_end"]) == [
        "2020-12-31", "2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31",
    ]
    assert len(pd.read_csv(artifact_dir / "oos_returns.csv")) == 30


def test_true_walkforward_strict_pit_preflight_rejects_unvalidated_bundle(
    monkeypatch,
    tmp_path,
) -> None:
    config = main_module._build_config(
        main_module._build_parser().parse_args(
            ["true-walkforward", "--strict-pit", "--output", str(tmp_path)]
        )
    )
    prepared = _prepared(str(tmp_path))
    monkeypatch.setattr(
        main_module,
        "prepare_k200mq_inputs",
        lambda config_arg, **kwargs: prepared,
    )
    engine_calls: list[object] = []
    monkeypatch.setattr(
        main_module,
        "execute_engine_interval",
        lambda *args, **kwargs: engine_calls.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="strict PIT preflight failed"):
        main_module._run_true_walkforward(config)
    assert engine_calls == []


def test_true_walkforward_strict_pit_runs_when_preflight_is_satisfied(
    monkeypatch,
    tmp_path,
) -> None:
    config = main_module._build_config(
        main_module._build_parser().parse_args(
            ["true-walkforward", "--strict-pit", "--output", str(tmp_path)]
        )
    )
    prepared = _prepared(str(tmp_path), strict=True)
    engine_calls: list[tuple[str, date, date]] = []

    monkeypatch.setattr(
        main_module,
        "prepare_k200mq_inputs",
        lambda config_arg, **kwargs: prepared,
    )
    monkeypatch.setattr(
        main_module,
        "_preflight_true_walkforward_strict_inputs",
        lambda prepared_arg: None,
    )

    def execute(prepared_arg, candidate_config, *, measured_start, measured_end,
                active_trading_start):
        assert prepared_arg is prepared
        assert active_trading_start == prepared.active_trading_start
        engine_calls.append((candidate_config["phase"], measured_start, measured_end))
        return _engine_result(measured_start)

    monkeypatch.setattr(main_module, "execute_engine_interval", execute)

    result = main_module._run_true_walkforward(config)

    assert result.classification == main_module.MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT
    assert len(engine_calls) == 5 * 16 + 5


def _prepared_with_first_ready_coverage() -> PreparedK200MQInputs:
    """A bundle whose first scheduled rebalance has zero six-fact coverage
    while the first *ready* rebalance (the one the engine trades on) has
    73/200 six-fact and 121/200 momentum coverage."""
    return PreparedK200MQInputs(
        price_data=pd.DataFrame(),
        factor_data=pd.DataFrame(),
        index_data=pd.DataFrame(),
        universe_history=pd.DataFrame(),
        measured_start=date(2015, 1, 1),
        measured_end=date(2024, 12, 31),
        coverage={
            "rebalance_readiness": {
                "first_ready_rebalance": {
                    "scheduled_date": "2015-05-29",
                    "usable_ticker_count": 121,
                    "universe_ticker_count": 200,
                },
            },
            "financial_coverage": {
                "records": [
                    {
                        "scheduled_date": "2015-01-30",
                        "six_fact_available_ticker_count": 0,
                        "universe_ticker_count": 200,
                        "six_fact_source_coverage_ratio": 0.0,
                    },
                    {
                        "scheduled_date": "2015-05-29",
                        "six_fact_available_ticker_count": 73,
                        "universe_ticker_count": 200,
                        "six_fact_source_coverage_ratio": 0.365,
                    },
                ],
            },
        },
    )


def test_validated_coverage_summary_uses_first_ready_rebalance_record() -> None:
    summary = main_module._true_walkforward_coverage_summary(
        _prepared_with_first_ready_coverage()
    )

    assert summary is not None
    first = summary["first_rebalance"]
    # records[0] is the first scheduled rebalance (0/200 = 0.0); the matching
    # first-ready record must be used instead.
    assert first["six_fact_ratio"] == pytest.approx(0.365)
    assert first["momentum_ratio"] == pytest.approx(0.605)


def test_validated_coverage_summary_falls_back_to_first_record_on_missing_match() -> None:
    prepared = _prepared_with_first_ready_coverage()
    # No financial record matches this ready date -> conservative records[0].
    prepared = PreparedK200MQInputs(
        price_data=pd.DataFrame(),
        factor_data=pd.DataFrame(),
        index_data=pd.DataFrame(),
        universe_history=pd.DataFrame(),
        coverage={
            "rebalance_readiness": {
                "first_ready_rebalance": {
                    "scheduled_date": "2015-06-30",
                    "usable_ticker_count": 121,
                    "universe_ticker_count": 200,
                },
            },
            "financial_coverage": {
                "records": [
                    {
                        "scheduled_date": "2015-01-30",
                        "six_fact_available_ticker_count": 0,
                        "universe_ticker_count": 200,
                        "six_fact_source_coverage_ratio": 0.0,
                    },
                ],
            },
        },
    )

    summary = main_module._true_walkforward_coverage_summary(prepared)

    assert summary is not None
    assert summary["first_rebalance"]["six_fact_ratio"] == pytest.approx(0.0)
    assert summary["first_rebalance"]["momentum_ratio"] == pytest.approx(0.605)


def _empty_walkforward_result(classification: str) -> WalkForwardResult:
    return WalkForwardResult(
        classification=classification,
        candidate_library_version="k200mq-wf-candidates-v4",
        candidate_config_hashes={},
        folds=(),
        stitched_oos_returns=(),
        valid=True,
        status="valid",
    )


def test_validated_manifest_uses_ready_record_and_drops_mechanical_limitation(
    tmp_path,
) -> None:
    config = K200MQConfig(OUTPUT_DIR=str(tmp_path))
    main_module._save_true_walkforward_artifacts(
        _empty_walkforward_result(
            main_module.VALIDATED_EXPANDING_WALK_FORWARD_PIT
        ),
        _prepared_with_first_ready_coverage(),
        config,
    )

    manifest_path = tmp_path / "true_walkforward" / "selection_and_folds.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["classification"] == "validated_expanding_walk_forward_pit"
    # The mechanical-only disclaimer must not survive a validated manifest.
    assert not any(
        item.startswith("Mechanical non-PIT walk-forward only")
        for item in manifest["limitations"]
    )
    assert any(
        item.startswith("Universe and financial provenance validators passed")
        for item in manifest["limitations"]
    )
    # Measured coverage lines are kept and reflect the first-ready record.
    assert any(
        "six-fact financial coverage 73/200 (36.5%)" in item
        for item in manifest["limitations"]
    )
    assert any(
        "momentum readiness 121/200 (60.5%)" in item
        for item in manifest["limitations"]
    )
    assert manifest["coverage_summary"]["first_rebalance"]["six_fact_ratio"] == (
        pytest.approx(0.365)
    )
    assert manifest["coverage_summary"]["first_rebalance"]["momentum_ratio"] == (
        pytest.approx(0.605)
    )


def test_mechanical_manifest_keeps_mechanical_limitation(tmp_path) -> None:
    config = K200MQConfig(OUTPUT_DIR=str(tmp_path))
    main_module._save_true_walkforward_artifacts(
        _empty_walkforward_result(
            main_module.MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT
        ),
        _prepared(str(tmp_path)),
        config,
    )

    manifest_path = tmp_path / "true_walkforward" / "selection_and_folds.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert any(
        item.startswith("Mechanical non-PIT walk-forward only")
        for item in manifest["limitations"]
    )
    assert "coverage_summary" not in manifest
