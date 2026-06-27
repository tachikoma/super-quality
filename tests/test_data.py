"""Tests for ``super_quality.data`` — cache roundtrip, TTM, and lag logic.

All tests are pure unit tests: **no actual API calls** are made.
"""

from __future__ import annotations

import tempfile
from datetime import date

import pandas as pd
import pytest

from super_quality.data.cache import DataCache
from super_quality.data.loader import calculate_ttm, get_available_lag


# ═══════════════════════════════════════════════════════════════════
# DataCache
# ═══════════════════════════════════════════════════════════════════


class TestDataCache:
    """Parquet-based cache round-trip and miss detection."""

    def test_cache_roundtrip(self) -> None:
        """``put`` then ``get`` returns an identical DataFrame."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(tmpdir)
            original = pd.DataFrame(
                {
                    "a": [1, 2, 3],
                    "b": [4.0, 5.0, 6.0],
                    "c": ["x", "y", "z"],
                },
            )
            cache.put("roundtrip", original)
            retrieved = cache.get("roundtrip")

            assert retrieved is not None
            pd.testing.assert_frame_equal(retrieved, original)

    def test_cache_miss(self) -> None:
        """``get`` on a nonexistent key returns ``None``."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(tmpdir)
            result = cache.get("i_do_not_exist")
            assert result is None

    def test_cache_exists(self) -> None:
        """``exists`` returns ``True``/``False`` correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(tmpdir)
            assert not cache.exists("missing")
            cache.put("present", pd.DataFrame({"x": [1]}))
            assert cache.exists("present")

    def test_cache_clear(self) -> None:
        """``clear`` removes all Parquet files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(tmpdir)
            cache.put("a", pd.DataFrame({"x": [1]}))
            cache.put("b", pd.DataFrame({"y": [2]}))
            cache.clear()
            assert not cache.exists("a")
            assert not cache.exists("b")


# ═══════════════════════════════════════════════════════════════════
# calculate_ttm
# ═══════════════════════════════════════════════════════════════════


class TestCalculateTTM:
    """Trailing-twelve-month derivation from cumulative K-IFRS data."""

    def test_ttm_basic_single_year(self) -> None:
        """Annual cumulative values → TTM equals the annual figure."""
        # Cumulative revenue for one fiscal year:
        #   Q1 (Mar):    100  (single-quarter = 100)
        #   Semi (Jun):  250  (single-quarter = 150)
        #   Q3  (Sep):   450  (single-quarter = 200)
        #   Ann (Dec):   700  (single-quarter = 250)
        #   TTM = 100 + 150 + 200 + 250 = 700
        df = pd.DataFrame(
            {
                "date": [
                    date(2024, 3, 31),
                    date(2024, 6, 30),
                    date(2024, 9, 30),
                    date(2024, 12, 31),
                ],
                "revenue": [100, 250, 450, 700],
            },
        )
        result = calculate_ttm(df, "revenue", "date")
        # Only the last row (4 quarters) should have a TTM value
        assert not result.empty
        assert result.iloc[-1] == pytest.approx(700.0)

    def test_ttm_cross_year(self) -> None:
        """TTM spanning two fiscal years."""
        # Q1 2023:     80
        # Semi 2023:  180  (Q2 alone = 100)
        # Q3 2023:    330  (Q3 alone = 150)
        # Ann 2023:   530  (Q4 alone = 200)
        # Q1 2024:    110  (Q1 2024 alone = 110)
        # TTM as of Q1 2024 = Q2 2023 + Q3 2023 + Q4 2023 + Q1 2024
        #                    = 100 + 150 + 200 + 110 = 560
        df = pd.DataFrame(
            {
                "date": [
                    date(2023, 3, 31),
                    date(2023, 6, 30),
                    date(2023, 9, 30),
                    date(2023, 12, 31),
                    date(2024, 3, 31),
                ],
                "revenue": [80, 180, 330, 530, 110],
            },
        )
        result = calculate_ttm(df, "revenue", "date")
        # TTM at Q1 2024 should be 560
        assert pd.Timestamp("2024-03-31") in result.index
        assert result.loc[pd.Timestamp("2024-03-31")] == pytest.approx(560.0)

    def test_ttm_year_quarter_columns(self) -> None:
        """Fallback when ``date_col`` is missing but ``year``/``quarter`` exist."""
        df = pd.DataFrame(
            {
                "ticker": ["A"] * 4,
                "year": [2024, 2024, 2024, 2024],
                "quarter": [1, 2, 3, 4],
                "revenue": [100, 250, 450, 700],
            },
        )
        # Should construct dates from year/quarter automatically
        result = calculate_ttm(df, "revenue")
        assert not result.empty
        assert result.iloc[-1] == pytest.approx(700.0)

    def test_ttm_insufficient_data(self) -> None:
        """Fewer than 4 quarters → empty result."""
        df = pd.DataFrame(
            {
                "date": [date(2024, 3, 31), date(2024, 6, 30)],
                "revenue": [100, 250],
            },
        )
        result = calculate_ttm(df, "revenue", "date")
        assert result.empty


