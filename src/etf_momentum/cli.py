from __future__ import annotations

import argparse
import hashlib
import math
import sys
import itertools
import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from etf_momentum.backtest import (
    ACCOUNTING_CHOICES,
    BacktestConfig,
    previous_month_end,
    run_backtest,
    select_holdings,
    summarize_performance,
)
from etf_momentum.data import (
    ACCOUNT_CHOICES,
    account_candidates,
    download_prices,
    download_traded_value,
    load_universe,
    splice_proxies,
    staleness_message,
    unique_tickers,
)
from etf_momentum.portfolio import PortfolioConfig, annualized_cost, run_portfolio, sleeve_returns_on_calendar
from etf_momentum.reporting import ensure_output_dir, save_equity_chart, save_json, save_run_metadata
from etf_momentum.strategy import Strategy, StrategyError, load_strategy
from etf_momentum.signals import LIQUIDITY_MAX_STALE_DAYS, RANK_BY_CHOICES, MomentumConfig, rank_momentum
from etf_momentum.walkforward import METRICS, oos_equity, walk_forward
from etf_momentum.weekly import append_snapshot, build_weekly_report, load_previous_snapshot


STRATEGY_CONFLICTS = (
    "--universe", "--benchmark", "--cash", "--account", "--use-proxies", "--lookbacks", "--weights",
    "--skip-days", "--sma-window", "--volatility-window", "--rank-by", "--min-avg-traded-value",
    "--top-n", "--max-per-group", "--risk-on-benchmark", "--risk-on-ticker", "--min-weighted-momentum",
    "--min-trend-strength", "--min-relative-momentum", "--require-relative-strength", "--max-risky-weight",
    "--proxy-fx", "--history-file",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="ETF sector momentum ranking and backtesting")
    subparsers = parser.add_subparsers(dest="command", required=True)

    rank_parser = subparsers.add_parser("rank", help="Rank ETFs by momentum", allow_abbrev=False)
    _add_common_args(rank_parser)
    rank_parser.add_argument("--top", type=_positive_int, default=20, help="Number of rows to print")
    rank_parser.add_argument("--top-n", type=_positive_int, default=3, help="ETFs to select (same rule as backtest)")
    rank_parser.add_argument(
        "--max-per-group", type=_non_negative_int, default=1, help="Max selected ETFs per universe 'group'; 0 disables the limit"
    )
    rank_parser.add_argument("--risk-on-benchmark", action="store_true", help="Select nothing when the market filter is off")
    rank_parser.add_argument(
        "--risk-on-ticker", default=None, help="Comma-separated market filter tickers (default: --benchmark)"
    )
    rank_parser.add_argument(
        "--strategy", default=None, help="Strategy JSON (config/strategies/*.json) fixing every selection setting"
    )
    rank_parser.set_defaults(func=run_rank_command)

    weekly_parser = subparsers.add_parser("weekly", help="Weekly leadership report against prior snapshot", allow_abbrev=False)
    _add_common_args(weekly_parser)
    weekly_parser.add_argument("--top", type=_positive_int, default=15, help="Number of report rows to print")
    weekly_parser.add_argument("--watch-rank", type=_positive_int, default=10, help="Ranks to monitor for leadership changes")
    weekly_parser.add_argument("--leader-rank", type=_positive_int, default=5, help="Ranks considered persistent leadership")
    weekly_parser.add_argument("--rise-threshold", type=_positive_int, default=3, help="Rank improvement treated as rising")
    weekly_parser.add_argument("--drop-threshold", type=_positive_int, default=3, help="Rank deterioration treated as fading")
    weekly_parser.add_argument("--history-file", default=None, help="Ranking history CSV; defaults to output-dir/rank_history.csv")
    weekly_parser.add_argument("--no-save-history", action="store_true", help="Do not append this run to history")
    weekly_parser.add_argument(
        "--strategy", default=None, help="Strategy JSON (config/strategies/*.json) fixing every selection setting"
    )
    weekly_parser.set_defaults(func=run_weekly_command)

    backtest_parser = subparsers.add_parser("backtest", help="Backtest monthly ETF momentum rotation", allow_abbrev=False)
    _add_common_args(backtest_parser)
    backtest_parser.add_argument("--top-n", type=_positive_int, default=3, help="Number of ETFs to hold")
    backtest_parser.add_argument("--rebalance", default="M", help="Pandas resample frequency, e.g. M or W-FRI")
    backtest_parser.add_argument("--risk-on-benchmark", action="store_true", help="Hold cash when benchmark is below SMA")
    backtest_parser.add_argument("--transaction-cost-bps", type=_non_negative_float, default=0.0, help="One-way trading cost in basis points")
    backtest_parser.add_argument(
        "--weighting",
        default="equal",
        choices=["equal", "inverse_vol", "score", "risk_adjusted_score"],
        help="Portfolio weighting method for selected ETFs",
    )
    backtest_parser.add_argument("--max-position-weight", type=_unit_fraction, default=1.0, help="Maximum single ETF weight")
    backtest_parser.add_argument(
        "--max-per-group",
        type=_non_negative_int,
        default=1,
        help="Max ETFs held from one universe 'group'; 0 disables the limit",
    )
    backtest_parser.add_argument(
        "--accounting",
        default="buy_and_hold",
        choices=ACCOUNTING_CHOICES,
        help="buy_and_hold: weights drift between rebalances; daily_rebalanced: weights reset daily",
    )
    backtest_parser.add_argument(
        "--target-vol",
        type=_positive_float,
        default=None,
        help="Annualized volatility target (e.g. 0.15); scales exposure down with the rest in cash. Off by default",
    )
    backtest_parser.add_argument(
        "--risk-on-ticker",
        default=None,
        help="Comma-separated market filter tickers for --risk-on-benchmark (default: --benchmark); "
        "risk-on if any is above its SMA",
    )
    backtest_parser.add_argument(
        "--strategy", default=None, help="Strategy JSON (config/strategies/*.json) fixing every selection setting"
    )
    backtest_parser.set_defaults(func=run_backtest_command)

    compare_parser = subparsers.add_parser("compare", help="Compare rebalance and parameter combinations")
    _add_common_args(compare_parser)
    compare_parser.add_argument("--top-n-grid", default="1,2,3,4", help="Comma-separated top-n values")
    compare_parser.add_argument("--rebalance-grid", default="W-FRI,ME", help="Comma-separated rebalance frequencies")
    compare_parser.add_argument(
        "--weighting-grid",
        default="equal,inverse_vol",
        help="Comma-separated weighting methods: equal,inverse_vol,score,risk_adjusted_score",
    )
    compare_parser.add_argument("--skip-days-grid", default=None, help="Optional comma-separated skip-day values")
    compare_parser.add_argument("--sma-window-grid", default=None, help="Optional comma-separated SMA windows")
    compare_parser.add_argument(
        "--relative-strength-grid",
        default="off,on",
        help="Comma-separated relative-strength filters: off,on or numeric thresholds",
    )
    compare_parser.add_argument("--risk-on-benchmark", action="store_true", help="Hold cash when benchmark is below SMA")
    compare_parser.add_argument("--transaction-cost-bps", type=_non_negative_float, default=0.0, help="One-way trading cost in basis points")
    compare_parser.add_argument("--max-position-weight", type=_unit_fraction, default=1.0, help="Maximum single ETF weight")
    compare_parser.add_argument(
        "--max-per-group",
        type=_non_negative_int,
        default=1,
        help="Max ETFs held from one universe 'group'; 0 disables the limit",
    )
    compare_parser.add_argument("--rank-by-grid", default=None, help="Optional comma-separated rank_by values")
    compare_parser.add_argument(
        "--accounting",
        default="buy_and_hold",
        choices=ACCOUNTING_CHOICES,
        help="buy_and_hold: weights drift between rebalances; daily_rebalanced: weights reset daily",
    )
    compare_parser.add_argument(
        "--target-vol",
        type=_positive_float,
        default=None,
        help="Annualized volatility target (e.g. 0.15); scales exposure down with the rest in cash. Off by default",
    )
    compare_parser.add_argument(
        "--risk-on-ticker",
        default=None,
        help="Comma-separated market filter tickers for --risk-on-benchmark (default: --benchmark); "
        "risk-on if any is above its SMA",
    )
    compare_parser.set_defaults(func=run_compare_command)

    wf_parser = subparsers.add_parser("walkforward", help="Walk-forward out-of-sample test of parameter selection")
    _add_common_args(wf_parser)
    wf_parser.add_argument("--top-n-grid", default="2,3,4", help="Comma-separated top-n values")
    wf_parser.add_argument("--rebalance-grid", default="ME", help="Comma-separated rebalance frequencies")
    wf_parser.add_argument("--weighting-grid", default="equal,inverse_vol", help="Comma-separated weighting methods")
    wf_parser.add_argument(
        "--rank-by-grid", default="weighted_momentum,risk_adjusted_score", help="Comma-separated rank_by values"
    )
    wf_parser.add_argument("--max-per-group-grid", default="0,1", help="Comma-separated max-per-group values (0 = no limit)")
    wf_parser.add_argument("--risk-on-grid", default="off,on", help="Benchmark SMA risk filter: off,on")
    wf_parser.add_argument("--relative-strength-grid", default="off,on", help="off,on or numeric thresholds")
    wf_parser.add_argument("--sma-window-grid", default=None, help="Optional comma-separated SMA windows")
    wf_parser.add_argument("--transaction-cost-bps", type=_non_negative_float, default=5.0, help="One-way trading cost in basis points")
    wf_parser.add_argument("--max-position-weight", type=_unit_fraction, default=1.0, help="Maximum single ETF weight")
    wf_parser.add_argument("--train-years", type=_positive_int, default=5, help="Training window length in years")
    wf_parser.add_argument("--test-months", type=_positive_int, default=12, help="Test window length (and step) in months")
    wf_parser.add_argument("--metric", default="calmar", choices=METRICS, help="Training score used to pick parameters")
    wf_parser.add_argument("--anchored", action="store_true", help="Expanding training window from the first date")
    wf_parser.add_argument(
        "--accounting",
        default="buy_and_hold",
        choices=ACCOUNTING_CHOICES,
        help="buy_and_hold: weights drift between rebalances; daily_rebalanced: weights reset daily",
    )
    wf_parser.add_argument(
        "--target-vol",
        type=_positive_float,
        default=None,
        help="Annualized volatility target (e.g. 0.15); scales exposure down with the rest in cash. Off by default",
    )
    wf_parser.add_argument(
        "--risk-on-ticker",
        default=None,
        help="Comma-separated market filter tickers for --risk-on-benchmark (default: --benchmark); "
        "risk-on if any is above its SMA",
    )
    wf_parser.set_defaults(func=run_walkforward_command)

    pf_parser = subparsers.add_parser("portfolio", help="Combine sleeves (ETFs, strategy curves) into one portfolio")
    pf_parser.add_argument("--spec", required=True, help="Portfolio spec JSON (see config/portfolio_spy_kr.json)")
    pf_parser.add_argument("--cache-dir", default="data", help="Price cache directory")
    pf_parser.add_argument("--output-dir", default="results/portfolio", help="Output directory")
    pf_parser.add_argument("--refresh", action="store_true", help="Refresh cached market data")
    pf_parser.set_defaults(func=run_portfolio_command)

    args = parser.parse_args()
    args.strategy_info = None
    if getattr(args, "strategy", None):
        conflicts = _conflicting_options(sys.argv[2:])
        if conflicts:
            parser.error(f"--strategy fixes the selection rule; remove {', '.join(conflicts)}")
        try:
            _apply_strategy(args, load_strategy(args.strategy))
        except StrategyError as exc:
            raise SystemExit(f"Strategy file error: {exc}") from exc
    args.func(args)


