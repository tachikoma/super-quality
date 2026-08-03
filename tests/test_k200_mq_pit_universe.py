"""Synthetic local-file PIT universe contract tests."""

from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from k200_mq.data.pit_universe import (
    EVENT_COLUMNS,
    SNAPSHOT_COLUMNS,
    PITUniverseError,
    fingerprint_dataframe,
    load_constituent_snapshots,
    load_membership_intervals,
    intervals_to_history,
    snapshots_to_history,
    validate_constituent_snapshots,
    validate_membership_intervals,
)
from k200_mq.data.provenance import validate_universe_provenance


SHA = "a" * 64


def _snapshot_frame(
    *,
    tickers: tuple[object, ...] = ("005930", "000660"),
    as_of: str = "2024-01-31",
) -> pd.DataFrame:
    return pd.DataFrame({
        "index_code": ["KOSPI200"] * len(tickers),
        "as_of_date": [as_of] * len(tickers),
        "security_code": list(tickers),
        "source_type": ["KRX historical file"] * len(tickers),
        "source_url": ["file:///tmp/krx.csv"] * len(tickers),
        "source_file_sha256": [SHA] * len(tickers),
        "retrieved_at_utc": ["2024-02-01T00:00:00Z"] * len(tickers),
    })


def _interval_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "index_code": ["KOSPI200"],
        "effective_from": ["2024-01-01"],
        "effective_to": [None],
        "security_code": ["005930"],
        "action": ["include"],
        "status": ["active"],
        "announcement_date": ["2023-12-20"],
        "provenance": [{"notice": "synthetic"}],
        "source_type": ["KRX historical file"],
        "source_url": ["file:///tmp/krx.csv"],
        "source_file_sha256": [SHA],
        "retrieved_at_utc": ["2024-02-01T00:00:00Z"],
    })


def _manifest(raw_bytes: bytes, *, source_type: str = "krx_official_snapshot") -> dict[str, object]:
    return {
        "official_source_url": "https://global.krx.co.kr/synthetic",
        "query_params": {"index": "KOSPI200"},
        "date_params": {"as_of": "2024-01-31"},
        "retrieved_at_utc": "2024-02-01T00:00:00+00:00",
        "raw_file_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "source_type": source_type,
        "source_is_krx": True,
    }


def _write_source(tmp_path: Path, frame: pd.DataFrame, suffix: str) -> tuple[Path, dict[str, object]]:
    path = tmp_path / f"source{suffix}"
    if suffix == ".csv":
        frame.to_csv(path, index=False)
    elif suffix == ".json":
        path.write_text(json.dumps(frame.to_dict(orient="records")), encoding="utf-8")
    elif suffix == ".parquet":
        frame.to_parquet(path, index=False)
    else:
        raise AssertionError(suffix)
    return path, _manifest(path.read_bytes())


def test_valid_snapshot_normalization_and_legacy_history_contract(tmp_path: Path) -> None:
    raw = _snapshot_frame(tickers=(5930, "660"))
    source, manifest = _write_source(tmp_path, raw, ".csv")
    snapshots = load_constituent_snapshots(source, acquisition_manifest=manifest)

    assert list(snapshots.columns) == list(SNAPSHOT_COLUMNS)
    assert snapshots["security_code"].tolist() == ["005930", "000660"]
    report = validate_constituent_snapshots(
        snapshots,
        requested_rebalance_date=date(2024, 1, 31),
        target_size=2,
    )
    assert report.pit_valid is True

    history = snapshots_to_history(
        snapshots,
        requested_rebalance_date=date(2024, 1, 31),
        target_size=2,
    )
    assert list(history.columns) == ["as_of", "ticker"]
    assert history["ticker"].tolist() == ["000660", "005930"]
    assert validate_universe_provenance(history)["pit_valid"] is True


