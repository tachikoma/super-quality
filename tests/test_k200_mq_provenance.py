"""Bounded PIT/provenance contract tests for K200MQ."""

from __future__ import annotations

import json
import sys
import types
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from k200_mq import main as main_module
from k200_mq.config import K200MQConfig
from k200_mq.data import universe as universe_module
from k200_mq.data.provenance import (
    NON_PIT_FINANCIAL_MODE,
    is_pit_valid_financial_data,
    validate_financial_provenance,
)


def test_strict_pit_defaults_false_and_cli_can_enable_it() -> None:
    parser = main_module._build_parser()
    default_args = parser.parse_args(["run"])
    strict_args = parser.parse_args(["run", "--strict-pit"])

    assert K200MQConfig().STRICT_PIT_VALIDATION is False
    assert main_module._build_config(default_args).STRICT_PIT_VALIDATION is False
    assert main_module._build_config(strict_args).STRICT_PIT_VALIDATION is True


def test_strict_pit_env_setting_is_not_overridden_when_cli_flag_is_absent(monkeypatch) -> None:
    monkeypatch.setenv("STRICT_PIT_VALIDATION", "true")
    parser = main_module._build_parser()
    args = parser.parse_args(["run"])

    assert not hasattr(args, "strict_pit")
    assert main_module._build_config(args).STRICT_PIT_VALIDATION is True


def test_env_strict_pit_and_top_n_are_preserved_without_cli_flags(monkeypatch) -> None:
    monkeypatch.setenv("STRICT_PIT_VALIDATION", "true")
    monkeypatch.setenv("EXCLUDE_KOSPI_TOP_N", "0")
    args = main_module._build_parser().parse_args(["run"])

    config = main_module._build_config(args)

    assert config.STRICT_PIT_VALIDATION is True
    assert config.EXCLUDE_KOSPI_TOP_N == 0

    # There is no --no-strict-pit flag.  A false Namespace value must not be
    # treated as an explicit override either.
    args.strict_pit = False
    assert main_module._build_config(args).STRICT_PIT_VALIDATION is True


def test_fdr_current_listing_is_explicitly_non_pit(monkeypatch) -> None:
    fdr_module = types.ModuleType("FinanceDataReader")
    fdr_module.StockListing = lambda name: pd.DataFrame({"Symbol": ["005930"]})
    monkeypatch.setitem(sys.modules, "FinanceDataReader", fdr_module)

    tickers, provenance = universe_module._fetch_kospi200_with_provenance(date(2019, 1, 1))

    assert tickers == ["005930"]
    assert provenance == "proxy_current"


def test_mcap_fallback_is_explicitly_non_pit(monkeypatch) -> None:
    fdr_module = types.ModuleType("FinanceDataReader")

    def unavailable_listing(name):
        raise RuntimeError("offline")

    fdr_module.StockListing = unavailable_listing
    monkeypatch.setitem(sys.modules, "FinanceDataReader", fdr_module)
    monkeypatch.setattr(
        universe_module,
        "_get_kospi200_by_mcap",
        lambda as_of: ["000660"],
    )

    tickers, provenance = universe_module._fetch_kospi200_with_provenance(date(2019, 1, 1))

    assert tickers == ["000660"]
    assert provenance == "mcap_proxy"


def test_legacy_cache_without_structured_provenance_is_unknown(monkeypatch) -> None:
    class FakeCache:
        def get(self, key):
            if key == "kospi200_2019-01-01":
                return pd.DataFrame({"ticker": ["005930"]})
            return None

        def get_json(self, key):
            if key == "kospi200_provenance":
                return {"kospi200_2019-01-01": "proxy_current"}
            return None

    monkeypatch.setattr(universe_module, "_CACHE", FakeCache())
    monkeypatch.setattr(universe_module, "_ensure_cache_dir", lambda: None)

    constituents = universe_module.get_kospi200_constituents(date(2019, 1, 1))

    assert constituents.provenance == "legacy_proxy_unknown"


def test_pit_label_requires_verified_acquisition_manifest_and_matching_fingerprint() -> None:
    tickers = ["A", "B"]
    as_of = date(2024, 1, 31)
    trusted = universe_module._provenance_metadata(
        "pit", as_of, tickers, "KRX historical constituent file"
    )

    assert universe_module._classify_cached_provenance(
        trusted, as_of, tickers,
    )[0] == "legacy_proxy_unknown"
    assert universe_module._classify_cached_provenance(
        "pit", as_of, tickers,
    )[0] == "legacy_proxy_unknown"

    history = pd.DataFrame({"as_of": [as_of], "ticker": tickers[:1]})
    history.attrs["provenance_by_as_of"] = {as_of.isoformat(): "pit"}
    validation = universe_module.validate_universe_provenance(history)
    assert validation["provenance"] == "legacy_proxy_unknown"
    assert validation["pit_valid"] is False

    history.attrs["provenance_metadata_by_as_of"] = {
        as_of.isoformat(): universe_module._provenance_metadata(
            "pit", as_of, tickers[:1], "KRX historical constituent file"
        ),
    }
    validation = universe_module.validate_universe_provenance(history)
    assert validation["provenance"] == "legacy_proxy_unknown"
    assert validation["pit_valid"] is False