def run_rank_command(args: argparse.Namespace) -> None:
    output_dir = ensure_output_dir(args.output_dir)
    members = load_universe(args.universe)
    tickers = unique_tickers(members, extras=[args.benchmark, *(_risk_on_tickers(args) or ())])
    start = _start_from_lookback_years(args.lookback_years)
    prices = _download(tickers, start, args)
    traded_value = _traded_value(tickers, start, args, prices)
    prices, traded_value = _apply_proxies(members, prices, traded_value, start, args)
    _require_tickers(prices, _required(args, include_cash=False))
    save_run_metadata(output_dir, args, prices, traded_value=traded_value)
    config = _momentum_config_from_args(args)
    ranking = rank_momentum(prices, benchmark=args.benchmark, config=config, candidate_tickers=_candidates(members, args), traded_value=traded_value)
    _exit_if_empty(ranking)
    ranking = _attach_names(ranking, members)
    ranking.to_csv(output_dir / "latest_ranking.csv", index=False)

    columns = [
        "rank",
        "ticker",
        "name",
        "category",
        "eligible",
        "group",
        "weighted_momentum",
        "return_1w",
        "return_1m",
        "return_3m",
        "return_6m",
        "return_12m",
        "return_ytd",
        "trend_strength_vs_sma",
        "relative_to_benchmark",
    ]
    if args.min_avg_traded_value is not None:
        columns.insert(columns.index("eligible") + 1, "liquid")
    print(f"[현재 순위] 기준일 {pd.Timestamp(ranking['as_of'].iloc[0]).date()} (최신 종가)")
    print(ranking[columns].head(args.top).to_string(index=False, float_format=lambda value: f"{value: .2%}"))
    print(f"\nSaved: {output_dir / 'latest_ranking.csv'}")

    selection = _month_end_selection(members, prices, traded_value, config, args)
    selection.to_csv(output_dir / "latest_selection.csv", index=False)
    print(f"Saved: {output_dir / 'latest_selection.csv'}")