def test_history_evidence_token_is_bound_to_rows_and_per_date_metadata(
    tmp_path: Path,
) -> None:
    source, manifest = _write_source(tmp_path, _snapshot_frame(), ".csv")
    snapshots = load_constituent_snapshots(source, acquisition_manifest=manifest)
    history = snapshots_to_history(
        snapshots,
        requested_rebalance_date=date(2024, 1, 31),
        target_size=2,
    )
    assert validate_universe_provenance(history)["pit_valid"] is True

    altered_rows = history.copy(deep=True)
    altered_rows.attrs = dict(history.attrs)
    altered_rows.loc[0, "ticker"] = "000270"
    assert validate_universe_provenance(altered_rows)["pit_valid"] is False

    altered_metadata = history.copy(deep=True)
    altered_metadata.attrs = dict(history.attrs)
    metadata = dict(altered_metadata.attrs["provenance_metadata_by_as_of"])
    metadata["2024-01-31"] = dict(metadata["2024-01-31"])
    metadata["2024-01-31"]["retrieved_at_utc"] = "2024-02-02T00:00:00+00:00"
    altered_metadata.attrs["provenance_metadata_by_as_of"] = metadata
    assert validate_universe_provenance(altered_metadata)["pit_valid"] is False

    other_source, other_manifest = _write_source(
        tmp_path,
        _snapshot_frame(as_of="2024-02-29"),
        ".csv",
    )
    other_snapshots = load_constituent_snapshots(
        other_source,
        acquisition_manifest=other_manifest,
    )
    other_history = snapshots_to_history(
        other_snapshots,
        requested_rebalance_date=date(2024, 2, 29),
        target_size=2,
    )
    other_history.attrs = dict(other_history.attrs)
    other_history.attrs["_verified_acquisition"] = history.attrs["_verified_acquisition"]
    assert validate_universe_provenance(other_history)["pit_valid"] is False


def test_missing_provenance_is_rejected_without_a_caller_pit_boolean() -> None:
    raw = _snapshot_frame().drop(columns=["source_url"])
    snapshots = load_constituent_snapshots(raw)
    assert validate_constituent_snapshots(snapshots).pit_valid is False

    missing_hash = _snapshot_frame().drop(columns=["source_file_sha256"])
    assert validate_constituent_snapshots(load_constituent_snapshots(missing_hash)).pit_valid is False


def test_duplicate_rows_are_invalid() -> None:
    snapshots = load_constituent_snapshots(pd.concat([_snapshot_frame(), _snapshot_frame()]))

    report = validate_constituent_snapshots(snapshots, target_size=2)

    assert report.pit_valid is False
    assert any("duplicate" in error for error in report.errors)


def test_bad_ticker_and_date_are_invalid_after_normalization() -> None:
    snapshots = load_constituent_snapshots(
        _snapshot_frame(tickers=("not-a-ticker",), as_of="not-a-date"),
    )

    report = validate_constituent_snapshots(snapshots, target_size=1)

    assert report.pit_valid is False
    assert any("security_code" in error for error in report.errors)
    assert any("as_of_date" in error for error in report.errors)


def test_future_effective_snapshot_is_rejected_for_requested_rebalance() -> None:
    snapshots = load_constituent_snapshots(_snapshot_frame(as_of="2024-02-29"))

    report = validate_constituent_snapshots(
        snapshots,
        requested_rebalance_date=date(2024, 1, 31),
        target_size=2,
    )

    assert report.pit_valid is False
    assert any("after a requested" in error for error in report.errors)


def test_transition_size_requires_an_explicit_exception(tmp_path: Path) -> None:
    source, manifest = _write_source(tmp_path, _snapshot_frame(tickers=("005930",)), ".csv")
    snapshots = load_constituent_snapshots(source, acquisition_manifest=manifest)

    rejected = validate_constituent_snapshots(snapshots, target_size=2)
    accepted = validate_constituent_snapshots(
        snapshots,
        target_size=2,
        transition_exceptions={date(2024, 1, 31): {
            "allowed_sizes": [1],
            "reason": "synthetic documented transition",
        }},
    )

    assert rejected.pit_valid is False
    assert accepted.pit_valid is True
    diagnostics = dict(accepted.diagnostics or {})
    assert diagnostics["snapshot_sizes"]["2024-01-31"]["transition_exception"] is True


