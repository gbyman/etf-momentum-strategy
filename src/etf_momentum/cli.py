from __future__ import annotations

import argparse
import functools
import hashlib
import math
import sys
import itertools
import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from etf_momentum.backtest import (
    ACCOUNTING_CHOICES,
    EXECUTION_CHOICES,
    BacktestConfig,
    StaleDataError,
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
    proxy_observations,
    splice_proxies,
    staleness_message,
    unique_tickers,
)
from etf_momentum.portfolio import PortfolioConfig, annualized_cost, run_portfolio, sleeve_returns_on_calendar
from etf_momentum.records import record_signal
from etf_momentum.reporting import _code_revision, ensure_output_dir, save_equity_chart, save_json, save_run_metadata
from etf_momentum.strategy import Strategy, StrategyError, load_strategy
from etf_momentum.signals import LIQUIDITY_MAX_STALE_DAYS, PRICE_MAX_STALE_DAYS, RANK_BY_CHOICES, MomentumConfig, observed_freshness, rank_momentum
from etf_momentum.walkforward import METRICS, oos_equity, walk_forward
from etf_momentum.weekly import append_snapshot, append_strategy_snapshot, build_weekly_report, load_previous_snapshot, load_strategy_snapshot


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
        "--holdings",
        default=None,
        help="Comma-separated tickers held now; prints keep / add / drop against the month-end selection",
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
    backtest_parser.add_argument(
        "--execution",
        default="same_close",
        choices=EXECUTION_CHOICES,
        help="same_close: trade at the signal close; next_close: trade at the next trading day's close",
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
    compare_parser.add_argument(
        "--execution",
        default="same_close",
        choices=EXECUTION_CHOICES,
        help="same_close: trade at the signal close; next_close: trade at the next trading day's close",
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
    wf_parser.add_argument(
        "--execution",
        default="same_close",
        choices=EXECUTION_CHOICES,
        help="same_close: trade at the signal close; next_close: trade at the next trading day's close",
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
    _validate_cash(members, args)
    tickers = unique_tickers(members, extras=[args.benchmark, args.cash, *(_risk_on_tickers(args) or ())])
    _require_one_market(tickers)
    start = _start_from_lookback_years(args.lookback_years, args.end)
    prices, raw = _download(tickers, start, args)
    traded_value = _traded_value(tickers, start, args, prices)
    prices, traded_value = _apply_proxies(members, prices, traded_value, start, args)
    observed = _observations(raw, args, prices)
    # The month-end selection names the cash ETF as the waiting asset, so it must have current data.
    _require_tickers(prices, _required(args, include_cash=True))
    _require_fresh(observed, _required(args, include_cash=True))
    save_run_metadata(output_dir, args, prices, traded_value=traded_value, execution=_execution_note(args))
    config = _momentum_config_from_args(args)
    ranking = rank_momentum(
        prices, benchmark=args.benchmark, config=config, candidate_tickers=_candidates(members, args), traded_value=traded_value, observed=observed
    )
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

    selection = _month_end_selection(members, prices, traded_value, observed, config, args)
    selection.to_csv(output_dir / "latest_selection.csv", index=False)
    print(f"Saved: {output_dir / 'latest_selection.csv'}")
    if args.holdings:
        selected = selection.loc[selection["status"] == "selected", "ticker"].tolist()
        changes = _holdings_changes(_parse_str_list(args.holdings), selected, args.cash)
        print("\n[현재 보유와 비교] (수량·주문은 직접 정하세요)")
        print(f" 유지: {', '.join(changes['keep']) or '-'}")
        print(f" 신규 편입: {', '.join(changes['add']) or '-'}")
        print(f" 선정 제외: {', '.join(changes['drop']) or '-'}")
    strategy = getattr(args, "strategy_info", None)
    if strategy is not None:
        _record_month_end_signal(output_dir, strategy, selection[selection["status"] != "excluded"], prices, args)


def _selection_today(args: argparse.Namespace) -> pd.Timestamp | None:
    """A past --end (exclusive download end) makes this a lookup as of that date; None means a live run.

    --end today or later downloads up to the latest data anyway, so it is judged as of today."""
    if not args.end:
        return None
    end = pd.Timestamp(args.end).normalize()
    return end if end <= pd.Timestamp.today().normalize() else None


def _holdings_changes(current: list[str], selected: list[str], cash: str | None) -> dict[str, list[str]]:
    """Keep / add / drop against the selection; the cash ETF is a parking place, never 'dropped'."""
    held = [t for t in current if t != cash]
    return {
        "keep": [t for t in selected if t in held],
        "add": [t for t in selected if t not in held],
        "drop": [t for t in held if t not in selected],
    }


def _signal_meta(selection: pd.DataFrame, prices: pd.DataFrame, args: argparse.Namespace, strategy: Strategy) -> dict[str, object]:
    revision = _code_revision() or {}
    dirty = revision.get("dirty")
    return {
        "signal_date": selection["as_of"].iloc[0],
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "strategy_id": strategy.id,
        "version": strategy.version,
        "config_hash": strategy.config_hash,
        "code_commit": revision.get("commit", ""),
        "code_dirty": "" if dirty is None else str(bool(dirty)).lower(),
        "price_last_date": str(pd.Timestamp(prices.index[-1]).date()),
    }


def _record_month_end_signal(
    output_dir: Path, strategy: Strategy, selection: pd.DataFrame, prices: pd.DataFrame, args: argparse.Namespace
) -> None:
    if _selection_today(args) is not None:
        print("과거 기준일 조회라 신호를 기록하지 않습니다 (--end)")
        return
    meta = _signal_meta(selection, prices, args, strategy)
    path = output_dir / "strategies" / strategy.id / "signals.csv"
    try:
        result = record_signal(path, selection, meta)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if result.action == "added":
        print(f"신호 기록: {meta['signal_date']} (버전 {strategy.version}, {strategy.config_hash}) → {path}")
    elif result.action == "unchanged":
        print(f"이미 기록됨: {meta['signal_date']} (revision {result.revision})")
    else:
        print(f"WARNING: 같은 월말·같은 규칙인데 결과가 달라 revision {result.revision}로 추가했습니다")
        for change in result.changes:
            print(f"  {change}")


def _month_end_selection(
    members: list,
    prices: pd.DataFrame,
    traded_value: pd.DataFrame | None,
    observed: pd.DataFrame,
    config: MomentumConfig,
    args: argparse.Namespace,
) -> pd.DataFrame:
    """Print and return the ETFs the strategy would hold after the previous month-end close."""
    as_of = _month_end_date(prices, observed, args)
    ranking = rank_momentum(
        prices,
        as_of,
        benchmark=args.benchmark,
        config=config,
        candidate_tickers=_candidates(members, args),
        traded_value=traded_value,
        observed=observed,
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
    picked = select_holdings(ranking, prices, as_of, rule, observed=observed)
    names = {m.ticker: m.name for m in members}
    groups = {m.ticker: m.group for m in members}
    filters = ", ".join(_risk_on_tickers(args) or (args.benchmark,))
    metrics = ranking.set_index("ticker")

    print(f"\n[선택 종목] 기준일 {as_of.date()} (직전 월말 종가), 상위 {args.top_n}개, 그룹당 최대 {args.max_per_group or '제한 없음'}")
    if args.risk_on_benchmark:
        print(f"시장 필터: {filters} {args.sma_window}일선 {'위 → 투자' if picked.risk_on else '아래 → 전액 현금'}")
    rows: list[dict[str, object]] = []
    for number, ticker in enumerate(picked.tickers, 1):
        m = metrics.loc[ticker]
        print(
            f" {number}. {ticker:<10} {names.get(ticker, ticker)}  {groups.get(ticker) or '-'}"
            f"  | 순위 {int(m['rank'])}, {_score_text(m, args.rank_by)}, {args.sma_window}일선 {m['trend_strength_vs_sma']:+.1%}"
            f"{_traded_value_text(m['avg_traded_value'])}"
        )
        rows.append({"status": "selected", "rank_in_selection": number, "ticker": ticker, "group": groups.get(ticker, "")})
    if picked.empty_slots:
        cash = args.cash or "미투자 현금"
        reason = "시장 필터 꺼짐" if not picked.risk_on else "조건 통과 후보 부족"
        print(f" 빈 자리 {picked.empty_slots}개 → 현금 ({cash}): {reason}")
        rows.append({"status": "cash", "ticker": args.cash or "", "slots": picked.empty_slots, "reason": reason})
    if picked.group_skipped:
        skipped = ", ".join(f"{t} ({groups.get(t)})" for t in picked.group_skipped)
        print(f" 그룹 제한으로 제외: {skipped}")
        rows.extend(
            {"status": "group_limit", "ticker": t, "group": groups.get(t, ""), "reason": f"그룹 제한 ({groups.get(t)})"}
            for t in picked.group_skipped
        )
    # Better-ranked ETFs that failed a check: "why wasn't this one picked?"
    cutoff = int(metrics.loc[picked.tickers, "rank"].max()) if picked.tickers else args.top_n
    passed_over = ranking[(ranking["rank"] <= cutoff) & ~ranking["eligible"].astype(bool)].sort_values("rank")
    if not passed_over.empty:
        reasons = {
            row.ticker: _exclusion_reason(pd.Series(row._asdict()), config, observed=True) for row in passed_over.itertuples(index=False)
        }
        print(" 상위 후보 중 제외: " + ", ".join(f"{t} ({r})" for t, r in reasons.items()))
        rows.extend({"status": "excluded", "ticker": t, "group": groups.get(t, ""), "reason": r} for t, r in reasons.items())
    frame = pd.DataFrame(rows, columns=["status", "rank_in_selection", "ticker", "group", "slots", "reason"])
    frame.insert(0, "as_of", str(as_of.date()))
    frame["name"] = frame["ticker"].map(lambda t: names.get(t, t))
    for column in ("rank", "weighted_momentum", "trend_strength_vs_sma", "avg_traded_value", "last_observed"):
        frame[column] = frame["ticker"].map(lambda t, c=column: metrics[c].get(t) if t in metrics.index else None)
    frame["risk_on"] = picked.risk_on
    frame["market_filter"] = ("on" if picked.risk_on else "off") if args.risk_on_benchmark else "unused"
    return frame


EXCLUSION_CHECKS = (
    ("positive_momentum", "{momentum}"),
    ("above_sma", "{trend}"),
    ("relative_ok", "상대강도 미달"),
    ("liquid", "유동성 부족"),
    ("volatility_ok", "변동성 계산 불가"),
    ("price_fresh", "{stale}"),
)


def _exclusion_reason(row: pd.Series, config: MomentumConfig | None = None, observed: bool = False) -> str:
    """Failed checks in words, naming the thresholds `config` used (defaults: momentum > 0, above the SMA).

    With `observed` (freshness came from the observation table) a stale price is reported as missing
    observations with the last observation date, not as an unchanged price."""
    config = config or MomentumConfig()
    stale = "가격 정체"
    if observed:
        last = row.get("last_observed")
        stale = "관측 없음" if last is None or pd.isna(last) else f"최근 관측 없음 (마지막 {pd.Timestamp(last).date()})"
    sma = config.sma_window
    momentum = "모멘텀≤0" if config.min_weighted_momentum == 0 else f"모멘텀<{config.min_weighted_momentum:.1%}"
    trend = f"{sma}일선 아래" if config.min_trend_strength == 0 else f"{sma}일선 대비 {config.min_trend_strength:.1%} 미만"
    return ", ".join(
        label.format(momentum=momentum, trend=trend, stale=stale) for column, label in EXCLUSION_CHECKS if column in row and not bool(row[column])
    )


def _score_text(metrics: pd.Series, rank_by: str) -> str:
    """The score that actually ordered the ranking."""
    if rank_by == "risk_adjusted_score":
        return f"위험조정 점수 {metrics['risk_adjusted_score']:.2f}"
    return f"점수 {metrics['weighted_momentum']:.1%}"


def _traded_value_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= size:
            return f", 거래대금 {value / size:,.1f}{suffix}"
    return f", 거래대금 {value:,.0f}"


def run_weekly_command(args: argparse.Namespace) -> None:
    output_dir = ensure_output_dir(args.output_dir)
    members = load_universe(args.universe)
    _validate_cash(members, args)
    tickers = unique_tickers(members, extras=[args.benchmark])
    _require_one_market(tickers)
    start = _start_from_lookback_years(args.lookback_years, args.end)
    prices, raw = _download(tickers, start, args)
    traded_value = _traded_value(tickers, start, args, prices)
    prices, traded_value = _apply_proxies(members, prices, traded_value, start, args)
    observed = _observations(raw, args, prices)
    _require_tickers(prices, _required(args, include_cash=False, include_filters=False))
    _require_fresh(observed, _required(args, include_cash=False, include_filters=False))
    save_run_metadata(output_dir, args, prices, traded_value=traded_value, execution=_execution_note(args))
    config = _momentum_config_from_args(args)
    ranking = rank_momentum(
        prices, benchmark=args.benchmark, config=config, candidate_tickers=_candidates(members, args), traded_value=traded_value, observed=observed
    )
    _exit_if_empty(ranking)
    ranking = _attach_names(ranking, members)
    ranking.to_csv(output_dir / "latest_ranking.csv", index=False)

    current_as_of = pd.Timestamp(ranking["as_of"].iloc[0])
    strategy = getattr(args, "strategy_info", None)
    if strategy is not None:
        history_path = output_dir / "strategies" / strategy.id / "weekly_history.csv"
        try:
            previous, notice = load_strategy_snapshot(history_path, current_as_of, strategy.config_hash, strategy.version)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if notice:
            print(notice)
    else:
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
        if strategy is not None:
            try:
                append_strategy_snapshot(history_path, ranking, strategy.version, strategy.config_hash)
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
        else:
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
    _validate_cash(members, args)
    tickers = unique_tickers(members, extras=[args.benchmark, args.cash, *(_risk_on_tickers(args) or ())])
    _require_one_market(tickers)
    prices, raw = _download(tickers, args.start, args)
    traded_value = _traded_value(tickers, args.start, args, prices)
    prices, traded_value = _apply_proxies(members, prices, traded_value, args.start, args)
    observed = _observations(raw, args, prices)
    _require_tickers(prices, _required(args, include_cash=True))
    _require_fresh(observed, _required(args, include_cash=True))
    save_run_metadata(output_dir, args, prices, traded_value=traded_value, execution=_execution_note(args))
    momentum_config = _momentum_config_from_args(args)
    config = BacktestConfig(
        accounting=args.accounting,
        execution=args.execution,
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
    equity, positions, metrics = run_backtest(prices, config, traded_value=traded_value, observed=observed, sessions=_exchange_sessions(args.benchmark))
    ranking = rank_momentum(
        prices,
        benchmark=args.benchmark,
        config=momentum_config,
        candidate_tickers=config.candidate_tickers,
        traded_value=traded_value,
        observed=observed,
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
    _validate_cash(members, args)
    tickers = unique_tickers(members, extras=[args.benchmark, args.cash, *(_risk_on_tickers(args) or ())])
    _require_one_market(tickers)
    prices, raw = _download(tickers, args.start, args)
    traded_value = _traded_value(tickers, args.start, args, prices)
    prices, traded_value = _apply_proxies(members, prices, traded_value, args.start, args)
    observed = _observations(raw, args, prices)
    _require_tickers(prices, _required(args, include_cash=True))
    _require_fresh(observed, _required(args, include_cash=True))
    save_run_metadata(output_dir, args, prices, traded_value=traded_value, execution=_execution_note(args))

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
                                execution=args.execution,
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
                                _, positions, metrics = run_backtest(prices, config, traded_value=traded_value, observed=observed, sessions=_exchange_sessions(args.benchmark))
                            except StaleDataError as exc:
                                raise SystemExit(f"Stale market filter data: {exc}. Rerun with --refresh or check the ticker") from exc
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
                                    "execution": args.execution,
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
    _validate_cash(members, args)
    tickers = unique_tickers(members, extras=[args.benchmark, args.cash, *(_risk_on_tickers(args) or ())])
    _require_one_market(tickers)
    prices, raw = _download(tickers, args.start, args)
    traded_value = _traded_value(tickers, args.start, args, prices)
    prices, traded_value = _apply_proxies(members, prices, traded_value, args.start, args)
    observed = _observations(raw, args, prices)
    _require_tickers(prices, _required(args, include_cash=True))
    _require_fresh(observed, _required(args, include_cash=True))
    save_run_metadata(output_dir, args, prices, traded_value=traded_value, execution=_execution_note(args))
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
            execution=args.execution,
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
            equity, _, _ = run_backtest(prices, config, traded_value=traded_value, observed=observed, sessions=_exchange_sessions(args.benchmark))
        except StaleDataError as exc:
            raise SystemExit(f"Stale market filter data: {exc}. Rerun with --refresh or check the ticker") from exc
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
            "execution": args.execution,
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
        risk_free=prices[args.cash].pct_change() if args.cash else None,
    )
    if windows.empty:
        raise SystemExit("Not enough history for a single walk-forward window; lower --train-years or use an earlier --start")

    period = oos.index
    benchmark = _benchmark_baseline(benchmark, period)
    fixed, incomplete = _fixed_baselines(grid_returns, period)
    if incomplete:
        print(f"Fixed-config baselines without full out-of-sample returns, left out: {', '.join(incomplete)}")
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


def _holds_cash(scenarios: list[dict]) -> bool:
    """Some scenario keeps part of the money in the cash ETF (leverage below 1)."""
    for scenario in scenarios:
        leverages = scenario.get("leverage", 1.0)
        for leverage in leverages if isinstance(leverages, list) else [leverages]:
            try:
                if float(leverage) < 1.0:
                    return True
            except (TypeError, ValueError):
                continue  # PortfolioConfig reports invalid values
    return False


def _require_cash_closes(closes: pd.Series, calendar: pd.DatetimeIndex, ticker: str) -> None:
    """Stop when the held cash ETF has no close for more than PRICE_MAX_STALE_DAYS evaluation days in a row
    (after its first close): its return there would be a made-up 0%, not cash interest."""
    seen = calendar.isin(closes.dropna().index)
    if not seen.any():
        return
    positions = np.arange(len(calendar))
    last = pd.Series(np.where(seen, positions, np.nan)).ffill().to_numpy()
    gaps = positions - last
    if np.nanmax(gaps) > PRICE_MAX_STALE_DAYS:
        end = int(np.nanargmax(gaps))
        raise SystemExit(
            f"Cash ETF {ticker} has no close for {int(gaps[end])} evaluation days up to {calendar[end].date()}; "
            "check its data or use a cash ETF with full history"
        )


def _used_sleeves(spec: dict) -> dict[str, dict]:
    """Sleeves some scenario holds (weight > 0). A sleeve every scenario weights 0 is not loaded or checked,
    so it cannot change or block the results. Non-numeric weights are kept for PortfolioConfig to reject."""

    def held(weight: object) -> bool:
        try:
            return float(weight) != 0  # NaN and negatives stay, so the config check reports them
        except (TypeError, ValueError):
            return True

    used = {name for scenario in spec["scenarios"] for name, weight in scenario["weights"].items() if held(weight)}
    skipped = [name for name in spec["sleeves"] if name not in used]
    if skipped:
        print(f"Unused sleeves (weight 0 in every scenario), not loaded: {', '.join(skipped)}")
    return {name: sleeve for name, sleeve in spec["sleeves"].items() if name in used}


def run_portfolio_command(args: argparse.Namespace) -> None:
    output_dir = ensure_output_dir(args.output_dir)
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    sleeves_spec: dict[str, dict] = _used_sleeves(spec)
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
                [ticker], start=spec.get("start"), end=spec.get("end"), cache_dir=args.cache_dir, refresh=args.refresh, raw=True
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
    if cash_ticker and _holds_cash(spec["scenarios"]):
        _require_cash_closes(own_dates(cash_ticker), calendar, cash_ticker)

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
            try:
                equity, history = run_portfolio(returns, config, cash_returns=cash_returns)
            except ValueError as exc:
                raise SystemExit(f"Scenario {name!r}: {exc}") from exc
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
    parser.add_argument("--lookbacks", type=_momentum_lookbacks, default="63,126,252", help="Momentum lookbacks in trading days")
    parser.add_argument("--weights", type=_momentum_weights, default="0.3,0.3,0.4", help="Momentum weights for lookbacks")
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


def _download(tickers: list[str], start: str | None, args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Filled prices for calculations and the raw closes they came from (one download)."""
    raw = download_prices(tickers, start=start, end=args.end, cache_dir=args.cache_dir, refresh=args.refresh, raw=True)
    prices = raw.ffill()
    print(f"Price data as of {pd.Timestamp(prices.index[-1]).date()}")
    if args.end is None and (message := staleness_message(prices)):
        print(f"WARNING: {message}")
    return prices, raw


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


def _execution_note(args: argparse.Namespace) -> str | None:
    """Run-record text for commands that simulate trades; None keeps the default note."""
    if getattr(args, "execution", "same_close") != "next_close":
        return None
    return (
        "Signals use closes up to and including the rebalance date; trades fill at the next trading day's close, "
        "the previous holdings earn that day; returns of the new holdings accrue from the day after; "
        "transaction costs are deducted on their first holding day."
    )


def _risk_on_tickers(args: argparse.Namespace) -> tuple[str, ...] | None:
    return tuple(_parse_str_list(args.risk_on_ticker)) if getattr(args, "risk_on_ticker", None) else None


def _safe_tickers(members: list, args: argparse.Namespace) -> tuple[str, ...]:
    """Universe members marked risk_asset=0. With a risky-asset cap, the cash ETF must be one of them
    when it is in the universe (otherwise the 'safe' remainder would itself be a risk asset)."""
    _validate_cash(members, args)
    return tuple(m.ticker for m in members if not m.risk_asset)


def _validate_cash(members: list, args: argparse.Namespace) -> None:
    """The cash ETF must be usable in the account: checked by every command before downloading.

    For pension / retirement accounts or a risky-asset cap it must be an enabled universe member;
    with a cap (retirement included) it must be a safe asset; and it must be allowed in the account."""
    if not args.cash:
        return
    capped = _max_risky_weight(args) < 1.0 or args.account == "retirement"
    if not capped and args.account == "all":
        return
    cash = next((m for m in members if m.ticker == args.cash), None)
    if cash is None:
        raise SystemExit(
            f"--cash {args.cash} is not an enabled member of the universe, so it can't be checked for the "
            f"{args.account} account; add it (enabled=1, risk_asset=0)"
        )
    if capped and cash.risk_asset:
        raise SystemExit(f"--cash {args.cash} is marked risk_asset=1; a risky-asset cap needs a safe cash ETF")
    if args.account == "retirement" and not cash.retirement_ok:
        raise SystemExit(f"--cash {args.cash} is not allowed in retirement accounts (retirement_ok=0)")
    if args.account == "pension" and not cash.pension_ok:
        raise SystemExit(f"--cash {args.cash} is not allowed in pension accounts (pension_ok=0)")


def _max_risky_weight(args: argparse.Namespace) -> float:
    if args.max_risky_weight is not None:
        return args.max_risky_weight
    return 0.7 if args.account == "retirement" else 1.0


def _apply_proxies(
    members: list, prices: pd.DataFrame, traded_value: pd.DataFrame | None, start: str | None, args: argparse.Namespace
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """With --use-proxies, fill each ETF's pre-listing history from its `proxy` ticker converted to local currency."""
    args.proxy_observed = {}
    proxies = {m.ticker: m.proxy for m in members if m.proxy and m.ticker in prices.columns}
    if not args.use_proxies or not proxies:
        return prices, traded_value
    tickers = sorted(set(proxies.values()) | {args.proxy_fx})
    # Raw closes: splice_proxies takes the last real close before each local date, and
    # proxy_observations needs the real observation dates.
    proxy_prices = download_prices(tickers, start=start, end=args.end, cache_dir=args.cache_dir, refresh=args.refresh, raw=True)
    if args.proxy_fx not in proxy_prices.columns:
        raise SystemExit(f"No data for proxy FX {args.proxy_fx}; rerun with --refresh or without --use-proxies")
    unavailable = sorted({p for p in proxies.values() if p not in proxy_prices.columns})
    if unavailable:
        print(f"WARNING: no data for proxy ticker(s) {', '.join(unavailable)}; those ETFs keep only their own history")
        proxies = {t: p for t, p in proxies.items() if p in proxy_prices.columns}
    fx = proxy_prices[args.proxy_fx]
    prices, starts = splice_proxies(prices, {t: proxy_prices[p] for t, p in proxies.items()}, fx)
    args.proxy_observed = {
        t: proxy_observations(proxy_prices[proxies[t]], fx, prices.index[prices.index < first])
        for t, first in starts.items()
    }
    if traded_value is not None:
        proxy_value = download_traded_value(tickers, start=start, end=args.end, cache_dir=args.cache_dir, refresh=args.refresh)
        available = {t: proxy_value[p] for t, p in proxies.items() if p in proxy_value.columns and t in traded_value.columns}
        traded_value, value_starts = splice_proxies(traded_value, available, fx, scale=False)
        traded_value = _mask_stale_proxy_values(traded_value, available, fx, value_starts)
    args.proxy_history = {t: f"{proxies[t]} until {d.date()}" for t, d in starts.items() if d > prices.index[0]}
    for ticker, text in args.proxy_history.items():
        print(f"Proxy history: {ticker} <- {text}")
    return prices, traded_value


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError(f"must be a finite number, got {value}")
    return number


def _momentum_lookbacks(value: str) -> str:
    """Three positive integers, e.g. 63,126,252 (kept as text; parsed by _momentum_config_from_args)."""
    items = _parse_str_list(value)
    if len(items) != 3 or not all(item.isdigit() and int(item) > 0 for item in items):
        raise argparse.ArgumentTypeError(f"must be three positive integers, got {value}")
    return value


def _momentum_weights(value: str) -> str:
    """Three finite numbers with a positive sum, the same rule as signal.weights in strategy files."""
    items = _parse_str_list(value)
    try:
        weights = [float(item) for item in items]
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be three numbers, got {value}") from None
    if len(weights) != 3 or not all(math.isfinite(w) for w in weights) or sum(weights) <= 0:
        raise argparse.ArgumentTypeError(f"must be three finite numbers with a positive sum, got {value}")
    return value


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


def _mask_stale_proxy_values(
    frame: pd.DataFrame, sources: dict[str, pd.Series], fx: pd.Series | None, starts: dict[str, pd.Timestamp]
) -> pd.DataFrame:
    """Blank proxy-filled rows (before each ETF's first own value) on which the proxy or FX had no new value.

    splice_proxies carries the last proxy value forward without limit; the liquidity average then sees
    these blanks and treats a proxy whose volume stopped as stale, like an ETF's own missing data."""
    frame = frame.copy()
    for ticker, first_real in starts.items():
        before = frame.index[frame.index < first_real]
        if len(before) and ticker in sources:
            seen = proxy_observations(sources[ticker], fx, before)
            frame.loc[before[~seen.to_numpy()], ticker] = np.nan
    return frame


def _observations(raw: pd.DataFrame, args: argparse.Namespace, prices: pd.DataFrame) -> pd.DataFrame:
    """Date x ticker table of real closes, from the raw frame `_download` returned with the prices.

    Days filled from a proxy before listing count as observed when the proxy had a new close
    (see data.proxy_observations), not merely because a value was carried forward."""
    observed = raw.reindex(index=prices.index, columns=prices.columns).notna()
    for ticker, seen in getattr(args, "proxy_observed", {}).items():
        if ticker in observed.columns:
            observed.loc[seen.index, ticker] = seen
    return observed


def _month_end_date(prices: pd.DataFrame, observed: pd.DataFrame, args: argparse.Namespace) -> pd.Timestamp:
    """Previous month-end on the benchmark's real observation days, by the exchange calendar when it covers the month."""
    today = _selection_today(args) or pd.Timestamp.today().normalize()
    benchmark_days = observed.index[observed[args.benchmark]] if args.benchmark in observed.columns else prices.index
    sessions = _month_end_sessions(args.benchmark, today)
    try:
        return previous_month_end(benchmark_days, today=today, sessions=sessions)
    except ValueError as exc:
        month = pd.Timestamp(today).to_period("M") - 1
        in_month = sessions[sessions.to_period("M") == month] if sessions is not None else sessions
        if in_month is not None and len(in_month) and in_month[-1] not in prices.index:
            # No ticker traded that day: more likely an unscheduled closure the calendar package doesn't know yet.
            raise SystemExit(
                f"직전 월말 선택을 만들 수 없습니다: {exc}. 거래소 달력에는 거래일이지만 어떤 티커에도 데이터가 없습니다. "
                "임시 휴장일이면 `uv lock --upgrade-package exchange-calendars`로 달력을 업데이트하세요"
            ) from exc
        raise SystemExit(f"직전 월말 선택을 만들 수 없습니다: {exc}. --refresh로 데이터를 다시 받으세요") from exc


def _require_fresh(observed: pd.DataFrame, required: dict[str, str | None]) -> None:
    """Stop when a benchmark, market filter or cash ETF has no recent real observation."""
    stale = []
    for role, ticker in required.items():
        if not ticker:
            continue
        last, fresh = observed_freshness(observed[ticker] if ticker in observed.columns else None, observed.index)
        if not fresh:
            stale.append(f"{role} {ticker} (last observation {last.date() if last is not None else 'none'})")
    if stale:
        raise SystemExit(f"Stale data for required ticker(s): {', '.join(stale)}. Rerun with --refresh or check the ticker")


@functools.lru_cache(maxsize=4)
def _exchange_sessions(benchmark: str) -> pd.DatetimeIndex | None:
    """Trading days of the benchmark's exchange: XKRX for .KS/.KQ tickers, otherwise XNYS."""
    import exchange_calendars as xc

    code = "XKRX" if benchmark.upper().endswith((".KS", ".KQ")) else "XNYS"
    return pd.DatetimeIndex(xc.get_calendar(code, start="2005-01-01").sessions).tz_localize(None)


def _month_end_sessions(benchmark: str, today: pd.Timestamp) -> pd.DatetimeIndex | None:
    """Exchange sessions when the calendar covers the month before `today`; otherwise None with a note."""
    sessions = _exchange_sessions(benchmark)
    month = pd.Timestamp(today).to_period("M") - 1
    if sessions is None or not ((sessions.to_period("M") == month).any() and sessions[-1] >= month.end_time.normalize()):
        print(f"거래소 달력이 {month}을(를) 다루지 않아 기존 월말 규칙으로 판단합니다")
        return None
    return sessions


def _market(ticker: str) -> str:
    """KR for Korea Exchange tickers (.KS/.KQ); every other ticker is treated as US-listed."""
    return "KR" if ticker.upper().endswith((".KS", ".KQ")) else "US"


def _require_one_market(tickers: list[str]) -> None:
    """Stop when one selection run mixes Korean and US tickers.

    On one date index a US close dated D is only known after the Korean close of D (look-ahead), and
    the other market's extra dates change how many rows the lookback and SMA windows span.
    Pre-listing proxies are separate: they are aligned to earlier US closes."""
    markets: dict[str, list[str]] = {}
    for ticker in tickers:
        markets.setdefault(_market(ticker), []).append(ticker)
    if len(markets) > 1:
        detail = "; ".join(f"{market}: {', '.join(sorted(group)[:5])}{' ...' if len(group) > 5 else ''}" for market, group in sorted(markets.items()))
        raise SystemExit(
            f"한국(.KS/.KQ)과 그 외 시장 티커를 한 번에 쓸 수 없습니다 ({detail}). "
            "벤치마크·시장 필터·현금 ETF를 유니버스와 같은 시장으로 지정하세요(예: 미국 지수 필터는 360750.KS)"
        )


def _fixed_baselines(grid_returns: pd.DataFrame, period: pd.DatetimeIndex) -> tuple[pd.DataFrame, list[str]]:
    """Grid configs with returns on every out-of-sample day (missing is not 0%), and the ones left out."""
    fixed = grid_returns.reindex(period)
    incomplete = [str(c) for c in fixed.columns[fixed.isna().any()]]
    if len(incomplete) == len(fixed.columns):
        raise SystemExit(f"No grid config has returns on every out-of-sample day: {', '.join(incomplete)}")
    return fixed.drop(columns=incomplete), incomplete


def _benchmark_baseline(benchmark: pd.Series, period: pd.DatetimeIndex) -> pd.Series:
    returns = benchmark.reindex(period)
    if returns.isna().any():
        gap = returns.index[returns.isna()]
        raise SystemExit(f"The benchmark has no return on {len(gap)} out-of-sample day(s), first {gap[0].date()}; check its data")
    return returns


def _require_tickers(prices: pd.DataFrame, required: dict[str, str | None]) -> None:
    """Stop when a benchmark, market filter or cash ETF the run depends on came back without data."""
    missing = [f"{role} {ticker}" for role, ticker in required.items() if ticker and (ticker not in prices.columns or prices[ticker].dropna().empty)]
    if missing:
        raise SystemExit(f"No price data for required ticker(s): {', '.join(missing)}. Check the ticker or rerun with --refresh")


def _required(args: argparse.Namespace, include_cash: bool, include_filters: bool = True) -> dict[str, str | None]:
    required: dict[str, str | None] = {"benchmark": args.benchmark}
    for number, ticker in enumerate((_risk_on_tickers(args) or ()) if include_filters else (), 1):
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


def _start_from_lookback_years(years: int, end: str | None = None) -> str:
    """Download start for rank/weekly: lookback years before --end (a past lookup) or before today."""
    base = pd.Timestamp(end).date() if end else date.today()
    return (base - timedelta(days=max(years, 1) * 366)).isoformat()


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
