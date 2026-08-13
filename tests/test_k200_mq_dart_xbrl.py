from __future__ import annotations

from io import BytesIO
import hashlib
import json
from pathlib import Path
import zipfile

import pandas as pd
import pytest

from k200_mq.data.dart_pit import (
    load_financial_facts,
    load_filing_metadata,
    pivot_financial_facts_to_wide,
    prepare_financial_facts,
)
from k200_mq.data.dart_xbrl import (
    XBRL_ENDPOINT,
    XBRL_SOURCE_TYPE,
    XBRLError,
    parse_xbrl_facts,
    request_params_sha256,
    write_xbrl_artifact,
    verify_derived_xbrl_manifest,
)


CORP_CODE = "00126186"
RCEPT_NO = "20150331003059"
IFRS_NS = "http://xbrl.ifrs.org/taxonomy/2014-03-05/ifrs-full"
ISO_NS = "http://www.xbrl.org/2003/iso4217"
INSTANCE_NS = "http://www.xbrl.org/2003/instance"
XBRLDI_NS = "http://xbrl.org/2006/xbrldi"
AXIS = "ConsolidatedAndSeparateFinancialStatementsAxis"

VALUES = {
    "Revenue": "7897747592292",
    "CostOfSales": "6605015701598",
    "ProfitLoss": "434272525474",
    "CashFlowsFromUsedInOperatingActivities": "996275205061",
    "Assets": "5546045160128",
    "Equity": "4214024628968",
}


