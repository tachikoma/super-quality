"""KOSPI 200 MQ — Shared core infrastructure.

Reusable components extracted from the legacy super_quality package.
"""

from k200_mq.core.cache import DataCache as DataCache
from k200_mq.core.factors.base import Factor as Factor
from k200_mq.core.analysis.metrics import (
    PerformanceMetrics as PerformanceMetrics,
    benchmark_metadata as benchmark_metadata,
    build_benchmark_returns as build_benchmark_returns,
    build_price_return_benchmark as build_price_return_benchmark,
    compute_cost_attribution as compute_cost_attribution,
)
from k200_mq.core.reporting.report import ReportGenerator as ReportGenerator
