"""KOSPI 200 Momentum + Quality 백테스팅용 구성 관리.

pydantic-settings를 사용하여 환경 변수와 .env 파일에서 설정을 로드합니다.
"""

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BacktestConfig(BaseSettings):
    """백테스팅 공통 구성.

    모든 전략이 공유하는 기본 설정을 정의합니다.
    """

    # ── API ──────────────────────────────────────────────────
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

    # ── 백테스트 기간 ──────────────────────────────────────
    START_DATE: str = Field(default="2015-01-01")
    END_DATE: str = Field(default="today")
    STRICT_PIT_VALIDATION: bool = Field(
        default=False,
        description=(
            "PIT universe 및 명시적 source/schema filing contract와 meaningful "
            "timestamp 또는 conservative next-session 재무 데이터가 없으면 실행을 중단합니다 "
            "(기본값: False)"
        ),
    )

    # ── 포트폴리오 ───────────────────────────────────────────
    INITIAL_CAPITAL: int = Field(
        default=100_000_000,
        description="초기 자본금 (원) (1억 원)",
    )
    MAX_HOLDINGS: int = Field(
        default=20,
        description="동시 보유 가능한 최대 종목 수",
    )

    # ── 비용 ─────────────────────────────────────────────────
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
    SL_STOP_LOSS: float = Field(
        default=-0.15,
        description="고점 대비 trailing stop 기준 (기본 -15%)",
    )

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class K200MQConfig(BacktestConfig):
    """KOSPI 200 Momentum + Quality 전략 전용 구성.

    BacktestConfig를 상속하여 공통 설정을 유지하면서
    전략 고유 파라미터를 추가합니다.
    """

    # ── 유니버스 ───────────────────────────────────────────────
    UNIVERSE_SIZE: int = Field(
        default=200,
        description="KOSPI 200 종목 수 (상위 200 by market cap)",
    )
    EXCLUDE_KOSPI_TOP_N: int = Field(
        default=50,
        description=(
            "모멘텀 스코어링에서 제외하는 상위 시가총액 종목 수 "
            "(strict PIT에서는 0만 허용)"
        ),
    )
    MIN_ADV_RATIO: float = Field(
        default=0.01,
        description="최소 유동성 비율 (포지션 크기 / ADV)",
    )

    # ── 모멘텀 팩터 ────────────────────────────────────────────
    MOMENTUM_WINDOW_SHORT: int = Field(
        default=126,
        description="단기 모멘텀 룩백 (거래일, 6개월)",
    )
    MOMENTUM_WINDOW_LONG: int = Field(
        default=252,
        description="장기 모멘텀 룩백 (거래일, 12개월)",
    )
    MOMENTUM_SKIP_DAYS: int = Field(
        default=42,
        description="형성 기간 마지막 skip 일수 (한국 2개월 반전 회피)",
    )
    USE_52WEEK_HIGH: bool = Field(
        default=True,
        description="52주 고점 비율을 보조 시그널로 사용",
    )

    # ── 품질 팩터 ──────────────────────────────────────────────
    QUALITY_WEIGHT_ROE: float = Field(
        default=0.35,
        description="ROE 가중치",
    )
    QUALITY_WEIGHT_DE: float = Field(
        default=0.25,
        description="부채비율 가중치 (낮을수록 좋은 방향)",
    )
    QUALITY_WEIGHT_OPMARGIN: float = Field(
        default=0.20,
        description="영업이익률 가중치",
    )
    QUALITY_WEIGHT_CASHCONV: float = Field(
        default=0.20,
        description="현금전환율 가중치",
    )
    QUALITY_MIN_TTM_QUARTERS: int = Field(
        default=3,
        description="품질 팩터 계산에 필요한 최소 TTM 분기 수",
    )

    # ── 리짓 필터 ──────────────────────────────────────────────
    REGIME_FILTER_ENABLED: bool = Field(
        default=True,
        description="KOSPI 200 MA200 리짓 필터 사용 여부",
    )
    REGIME_MA_PERIOD: int = Field(
        default=200,
        description="리짓 MA 기간 (거래일)",
    )
    REGIME_MIN_RETURN: float = Field(
        default=0.0,
        description="리짓 활성화 시 최소 20일 수익률 (0=MA200 위에 있으면 활성)",
    )
    REGIME_REDUCTION: float = Field(
        default=0.50,
        description="리�트 비활성 시 포지션 축소 비율 (0.5 = 50%)",
    )

    # ── 리밸런싱 ──────────────────────────────────────────────
    REBALANCE_FREQ: str = Field(
        default="M",
        description="리밸런싱 주기 (M=월간, Q=분기)",
    )

    # ── 포트폴리오 구성 ────────────────────────────────────────
    TOP_N: int = Field(
        default=20,
        description="선택 종목 수",
    )
    WEIGHT_METHOD: str = Field(
        default="equal",
        description="포지션 배분 방법 (equal 또는 rank_weighted)",
    )
    SECTOR_CAP: float = Field(
        default=0.30,
        description="섹션별 최대 노출 비율 (30%)",
    )
    MIN_CASH_RATIO: float = Field(
        default=0.05,
        description="최소 현금 버퍼 비율 (5%)",
    )
    MAX_POSITION_WEIGHT: float = Field(
        default=0.10,
        description="단일 포지션 최대 비중 (10%)",
    )

    # ── 종목 제외 ──────────────────────────────────────────────
    EXCLUDE_MANAGEMENT: bool = Field(
        default=True,
        description="관리종목 제외",
    )
    EXCLUDE_INVESTMENT_NOTICE: bool = Field(
        default=True,
        description="투자주의 종목 제외",
    )
    EXCLUDE_PREFERRED: bool = Field(
        default=True,
        description="우선주 제외",
    )
    EXCLUDE_ETF_ETN: bool = Field(
        default=True,
        description="ETF/ETN 제외",
    )

    # ── 모멘텀+품질 가중치 ─────────────────────────────────────
    WEIGHT_MOMENTUM: float = Field(
        default=0.50,
        description="모멘텀 팩터 가중치",
    )
    WEIGHT_QUALITY: float = Field(
        default=0.50,
        description="품질 팩터 가중치",
    )

    # ── 시장 지수 ──────────────────────────────────────────────
    MARKET_INDEX_TICKER: str = Field(
        default="KPI200",
        description="FinanceDataReader용 시장 지수 티커 (KOSPI 200=KPI200)",
    )

    # ── 출력 ────────────────────────────────────────────────────
    OUTPUT_DIR: str = Field(
        default="outputs_k200mq",
        description="백테스트 결과 출력 디렉토리",
    )