def _instance_xml(
    *,
    entity: str = CORP_CODE,
    period_end: str = "2014-12-31",
    currency: str = "KRW",
    member: str = "ConsolidatedMember",
    duplicate_revenue: bool = False,
    entity_scheme: str = "http://opendart.fss.or.kr",
    root_namespace: str = INSTANCE_NS,
    axis_namespace: str = IFRS_NS,
    member_namespace: str = IFRS_NS,
    unit_namespace: str = ISO_NS,
    concept_namespace: str = IFRS_NS,
) -> bytes:
    duration_facts = "\n".join(
        f'  <ifrs-full:{concept} contextRef="duration" unitRef="KRW">{value}</ifrs-full:{concept}>'
        for concept, value in VALUES.items()
        if concept not in {"Assets", "Equity"}
    )
    instant_facts = "\n".join(
        f'  <ifrs-full:{concept} contextRef="instant" unitRef="KRW">{VALUES[concept]}</ifrs-full:{concept}>'
        for concept in ("Assets", "Equity")
    )
    duplicate = (
        f'  <ifrs-full:Revenue contextRef="duration" unitRef="KRW">{VALUES["Revenue"]}</ifrs-full:Revenue>'
        if duplicate_revenue else ""
    )
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="{root_namespace}" xmlns:ifrs-full="{concept_namespace}" xmlns:iso4217="{unit_namespace}" xmlns:xbrldi="{XBRLDI_NS}" xmlns:axis="{axis_namespace}" xmlns:member="{member_namespace}">
  <xbrli:unit id="KRW"><xbrli:measure>iso4217:{currency}</xbrli:measure></xbrli:unit>
  <xbrli:context id="duration">
    <xbrli:entity><xbrli:identifier scheme="{entity_scheme}">{entity}</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:startDate>2014-01-01</xbrli:startDate><xbrli:endDate>{period_end}</xbrli:endDate></xbrli:period>
    <xbrli:scenario><xbrldi:explicitMember dimension="axis:{AXIS}">member:{member}</xbrldi:explicitMember></xbrli:scenario>
  </xbrli:context>
  <xbrli:context id="instant">
    <xbrli:entity><xbrli:identifier scheme="{entity_scheme}">{entity}</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:instant>{period_end}</xbrli:instant></xbrli:period>
    <xbrli:scenario><xbrldi:explicitMember dimension="axis:{AXIS}">member:{member}</xbrldi:explicitMember></xbrli:scenario>
  </xbrli:context>
{duration_facts}
{instant_facts}
{duplicate}
</xbrli:xbrl>
'''
    return xml.encode("utf-8")


def _write_acquisition(tmp_path: Path, *, xml: bytes | None = None) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw = tmp_path / "018260_20150331003059.bin"
    instance = xml or _instance_xml()
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("statement.xbrl", instance)
    raw.write_bytes(buffer.getvalue())
    params = {"rcept_no": RCEPT_NO, "reprt_code": "11011"}
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    manifest = raw.with_suffix(raw.suffix + ".manifest.json")
    manifest.write_text(json.dumps({
        "source_url": XBRL_ENDPOINT,
        "source_type": XBRL_SOURCE_TYPE,
        "request_params": params,
        "request_params_sha256": request_params_sha256(params),
        "api_status": "000",
        "response_format": "zip",
        "response_status": "success",
        "pagination": {"complete": True},
        "retrieved_at_utc": "2024-01-03T00:00:00+00:00",
        "response_sha256": digest,
        "raw_file_sha256": digest,
        "raw_payload_path": str(raw.resolve()),
        "verified": True,
    }, ensure_ascii=False), encoding="utf-8")
    return raw, manifest


def test_golden_six_facts_and_verified_pit_wide_pivot(tmp_path: Path) -> None:
    raw, acquisition = _write_acquisition(tmp_path)
    normalization = parse_xbrl_facts(raw, acquisition, corp_code=CORP_CODE)
    assert len(normalization.facts) == 6
    assert {row["account_id"] for row in normalization.facts} == {
        "ifrs-full_Revenue",
        "ifrs-full_CostOfSales",
        "ifrs-full_ProfitLoss",
        "ifrs-full_CashFlowsFromUsedInOperatingActivities",
        "ifrs-full_Assets",
        "ifrs-full_Equity",
    }
    assert normalization.facts[3]["ord"] == "4"

    artifact = tmp_path / "dart_facts_fy2014.json"
    derived_manifest = tmp_path / "dart_facts_fy2014.derived.manifest.json"
    write_xbrl_artifact(normalization, artifact, derived_manifest)
    facts = load_financial_facts(artifact, manifest=derived_manifest)

    filing_source = tmp_path / "filings.json"
    filing_payload = [{
        "corp_code": CORP_CODE,
        "stock_code": "018260",
        "corp_name": "Example",
        "rcept_no": RCEPT_NO,
        "rcept_dt": "20150331",
        "report_nm": "사업보고서",
        "pblntf_ty": "A",
        "pblntf_detail_ty": "B",
        "rm": "",
    }]
    filing_source.write_text(json.dumps(filing_payload, ensure_ascii=False), encoding="utf-8")
    filing_digest = hashlib.sha256(filing_source.read_bytes()).hexdigest()
    filing_params = {"fixture_name": "filings.json"}
    filing_manifest = filing_source.with_suffix(filing_source.suffix + ".manifest.json")
    filing_manifest.write_text(json.dumps({
        "source_url": "https://opendart.fss.or.kr/api/list.json",
        "source_type": "opendartfilinglist",
        "request_params": filing_params,
        "request_params_sha256": request_params_sha256(filing_params),
        "api_status": "000",
        "pagination": {"complete": True},
        "retrieved_at_utc": "2024-01-03T00:00:00+00:00",
        "response_sha256": filing_digest,
    }), encoding="utf-8")
    filings = load_filing_metadata(filing_source, manifest=filing_manifest)
    prepared = prepare_financial_facts(
        facts,
        filings,
        pd.to_datetime(["2015-04-01", "2015-04-02"]),
        amendment_policy="first_filing",
    )
    wide = pivot_financial_facts_to_wide(prepared)
    assert prepared.attrs["financial_provenance"]["mode"] == "pit_filing_date"
    assert wide.loc[0, "revenue"] == 7897747592292
    assert wide.loc[0, "cogs"] == 6605015701598
    assert wide.loc[0, "net_income"] == 434272525474
    assert wide.loc[0, "operating_cf"] == 996275205061
    assert wide.loc[0, "total_assets"] == 5546045160128
    assert wide.loc[0, "total_equity"] == 4214024628968


def test_parser_rejects_mutated_transport_chain(tmp_path: Path) -> None:
    raw, acquisition = _write_acquisition(tmp_path)
    manifest = json.loads(acquisition.read_text(encoding="utf-8"))
    manifest["response_sha256"] = "0" * 64
    acquisition.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(XBRLError, match="hash"):
        parse_xbrl_facts(raw, acquisition, corp_code=CORP_CODE)


def test_mutated_canonical_artifact_with_refreshed_hashes_fails(tmp_path: Path) -> None:
    raw, acquisition = _write_acquisition(tmp_path)
    normalization = parse_xbrl_facts(raw, acquisition, corp_code=CORP_CODE)
    artifact = tmp_path / "facts.json"
    derived = tmp_path / "facts.derived.manifest.json"
    write_xbrl_artifact(normalization, artifact, derived)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload[0]["numeric_value"] = 1
    mutated = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    artifact.write_bytes(mutated)
    manifest = json.loads(derived.read_text(encoding="utf-8"))
    digest = hashlib.sha256(mutated).hexdigest()
    manifest["raw_file_sha256"] = digest
    manifest["response_sha256"] = digest
    manifest["artifact_sha256"] = digest
    derived.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(XBRLError, match="facts do not match parser output"):
        verify_derived_xbrl_manifest(manifest, artifact_path=artifact)

    raw, acquisition = _write_acquisition(tmp_path / "endpoint")
    manifest = json.loads(acquisition.read_text(encoding="utf-8"))
    manifest["source_url"] = "https://opendart.fss.or.kr/api/document.xml"
    acquisition.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(XBRLError, match="endpoint"):
        parse_xbrl_facts(raw, acquisition, corp_code=CORP_CODE)

    raw, acquisition = _write_acquisition(tmp_path / "request")
    manifest = json.loads(acquisition.read_text(encoding="utf-8"))
    manifest["request_params"]["rcept_no"] = "other"
    acquisition.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(XBRLError, match="request_params_sha256"):
        parse_xbrl_facts(raw, acquisition, corp_code=CORP_CODE)


@pytest.mark.parametrize(
    "case",
    [
        "entity",
        "period",
        "unit",
        "context",
    ],
)
def test_parser_rejects_entity_period_unit_or_context_mismatch(tmp_path: Path, case: str) -> None:
    if case == "entity":
        xml = _instance_xml(entity="99999999")
    elif case == "period":
        xml = _instance_xml(period_end="2013-12-31")
    elif case == "unit":
        xml = _instance_xml(currency="USD")
    else:
        xml = _instance_xml(member="SeparateMember")
    raw, acquisition = _write_acquisition(tmp_path, xml=xml)
    with pytest.raises(XBRLError):
        parse_xbrl_facts(raw, acquisition, corp_code=CORP_CODE)


def test_parser_rejects_duplicate_required_fact(tmp_path: Path) -> None:
    raw, acquisition = _write_acquisition(tmp_path, xml=_instance_xml(duplicate_revenue=True))
    with pytest.raises(XBRLError, match="ambiguous"):
        parse_xbrl_facts(raw, acquisition, corp_code=CORP_CODE)


@pytest.mark.parametrize(
    "case",
    [
        "concept",
        "axis",
        "member",
        "unit",
        "entity",
        "root",
    ],
)
def test_parser_rejects_namespace_impostors(tmp_path: Path, case: str) -> None:
    if case == "concept":
        xml = _instance_xml(concept_namespace="urn:custom")
    elif case == "axis":
        xml = _instance_xml(axis_namespace="urn:custom")
    elif case == "member":
        xml = _instance_xml(member_namespace="urn:custom")
    elif case == "unit":
        xml = _instance_xml(unit_namespace="urn:custom")
    elif case == "entity":
        xml = _instance_xml(entity_scheme="urn:custom")
    else:
        xml = _instance_xml(root_namespace="urn:custom")
    raw, acquisition = _write_acquisition(tmp_path, xml=xml)
    with pytest.raises(XBRLError):
        parse_xbrl_facts(raw, acquisition, corp_code=CORP_CODE)


def test_parser_rejects_prefix_rebind_after_attacker_context(tmp_path: Path) -> None:
    xml = _instance_xml().decode("utf-8")
    xml = xml.replace(
        '<xbrli:context id="duration">',
        '<xbrli:context id="duration" xmlns:axis="urn:attacker:axis" '
        'xmlns:member="urn:attacker:member">',
    )
    xml = xml.replace(
        '<xbrli:context id="instant">',
        '<xbrli:context id="instant" '
        f'xmlns:axis="{IFRS_NS}" xmlns:member="{IFRS_NS}">',
    )
    raw, acquisition = _write_acquisition(tmp_path, xml=xml.encode("utf-8"))
    with pytest.raises(XBRLError, match="rebound"):
        parse_xbrl_facts(raw, acquisition, corp_code=CORP_CODE)


def test_parser_rejects_multiple_xbrl_instances(tmp_path: Path) -> None:
    raw, acquisition = _write_acquisition(tmp_path)
    second = tmp_path / "second.bin"
    with zipfile.ZipFile(raw, "r") as source, zipfile.ZipFile(second, "w") as target:
        for info in source.infolist():
            target.writestr(info.filename, source.read(info))
        target.writestr("second.xbrl", _instance_xml())
    raw.write_bytes(second.read_bytes())
    manifest = json.loads(acquisition.read_text(encoding="utf-8"))
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    manifest["response_sha256"] = digest
    manifest["raw_file_sha256"] = digest
    acquisition.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(XBRLError, match="exactly one"):
        parse_xbrl_facts(raw, acquisition, corp_code=CORP_CODE)


def test_parser_rejects_unsafe_zip_member(tmp_path: Path) -> None:
    raw, acquisition = _write_acquisition(tmp_path)
    unsafe = tmp_path / "unsafe.bin"
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("../statement.xbrl", _instance_xml())
    raw.write_bytes(unsafe.read_bytes())
    manifest = json.loads(acquisition.read_text(encoding="utf-8"))
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    manifest["response_sha256"] = digest
    manifest["raw_file_sha256"] = digest
    acquisition.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(XBRLError, match="unsafe"):
        parse_xbrl_facts(raw, acquisition, corp_code=CORP_CODE)


def test_parser_rejects_raw_zip_before_reading_oversized_file(tmp_path: Path, monkeypatch) -> None:
    raw, acquisition = _write_acquisition(tmp_path)
    monkeypatch.setattr("k200_mq.data.dart_xbrl.MAX_RAW_ZIP_BYTES", 1)
    with pytest.raises(XBRLError, match="compressed-size"):
        parse_xbrl_facts(raw, acquisition, corp_code=CORP_CODE)


def test_statusless_xml_is_not_a_proven_xbrl_acquisition(tmp_path: Path) -> None:
    raw, acquisition = _write_acquisition(tmp_path)
    manifest = json.loads(acquisition.read_text(encoding="utf-8"))
    manifest.pop("api_status")
    acquisition.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(XBRLError, match="successful"):
        parse_xbrl_facts(raw, acquisition, corp_code=CORP_CODE)


def test_untyped_raw_xbrl_manifest_cannot_load_as_financial_facts(tmp_path: Path) -> None:
    raw, acquisition = _write_acquisition(tmp_path)
    payload = [{"rcept_no": RCEPT_NO, "corp_code": CORP_CODE}]
    raw_json = tmp_path / "raw.json"
    raw_json.write_text(json.dumps(payload), encoding="utf-8")
    manifest = json.loads(acquisition.read_text(encoding="utf-8"))
    digest = hashlib.sha256(raw_json.read_bytes()).hexdigest()
    manifest.update({"response_sha256": digest, "raw_file_sha256": digest})
    acquisition.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(Exception):
        load_financial_facts(raw_json, manifest=acquisition)


def test_optional_real_fy2014_receipt_integration() -> None:
    """Run with the ignored probe; equivalent CLI: build_local_dart_xbrl_facts.py."""
    raw = Path("data/raw/dart_xbrl_fy2014_probe/018260_20150331003059.bin")
    manifest = raw.with_suffix(raw.suffix + ".manifest.json")
    if not raw.is_file() or not manifest.is_file():
        pytest.skip("ignored FY2014 XBRL probe is not present locally")
    normalization = parse_xbrl_facts(raw, manifest, corp_code=CORP_CODE)
    values = {row["account_id"]: row["numeric_value"] for row in normalization.facts}
    assert values == {
        "ifrs-full_Revenue": 7897747592292,
        "ifrs-full_CostOfSales": 6605015701598,
        "ifrs-full_ProfitLoss": 434272525474,
        "ifrs-full_CashFlowsFromUsedInOperatingActivities": 996275205061,
        "ifrs-full_Assets": 5546045160128,
        "ifrs-full_Equity": 4214024628968,
    }
