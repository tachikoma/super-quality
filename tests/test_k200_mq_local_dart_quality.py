"""Integration tests for the local DART financial path wiring into the quality factor.

These cover the two wiring gaps that previously kept the quality factor
disconnected from local DART files:

1. long-format facts (``prepare_financial_facts`` output) must be pivoted into
   the wide schema ``_convert_financial_to_daily`` and ``QualityFactor.compute``
   expect;
2. the quality factor gate must recognize the local DART source even when
   ``DART_API_KEY`` is empty.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from k200_mq.data.dart_pit import load_financial_facts, load_filing_metadata, pivot_financial_facts_to_wide
from k200_mq.factors.quality import QualityFactor
from k200_mq import main as main_module


def _write_json(tmp_path: Path, name: str, rows: list[dict[str, object]]) -> tuple[Path, Path]:
    source = tmp_path / name
    source.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = source.with_suffix(source.suffix + ".manifest.json")
    endpoint = (
        "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
        if "fact" in name else "https://opendart.fss.or.kr/api/list.json"
    )
    manifest.write_text(json.dumps({
        "response_sha256": digest,
        "source_url": endpoint,
        "request_params": {"fixture_name": name},
        "request_params_sha256": hashlib.sha256(
            json.dumps({"fixture_name": name}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "api_status": "000",
        "pagination": {"complete": True},
        "retrieved_at_utc": "2024-01-03T00:00:00+00:00",
    }), encoding="utf-8")
    return source, manifest


def _fact_rows(corp_code: str = "001", stock_code: str = "005930") -> list[dict[str, object]]:
    return [
        {
            "rcept_no": "R1", "corp_code": corp_code, "bsns_year": "2023",
            "reprt_code": "11011", "fs_div": "CFS", "sj_div": "IS",
            "account_id": "ifrs-full_Revenue", "account_nm": "매출액",
            "account_detail": "", "period_end": "20231231",
            "thstrm_amount": "1,000", "currency": "KRW",
        },
        {
            "rcept_no": "R1", "corp_code": corp_code, "bsns_year": "2023",
            "reprt_code": "11011", "fs_div": "CFS", "sj_div": "IS",
            "account_id": "ifrs-full_CostOfSales", "account_nm": "매출원가",
            "account_detail": "", "period_end": "20231231",
            "thstrm_amount": "400", "currency": "KRW",
        },
        {
            "rcept_no": "R1", "corp_code": corp_code, "bsns_year": "2023",
            "reprt_code": "11011", "fs_div": "CFS", "sj_div": "IS",
            "account_id": "ifrs-full_ProfitLoss", "account_nm": "당기순이익",
            "account_detail": "", "period_end": "20231231",
            "thstrm_amount": "100", "currency": "KRW",
        },
        {
            "rcept_no": "R1", "corp_code": corp_code, "bsns_year": "2023",
            "reprt_code": "11011", "fs_div": "CFS", "sj_div": "CF",
            "account_id": "ifrs-full_CashFlowsFromOperatingActivities",
            "account_nm": "영업활동현금흐름", "account_detail": "", "period_end": "20231231",
            "thstrm_amount": "80", "currency": "KRW",
        },
        {
            "rcept_no": "R1", "corp_code": corp_code, "bsns_year": "2023",
            "reprt_code": "11011", "fs_div": "CFS", "sj_div": "BS",
            "account_id": "ifrs-full_Assets", "account_nm": "자산총계",
            "account_detail": "", "period_end": "20231231",
            "thstrm_amount": "2,000", "currency": "KRW",
        },
        {
            "rcept_no": "R1", "corp_code": corp_code, "bsns_year": "2023",
            "reprt_code": "11011", "fs_div": "CFS", "sj_div": "BS",
            "account_id": "ifrs-full_Equity", "account_nm": "자본총계",
            "account_detail": "", "period_end": "20231231",
            "thstrm_amount": "1,200", "currency": "KRW",
        },
    ]


def _local_dart_sources(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    filing_source, filing_manifest = _write_json(tmp_path, "filings.json", [
        {
            "corp_code": "001", "stock_code": "005930", "corp_name": "Example",
            "rcept_no": "R1", "rcept_dt": "20240102", "report_nm": "사업보고서",
            "pblntf_ty": "A", "pblntf_detail_ty": "B", "rm": "",
        },
        {
            "corp_code": "002", "stock_code": "000660", "corp_name": "Peer",
            "rcept_no": "R2", "rcept_dt": "20240102", "report_nm": "사업보고서",
            "pblntf_ty": "A", "pblntf_detail_ty": "B", "rm": "",
        },
    ])
    peer_facts = [
        {
            "rcept_no": "R2", "corp_code": "002", "bsns_year": "2023",
            "reprt_code": "11011", "fs_div": "CFS", "sj_div": row["sj_div"],
            "account_id": row["account_id"], "account_nm": row["account_nm"],
            "account_detail": "", "period_end": "20231231",
            "thstrm_amount": str(200), "currency": "KRW",
        }
        for row in _fact_rows()
    ]
    fact_source, fact_manifest = _write_json(tmp_path, "facts.json", _fact_rows() + peer_facts)
    return filing_source, filing_manifest, fact_source, fact_manifest


class _FakeConfig:
    DART_API_KEY: str = ""
    LOCAL_DART_FILING_PATH: str = ""
    LOCAL_DART_FILING_MANIFEST: str = ""
    LOCAL_DART_FINANCIAL_PATH: str = ""
    LOCAL_DART_FINANCIAL_MANIFEST: str = ""


def _fake_config(tmp_path: Path) -> _FakeConfig:
    filing_source, filing_manifest, fact_source, fact_manifest = _local_dart_sources(tmp_path)
    config = _FakeConfig()
    config.DART_API_KEY = ""
    config.LOCAL_DART_FILING_PATH = str(filing_source)
    config.LOCAL_DART_FILING_MANIFEST = str(filing_manifest)
    config.LOCAL_DART_FINANCIAL_PATH = str(fact_source)
    config.LOCAL_DART_FINANCIAL_MANIFEST = str(fact_manifest)
    return config


def test_local_dart_source_is_recognized_without_api_key(tmp_path: Path) -> None:
    config = _fake_config(tmp_path)

    assert main_module._local_dart_source_ready(config) is True


def test_load_local_dart_financial_inputs_produces_nonzero_quality_input(tmp_path: Path) -> None:
    config = _fake_config(tmp_path)
    dates = pd.date_range("2024-01-02", "2024-01-31", freq="B")

    financial_data, daily_financial, provenance = main_module._load_local_dart_financial_inputs(
        config, dates,
    )

    assert provenance["pit_valid"] is True
    assert provenance["mode"] == "pit_filing_date"
    assert not daily_financial.empty
    assert (daily_financial["revenue"] > 0).any()
    assert (daily_financial["net_income"] > 0).any()
    primary = daily_financial[daily_financial["ticker"] == "005930"]
    assert not primary.empty
    assert primary["operating_cf"].iloc[-1] == pytest.approx(80.0)


def test_quality_factor_computes_nontrivial_score_from_local_pivot(tmp_path: Path) -> None:
    filing_source, filing_manifest, fact_source, fact_manifest = _local_dart_sources(tmp_path)
    filings = load_filing_metadata(filing_source, manifest=filing_manifest)
    facts = load_financial_facts(fact_source, manifest=fact_manifest)

    from k200_mq.data.dart_pit import prepare_financial_facts

    dates = pd.date_range("2024-01-02", "2024-01-31", freq="B")
    prepared = prepare_financial_facts(facts, filings, dates, amendment_policy="first_filing")
    wide = pivot_financial_facts_to_wide(prepared)
    daily = main_module._convert_financial_to_daily(wide, dates)

    quality = QualityFactor().compute(daily)

    assert not quality.empty
    assert (quality["quality_composite_z"] != 0.0).any()
    primary = daily[daily["ticker"] == "005930"]
    assert not primary.empty
    last = primary.iloc[-1]
    assert last["net_income"] / last["total_equity"] == pytest.approx(100.0 / 1200.0)
    assert last["total_debt"] / last["total_equity"] == pytest.approx(800.0 / 1200.0)
    assert last["gross_profit_proxy"] / last["revenue"] == pytest.approx(600.0 / 1000.0)
    assert last["operating_cf"] / last["net_income"] == pytest.approx(80.0 / 100.0)