def test_intervals_reject_overlap_and_accept_adjacent_ranges(tmp_path: Path) -> None:
    source, manifest = _write_source(tmp_path, _interval_frame(), ".csv")
    intervals = load_membership_intervals(source, acquisition_manifest=manifest)
    closed = intervals.assign(effective_to=pd.Timestamp("2024-02-01").date())
    adjacent = pd.concat([
        closed,
        closed.assign(
            effective_from=pd.Timestamp("2024-02-01").date(),
            effective_to=pd.Timestamp("2024-03-01").date(),
        ),
    ], ignore_index=True)
    overlapping = pd.concat([
        intervals,
        intervals.assign(
            effective_from=pd.Timestamp("2024-01-15").date(),
            effective_to=pd.Timestamp("2024-02-15").date(),
        ),
    ], ignore_index=True)

    assert validate_membership_intervals(adjacent).pit_valid is False
    assert not any("overlapping" in error for error in validate_membership_intervals(adjacent).errors)
    report = validate_membership_intervals(overlapping)
    assert report.pit_valid is False
    assert any("overlapping" in error for error in report.errors)


def test_fingerprints_are_deterministic_for_equivalent_frames() -> None:
    first = _snapshot_frame()
    second = first.iloc[::-1].reset_index(drop=True)

    assert fingerprint_dataframe(first) == fingerprint_dataframe(second)


def test_explicit_mapping_rejects_ambiguous_aliases() -> None:
    raw = _snapshot_frame().rename(columns={"as_of_date": "Date"})
    raw["effective_date"] = raw["Date"]

    with pytest.raises(PITUniverseError, match="ambiguous"):
        load_constituent_snapshots(raw)


def test_interval_schema_is_canonical(tmp_path: Path) -> None:
    source, manifest = _write_source(tmp_path, _interval_frame(), ".csv")
    intervals = load_membership_intervals(source, acquisition_manifest=manifest)

    assert list(intervals.columns) == list(EVENT_COLUMNS)
    assert validate_membership_intervals(
        intervals,
        requested_rebalance_date=date(2024, 1, 31),
    ).pit_valid is True


def test_invalid_nonempty_effective_to_cannot_become_open_ended_pit_data(
    tmp_path: Path,
) -> None:
    raw = _interval_frame()
    raw["effective_to"] = ["not-a-date"]
    source, manifest = _write_source(tmp_path, raw, ".csv")

    intervals = load_membership_intervals(source, acquisition_manifest=manifest)

    assert intervals.attrs["acquisition_manifest_verified"] is True
    assert intervals.loc[0, "effective_to"] == "not-a-date"
    report = validate_membership_intervals(intervals)
    assert report.pit_valid is False
    assert any("effective_to contains invalid dates" in error for error in report.errors)


def test_intervals_materialize_deterministic_legacy_history(tmp_path: Path) -> None:
    source, manifest = _write_source(tmp_path, _interval_frame(), ".csv")
    intervals = load_membership_intervals(source, acquisition_manifest=manifest)

    history = intervals_to_history(
        intervals,
        [date(2024, 1, 31)],
        target_size=1,
    )

    assert list(history.columns) == ["as_of", "ticker"]
    assert history["ticker"].tolist() == ["005930"]
    assert validate_universe_provenance(history)["pit_valid"] is True


@pytest.mark.parametrize("suffix", [".csv", ".json", ".parquet"])
def test_supported_file_formats_require_byte_verified_manifest(tmp_path: Path, suffix: str) -> None:
    source, manifest = _write_source(tmp_path, _snapshot_frame(), suffix)

    snapshots = load_constituent_snapshots(source, acquisition_manifest=manifest)

    assert validate_constituent_snapshots(
        snapshots,
        requested_rebalance_date="2024-01-31",
        target_size=2,
    ).pit_valid is True


def test_bytes_fingerprint_is_verified_against_raw_bytes() -> None:
    raw_bytes = _snapshot_frame().to_csv(index=False).encode("utf-8")
    snapshots = load_constituent_snapshots(
        raw_bytes,
        source_format="csv",
        acquisition_manifest=_manifest(raw_bytes),
    )

    assert validate_constituent_snapshots(snapshots, target_size=2).pit_valid is True


def test_manifest_is_loaded_from_a_separate_sidecar_file(tmp_path: Path) -> None:
    source, manifest = _write_source(tmp_path, _snapshot_frame(), ".csv")
    sidecar = tmp_path / "source.manifest.json"
    sidecar.write_text(json.dumps(manifest), encoding="utf-8")

    snapshots = load_constituent_snapshots(source, acquisition_manifest=sidecar)

    assert validate_constituent_snapshots(snapshots, target_size=2).pit_valid is True


