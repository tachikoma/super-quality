"""Super Quality 2.0 보고서 생성 및 시각화.

CSV 거래 로그, PNG 차트(자본 곡선, 낙폭, 월별 수익률 히트맵) 및
자체 포함된 HTML 티어시트를 생성하는 :class:`ReportGenerator`를 제공합니다.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import matplotlib
import matplotlib.colors as mcolors
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# matplotlib.display가 필요 없는 비상호형 백엔드 사용
matplotlib.use("Agg")

# ── 전역 matplotlib 설정 ──────────────────────────────────────────
plt.rcParams["figure.dpi"] = 150
plt.rcParams["savefig.dpi"] = 150
plt.rcParams["font.size"] = 10
plt.rcParams["axes.grid"] = True
plt.rcParams["grid.alpha"] = 0.3


class ReportGenerator:
    """백테스트 결과에서 출력 파일을 생성합니다.

    Parameters
    ----------
    output_dir : str
        보고서 파일이 작성되는 디렉토리 (없으면 생성됨).
    """

    def __init__(self, output_dir: str = "outputs") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── CSV 출력 ───────────────────────────────────────────────────

    def save_trade_log(self, trade_log: pd.DataFrame) -> str:
        """거래 로그를 CSV로 저장합니다.

        Parameters
        ----------
        trade_log : pd.DataFrame
            ``entry_date``, ``exit_date``, ``ticker``, ``buy_price``,
            ``sell_price``, ``shares``, ``return_pct``, ``hold_days``,
            ``exit_reason`` 컬럼을 가진 거래 로그.

        Returns
        -------
        str
            저장된 CSV 파일 경로.
        """
        path = self.output_dir / "trade_log.csv"
        trade_log.to_csv(path, index=False)
        return str(path)

    def save_portfolio_snapshots(self, snapshots: pd.DataFrame) -> str:
        """일별 포트폴리오 스냅샷을 CSV로 저장합니다.

        Parameters
        ----------
        snapshots : pd.DataFrame
            ``date``, ``cash``, ``holdings_value``, ``nav``,
            ``num_positions`` (그리고 선택적으로 ``daily_return``) 컬럼.

        Returns
        -------
        str
            저장된 CSV 파일 경로.
        """
        path = self.output_dir / "portfolio_snapshots.csv"
        snapshots.to_csv(path, index=False)
        return str(path)

    # ── 차트 출력 ─────────────────────────────────────────────────

    def plot_equity_curve(self, snapshots: pd.DataFrame) -> str:
        """자본 곡선을 그립니다 (NAV over time).

        Parameters
        ----------
        snapshots : pd.DataFrame
            ``date``와 ``nav`` 컬럼을 포함해야 함.

        Returns
        -------
        str
            저장된 PNG 파일 경로.
        """
        if snapshots.empty:
            return ""

        path = self.output_dir / "equity_curve.png"
        dates = self._to_datetime(snapshots["date"])
        nav = snapshots["nav"].values

        fig, ax = plt.subplots(figsize=(12, 6))
        ax.plot(dates, nav, label="Portfolio", linewidth=2, color="#1f77b4")
        ax.set_title("Equity Curve")
        ax.set_xlabel("Date")
        ax.set_ylabel("Portfolio Value (KRW)")
        ax.legend()
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        fig.autofmt_xdate()
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return str(path)

    def plot_drawdown(self, snapshots: pd.DataFrame) -> str:
        """낙폭 차트를 그립니다.

        Parameters
        ----------
        snapshots : pd.DataFrame
            ``date``와 ``nav`` 컬럼을 포함해야 함.

        Returns
        -------
        str
            저장된 PNG 파일 경로.
        """
        if snapshots.empty:
            return ""

        path = self.output_dir / "drawdown.png"
        dates = self._to_datetime(snapshots["date"])
        nav = snapshots["nav"].values
        running_max = np.maximum.accumulate(nav)
        drawdown = nav / running_max - 1.0

        fig, ax = plt.subplots(figsize=(12, 4))
        ax.fill_between(dates, drawdown * 100.0, 0, color="red", alpha=0.3)
        ax.plot(dates, drawdown * 100.0, color="red", linewidth=1)
        ax.set_title("Drawdown")
        ax.set_ylabel("Drawdown (%)")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        fig.autofmt_xdate()
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return str(path)

    def _plot_monthly_returns_heatmap(
        self,
        monthly_returns: pd.DataFrame,
    ) -> str:
        """월별 수익률 히트맵을 그리고 PNG 파일 경로를 반환합니다.

        Parameters
        ----------
        monthly_returns : pd.DataFrame
            ``"2024-01"`` 형식의 index와 ``return`` 컬럼을 가진 DataFrame.

        Returns
        -------
        str
            저장된 PNG 파일 경로.
        """
        path = self.output_dir / "monthly_returns.png"
        if monthly_returns.empty:
            return ""

        # 피벗 구성: rows=year, columns=month
        parsed = (
            monthly_returns["return"]
            .to_frame("ret")
            .assign(
                year=lambda x: x.index.map(lambda s: str(s)[:4]),
                month=lambda x: x.index.map(lambda s: int(str(s)[5:7])),
            )
        )
        pivot = parsed.pivot_table(
            index="year",
            columns="month",
            values="ret",
            aggfunc="first",
        )
        pivot = pivot * 100.0  # %로 변환

        months = ["1월", "2월", "3월", "4월", "5월", "6월",
                   "7월", "8월", "9월", "10월", "11월", "12월"]

        fig, ax = plt.subplots(figsize=(10, max(3, len(pivot) * 0.4 + 1)))
        cmap = plt.colormaps["RdYlGn"]
        norm = mcolors.TwoSlopeNorm(vmin=-5, vcenter=0, vmax=5)

        # 12개월 컬럼이 모두 존재하는지 확인
        for m in range(1, 13):
            if m not in pivot.columns:
                pivot[m] = np.nan
        pivot = pivot[sorted(pivot.columns)]

        data = pivot.values
        im = ax.imshow(data, cmap=cmap, norm=norm, aspect="auto")

        # 셀에 값 표시
        for i in range(data.shape[0]):
            for j in range(data.shape[1]):
                val = data[i, j]
                if np.isnan(val):
                    label = ""
                elif abs(val) < 0.01:
                    label = "0.0"
                else:
                    label = f"{val:.1f}"
                color = "black" if abs(val) < 2.0 else "white"
                ax.text(j, i, label, ha="center", va="center", fontsize=8, color=color)

        ax.set_xticks(range(12))
        ax.set_xticklabels(months, fontsize=9)
        ax.set_yticks(range(len(pivot)))
        ax.set_yticklabels(pivot.index, fontsize=9)
        ax.set_title("Monthly Returns (%)", fontsize=12, fontweight="bold")
        fig.colorbar(im, ax=ax, fraction=0.05, pad=0.04)
        fig.tight_layout()
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return str(path)

    # ── HTML 티어시트 ────────────────────────────────────────────────

    def generate_html_tearsheet(
        self,
        metrics: dict,
        snapshots: pd.DataFrame,
        trade_log: pd.DataFrame,
    ) -> str:
        """자체 포함된 HTML 티어시트를 생성합니다.

        티어시트에는 주요 통계치, 내장된 자본 곡선 및 낙폭 차트,
        월별 수익률 히트맵, 상위 20개 거래 로그 행이 포함됩니다.
        모든 이미지는 base64로 인라인 처리되어 외부 의존성이 없습니다.

        Parameters
        ----------
        metrics : dict
            :meth:`PerformanceMetrics.compute_all`의 출력.
        snapshots : pd.DataFrame
            일별 포트폴리오 스냅샷 (``date``, ``nav``, …).
        trade_log : pd.DataFrame
            거래 로그.

        Returns
        -------
        str
            저장된 HTML 파일 경로.
        """
        path = self.output_dir / "tearsheet.html"

        # ── 차트 생성 및 삽입 ─────────────────────────────────
        equity_png = self.plot_equity_curve(snapshots)
        dd_png = self.plot_drawdown(snapshots)
        monthly_ret = metrics.get("monthly_returns", pd.DataFrame(columns=["return"]))
        heatmap_png = self._plot_monthly_returns_heatmap(monthly_ret)

        equity_b64 = self._png_to_b64(equity_png) if equity_png else ""
        dd_b64 = self._png_to_b64(dd_png) if dd_png else ""
        heatmap_b64 = self._png_to_b64(heatmap_png) if heatmap_png else ""

        # ── 주요 통계 ─────────────────────────────────────────────────
        def _pct(v: Any) -> str:
            try:
                return f"{float(v) * 100:.2f}%"
            except (TypeError, ValueError):
                return "N/A"

        def _num(v: Any, decimals: int = 2) -> str:
            try:
                return f"{float(v):.{decimals}f}"
            except (TypeError, ValueError):
                return "N/A"

        stats_html = f"""
        <table class="stats-table">
          <tr><td>CAGR</td><td class="val">{_pct(metrics.get('cagr', 0))}</td>
              <td>Total Return</td><td class="val">{_pct(metrics.get('total_return', 0))}</td></tr>
          <tr><td>Sharpe Ratio</td><td class="val">{_num(metrics.get('sharpe_ratio', 0))}</td>
              <td>Sortino Ratio</td><td class="val">{_num(metrics.get('sortino_ratio', 0))}</td></tr>
          <tr><td>Volatility</td><td class="val">{_pct(metrics.get('volatility', 0))}</td>
              <td>Max Drawdown</td><td class="val">{_pct(metrics.get('max_drawdown', 0))}</td></tr>
          <tr><td>Max DD Duration</td><td class="val">{int(metrics.get('max_drawdown_duration', 0))} days</td>
              <td>Win Rate</td><td class="val">{_pct(metrics.get('win_rate', 0))}</td></tr>
          <tr><td>Profit Factor</td><td class="val">{_num(metrics.get('profit_factor', 0))}</td>
              <td>Total Trades</td><td class="val">{int(metrics.get('total_trades', 0))}</td></tr>
          <tr><td>Avg Hold Days</td><td class="val">{_num(metrics.get('avg_hold_days', 0), 1)}</td>
              <td></td><td class="val"></td></tr>
        </table>"""

        # ── 벤치마크 통계 (있는 경우) ──────────────────────────────
        bench = metrics.get("benchmark_comparison", {})
        bench_html = ""
        if bench:
            bench_html = f"""
        <h2>Benchmark Comparison</h2>
        <table class="stats-table">
          <tr><td>Alpha</td><td class="val">{_pct(bench.get('alpha', 0))}</td>
              <td>Beta</td><td class="val">{_num(bench.get('beta', 0))}</td></tr>
          <tr><td>Correlation</td><td class="val">{_num(bench.get('correlation', 0))}</td>
              <td>Tracking Error</td><td class="val">{_pct(bench.get('tracking_error', 0))}</td></tr>
        </table>"""

        # ── 거래 로그 테이블 (상위 20개) ──────────────────────────────────
        trade_html = ""
        if trade_log is not None and not trade_log.empty:
            display_cols = [
                c for c in
                ["entry_date", "exit_date", "ticker", "buy_price",
                 "sell_price", "shares", "return_pct", "hold_days", "exit_reason"]
                if c in trade_log.columns
            ]
            top = trade_log.head(20).copy()
            # return_pct를 %로 포맷팅
            if "return_pct" in top.columns:
                top["return_pct"] = top["return_pct"].apply(
                    lambda v: f"{v*100:.2f}%" if pd.notna(v) else ""
                )
            # 긴 값은 표시를 위해 자름
            for col in ["buy_price", "sell_price"]:
                if col in top.columns:
                    top[col] = top[col].apply(
                        lambda v: f"{v:,.0f}" if pd.notna(v) else ""
                    )
            rows = ""
            for _, row in top.iterrows():
                cells = "".join(
                    f"<td>{row.get(c, '')}</td>" for c in display_cols
                )
                rows += f"<tr>{cells}</tr>"
            trade_html = f"""
        <h2>Trade Log (Top {min(20, len(trade_log))})</h2>
        <table class="trade-table">
          <thead><tr>{"".join(f'<th>{c}</th>' for c in display_cols)}</tr></thead>
          <tbody>{rows}</tbody>
        </table>"""

        # ── HTML 조합 ─────────────────────────────────────────────
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Super Quality 2.0 — Tearsheet</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; max-width: 1100px; margin: 2em auto; padding: 0 1em; color: #333; background: #fafafa; }}
  h1 {{ color: #1a1a2e; border-bottom: 2px solid #e0e0e0; padding-bottom: 0.3em; }}
  h2 {{ color: #16213e; margin-top: 1.5em; }}
  .stats-table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
  .stats-table td {{ padding: 0.5em 1em; border-bottom: 1px solid #eee; }}
  .stats-table .val {{ font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; }}
  .chart {{ margin: 1.5em 0; text-align: center; }}
  .chart img {{ max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px; }}
  .trade-table {{ border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.85em; }}
  .trade-table th {{ background: #16213e; color: white; padding: 0.5em; text-align: left; }}
  .trade-table td {{ padding: 0.4em 0.5em; border-bottom: 1px solid #ddd; }}
  .trade-table tr:nth-child(even) {{ background: #f5f5f5; }}
  .footer {{ margin-top: 2em; font-size: 0.8em; color: #888; text-align: center; }}
</style>
</head>
<body>
<h1>Super Quality 2.0 — Performance Tearsheet</h1>
{stats_html}
{bench_html}
<div class="chart"><h2>Equity Curve</h2><img src="data:image/png;base64,{equity_b64}" alt="Equity Curve"></div>
<div class="chart"><h2>Drawdown</h2><img src="data:image/png;base64,{dd_b64}" alt="Drawdown"></div>
<div class="chart"><h2>Monthly Returns (%)</h2><img src="data:image/png;base64,{heatmap_b64}" alt="Monthly Returns Heatmap"></div>
{trade_html}
<div class="footer">Generated by Super Quality 2.0</div>
</body>
</html>"""

        path.write_text(html, encoding="utf-8")
        return str(path)

    # ── 일괄 생성 ─────────────────────────────────────────────────────────

    def generate_all(
        self,
        metrics: dict,
        snapshots: pd.DataFrame,
        trade_log: pd.DataFrame,
    ) -> dict[str, str]:
        """모든 출력 파일을 생성합니다.

        Parameters
        ----------
        metrics : dict
            :meth:`PerformanceMetrics.compute_all`의 출력.
        snapshots : pd.DataFrame
            일별 포트폴리오 스냅샷.
        trade_log : pd.DataFrame
            거래 로그.

        Returns
        -------
        dict
            키는 사람이 읽을 수 있는 이름 (``"Trade Log"``,
            ``"Portfolio Snapshots"``, ``"Equity Curve"``,
            ``"Drawdown"``, ``"Tearsheet"``)이고 값은 파일 경로입니다.
        """
        result: dict[str, str] = {}

        if trade_log is not None and not trade_log.empty:
            result["Trade Log"] = self.save_trade_log(trade_log)

        if snapshots is not None and not snapshots.empty:
            result["Portfolio Snapshots"] = self.save_portfolio_snapshots(snapshots)

        eq_path = self.plot_equity_curve(snapshots)
        if eq_path:
            result["Equity Curve"] = eq_path

        dd_path = self.plot_drawdown(snapshots)
        if dd_path:
            result["Drawdown"] = dd_path

        ts_path = self.generate_html_tearsheet(metrics, snapshots, trade_log)
        if ts_path:
            result["Tearsheet"] = ts_path

        return result

    # ── 헬퍼 ───────────────────────────────────────────────────────

    @staticmethod
    def _to_datetime(series: pd.Series) -> np.ndarray:
        """날짜/문자열 시리즈를 numpy datetime64 배열로 변환합니다."""
        return pd.to_datetime(series).values

    @staticmethod
    def _png_to_b64(png_path: str) -> str:
        """PNG 파일을 읽고 base64로 인코딩된 문자열을 반환합니다."""
        path = Path(png_path)
        if not path.exists():
            return ""
        data = path.read_bytes()
        return base64.b64encode(data).decode("ascii")
