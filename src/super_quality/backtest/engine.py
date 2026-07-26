"""백테스팅 엔진 for Super Quality 2.0.

2단계 실행을 사용하는 일별 시뮬레이션 루프:
  Phase 1 — 전일 결정에 따라 주문 실행 (매도 / 매수 체결).
  Phase 2 — 새로운 결정을 내림 (매도/매수 조건 확인). 룩어헤드 편향을
            피하기 위해 전일 거래 데이터를 사용.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from super_quality.config import SuperQualityConfig
from super_quality.factors.market_timing import KosdaqMAFactor
from super_quality.strategies.super_quality import SuperQualityStrategy

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Super Quality 2.0의 일별 백테스팅 시뮬레이션.

    Parameters
    ----------
    config : SuperQualityConfig
        전략 파라미터 (포트폴리오 제한, 비용, 임계값).
    """

    def __init__(self, config: SuperQualityConfig) -> None:
        self.config = config
        self.strategy = SuperQualityStrategy(config)

    def set_strategy(self, strategy: SuperQualityStrategy) -> None:
        self.strategy = strategy

    # ═══════════════════════════════════════════════════════════════════
    # Public API
    # ═══════════════════════════════════════════════════════════════════

    def run(
        self,
        price_data: pd.DataFrame,
        index_data: pd.DataFrame,
        factor_data: pd.DataFrame,
        financial_data: pd.DataFrame,  # noqa: ARG002 — reserved for future pre-processing
    ) -> dict[str, Any]:
        """백테스트 시뮬레이션을 실행합니다.

        Parameters
        ----------
        price_data : pd.DataFrame
            MultiIndex ``(ticker, date)``이며 ``open``, ``high``,
            ``low``, ``close``, ``volume``, ``mcap`` 컬럼을 포함.
        index_data : pd.DataFrame
            ``date``를 index로 하고 ``close`` 컬럼을 가진 DataFrame (시장 지수 종가).
        factor_data : pd.DataFrame
            ``ticker``, ``date`` 컬럼 및
            :meth:`SuperQualityStrategy.evaluate_buy_conditions`에 필요한
            모든 컬럼:
            ``pbr``, ``pbr_percentile``, ``mcap_percentile``,
            ``share_change_5mo_ago``, ``share_change_now``,
            ``trailing_ni``, ``trailing_ocf``, ``gpa_percentile``,
            ``supply_percentile``.
        financial_data : pd.DataFrame
            원시 재무제표 데이터 (향후 전처리를 위해 예약됨).

        Returns
        -------
        dict
            ``portfolio_snapshots`` : pd.DataFrame
                컬럼: date, cash, holdings_value, nav, num_positions.
            ``trade_log`` : pd.DataFrame
                컬럼: entry_date, exit_date, ticker, buy_price,
                sell_price, shares, return_pct, hold_days, exit_reason.
            ``daily_returns`` : pd.Series
                date를 index로 함.
        """
        _ = financial_data  # 예약됨

        # ── 시장 타이밍 신호 미리 계산 ────────────────────────
        index_signals = self._compute_index_signals(index_data)

        # ── factor_data에 buy_signal 컬럼 추가 ──────────────────
        factor_data = self._enrich_factor_data(factor_data, index_signals)

        # ── 모든 ticker × date에 대해 매수 조건 미리 평가 ──
        condition_data = self.strategy.evaluate_buy_conditions(factor_data)
        condition_data["date"] = factor_data["date"].values

        # ── 일별 루프 실행 ───────────────────────────────────────
        return self._run_daily_loop(price_data, condition_data, index_signals)

    # ═══════════════════════════════════════════════════════════════════
    # 내부 메서드
    # ═══════════════════════════════════════════════════════════════════

    @staticmethod
    def _compute_index_signals(index_data: pd.DataFrame) -> pd.DataFrame:
        """시장 지수 이동 평균 타이밍 신호를 계산합니다.

        ``buy_signal``과 ``sell_signal`` (모두 bool) 컬럼을 가진
        ``date``를 index로 하는 DataFrame을 반환합니다.
        """
        factor = KosdaqMAFactor()
        close_series: pd.Series = (
            index_data["close"]
            if "close" in index_data.columns
            else index_data.iloc[:, 0]
        )
        signals = factor.compute(close_series)
        signals = signals.set_index("date")
        if not isinstance(signals.index, pd.DatetimeIndex):
            signals.index = pd.to_datetime(signals.index)
        return signals[["buy_signal", "sell_signal"]]

    @staticmethod
    def _enrich_factor_data(
        factor_data: pd.DataFrame,
        index_signals: pd.DataFrame,
    ) -> pd.DataFrame:
        """``buy_signal`` 컬럼을 *factor_data*에 추가합니다.

        각 행에 대해, 해당 행의 ``date``와 같거나 이전인 가장 최근
        시장 신호값이 사용됩니다 (룩어헤드 방지).
        """
        if "buy_signal" in factor_data.columns:
            return factor_data

        df = factor_data.copy()
        # asof 병합이 올바르게 작동하도록 정렬
        sig = index_signals[["buy_signal"]].copy()
        sig.index.name = "date"
        sig = sig.reset_index()

        # 두 date 컬럼이 비교 가능한지 확인
        df["_date"] = pd.to_datetime(df["date"])
        sig["_date"] = pd.to_datetime(sig["date"])

        sig = sig.sort_values("_date")
        df = df.sort_values("_date")

        # Forward-fill: 각 factor 행에 대해 해당 날짜 이전의
        # 가장 최신 시장 신호값을 가져옴
        df["buy_signal"] = np.nan
        # 수동 asof 병합
        sig_sorted = sig.set_index("_date")["buy_signal"]
        df["buy_signal"] = (
            df.set_index("_date")[[]]
            .join(sig_sorted, how="left")
            .ffill()["buy_signal"]
            .fillna(False)
            .values
        )

        df = df.drop(columns=["_date"])
        return df

    # ── 일별 시뮬레이션 루프 ─────────────────────────────────────────

    def _run_daily_loop(
        self,
        price_data: pd.DataFrame,
        condition_data: pd.DataFrame,
        index_signals: pd.DataFrame,
    ) -> dict[str, Any]:
        """반복 일별 백테스트를 실행합니다.

        각 반복의 로직:

        1. **실행**: 전일 결정된 만기(expiry) 매도 주문을 *전일* 종가로 정산
        2. **실행**: 전일 결정된 조건부(conditional) 매도 주문을 *당일* 시가로 정산
        3. **실행**: 전일 제출된 매수 주문 — *당일* 저가가 지정가(전일종가 × 0.99) 이하인지 확인
        4. **평가**: 보유 포지션의 매도 조건 평가 (*전일* 종가 데이터 사용)
        5. **평가**: 신규 포지션의 매수 조건 평가 (*전일* 팩터 / 시장 데이터 사용)
        6. **기록**: 일별 포트폴리오 스냅샷 저장
        """
        config = self.config

        # ── Sorted unique trading days ──
        all_dates = sorted(price_data.index.get_level_values("date").unique())
        if len(all_dates) < 2:
            return self._empty_result()

        # ── 날짜 → 순서 인덱스 (O(1) 보유기간 계산용) ──────────────
        date_ordinal = {d: i for i, d in enumerate(all_dates)}

        # ── 조건 조회 테이블 미리 계산: (date, ticker) → row ───────
        cond_lookup: dict[date, dict[str, dict[str, Any]]] = {}
        for d, grp in condition_data.groupby("date"):
            d_key = d if isinstance(d, date) else pd.Timestamp(d).date()
            cond_lookup[d_key] = {}
            for _, row in grp.iterrows():
                cond_lookup[d_key][str(row["ticker"])] = row.to_dict()

        # ── 시장 신호 조회 ──
        def _index_signal(d: date, col: str) -> bool:
            """*d*보다 작거나 같은 가장 최근 날짜의 시장 신호(*col*)를 반환합니다."""
            ts = pd.Timestamp(d)
            mask = index_signals.index <= ts
            if not mask.any():
                return False
            latest = index_signals.loc[mask].iloc[-1]
            return bool(latest[col])

        def _nearest_index_signal(prev: date, col: str) -> bool:
            """_index_signal과 동일하지만 bool을 안전하게 반환합니다."""
            return _index_signal(prev, col)

        # ── 상태 ────────────────────────────────────────────────────
        cash = float(config.INITIAL_CAPITAL)
        positions: dict[str, dict[str, Any]] = {}  # ticker → {shares, entry_price, entry_date}
        expiry_sell_queue: list[tuple[str, int]] = []  # (ticker, shares) — 전일 종가로 매도
        conditional_sell_queue: list[tuple[str, int, str]] = []  # (ticker, shares, reason)
        buy_order_queue: list[tuple[str, float, int]] = []  # (ticker, limit_price, shares)

        trade_log: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []

        # ── 헬퍼: (ticker, date)에 대한 가격 조회 ─────────────────────
        def _price(tkr: str, dt: pd.Timestamp, col: str) -> float | None:
            try:
                val = price_data.loc[(tkr, dt), col]
                if isinstance(val, pd.Series):
                    val = val.iloc[0]
                return float(val)
            except (KeyError, ValueError, TypeError):
                return None

        # ══════════════════════════════════════════════════════════════
        # 메인 루프
        # ══════════════════════════════════════════════════════════════
        for i, current_date in enumerate(all_dates):
            current_ts = pd.Timestamp(current_date)
            prev_date = all_dates[i - 1] if i > 0 else None
            prev_ts = pd.Timestamp(prev_date) if prev_date else None

            # ── 첫째 날: 초기 스냅샷만 기록, 결정 없음 ─────
            if i == 0:
                snapshots.append({
                    "date": current_date,
                    "cash": cash,
                    "holdings_value": 0.0,
                    "nav": cash,
                    "num_positions": 0,
                })
                continue

            # ══════════════════════════════════════════════════════════
            # PHASE 1 — 전일 결정에 따른 주문 실행
            # ══════════════════════════════════════════════════════════

            # 1a. 만기 매도 (전일 종가로 결정, 전일 종가로 정산)
            for ticker, shares in expiry_sell_queue:
                pos = positions.get(ticker)
                if pos is None:
                    continue
                sell_price = _price(ticker, prev_ts, "close")  # type: ignore[arg-type]
                if sell_price is None:
                    continue
                ret = sell_price / pos["entry_price"] - 1.0
                sell_cost = 1.0 - config.COMMISSION_RATE - config.TAX_RATE - config.SLIPPAGE
                proceeds = shares * sell_price * sell_cost
                cash += proceeds
                trade_log.append({
                    "entry_date": pos["entry_date"],
                    "exit_date": current_date,
                    "ticker": ticker,
                    "buy_price": pos["entry_price"],
                    "sell_price": sell_price,
                    "shares": shares,
                    "return_pct": ret,
                    "hold_days": date_ordinal[current_date] - date_ordinal[pos["entry_date"]],
                    "exit_reason": "expiry",
                })
                del positions[ticker]

            # 1b. 조건부 매도 (전일 종가로 결정, 당일 시가로 정산)
            for ticker, shares, reason in conditional_sell_queue:
                pos = positions.get(ticker)
                if pos is None:
                    continue
                sell_price = _price(ticker, current_ts, "open")
                if sell_price is None:
                    continue
                ret = sell_price / pos["entry_price"] - 1.0
                sell_cost = 1.0 - config.COMMISSION_RATE - config.TAX_RATE - config.SLIPPAGE
                proceeds = shares * sell_price * sell_cost
                cash += proceeds
                trade_log.append({
                    "entry_date": pos["entry_date"],
                    "exit_date": current_date,
                    "ticker": ticker,
                    "buy_price": pos["entry_price"],
                    "sell_price": sell_price,
                    "shares": shares,
                    "return_pct": ret,
                    "hold_days": date_ordinal[current_date] - date_ordinal[pos["entry_date"]],
                    "exit_reason": reason,
                })
                del positions[ticker]

            # 매도 큐 초기화 (Phase 2에서 다시 채워짐)
            expiry_sell_queue = []
            conditional_sell_queue = []

            # 1c. 매수 주문 실행 (전일 종가 × 0.99로 지정)
            for ticker, limit_price, target_shares in buy_order_queue:
                if ticker in positions:
                    continue  # 이미 보유 중 — 건너뜀 (매도 큐 우선)
                d_low = _price(ticker, current_ts, "low")
                if d_low is None or d_low > limit_price:
                    continue  # 체결되지 않음
                cost = target_shares * limit_price * (1.0 + config.COMMISSION_RATE + config.SLIPPAGE)
                if cash < cost or target_shares <= 0:
                    continue
                cash -= cost
                positions[ticker] = {
                    "shares": target_shares,
                    "entry_price": limit_price,
                    "entry_date": current_date,
                }
                trade_log.append({
                    "entry_date": current_date,
                    "exit_date": None,
                    "ticker": ticker,
                    "buy_price": limit_price,
                    "sell_price": None,
                    "shares": target_shares,
                    "return_pct": None,
                    "hold_days": 0,
                    "exit_reason": None,
                })

            buy_order_queue = []

            # ══════════════════════════════════════════════════════════
            # PHASE 2 — 새로운 결정 (전일 데이터 사용)
            # ══════════════════════════════════════════════════════════

            prev_date_obj = prev_date if isinstance(prev_date, date) else pd.Timestamp(prev_date).date()

            # 2a. 보유 포지션의 매도 조건 평가
            for ticker, pos in list(positions.items()):
                prev_close = _price(ticker, prev_ts, "close")  # type: ignore[arg-type]
                if prev_close is None:
                    continue

                hold_days = date_ordinal[prev_date] - date_ordinal[pos["entry_date"]]

                reason = self.strategy.evaluate_sell_conditions(
                    position=pos,
                    current_price=prev_close,
                    entry_price=pos["entry_price"],
                    hold_days=hold_days,
                )
                if reason is None:
                    continue

                if reason == "expiry":
                    expiry_sell_queue.append((ticker, pos["shares"]))
                else:
                    conditional_sell_queue.append((ticker, pos["shares"], reason))

            # 2b. 신규 포지션의 매수 조건 평가
            index_buy = _nearest_index_signal(prev_date_obj, "buy_signal")

            if index_buy:
                # prev_date의 조건 데이터 가져오기
                prev_cond = cond_lookup.get(prev_date_obj, {})
                qualifying = [
                    row for row in prev_cond.values()
                    if row.get("all_buy_conditions", False)
                    and row["ticker"] not in positions
                ]

                if qualifying:
                    # priority_score 내림차순 정렬
                    qualifying.sort(key=lambda r: -r.get("priority_score", 0.0))

                    # 최대 config.MAX_HOLDINGS개 신규 포지션
                    remaining_slots = config.MAX_HOLDINGS - len(positions)
                    selected = qualifying[:remaining_slots] if remaining_slots > 0 else []

                    # 현재 NAV (전일 종가 사용)
                    nav = cash
                    for tkr, pos in positions.items():
                        p = _price(tkr, prev_ts, "close")  # type: ignore[arg-type]
                        if p is not None:
                            nav += pos["shares"] * p

                    for row in selected:
                        ticker = row["ticker"]
                        prev_close = _price(ticker, prev_ts, "close")  # type: ignore[arg-type]
                        if prev_close is None or prev_close <= 0:
                            continue
                        limit_price = round(prev_close * config.BUY_PRICE_OFFSET)
                        if limit_price <= 0:
                            continue
                        target_value = nav / config.MAX_HOLDINGS
                        target_shares = int(target_value / limit_price)
                        if target_shares <= 0:
                            continue
                        buy_order_queue.append((ticker, limit_price, target_shares))

            # 2c. 스냅샷 기록 (당일 종가 사용)
            holdings_value = 0.0
            for tkr, pos in positions.items():
                close_p = _price(tkr, current_ts, "close")
                if close_p is not None:
                    holdings_value += pos["shares"] * close_p

            nav = cash + holdings_value
            snapshots.append({
                "date": current_date,
                "cash": cash,
                "holdings_value": holdings_value,
                "nav": nav,
                "num_positions": len(positions),
            })

        # ══════════════════════════════════════════════════════════════
        # 결과 생성
        # ══════════════════════════════════════════════════════════════
        snapshots_df = pd.DataFrame(snapshots)
        if not snapshots_df.empty:
            snapshots_df["daily_return"] = snapshots_df["nav"].pct_change().fillna(0.0)
            daily_returns = snapshots_df.set_index("date")["daily_return"]
        else:
            daily_returns = pd.Series(dtype=float)

        trade_log_df = pd.DataFrame(trade_log)

        return {
            "portfolio_snapshots": snapshots_df,
            "trade_log": trade_log_df,
            "daily_returns": daily_returns,
        }

    # ── 헬퍼 ───────────────────────────────────────────────────────

    @staticmethod
    def _empty_result() -> dict[str, Any]:
        """빈 결과 딕셔너리를 반환합니다."""
        return {
            "portfolio_snapshots": pd.DataFrame(
                columns=["date", "cash", "holdings_value", "nav", "num_positions"],
            ),
            "trade_log": pd.DataFrame(
                columns=[
                    "entry_date", "exit_date", "ticker", "buy_price",
                    "sell_price", "shares", "return_pct", "hold_days", "exit_reason",
                ],
            ),
            "daily_returns": pd.Series(dtype=float),
        }
