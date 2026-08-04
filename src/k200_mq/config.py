"""KOSPI 200 Momentum + Quality 백테스팅용 구성 관리.

pydantic-settings를 사용하여 환경 변수와 .env 파일에서 설정을 로드합니다.
"""

from pathlib import Path
import math

from pydantic import Field, field_validator, model_validator
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
    LOCAL_DART_FILING_PATH: str = Field(
        default="",
        description="선택적 로컬 DART filing metadata 파일 경로",
    )
    LOCAL_DART_FILING_MANIFEST: str = Field(
        default="",
        description="로컬 DART filing metadata acquisition manifest 경로",
    )
    LOCAL_DART_FINANCIAL_PATH: str = Field(
        default="",
        description="선택적 로컬 DART financial facts 파일 경로",
    )
    LOCAL_DART_FINANCIAL_MANIFEST: str = Field(
        default="",
        description="로컬 DART financial facts acquisition manifest 경로",
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
        description="동시 보유 최대 종목 수 (0보다 큰 정수)",
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
        description="활성화 시 고점 대비 trailing stop 기준 (-1.0 초과, 0 미만; 기본 -15%)",
    )
    ENABLE_STOP_LOSS: bool = Field(
        default=True,
        description="trailing stop-loss 주문 생성 여부 (기본 활성화)",
    )

    @field_validator("SL_STOP_LOSS")
    @classmethod
    def validate_stop_loss_threshold(cls, value: float) -> float:
        """Reject non-finite thresholds; the active-domain check is conditional."""
        if not math.isfinite(value):
            raise ValueError("SL_STOP_LOSS must be finite")
        return value

    @model_validator(mode="after")
    def validate_stop_loss_domain(self) -> "BacktestConfig":
        """Require a usable fractional loss threshold when stops are enabled.

        A disabled stop-loss retains its configured value for reproducibility,
        but the execution engine must not interpret that value as an order
        trigger.  This lets callers preserve an existing threshold while
        explicitly disabling the feature.
        """
        if self.ENABLE_STOP_LOSS and not -1.0 < self.SL_STOP_LOSS < 0.0:
            raise ValueError(
                "SL_STOP_LOSS must satisfy -1.0 < SL_STOP_LOSS < 0.0 "
                "when ENABLE_STOP_LOSS=True"
            )
        return self

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
        description=(
            "미지원/Deferred: 현재 유니버스 로더가 KOSPI 200 이력을 직접 결정하며 "
            "이 설정을 소비하지 않음"
        ),
    )
    LOCAL_PIT_UNIVERSE_PATH: str = Field(
        default="",
        description="선택적 로컬 PIT 유니버스 파일 경로 (미설정 시 기존 proxy 로더 사용)",
    )
    LOCAL_PIT_UNIVERSE_SOURCE_KIND: str = Field(
        default="",
        description="로컬 PIT 유니버스 형식 (snapshots 또는 intervals; 기본 snapshots)",
    )
    LOCAL_PIT_UNIVERSE_MANIFEST: str = Field(
        default="",
        description="선택적 로컬 PIT 유니버스 acquisition manifest 경로",
    )
    LOCAL_PIT_SECTOR_PATH: str = Field(
        default="",
        description=(
            "선택적 로컬 PIT 섹터 맵 구간 파일 경로 "
            "(미설정 시 섹터 맵 준비를 건너뜀)"
        ),
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
        description="ENABLE_ADV_FILTER=True일 때 적용되는 최소 ADV turnover 비율",
    )
    ENABLE_ADV_FILTER: bool = Field(
        default=False,
        description="리밸런싱 후보에 ADV turnover 기반 유동성 필터 적용 여부",
    )
    ADV_LOOKBACK_DAYS: int = Field(
        default=20,
        description="ADV turnover 계산에 사용하는 trailing 룩백 일수 (최소 5)",
    )

    # ── 모멘텀 팩터 ────────────────────────────────────────────
    MOMENTUM_WINDOW_SHORT: int = Field(
        default=126,
        description=(
            "진단 전용: momentum_6m 표시용 룩백 (거래일, 6개월); "
            "랭킹·민감도·readiness에는 사용하지 않음"
        ),
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
        description="미지원/Deferred: 52주 고점 보조 시그널 (현재 랭킹에 적용하지 않음)",
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
        description="미지원/Deferred: TTM 분기 필터 (현재 inert; 필터링하지 않음)",
    )

    @model_validator(mode="after")
    def validate_quality_weights(self) -> "K200MQConfig":
        """Validate the quality composite weight contract at config load time."""
        weights = (
            self.QUALITY_WEIGHT_ROE,
            self.QUALITY_WEIGHT_DE,
            self.QUALITY_WEIGHT_OPMARGIN,
            self.QUALITY_WEIGHT_CASHCONV,
        )
        if any(not math.isfinite(weight) or weight < 0 for weight in weights):
            raise ValueError("quality component weights must be finite and nonnegative")
        if sum(weights) <= 0:
            raise ValueError("quality component weights must have a positive sum")
        return self

    @model_validator(mode="after")
    def validate_portfolio_limits(self) -> "K200MQConfig":
        """Validate portfolio limits consumed by strategy and engine."""
        if self.MAX_HOLDINGS < 1:
            raise ValueError("MAX_HOLDINGS must be at least 1")
        if not 0.0 <= self.MIN_CASH_RATIO <= 1.0:
            raise ValueError("MIN_CASH_RATIO must satisfy 0.0 <= MIN_CASH_RATIO <= 1.0")
        if self.ENABLE_SECTOR_CAP and not 0.0 < self.SECTOR_CAP <= 1.0:
            raise ValueError(
                "SECTOR_CAP must satisfy 0.0 < SECTOR_CAP <= 1.0 when ENABLE_SECTOR_CAP=True"
            )
        if self.ENABLE_CORRELATION_FILTER and not -1.0 <= self.MAX_PAIR_CORRELATION <= 1.0:
            raise ValueError(
                "MAX_PAIR_CORRELATION must satisfy -1.0 <= MAX_PAIR_CORRELATION <= 1.0 "
                "when ENABLE_CORRELATION_FILTER=True"
            )
        if self.ENABLE_CORRELATION_FILTER and self.CORRELATION_LOOKBACK_DAYS < 20:
            raise ValueError(
                "CORRELATION_LOOKBACK_DAYS must be at least 20 when "
                "ENABLE_CORRELATION_FILTER=True"
            )
        if self.ENABLE_ADV_FILTER and not 0.0 <= self.MIN_ADV_RATIO <= 1.0:
            raise ValueError(
                "MIN_ADV_RATIO must satisfy 0.0 <= MIN_ADV_RATIO <= 1.0 when "
                "ENABLE_ADV_FILTER=True"
            )
        if self.ENABLE_ADV_FILTER and self.ADV_LOOKBACK_DAYS < 5:
            raise ValueError(
                "ADV_LOOKBACK_DAYS must be at least 5 when ENABLE_ADV_FILTER=True"
            )
        return self

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
        description=(
            "리짓 활성화 시 최소 20거래일 누적 수익률 threshold "
            "(기본 0.0; return window는 20일로 고정)"
        ),
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
        description="ENABLE_SECTOR_CAP=True일 때 섹터별 최대 노출 상한 (0 초과 1 이하)",
    )
    ENABLE_SECTOR_CAP: bool = Field(
        default=False,
        description="로컬 PIT 섹터 맵이 준비된 경우 섹터 노출 상한(SECTOR_CAP) 적용 여부",
    )
    ENABLE_CORRELATION_FILTER: bool = Field(
        default=False,
        description="리밸런싱 후보 내 고상관 페어를 제한하는 상관관계 제약 적용 여부",
    )
    MAX_PAIR_CORRELATION: float = Field(
        default=0.90,
        description=(
            "ENABLE_CORRELATION_FILTER=True일 때 허용되는 후보 간 최대 pairwise 상관계수 "
            "(-1 이상 1 이하)"
        ),
    )
    CORRELATION_LOOKBACK_DAYS: int = Field(
        default=60,
        description=(
            "ENABLE_CORRELATION_FILTER=True일 때 pairwise 상관계수 계산에 사용하는 "
            "수익률 룩백 일수 (최소 20)"
        ),
    )
    MIN_CASH_RATIO: float = Field(
        default=0.05,
        description="최소 현금 버퍼 비율 (0.0 이상 1.0 이하)",
    )
    MAX_POSITION_WEIGHT: float = Field(
        default=0.10,
        description="단일 포지션 최대 비중 (10%)",
    )

    # ── 종목 제외 ──────────────────────────────────────────────
    EXCLUDE_MANAGEMENT: bool = Field(
        default=True,
        description="unsupported/inert: runtime consumer 없음; 호환성을 위해 유지",
    )
    EXCLUDE_INVESTMENT_NOTICE: bool = Field(
        default=True,
        description="unsupported/inert: runtime consumer 없음; 호환성을 위해 유지",
    )
    EXCLUDE_PREFERRED: bool = Field(
        default=True,
        description="unsupported/inert: runtime consumer 없음; 호환성을 위해 유지",
    )
    EXCLUDE_ETF_ETN: bool = Field(
        default=True,
        description="unsupported/inert: runtime consumer 없음; 호환성을 위해 유지",
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
    PRINT_SUMMARY: bool = Field(
        default=True,
        description="백테스트 완료 후 대화형 요약을 출력할지 여부",
    )
