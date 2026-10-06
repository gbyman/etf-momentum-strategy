from __future__ import annotations

import argparse
import itertools
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from etf_momentum.backtest import ACCOUNTING_CHOICES, BacktestConfig, run_backtest, summarize_performance
from etf_momentum.data import download_prices, download_traded_value, load_universe, staleness_message, unique_tickers
from etf_momentum.reporting import ensure_output_dir, save_equity_chart, save_json, save_run_metadata
from etf_momentum.signals import LIQUIDITY_MAX_STALE_DAYS, RANK_BY_CHOICES, MomentumConfig, rank_momentum
from etf_momentum.walkforward import METRICS, walk_forward
from etf_momentum.weekly import append_snapshot, build_weekly_report, load_previous_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="ETF sector momentum ranking and backtesting")
    subparsers = parser.add_subparsers(dest="command", required=True)

    rank_parser = subparsers.add_parser("rank", help="Rank ETFs by momentum")
    _add_common_args(rank_parser)
    rank_parser.add_argument("--top", type=int, default=20, help="Number of rows to print")
    rank_parser.set_defaults(func=run_rank_command)

    weekly_parser = subparsers.add_parser("weekly", help="Weekly leadership report against prior snapshot")
    _add_common_args(weekly_parser)
    weekly_parser.add_argument("--top", type=int, default=15, help="Number of report rows to print")
    weekly_parser.add_argument("--watch-rank", type=int, default=10, help="Ranks to monitor for leadership changes")
    weekly_parser.add_argument("--leader-rank", type=int, default=5, help="Ranks considered persistent leadership")
    weekly_parser.add_argument("--rise-threshold", type=int, default=3, help="Rank improvement treated as rising")
    weekly_parser.add_argument("--drop-threshold", type=int, default=3, help="Rank deterioration treated as fading")
    weekly_parser.add_argument("--history-file", default=None, help="Ranking history CSV; defaults to output-dir/rank_history.csv")
    weekly_parser.add_argument("--no-save-history", action="store_true", help="Do not append this run to history")
    weekly_parser.set_defaults(func=run_weekly_command)

    backtest_parser = subparsers.add_parser("backtest", help="Backtest monthly ETF momentum rotation")
    _add_common_args(backtest_parser)
    backtest_parser.add_argument("--top-n", type=int, default=3, help="Number of ETFs to hold")
    backtest_parser.add_argument("--rebalance", default="M", help="Pandas resample frequency, e.g. M or W-FRI")
    backtest_parser.add_argument("--risk-on-benchmark", action="store_true", help="Hold cash when benchmark is below SMA")
    backtest_parser.add_argument("--transaction-cost-bps", type=float, default=0.0, help="One-way trading cost in basis points")
    backtest_parser.add_argument(
        "--weighting",
        default="equal",
        choices=["equal", "inverse_vol", "score", "risk_adjusted_score"],
        help="Portfolio weighting method for selected ETFs",
    )
    backtest_parser.add_argument("--max-position-weight", type=float, default=1.0, help="Maximum single ETF weight")
    backtest_parser.add_argument(
        "--max-per-group",
        type=int,
        default=1,
        help="Max ETFs held from one universe 'group'; 0 disables the limit",
    )
    backtest_parser.add_argument(
        "--accounting",
        default="buy_and_hold",
        choices=ACCOUNTING_CHOICES,
        help="buy_and_hold: weights drift between rebalances; daily_rebalanced: weights reset daily",
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
    compare_parser.add_argument("--transaction-cost-bps", type=float, default=0.0, help="One-way trading cost in basis points")
    compare_parser.add_argument("--max-position-weight", type=float, default=1.0, help="Maximum single ETF weight")
    compare_parser.add_argument(
        "--max-per-group",
        type=int,
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
    wf_parser.add_argument("--transaction-cost-bps", type=float, default=5.0, help="One-way trading cost in basis points")
    wf_parser.add_argument("--max-position-weight", type=float, default=1.0, help="Maximum single ETF weight")
    wf_parser.add_argument("--train-years", type=int, default=5, help="Training window length in years")
    wf_parser.add_argument("--test-months", type=int, default=12, help="Test window length (and step) in months")
    wf_parser.add_argument("--metric", default="calmar", choices=METRICS, help="Training score used to pick parameters")
    wf_parser.add_argument("--anchored", action="store_true", help="Expanding training window from the first date")
    wf_parser.add_argument(
        "--accounting",
        default="buy_and_hold",
        choices=ACCOUNTING_CHOICES,
        help="buy_and_hold: weights drift between rebalances; daily_rebalanced: weights reset daily",
    )
    wf_parser.set_defaults(func=run_walkforward_command)

    args = parser.parse_args()
    args.func(args)


def run_rank_command(args: argparse.Namespace) -> None:
    output_dir = ensure_output_dir(args.output_dir)
    members = load_universe(args.universe)
    tickers = unique_tickers(members, extras=[args.benchmark])
    start = _start_from_lookback_years(args.lookback_years)
    prices = _download(tickers, start, args)
    traded_value = _traded_value(tickers, start, args, prices)
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
    print(ranking[columns].head(args.top).to_string(index=False, float_format=lambda value: f"{value: .2%}"))
    print(f"\nSaved: {output_dir / 'latest_ranking.csv'}")


def run_weekly_command(args: argparse.Namespace) -> None:
    output_dir = ensure_output_dir(args.output_dir)
    members = load_universe(args.universe)
    tickers = unique_tickers(members, extras=[args.benchmark])
    start = _start_from_lookback_years(args.lookback_years)
    prices = _download(tickers, start, args)
    traded_value = _traded_value(tickers, start, args, prices)
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
    tickers = unique_tickers(members, extras=[args.benchmark, args.cash])
    prices = _download(tickers, args.start, args)
    traded_value = _traded_value(tickers, args.start, args, prices)
    save_run_metadata(output_dir, args, prices, traded_value=traded_value)
    momentum_config = _momentum_config_from_args(args)
    config = BacktestConfig(
        accounting=args.accounting,
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
        if "ending_value" in key:
            print(f"  {key}: {value:.3f}")
        else:
            print(f"  {key}: {value:.2%}")
    print(f"\nSaved results under: {output_dir}")


def run_compare_command(args: argparse.Namespace) -> None:
    output_dir = ensure_output_dir(args.output_dir)
    members = load_universe(args.universe)
    tickers = unique_tickers(members, extras=[args.benchmark, args.cash])
    prices = _download(tickers, args.start, args)
    traded_value = _traded_value(tickers, args.start, args, prices)
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
        "benchmark_cagr",
        "avg_turnover",
    ]
    existing_columns = [column for column in display_columns if column in comparison.columns]
    print(comparison[existing_columns].head(15).to_string(index=False, float_format=lambda value: f"{value: .2%}"))
    print(f"\nSaved: {output_dir / 'comparison.csv'}")


def run_walkforward_command(args: argparse.Namespace) -> None:
    output_dir = ensure_output_dir(args.output_dir)
    members = load_universe(args.universe)
    tickers = unique_tickers(members, extras=[args.benchmark, args.cash])
    prices = _download(tickers, args.start, args)
    traded_value = _traded_value(tickers, args.start, args, prices)
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
    equity = (1 + curves).cumprod()
    metrics = summarize_performance(equity)
    grid_summary = pd.DataFrame(
        [{"label": label, **params[label], "oos_period_growth": float(fixed_growth[label])} for label in fixed.columns]
    ).sort_values("oos_period_growth", ascending=False)

    equity.to_csv(output_dir / "walkforward_equity.csv")
    windows.to_csv(output_dir / "walkforward_windows.csv", index=False)
    grid_summary.to_csv(output_dir / "walkforward_grid.csv", index=False)
    save_json(metrics, output_dir / "walkforward_summary.json")

    print(f"\nOut-of-sample period: {period[0].date()} to {period[-1].date()} ({len(windows)} windows)")
    print(f"{'':16s}{'CAGR':>9s}{'MaxDD':>9s}{'Calmar':>9s}{'Vol':>9s}")
    for name in curves.columns:
        print(
            f"{name:16s}{metrics[name + '_cagr']:9.1%}{metrics[name + '_max_drawdown']:9.1%}"
            f"{metrics[name + '_calmar']:9.2f}{metrics[name + '_volatility']:9.1%}"
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


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--universe", default="config/universe_us_sectors.csv", help="ETF universe CSV")
    parser.add_argument("--benchmark", default="SPY", help="Benchmark ticker")
    parser.add_argument("--start", default="2010-01-01", help="Start date for historical data")
    parser.add_argument("--end", default=None, help="End date for historical data")
    parser.add_argument("--lookback-years", type=int, default=5, help="Data years for ranking command")
    parser.add_argument("--lookbacks", default="63,126,252", help="Momentum lookbacks in trading days")
    parser.add_argument("--weights", default="0.3,0.3,0.4", help="Momentum weights for lookbacks")
    parser.add_argument("--skip-days", type=int, default=21, help="Recent days skipped in momentum calculation")
    parser.add_argument("--sma-window", type=int, default=200, help="Absolute trend filter window")
    parser.add_argument(
        "--rank-by",
        default="weighted_momentum",
        choices=RANK_BY_CHOICES,
        help="Score used to order ETFs (risk_adjusted_score = weighted momentum / volatility)",
    )
    parser.add_argument("--volatility-window", type=int, default=63, help="Realized volatility window")
    parser.add_argument("--min-weighted-momentum", type=float, default=0.0, help="Minimum weighted momentum for eligibility")
    parser.add_argument("--min-trend-strength", type=float, default=0.0, help="Minimum price-vs-SMA trend strength")
    parser.add_argument(
        "--require-relative-strength",
        action="store_true",
        help="Require weighted momentum to beat benchmark momentum",
    )
    parser.add_argument(
        "--min-relative-momentum",
        type=float,
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
        type=float,
        default=None,
        help="Exclude candidates whose 63-day average daily traded value is below this, in the ETF's currency "
        "(e.g. 1e9 = 10억 KRW, 2e7 = $20M); off by default",
    )
    parser.add_argument("--cache-dir", default="data", help="Price cache directory")
    parser.add_argument("--output-dir", default="results", help="Output directory")
    parser.add_argument("--refresh", action="store_true", help="Refresh cached market data")


def _download(tickers: list[str], start: str | None, args: argparse.Namespace) -> pd.DataFrame:
    prices = download_prices(tickers, start=start, end=args.end, cache_dir=args.cache_dir, refresh=args.refresh)
    print(f"Price data as of {pd.Timestamp(prices.index[-1]).date()}")
    if args.end is None and (message := staleness_message(prices)):
        print(f"WARNING: {message}")
    return prices


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
    """Universe members eligible for ranking: the benchmark and cash ETF are reference assets only."""
    excluded = {args.benchmark, args.cash}
    return tuple(member.ticker for member in members if member.ticker not in excluded)


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
