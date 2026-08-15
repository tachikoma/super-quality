"""품질 팩터 계산.

Provides
--------
- :class:`QualityFactor` — ROE, Debt/Equity, gross-margin proxy, Cash Conversion
"""

from __future__ import annotations

import math
from types import MappingProxyType

import pandas as pd

from k200_mq.core.factors.base import Factor


QUALITY_FORMULA_VERSION = "k200mq-quality-weighted-zscore-v3"
QUALITY_FORMULA = (
    "normalized(roe_z, de_z, gross_margin_proxy_z, cashconv_z; "
    "de inverted; missing components neutral)"
)

# ``revenue - cogs`` is retained as the historical arithmetic, but the input
# does not contain operating income.  The numerator floor and final ratio clip
# are explicit parts of this proxy's contract.
GROSS_PROFIT_PROXY_NUMERATOR_FLOOR = 0.0  # Floor for max(revenue - cogs, 0).
GROSS_MARGIN_PROXY_RATIO_CLIP = (-1.0, 1.0)  # Final ratio clip after division.


class QualityFactor(Factor):
    """다차원 품질 팩터.

    다음 네 가지 품질 지표를 계산하고
    교차섹셔널 z-score로 정규화합니다:

    * ROE (Return on Equity) — 총이익/자본총계
    * Debt/Equity — 총부채/자본총계 (낮을수록 좋음)
    * Gross Margin Proxy — ``gross_profit_proxy / revenue``.  This is not a
      true gross-margin or operating-margin measure because only revenue and
      COGS are available.  The numerator is explicitly floored at
      ``GROSS_PROFIT_PROXY_NUMERATOR_FLOOR`` and the final ratio is clipped to
      ``GROSS_MARGIN_PROXY_RATIO_CLIP``.
    * Cash Conversion — 영업현금흐름/당기순이익

    Component weights are normalized to sum to one.  The four weights must be
    nonnegative and have a positive sum.  ``raw_weights`` preserves the
    configured values and ``weights`` contains the effective values used by
    the composite.  ``min_ttm_quarters`` is retained for API compatibility but
    is currently inert; no TTM-quarter filter is implemented in this factor.
    """

    formula_version = QUALITY_FORMULA_VERSION
    formula = QUALITY_FORMULA

    def __init__(
        self,
        weight_roe: float = 0.35,
        weight_de: float = 0.25,
        weight_gross_margin_proxy: float | None = None,
        weight_cashconv: float = 0.20,
        *,
        weight_opmargin: float | None = None,
    ) -> None:
        """Create the factor using the canonical proxy weight name.

        ``weight_opmargin`` is a deprecated constructor alias retained for
        existing callers.  The canonical name is
        ``weight_gross_margin_proxy``.
        """
        if weight_gross_margin_proxy is None:
            weight_gross_margin_proxy = (
                weight_opmargin if weight_opmargin is not None else 0.20
            )
        raw_weights = {
            "roe": weight_roe,
            "de": weight_de,
            "gross_margin_proxy": weight_gross_margin_proxy,
            "cashconv": weight_cashconv,
        }
        try:
            numeric_weights = {name: float(value) for name, value in raw_weights.items()}
        except (TypeError, ValueError) as exc:
            raise ValueError("quality component weights must be numeric") from exc
        if any(not math.isfinite(value) or value < 0 for value in numeric_weights.values()):
            raise ValueError("quality component weights must be finite and nonnegative")
        total = sum(numeric_weights.values())
        if total <= 0:
            raise ValueError("quality component weights must have a positive sum")
        self.raw_weights = MappingProxyType(dict(numeric_weights))
        self.weights = MappingProxyType({
            name: value / total for name, value in numeric_weights.items()
        })

    @property
    def name(self) -> str:
        return "Quality"

    def compute(
        self,
        data: pd.DataFrame,
        min_ttm_quarters: int = 3,
    ) -> pd.DataFrame:
        """품질 팩터를 계산합니다.

        Parameters
        ----------
        data : pd.DataFrame
            ``ticker``, ``date``, ``net_income``, ``total_equity``,
            ``total_debt``, ``revenue``, ``gross_profit_proxy``,
            ``operating_cf`` 컬럼을 포함해야 합니다.  If
            ``financial_six_fact_available`` is present, only explicitly
            complete rows are scored; without that flag, rows remain eligible
            for backward-compatible standalone calls.
        min_ttm_quarters : int
            Deprecated compatibility argument.  It is inert until an
            explicit point-in-time TTM contract is implemented.

        Returns
        -------
        pd.DataFrame
            컬럼: ``ticker``, ``date``,
            ``roe_z``, ``de_z``, ``gross_margin_proxy_z``, ``cashconv_z``,
            ``quality_composite_z``.
        """
        input_columns = [
            "ticker",
            "date",
            "net_income",
            "total_equity",
            "total_debt",
            "revenue",
            "gross_profit_proxy",
            "operating_cf",
        ]
        has_availability_flag = "financial_six_fact_available" in data.columns
        if has_availability_flag:
            input_columns.append("financial_six_fact_available")
        df = data[input_columns].copy()
        if has_availability_flag:
            # Neutral-filled values from incomplete six-fact rows must not
            # enter peer statistics.  The pipeline merges their absent rows
            # back as its explicit quality_z=0 neutral value.
            df = df[
                df["financial_six_fact_available"].astype("boolean").fillna(False)
            ].copy()
        df = df.sort_values(["ticker", "date"])

        # 분모가 0이면 1 또는 NaN 대신 작은 양수로 대체 (NaN 전파 방지)
        safe_equity = df["total_equity"].replace(0, 1.0)
        safe_revenue = df["revenue"].replace(0, 1.0)
        safe_ni = df["net_income"].replace(0, 1.0)

        # ROE
        df["roe"] = df["net_income"] / safe_equity
        df["roe"] = df["roe"].clip(lower=-10.0, upper=10.0)

        # Debt/Equity (낮을수록 좋음)
        df["de"] = df["total_debt"] / safe_equity
        df["de"] = df["de"].clip(lower=0, upper=10.0)

        # Gross-margin proxy; this is explicitly not operating margin.
        df["gross_margin_proxy"] = df["gross_profit_proxy"] / safe_revenue
        df["gross_margin_proxy"] = df["gross_margin_proxy"].clip(
            lower=GROSS_MARGIN_PROXY_RATIO_CLIP[0],
            upper=GROSS_MARGIN_PROXY_RATIO_CLIP[1],
        )

        # Cash Conversion
        df["cashconv"] = df["operating_cf"] / safe_ni
        df["cashconv"] = df["cashconv"].clip(lower=0, upper=5.0)

        # TTM filtering is deliberately not implemented in this patch.
        del min_ttm_quarters

        # 교차섹셔널 z-score 정규화
        for col, invert in [
            ("roe", False),
            ("de", True),  # 낮을수록 좋음
            ("gross_margin_proxy", False),
            ("cashconv", False),
        ]:
            z_col = f"{col}_z"
            df[z_col] = (
                df.groupby("date")[col]
                .transform(
                    lambda x: (x - x.mean()) / x.std()
                    if x.std() > 0 and x.notna().sum() > 1
                    else 0.0
                )
            )
            # DE는 부호 반전 (낮을수록 좋음)
            if invert:
                df[z_col] = -df[z_col]

        # 품질 종합 점수 (가중 평균)
        df["quality_composite_z"] = sum(
            self.weights[name] * df[f"{name}_z"].fillna(0)
            for name in self.weights
        )

        return df[
            [
                "ticker",
                "date",
                "roe_z",
                "de_z",
                "gross_margin_proxy_z",
                "cashconv_z",
                "quality_composite_z",
            ]
        ]
