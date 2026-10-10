"""Rebuild the result tables of docs/research_summary_*.md from saved results.

Usage (after running the reproduction commands in the research summary):

    uv run python scripts/research_tables.py [--end 2025-02-28] [--output results/research_tables.md]

Every table uses the same rules:
- evaluation starts at the walk-forward base date (the close before the first out-of-sample day),
  rebased to 1.0, and ends at --end (the main period) or the last saved date (full period);
- Sharpe uses the cash ETF of the curve's market as the risk-free rate (153130.KS for Korean
  curves, BIL for US curves), on that market's own trading days;
- combinations come from results/portfolio/portfolio_summary.csv, which the portfolio command
  evaluates on Korean trading days.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from etf_momentum.backtest import summarize_performance
from etf_momentum.data import download_prices

DATA_END = "2026-10-07"  # pinned download end used by the reproduction commands
WF = Path("results/walkforward")
CASH = {"kr": "153130.KS", "us": "BIL"}

# (label, run directory, column, market)
MARKET_TABLE = [
    ("미국 섹터 모멘텀", "us_20m_5bp_robustgrid_tvoff", "walk_forward", "us"),
    ("SPY (달러)", "us_20m_5bp_robustgrid_tvoff", "benchmark", "us"),
    ("한국 섹터 모멘텀", "kr_10eok_10bp_robustgrid_tvoff", "walk_forward", "kr"),
    ("KODEX 200", "kr_10eok_10bp_robustgrid_tvoff", "benchmark", "kr"),
    ("연금저축 유니버스 모멘텀", "kr_pension_pension_10eok_10bp_robustgrid_tvoff", "walk_forward", "kr"),
    ("IRP·DC 유니버스 모멘텀 (위험자산 70% 상한)", "kr_pension_retirement_10eok_10bp_robustgrid_tvoff", "walk_forward", "kr"),
]
FIXED_TABLE = [
    ("고정 설정, 동일 비중", Path("results/backtest/pension_fixed_top4_equal/equity_curve.csv"), "strategy", "kr"),
    ("고정 설정, 변동성 역가중", Path("results/backtest/pension_fixed_top4_inverse_vol/equity_curve.csv"), "strategy", "kr"),
]
VOL_TARGET_TABLE = [
    ("끔", "kr_10eok_10bp_robustgrid_tvoff"),
    ("15%", "kr_10eok_10bp_robustgrid_tv15"),
    ("10%", "kr_10eok_10bp_robustgrid_tv10"),
]
FILTER_TABLE = [
    ("KODEX 200", "kr_pension_pension_10eok_10bp_robustgrid_tvoff_filter_kr"),
    ("S&P500", "kr_pension_pension_10eok_10bp_robustgrid_tvoff_filter_us"),
    ("한국 또는 미국", "kr_pension_pension_10eok_10bp_robustgrid_tvoff_filter_kr_or_us"),
    ("필터 없음", "kr_pension_pension_10eok_10bp_robustgrid_tvoff_filter_none"),
]
# Full Korean ETF universe (current listings only: survivorship bias), compared with the 60-ETF pension universe
FULL_UNIVERSE_TABLE = [
    ("연금저축 유니버스 60개 (기존)", "kr_pension_pension_10eok_10bp_robustgrid_tvoff"),
    ("국내 상장 ETF 전체, 그룹당 1개 (비교용)", "kr_all_pension_10eok_10bp_robustgrid_tvoff"),
    ("국내 상장 ETF 전체, 그룹 제한 없음 (현재 전략)", "kr_all_pension_10eok_10bp_nogroup_tvoff"),
]
EXECUTION_SCENARIOS = [
    ("연금 모멘텀(워크포워드), 같은 날 종가", "WF_same 100"),
    ("연금 모멘텀(워크포워드), 다음 날 종가", "WF_next 100"),
    ("고정 설정(동일 비중), 같은 날 종가", "FIX_same 100"),
    ("고정 설정(동일 비중), 다음 날 종가", "FIX_next 100"),
    ("S&P500 70 / 워크포워드 30, 같은 날 종가", "S&P500 70 / WF_same 30"),
    ("S&P500 70 / 워크포워드 30, 다음 날 종가", "S&P500 70 / WF_next 30"),
    ("S&P500 70 / 고정 설정 30, 같은 날 종가", "S&P500 70 / FIX_same 30"),
    ("S&P500 70 / 고정 설정 30, 다음 날 종가", "S&P500 70 / FIX_next 30"),
]
COMBO_SCENARIOS = [
    ("S&P500 100% (원화 환산)", "S&P500 100"),
    ("S&P500 70 / 연금 모멘텀(워크포워드) 30", "S&P500 70 / PEN_mom 30"),
    ("S&P500 50 / 연금 모멘텀(워크포워드) 50", "S&P500 50 / PEN_mom 50"),
    ("S&P500 70 / 고정 설정(동일 비중) 30", "S&P500 70 / PEN_fixed_equal 30"),
    ("S&P500 70 / 고정 설정(변동성 역가중) 30", "S&P500 70 / PEN_fixed_inverse_vol 30"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--end", default="2025-02-28", help="End of the main evaluation period")
    parser.add_argument("--output", default="results/research_tables.md", help="Markdown output file")
    args = parser.parse_args()

    risk_free = {
        market: download_prices([ticker], start="2010-01-01", end=DATA_END)[ticker].pct_change()
        for market, ticker in CASH.items()
    }
    base = pd.Timestamp(pd.read_csv(WF / MARKET_TABLE[4][1] / "walkforward_windows.csv")["train_end"].iloc[0])

    def stats(curve: pd.Series, market: str, end: str | None) -> dict[str, float]:
        window = curve.dropna().loc[base:end]
        metrics = summarize_performance((window / window.iloc[0]).to_frame("x"), risk_free=risk_free[market])
        return {k: metrics[f"x_{k}"] for k in ("cagr", "max_drawdown", "sharpe")}

    def wf_curve(run: str, column: str = "walk_forward") -> pd.Series:
        return pd.read_csv(WF / run / "walkforward_equity.csv", index_col=0, parse_dates=True)[column]

    def row(label: str, main: dict[str, float], full: dict[str, float]) -> str:
        return (
            f"| {label} | {main['cagr']:.1%} | {main['max_drawdown']:.1%} | {main['sharpe']:.2f} "
            f"| {full['cagr']:.1%} / {full['max_drawdown']:.1%} |"
        )

    header = "| 전략 | 연평균 수익률 | 최대 낙폭 | Sharpe | 최신까지 (수익률 / 낙폭) |\n|---|---|---|---|---|"
    out: list[str] = [f"기준 기간: {base.date()} ~ {args.end} (최신까지: ~ 저장된 마지막 날짜)\n"]

    out.append("### 시장별 모멘텀 단독 (워크포워드)\n\n" + header)
    for label, run, column, market in MARKET_TABLE:
        curve = wf_curve(run, column)
        out.append(row(label, stats(curve, market, args.end), stats(curve, market, None)))

    out.append("\n### 고정 설정 검증 (연금저축, 상위 4개)\n\n" + header)
    for label, path, column, market in FIXED_TABLE:
        curve = pd.read_csv(path, index_col=0, parse_dates=True)[column]
        out.append(row(label, stats(curve, market, args.end), stats(curve, market, None)))

    out.append(
        "\n### 변동성 목표 (한국 섹터 모멘텀, 워크포워드)\n\n"
        "| 목표 | 연평균 수익률 | 최대 낙폭 | Sharpe | 32개 중 중간 조합 |\n|---|---|---|---|---|"
    )
    for label, run in VOL_TARGET_TABLE:
        main, median = stats(wf_curve(run), "kr", args.end), stats(wf_curve(run, "median_fixed"), "kr", args.end)
        out.append(
            f"| {label} | {main['cagr']:.1%} | {main['max_drawdown']:.1%} | {main['sharpe']:.2f} "
            f"| {median['cagr']:.1%} / {median['max_drawdown']:.1%} |"
        )

    out.append("\n### 시장 필터 기준 비교 (연금저축, 워크포워드, 시장 필터 켬 고정)\n\n" + header)
    for label, run in FILTER_TABLE:
        curve = wf_curve(run)
        out.append(row(label, stats(curve, "kr", args.end), stats(curve, "kr", None)))

    def portfolio_table(title: str, path: str, scenarios: list[tuple[str, str]]) -> None:
        summary = pd.read_csv(path)
        main_period = summary["period"].str.endswith(args.end)
        out.append(f"\n### {title}\n\n" + header.replace("전략", "조합"))
        for label, scenario in scenarios:
            rows = summary[summary["scenario"] == scenario]
            main, full = rows[main_period.loc[rows.index]].iloc[0], rows[~main_period.loc[rows.index]].iloc[0]
            out.append(row(label, main, full))

    out.append("\n### 전체 ETF 유니버스 비교 (연금저축, 워크포워드, 생존 편향 있음)\n\n" + header)
    for label, run in FULL_UNIVERSE_TABLE:
        curve = wf_curve(run)
        out.append(row(label, stats(curve, "kr", args.end), stats(curve, "kr", None)))

    portfolio_table("원화 기준 조합 (한국 거래일 기준 평가)", "results/portfolio/portfolio_summary.csv", COMBO_SCENARIOS)
    portfolio_table(
        "체결 시점 비교 (월말 신호, 같은 날 종가 vs 다음 날 종가)",
        "results/portfolio_execution/portfolio_summary.csv",
        EXECUTION_SCENARIOS,
    )

    text = "\n".join(out) + "\n"
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(text, encoding="utf-8")
    print(text)
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
