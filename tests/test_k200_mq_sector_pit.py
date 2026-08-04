"""Tests for local PIT sector mapping contract scaffolding."""

from __future__ import annotations

from datetime import date

import pandas as pd

from k200_mq.data.sector_pit import (
    SECTOR_INTERVAL_COLUMNS,
    load_sector_intervals,
    sector_map_as_of,
    sector_map_fingerprint,
    validate_sector_intervals,
)


def _base_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "ticker": ["005930", "000660"],
        "sector": ["IT", "IT"],
        "effective_from": ["2024-01-01", "2024-01-01"],
        "effective_to": [None, None],
        "source_type": ["krx_official_snapshot", "krx_official_snapshot"],
        "source_url": ["https://data.krx.co.kr/mock", "https://data.krx.co.kr/mock"],
        "source_file_sha256": ["a" * 64, "b" * 64],
        "retrieved_at_utc": ["2024-02-01T00:00:00Z", "2024-02-01T00:00:00Z"],
    })


def test_load_sector_intervals_normalizes_schema_and_order() -> None:
    intervals = load_sector_intervals(_base_frame())

    assert tuple(intervals.columns) == SECTOR_INTERVAL_COLUMNS
    assert intervals["ticker"].tolist() == ["000660", "005930"]


def test_validate_sector_intervals_accepts_valid_contract() -> None:
    intervals = load_sector_intervals(_base_frame())

    result = validate_sector_intervals(intervals)

    assert result.pit_valid is True
    assert result.errors == ()


def test_validate_sector_intervals_rejects_overlap() -> None:
    raw = pd.DataFrame({
        "ticker": ["005930", "005930"],
        "sector": ["IT", "IT"],
        "effective_from": ["2024-01-01", "2024-01-15"],
        "effective_to": ["2024-02-01", "2024-03-01"],
        "source_type": ["krx_official_snapshot", "krx_official_snapshot"],
        "source_url": ["https://data.krx.co.kr/mock", "https://data.krx.co.kr/mock"],
        "source_file_sha256": ["a" * 64, "b" * 64],
        "retrieved_at_utc": ["2024-02-01T00:00:00Z", "2024-02-01T00:00:00Z"],
    })
    intervals = load_sector_intervals(raw)

    result = validate_sector_intervals(intervals)

    assert result.pit_valid is False
    assert any("overlapping" in error for error in result.errors)


def test_sector_map_as_of_uses_half_open_intervals() -> None:
    raw = pd.DataFrame({
        "ticker": ["005930", "005930"],
        "sector": ["IT", "INDUSTRIAL"],
        "effective_from": ["2024-01-01", "2024-03-01"],
        "effective_to": ["2024-03-01", None],
        "source_type": ["krx_official_snapshot", "krx_official_snapshot"],
        "source_url": ["https://data.krx.co.kr/mock", "https://data.krx.co.kr/mock"],
        "source_file_sha256": ["a" * 64, "b" * 64],
        "retrieved_at_utc": ["2024-02-01T00:00:00Z", "2024-03-02T00:00:00Z"],
    })
    intervals = load_sector_intervals(raw)

    before = sector_map_as_of(intervals, date(2024, 2, 29))
    on_boundary = sector_map_as_of(intervals, date(2024, 3, 1))

    assert before["005930"] == "IT"
    assert on_boundary["005930"] == "INDUSTRIAL"


def test_sector_map_fingerprint_is_stable_for_same_content() -> None:
    first = {"005930": "IT", "000660": "IT"}
    second = {"000660": "IT", "005930": "IT"}

    assert sector_map_fingerprint(first) == sector_map_fingerprint(second)
