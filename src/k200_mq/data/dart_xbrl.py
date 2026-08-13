"""Pure, receipt-keyed OpenDART XBRL normalisation for FY2014.

The module deliberately has no pandas or application-pipeline dependency.  It
validates the acquisition evidence, reads one XBRL instance directly from a
bounded ZIP archive, and emits the canonical long financial-fact records used
by :mod:`k200_mq.data.dart_pit`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO


XBRL_ENDPOINT = "https://opendart.fss.or.kr/api/fnlttXbrl.xml"
XBRL_SOURCE_TYPE = "opendartfinancialxbrl"
XBRLI_NAMESPACE = "http://www.xbrl.org/2003/instance"
XBRLDI_NAMESPACE = "http://xbrl.org/2006/xbrldi"
IFRS_FULL_2014_NAMESPACE = "http://xbrl.ifrs.org/taxonomy/2014-03-05/ifrs-full"
IFRS_IASB_2010_NAMESPACE = "http://xbrl.iasb.org/taxonomy/2010-04-30/ifrs"
ISO4217_NAMESPACE = "http://www.xbrl.org/2003/iso4217"
PERMITTED_ENTITY_SCHEMES = frozenset({
    "http://opendart.fss.or.kr",
    "http://dart.fss.or.kr/ifrs/CIK",
})
PERMITTED_IFRS_NAMESPACES = frozenset({IFRS_FULL_2014_NAMESPACE, IFRS_IASB_2010_NAMESPACE})
DERIVED_XBRL_SOURCE_TYPE = "k200mqderivedfinancialxbrl"
DERIVED_XBRL_MANIFEST_VERSION = "k200mq-derived-xbrl-v1"
PARSER_VERSION = "k200mq-dart-xbrl-v1"
FINANCIAL_FACT_COLUMNS = (
    "rcept_no",
    "corp_code",
    "bsns_year",
    "reprt_code",
    "fs_div",
    "sj_div",
    "account_id",
    "account_name",
    "account_detail",
    "ord",
    "period_end",
    "raw_value",
    "numeric_value",
    "currency",
    "payload_sha256",
)

EXPECTED_START_DATE = "2014-01-01"
EXPECTED_END_DATE = "2014-12-31"
EXPECTED_INSTANT_DATE = "2014-12-31"
EXPECTED_REPRT_CODE = "11011"
EXPECTED_FS_DIV = "CFS"
EXPECTED_CURRENCY = "KRW"
CONSOLIDATION_AXIS = "ConsolidatedAndSeparateFinancialStatementsAxis"
CONSOLIDATED_MEMBER = "ConsolidatedMember"
BASE_ACCOUNT_DETAIL = f"{CONSOLIDATION_AXIS}={CONSOLIDATED_MEMBER}"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# These limits are intentionally conservative for a single-company XBRL
# response.  They prevent a malformed or hostile ZIP from becoming a resource
# exhaustion path while leaving ample room for the real DART archive.
MAX_ZIP_ENTRIES = 256
MAX_ZIP_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 1000
MAX_RAW_ZIP_BYTES = 256 * 1024 * 1024

_SEMANTIC_FACTS: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("revenue", ("Revenue",), "Revenue", "IS"),
    ("cogs", ("CostOfSales",), "CostOfSales", "IS"),
    ("net_income", ("ProfitLoss",), "ProfitLoss", "IS"),
    (
        "operating_cf",
        ("CashFlowsFromUsedInOperatingActivities",),
        "CashFlowsFromUsedInOperatingActivities",
        "CF",
    ),
    ("total_assets", ("Assets",), "Assets", "BS"),
    ("total_equity", ("Equity",), "Equity", "BS"),
)
_SEMANTIC_BY_LOCAL = {
    local_name: (semantic, display_name, sj_div)
    for semantic, local_names, display_name, sj_div in _SEMANTIC_FACTS
    for local_name in local_names
}


class XBRLError(ValueError):
    """Raised when XBRL transport, context, or fact evidence is invalid."""


@dataclass(frozen=True)
class XBRLNormalization:
    """Facts and evidence collected while normalising one XBRL archive."""

    facts: tuple[dict[str, Any], ...]
    raw_xbrl_path: str
    raw_xbrl_sha256: str
    acquisition_manifest_path: str | None
    acquisition_manifest_sha256: str | None
    request_params: dict[str, str]
    request_params_sha256: str
    xbrl_instance_member: str
    xbrl_instance_sha256: str


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def request_params_sha256(params: Mapping[str, str]) -> str:
    payload = json.dumps(dict(params), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(payload.encode("utf-8"))


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _check_raw_zip_size(path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise XBRLError(f"unable to stat XBRL raw ZIP: {path}") from exc
    if size > MAX_RAW_ZIP_BYTES:
        raise XBRLError("XBRL raw ZIP exceeds the safety compressed-size limit")


def _read_manifest(value: Mapping[str, Any] | str | Path) -> tuple[dict[str, Any], Path | None, str | None]:
    if isinstance(value, Mapping):
        return dict(value), None, None
    path = Path(value)
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise XBRLError(f"invalid XBRL acquisition manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise XBRLError("XBRL acquisition manifest must be a JSON object")
    return payload, path.resolve(), sha256_bytes(raw)


def _resolved_path(value: Any, *, relative_to: Path | None = None) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if not path.is_absolute() and relative_to is not None:
        path = relative_to / path
    return path.resolve()


def _path_matches(value: Any, actual: Path, *, relative_to: Path | None = None) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    candidates = {Path(value).resolve()}
    if relative_to is not None:
        candidates.add((relative_to / value).resolve())
    return actual.resolve() in candidates


def _success_status(value: Any) -> bool:
    return str(value).strip() in {"000", "0"}


def _manifest_raw_hash(manifest: Mapping[str, Any]) -> str | None:
    for key in ("response_sha256", "raw_file_sha256", "raw_response_sha256"):
        value = manifest.get(key)
        if _valid_hash(value):
            return str(value).lower()
    return None


def _sanitize_params(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise XBRLError("XBRL acquisition request_params must be an object")
    secret_names = {"api_key", "crtfc_key", "apikey", "token", "key"}
    if any(str(key).casefold() in secret_names for key in value):
        raise XBRLError("XBRL acquisition request_params must not contain API keys")
    result = {
        str(key): str(item)
        for key, item in value.items()
        if str(item) != ""
    }
    return dict(sorted(result.items()))


def _verify_request_identity(manifest: Mapping[str, Any]) -> dict[str, str]:
    params = _sanitize_params(manifest.get("request_params"))
    expected_hash = manifest.get("request_params_sha256")
    if not _valid_hash(expected_hash) or request_params_sha256(params) != str(expected_hash).lower():
        raise XBRLError("XBRL acquisition request_params_sha256 does not match request_params")
    for name in ("rcept_no", "reprt_code"):
        if not params.get(name):
            raise XBRLError(f"XBRL acquisition request_params missing {name}")
    return params


def verify_xbrl_acquisition(
    raw_xbrl_path: str | Path,
    acquisition_manifest: Mapping[str, Any] | str | Path,
) -> dict[str, Any]:
    """Verify the exact transport contract for a downloaded XBRL ZIP."""
    raw_path = Path(raw_xbrl_path).resolve()
    if not raw_path.is_file():
        raise XBRLError(f"XBRL raw ZIP does not exist: {raw_path}")
    _check_raw_zip_size(raw_path)
    manifest, manifest_path, manifest_digest = _read_manifest(acquisition_manifest)
    raw_hash = sha256_file(raw_path)
    if manifest.get("source_url") != XBRL_ENDPOINT:
        raise XBRLError("XBRL acquisition endpoint is not the official fnlttXbrl.xml endpoint")
    if manifest.get("source_type") != XBRL_SOURCE_TYPE:
        raise XBRLError("XBRL acquisition source_type is not opendartfinancialxbrl")
    if not _success_status(manifest.get("api_status")) or manifest.get("verified") is not True:
        raise XBRLError("XBRL acquisition manifest is not explicitly successful and verified")
    if manifest.get("response_format") != "zip" or manifest.get("response_status") != "success":
        raise XBRLError("XBRL acquisition manifest must prove a successful ZIP response")
    declared_hashes = [manifest.get("response_sha256"), manifest.get("raw_file_sha256")]
    if any(not _valid_hash(value) or str(value).lower() != raw_hash for value in declared_hashes):
        raise XBRLError("XBRL acquisition manifest hash does not match raw ZIP bytes")
    if not _path_matches(
        manifest.get("raw_payload_path"),
        raw_path,
        relative_to=manifest_path.parent if manifest_path else None,
    ):
        raise XBRLError("XBRL acquisition raw_payload_path does not match raw ZIP path")
    params = _verify_request_identity(manifest)
    retrieved = manifest.get("retrieved_at_utc")
    try:
        parsed_retrieved = datetime.fromisoformat(str(retrieved))
    except ValueError as exc:
        raise XBRLError("XBRL acquisition retrieved_at_utc is invalid") from exc
    if parsed_retrieved.tzinfo is None:
        raise XBRLError("XBRL acquisition retrieved_at_utc must be timezone-aware")
    return {
        "manifest": manifest,
        "manifest_path": str(manifest_path) if manifest_path else None,
        "manifest_sha256": manifest_digest,
        "raw_sha256": raw_hash,
        "request_params": params,
    }


def _safe_zip_member(info: zipfile.ZipInfo) -> None:
    name = info.filename.replace("\\", "/")
    path = PurePosixPath(name)
    if not name or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or "\x00" in name:
        raise XBRLError(f"unsafe XBRL ZIP member name: {info.filename!r}")
    if info.flag_bits & 0x1:
        raise XBRLError(f"encrypted XBRL ZIP member is not allowed: {info.filename!r}")
    if info.file_size > MAX_ZIP_MEMBER_BYTES:
        raise XBRLError("XBRL ZIP member exceeds the safety size limit")
    if info.compress_size and info.file_size / info.compress_size > MAX_ZIP_COMPRESSION_RATIO:
        raise XBRLError("XBRL ZIP member exceeds the safety compression-ratio limit")


def _read_xbrl_instance(raw_zip: bytes) -> tuple[str, bytes]:
    try:
        archive = zipfile.ZipFile(BytesIO(raw_zip))
    except (OSError, zipfile.BadZipFile) as exc:
        raise XBRLError("XBRL acquisition payload is not a valid ZIP archive") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise XBRLError("XBRL ZIP contains too many members")
        names = [info.filename.replace("\\", "/") for info in infos]
        if len(names) != len(set(names)) or len(names) != len({name.casefold() for name in names}):
            raise XBRLError("XBRL ZIP contains duplicate member names")
        total_size = 0
        for info in infos:
            _safe_zip_member(info)
            total_size += info.file_size
        if total_size > MAX_ZIP_TOTAL_BYTES:
            raise XBRLError("XBRL ZIP exceeds the safety uncompressed-size limit")
        instances = [
            info for info in infos
            if not info.is_dir() and info.filename.casefold().endswith(".xbrl")
        ]
        if len(instances) != 1:
            raise XBRLError("XBRL ZIP must contain exactly one .xbrl instance")
        try:
            instance = archive.read(instances[0])
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise XBRLError("unable to read XBRL instance from ZIP") from exc
    return instances[0].filename, instance


def _xml_tag(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") and "}" in tag else ""


def _concept_identity(tag: str) -> str:
    local = _local_name(tag)
    namespace = _namespace(tag)
    if not namespace:
        return local
    tail = namespace.rstrip("/").rsplit("/", 1)[-1]
    if namespace in PERMITTED_IFRS_NAMESPACES or tail.casefold() in {
        "ifrs-full",
        "ifrs-full.xsd",
    }:
        return f"ifrs-full_{local}"
    if ":" in tail:
        tail = tail.rsplit(":", 1)[-1]
    return f"{tail}_{local}" if tail else local


def _children(element: ET.Element, namespace: str, local: str) -> list[ET.Element]:
    return [child for child in list(element) if child.tag == _xml_tag(namespace, local)]


def _resolve_qname(value: str | None, namespaces: Mapping[str, str]) -> tuple[str, str] | None:
    if not value or not value.strip():
        return None
    text = value.strip()
    if ":" in text:
        prefix, local = text.split(":", 1)
        namespace = namespaces.get(prefix)
        if namespace is None:
            return None
        return namespace, local
    namespace = namespaces.get("")
    return (namespace, text) if namespace is not None else None


def _parse_instance_xml(instance: bytes) -> tuple[ET.Element, dict[str, str]]:
    namespaces: dict[str, str] = {}
    try:
        parser = ET.iterparse(BytesIO(instance), events=("start", "start-ns"))
        root: ET.Element | None = None
        for event, value in parser:
            if event == "start-ns":
                prefix, namespace = value
                previous_namespace = namespaces.get(prefix)
                if previous_namespace is not None and previous_namespace != namespace:
                    raise XBRLError(
                        f"XBRL namespace prefix {prefix!r} is rebound within the instance"
                    )
                namespaces[prefix] = namespace
            elif root is None:
                root = value
        if root is None:
            raise XBRLError("XBRL instance is empty")
    except (ET.ParseError, UnicodeDecodeError) as exc:
        raise XBRLError("XBRL instance XML is malformed") from exc
    if root.tag != _xml_tag(XBRLI_NAMESPACE, "xbrl"):
        raise XBRLError("XBRL instance root must be the exact xbrli:xbrl element")
    return root, namespaces


def _context_dimensions(context: ET.Element, namespaces: Mapping[str, str]) -> tuple[str, ...]:
    entity = _children(context, XBRLI_NAMESPACE, "entity")
    period = _children(context, XBRLI_NAMESPACE, "period")
    scenario = _children(context, XBRLI_NAMESPACE, "scenario")
    if len(entity) != 1 or len(period) != 1 or len(scenario) != 1:
        raise XBRLError("XBRL base context has invalid xbrli structure")
    if _children(entity[0], XBRLI_NAMESPACE, "segment"):
        raise XBRLError("XBRL base context contains an extra entity dimension")
    members = list(scenario[0])
    if len(members) != 1 or members[0].tag != _xml_tag(XBRLDI_NAMESPACE, "explicitMember"):
        raise XBRLError("XBRL base context has extra or non-standard dimensions")
    dimension = _resolve_qname(members[0].attrib.get("dimension"), namespaces)
    member = _resolve_qname(members[0].text, namespaces)
    expected_pairs = {
        (namespace, CONSOLIDATION_AXIS, namespace, CONSOLIDATED_MEMBER)
        for namespace in PERMITTED_IFRS_NAMESPACES
    }
    if dimension is None or member is None or (
        dimension[0], dimension[1], member[0], member[1]
    ) not in expected_pairs:
        raise XBRLError("XBRL base context consolidation axis/member is not exact")
    return (f"{CONSOLIDATION_AXIS}={CONSOLIDATED_MEMBER}",)


def _accepted_contexts(
    root: ET.Element,
    corp_code: str,
    namespaces: Mapping[str, str],
) -> dict[str, str]:
    accepted: dict[str, str] = {}
    contexts = [element for element in root if element.tag == _xml_tag(XBRLI_NAMESPACE, "context")]
    for context in contexts:
        context_id = context.attrib.get("id")
        if not context_id:
            continue
        entity = _children(context, XBRLI_NAMESPACE, "entity")
        identifiers = _children(entity[0], XBRLI_NAMESPACE, "identifier") if len(entity) == 1 else []
        if (
            len(identifiers) != 1
            or identifiers[0].attrib.get("scheme") not in PERMITTED_ENTITY_SCHEMES
            or (identifiers[0].text or "").strip() != corp_code
        ):
            continue
        periods = _children(context, XBRLI_NAMESPACE, "period")
        if len(periods) != 1:
            continue
        period = periods[0]
        starts = _children(period, XBRLI_NAMESPACE, "startDate")
        ends = _children(period, XBRLI_NAMESPACE, "endDate")
        instants = _children(period, XBRLI_NAMESPACE, "instant")
        period_kind: str | None = None
        period_ok = (
            len(starts) == 1 and len(ends) == 1
            and (starts[0].text or "").strip() == EXPECTED_START_DATE
            and (ends[0].text or "").strip() == EXPECTED_END_DATE
        )
        if period_ok:
            period_kind = "duration"
        elif len(instants) == 1 and (instants[0].text or "").strip() == EXPECTED_INSTANT_DATE:
            period_ok = True
            period_kind = "instant"
        if not period_ok:
            continue
        try:
            dimensions = _context_dimensions(context, namespaces)
        except XBRLError:
            continue
        if dimensions != (f"{CONSOLIDATION_AXIS}={CONSOLIDATED_MEMBER}",):
            continue
        if period_kind is not None:
            accepted[context_id] = period_kind
    return accepted


def _unit_ids(root: ET.Element, namespaces: Mapping[str, str]) -> set[str]:
    accepted: set[str] = set()
    for unit in _children(root, XBRLI_NAMESPACE, "unit"):
        unit_id = unit.attrib.get("id")
        measures = _children(unit, XBRLI_NAMESPACE, "measure")
        measure_qname = _resolve_qname(measures[0].text, namespaces) if len(measures) == 1 else None
        if unit_id and len(measures) == 1 and measure_qname == (ISO4217_NAMESPACE, EXPECTED_CURRENCY):
            accepted.add(unit_id)
    return accepted


def _numeric_text(value: str) -> tuple[str, int | str]:
    raw = value.strip()
    if not raw:
        raise XBRLError("required XBRL fact has an empty value")
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise XBRLError(f"required XBRL fact has a non-numeric value: {raw!r}") from exc
    if not number.is_finite():
        raise XBRLError("required XBRL fact is not finite")
    numeric = format(number, "f")
    if "." in numeric:
        numeric = numeric.rstrip("0").rstrip(".") or "0"
    return raw, int(numeric) if number == number.to_integral_value() else numeric


def _parse_instance(
    instance: bytes,
    *,
    corp_code: str,
    rcept_no: str,
    bsns_year: int,
    reprt_code: str,
    payload_sha256: str,
) -> tuple[dict[str, Any], ...]:
    if bsns_year != 2014 or reprt_code != EXPECTED_REPRT_CODE:
        raise XBRLError("this FY2014 normalizer accepts only bsns_year=2014 and reprt_code=11011")
    root, namespaces = _parse_instance_xml(instance)
    context_ids = _accepted_contexts(root, corp_code, namespaces)
    if not context_ids:
        raise XBRLError("XBRL instance has no exact FY2014 base consolidated context")
    units = _unit_ids(root, namespaces)
    if not units:
        raise XBRLError("XBRL instance has no KRW unit")
    candidates: dict[str, list[tuple[ET.Element, str, int | str]]] = {
        key: [] for key, *_ in _SEMANTIC_FACTS
    }
    for fact in root.iter():
        if not isinstance(fact.tag, str):
            continue
        if _namespace(fact.tag) not in PERMITTED_IFRS_NAMESPACES:
            continue
        semantic_info = _SEMANTIC_BY_LOCAL.get(_local_name(fact.tag))
        if semantic_info is None:
            continue
        context_ref = fact.attrib.get("contextRef")
        if context_ref not in context_ids:
            continue
        unit_ref = fact.attrib.get("unitRef")
        if unit_ref not in units:
            raise XBRLError(f"required XBRL fact {_local_name(fact.tag)} does not use KRW")
        raw_value, numeric_value = _numeric_text(fact.text or "")
        semantic, display_name, _ = semantic_info
        expected_period = "instant" if semantic in {"total_assets", "total_equity"} else "duration"
        if context_ids[context_ref] != expected_period:
            continue
        candidates[semantic].append((fact, raw_value, numeric_value))
    missing = [semantic for semantic, values in candidates.items() if not values]
    if missing:
        raise XBRLError("XBRL instance is missing required facts: " + ", ".join(missing))
    ambiguous = [semantic for semantic, values in candidates.items() if len(values) != 1]
    if ambiguous:
        raise XBRLError("XBRL instance has ambiguous required facts: " + ", ".join(ambiguous))

    rows: list[dict[str, Any]] = []
    for ordinal, (semantic, local_names, display_name, sj_div) in enumerate(_SEMANTIC_FACTS, start=1):
        fact, raw_value, numeric_value = candidates[semantic][0]
        account_id = _concept_identity(fact.tag)
        if _local_name(fact.tag) not in local_names:
            raise XBRLError(f"unexpected XBRL concept selected for {semantic}")
        period_end = EXPECTED_INSTANT_DATE if sj_div == "BS" else EXPECTED_END_DATE
        rows.append({
            "rcept_no": rcept_no,
            "corp_code": corp_code,
            "bsns_year": str(bsns_year),
            "reprt_code": reprt_code,
            "fs_div": EXPECTED_FS_DIV,
            "sj_div": sj_div,
            "account_id": account_id,
            "account_name": display_name,
            "account_detail": BASE_ACCOUNT_DETAIL,
            "ord": str(ordinal),
            "period_end": period_end,
            "raw_value": raw_value,
            "numeric_value": numeric_value,
            "currency": EXPECTED_CURRENCY,
            "payload_sha256": payload_sha256,
        })
    return tuple(rows)


def parse_xbrl_facts(
    raw_xbrl_path: str | Path,
    acquisition_manifest: Mapping[str, Any] | str | Path,
    *,
    corp_code: str,
    rcept_no: str | None = None,
    bsns_year: int = 2014,
    reprt_code: str = EXPECTED_REPRT_CODE,
) -> XBRLNormalization:
    """Verify and normalise one receipt-keyed FY2014 XBRL archive."""
    raw_path = Path(raw_xbrl_path).resolve()
    evidence = verify_xbrl_acquisition(raw_path, acquisition_manifest)
    params = evidence["request_params"]
    expected_rcept = rcept_no or params["rcept_no"]
    if expected_rcept != params["rcept_no"] or reprt_code != params["reprt_code"]:
        raise XBRLError("normalizer request identity does not match acquisition request_params")
    member_name, instance = _read_xbrl_instance(raw_path.read_bytes())
    facts = _parse_instance(
        instance,
        corp_code=corp_code,
        rcept_no=expected_rcept,
        bsns_year=bsns_year,
        reprt_code=reprt_code,
        payload_sha256=evidence["raw_sha256"],
    )
    return XBRLNormalization(
        facts=facts,
        raw_xbrl_path=str(raw_path),
        raw_xbrl_sha256=evidence["raw_sha256"],
        acquisition_manifest_path=evidence["manifest_path"],
        acquisition_manifest_sha256=evidence["manifest_sha256"],
        request_params=params,
        request_params_sha256=request_params_sha256(params),
        xbrl_instance_member=member_name,
        xbrl_instance_sha256=sha256_bytes(instance),
    )


def normalize_xbrl_facts(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Convenience API returning only canonical fact rows."""
    return [dict(row) for row in parse_xbrl_facts(*args, **kwargs).facts]


