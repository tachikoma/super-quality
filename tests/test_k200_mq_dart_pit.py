"""Local-only OpenDART filing provenance contract tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
import k200_mq.data as data_api

from k200_mq.data.dart_pit import (
    DARTPITError,
    FINANCIAL_FACT_COLUMNS,
    FILING_METADATA_COLUMNS,
    load_financial_facts,
    load_filing_metadata,
    join_financial_facts_to_filings,
    map_filing_availability,
    prepare_financial_facts,
    validate_dart_pit,
)
from k200_mq.data.provenance import validate_financial_provenance


def _write_json(tmp_path: Path, name: str, rows: list[dict[str, object]]) -> tuple[Path, Path]:
    source = tmp_path / name
    source.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = source.with_suffix(source.suffix + ".manifest.json")
    request_params = {"fixture_name": name}
    request_hash = hashlib.sha256(
        json.dumps(request_params, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    endpoint = (
        "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
        if "fact" in name else "https://opendart.fss.or.kr/api/list.json"
    )
    manifest.write_text(json.dumps({
        "response_sha256": digest,
        "source_url": endpoint,
        "request_params": request_params,
        "request_params_sha256": request_hash,
        "api_status": "000",
        "pagination": {"complete": True},
        "retrieved_at_utc": "2024-01-03T00:00:00+00:00",
    }), encoding="utf-8")
    return source, manifest


def _sources(tmp_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    filing_source, filing_manifest = _write_json(tmp_path, "filings.json", [{
        "corp_code": "001",
        "stock_code": "005930",
        "corp_name": "Example",
        "rcept_no": "R1",
        "rcept_dt": "20240102",
        "report_nm": "사업보고서",
        "pblntf_ty": "A",
        "pblntf_detail_ty": "B",
        "rm": "",
    }])
    facts_source, facts_manifest = _write_json(tmp_path, "facts.json", [{
        "rcept_no": "R1",
        "corp_code": "001",
        "bsns_year": "2023",
        "reprt_code": "11011",
        "fs_div": "CFS",
        "sj_div": "BS",
        "account_id": "ifrs-full_Revenue",
        "account_nm": "Revenue",
        "account_detail": "consolidated",
        "period_end": "20231231",
        "thstrm_amount": "1,000",
        "currency": "KRW",
    }])
    return (
        load_filing_metadata(filing_source, manifest=filing_manifest),
        load_financial_facts(facts_source, manifest=facts_manifest),
    )


def test_valid_join_and_schema_are_raw_submission_keyed(tmp_path: Path) -> None:
    filings, facts = _sources(tmp_path)
    joined = join_financial_facts_to_filings(facts, filings)

    assert list(filings.columns) == list(FILING_METADATA_COLUMNS)
    assert list(facts.columns) == list(FINANCIAL_FACT_COLUMNS)
    assert joined.loc[0, "rcept_no"] == "R1"
    assert joined.loc[0, "corp_name"] == "Example"
    assert joined.attrs["dart_join_key"] == "(corp_code, rcept_no)"
    assert joined.loc[0, "numeric_value"] == 1000.0


def test_post_join_mutations_cannot_issue_a_fresh_mapping_token(tmp_path: Path) -> None:
    filings, facts = _sources(tmp_path)
    joined = join_financial_facts_to_filings(facts, filings)
    joined.loc[0, "numeric_value"] = 999.0
    with pytest.raises(DARTPITError, match="join evidence|stale"):
        map_filing_availability(
            joined, pd.to_datetime(["2024-01-03"]), amendment_policy="first_filing",
        )

    joined = join_financial_facts_to_filings(facts, filings)
    embedded_filings = joined.attrs["filing_metadata"]
    embedded_filings.loc[0, "corp_name"] = "mutated-after-join"
    with pytest.raises(DARTPITError, match="join evidence|stale"):
        map_filing_availability(
            joined, pd.to_datetime(["2024-01-03"]), amendment_policy="first_filing",
        )


@pytest.mark.parametrize("kind", ["missing", "ambiguous"])
def test_missing_or_ambiguous_receipt_join_is_rejected(tmp_path: Path, kind: str) -> None:
    filings, facts = _sources(tmp_path)
    if kind == "missing":
        facts.loc[0, "rcept_no"] = "UNKNOWN"
    else:
        filings = pd.concat([filings, filings], ignore_index=True)
    with pytest.raises(DARTPITError, match="(missing|ambiguous)"):
        join_financial_facts_to_filings(facts, filings)


def test_date_only_receipt_uses_strict_next_session(tmp_path: Path) -> None:
    filings, facts = _sources(tmp_path)
    prepared = prepare_financial_facts(
        facts,
        filings,
        pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
        amendment_policy="first_filing",
    )
    assert prepared.loc[0, "filing_date"] == pd.Timestamp("2024-01-02").date()
    assert prepared.loc[0, "availability_session"] == pd.Timestamp("2024-01-03")
    assert prepared.attrs["financial_provenance"]["mode"] == "pit_filing_date"


def test_no_same_day_session_is_accepted(tmp_path: Path) -> None:
    filings, facts = _sources(tmp_path)
    with pytest.raises(DARTPITError, match="cannot be mapped"):
        prepare_financial_facts(
            facts, filings, pd.to_datetime(["2024-01-02"]), amendment_policy="first_filing",
        )


def test_post_join_timestamp_injection_cannot_promote_to_pit(tmp_path: Path) -> None:
    filings, facts = _sources(tmp_path)
    joined = join_financial_facts_to_filings(facts, filings)
    joined["filing_timestamp"] = ["2024-01-02T09:00:00+09:00"]
    report = validate_dart_pit(
        joined,
        filings=filings,
        trading_dates=pd.to_datetime(["2024-01-02", "2024-01-03"]),
        availability_policy={
            "name": "session_cutoff", "timezone": "Asia/Seoul", "cutoff_time": "15:30",
        },
        amendment_policy="first_filing",
    )
    assert report.mode == "non_pit_fiscal_period"
    assert report.pit_valid is False


def test_explicit_cutoff_rejects_non_seoul_official_timezone(tmp_path: Path) -> None:
    filings, facts = _sources(tmp_path)
    joined = join_financial_facts_to_filings(facts, filings).drop(columns=["rcept_dt_raw"])
    joined["filing_timestamp"] = ["2024-01-02T09:00:00+00:00"]
    with pytest.raises(DARTPITError, match="exact-join|stale"):
        map_filing_availability(
            joined,
            pd.to_datetime(["2024-01-02", "2024-01-03"]),
            availability_policy={
                "name": "session_cutoff", "timezone": "UTC", "cutoff_time": "15:30",
            },
            amendment_policy="first_filing",
        )


def test_withdrawal_and_unspecified_amendment_policy_are_rejected(tmp_path: Path) -> None:
    filings, facts = _sources(tmp_path)
    filings.loc[0, "is_withdrawn"] = True
    joined = join_financial_facts_to_filings(facts, filings)
    with pytest.raises(DARTPITError, match="withdrawn"):
        map_filing_availability(
            joined, pd.to_datetime(["2024-01-03"]), amendment_policy="first_filing",
        )
    filings.loc[0, "is_withdrawn"] = False
    with pytest.raises(DARTPITError, match="amendment_policy"):
        map_filing_availability(joined, pd.to_datetime(["2024-01-03"]), amendment_policy=None)


def test_amendment_policies_are_explicit_and_deterministic(tmp_path: Path) -> None:
    filings, facts = _sources(tmp_path)
    amendment = filings.copy()
    amendment.loc[0, "rcept_no"] = "R2"
    amendment.loc[0, "rcept_dt_raw"] = "20240103"
    amendment.loc[0, "rcept_date"] = pd.Timestamp("2024-01-03").date()
    amendment.loc[0, "is_amendment"] = True
    amended_fact = facts.copy()
    amended_fact.loc[0, "rcept_no"] = "R2"
    amended_fact.loc[0, "raw_value"] = "2,000"
    amended_fact.loc[0, "numeric_value"] = 2000.0
    all_filings = pd.concat([filings, amendment], ignore_index=True)
    all_facts = pd.concat([facts, amended_fact], ignore_index=True)
    first = prepare_financial_facts(
        all_facts, all_filings, pd.date_range("2024-01-02", "2024-01-05"),
        amendment_policy="first_filing",
    )
    latest = prepare_financial_facts(
        all_facts, all_filings, pd.date_range("2024-01-02", "2024-01-05"),
        amendment_policy="latest_filing_available_as_of",
    )
    assert first["rcept_no"].tolist() == ["R1"]
    assert latest["rcept_no"].tolist() == ["R1", "R2"]


def test_missing_manifest_hash_is_non_pit_and_does_not_upgrade_existing_validator(
    tmp_path: Path,
) -> None:
    filings, facts = _sources(tmp_path)
    filings.loc[0, "corp_name"] = "mutated-after-import"
    prepared = prepare_financial_facts(
        facts, filings, pd.date_range("2024-01-02", "2024-01-04"), amendment_policy="first_filing",
    )
    assert prepared.attrs["financial_provenance"]["mode"] == "non_pit_fiscal_period"
    assert validate_financial_provenance(prepared, filing_date_used=True)["pit_valid"] is False


def test_missing_manifest_is_a_candidate_but_not_pit_evidence(tmp_path: Path) -> None:
    filing_source, _ = _write_json(tmp_path, "filings-no-manifest.json", [{
        "corp_code": "001", "rcept_no": "R1", "rcept_dt": "20240102",
    }])
    filing_source.with_suffix(filing_source.suffix + ".manifest.json").unlink()
    filings = load_filing_metadata(filing_source)

    assert filings.attrs["manifest_verified"] is False
    assert filings["response_sha256"].isna().all()


def test_validation_result_and_output_are_deterministic(tmp_path: Path) -> None:
    filings, facts = _sources(tmp_path)
    first = prepare_financial_facts(
        facts.iloc[::-1], filings.iloc[::-1], pd.date_range("2024-01-02", "2024-01-04"),
        amendment_policy="first_filing",
    )
    second = prepare_financial_facts(
        facts, filings, pd.date_range("2024-01-02", "2024-01-04"), amendment_policy="first_filing",
    )
    pd.testing.assert_frame_equal(first, second)
    assert validate_dart_pit(first).mode == "pit_filing_date"
    assert validate_financial_provenance(first, filing_date_used=True)["pit_valid"] is True


def test_normalized_mutation_and_forged_attrs_cannot_repromote_pit(tmp_path: Path) -> None:
    filings, facts = _sources(tmp_path)
    prepared = prepare_financial_facts(
        facts, filings, pd.date_range("2024-01-02", "2024-01-04"), amendment_policy="first_filing",
    )
    prepared.loc[0, "numeric_value"] = 999.0
    prepared.attrs["dart_availability_valid"] = True
    prepared.attrs["financial_provenance_contract"] = {
        "source": "forged", "schema": {"filing_date": "filing date"},
        "availability_policy": "next_session",
    }
    report = validate_dart_pit(prepared)
    assert report.pit_valid is False
    assert report.mode == "non_pit_fiscal_period"


def test_facts_filing_metadata_collision_is_rejected(tmp_path: Path) -> None:
    source, manifest = _write_json(tmp_path, "facts-collision.json", [{
        "rcept_no": "R1", "corp_code": "001", "bsns_year": "2023",
        "reprt_code": "11011", "fs_div": "CFS", "sj_div": "BS",
        "account_id": "a", "account_detail": "d", "period_end": "20231231",
        "thstrm_amount": "1", "rcept_date": "2024-01-02",
    }])
    with pytest.raises(DARTPITError, match="filing provenance"):
        load_financial_facts(source, manifest=manifest)


def test_same_receipt_distinct_account_details_are_not_collapsed(tmp_path: Path) -> None:
    filing_source, filing_manifest = _write_json(tmp_path, "filings-detail.json", [{
        "corp_code": "001", "rcept_no": "R1", "rcept_dt": "20240102",
    }])
    fact_source, fact_manifest = _write_json(tmp_path, "facts-detail.json", [
        {"corp_code": "001", "rcept_no": "R1", "bsns_year": "2023", "reprt_code": "11011",
         "fs_div": "CFS", "sj_div": "BS", "account_id": "a", "account_detail": "d1",
         "period_end": "20231231", "thstrm_amount": "1"},
        {"corp_code": "001", "rcept_no": "R1", "bsns_year": "2023", "reprt_code": "11011",
         "fs_div": "CFS", "sj_div": "BS", "account_id": "a", "account_detail": "d2",
         "period_end": "20231231", "thstrm_amount": "2"},
    ])
    filings = load_filing_metadata(filing_source, manifest=filing_manifest)
    facts = load_financial_facts(fact_source, manifest=fact_manifest)
    prepared = prepare_financial_facts(
        facts, filings, pd.date_range("2024-01-02", "2024-01-04"), amendment_policy="first_filing",
    )
    assert prepared["account_detail"].tolist() == ["d1", "d2"]


def test_duplicate_account_detail_identity_is_rejected(tmp_path: Path) -> None:
    filing_source, filing_manifest = _write_json(tmp_path, "filings-duplicate-detail.json", [{
        "corp_code": "001", "rcept_no": "R1", "rcept_dt": "20240102",
    }])
    row: dict[str, object] = {
        "corp_code": "001", "rcept_no": "R1", "bsns_year": "2023", "reprt_code": "11011",
        "fs_div": "CFS", "sj_div": "BS", "account_id": "a", "account_detail": "d",
        "period_end": "20231231", "thstrm_amount": "1",
    }
    fact_source, fact_manifest = _write_json(tmp_path, "facts-duplicate-detail.json", [row, row])
    filings = load_filing_metadata(filing_source, manifest=filing_manifest)
    facts = load_financial_facts(fact_source, manifest=fact_manifest)
    with pytest.raises(DARTPITError, match="ambiguous duplicate"):
        join_financial_facts_to_filings(facts, filings)


def test_unknown_explicit_status_fails_closed(tmp_path: Path) -> None:
    source, manifest = _write_json(tmp_path, "filings-unknown-status.json", [{
        "corp_code": "001", "rcept_no": "R1", "rcept_dt": "20240102", "is_withdrawn": "MAYBE",
    }])
    with pytest.raises(DARTPITError, match="unknown explicit is_withdrawn"):
        load_filing_metadata(source, manifest=manifest)


def test_midnight_and_conflicting_timestamp_candidates_fail_closed(tmp_path: Path) -> None:
    filings, facts = _sources(tmp_path)
    joined = join_financial_facts_to_filings(facts, filings).drop(columns=["rcept_dt_raw"])
    joined["filing_timestamp"] = ["2024-01-02T00:00:00+09:00"]
    policy = {"name": "session_cutoff", "timezone": "Asia/Seoul", "cutoff_time": "15:30"}
    with pytest.raises(DARTPITError, match="exact-join|stale"):
        map_filing_availability(joined, pd.date_range("2024-01-02", "2024-01-03"),
                                availability_policy=policy, amendment_policy="first_filing")
    joined["availability_timestamp"] = joined["filing_timestamp"]
    with pytest.raises(DARTPITError, match="exact-join|stale"):
        map_filing_availability(joined, pd.date_range("2024-01-02", "2024-01-03"),
                                availability_policy="next_session", amendment_policy="first_filing")


def test_aware_krx_sessions_keep_seoul_calendar_date(tmp_path: Path) -> None:
    filings, facts = _sources(tmp_path)
    joined = join_financial_facts_to_filings(facts, filings)
    mapped = map_filing_availability(
        joined,
        pd.to_datetime(["2024-01-02T15:00:00+09:00", "2024-01-03T00:00:00+09:00"]),
        amendment_policy="first_filing",
    )
    assert mapped.loc[0, "availability_session"] == pd.Timestamp("2024-01-03")


def test_fixture_manifest_is_explicitly_unverified_and_cannot_emit_pit(tmp_path: Path) -> None:
    filing_source, filing_manifest = _write_json(tmp_path, "filings-fixture.json", [{
        "corp_code": "001", "rcept_no": "R1", "rcept_dt": "20240102",
    }])
    manifest_data = json.loads(filing_manifest.read_text(encoding="utf-8"))
    manifest_data["fixture"] = True
    filings = load_filing_metadata(filing_source, manifest=manifest_data)
    assert filings.attrs["manifest_verified"] is False
    assert filings["response_sha256"].isna().all()


@pytest.mark.parametrize(
    ("loader_kind", "bad_url"),
    [
        ("filing", "https://opendart.fss.or.kr/api/document.xml"),
        ("filing", "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"),
        ("facts", "https://opendart.fss.or.kr/api/document.xml"),
        ("facts", "https://opendart.fss.or.kr/api/arbitrary.json"),
    ],
)
def test_manifest_endpoint_is_bound_to_the_dart_loader(
    tmp_path: Path, loader_kind: str, bad_url: str,
) -> None:
    filename = "filings-endpoint.json" if loader_kind == "filing" else "facts-endpoint.json"
    source, manifest_path = _write_json(
        tmp_path,
        filename,
        ([{"corp_code": "001", "rcept_no": "R1", "rcept_dt": "20240102"}]
         if loader_kind == "filing" else [{
             "rcept_no": "R1", "corp_code": "001", "bsns_year": "2023",
             "reprt_code": "11011", "fs_div": "CFS", "sj_div": "BS",
         }]),
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_url"] = bad_url
    loader = load_filing_metadata if loader_kind == "filing" else load_financial_facts
    with pytest.raises(DARTPITError, match="endpoint/source type"):
        loader(source, manifest=manifest)


def test_manifest_source_type_must_match_financial_endpoint(tmp_path: Path) -> None:
    source, manifest_path = _write_json(tmp_path, "facts-source-type.json", [{
        "rcept_no": "R1", "corp_code": "001", "bsns_year": "2023",
        "reprt_code": "11011", "fs_div": "CFS", "sj_div": "BS",
    }])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_type"] = "opendart_filing_list"
    with pytest.raises(DARTPITError, match="endpoint/source type"):
        load_financial_facts(source, manifest=manifest)


def test_package_exports_are_unambiguous_dart_apis() -> None:
    assert data_api.DART_FINANCIAL_FACT_COLUMNS == FINANCIAL_FACT_COLUMNS
    assert hasattr(data_api, "load_dart_financial_facts")
    assert hasattr(data_api, "prepare_dart_financial_facts")
    assert not hasattr(data_api, "load_financial_facts")
    assert not hasattr(data_api, "prepare_financial_facts")
