"""Super Quality 2.0 전략: 일별 신호 생성.

매수 조건 A-H를 평가하고, 우선순위(GP/A + 공급 점수)를 할당하며,
매도 신호(손절, 시장 타이밍, 만기)를 관리합니다.
"""

from __future__ import annotations

import pandas as pd

from super_quality.config import SuperQualityConfig


class SuperQualityStrategy:
    """Super Quality 2.0 전략: 일별 신호 생성.

    Parameters
    ----------
    config : SuperQualityConfig
        임계값, 포트폴리오 제한, 비용이 포함된 전략 설정.
    """

    def __init__(self, config: SuperQualityConfig) -> None:
        self.config = config

    # ── 매수 조건 ────────────────────────────────────────────────

    def evaluate_buy_conditions(self, df: pd.DataFrame) -> pd.DataFrame:
        """각 ticker 행에 대해 조건 A-H를 평가합니다.

        Parameters
        ----------
        df : pd.DataFrame
            다음 컬럼을 포함해야 함:
            - ``ticker``
            - ``pbr``                      (조건 B)
            - ``pbr_percentile``           (조건 A)
            - ``mcap_percentile``          (조건 G)
            - ``share_change_5mo_ago``     (조건 C)
            - ``share_change_now``         (조건 D)
            - ``trailing_ni``              (조건 E)
            - ``trailing_ocf``             (조건 F)
            - ``kosdaq_buy_signal``        (조건 H)
            - ``gpa_percentile``           (우선순위 점수)
            - ``supply_percentile``        (우선순위 점수)

        Returns
        -------
        pd.DataFrame
            입력과 동일한 행, 추가 컬럼:
            - ``a_pass`` … ``h_pass``  (bool)
            - ``all_buy_conditions``   (bool — A-H **모두** 통과시만 True)
            - ``priority_score``       (float — gpa_percentile + supply_percentile)
        """
        result = df[["ticker"]].copy()

        # A: PBR 백분위 ≤ 임계값 (낮은 PBR = 저렴)
        result["a_pass"] = df["pbr_percentile"] <= self.config.PBR_PERCENTILE * 100.0

        # B: PBR > 0 (양수 장부가치)
        result["b_pass"] = df["pbr"] > 0

        # C: 5개월 전 주식 발행 없음
        result["c_pass"] = df["share_change_5mo_ago"] == 0

        # D: 현재 기간 주식 발행 없음
        result["d_pass"] = df["share_change_now"] == 0

        # E: 최근 당기순이익 > 0
        result["e_pass"] = df["trailing_ni"] > 0

        # F: 최근 영업현금흐름 > 0
        result["f_pass"] = df["trailing_ocf"] > 0

        # G: 시가총액 ≤ 임계값 백분위 (소형주)
        result["g_pass"] = df["mcap_percentile"] <= self.config.MCAP_PERCENTILE * 100.0

        # H: KOSDAQ 매수 타이밍 신호 (종가 > MA3, MA5, MA10 중 하나)
        result["h_pass"] = df["kosdaq_buy_signal"].astype(bool)

        # 8가지 조건 모두 통과해야 함
        condition_cols = [
            "a_pass", "b_pass", "c_pass", "d_pass",
            "e_pass", "f_pass", "g_pass", "h_pass",
        ]
        result["all_buy_conditions"] = result[condition_cols].all(axis=1)

        # 우선순위 점수 = GP/A 백분위 + 공급 백분위 (내림차순 정렬)
        result["priority_score"] = 0.0
        mask = result["all_buy_conditions"]
        result.loc[mask, "priority_score"] = (
            df.loc[mask, "gpa_percentile"] + df.loc[mask, "supply_percentile"]
        )

        return result

    # ── 매도 조건 ───────────────────────────────────────────────

    def evaluate_sell_conditions(
        self,
        position: dict,
        current_price: float,
        entry_price: float,
        hold_days: int,
        kosdaq_sell_signal: bool,
    ) -> str | None:
        """단일 포지션의 매도 조건을 평가합니다.

        우선순위 (첫 번째 매치 승리):
        1. ``stop_loss``     — 수익률 ≤ ``config.STOP_LOSS`` (-7 %)
        2. ``market_timing`` — KOSDAQ 매도 신호가 ``True``
        3. ``expiry``        — ``hold_days ≥ config.MAX_HOLD_DAYS`` (5)

        Parameters
        ----------
        position : dict
            ``ticker``, ``shares``, ``entry_price``, ``entry_date``를
            포함하는 포지션 딕셔너리 (*hold_days*를 통해 간접 사용).
        current_price : float
            수익률 계산을 위한 현재 (또는 전일 종가) 가격.
        entry_price : float
            포지션 진입 가격.
        hold_days : int
            포지션 보유 거래일 수.
        kosdaq_sell_signal : bool
            KOSDAQ 지수가 매도 신호를 트리거했는지 여부.

        Returns
        -------
        str or None
            ``'stop_loss'`` | ``'market_timing'`` | ``'expiry'`` | ``None``
        """
        _ = position  # 향후 사용을 위해 예약됨 (예: ticker별 로직)

        # 1. 손절 — 최우선 순위
        ret = current_price / entry_price - 1.0
        if ret <= self.config.STOP_LOSS:
            return "stop_loss"

        # 2. 시장 타이밍 — KOSDAQ 매도 신호
        if kosdaq_sell_signal:
            return "market_timing"

        # 3. 만기 — 최대 보유 기간 도달
        if hold_days >= self.config.MAX_HOLD_DAYS:
            return "expiry"

        return None
