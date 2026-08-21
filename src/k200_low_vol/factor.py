"""Pure consecutive-session low-volatility factor calculation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from k200_low_vol.contract import validate_rows_cutoff
from k200_low_vol.spec import LowVolSpec


class LowVolatilityFactor:
    """Compute sample volatility from a constructed close-price panel.

    Rows are canonical KRX sessions.  A row with a missing/invalid close,
    zero volume, stale status, suspension status, or ``observed=False`` cannot
    contribute to either adjacent return.  There is deliberately no call to
    :meth:`pandas.Series.pct_change`, and consequently no implicit fill.
    """

    formula_version = "k200-low-volatility-consecutive-close-ddof1-v1"

    def __init__(self, spec: LowVolSpec | None = None) -> None:
        self.spec = spec or LowVolSpec()

    @property
    def name(self) -> str:
        return "LowVolatility"

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        frame = self._canonical_frame(data)
        validate_rows_cutoff(frame)
        close_column = "constructed_close" if "constructed_close" in frame.columns else "close"
        if close_column not in frame.columns or "volume" not in frame.columns:
            raise ValueError("constructed close data requires close and volume columns")
        if frame.duplicated(["ticker", "date"]).any():
            raise ValueError("constructed close data contains duplicate ticker/date rows")

        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        if frame["date"].isna().any():
            raise ValueError("factor input contains invalid dates")
        frame = frame.sort_values(["ticker", "date"], kind="mergesort").reset_index(drop=True)
        session_calendar = self._session_calendar(frame)
        if len(session_calendar) < self.spec.window:
            return self._empty_output()
        result_rows: list[dict[str, Any]] = []
        for ticker, group in frame.groupby("ticker", sort=True, dropna=False):
            if pd.isna(ticker) or not str(ticker).strip():
                raise ValueError("ticker must be non-empty")
            valid_rows = self._valid_rows(group, close_column)
            close_values = pd.to_numeric(group[close_column], errors="coerce").to_numpy(dtype=float)
            by_date = {
                pd.Timestamp(row["date"]): (bool(valid), float(close))
                for row, valid, close in zip(
                    group.to_dict(orient="records"), valid_rows, close_values
                )
            }
            for end, signal_date in enumerate(session_calendar):
                if end < self.spec.window - 1 or signal_date not in by_date:
                    continue
                window_dates = session_calendar[end - self.spec.window + 1 : end + 1]
                signal_valid, _ = by_date[signal_date]
                if not signal_valid:
                    continue
                returns: list[float] = []
                for left_date, right_date in zip(window_dates, window_dates[1:]):
                    left = by_date.get(left_date)
                    right = by_date.get(right_date)
                    if left is None or right is None:
                        continue
                    if not (left[0] and right[0]):
                        continue
                    left_close = left[1]
                    right_close = right[1]
                    value = right_close / left_close - 1.0
                    if np.isfinite(value):
                        returns.append(float(value))
                if len(returns) < self.spec.min_valid_returns:
                    continue
                volatility = float(np.std(np.asarray(returns, dtype=float), ddof=1))
                result_rows.append({
                    "ticker": str(ticker),
                    "date": signal_date,
                    "low_volatility": volatility,
                    "valid_return_count": len(returns),
                })

        output = pd.DataFrame(
            result_rows,
            columns=["ticker", "date", "low_volatility", "valid_return_count"],
        )
        if not output.empty:
            output = output.sort_values(["ticker", "date"], kind="mergesort").reset_index(drop=True)
        fingerprint = self._fingerprint(output)
        output["factor_fingerprint"] = fingerprint
        output.attrs["factor_fingerprint"] = fingerprint
        output.attrs["formula_version"] = self.formula_version
        output.attrs["spec"] = self.spec
        return output

    def _empty_output(self) -> pd.DataFrame:
        output = pd.DataFrame(
            columns=[
                "ticker", "date", "low_volatility", "valid_return_count", "factor_fingerprint",
            ]
        )
        fingerprint = self._fingerprint(output)
        output.attrs["factor_fingerprint"] = fingerprint
        output.attrs["formula_version"] = self.formula_version
        output.attrs["spec"] = self.spec
        return output

    @staticmethod
    def _session_calendar(frame: pd.DataFrame) -> tuple[pd.Timestamp, ...]:
        supplied = frame.attrs.get("session_calendar")
        if supplied is None:
            supplied = frame.attrs.get("verified_session_calendar")
        if supplied is None:
            return tuple(sorted(pd.DatetimeIndex(frame["date"].unique())))
        if frame.attrs.get("session_lattice_verified") is False:
            raise ValueError("factor input session lattice is not verified")
        calendar = pd.to_datetime(list(supplied), errors="coerce").normalize()
        if calendar.isna().any() or not calendar.is_unique:
            raise ValueError("factor input session calendar is invalid")
        return tuple(sorted(calendar))

    @staticmethod
    def _canonical_frame(data: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(data, pd.DataFrame):
            raise TypeError("low-volatility factor input must be a pandas DataFrame")
        frame = data.copy()
        required = {"ticker", "date"}
        if not required.issubset(frame.columns) and isinstance(frame.index, pd.MultiIndex):
            names = set(frame.index.names)
            if required.issubset(names):
                frame = frame.reset_index()
        if not required.issubset(frame.columns):
            raise ValueError("factor input must contain ticker and date columns or index levels")
        return frame

    @staticmethod
    def _valid_rows(group: pd.DataFrame, close_column: str) -> np.ndarray:
        close = pd.to_numeric(group[close_column], errors="coerce")
        volume = pd.to_numeric(group["volume"], errors="coerce")
        valid = close.notna() & np.isfinite(close) & (close > 0)
        valid &= volume.notna() & np.isfinite(volume) & (volume > 0)
        status_columns = (
            "observed", "is_observed", "missing", "is_missing", "suspended", "is_suspended",
            "stale", "is_stale", "zero_volume",
        )
        for column in status_columns:
            if column in group:
                valid &= group[column].map(type).eq(bool)
        for column in ("observed", "is_observed"):
            if column in group:
                valid &= _boolean_flag(group[column], default=False)
        for column in ("missing", "is_missing"):
            if column in group:
                valid &= ~_boolean_flag(group[column], default=False)
        for column in ("suspended", "is_suspended", "stale", "is_stale", "zero_volume"):
            if column in group:
                valid &= ~_boolean_flag(group[column], default=False)
        return valid.to_numpy(dtype=bool)

    @classmethod
    def _row_is_valid(cls, row: dict[str, Any], close_column: str) -> bool:
        frame = pd.DataFrame([row])
        return bool(cls._valid_rows(frame, close_column)[0])

    def _fingerprint(self, output: pd.DataFrame) -> str:
        records = []
        for row in output.to_dict(orient="records"):
            records.append({
                "date": pd.Timestamp(row["date"]).isoformat(),
                "low_volatility": float(row["low_volatility"]),
                "ticker": str(row["ticker"]),
                "valid_return_count": int(row["valid_return_count"]),
            })
        payload = {
            "formula_version": self.formula_version,
            "spec": {
                "cutoff": self.spec.development_cutoff.isoformat(),
                "window": self.spec.window,
                "min_valid_returns": self.spec.min_valid_returns,
                "bottom_fraction": self.spec.bottom_fraction,
                "quarterly_months": self.spec.quarterly_months,
                "price_return_basis": self.spec.price_return_basis,
            },
            "rows": records,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _boolean_flag(values: pd.Series, *, default: bool) -> pd.Series:
    """Parse status flags without treating missing status as safe."""
    true_values = {"true", "1", "yes", "y", "suspended", "stale", "missing"}

    def parse(value: Any) -> bool:
        if pd.isna(value):
            return default
        if isinstance(value, (bool, np.bool_)):
            return bool(value)
        text = str(value).strip().casefold()
        if text in true_values:
            return True
        if text in {"false", "0", "no", "n", "observed", ""}:
            return False
        return default

    return values.map(parse)


__all__ = ["LowVolatilityFactor"]