def _selection_today(args: argparse.Namespace) -> pd.Timestamp | None:
    """With --end (exclusive download end), the previous month-end is judged as of that date."""
    return pd.Timestamp(args.end) if args.end else None


def _month_end_selection(
    members: list, prices: pd.DataFrame, traded_value: pd.DataFrame | None, config: MomentumConfig, args: argparse.Namespace
) -> pd.DataFrame:
    """Print and return the ETFs the strategy would hold after the previous month-end close."""
    try:
        as_of = previous_month_end(prices.index, today=_selection_today(args))
    except ValueError as exc:
        raise SystemExit(f"직전 월말 선택을 만들 수 없습니다: {exc}. --refresh로 데이터를 다시 받으세요") from exc
    ranking = rank_momentum(
        prices, as_of, benchmark=args.benchmark, config=config, candidate_tickers=_candidates(members, args), traded_value=traded_value
    )
    rule = BacktestConfig(
        top_n=args.top_n,
        cash_ticker=args.cash,
        benchmark=args.benchmark,
        risk_on_benchmark=args.risk_on_benchmark,
        risk_on_tickers=_risk_on_tickers(args),
        momentum=config,
        **_group_config(members, args),
    )
    picked = select_holdings(ranking, prices, as_of, rule)
    names = {m.ticker: m.name for m in members}
    groups = {m.ticker: m.group for m in members}
    filters = ", ".join(_risk_on_tickers(args) or (args.benchmark,))

    print(f"\n[선택 종목] 기준일 {as_of.date()} (직전 월말 종가), 상위 {args.top_n}개, 그룹당 최대 {args.max_per_group or '제한 없음'}")
    if args.risk_on_benchmark:
        print(f"시장 필터: {filters} 200일선 {'위 → 투자' if picked.risk_on else '아래 → 전액 현금'}")
    rows: list[dict[str, object]] = []
    for number, ticker in enumerate(picked.tickers, 1):
        print(f" {number}. {ticker:<10} {names.get(ticker, ticker)}  {groups.get(ticker) or '-'}")
        rows.append({"status": "selected", "ticker": ticker, "group": groups.get(ticker, "")})
    if picked.empty_slots:
        cash = args.cash or "미투자 현금"
        print(f" 빈 자리 {picked.empty_slots}개 → 현금 ({cash})")
        rows.append({"status": "cash", "ticker": args.cash or "", "slots": picked.empty_slots})
    if picked.group_skipped:
        skipped = ", ".join(f"{t} ({groups.get(t)})" for t in picked.group_skipped)
        print(f" 그룹 제한으로 제외: {skipped}")
        rows.extend({"status": "group_limit", "ticker": t, "group": groups.get(t, "")} for t in picked.group_skipped)
    frame = pd.DataFrame(rows, columns=["status", "ticker", "group", "slots"])
    frame.insert(0, "as_of", str(as_of.date()))
    frame["name"] = frame["ticker"].map(lambda t: names.get(t, t))
    frame["risk_on"] = picked.risk_on
    return frame


def run_weekly_command(args: argparse.Namespace) -> None:
    output_dir = ensure_output_dir(args.output_dir)
    members = load_universe(args.universe)
    tickers = unique_tickers(members, extras=[args.benchmark])
    start = _start_from_lookback_years(args.lookback_years)
    prices = _download(tickers, start, args)
    traded_value = _traded_value(tickers, start, args, prices)
    prices, traded_value = _apply_proxies(members, prices, traded_value, start, args)
    _require_tickers(prices, _required(args, include_cash=False))
    save_run_metadata(output_dir, args, prices, traded_value=traded_value)
    config = _momentum_config_from_args(args)
    ranking = rank_momentum(prices, benchmark=args.benchmark, config=config, candidate_tickers=_candidates(members, args), traded_value=traded_value)
    _exit_if_empty(ranking)
    ranking = _attach_names(ranking, members)
    ranking.to_csv(output_dir / "latest_ranking.csv", index=False)

    current_as_of = pd.Timestamp(ranking["as_of"].iloc[0])
    history_path = Path(args.history_file) if args.history_file else output_dir / "rank_history.csv"
    previous = load_previous_snapshot(history_path, current_as_of)
    report = build_weekly_report(
        ranking,
        previous,
        watch_rank=args.watch_rank,
        leader_rank=args.leader_rank,
        rise_threshold=args.rise_threshold,
        drop_threshold=args.drop_threshold,
    )
    report.to_csv(output_dir / "weekly_report.csv", index=False)
    if not args.no_save_history:
        append_snapshot(history_path, ranking)

    previous_label = "none"
    if not previous.empty:
        previous_label = str(pd.Timestamp(previous["as_of"].iloc[0]).date())
    print(f"Weekly leadership report: {current_as_of.date()} vs {previous_label}")
    columns = [
        "ticker",
        "name",
        "rank",
        "previous_rank",
        "rank_change",
        "still_top",
        "new_or_rising",
        "leader_fading",
        "one_month_rolling_over",
        "return_1w",
        "return_1m",
        "return_3m",
        "status",
    ]
    if args.min_avg_traded_value is not None:
        columns.insert(columns.index("rank"), "liquid")
    existing_columns = [column for column in columns if column in report.columns]
    if report.empty:
        print("No report rows. Check universe, ranking, and data history.")
    else:
        print(report[existing_columns].head(args.top).to_string(index=False, float_format=lambda value: f"{value: .2%}"))
    print(f"\nSaved: {output_dir / 'weekly_report.csv'}")
    print(f"History: {history_path}")


def run_backtest_command(args: argparse.Namespace) -> None:
    output_dir = ensure_output_dir(args.output_dir)
    members = load_universe(args.universe)
    tickers = unique_tickers(members, extras=[args.benchmark, args.cash, *(_risk_on_tickers(args) or ())])
    prices = _download(tickers, args.start, args)
    traded_value = _traded_value(tickers, args.start, args, prices)
    prices, traded_value = _apply_proxies(members, prices, traded_value, args.start, args)
    _require_tickers(prices, _required(args, include_cash=True))
    save_run_metadata(output_dir, args, prices, traded_value=traded_value)
    momentum_config = _momentum_config_from_args(args)
    config = BacktestConfig(
        accounting=args.accounting,
        target_volatility=args.target_vol,
        max_risky_weight=_max_risky_weight(args),
        safe_tickers=_safe_tickers(members, args),
        risk_on_tickers=_risk_on_tickers(args),
        top_n=args.top_n,
        rebalance=args.rebalance,
        cash_ticker=args.cash,
        benchmark=args.benchmark,
        risk_on_benchmark=args.risk_on_benchmark,
        transaction_cost_bps=args.transaction_cost_bps,
        weighting=args.weighting,
        max_position_weight=args.max_position_weight,
        momentum=momentum_config,
        candidate_tickers=_candidates(members, args),
        **_group_config(members, args),
    )
    equity, positions, metrics = run_backtest(prices, config, traded_value=traded_value)
    ranking = rank_momentum(
        prices,
        benchmark=args.benchmark,
        config=momentum_config,
        candidate_tickers=config.candidate_tickers,
        traded_value=traded_value,
    )
    ranking = _attach_names(ranking, members)

    equity.to_csv(output_dir / "equity_curve.csv")
    positions.to_csv(output_dir / "positions.csv", index=False)
    ranking.to_csv(output_dir / "latest_ranking.csv", index=False)
    save_json(metrics, output_dir / "summary.json")
    save_equity_chart(equity, output_dir / "equity_curve.png")

    print("Performance summary")
    for key, value in metrics.items():
        print(f"  {key}: {_format_metric(key, value)}")
    print(f"\nSaved results under: {output_dir}")


