"""슈퍼 퀄리티 백테스팅용 구성 관리.

pydantic-settings를 사용하여 환경 변수와 .env 파일에서 설정을 로드합니다.
"""  # noqa: D200

import os
from datetime import date
from pathlib import Path
from warnings import warn

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SuperQualityConfig(BaseSettings):
    """슈퍼 퀄리티 2.0 전략의 백테스팅 구성.

    필수 값은 ``DART_API_KEY`` (https://opendart.fss.or.kr)와
    ``KRX_ID`` / ``KRX_PW`` (https://data.krx.co.kr)이며,
    환경 변수 또는 ``.env`` 파일에 설정해야 합니다.
    """  # noqa: D400

    # ── API ──────────────────────────────────────────────────────────
    DART_API_KEY: str = Field(
        default="",
        description="opendart.fss.or.kr의 OpenDartReader API 키",
    )
    KRX_ID: str = Field(
        default="",
        description="KRX 데이터(data.krx.co.kr) 로그인 ID (pykrx)",
    )
    KRX_PW: str = Field(
        default="",
        description="KRX 데이터(data.krx.co.kr) 로그인 비밀번호 (pykrx)",
    )

    # ── 백테스트 기간 ──────────────────────────────────────────────
    START_DATE: date = Field(default=date(2015, 1, 1))
    END_DATE: date = Field(default=date.today())

    # ── 포트폴리오 ───────────────────────────────────────────────────
    INITIAL_CAPITAL: int = Field(
        default=100_000_000,
        description="초기 자본금 (원) (1억 원)",
    )
    MAX_HOLDINGS: int = Field(
        default=20,
        description="동시 보유 가능한 최대 종목 수",
    )
    POSITION_SIZE: float = Field(
        default=0.10,
        description="NAV 대비 최대 포지션 크기 (10%)",
    )
    BUY_PRICE_OFFSET: float = Field(
        default=0.99,
        description="매수 가격 = 전일 종가 × 오프셋 (-1%)",
    )
    MAX_HOLD_DAYS: int = Field(
        default=20,
        description="최대 보유 기간 (거래일 기준)",
    )
    STOP_LOSS: float = Field(
        default=-0.20,
        description="손절 기준 (-20%)",
    )
    TAKE_PROFIT: float = Field(
        default=0.30,
        description="이익실현 기준 (+30%)",
    )

    # ── 비용 ─────────────────────────────────────────────────────────
    COMMISSION_RATE: float = Field(
        default=0.00015,
        description="증권사 수수료 (0.015%)",
    )
    TAX_RATE: float = Field(
        default=0.0020,
        description="거래세 (0.20%, 매도 시에만 부과)",
    )
    SLIPPAGE: float = Field(
        default=0.001,
        description="슬리피지 가정 (0.1%)",
    )

    # ── 전략 파라미터 ───────────────────────────────────────────────
    SUPPLY_SCORE_DAYS: int = Field(
        default=5,
        description="공급 점수를 계산할 개인 순매수 누적 일수",
    )
    PBR_PERCENTILE: float = Field(
        default=0.20,
        description="PBR 하위 퍼센타일 기준값 (20%)",
    )
    MCAP_PERCENTILE: float = Field(
        default=0.40,
        description="시가총액 하위 퍼센타일 기준값 (40%)",
    )
    RELAXED_ENTRY_MODE: bool = Field(
        default=True,
        description="True: 6/8 조건 (핵심 A,B,E,F 모두 + 보조 C,D,G,H 중 ≥2). False: 8개 모두 AND",
    )

    # ── 시장 데이터 ──────────────────────────────────────────────────
    MARKET_TIMING_TICKER: str = Field(
        default="KQ11",
        description="FinanceDataReader용 시장 타이밍 지수 티커 (KOSDAQ=KQ11)",
    )

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent.parent / ".env",
        env_file_encoding="utf-8",
        validate_default=True,
    )

    def model_post_init(self, __context: object) -> None:
        """설정 검증 및 pykrx/OpenDartReader를 위한 환경 변수 설정."""
        if self.KRX_ID and self.KRX_PW:
            os.environ.setdefault("KRX_ID", self.KRX_ID)
            os.environ.setdefault("KRX_PW", self.KRX_PW)
        if self.DART_API_KEY:
            os.environ.setdefault("DART_API_KEY", self.DART_API_KEY)
        if not self.DART_API_KEY:
            warn(
                "DART_API_KEY가 비어 있습니다 — OpenDartReader를 통한 "
                "재무 데이터 조회가 실패합니다. "
                "DART_API_KEY를 환경 변수 또는 .env 파일에 설정하십시오.",
                stacklevel=2,
            )