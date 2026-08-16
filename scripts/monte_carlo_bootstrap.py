#!/usr/bin/env python3
"""Monte Carlo bootstrap of stitched OOS performance metrics.

Resamples the OOS daily-return series (block/stationary bootstrap) to build
confidence intervals for the stitched CAGR, Sharpe, max drawdown, and Calmar
of a true-walkforward run, plus the empirical probability of passing each
scorecard performance gate.

Usage:
    uv run python scripts/monte_carlo_bootstrap.py \
        --oos outputs_k200mq_day20_validated_mom70/true_walkforward/oos_returns.csv \
        [--oos outputs_k200mq_day18_validation_check_v2/true_walkforward/oos_returns.csv ...] \
        [--n-bootstrap 5000] [--block-length 20] [--seed 42] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

TRADING_DAYS = 252

GATES = {
    "cagr": 0.05,
    "sharpe": 0.7,
    "mdd": -0.25,  # pass when mdd >= -0.25
    "calmar": 0.3,
}


def _load_returns(path: Path) -> np.ndarray:
    frame = pd.read_csv(path)
    if "daily_return" not in frame.columns:
        raise ValueError(f"missing daily_return column in {path}")
    values = pd.to_numeric(frame["daily_return"], errors="coerce").fillna(0.0).to_numpy()
    if len(values) < 100:
        raise ValueError(f"too few OOS points in {path}: {len(values)}")
    return values


def _stationary_bootstrap_indices(n: int, mean_block: int, rng: np.random.Generator) -> np.ndarray:
    """Stationary bootstrap index sequence (Politis & Romano 1994)."""
    indices: list[int] = []
    while len(indices) < n:
        start = int(rng.integers(0, n))
        length = int(rng.geometric(1.0 / mean_block))
        indices.extend(range(start, start + length))
    return np.asarray(indices[:n]) % n


def _metrics(returns: np.ndarray) -> dict[str, float]:
    total = float(np.prod(1.0 + returns) - 1.0)
    years = len(returns) / TRADING_DAYS
    cagr = float((1.0 + total) ** (1.0 / years) - 1.0) if years > 0 else float("nan")
    sharpe = float(returns.mean() / returns.std() * np.sqrt(TRADING_DAYS)) if returns.std() > 0 else 0.0
    eq = np.cumprod(1.0 + returns)
    mdd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
    calmar = float(cagr / abs(mdd)) if mdd < 0 else float("nan")
    return {"cagr": cagr, "sharpe": sharpe, "mdd": mdd, "calmar": calmar}


def bootstrap_metrics(
    returns: np.ndarray,
    *,
    n_bootstrap: int,
    block_length: int,
    seed: int,
) -> dict[str, object]:
    rng = np.random.default_rng(seed)
    observed = _metrics(returns)
    samples = {key: np.empty(n_bootstrap) for key in observed}
    for i in range(n_bootstrap):
        idx = _stationary_bootstrap_indices(len(returns), block_length, rng)
        for key, value in _metrics(returns[idx]).items():
            samples[key][i] = value

    summary: dict[str, object] = {"observed": observed, "n_bootstrap": n_bootstrap,
                                  "block_length": block_length, "seed": seed, "n_points": len(returns)}
    ci: dict[str, dict[str, float]] = {}
    for key, values in samples.items():
        ci[key] = {
            "p2_5": float(np.percentile(values, 2.5)),
            "p50": float(np.percentile(values, 50)),
            "p97_5": float(np.percentile(values, 97.5)),
            "p10": float(np.percentile(values, 10)),
            "p90": float(np.percentile(values, 90)),
        }
    summary["ci"] = ci

    pass_probs: dict[str, float] = {}
    for gate, threshold in GATES.items():
        values = samples[gate]
        if gate == "mdd":
            pass_probs[gate] = float((values >= threshold).mean())
        else:
            pass_probs[gate] = float((values >= threshold).mean())
    summary["gate_pass_probability"] = pass_probs
    summary["gates"] = GATES
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oos", action="append", required=True,
                        help="OOS returns CSV path (repeatable)")
    parser.add_argument("--n-bootstrap", type=int, default=5000)
    parser.add_argument("--block-length", type=int, default=20,
                        help="Mean block length (trading days) for stationary bootstrap")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--json", default="",
                        help="Optional JSON output path")
    args = parser.parse_args()

    results: dict[str, object] = {}
    for raw_path in args.oos:
        path = Path(raw_path)
        returns = _load_returns(path)
        label = path.parent.parent.name if path.parent.parent.name else str(path)
        summary = bootstrap_metrics(
            returns,
            n_bootstrap=args.n_bootstrap,
            block_length=args.block_length,
            seed=args.seed,
        )
        results[str(path)] = summary

        print(f"\n=== {path} (label: {label}) ===")
        print(f"  observed: CAGR={summary['observed']['cagr']:.2%} "
              f"Sharpe={summary['observed']['sharpe']:.3f} "
              f"MDD={summary['observed']['mdd']:.2%} "
              f"Calmar={summary['observed']['calmar']:.3f}")
        print("  95% CI [2.5%, 97.5%] (median):")
        for key, ci in summary["ci"].items():  # type: ignore[union-attr]
            print(f"    {key:7s}: [{ci['p2_5']:.4f}, {ci['p97_5']:.4f}]  median={ci['p50']:.4f}")
        print("  gate pass probability (P(metric >= threshold)):")
        for gate, prob in summary["gate_pass_probability"].items():  # type: ignore[union-attr]
            threshold = GATES[gate]
            print(f"    {gate:7s} >= {threshold:+.2f}: {prob:.1%}")

    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(f"\nJSON written -> {args.json}")


if __name__ == "__main__":
    main()