def test_pit_provenance_requires_every_history_date() -> None:
    history = pd.DataFrame({
        "as_of": [date(2024, 1, 31), date(2024, 2, 29)],
        "ticker": ["A", "A"],
    })
    history.attrs["provenance_by_as_of"] = {"2024-01-31": "pit"}
    history.attrs["provenance_metadata_by_as_of"] = {
        "2024-01-31": universe_module._provenance_metadata(
            "pit", date(2024, 1, 31), ["A"], "KRX historical constituent file",
        ),
    }

    validation = universe_module.validate_universe_provenance(history)

    assert validation["pit_valid"] is False
    assert validation["provenance_by_as_of"]["2024-02-29"] == "legacy_proxy_unknown"


def test_aggregate_history_pit_metadata_is_rejected() -> None:
    as_of = date(2024, 1, 31)
    history = pd.DataFrame({"as_of": [as_of], "ticker": ["A"]})
    history.attrs["provenance_by_as_of"] = {"history": "pit"}
    history.attrs["provenance_metadata_by_as_of"] = {
        "history": universe_module._provenance_metadata(
            "pit", as_of, ["A"], "KRX historical constituent file",
        ),
    }

    validation = universe_module.validate_universe_provenance(history)

    assert validation["pit_valid"] is False
    assert validation["provenance"] == "legacy_proxy_unknown"


@pytest.mark.parametrize(
    ("effective_date", "expected_pit"),
    [
        (date(2024, 1, 30), True),
        (date(2024, 1, 31), True),
        (date(2024, 2, 1), False),
    ],
)
def test_pit_effective_date_only_passes_when_not_after_as_of(
    effective_date: date,
    expected_pit: bool,
) -> None:
    tickers = ["A", "B"]
    rebalance_date = date(2024, 1, 31)
    metadata = universe_module._provenance_metadata(
        "pit", effective_date, tickers, "KRX historical constituent file",
    )

    expected = "legacy_proxy_unknown"
    assert universe_module._classify_cached_provenance(metadata, rebalance_date, tickers)[0] == expected


def test_history_preserves_legacy_columns_and_reports_proxy_provenance(monkeypatch) -> None:
    monkeypatch.setattr(
        universe_module,
        "get_kospi200_constituents",
        lambda as_of: universe_module.ConstituentList(["A"], "proxy_current"),
    )

    history = universe_module.get_kospi200_history(
        date(2024, 1, 1), date(2024, 1, 31), "M",
    )
    validation = universe_module.validate_universe_provenance(history)

    assert list(history.columns) == ["as_of", "ticker"]
    assert history.attrs["provenance"] == "proxy_current"
    assert validation["pit_valid"] is False


def test_missing_filing_date_cannot_claim_financial_pit_validity() -> None:
    fiscal_data = pd.DataFrame({"ticker": ["A"], "year": [2024], "quarter": [1]})

    result = validate_financial_provenance(fiscal_data, filing_date_used=True)

    assert result["mode"] == NON_PIT_FINANCIAL_MODE
    assert result["pit_valid"] is False
    assert not is_pit_valid_financial_data(fiscal_data)


def _filing_timestamp_contract(field: str = "filing_timestamp") -> dict[str, object]:
    return {
        "source": "test raw filing source",
        "source_timezone": "Asia/Seoul",
        "cutoff_time": "15:30",
        "schema": {
            field: {
                "type": "timestamp",
                "role": "filing availability timestamp",
            },
        },
    }


@pytest.mark.parametrize(
    "filing_value",
    [
        "2024-05-15T09:00:00+09:00",
        datetime(2024, 5, 15, 9, 0, tzinfo=timezone(timedelta(hours=9))),
        pd.Timestamp("2024-05-15 09:00:00", tz="Asia/Seoul"),
    ],
)
def test_explicit_filing_timestamp_contract_is_used_as_availability_date(
    filing_value,
) -> None:
    financial_data = pd.DataFrame({
        "ticker": ["A"],
        "year": [2024],
        "quarter": [1],
        "filing_timestamp": [filing_value],
        "revenue": [100.0],
        "cogs": [40.0],
        "net_income": [10.0],
        "operating_cf": [12.0],
        "total_assets": [200.0],
        "total_equity": [100.0],
    })
    financial_data.attrs["financial_provenance_contract"] = _filing_timestamp_contract()
    dates = pd.date_range("2024-03-29", "2024-05-17", freq="B")

    daily = main_module._convert_financial_to_daily(financial_data, dates)

    assert daily.attrs["financial_provenance"]["pit_valid"] is True
    assert daily.attrs["financial_provenance"]["mode"] == "pit_filing_date"
    assert daily.loc[daily["date"] == pd.Timestamp("2024-05-15"), "revenue"].iloc[0] == 100.0
    assert daily.loc[daily["date"] == pd.Timestamp("2024-05-14"), "revenue"].iloc[0] == 0.0


