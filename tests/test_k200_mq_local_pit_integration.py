"""Focused tests for the opt-in local PIT universe boundary."""

from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from k200_mq.config import K200MQConfig
from k200_mq.data import universe as universe_module
from k200_mq.data.pit_universe import PITUniverseError
from k200_mq.data.provenance import validate_universe_provenance


def _verified_snapshot_source(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "kospi200_snapshots.csv"
    tickers = [f"{number:06d}" for number in range(1, 201)]
    frame = pd.DataFrame({
        "index_code": ["KOSPI200"] * len(tickers),
        "as_of_date": ["2024-01-31"] * len(tickers),
        "security_code": tickers,
        "source_type": ["KRX historical file"] * len(tickers),
        "source_url": ["https://global.krx.co.kr/synthetic"] * len(tickers),
        "source_file_sha256": ["a" * 64] * len(tickers),
        "retrieved_at_utc": ["2024-02-01T00:00:00Z"] * len(tickers),
    })
    frame.to_csv(source, index=False)
    manifest = {
        "official_source_url": "https://global.krx.co.kr/synthetic",
        "query_params": {"index": "KOSPI200"},
        "date_params": {"as_of": "2024-01-31"},
        "retrieved_at_utc": "2024-02-01T00:00:00+00:00",
        "raw_file_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_type": "krx_official_snapshot",
        "source_is_krx": True,
    }
    manifest_path = tmp_path / "kospi200_snapshots.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return source, manifest_path


def _bundle_member_source(tmp_path: Path, as_of: str, prefix: str) -> tuple[Path, Path]:
    source = tmp_path / f"{prefix}_{as_of}.parquet"
    tickers = [f"{number:06d}" for number in range(1, 201)]
    frame = pd.DataFrame({
        "index_code": ["KOSPI200"] * len(tickers),
        "as_of_date": [as_of] * len(tickers),
        "security_code": tickers,
        "source_type": ["krx_official_snapshot"] * len(tickers),
        "source_url": ["https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"] * len(tickers),
        "source_file_sha256": ["b" * 64] * len(tickers),
        "retrieved_at_utc": ["2024-02-01T00:00:00+00:00"] * len(tickers),
    })
    frame.to_parquet(source, index=False)
    manifest = {
        "official_source_url": "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
        "query_params": {"bld": "dbms/MDC/STAT/standard/MDCSTAT00601", "indIdx": "1", "indIdx2": "028"},
        "date_params": {"trdDd": as_of},
        "retrieved_at_utc": "2024-02-01T00:00:00+00:00",
        "raw_file_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_type": "krx_official_snapshot",
        "source_is_krx": True,
        "snapshot_identity_sha256": hashlib.sha256(
            json.dumps(tickers, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    manifest_path = source.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return source, manifest_path


def test_local_pit_config_is_empty_by_default() -> None:
    config = K200MQConfig()

    assert config.LOCAL_PIT_UNIVERSE_PATH == ""
    assert config.LOCAL_PIT_UNIVERSE_SOURCE_KIND == ""
    assert config.LOCAL_PIT_UNIVERSE_MANIFEST == ""


def test_default_history_does_not_enter_local_pit_branch(monkeypatch) -> None:
    monkeypatch.setattr(
        universe_module,
        "get_kospi200_constituents",
        lambda as_of: universe_module.ConstituentList(["A"], "proxy_current"),
    )

    def fail_local_import(*args, **kwargs):
        raise AssertionError("local PIT importer must not run for the default path")

    monkeypatch.setattr(universe_module, "import_local_pit_universe", fail_local_import)

    history = universe_module.get_kospi200_history(
        date(2024, 1, 1), date(2024, 1, 31), "M",
    )

    assert history["ticker"].tolist() == ["A"]
    assert history.attrs["provenance"] == "proxy_current"


def test_explicit_verified_snapshot_path_is_loaded_at_history_boundary(tmp_path: Path) -> None:
    source, manifest = _verified_snapshot_source(tmp_path)

    history = universe_module.get_kospi200_history(
        date(2024, 1, 1),
        date(2024, 1, 31),
        "M",
        local_pit_universe_path=source,
        local_pit_universe_source_kind="snapshots",
        local_pit_universe_manifest=manifest,
    )

    assert history["as_of"].unique().tolist() == [date(2024, 1, 31)]
    assert len(history) == 200
    assert validate_universe_provenance(history)["pit_valid"] is True


def test_explicit_local_pit_failure_does_not_fall_back_to_proxy(monkeypatch, tmp_path: Path) -> None:
    def fail_local_import(*args, **kwargs):
        raise PITUniverseError("invalid explicit local PIT source")

    def fail_proxy(*args, **kwargs):
        raise AssertionError("proxy loader must not run after explicit PIT failure")

    monkeypatch.setattr(universe_module, "import_local_pit_universe", fail_local_import)
    monkeypatch.setattr(universe_module, "get_kospi200_constituents", fail_proxy)

    with pytest.raises(PITUniverseError, match="invalid explicit local PIT source"):
        universe_module.get_kospi200_history(
            date(2024, 1, 1),
            date(2024, 1, 31),
            "M",
            local_pit_universe_path=tmp_path / "missing.csv",
        )


def test_local_pit_directory_path_is_rejected_with_actionable_error(tmp_path: Path) -> None:
    source_dir = tmp_path / "pit_sources"
    source_dir.mkdir()

    with pytest.raises(PITUniverseError, match="no snapshot files found"):
        universe_module.get_kospi200_history(
            date(2024, 1, 1),
            date(2024, 1, 31),
            "M",
            local_pit_universe_path=source_dir,
            local_pit_universe_source_kind="snapshots",
        )


def test_directory_bundle_with_sidecars_is_pit_valid(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    member_a, _ = _bundle_member_source(bundle_dir, "2024-01-31", "kospi200")
    member_b, _ = _bundle_member_source(bundle_dir, "2024-02-29", "kospi200")
    # Ensure the bundle path contains both members and their sidecars.
    assert member_a.exists() and member_b.exists()

    history = universe_module.get_kospi200_history(
        date(2024, 1, 1),
        date(2024, 2, 29),
        "M",
        local_pit_universe_path=bundle_dir,
        local_pit_universe_source_kind="snapshots",
    )

    assert sorted(history["as_of"].astype(str).unique().tolist()) == ["2024-01-31", "2024-02-29"]
    assert len(history) == 400
    assert validate_universe_provenance(history)["pit_valid"] is True


def test_prepare_inputs_wires_configured_local_pit_options(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def capture_history(start, end, frequency, **kwargs):
        captured.update({"start": start, "end": end, "frequency": frequency, **kwargs})
        raise RuntimeError("stop after boundary capture")

    monkeypatch.setattr(universe_module, "get_kospi200_history", capture_history)
    config = K200MQConfig(
        START_DATE="2024-01-01",
        END_DATE="2024-01-31",
        LOCAL_PIT_UNIVERSE_PATH="local.csv",
        LOCAL_PIT_UNIVERSE_SOURCE_KIND="intervals",
        LOCAL_PIT_UNIVERSE_MANIFEST="local.manifest.json",
    )

    from k200_mq import main as main_module

    with pytest.raises(RuntimeError, match="stop after boundary capture"):
        main_module.prepare_k200mq_inputs(config)

    assert captured == {
        "start": date(2024, 1, 1),
        "end": date(2024, 1, 31),
        "frequency": "M",
        "local_pit_universe_path": "local.csv",
        "local_pit_universe_source_kind": "intervals",
        "local_pit_universe_manifest": "local.manifest.json",
    }