def run_compare_command(args: argparse.Namespace) -> None:
    output_dir = ensure_output_dir(args.output_dir)
    members = load_universe(args.universe)
    tickers = unique_tickers(members, extras=[args.benchmark, args.cash, *(_risk_on_tickers(args) or ())])
    prices = _download(tickers, args.start, args)
    traded_value = _traded_value(tickers, args.start, args, prices)
    prices, traded_value = _apply_proxies(members, prices, traded_value, args.start, args)
    _require_tickers(prices, _required(args, include_cash=True))
    save_run_metadata(output_dir, args, prices, traded_value=traded_value)

    base_momentum = _momentum_config_from_args(args)
    top_n_values = _parse_int_list(args.top_n_grid)
    rebalance_values = _parse_str_list(args.rebalance_grid)
    weighting_values = _parse_str_list(args.weighting_grid)
    skip_days_values = _parse_int_list(args.skip_days_grid) if args.skip_days_grid else [base_momentum.skip_days]
    sma_window_values = _parse_int_list(args.sma_window_grid) if args.sma_window_grid else [base_momentum.sma_window]
    relative_strength_values = _parse_relative_strength_grid(args.relative_strength_grid)
    rank_by_values = _parse_str_list(args.rank_by_grid) if args.rank_by_grid else [base_momentum.rank_by]
    group_config = _group_config(members, args)
    candidate_tickers = _candidates(members, args)
    if args.cash and args.cash not in prices.columns:
        raise SystemExit(f"Cash ticker {args.cash} has no price data; use --cash none to hold uninvested cash")

    rows: list[dict[str, object]] = []
    run_number = 0
    for top_n in top_n_values:
        for rebalance in rebalance_values:
            for weighting in weighting_values:
                for skip_days in skip_days_values:
                    for sma_window in sma_window_values:
                        for min_relative_momentum, rank_by in itertools.product(relative_strength_values, rank_by_values):
                            run_number += 1
                            momentum_config = replace(
                                base_momentum,
                                skip_days=skip_days,
                                sma_window=sma_window,
                                min_relative_momentum=min_relative_momentum,
                                rank_by=rank_by,
                            )
                            config = BacktestConfig(
                                accounting=args.accounting,
                                target_volatility=args.target_vol,
                                max_risky_weight=_max_risky_weight(args),
                                safe_tickers=_safe_tickers(members, args),
                                risk_on_tickers=_risk_on_tickers(args),
                                top_n=top_n,
                                rebalance=rebalance,
                                cash_ticker=args.cash,
                                benchmark=args.benchmark,
                                risk_on_benchmark=args.risk_on_benchmark,
                                transaction_cost_bps=args.transaction_cost_bps,
                                weighting=weighting,
                                max_position_weight=args.max_position_weight,
                                momentum=momentum_config,
                                candidate_tickers=candidate_tickers,
                                **group_config,
                            )
                            try:
                                _, positions, metrics = run_backtest(prices, config, traded_value=traded_value)
                            except ValueError as exc:
                                rows.append(
                                    {
                                        "run": run_number,
                                        "top_n": top_n,
                                        "rebalance": rebalance,
                                        "weighting": weighting,
                                        "skip_days": skip_days,
                                        "sma_window": sma_window,
                                        "min_relative_momentum": min_relative_momentum,
                                        "rank_by": rank_by,
                                        "accounting": args.accounting,
                                        "error": str(exc),
                                    }
                                )
                                continue

                            avg_turnover = float(positions["turnover"].mean()) if not positions.empty else 0.0
                            rows.append(
                                {
                                    "run": run_number,
                                    "top_n": top_n,
                                    "rebalance": rebalance,
                                    "weighting": weighting,
                                    "skip_days": skip_days,
                                    "sma_window": sma_window,
                                    "min_relative_momentum": min_relative_momentum,
                                    "rank_by": rank_by,
                                    "max_per_group": args.max_per_group,
                                    "accounting": args.accounting,
                                    "min_avg_traded_value": args.min_avg_traded_value,
                                    "target_vol": args.target_vol,
                                    "account": args.account,
                                    "avg_turnover": avg_turnover,
                                    **metrics,
                                }
                            )

    comparison = pd.DataFrame(rows)
    if not comparison.empty and "strategy_calmar" in comparison.columns:
        comparison = comparison.sort_values(
            ["strategy_calmar", "strategy_cagr", "strategy_max_drawdown"],
            ascending=[False, False, False],
            na_position="last",
        )
    comparison.to_csv(output_dir / "comparison.csv", index=False)

    display_columns = [
        "top_n",
        "rebalance",
        "weighting",
        "skip_days",
        "sma_window",
        "min_relative_momentum",
        "rank_by",
        "strategy_cagr",
        "strategy_max_drawdown",
        "strategy_calmar",
        "strategy_sharpe",
        "benchmark_cagr",
        "excess_cagr",
        "annual_transaction_cost",
        "avg_turnover",
    ]
    existing_columns = [column for column in display_columns if column in comparison.columns]
    formatters = {
        column: (lambda value, column=column: _format_metric(column, value))
        for column in existing_columns
        if pd.api.types.is_float_dtype(comparison[column])
    }
    print(comparison[existing_columns].head(15).to_string(index=False, formatters=formatters))
    print(f"\nSaved: {output_dir / 'comparison.csv'}")