def test_after_close_filing_maps_to_next_exchange_session() -> None:
    financial_data = pd.DataFrame({
        "ticker": ["A"],
        "filing_timestamp": ["2024-05-15T18:00:00+09:00"],
        "revenue": [100.0],
    })
    financial_data.attrs["financial_provenance_contract"] = _filing_timestamp_contract()
    dates = pd.date_range("2024-05-15", "2024-05-17", freq="B")

    daily = main_module._convert_financial_to_daily(financial_data, dates)

    assert daily.attrs["financial_provenance"]["pit_valid"] is True
    assert daily.loc[daily["date"] == pd.Timestamp("2024-05-15"), "revenue"].iloc[0] == 0.0
    assert daily.loc[daily["date"] == pd.Timestamp("2024-05-16"), "revenue"].iloc[0] == 100.0


@pytest.mark.parametrize(
    "filing_value",
    [
        datetime(2024, 5, 15, 9, 0),
        datetime(2024, 11, 3, 1, 30, tzinfo=ZoneInfo("America/New_York")),
    ],
)
def test_timezone_naive_or_ambiguous_timestamp_is_not_pit_without_next_session_policy(
    filing_value,
) -> None:
    financial_data = pd.DataFrame({
        "ticker": ["A"],
        "filing_timestamp": [filing_value],
    })
    financial_data.attrs["financial_provenance_contract"] = _filing_timestamp_contract()

    result = validate_financial_provenance(financial_data, filing_date_used=True)

    assert result["pit_valid"] is False
    assert result["timezone_safe"] is False


def test_next_session_policy_allows_naive_timestamp_and_moves_forward() -> None:
    financial_data = pd.DataFrame({
        "ticker": ["A"],
        "filing_timestamp": ["2024-05-15 09:00:00"],
        "revenue": [100.0],
    })
    financial_data.attrs["financial_provenance_contract"] = {
        **_filing_timestamp_contract(),
        "availability_policy": "next_session",
    }
    dates = pd.date_range("2024-05-15", "2024-05-17", freq="B")

    daily = main_module._convert_financial_to_daily(financial_data, dates)

    assert daily.attrs["financial_provenance"]["pit_valid"] is True
    assert daily.loc[daily["date"] == pd.Timestamp("2024-05-15"), "revenue"].iloc[0] == 0.0
    assert daily.loc[daily["date"] == pd.Timestamp("2024-05-16"), "revenue"].iloc[0] == 100.0


def test_date_only_financial_availability_is_not_pit() -> None:
    financial_data = pd.DataFrame({
        "ticker": ["A"],
        "year": [2024],
        "quarter": [1],
        "filing_date": ["2024-05-15"],
    })

    result = validate_financial_provenance(financial_data, filing_date_used=True)

    assert result["mode"] == NON_PIT_FINANCIAL_MODE
    assert result["pit_valid"] is False
    with pytest.raises(RuntimeError, match="financial quality mode"):
        main_module._enforce_strict_pit_validation(
            {"provenance": "pit", "pit_valid": True}, result,
        )


def test_date_only_filing_timestamp_requires_next_session_policy() -> None:
    financial_data = pd.DataFrame({
        "ticker": ["A"],
        "filing_timestamp": ["2024-05-15"],
    })
    financial_data.attrs["financial_provenance_contract"] = _filing_timestamp_contract()

    result = validate_financial_provenance(financial_data, filing_date_used=True)

    assert result["source_schema_contract"] is True
    assert result["meaningful_timestamp"] is False
    assert result["pit_valid"] is False

    financial_data.attrs["financial_provenance_contract"] = {
        **_filing_timestamp_contract(),
        "availability_policy": "next_session",
    }
    result = validate_financial_provenance(financial_data, filing_date_used=True)

    assert result["pit_valid"] is True
    assert result["availability_policy"] == "next_session"


def test_report_date_is_non_pit_without_explicit_filing_semantics() -> None:
    financial_data = pd.DataFrame({"ticker": ["A"], "report_date": ["2024-05-15T09:00:00"]})

    result = validate_financial_provenance(financial_data, filing_date_used=True)

    assert result["pit_valid"] is False