def _artifact_bytes(facts: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> bytes:
    return (json.dumps(list(facts), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def write_xbrl_artifact(
    normalization: XBRLNormalization,
    artifact_path: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    """Write canonical JSON facts and a distinct recursive derivation manifest."""
    artifact = Path(artifact_path).resolve()
    derived_manifest_path = Path(manifest_path).resolve()
    artifact.parent.mkdir(parents=True, exist_ok=True)
    derived_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_bytes = _artifact_bytes(normalization.facts)
    artifact.write_bytes(artifact_bytes)
    artifact_hash = sha256_bytes(artifact_bytes)
    if normalization.acquisition_manifest_path is None:
        raise XBRLError("writing a derived artifact requires an acquisition manifest path")
    manifest_payload: dict[str, Any] = {
        "manifest_version": DERIVED_XBRL_MANIFEST_VERSION,
        "source_url": XBRL_ENDPOINT,
        "source_type": DERIVED_XBRL_SOURCE_TYPE,
        "raw_payload_path": str(artifact),
        "raw_file_sha256": artifact_hash,
        "response_sha256": artifact_hash,
        "artifact_path": str(artifact),
        "artifact_sha256": artifact_hash,
        "api_status": "000",
        "verified": True,
        "response_format": "derived-json",
        "response_status": "success",
        "pagination": {"complete": True},
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "request_params": dict(normalization.request_params),
        "request_params_sha256": normalization.request_params_sha256,
        "corp_code": normalization.facts[0]["corp_code"],
        "bsns_year": normalization.facts[0]["bsns_year"],
        "reprt_code": normalization.facts[0]["reprt_code"],
        "raw_xbrl_path": normalization.raw_xbrl_path,
        "raw_xbrl_sha256": normalization.raw_xbrl_sha256,
        "acquisition_manifest_path": normalization.acquisition_manifest_path,
        "acquisition_manifest_sha256": normalization.acquisition_manifest_sha256,
        "xbrl_instance_member": normalization.xbrl_instance_member,
        "xbrl_instance_sha256": normalization.xbrl_instance_sha256,
        "parser_version": PARSER_VERSION,
        "derivation": "fnlttXbrl.xml ZIP -> canonical financial facts",
    }
    derived_manifest_path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_payload


def verify_derived_xbrl_manifest(
    manifest: Mapping[str, Any],
    *,
    artifact_path: str | Path,
) -> None:
    """Recursively verify an emitted artifact's XBRL derivation chain."""
    if manifest.get("manifest_version") != DERIVED_XBRL_MANIFEST_VERSION:
        raise XBRLError("derived XBRL manifest version is unsupported")
    artifact = Path(artifact_path).resolve()
    if not artifact.is_file():
        raise XBRLError("derived XBRL artifact is missing")
    artifact_hash = sha256_file(artifact)
    if manifest.get("parser_version") != PARSER_VERSION:
        raise XBRLError("derived XBRL parser version is unsupported")
    if manifest.get("source_url") != XBRL_ENDPOINT or manifest.get("source_type") != DERIVED_XBRL_SOURCE_TYPE:
        raise XBRLError("derived XBRL manifest endpoint/source type is invalid")
    if manifest.get("raw_payload_path") != str(artifact):
        raise XBRLError("derived XBRL manifest artifact path does not match loaded artifact")
    for key in ("raw_file_sha256", "response_sha256", "artifact_sha256"):
        if manifest.get(key) != artifact_hash:
            raise XBRLError(f"derived XBRL manifest {key} does not match artifact bytes")
    if not _success_status(manifest.get("api_status")) or manifest.get("verified") is not True:
        raise XBRLError("derived XBRL manifest is not successful and verified")
    if manifest.get("response_format") != "derived-json" or manifest.get("response_status") != "success":
        raise XBRLError("derived XBRL manifest response status is invalid")
    params = _verify_request_identity(manifest)
    corp_code = manifest.get("corp_code")
    if not isinstance(corp_code, str) or not corp_code:
        raise XBRLError("derived XBRL corp_code is missing")
    bsns_year = manifest.get("bsns_year")
    reprt_code = manifest.get("reprt_code")
    if str(bsns_year) != "2014" or reprt_code != "11011":
        raise XBRLError("derived XBRL fiscal identity is invalid")
    raw_path = _resolved_path(manifest.get("raw_xbrl_path"))
    acq_path = _resolved_path(manifest.get("acquisition_manifest_path"))
    if raw_path is None or acq_path is None or not raw_path.is_file() or not acq_path.is_file():
        raise XBRLError("derived XBRL manifest raw/acquisition paths are missing")
    if manifest.get("raw_xbrl_sha256") != sha256_file(raw_path):
        raise XBRLError("derived XBRL raw ZIP hash does not match bytes")
    declared_acq_hash = manifest.get("acquisition_manifest_sha256")
    if not _valid_hash(declared_acq_hash) or str(declared_acq_hash).lower() != sha256_file(acq_path):
        raise XBRLError("derived XBRL acquisition manifest hash does not match bytes")
    acq = verify_xbrl_acquisition(raw_path, acq_path)
    if acq["request_params"] != params:
        raise XBRLError("derived XBRL request identity differs from acquisition evidence")
    if not manifest.get("xbrl_instance_member") or not _valid_hash(manifest.get("xbrl_instance_sha256")):
        raise XBRLError("derived XBRL instance evidence is incomplete")
    member_name, instance = _read_xbrl_instance(raw_path.read_bytes())
    if member_name != manifest.get("xbrl_instance_member"):
        raise XBRLError("derived XBRL instance member does not match raw ZIP")
    if sha256_bytes(instance) != str(manifest["xbrl_instance_sha256"]).lower():
        raise XBRLError("derived XBRL instance hash does not match raw ZIP")
    try:
        artifact_rows = json.loads(artifact.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise XBRLError("derived XBRL artifact JSON is invalid") from exc
    if not isinstance(artifact_rows, list) or not all(isinstance(row, dict) for row in artifact_rows):
        raise XBRLError("derived XBRL artifact must be a JSON fact list")
    canonical_bytes = _artifact_bytes(artifact_rows)
    if canonical_bytes != artifact.read_bytes():
        raise XBRLError("derived XBRL artifact bytes are not canonical")
    expected = parse_xbrl_facts(
        raw_path,
        acq_path,
        corp_code=corp_code,
        rcept_no=params["rcept_no"],
        bsns_year=2014,
        reprt_code="11011",
    )
    if _artifact_bytes(expected.facts) != artifact.read_bytes():
        raise XBRLError("derived XBRL artifact facts do not match parser output")


__all__ = [
    "BASE_ACCOUNT_DETAIL",
    "DERIVED_XBRL_MANIFEST_VERSION",
    "DERIVED_XBRL_SOURCE_TYPE",
    "EXPECTED_REPRT_CODE",
    "FINANCIAL_FACT_COLUMNS",
    "PARSER_VERSION",
    "XBRL_ENDPOINT",
    "XBRL_SOURCE_TYPE",
    "XBRLError",
    "XBRLNormalization",
    "normalize_xbrl_facts",
    "parse_xbrl_facts",
    "request_params_sha256",
    "sha256_bytes",
    "sha256_file",
    "verify_derived_xbrl_manifest",
    "verify_xbrl_acquisition",
    "write_xbrl_artifact",
]
