"""Preparation-path tests for optional PIT sector-map artifacts."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from k200_mq.config import K200MQConfig
from k200_mq.main import _prepare_sector_map_artifacts


def _universe_history() -> pd.DataFrame:
    return pd.DataFrame({
        "as_of": [pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-29")],
        "ticker": ["005930", "005930"],
    })


def _sector_csv(tmp_path: Path) -> Path:
    frame = pd.DataFrame({
        "ticker": ["005930"],
        "sector": ["IT"],
        "effective_from": ["2024-01-01"],
        "effective_to": [None],
        "source_type": ["krx_official_snapshot"],
        "source_url": ["https://data.krx.co.kr/mock"],
        "source_file_sha256": ["a" * 64],
        "retrieved_at_utc": ["2024-02-01T00:00:00Z"],
    })
    path = tmp_path / "sector_intervals.csv"
    frame.to_csv(path, index=False)
    return path


def test_sector_map_artifacts_are_disabled_when_not_configured() -> None:
    sector_map, context = _prepare_sector_map_artifacts(K200MQConfig(), _universe_history())

    assert sector_map == {}
    assert context["status"] == "disabled"
    assert context["source"] == "not_configured"


def test_sector_map_artifacts_load_and_cover_universe_rows(tmp_path: Path) -> None:
    sector_path = _sector_csv(tmp_path)
    config = K200MQConfig(LOCAL_PIT_SECTOR_PATH=str(sector_path))

    sector_map, context = _prepare_sector_map_artifacts(config, _universe_history())

    assert context["status"] == "loaded"
    assert context["pit_valid"] is True
    assert context["coverage_ratio"] == pytest.approx(1.0)
    assert sector_map["2024-01-31"]["005930"] == "IT"
    assert sector_map["2024-02-29"]["005930"] == "IT"


def test_sector_map_artifacts_fail_closed_on_invalid_intervals(tmp_path: Path) -> None:
    bad = pd.DataFrame({
        "ticker": ["005930"],
        "sector": [""],
        "effective_from": ["not-a-date"],
    })
    path = tmp_path / "bad_sector_intervals.csv"
    bad.to_csv(path, index=False)

    config = K200MQConfig(LOCAL_PIT_SECTOR_PATH=str(path))
    with pytest.raises(RuntimeError, match="LOCAL_PIT_SECTOR_PATH failed PIT sector validation"):
        _prepare_sector_map_artifacts(config, _universe_history())
