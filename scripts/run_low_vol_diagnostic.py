#!/usr/bin/env python
"""Standalone single-run diagnostic of the frozen KOSPI 200 low-volatility spec.

This is a NON-VALIDATED diagnostic.  It runs the registered low-volatility
spec (quarterly signal dates, trailing 252-session volatility, bottom-20%
equal-weight selection, next-session-open execution through the MQ engine's
injected target-provider boundary) on the *adjusted* pykrx OHLCV cache.

Adjusted prices approximate total return, so this is an OPTIMISTIC UPPER-BOUND
proxy for the price-return target.  Every artifact is labelled
``diagnostic_adjusted_price_non_validated`` and the classification is fixed to
that string.  No network calls are made; missing cached price history is
excluded per rebalance with a warning count.

Hard cutoff: any data dated after 2024-12-31 fails closed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from k200_low_vol.factor import LowVolatilityFactor
from k200_low_vol.schedule import krx_quarterly_schedule
from k200_low_vol.selector import LowVolatilitySelector
from k200_low_vol.spec import LowVolSpec
from k200_mq.backtest.portfolio_engine import PortfolioRebalanceEngine
from k200_mq.config import K200MQConfig

CLASSIFICATION = "diagnostic_adjusted_price_non_validated"
CUTOFF = pd.Timestamp("2024-12-31")
PRICE_COLUMNS = ["ticker", "date", "open", "high", "low", "close", "volume", "mcap"]
NEUTRAL_CONFIG_FIELDS = (
    "REGIME_FILTER_ENABLED",
    "CONTINUOUS_REGIME",
    "QUALITY_PRIMARY",
    "ENABLE_ADV_FILTER",
    "ENABLE_CORRELATION_FILTER",
    "ENABLE_SECTOR_CAP",
    "ENABLE_STOP_LOSS",
    "ENABLE_DELISTING_DETECTION",
    "EXCLUDE_KOSPI_TOP_N",
    "REGIME_REDUCTION",
    "MIN_CASH_RATIO",
    "MAX_POSITION_WEIGHT",
    "MAX_HOLDINGS",
    "COMMISSION_RATE",
    "TAX_RATE",
    "SLIPPAGE",
    "INITIAL_CAPITAL",
)


def _fail_closed_if_exceeds_cutoff(ts: pd.Timestamp, label: str) -> None:
    if pd.Timestamp(ts).normalize() > CUTOFF:
        raise RuntimeError(f"{label} exceeds hard cutoff 2024-12-31 (got {ts.date()})")


def load_universe(universe_dir: Path) -> pd.DataFrame:
    """Load every PIT snapshot CSV into a (as_of, ticker) frame."""
    frames: list[pd.DataFrame] = []
    for csv in sorted(universe_dir.glob("kospi200_*.csv")):
        df = pd.read_csv(csv)
        if "as_of_date" not in df.columns or "security_code" not in df.columns:
            continue
        frames.append(
            df[["as_of_date", "security_code"]].rename(
                columns={"as_of_date": "as_of", "security_code": "ticker"}
            )
        )
    if not frames:
        raise RuntimeError(f"no universe snapshots found in {universe_dir}")
    out = pd.concat(frames, ignore_index=True)
    out["as_of"] = pd.to_datetime(out["as_of"], errors="coerce")
    out = out.dropna(subset=["as_of"])
    out["ticker"] = out["ticker"].astype(str).str.strip()
    out = out[out["ticker"].str.len() > 0]
    _fail_closed_if_exceeds_cutoff(out["as_of"].max(), "universe as_of")
    return out


def load_prices(price_dir: Path, years: range) -> pd.DataFrame:
    """Load the adjusted OHLCV parquet cache directly (no network)."""
    frames: list[pd.DataFrame] = []
    for y in years:
        p = price_dir / f"price_{y}.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p, engine="pyarrow")
        df = df[[c for c in PRICE_COLUMNS if c in df.columns]].copy()
        frames.append(df)
    if not frames:
        raise RuntimeError(f"no price cache found in {price_dir}")
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"])
    out["ticker"] = out["ticker"].astype(str).str.strip()
    out = out.drop_duplicates(subset=["ticker", "date"], keep="last")
    _fail_closed_if_exceeds_cutoff(out["date"].max(), "price date")
    out = out[pd.to_datetime(out["date"]).dt.normalize() <= CUTOFF]
    return out


def build_signal_dates(price_panel: pd.DataFrame, spec: LowVolSpec) -> tuple[date, ...]:
    """Last KRX session of each quarter, derived from the cached calendar."""
    dates = pd.to_datetime(price_panel["date"], errors="coerce").dt.normalize()
    cal = sorted(
        {
            d
            for d in dates
            if pd.Timestamp("2015-01-01") <= d <= CUTOFF
        }
    )
    return krx_quarterly_schedule(cal, spec=spec)


def build_universe_data(
    signal_dates: tuple[date, ...], universe: pd.DataFrame
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    """Map each signal date to the latest snapshot <= it; log per-rebalance size."""
    snap_dates = sorted(universe["as_of"].dt.normalize().unique().tolist())
    rows: list[dict[str, object]] = []
    log: list[dict[str, object]] = []
    for sd in signal_dates:
        sd_ts = pd.Timestamp(sd).normalize()
        prior = [d for d in snap_dates if d <= sd_ts]
        if not prior:
            continue
        snap = max(prior)
        members = (
            universe[universe["as_of"] == snap]["ticker"].astype(str).unique().tolist()
        )
        for t in members:
            rows.append({"as_of": sd_ts, "ticker": t})
        log.append(
            {
                "signal_date": sd.isoformat(),
                "snapshot_as_of": snap.date().isoformat(),
                "n_universe": len(members),
            }
        )
    return pd.DataFrame(rows), log


def build_engine_config() -> K200MQConfig:
    """Neutral MQ controls so the frozen low-vol target is never altered."""
    return K200MQConfig(
        ENABLE_STOP_LOSS=False,
        ENABLE_DELISTING_DETECTION=False,
        EXCLUDE_KOSPI_TOP_N=0,
        REGIME_FILTER_ENABLED=False,
        CONTINUOUS_REGIME=False,
        QUALITY_PRIMARY=False,
        ENABLE_ADV_FILTER=False,
        ENABLE_CORRELATION_FILTER=False,
        ENABLE_SECTOR_CAP=False,
        REGIME_REDUCTION=0.0,
        MIN_CASH_RATIO=0.0,
        MAX_POSITION_WEIGHT=1.0,
        MAX_HOLDINGS=10000,
    )


def enrich_rebalance_log(
    log: list[dict[str, object]],
    universe_data: pd.DataFrame,
    factor: pd.DataFrame,
    spec: LowVolSpec,
) -> list[dict[str, object]]:
    """Add eligible / excluded / selected counts per rebalance."""
    f = factor.copy()
    f["_date"] = pd.to_datetime(f["date"], errors="coerce").dt.normalize()
    for entry in log:
        sd = pd.Timestamp(entry["signal_date"]).normalize()
        members = (
            universe_data[universe_data["as_of"] == sd]["ticker"].astype(str).unique()
        )
        eligible = f[(f["_date"] == sd) & (f["ticker"].isin(members))]
        n_eligible = int(eligible["ticker"].nunique())
        n_universe = int(entry["n_universe"])
        n_excluded = n_universe - n_eligible
        n_selected = int(np.floor(n_eligible * spec.bottom_fraction))
        entry["n_eligible"] = n_eligible
        entry["n_excluded"] = n_excluded
        entry["n_selected"] = n_selected
    return log


def compute_metrics(daily_returns: pd.Series) -> dict[str, object]:
    """Stitched total return, CAGR, Sharpe, MDD, Calmar, yearly returns."""
    ret = daily_returns.dropna()
    if ret.empty:
        return {
            "stitched_total_return": 0.0,
            "cagr": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "calmar": 0.0,
            "years": 0.0,
            "yearly_returns": {},
        }
    nav = (1.0 + ret).cumprod()
    total_return = float(nav.iloc[-1] - 1.0)
    start = pd.Timestamp(ret.index[0])
    end = pd.Timestamp(ret.index[-1])
    days = (end - start).days
    years = days / 365.25
    cagr = float(nav.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 else 0.0
    mean = float(ret.mean())
    std = float(ret.std(ddof=1))
    sharpe = float(mean / std * np.sqrt(252)) if std > 0 else 0.0
    peak = nav.cummax()
    dd = nav / peak - 1.0
    mdd = float(dd.min())
    calmar = float(cagr / abs(mdd)) if mdd < 0 else 0.0
    yearly = (1.0 + ret).groupby(ret.index.year).prod() - 1.0
    yearly_map = {int(y): float(v) for y, v in yearly.items()}
    return {
        "stitched_total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "calmar": calmar,
        "years": years,
        "yearly_returns": yearly_map,
    }


def decide(metrics: dict[str, object]) -> dict[str, object]:
    """Apply the pre-declared decision rule."""
    cagr_ok = bool(metrics["cagr"] >= 0.07)
    sharpe_ok = bool(metrics["sharpe"] >= 0.75)
    mdd_ok = bool(metrics["max_drawdown"] > -0.25)
    proceed = cagr_ok and sharpe_ok and mdd_ok
    return {
        "classification": CLASSIFICATION,
        "decision_rule": "CAGR >= 7% AND Sharpe >= 0.75 AND MDD > -25%",
        "cagr_ok": cagr_ok,
        "sharpe_ok": sharpe_ok,
        "mdd_ok": mdd_ok,
        "verdict": "PROCEED_TO_LEDGER" if proceed else "ARCHIVE_PERMANENTLY",
    }


def git_commit_hash(repo_root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def run_diagnostic(
    universe_dir: Path, price_dir: Path, output_dir: Path
) -> dict[str, object]:
    spec = LowVolSpec()
    repo_root = Path(__file__).resolve().parent.parent

    universe = load_universe(universe_dir)
    price_panel = load_prices(price_dir, range(2014, 2025))

    signal_dates = build_signal_dates(price_panel, spec)
    if not signal_dates:
        raise RuntimeError("no quarterly signal dates produced")

    universe_data, rebalance_log = build_universe_data(signal_dates, universe)

    # The factor is computed per-ticker, so restricting to the PIT universe
    # tickers yields identical volatility values while avoiding the full
    # ~71k-ticker price cache.  Only universe members can ever be selected.
    universe_tickers = set(universe["ticker"].astype(str).unique())
    factor_panel = price_panel[price_panel["ticker"].isin(universe_tickers)].copy()
    factor = LowVolatilityFactor(spec).compute(factor_panel)
    if factor.empty:
        raise RuntimeError("factor produced no rows (window too large for data?)")

    rebalance_log = enrich_rebalance_log(rebalance_log, universe_data, factor, spec)

    config = build_engine_config()
    engine = PortfolioRebalanceEngine(config)
    selector = LowVolatilitySelector(spec)
    engine.set_target_provider(selector)

    price_data = price_panel.set_index(["ticker", "date"]).sort_index()
    result = engine.run(
        price_data=price_data,
        index_data=pd.DataFrame(),
        factor_data=factor,
        universe_data=universe_data,
        measured_start=pd.Timestamp(signal_dates[0]),
        measured_end=CUTOFF,
    )
    daily_returns = result["daily_returns"]
    metrics = compute_metrics(daily_returns)
    verdict = decide(metrics)

    output_dir.mkdir(parents=True, exist_ok=True)
    daily_path = output_dir / f"{CLASSIFICATION}_daily_returns.csv"
    daily_returns.rename("daily_return").to_frame().to_csv(daily_path)

    rebal_path = output_dir / f"{CLASSIFICATION}_rebalance_log.csv"
    pd.DataFrame(rebalance_log).to_csv(rebal_path, index=False)

    effective_config = {name: getattr(config, name) for name in NEUTRAL_CONFIG_FIELDS}
    manifest = {
        "classification": CLASSIFICATION,
        "label": CLASSIFICATION,
        "note": (
            "Adjusted-price (total-return proxy) diagnostic of the frozen "
            "KOSPI 200 low-volatility spec. NON-VALIDATED optimistic upper bound."
        ),
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "git_commit": git_commit_hash(repo_root),
        "hard_cutoff": "2024-12-31",
        "price_source": "pykrx adjusted OHLCV parquet cache (data/raw/price_YYYY.parquet)",
        "universe_source": "PIT bundle data/universe/kospi200_bundle_pit/",
        "effective_config": effective_config,
        "spec": {
            "window": spec.window,
            "min_valid_returns": spec.min_valid_returns,
            "bottom_fraction": spec.bottom_fraction,
            "quarterly_months": list(spec.quarterly_months),
            "price_return_basis": spec.price_return_basis,
        },
        "signal_dates": [d.isoformat() for d in signal_dates],
        "n_signal_dates": len(signal_dates),
        "n_universe_unique_tickers": int(universe["ticker"].nunique()),
        "rebalance_log": rebalance_log,
        "metrics": metrics,
        "verdict": verdict,
    }
    manifest_path = output_dir / f"{CLASSIFICATION}_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    summary = {
        "classification": CLASSIFICATION,
        "metrics": metrics,
        "verdict": verdict,
    }
    summary_path = output_dir / f"{CLASSIFICATION}_summary.json"
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--universe-dir",
        type=Path,
        default=Path("data/universe/kospi200_bundle_pit"),
    )
    parser.add_argument(
        "--price-dir", type=Path, default=Path("data/raw")
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs_k200_lowvol_diagnostic"),
    )
    args = parser.parse_args()

    manifest = run_diagnostic(args.universe_dir, args.price_dir, args.output_dir)
    print(json.dumps(manifest["verdict"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