def test_report_date_can_be_pit_only_when_contract_declares_filing_semantics() -> None:
    financial_data = pd.DataFrame({
        "ticker": ["A"],
        "report_date": ["2024-05-15T09:00:00+09:00"],
    })
    financial_data.attrs["financial_provenance_contract"] = _filing_timestamp_contract(
        "report_date",
    )

    result = validate_financial_provenance(financial_data, filing_date_used=True)

    assert result["pit_valid"] is True


def test_effective_date_is_not_a_financial_filing_field() -> None:
    financial_data = pd.DataFrame({"ticker": ["A"], "effective_date": ["2024-05-15"]})

    result = validate_financial_provenance(financial_data, filing_date_used=True)

    assert result["filing_date_field"] is None
    assert result["pit_valid"] is False


def test_manifest_downgrades_incomplete_financial_pit_claim(tmp_path) -> None:
    config = K200MQConfig(OUTPUT_DIR=str(tmp_path))
    manifest = main_module._build_run_manifest(config, {
        "financial_provenance": {
            "mode": "pit_filing_date",
            "pit_valid": True,
            "filing_date_field": "filing_timestamp",
        },
    })

    assert manifest["quality"]["financial_data_mode"] == NON_PIT_FINANCIAL_MODE
    assert manifest["data_validity"]["financials"]["pit_valid"] is False


def test_manifest_rejects_unvalidated_universe_pit_claim(tmp_path) -> None:
    config = K200MQConfig(OUTPUT_DIR=str(tmp_path))
    manifest = main_module._build_run_manifest(config, {
        "universe": {
            "dates": ["2024-01-31"],
            "date_count": 1,
            "ticker_count": 1,
        },
        "universe_provenance": {
            "provenance": "pit",
            "provenance_by_as_of": {"2024-01-31": "pit"},
            "pit_valid": True,
        },
    })

    assert manifest["universe"]["provenance"] == "legacy_proxy_unknown"
    assert manifest["universe"]["pit_valid"] is False


def test_strict_validation_rejects_current_proxy_and_non_pit_financials() -> None:
    with pytest.raises(RuntimeError, match="universe provenance"):
        main_module._enforce_strict_pit_validation({
            "provenance": "proxy_current",
            "pit_valid": False,
        })

    with pytest.raises(RuntimeError, match="financial quality mode"):
        main_module._enforce_strict_pit_validation(
            {"provenance": "pit", "pit_valid": True},
            {"mode": NON_PIT_FINANCIAL_MODE, "pit_valid": False},
        )


def test_strict_validation_rejects_current_market_cap_top_n() -> None:
    config = K200MQConfig(STRICT_PIT_VALIDATION=True, EXCLUDE_KOSPI_TOP_N=50)

    with pytest.raises(RuntimeError, match="EXCLUDE_KOSPI_TOP_N=0"):
        main_module._enforce_strict_pit_validation(
            {"provenance": "pit", "pit_valid": True}, config=config,
        )

    with pytest.raises(RuntimeError, match="EXCLUDE_KOSPI_TOP_N=0"):
        universe_module.exclude_kospi_top_n(["A"], strict_pit=True)


def test_non_strict_manifest_labels_proxy_and_fiscal_financial_mode(tmp_path) -> None:
    config = K200MQConfig(OUTPUT_DIR=str(tmp_path), STRICT_PIT_VALIDATION=False)
    manifest = main_module._build_run_manifest(config, {
        "universe": {"dates": [], "date_count": 0, "ticker_count": 1},
        "universe_provenance": {
            "provenance": "mcap_proxy",
            "provenance_by_as_of": {"2024-01-31": "mcap_proxy"},
            "pit_valid": False,
        },
        "financial_provenance": {
            "mode": NON_PIT_FINANCIAL_MODE,
            "pit_valid": False,
        },
    })

    assert manifest["universe"]["provenance"] == "mcap_proxy"
    assert manifest["universe"]["pit_valid"] is False
    assert manifest["quality"]["financial_data_mode"] == NON_PIT_FINANCIAL_MODE
    assert manifest["data_validity"]["strict_pit_validation"] is False
    json.dumps(manifest)


def test_manifest_calls_untrusted_legacy_cache_unknown(tmp_path) -> None:
    config = K200MQConfig(OUTPUT_DIR=str(tmp_path), STRICT_PIT_VALIDATION=False)
    manifest = main_module._build_run_manifest(config, {
        "universe_provenance": {
            "provenance": "legacy_proxy_unknown",
            "provenance_by_as_of": {"2024-01-31": "legacy_proxy_unknown"},
            "pit_valid": False,
        },
    })

    assert "legacy_proxy_unknown" in manifest["limitations"]["universe"]
