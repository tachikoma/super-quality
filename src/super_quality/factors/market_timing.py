"""시장 타이밍 팩터 계산.

이 팩터는 KOSDAQ 지수 종가에 대한 3일, 5일, 10일 단순 이동 평균을 계산하고
매수·매도 신호를 생성합니다.
"""


import pandas as pd

from super_quality.factors.base import Factor


class KosdaqMAFactor(Factor):
    """KOSDAQ 이동 평균 타이밍 신호.

    KOSDAQ 지수 종가의 3일, 5일, 10일 단순 이동 평균을 계산하여
    매수/매도 신호를 생성합니다:

    * **매수 신호** — `close > ANY` (MA3, MA5, MA10 중 하나라도 높음) — OR 조건
    * **매도 신호** — `close < MA3` AND `close < MA5` — AND 조건
      (전략 명세에 따라 MA10은 매도 조건에 포함되지 않음)

    처음 10개 행은 이동 평균값이 NaN이므로, 두 신호 모두
    `False`로 강제 설정됩니다.
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
            컬럼: ``date``, ``ma3``, ``ma5``, ``ma10``,
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

        # 매수: close > ANY (MA3, MA5, MA10) — OR 조건
        df["buy_signal"] = (
            (close > df["ma3"]) | (close > df["ma5"]) | (close > df["ma10"])
        )

        # 매도: close < MA3 AND close < MA5 (MA10 포함되지 않음)
        df["sell_signal"] = (close < df["ma3"]) & (close < df["ma5"])

        # 처음 10개 행은 불완전한 MA — 신호를 False로 강제 설정
        # .loc을 사용하여 pandas Copy-on-Write 체인 할당 문제 방지
        df.loc[df.index[:10], "buy_signal"] = False
        df.loc[df.index[:10], "sell_signal"] = False

        # 출력 date 컬럼 구성
        if isinstance(data, pd.DataFrame) and "date" in data.columns:
            df["date"] = data["date"]
        else:
            df["date"] = data.index

        df = df.reset_index(drop=True)
        return df[["date", "ma3", "ma5", "ma10", "buy_signal", "sell_signal"]]