def test_dataframe_and_self_supplied_hash_remain_unverified() -> None:
    raw = _snapshot_frame()
    raw.attrs["pit_valid"] = True
    raw.attrs["provenance"] = "pit"

    snapshots = load_constituent_snapshots(raw)
    report = validate_constituent_snapshots(snapshots, target_size=2)

    assert report.pit_valid is False
    assert report.pit_candidate is True
    assert report.provenance == "pit_candidate"


def test_manifest_hash_must_match_source_bytes(tmp_path: Path) -> None:
    source, manifest = _write_source(tmp_path, _snapshot_frame(), ".csv")
    manifest["raw_file_sha256"] = "a" * 64

    with pytest.raises(PITUniverseError, match="does not match source bytes"):
        load_constituent_snapshots(source, acquisition_manifest=manifest)


def test_manifest_rejects_local_url_naive_timestamp_and_unallowlisted_source(tmp_path: Path) -> None:
    source, manifest = _write_source(tmp_path, _snapshot_frame(), ".csv")
    for field, value, message in [
        ("official_source_url", "file:///tmp/krx.csv", "official HTTPS KRX"),
        ("retrieved_at_utc", "2024-02-01T00:00:00", "timezone-aware"),
        ("source_type", "self_supplied", "source_type"),
    ]:
        invalid = dict(manifest)
        invalid[field] = value
        with pytest.raises(PITUniverseError, match=message):
            load_constituent_snapshots(source, acquisition_manifest=invalid)


def test_manifest_requires_explicit_krx_attestation(tmp_path: Path) -> None:
    source, manifest = _write_source(tmp_path, _snapshot_frame(), ".csv")
    manifest.pop("source_is_krx")

    with pytest.raises(PITUniverseError, match="attestation"):
        load_constituent_snapshots(source, acquisition_manifest=manifest)


def test_strict_dates_reject_numeric_epoch_like_values() -> None:
    raw = _snapshot_frame(as_of="20240131")
    raw["as_of_date"] = [20240131, 20240131]

    snapshots = load_constituent_snapshots(raw)

    assert validate_constituent_snapshots(snapshots).pit_valid is False
    assert any("as_of_date" in error for error in validate_constituent_snapshots(snapshots).errors)


def test_interval_chronology_and_unknown_state_fail_closed() -> None:
    raw = _interval_frame()
    raw.loc[0, "announcement_date"] = "2024-01-02"
    raw.loc[0, "status"] = "active-ish"

    intervals = load_membership_intervals(raw)
    report = validate_membership_intervals(intervals)

    assert report.pit_valid is False
    assert any("unknown action/status" in error for error in report.errors)

    raw.loc[0, "status"] = "active"
    raw.loc[0, "announcement_date"] = "2024-02-02"
    report = validate_membership_intervals(load_membership_intervals(raw))
    assert any("announcement_date" in error for error in report.errors)


def test_event_aliases_are_unsupported() -> None:
    raw = _interval_frame().rename(columns={"action": "event"})

    with pytest.raises(PITUniverseError, match="event/events"):
        load_membership_intervals(raw)

    with pytest.raises(PITUniverseError, match="event/events"):
        from k200_mq.data.pit_universe import import_local_pit_universe

        import_local_pit_universe(raw, source_kind="events")


def test_duplicate_normalized_source_labels_are_rejected() -> None:
    raw = _snapshot_frame()
    raw.columns = ["index_code", "as_of_date", "security_code", "source_type",
                   "source_url", "source_file_sha256", "retrieved_at_utc"]
    duplicate = pd.concat([raw, raw["source_url"]], axis=1)
    duplicate.columns = [*raw.columns, "source url"]

    with pytest.raises(PITUniverseError, match="duplicate normalized"):
        load_constituent_snapshots(duplicate)


def test_bare_true_cannot_disable_snapshot_size_check() -> None:
    snapshots = load_constituent_snapshots(_snapshot_frame(tickers=("005930",)))

    with pytest.raises(PITUniverseError, match="documented policy"):
        validate_constituent_snapshots(
            snapshots,
            target_size=2,
            transition_exceptions={"2024-01-31": True},
        )