def run_walkforward_command(args: argparse.Namespace) -> None:
    output_dir = ensure_output_dir(args.output_dir)
    members = load_universe(args.universe)
    tickers = unique_tickers(members, extras=[args.benchmark, args.cash, *(_risk_on_tickers(args) or ())])
    prices = _download(tickers, args.start, args)
    traded_value = _traded_value(tickers, args.start, args, prices)
    prices, traded_value = _apply_proxies(members, prices, traded_value, args.start, args)
    _require_tickers(prices, _required(args, include_cash=True))
    save_run_metadata(output_dir, args, prices, traded_value=traded_value)
    if args.cash and args.cash not in prices.columns:
        raise SystemExit(f"Cash ticker {args.cash} has no price data; use --cash none to hold uninvested cash")

    base = _momentum_config_from_args(args)
    candidate_tickers = _candidates(members, args)
    groups = {member.ticker: member.group for member in members if member.group}
    grid = list(
        itertools.product(
            _parse_int_list(args.top_n_grid),
            _parse_str_list(args.rebalance_grid),
            _parse_str_list(args.weighting_grid),
            _parse_str_list(args.rank_by_grid),
            _parse_int_list(args.max_per_group_grid),
            [value == "on" for value in _parse_str_list(args.risk_on_grid)],
            _parse_relative_strength_grid(args.relative_strength_grid),
            _parse_int_list(args.sma_window_grid) if args.sma_window_grid else [base.sma_window],
        )
    )

    returns: dict[str, pd.Series] = {}
    params: dict[str, dict[str, object]] = {}
    benchmark = pd.Series(dtype=float)
    for number, (top_n, rebalance, weighting, rank_by, max_per_group, risk_on, min_rel, sma) in enumerate(grid, 1):
        label = (
            f"top{top_n}|{rebalance}|{weighting}|{rank_by}|group{max_per_group}"
            f"|risk_{'on' if risk_on else 'off'}|rs_{'off' if min_rel is None else min_rel}|sma{sma}"
        )
        print(f"[{number}/{len(grid)}] {label}", flush=True)
        config = BacktestConfig(
            accounting=args.accounting,
            target_volatility=args.target_vol,
            max_risky_weight=_max_risky_weight(args),
            safe_tickers=_safe_tickers(members, args),
            risk_on_tickers=_risk_on_tickers(args),
            top_n=top_n,
            rebalance=rebalance,
            cash_ticker=args.cash,
            benchmark=args.benchmark,
            risk_on_benchmark=risk_on,
            transaction_cost_bps=args.transaction_cost_bps,
            weighting=weighting,
            max_position_weight=args.max_position_weight,
            momentum=replace(base, rank_by=rank_by, min_relative_momentum=min_rel, sma_window=sma),
            candidate_tickers=candidate_tickers,
            groups=groups,
            max_per_group=max_per_group if max_per_group > 0 else None,
        )
        try:
            equity, _, _ = run_backtest(prices, config, traded_value=traded_value)
        except ValueError as exc:
            print(f"  skipped: {exc}")
            continue
        returns[label] = equity["strategy"].pct_change().dropna()
        if benchmark.empty:
            benchmark = prices[args.benchmark].pct_change().dropna()
        params[label] = {
            "top_n": top_n,
            "rebalance": rebalance,
            "weighting": weighting,
            "rank_by": rank_by,
            "max_per_group": max_per_group,
            "risk_on_benchmark": risk_on,
            "min_relative_momentum": min_rel,
            "sma_window": sma,
            "accounting": args.accounting,
            "transaction_cost_bps": args.transaction_cost_bps,
            "min_avg_traded_value": args.min_avg_traded_value,
            "target_vol": args.target_vol,
            "account": args.account,
            "max_risky_weight": _max_risky_weight(args),
            "risk_on_ticker": args.risk_on_ticker,
        }
    if not returns:
        raise SystemExit("No parameter set produced a backtest")

    grid_returns = pd.DataFrame(returns)
    oos, windows = walk_forward(
        grid_returns,
        train_years=args.train_years,
        test_months=args.test_months,
        metric=args.metric,
        anchored=args.anchored,
    )
    if windows.empty:
        raise SystemExit("Not enough history for a single walk-forward window; lower --train-years or use an earlier --start")

    period = oos.index
    benchmark = benchmark.reindex(period).fillna(0.0)
    fixed = grid_returns.reindex(period).fillna(0.0)
    fixed_growth = (1 + fixed).prod()
    median_label = fixed_growth.sort_values().index[len(fixed_growth) // 2]
    hindsight_label = fixed_growth.idxmax()
    windows["benchmark_return"] = [
        float((1 + benchmark.loc[row.test_start : row.test_end]).prod() - 1) for row in windows.itertuples()
    ]
    windows["beat_benchmark"] = windows["test_return"] > windows["benchmark_return"]
    windows = pd.concat([windows, pd.DataFrame([params[label] for label in windows["selected"]])], axis=1)

    curves = pd.DataFrame(
        {
            "walk_forward": oos,
            "benchmark": benchmark,
            "median_fixed": fixed[median_label],
            "hindsight_best": fixed[hindsight_label],
        }
    )
    equity = oos_equity(curves, windows["train_end"].iloc[0])
    risk_free = prices[args.cash].pct_change() if args.cash else None
    metrics = summarize_performance(equity, risk_free=risk_free)
    grid_summary = pd.DataFrame(
        [{"label": label, **params[label], "oos_period_growth": float(fixed_growth[label])} for label in fixed.columns]
    ).sort_values("oos_period_growth", ascending=False)

    equity.to_csv(output_dir / "walkforward_equity.csv")
    windows.to_csv(output_dir / "walkforward_windows.csv", index=False)
    grid_summary.to_csv(output_dir / "walkforward_grid.csv", index=False)
    save_json(metrics, output_dir / "walkforward_summary.json")

    print(f"\nOut-of-sample period: {period[0].date()} to {period[-1].date()} ({len(windows)} windows)")
    print(f"{'':16s}{'CAGR':>9s}{'MaxDD':>9s}{'Calmar':>9s}{'Sharpe':>9s}{'Vol':>9s}")
    for name in curves.columns:
        print(
            f"{name:16s}{metrics[name + '_cagr']:9.1%}{metrics[name + '_max_drawdown']:9.1%}"
            f"{metrics[name + '_calmar']:9.2f}{metrics[name + '_sharpe']:9.2f}{metrics[name + '_volatility']:9.1%}"
        )
    print(f"\nWindows beating benchmark: {int(windows['beat_benchmark'].sum())}/{len(windows)}")
    print(f"Distinct parameter sets selected: {windows['selected'].nunique()} of {len(returns)}")
    print(f"Median fixed set:  {median_label}")
    print(f"Hindsight best:    {hindsight_label}")
    print("\nPer window:")
    print(
        windows[["test_start", "test_end", "selected", "test_return", "benchmark_return"]].to_string(
            index=False, formatters={"test_return": "{:.1%}".format, "benchmark_return": "{:.1%}".format}
        )
    )
    print(f"\nSaved: {output_dir / 'walkforward_windows.csv'}")


PORTFOLIO_EXECUTION = (
    "Evaluated on the calendar_ticker's trading days. same_day sleeves use that day's close; previous_close "
    "sleeves (foreign markets) use the latest close and FX dated before that day, as known at its close. "
    "Target weights are set at the close of the first common date and of each month's last trading day; "
    "rebalance costs are charged on the notional traded between sleeves. Sleeve curves carry their own "
    "execution assumptions (see their run_config files)."
)


def _input_file_record(path: Path) -> dict[str, object]:
    """Hash, date range and producing run (if a run_config sits next to it) of a sleeve input file."""
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    record: dict[str, object] = {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "first_date": str(frame.index[0].date()),
        "last_date": str(frame.index[-1].date()),
    }
    run_config = next(iter(sorted(path.parent.glob("*_run_config.json"))), None)
    if run_config is not None:
        produced_by = json.loads(run_config.read_text(encoding="utf-8"))
        record["produced_by"] = {k: produced_by.get(k) for k in ("command", "run_at", "code_revision", "arguments")}
    return record


def run_portfolio_command(args: argparse.Namespace) -> None:
    output_dir = ensure_output_dir(args.output_dir)
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    sleeves_spec: dict[str, dict] = spec["sleeves"]
    fx_tickers: dict[str, str] = spec.get("fx", {})
    base = spec.get("base_currency", "KRW")
    cash_ticker = spec.get("cash_ticker")
    calendar_ticker = spec.get("calendar_ticker")
    if not calendar_ticker:
        raise SystemExit("Portfolio spec needs a calendar_ticker (e.g. 069500.KS) that defines the evaluation days")

    tickers = {sleeve["ticker"] for sleeve in sleeves_spec.values() if "ticker" in sleeve} | {calendar_ticker}
    tickers |= {fx_tickers[sleeve["currency"]] for sleeve in sleeves_spec.values() if sleeve["currency"] != base}
    if cash_ticker:
        tickers.add(cash_ticker)
    prices = download_prices(
        sorted(tickers), start=spec.get("start"), end=spec.get("end"), cache_dir=args.cache_dir, refresh=args.refresh
    )
    print(f"Price data as of {pd.Timestamp(prices.index[-1]).date()}")
    if spec.get("end") is None and (message := staleness_message(prices)):
        print(f"WARNING: {message}")

    own: dict[str, pd.Series] = {}

    def own_dates(ticker: str) -> pd.Series:
        # Downloaded alone, so the series keeps only its own trading days. In the combined frame
        # every ticker is forward-filled onto the other markets' days (e.g. US days on Korean holidays).
        if ticker not in own:
            own[ticker] = download_prices(
                [ticker], start=spec.get("start"), end=spec.get("end"), cache_dir=args.cache_dir, refresh=args.refresh
            )[ticker]
        return own[ticker]

    calendar = pd.DatetimeIndex(own_dates(calendar_ticker).dropna().index)
    print(f"Evaluation calendar: {calendar_ticker} trading days, {calendar[0].date()} ~ {calendar[-1].date()}")
    sleeve_returns: dict[str, pd.Series] = {}
    inputs: dict[str, dict[str, object]] = {}
    for name, sleeve in sleeves_spec.items():
        if "ticker" in sleeve:
            curve = own_dates(sleeve["ticker"]).rename(name)
        else:
            path = Path(sleeve["equity_csv"])
            curve = pd.read_csv(path, index_col=0, parse_dates=True)[sleeve.get("column", "strategy")].rename(name)
            inputs[name] = _input_file_record(path)
        foreign = sleeve["currency"] != base
        timing = sleeve.get("timing", "previous_close" if foreign else "same_day")
        fx = own_dates(fx_tickers[sleeve["currency"]]) if foreign else None
        try:
            returns_on_calendar = sleeve_returns_on_calendar(curve, calendar, timing, fx=fx)
        except ValueError as exc:
            raise SystemExit(f"Sleeve {name}: {exc}") from exc
        sleeve_returns[name] = returns_on_calendar
        valid = returns_on_calendar.dropna()
        print(f"Sleeve {name} ({timing}): {valid.index[0].date()} ~ {valid.index[-1].date()}")
    save_run_metadata(
        output_dir,
        args,
        prices,
        extra={
            "spec": spec,
            "spec_sha256": hashlib.sha256(Path(args.spec).read_bytes()).hexdigest(),
            "sleeve_inputs": inputs,
        },
        execution=PORTFOLIO_EXECUTION,
    )
    returns = pd.DataFrame(sleeve_returns)
    cash_returns = prices[cash_ticker].reindex(calendar).ffill().pct_change() if cash_ticker else None

    curves: dict[str, pd.Series] = {}
    rows: list[dict[str, object]] = []
    for scenario in spec["scenarios"]:
        leverages = scenario.get("leverage", 1.0)
        for leverage in leverages if isinstance(leverages, list) else [leverages]:
            name = scenario["name"] + (f" x{leverage:g}" if leverage != 1.0 else "")
            config = PortfolioConfig(
                weights=scenario["weights"],
                leverage=leverage,
                borrow_rate=spec.get("borrow_rate", 0.07),
                rebalance_cost_bps=spec.get("rebalance_cost_bps", 0.0),
            )
            equity, history = run_portfolio(returns, config, cash_returns=cash_returns)
            curves[name] = equity
            for start, end in spec.get("periods", [[None, None]]):
                window = equity.loc[start:end]
                if window.empty:
                    continue
                metrics = summarize_performance((window / window.iloc[0]).to_frame("portfolio"), risk_free=cash_returns)
                rows.append(
                    {
                        "scenario": name,
                        "weights": ", ".join(f"{k} {v:.0%}" for k, v in config.weights.items()),
                        "leverage": leverage,
                        "period": f"{window.index[0].date()}~{window.index[-1].date()}",
                        "cagr": metrics["portfolio_cagr"],
                        "max_drawdown": metrics["portfolio_max_drawdown"],
                        "sharpe": metrics["portfolio_sharpe"],
                        "volatility": metrics["portfolio_volatility"],
                        "annual_rebalance_cost": annualized_cost(history, window.index),
                    }
                )

    summary = pd.DataFrame(rows)
    summary.to_csv(output_dir / "portfolio_summary.csv", index=False)
    pd.DataFrame(curves).to_csv(output_dir / "portfolio_equity.csv")
    for period, group in summary.groupby("period", sort=False):
        print(f"\n== {period} ({base} base)")
        print(
            group[["scenario", "cagr", "max_drawdown", "sharpe", "volatility"]].to_string(
                index=False,
                formatters={
                    "cagr": "{:.1%}".format,
                    "max_drawdown": "{:.1%}".format,
                    "volatility": "{:.1%}".format,
                    "sharpe": "{:.2f}".format,
                },
            )
        )
    print(f"\nSaved: {output_dir / 'portfolio_summary.csv'}")


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--universe", default="config/universe_us_sectors.csv", help="ETF universe CSV")
    parser.add_argument("--benchmark", default="SPY", help="Benchmark ticker")
    parser.add_argument("--start", default="2010-01-01", help="Start date for historical data")
    parser.add_argument("--end", default=None, help="End date for historical data")
    parser.add_argument("--lookback-years", type=_positive_int, default=5, help="Data years for ranking command")
    parser.add_argument("--lookbacks", default="63,126,252", help="Momentum lookbacks in trading days")
    parser.add_argument("--weights", default="0.3,0.3,0.4", help="Momentum weights for lookbacks")
    parser.add_argument("--skip-days", type=_non_negative_int, default=21, help="Recent days skipped in momentum calculation")
    parser.add_argument("--sma-window", type=_positive_int, default=200, help="Absolute trend filter window")
    parser.add_argument(
        "--rank-by",
        default="weighted_momentum",
        choices=RANK_BY_CHOICES,
        help="Score used to order ETFs (risk_adjusted_score = weighted momentum / volatility)",
    )
    parser.add_argument("--volatility-window", type=_positive_int, default=63, help="Realized volatility window")
    parser.add_argument("--min-weighted-momentum", type=_finite_float, default=0.0, help="Minimum weighted momentum for eligibility")
    parser.add_argument("--min-trend-strength", type=_finite_float, default=0.0, help="Minimum price-vs-SMA trend strength")
    parser.add_argument(
        "--require-relative-strength",
        action="store_true",
        help="Require weighted momentum to beat benchmark momentum",
    )
    parser.add_argument(
        "--min-relative-momentum",
        type=_finite_float,
        default=None,
        help="Minimum momentum spread over benchmark; implies relative-strength filter",
    )
    parser.add_argument(
        "--cash",
        default="BIL",
        type=_parse_cash,
        help="Cash-like ETF ticker, excluded from ranking candidates; 'none' for uninvested cash",
    )
    parser.add_argument(
        "--min-avg-traded-value",
        type=_non_negative_float,
        default=None,
        help="Exclude candidates whose 63-day average daily traded value is below this, in the ETF's currency "
        "(e.g. 1e9 = 10억 KRW, 2e7 = $20M); off by default",
    )
    parser.add_argument(
        "--account",
        default="all",
        choices=ACCOUNT_CHOICES,
        help="Restrict candidates to ETFs allowed in this account (pension = 연금저축, retirement = IRP/DC)",
    )
    parser.add_argument(
        "--max-risky-weight",
        type=_unit_fraction,
        default=None,
        help="Cap on total non-cash weight; defaults to 0.7 for --account retirement, else 1.0",
    )
    parser.add_argument(
        "--use-proxies",
        action="store_true",
        help="Fill pre-listing history of ETFs with a universe `proxy` ticker converted by --proxy-fx",
    )
    parser.add_argument("--proxy-fx", default="KRW=X", help="FX ticker: local currency per proxy currency unit")
    parser.add_argument("--cache-dir", default="data", help="Price cache directory")
    parser.add_argument("--output-dir", default="results", help="Output directory")
    parser.add_argument("--refresh", action="store_true", help="Refresh cached market data")


def _download(tickers: list[str], start: str | None, args: argparse.Namespace) -> pd.DataFrame:
    prices = download_prices(tickers, start=start, end=args.end, cache_dir=args.cache_dir, refresh=args.refresh)
    print(f"Price data as of {pd.Timestamp(prices.index[-1]).date()}")
    if args.end is None and (message := staleness_message(prices)):
        print(f"WARNING: {message}")
    return prices


RATIO_METRIC_SUFFIXES = ("_sharpe", "_sharpe_like", "_calmar", "_ending_value")


def _format_metric(key: str, value: float) -> str:
    """Ratios (Sharpe, Calmar, growth multiple) as plain numbers; returns, drawdowns, costs as percentages."""
    if pd.isna(value):
        return "n/a"
    return f"{value:.3f}" if key.endswith(RATIO_METRIC_SUFFIXES) else f"{value:.2%}"


def _exit_if_empty(ranking: pd.DataFrame) -> None:
    if ranking.empty:
        raise SystemExit("No candidate ETF has enough price history to rank; increase --lookback-years or check tickers")


def _group_config(members: list, args: argparse.Namespace) -> dict[str, object]:
    return {
        "groups": {member.ticker: member.group for member in members if member.group},
        "max_per_group": args.max_per_group if args.max_per_group > 0 else None,
    }


def _traded_value(
    tickers: list[str], start: str | None, args: argparse.Namespace, prices: pd.DataFrame
) -> pd.DataFrame | None:
    if args.min_avg_traded_value is None:
        return None
    value = download_traded_value(tickers, start=start, end=args.end, cache_dir=args.cache_dir, refresh=args.refresh)
    value_last = pd.Timestamp(value.dropna(how="all").index[-1])
    price_last = pd.Timestamp(prices.index[-1])
    print(f"Traded value data as of {value_last.date()}")
    lag = int(np.busday_count(value_last.date(), price_last.date()))
    if lag > LIQUIDITY_MAX_STALE_DAYS:
        print(
            f"WARNING: traded value data ends {lag} business days before prices ({price_last.date()}); "
            "ETFs are treated as illiquid where volume is stale. Rerun with --refresh."
        )
    missing = sorted(set(tickers) - set(value.dropna(axis=1, how="all").columns))
    if missing:
        print(f"WARNING: no traded value data for {', '.join(missing)}; they cannot pass the liquidity filter")
    return value


def _candidates(members: list, args: argparse.Namespace) -> tuple[str, ...]:
    """Universe members allowed in the chosen account; the benchmark and cash ETF are reference assets only."""
    return account_candidates(members, args.account, args.benchmark, args.cash)


def _conflicting_options(argv: list[str]) -> list[str]:
    """Selection options given on the command line, in either `--opt value` or `--opt=value` form."""
    given = {token.split("=", 1)[0] for token in argv if token.startswith("--")}
    return [option for option in STRATEGY_CONFLICTS if option in given]


def _apply_strategy(args: argparse.Namespace, strategy: Strategy) -> None:
    """Write the strategy's selection settings into args, so the normal command code runs unchanged."""
    args.strategy_info = strategy
    args.universe = strategy.universe
    args.account = strategy.account
    args.benchmark = strategy.benchmark
    args.cash = strategy.cash
    args.use_proxies = strategy.use_proxies
    args.proxy_fx = "KRW=X"
    args.lookbacks = ",".join(str(v) for v in strategy.lookbacks)
    args.weights = ",".join(str(v) for v in strategy.weights)
    args.skip_days = strategy.skip_days
    args.sma_window = strategy.sma_window
    args.volatility_window = strategy.volatility_window
    args.rank_by = strategy.rank_by
    args.min_avg_traded_value = strategy.min_avg_traded_value
    args.min_weighted_momentum = 0.0
    args.min_trend_strength = 0.0
    args.min_relative_momentum = None
    args.require_relative_strength = False
    args.max_risky_weight = None
    args.top_n = strategy.top_n
    args.max_per_group = strategy.max_per_group
    args.risk_on_benchmark = strategy.risk_on_benchmark
    args.risk_on_ticker = ",".join(strategy.risk_on_tickers) if strategy.risk_on_tickers else None


def _risk_on_tickers(args: argparse.Namespace) -> tuple[str, ...] | None:
    return tuple(_parse_str_list(args.risk_on_ticker)) if getattr(args, "risk_on_ticker", None) else None


def _safe_tickers(members: list, args: argparse.Namespace) -> tuple[str, ...]:
    """Universe members marked risk_asset=0. With a risky-asset cap, the cash ETF must be one of them
    when it is in the universe (otherwise the 'safe' remainder would itself be a risk asset)."""
    safe = tuple(m.ticker for m in members if not m.risk_asset)
    capped = _max_risky_weight(args) < 1.0 or args.account == "retirement"
    if not capped or not args.cash:
        return safe
    cash = next((m for m in members if m.ticker == args.cash), None)
    if cash is None:
        raise SystemExit(
            f"--cash {args.cash} is not an enabled member of the universe, so it can't be checked as a safe, "
            "account-eligible asset; add it with risk_asset=0"
        )
    if cash.risk_asset:
        raise SystemExit(f"--cash {args.cash} is marked risk_asset=1; a risky-asset cap needs a safe cash ETF")
    if args.account == "retirement" and not cash.retirement_ok:
        raise SystemExit(f"--cash {args.cash} is not allowed in retirement accounts (retirement_ok=0)")
    return safe


def _max_risky_weight(args: argparse.Namespace) -> float:
    if args.max_risky_weight is not None:
        return args.max_risky_weight
    return 0.7 if args.account == "retirement" else 1.0


def _apply_proxies(
    members: list, prices: pd.DataFrame, traded_value: pd.DataFrame | None, start: str | None, args: argparse.Namespace
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """With --use-proxies, fill each ETF's pre-listing history from its `proxy` ticker converted to local currency."""
    proxies = {m.ticker: m.proxy for m in members if m.proxy and m.ticker in prices.columns}
    if not args.use_proxies or not proxies:
        return prices, traded_value
    tickers = sorted(set(proxies.values()) | {args.proxy_fx})
    proxy_prices = download_prices(tickers, start=start, end=args.end, cache_dir=args.cache_dir, refresh=args.refresh)
    if args.proxy_fx not in proxy_prices.columns:
        raise SystemExit(f"No data for proxy FX {args.proxy_fx}; rerun with --refresh or without --use-proxies")
    unavailable = sorted({p for p in proxies.values() if p not in proxy_prices.columns})
    if unavailable:
        print(f"WARNING: no data for proxy ticker(s) {', '.join(unavailable)}; those ETFs keep only their own history")
        proxies = {t: p for t, p in proxies.items() if p in proxy_prices.columns}
    fx = proxy_prices[args.proxy_fx]
    prices, starts = splice_proxies(prices, {t: proxy_prices[p] for t, p in proxies.items()}, fx)
    if traded_value is not None:
        proxy_value = download_traded_value(tickers, start=start, end=args.end, cache_dir=args.cache_dir, refresh=args.refresh)
        available = {t: proxy_value[p] for t, p in proxies.items() if p in proxy_value.columns and t in traded_value.columns}
        traded_value, _ = splice_proxies(traded_value, available, fx, scale=False)
    args.proxy_history = {t: f"{proxies[t]} until {d.date()}" for t, d in starts.items() if d > prices.index[0]}
    for ticker, text in args.proxy_history.items():
        print(f"Proxy history: {ticker} <- {text}")
    return prices, traded_value


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError(f"must be a finite number, got {value}")
    return number


def _non_negative_float(value: str) -> float:
    number = _finite_float(value)
    if number < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {value}")
    return number


def _positive_float(value: str) -> float:
    number = _finite_float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {value}")
    return number


def _unit_fraction(value: str) -> float:
    number = _finite_float(value)
    if not 0 < number <= 1:
        raise argparse.ArgumentTypeError(f"must be in (0, 1], got {value}")
    return number


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {value}")
    return number


def _non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {value}")
    return number


def _require_tickers(prices: pd.DataFrame, required: dict[str, str | None]) -> None:
    """Stop when a benchmark, market filter or cash ETF the run depends on came back without data."""
    missing = [f"{role} {ticker}" for role, ticker in required.items() if ticker and (ticker not in prices.columns or prices[ticker].dropna().empty)]
    if missing:
        raise SystemExit(f"No price data for required ticker(s): {', '.join(missing)}. Check the ticker or rerun with --refresh")


def _required(args: argparse.Namespace, include_cash: bool) -> dict[str, str | None]:
    required: dict[str, str | None] = {"benchmark": args.benchmark}
    for number, ticker in enumerate(_risk_on_tickers(args) or (), 1):
        required[f"market filter {number}"] = ticker
    if include_cash:
        required["cash"] = args.cash
    return required


def _parse_cash(value: str) -> str | None:
    return None if value.strip().lower() in {"", "none", "off"} else value.strip()


def _momentum_config_from_args(args: argparse.Namespace) -> MomentumConfig:
    lookbacks = tuple(int(value.strip()) for value in args.lookbacks.split(",") if value.strip())
    weights = tuple(float(value.strip()) for value in args.weights.split(",") if value.strip())
    if len(lookbacks) != 3 or len(weights) != 3:
        raise ValueError("This CLI expects exactly three lookbacks and three weights")
    min_relative_momentum = args.min_relative_momentum
    if min_relative_momentum is None and args.require_relative_strength:
        min_relative_momentum = 0.0
    return MomentumConfig(
        lookbacks=lookbacks,
        weights=weights,
        skip_days=args.skip_days,
        sma_window=args.sma_window,
        volatility_window=args.volatility_window,
        min_weighted_momentum=args.min_weighted_momentum,
        min_trend_strength=args.min_trend_strength,
        min_relative_momentum=min_relative_momentum,
        rank_by=args.rank_by,
        min_avg_traded_value=args.min_avg_traded_value,
    )


def _attach_names(ranking: pd.DataFrame, members: list) -> pd.DataFrame:
    lookup = {member.ticker: member for member in members}
    ranking = ranking.copy()
    ranking["name"] = ranking["ticker"].map(lambda ticker: lookup[ticker].name if ticker in lookup else ticker)
    ranking["category"] = ranking["ticker"].map(lambda ticker: lookup[ticker].category if ticker in lookup else "Extra")
    ranking["group"] = ranking["ticker"].map(lambda ticker: lookup[ticker].group if ticker in lookup else "")
    return ranking


def _start_from_lookback_years(years: int) -> str:
    return (date.today() - timedelta(days=max(years, 1) * 366)).isoformat()


def _parse_str_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_int_list(value: str) -> list[int]:
    return [int(item) for item in _parse_str_list(value)]


def _parse_relative_strength_grid(value: str) -> list[float | None]:
    parsed: list[float | None] = []
    for item in _parse_str_list(value):
        lowered = item.lower()
        if lowered in {"off", "false", "none", "no"}:
            parsed.append(None)
        elif lowered in {"on", "true", "yes"}:
            parsed.append(0.0)
        else:
            parsed.append(float(item))
    return parsed


if __name__ == "__main__":
    main()