# ═══════════════════════════════════════════════════════════════════
# get_available_lag
# ═══════════════════════════════════════════════════════════════════


class TestGetAvailableLag:
    """Filing-deadline-aware financial period availability."""

    # ── Q1 (due May 15) ─────────────────────────────────────────

    def test_q1_available_after_may15(self) -> None:
        """After May 15 → Q1 of current year is available."""
        assert get_available_lag(date(2024, 6, 1)) == date(2024, 3, 31)

    def test_q1_on_deadline(self) -> None:
        """On May 15 → Q1 is available."""
        assert get_available_lag(date(2024, 5, 15)) == date(2024, 3, 31)

    # ── Annual (due Mar 31) ─────────────────────────────────────

    def test_annual_available_apr1(self) -> None:
        """April 1 → Q1 2024 still pending, latest is Annual 2023."""
        assert get_available_lag(date(2024, 4, 1)) == date(2023, 12, 31)

    def test_annual_on_deadline(self) -> None:
        """March 31 → Annual 2023 is available."""
        assert get_available_lag(date(2024, 3, 31)) == date(2023, 12, 31)

    # ── Before any deadline in the year ─────────────────────────

    def test_january_before_any_deadline(self) -> None:
        """January → latest is Q3 of previous year."""
        assert get_available_lag(date(2024, 1, 15)) == date(2023, 9, 30)

    def test_early_march_before_annual_deadline(self) -> None:
        """March 15 → before Mar 31 deadline → Q3 of previous year."""
        assert get_available_lag(date(2024, 3, 15)) == date(2023, 9, 30)

    # ── Semi-annual (due Aug 15) ────────────────────────────────

    def test_semi_annual_available_after_aug15(self) -> None:
        """After Aug 15 → semi-annual is available."""
        assert get_available_lag(date(2024, 8, 20)) == date(2024, 6, 30)

    def test_semi_annual_on_deadline(self) -> None:
        """On Aug 15 → semi-annual is available."""
        assert get_available_lag(date(2024, 8, 15)) == date(2024, 6, 30)

    # ── Q3 (due Nov 15) ─────────────────────────────────────────

    def test_q3_available_after_nov15(self) -> None:
        """After Nov 15 → Q3 is available."""
        assert get_available_lag(date(2024, 11, 20)) == date(2024, 9, 30)

    def test_q3_on_deadline(self) -> None:
        """On Nov 15 → Q3 is available."""
        assert get_available_lag(date(2024, 11, 15)) == date(2024, 9, 30)

    # ── Year-end ────────────────────────────────────────────────

    def test_december_q3_still_latest(self) -> None:
        """December → Q3 still latest (annual report not due yet)."""
        assert get_available_lag(date(2024, 12, 1)) == date(2024, 9, 30)

    def test_may14_before_q1_deadline(self) -> None:
        """May 14 (day before deadline) → Q1 not yet filed → Annual."""
        assert get_available_lag(date(2024, 5, 14)) == date(2023, 12, 31)


# ═══════════════════════════════════════════════════════════════════
# get_paid_in_capital_increases
# ═══════════════════════════════════════════════════════════════════


class TestGetPaidInCapitalIncreases:
    """Unit tests for DART paid-in capital increases retrieval and parsing."""

    def test_get_paid_in_capital_increases_mocked(self, monkeypatch) -> None:
        """Parses DART report rows with '유상' in the style column correctly."""
        import sys
        from super_quality.data.loader import get_paid_in_capital_increases

        class MockDartReader:
            def __init__(self, api_key: str) -> None:
                self.api_key = api_key

            def report(self, corp: str, key_word: str, bsns_year: int, reprt_code: str = "11011") -> pd.DataFrame:
                # Return sample df for CJ CGV
                return pd.DataFrame({
                    "isu_dcrs_de": ["2023.09.27", "2023.12.31", "2022-07-29"],
                    "isu_dcrs_stle": ["유상증자(주주배정)", "전환권행사", "유상증자(제3자배정)"],
                    "isu_dcrs_qy": ["74700000", "58", "6818182"],
                })

        monkeypatch.setitem(sys.modules, "OpenDartReader", MockDartReader)

        dates = get_paid_in_capital_increases("079160", [2023], api_key="mock_key")
        assert len(dates) == 2
        assert date(2023, 9, 27) in dates
        assert date(2022, 7, 29) in dates
        assert date(2023, 12, 31) not in dates  # 전환권행사 (no '유상')


