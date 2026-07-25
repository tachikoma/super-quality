"""시장 타이밍 팩터 계산.

이 팩터는 KOSDAQ 지수 종가에 대한 3일, 5일, 10일 단순 이동 평균을 계산하고
매수·매도 신호를 생성합니다.
"""


import pandas as pd

from super_quality.factors.base import Factor


class KosdaqMAFactor(Factor):
    """KOSDAQ 이동 평균 타이밍 신호.

    KOSDAQ 지수 종가의 3일, 5일, 10일, 20일 단순 이동 평균을 계산합니다:

    * **매수 신호** — `close > MA20` (20일 추세 필터 — 안정적 상승 추세)
    * **매도 신호** — `close < MA3` AND `close < MA5` (단기 하락 — 현재 미사용)

    MA20을 사용하는 이유: 단기 MA(3/5/10)는 노이즈가 심해 KOSDAQ
    하락장에서 전체 매수가 차단되던 문제를 완화. MA20은 후행성이 있어
    바닥권 회복 시 더 빠른 매수 진입이 가능.
    """

    @property
    def name(self) -> str:
        return "KosdaqMA"

    def compute(self, data: pd.Series | pd.DataFrame) -> pd.DataFrame:
        """MA 기반 타이밍 신호를 계산합니다.

        Parameters
        ----------
        data : pd.Series or pd.DataFrame
            KOSDAQ 종가 시리즈, 또는 ``close`` 컬럼을 가진 DataFrame.
            index (또는 ``date`` 컬럼이 있으면 해당 컬럼)가
            출력 ``date`` 컬럼으로 사용됩니다.

        Returns
        -------
        pd.DataFrame
            컬럼: ``date``, ``ma3``, ``ma5``, ``ma10``, ``ma20``,
            ``buy_signal``, ``sell_signal``.
        """
        # 입력값을 'close' 시리즈를 가진 DataFrame으로 정규화
        if isinstance(data, pd.Series):
            close = data
        else:
            close = data["close"]

        df = pd.DataFrame({"close": close})

        # 단순 이동 평균
        df["ma3"] = close.rolling(3).mean()
        df["ma5"] = close.rolling(5).mean()
        df["ma10"] = close.rolling(10).mean()
        df["ma20"] = close.rolling(20).mean()

        # 매수: close > MA20 (20일 추세 필터)
        df["buy_signal"] = close > df["ma20"]

        # 매도: close < MA3 AND close < MA5 (현재 미사용)
        df["sell_signal"] = (close < df["ma3"]) & (close < df["ma5"])

        # 처음 20개 행은 불완전한 MA — 신호를 False로 강제 설정
        df.loc[df.index[:20], "buy_signal"] = False
        df.loc[df.index[:20], "sell_signal"] = False

        # 출력 date 컬럼 구성
        if isinstance(data, pd.DataFrame) and "date" in data.columns:
            df["date"] = data["date"]
        else:
            df["date"] = data.index

        df = df.reset_index(drop=True)
        return df[["date", "ma3", "ma5", "ma10", "ma20", "buy_signal", "sell_signal"]]
