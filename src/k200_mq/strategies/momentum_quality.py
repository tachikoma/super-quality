"""KOSPI 200 Momentum + Quality 전략.

크로스섹셔널 모멘텀+품질 스코어링으로 리밸런싱 일자에
최적 포트폴리오를 구성합니다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import pandas as pd

from k200_mq.config import K200MQConfig

logger = logging.getLogger(__name__)


class MomentumQualityStrategy:
    """KOSPI 200 Momentum + Quality 전략.

    교차섹셔널 모멘텀+품질 스코어링으로 종목을 선택하고
    리밸런싱 일자에 포트폴리오를 재구성합니다.
    """

    def __init__(
        self,
        config: K200MQConfig,
        kospi_mcap_ranking: tuple[str, ...] | None = None,
        sector_map_by_as_of: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self.config = config
        self._kospi_mcap_ranking = kospi_mcap_ranking
        self._sector_map_by_as_of = {
            str(as_of): {
                str(ticker): str(sector)
                for ticker, sector in sector_map.items()
            }
            for as_of, sector_map in dict(sector_map_by_as_of or {}).items()
        }

    def select_portfolio(
        self,
        factor_data: pd.DataFrame,
        universe: list[str],
        as_of: Any,
        adv_ratio_by_ticker: Mapping[str, float] | None = None,
        pair_correlation_map: Mapping[tuple[str, str], float] | None = None,
    ) -> list[dict[str, Any]]:
        """리밸런싱 일자에 포트폴리오를 선택합니다.

        Parameters
        ----------
        factor_data : pd.DataFrame
            팩터 스코어가 포함된 DataFrame.
            컬럼: ticker, momentum_z, quality_z, composite_z.
        universe : list[str]
            리서치 대상 티커 리스트.
        as_of : any
            리밸런싱 일자 (로그용).

        Returns
        -------
        list[dict]
            [{"ticker": str, "weight": float}, ...]
        """
        # 유니버스 필터링
        eligible = factor_data[
            factor_data["ticker"].isin(universe)
        ].copy()

        if eligible.empty:
            logger.warning("리밸런싱 %s: eligible 종목 없음", as_of)
            return []

        if bool(getattr(self.config, "ENABLE_ADV_FILTER", False)):
            eligible = self._apply_adv_filter(eligible, adv_ratio_by_ticker)
            if eligible.empty:
                logger.warning("리밸런싱 %s: ADV 필터 통과 종목 없음", as_of)
                return []

        # 모멘텀 가중치 + 품질 가중치 = 복합 스코어
        w_mom = self.config.WEIGHT_MOMENTUM
        w_qual = self.config.WEIGHT_QUALITY
        eligible["composite_z"] = (
            w_mom * eligible["momentum_z"].fillna(0)
            + w_qual * eligible["quality_z"].fillna(0)
        )

        # 종목 선택 (top N)
        top_n = self.config.TOP_N
        selected = eligible.nlargest(top_n, "composite_z")

        # KOSPI 상위 50개 제외 (메가캡 모멘텀 희석 방지)
        if self.config.EXCLUDE_KOSPI_TOP_N > 0:
            selected = self._exclude_kospi_top(selected)

        max_holdings = int(self.config.MAX_HOLDINGS)
        if len(selected) > max_holdings:
            selected = selected.nlargest(max_holdings, "composite_z")

        if bool(getattr(self.config, "ENABLE_CORRELATION_FILTER", False)):
            selected = self._apply_correlation_filter(selected, pair_correlation_map)

        if selected.empty:
            logger.warning("리밸런싱 %s: 선택된 종목 없음 (제외 필터 적용 후)", as_of)
            return []

        # 포지션 배분
        weight_method = self.config.WEIGHT_METHOD
        if weight_method == "equal":
            n = len(selected)
            weight = 1.0 / n if n > 0 else 0.0
            selected["weight"] = weight
        elif weight_method == "rank_weighted":
            selected["rank"] = range(len(selected), 0, -1)
            total_rank = selected["rank"].sum()
            selected["weight"] = selected["rank"] / total_rank
        else:
            selected["weight"] = 1.0 / len(selected)

        # 포지션당 최대 비중 제한
        max_w = self.config.MAX_POSITION_WEIGHT
        selected["weight"] = selected["weight"].clip(upper=max_w)
        weights_norm = selected["weight"].sum()
        if weights_norm > 0:
            selected["weight"] = selected["weight"] / weights_norm

        if bool(getattr(self.config, "ENABLE_SECTOR_CAP", False)):
            selected = self._apply_sector_cap(selected, as_of)

        logger.info(
            "리밸런싱 %s: %d개 선정, 평균 weight=%.4f",
            as_of,
            len(selected),
            selected["weight"].mean(),
        )

        return selected.to_dict(orient="records")

    def _apply_adv_filter(
        self,
        eligible: pd.DataFrame,
        adv_ratio_by_ticker: Mapping[str, float] | None,
    ) -> pd.DataFrame:
        """Filter eligible candidates by trailing ADV turnover ratio.

        Tickers whose ADV ratio cannot be measured (e.g. delisted names with no
        market cap in the price cache) are excluded from the candidate pool
        with a warning instead of failing the whole run. A missing map itself
        (wiring bug) still fails closed.
        """
        if adv_ratio_by_ticker is None:
            raise RuntimeError(
                "ENABLE_ADV_FILTER requires precomputed ADV turnover ratios"
            )

        ratio_series = eligible["ticker"].map(
            lambda ticker: adv_ratio_by_ticker.get(str(ticker))
        )
        missing_mask = ratio_series.isna()
        if missing_mask.any():
            missing = sorted(
                str(ticker)
                for ticker in eligible.loc[missing_mask, "ticker"].unique()
            )
            logger.warning(
                "ADV 커버리지 누락으로 후보에서 제외: %d개 [%s]",
                len(missing),
                ", ".join(missing),
            )
            eligible = eligible.loc[~missing_mask].copy()

        if eligible.empty:
            return eligible

        eligible["adv_ratio"] = eligible["ticker"].map(
            lambda ticker: adv_ratio_by_ticker.get(str(ticker))
        ).astype(float)
        threshold = float(self.config.MIN_ADV_RATIO)
        filtered = eligible[eligible["adv_ratio"] >= threshold].copy()
        return filtered.drop(columns=["adv_ratio"])

    def _apply_correlation_filter(
        self,
        selected: pd.DataFrame,
        pair_correlation_map: Mapping[tuple[str, str], float] | None,
    ) -> pd.DataFrame:
        """Apply greedy pairwise correlation constraint to selected candidates."""
        if pair_correlation_map is None:
            raise RuntimeError(
                "ENABLE_CORRELATION_FILTER requires precomputed pairwise correlation data"
            )

        if selected.empty or len(selected) <= 1:
            return selected

        threshold = float(self.config.MAX_PAIR_CORRELATION)
        ordered = selected.nlargest(len(selected), "composite_z")
        kept_tickers: list[str] = []
        kept_rows: list[int] = []

        for idx, row in ordered.iterrows():
            ticker = str(row["ticker"])
            rejected = False
            for kept in kept_tickers:
                key = self._pair_corr_key(ticker, kept)
                corr = pair_correlation_map.get(key)
                if corr is None:
                    raise RuntimeError(
                        "ENABLE_CORRELATION_FILTER requires complete pairwise "
                        "correlation coverage for selected candidates"
                    )
                if float(corr) > threshold:
                    rejected = True
                    break
            if rejected:
                continue
            kept_tickers.append(ticker)
            kept_rows.append(idx)

        return ordered.loc[kept_rows].copy()

    @staticmethod
    def _pair_corr_key(left: str, right: str) -> tuple[str, str]:
        """Return a normalized pair key for symmetric correlation lookups."""
        a = str(left)
        b = str(right)
        return (a, b) if a <= b else (b, a)

    def _apply_sector_cap(self, selected: pd.DataFrame, as_of: Any) -> pd.DataFrame:
        """Apply sector-level cap using prepared PIT sector-map snapshots."""
        as_of_key = self._resolve_sector_map_key(as_of)
        if as_of_key is None:
            raise RuntimeError(
                "ENABLE_SECTOR_CAP requires a prepared sector map for the rebalance date"
            )
        sector_map = self._sector_map_by_as_of.get(as_of_key, {})
        if not sector_map:
            raise RuntimeError(
                "ENABLE_SECTOR_CAP requires non-empty prepared sector map snapshots"
            )

        selected = selected.copy()
        selected["sector"] = selected["ticker"].map(lambda ticker: sector_map.get(str(ticker), ""))
        if (selected["sector"].str.len() == 0).any():
            missing_tickers = sorted(
                str(ticker)
                for ticker in selected.loc[selected["sector"].str.len() == 0, "ticker"].unique()
            )
            raise RuntimeError(
                "ENABLE_SECTOR_CAP requires sector assignments for all selected tickers; "
                f"missing: {', '.join(missing_tickers)}"
            )

        cap = float(self.config.SECTOR_CAP)
        if cap >= 1.0:
            return selected

        selected["weight"] = selected["weight"].astype(float)
        if selected["weight"].sum() <= 0:
            return selected

        selected["weight"] = selected["weight"] / selected["weight"].sum()
        sector_weights = selected.groupby("sector")["weight"].sum().to_dict()

        sector_target = {sector: min(weight, cap) for sector, weight in sector_weights.items()}
        allocated = sum(sector_target.values())
        leftover = max(1.0 - allocated, 0.0)
        total_room = sum(max(cap - sector_target[sector], 0.0) for sector in sector_target)
        if leftover > 0 and total_room > 0:
            fill = min(leftover, total_room)
            for sector in sector_target:
                room = max(cap - sector_target[sector], 0.0)
                if room <= 0:
                    continue
                sector_target[sector] += fill * (room / total_room)

        row_weights: list[float] = []
        for _, row in selected.iterrows():
            sector = str(row["sector"])
            sector_weight = sector_weights.get(sector, 0.0)
            if sector_weight <= 0:
                row_weights.append(0.0)
                continue
            row_weights.append(float(row["weight"]) * (sector_target[sector] / sector_weight))
        selected["weight"] = row_weights
        return selected.drop(columns=["sector"])

    def _resolve_sector_map_key(self, as_of: Any) -> str | None:
        """Resolve a prepared sector-map key for a signal date.

        Universe schedules can map month-end dates to a prior trading session
        in the engine, so this resolver tolerates a small calendar mismatch.
        """
        if not self._sector_map_by_as_of:
            return None
        ts = pd.Timestamp(as_of).normalize()
        key = ts.date().isoformat()
        if key in self._sector_map_by_as_of:
            return key

        candidates = [pd.Timestamp(candidate) for candidate in self._sector_map_by_as_of]
        if not candidates:
            return None
        nearest = min(candidates, key=lambda candidate: abs((candidate - ts).days))
        if abs((nearest - ts).days) <= 7:
            return nearest.date().isoformat()
        return None

    def get_signal(
        self,
        factor_data: pd.DataFrame,
        as_of: Any,
    ) -> pd.DataFrame:
        """일일 시그널을 생성합니다 (stop-loss 모니터링용).

        Parameters
        ----------
        factor_data : pd.DataFrame
            일별 팩터 데이터 (ticker, date, close, momentum_z, quality_z).
        as_of : any
            기준 일자.

        Returns
        -------
        pd.DataFrame
            일일 시그널 DataFrame.
        """
        eligible = factor_data[
            factor_data["date"] == as_of
        ].copy() if "date" in factor_data.columns else factor_data.copy()

        w_mom = self.config.WEIGHT_MOMENTUM
        w_qual = self.config.WEIGHT_QUALITY
        eligible["composite_z"] = (
            w_mom * eligible.get("momentum_z", pd.Series(0, index=eligible.index))
            + w_qual * eligible.get("quality_z", pd.Series(0, index=eligible.index))
        )

        return eligible[["ticker", "composite_z"]]

    def _exclude_kospi_top(
        self, selected: pd.DataFrame
    ) -> pd.DataFrame:
        """Exclude KOSPI mega-caps using the prepared ranking artifact.

        Interval execution is deliberately an in-memory boundary.  Falling
        back to ``exclude_kospi_top_n`` here would re-enter the loader/cache
        layer and make a candidate run depend on an unprepared snapshot.
        """
        n = self.config.EXCLUDE_KOSPI_TOP_N
        if self._kospi_mcap_ranking is not None:
            excluded = set(self._kospi_mcap_ranking[:n])
            return selected[~selected["ticker"].isin(excluded)]
        raise RuntimeError(
            "EXCLUDE_KOSPI_TOP_N requires a prepared KOSPI market-cap ranking "
            "artifact; interval execution cannot perform loader/cache fallback"
        )
