from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from etf_momentum.signals import PRICE_MAX_STALE_DAYS, MomentumConfig, observed_freshness, rank_momentum


# buy_and_hold: target weights are set at each rebalance and then drift with prices until the next one.
# daily_rebalanced: target weights are restored every day (the original, simpler model).
ACCOUNTING_CHOICES = ("buy_and_hold", "daily_rebalanced")
# same_close: trade at the signal day's close. next_close: trade at the next trading day's close; the
# previous holdings keep earning that day (closer to "check the month-end signal, trade next session").
EXECUTION_CHOICES = ("same_close", "next_close")


class StaleDataError(ValueError):
    """A market filter without (recent) data: a data problem that must stop the run, not skip a config."""
EXECUTION_ASSUMPTION = (
    "Signals use closes up to and including the rebalance date; trades fill at that same close; "
    "returns accrue from the next trading day; transaction costs are deducted on the first holding day."
)


@dataclass(frozen=True)
class BacktestConfig:
    top_n: int = 3
    rebalance: str = "M"
    cash_ticker: str | None = "BIL"
    benchmark: str = "SPY"
    risk_on_benchmark: bool = False
    transaction_cost_bps: float = 0.0
    weighting: str = "equal"
    max_position_weight: float = 1.0
    momentum: MomentumConfig = MomentumConfig()
    candidate_tickers: tuple[str, ...] | None = None
    # ticker -> group; tickers without a group are their own group. None max_per_group means no limit.
    groups: Mapping[str, str] | None = None
    max_per_group: int | None = None
    accounting: str = "buy_and_hold"
    # Annualized volatility target for the invested sleeve. At each rebalance, exposure is scaled by
    # min(1, target / estimated portfolio volatility) and the rest goes to cash. None disables it.
    target_volatility: float | None = None
    # Maximum total weight in non-cash holdings (retirement accounts: 0.7). The rest goes to the cash
    # ETF, which must then be a safe asset such as a short-term bond ETF.
    max_risky_weight: float = 1.0
    # Tickers that are safe assets (universe risk_asset=0) besides the cash ETF; they don't count
    # toward max_risky_weight. The cap is applied at each rebalance; prices can drift above it in between.
    safe_tickers: tuple[str, ...] = ()
    # Market filter tickers for risk_on_benchmark (default: the benchmark). Risk-on when ANY of them
    # closes above its SMA, e.g. ("069500.KS", "360750.KS") stays invested if Korea or the US is trending up.
    risk_on_tickers: tuple[str, ...] | None = None
    execution: str = "same_close"


def run_backtest(
    prices: pd.DataFrame,
    config: BacktestConfig,
    traded_value: pd.DataFrame | None = None,
    observed: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    if config.top_n < 1:
        raise ValueError("top_n must be at least 1")
    if config.execution not in EXECUTION_CHOICES:
        raise ValueError(f"Unsupported execution: {config.execution}; choose from {', '.join(EXECUTION_CHOICES)}")
    if config.accounting not in ACCOUNTING_CHOICES:
        raise ValueError(f"Unsupported accounting: {config.accounting}; choose from {', '.join(ACCOUNTING_CHOICES)}")
    prices = prices.sort_index().dropna(axis=1, how="all").ffill()
    if config.max_risky_weight < 1.0 and not config.cash_ticker:
        raise ValueError("max_risky_weight < 1 needs a cash_ticker (safe asset) to hold the remainder")
    if config.cash_ticker and config.cash_ticker not in prices.columns:
        raise ValueError(f"Cash ticker {config.cash_ticker} has no price data; pass cash_ticker=None to hold uninvested cash")
    returns = prices.pct_change().fillna(0.0)
    rebalance_dates = _rebalance_dates(prices, config.rebalance)
    min_history = max(
        max(config.momentum.lookbacks) + config.momentum.skip_days + 2,
        config.momentum.sma_window,
        config.momentum.volatility_window + 1,  # volatility needs `window` daily returns
    )
    rebalance_dates = [date for date in rebalance_dates if prices.index.get_loc(date) >= min_history]
    if len(rebalance_dates) < 2:
        raise ValueError("Not enough rebalance dates after warm-up period")

    since_observed = _rows_since_observation(observed, prices.index) if observed is not None else None
    strategy_returns = pd.Series(0.0, index=prices.index, name="strategy_return")
    positions: list[dict[str, object]] = []
    previous_weights = pd.Series(0.0, index=prices.columns)

    for idx, rebalance_date in enumerate(rebalance_dates[:-1]):
        next_rebalance_date = rebalance_dates[idx + 1]
        # Holdings set at the trade close earn from the next day until the next trade close (inclusive).
        lag = 1 if config.execution == "next_close" else 0
        last_pos = len(prices.index) - 1
        trade_pos = prices.index.get_loc(rebalance_date) + lag
        start_pos = trade_pos + 1
        end_pos = min(prices.index.get_loc(next_rebalance_date) + lag, last_pos)
        if start_pos > end_pos:
            continue
        holding_dates = prices.index[start_pos : end_pos + 1]

        ranking = rank_momentum(
            prices,
            rebalance_date,
            benchmark=config.benchmark,
            config=config.momentum,
            candidate_tickers=config.candidate_tickers,
            traded_value=traded_value,
            observed=observed,
        )
        weights = _select_weights(ranking, prices, rebalance_date, config, observed)
        weights, vol_scale, est_vol = _apply_volatility_target(weights, prices, rebalance_date, config)
        weights = _apply_risky_cap(weights, config)
        if since_observed is not None:
            _check_held_observations(since_observed, weights, holding_dates)
        traded_notional = float((weights - previous_weights).abs().sum())
        turnover = traded_notional / 2.0
        cost = traded_notional * (config.transaction_cost_bps / 10_000.0)

        period_returns, end_weights = _holding_period(returns.loc[holding_dates], weights, config.accounting)
        if not period_returns.empty and cost:
            period_returns.iloc[0] -= cost
        strategy_returns.loc[holding_dates] = period_returns

        selected = weights[weights > 0].index.tolist()
        positions.append(
            {
                "rebalance_date": rebalance_date,
                "trade_date": prices.index[trade_pos],
                "hold_until": prices.index[end_pos],
                "tickers": ",".join(selected),
                "weights": ",".join(f"{ticker}:{weights[ticker]:.4f}" for ticker in selected),
                "turnover": turnover,
                "traded_notional": traded_notional,
                "transaction_cost": cost,
                "est_portfolio_vol": est_vol,
                "vol_scale": vol_scale,
                "eligible_count": int(ranking["eligible"].sum()) if not ranking.empty else 0,
                "liquidity_excluded": _liquidity_excluded(ranking),
                "held_avg_traded_value": _held_traded_value(ranking, selected),
            }
        )
        # Next rebalance trades from where the weights drifted to, not from the old targets.
        previous_weights = end_weights

    benchmark_return = returns[config.benchmark] if config.benchmark in returns.columns else pd.Series(0.0, index=returns.index)
    equity = pd.DataFrame(
        {
            "strategy": (1 + strategy_returns).cumprod(),
            "benchmark": (1 + benchmark_return).cumprod(),
        }
    )
    # Measure strategy and benchmark from the first rebalance (positions are set at its close), both rebased to 1.0.
    first_rebalance = positions[0]["rebalance_date"] if positions else equity.index[0]
    equity = equity.loc[first_rebalance:]
    equity = equity / equity.iloc[0]
    risk_free = returns[config.cash_ticker] if config.cash_ticker else None
    metrics = summarize_performance(equity, risk_free=risk_free)
    metrics.update(_trading_metrics(pd.DataFrame(positions), equity))
    return equity, pd.DataFrame(positions), metrics


def _apply_volatility_target(
    weights: pd.Series, prices: pd.DataFrame, rebalance_date: pd.Timestamp, config: BacktestConfig
) -> tuple[pd.Series, float, float]:
    """Scale the non-cash holdings toward the volatility target; returns (weights, scale, estimated vol).

    The estimate is the annualized volatility of the current target weights applied to the last
    `momentum.volatility_window` daily returns up to the rebalance date. No leverage: scale <= 1.
    """
    risky = weights[(weights > 0) & (weights.index != config.cash_ticker)]
    if risky.empty:
        return weights, 1.0, np.nan
    recent = prices[risky.index].loc[:rebalance_date].pct_change().tail(config.momentum.volatility_window)
    est_vol = float((recent.fillna(0.0) @ risky).std() * np.sqrt(252))
    if config.target_volatility is None or not np.isfinite(est_vol) or est_vol <= 0:
        return weights, 1.0, est_vol
    scale = min(1.0, config.target_volatility / est_vol)
    if scale < 1.0:
        weights = weights.copy()
        weights[risky.index] = risky * scale
        weights = _cash_weights(weights, config, float(risky.sum()) * (1.0 - scale))
    return weights, scale, est_vol


def _above_sma(history: pd.Series, window: int) -> bool:
    return bool(history.iloc[-1] > history.rolling(window).mean().iloc[-1])


def _apply_risky_cap(weights: pd.Series, config: BacktestConfig) -> pd.Series:
    safe = set(config.safe_tickers) | {config.cash_ticker}
    risky = weights[(weights > 0) & ~weights.index.isin(list(safe))]
    total = float(risky.sum())
    if total <= config.max_risky_weight + 1e-12:
        return weights
    weights = weights.copy()
    weights[risky.index] = risky * (config.max_risky_weight / total)
    return _cash_weights(weights, config, total - config.max_risky_weight)


def _liquidity_excluded(ranking: pd.DataFrame) -> str:
    """Tickers that passed every other eligibility check but failed the liquidity filter."""
    if ranking.empty:
        return ""
    other_checks = ranking["positive_momentum"] & ranking["above_sma"] & ranking["relative_ok"] & ranking["volatility_ok"]
    return ",".join(ranking.loc[other_checks.astype(bool) & ~ranking["liquid"].astype(bool), "ticker"])


def _held_traded_value(ranking: pd.DataFrame, held: list[str]) -> str:
    if ranking.empty:
        return ""
    values = ranking.set_index("ticker")["avg_traded_value"]
    return ",".join(f"{ticker}:{values[ticker]:.0f}" for ticker in held if ticker in values.index and np.isfinite(values[ticker]))


def _holding_period(returns: pd.DataFrame, weights: pd.Series, accounting: str) -> tuple[pd.Series, pd.Series]:
    """Daily portfolio returns over one holding period and the weights at its end."""
    if accounting == "daily_rebalanced":
        return returns.mul(weights, axis=1).sum(axis=1), weights
    held = weights[weights > 0]
    growth = (1 + returns[held.index]).cumprod()
    uninvested = 1.0 - float(held.sum())
    value = growth.mul(held, axis=1).sum(axis=1) + uninvested
    period_returns = value / value.shift(1).fillna(1.0) - 1
    end_weights = (held * growth.iloc[-1] / value.iloc[-1]).reindex(weights.index, fill_value=0.0)
    return period_returns, end_weights


def summarize_performance(equity: pd.DataFrame, risk_free: pd.Series | None = None) -> dict[str, float]:
    """Per-curve performance metrics.

    `<curve>_sharpe` is the annualized mean daily return in excess of `risk_free` (daily returns, e.g. the
    cash ETF; 0 when omitted) divided by its standard deviation. `<curve>_sharpe_like` is the older
    CAGR / volatility ratio, kept for comparison with earlier results. `excess_cagr` is strategy minus
    benchmark CAGR when both curves are present.
    """
    out: dict[str, float] = {}
    for column in equity.columns:
        curve = equity[column].dropna()
        if curve.empty:
            continue
        daily_returns = curve.pct_change().dropna()
        years = max((curve.index[-1] - curve.index[0]).days / 365.25, 1e-9)
        growth = float(curve.iloc[-1] / curve.iloc[0])
        cagr = float(growth ** (1.0 / years) - 1.0)
        volatility = float(daily_returns.std() * np.sqrt(252)) if not daily_returns.empty else 0.0
        sharpe_like = float(cagr / volatility) if volatility > 0 else np.nan
        rf = risk_free.reindex(daily_returns.index).fillna(0.0) if risk_free is not None else 0.0
        excess = daily_returns - rf
        excess_std = float(excess.std()) if len(excess) > 1 else 0.0
        sharpe = float(excess.mean() / excess_std * np.sqrt(252)) if excess_std > 0 else np.nan
        drawdown = curve / curve.cummax() - 1.0
        max_drawdown = float(drawdown.min())
        calmar = float(cagr / abs(max_drawdown)) if max_drawdown < 0 else np.nan
        out[f"{column}_cagr"] = cagr
        out[f"{column}_volatility"] = volatility
        out[f"{column}_sharpe"] = sharpe
        out[f"{column}_sharpe_like"] = sharpe_like
        out[f"{column}_max_drawdown"] = max_drawdown
        out[f"{column}_calmar"] = calmar
        out[f"{column}_ending_value"] = growth
    if "strategy_cagr" in out and "benchmark_cagr" in out:
        out["excess_cagr"] = out["strategy_cagr"] - out["benchmark_cagr"]
    return out


def _trading_metrics(positions: pd.DataFrame, equity: pd.DataFrame) -> dict[str, float]:
    """Costs are fractions of portfolio value at each rebalance, summed (not compounded)."""
    if positions.empty or equity.empty:
        return {}
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    total_cost = float(positions["transaction_cost"].sum())
    return {
        "total_transaction_cost": total_cost,
        "annual_transaction_cost": total_cost / years,
        "annual_turnover": float(positions["turnover"].sum()) / years,
    }


@dataclass(frozen=True)
class Selection:
    """Which ETFs the strategy holds after a rebalance (weights aside)."""

    risk_on: bool
    tickers: list[str] = field(default_factory=list)
    group_skipped: list[str] = field(default_factory=list)  # eligible, but their group was already full
    empty_slots: int = 0  # of top_n, left in cash


def select_holdings(
    ranking: pd.DataFrame,
    prices: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    config: BacktestConfig,
    observed: pd.DataFrame | None = None,
) -> Selection:
    """Market filter, then eligible tickers in rank order under the group limit, up to top_n.

    The backtest and the rank command both use this, so their picks follow the same rules.
    """
    if config.top_n < 1:
        raise ValueError(f"top_n must be at least 1, got {config.top_n}")
    risk_on = True
    if config.risk_on_benchmark:
        filters = config.risk_on_tickers or (config.benchmark,)
        missing = [t for t in filters if t not in prices.columns or prices[t].loc[:rebalance_date].dropna().empty]
        if missing:
            # Never treat an unevaluated market filter as "risk on".
            raise StaleDataError(f"Market filter ticker(s) without price data: {', '.join(missing)}")
        if observed is not None:
            history = prices.loc[:rebalance_date].index
            stale = [
                t for t in filters
                if not observed_freshness(observed[t] if t in observed.columns else None, history)[1]
            ]
            if stale:
                raise StaleDataError(
                    f"Market filter ticker(s) without a recent observation at {pd.Timestamp(rebalance_date).date()}: "
                    f"{', '.join(stale)}"
                )
        risk_on = any(_above_sma(prices.loc[:rebalance_date, t], config.momentum.sma_window) for t in filters)
    if not risk_on or ranking.empty:
        return Selection(risk_on=risk_on, empty_slots=config.top_n)
    picked, skipped = _pick_tickers(ranking, config)
    return Selection(risk_on=True, tickers=picked, group_skipped=skipped, empty_slots=config.top_n - len(picked))


def previous_month_end(
    dates: pd.DatetimeIndex, today: pd.Timestamp | None = None, sessions: pd.DatetimeIndex | None = None
) -> pd.Timestamp:
    """Last trading day of the month before `today`'s month, checked to be really in the data.

    With exchange `sessions`, that day is the month's last session and it must be in `dates`.
    Without them, the month counts as complete when its last date in `dates` is the month's last
    weekday or the data continues into the next month. A missing month never falls back to an older one.
    """
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    dates = pd.DatetimeIndex(dates).sort_values()
    month = today.to_period("M") - 1
    if sessions is not None:
        in_month_sessions = pd.DatetimeIndex(sessions)[pd.DatetimeIndex(sessions).to_period("M") == month]
        if len(in_month_sessions):
            last_session = in_month_sessions[-1].normalize()
            if last_session not in dates.normalize():
                raise ValueError(f"{month} last trading day {last_session.date()} is missing from the data")
            return last_session
    in_month = dates[dates.to_period("M") == month]
    if in_month.empty:
        raise ValueError(f"No price data for {month} (the month before {today:%Y-%m})")
    last = in_month[-1]
    last_weekday = pd.bdate_range(month.start_time, month.end_time)[-1]
    complete = last.normalize() >= last_weekday.normalize() or bool((dates > month.end_time).any())
    if not complete:
        raise ValueError(f"{month} month-end data is incomplete: last observation {last.date()}")
    return last


def _select_weights(
    ranking: pd.DataFrame,
    prices: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    config: BacktestConfig,
    observed: pd.DataFrame | None = None,
) -> pd.Series:
    weights = pd.Series(0.0, index=prices.columns)
    selection = select_holdings(ranking, prices, rebalance_date, config, observed)
    selected = selection.tickers
    if not selected:
        return _cash_weights(weights, config)

    # Each of the top_n slots is worth 1/top_n; slots without an eligible ETF stay in cash.
    invested = len(selected) / config.top_n
    selected_weights = _selected_weights(ranking, selected, config) * invested
    selected_weights = _cap_weights(selected_weights, config.max_position_weight)
    for ticker, weight in selected_weights.items():
        weights[ticker] = weight
    return _cash_weights(weights, config, 1.0 - float(selected_weights.sum()))


def _pick_tickers(ranking: pd.DataFrame, config: BacktestConfig) -> tuple[list[str], list[str]]:
    """Take eligible tickers in rank order, skipping any whose group already holds max_per_group names.

    Returns (picked, skipped for the group limit before the slots filled up)."""
    groups = config.groups or {}
    picked: list[str] = []
    skipped: list[str] = []
    group_counts: dict[str, int] = {}
    for ticker in ranking.loc[ranking["eligible"].astype(bool), "ticker"]:
        group = groups.get(ticker) or ticker
        if config.max_per_group is not None and group_counts.get(group, 0) >= config.max_per_group:
            skipped.append(ticker)
            continue
        picked.append(ticker)
        group_counts[group] = group_counts.get(group, 0) + 1
        if len(picked) == config.top_n:
            break
    return picked, skipped


def _selected_weights(ranking: pd.DataFrame, selected: list[str], config: BacktestConfig) -> pd.Series:
    selected_ranking = ranking.set_index("ticker").loc[selected]
    method = config.weighting.lower()
    if method == "equal":
        raw = pd.Series(1.0, index=selected)
    elif method == "inverse_vol":
        raw = 1.0 / selected_ranking["realized_volatility"].clip(lower=config.momentum.volatility_floor)
    elif method == "score":
        raw = selected_ranking["weighted_momentum"].clip(lower=0.0)
    elif method == "risk_adjusted_score":
        raw = selected_ranking["risk_adjusted_score"].clip(lower=0.0)
    else:
        raise ValueError(f"Unsupported weighting method: {config.weighting}")

    raw = raw.replace([np.inf, -np.inf], np.nan).dropna()
    if raw.empty or raw.sum() <= 0:
        raw = pd.Series(1.0, index=selected)
    return (raw / raw.sum()).reindex(selected, fill_value=0.0)


def _cap_weights(weights: pd.Series, cap: float) -> pd.Series:
    """Cap each weight, redistributing the excess to uncapped names; what cannot be placed is left for cash."""
    if cap <= 0 or cap >= 1:
        return weights
    budget = float(weights.sum())
    capped = weights.copy()
    for _ in range(len(capped) + 1):
        over_cap = capped > cap + 1e-12
        if not over_cap.any():
            break
        capped[over_cap] = cap
        at_cap = capped >= cap - 1e-12
        free = ~at_cap & (capped > 0)
        room = budget - float(capped[at_cap].sum())
        if not free.any() or room <= 0:
            break
        capped[free] = capped[free] / capped[free].sum() * room
    return capped


def _rows_since_observation(observed: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Rows of `index` since each ticker's last real close (NaN before its first one)."""
    seen = observed.reindex(index=index).fillna(False).astype(bool).to_numpy()
    positions = np.arange(len(index))[:, None]
    last = pd.DataFrame(np.where(seen, positions, np.nan)).ffill().to_numpy()
    return pd.DataFrame(positions - last, index=index, columns=observed.columns)


def _check_held_observations(since_observed: pd.DataFrame, weights: pd.Series, holding_dates: pd.DatetimeIndex) -> None:
    """A held asset whose closes stop mid-holding would earn a made-up 0%; stop instead.

    Before an asset's first close (e.g. the cash ETF before listing) it is uninvested cash, as before."""
    held = [t for t in weights.index[weights > 0] if t in since_observed.columns]
    gaps = since_observed.loc[holding_dates, held]
    stale = gaps.columns[(gaps > PRICE_MAX_STALE_DAYS).any()]
    if len(stale):
        raise StaleDataError(
            f"Held ticker(s) without a close for more than {PRICE_MAX_STALE_DAYS} trading days between "
            f"{holding_dates[0].date()} and {holding_dates[-1].date()}: {', '.join(stale)}"
        )


def _cash_weights(weights: pd.Series, config: BacktestConfig, amount: float = 1.0) -> pd.Series:
    if config.cash_ticker and amount > 1e-12:
        weights[config.cash_ticker] += amount
    return weights


def _rebalance_dates(prices: pd.DataFrame, frequency: str) -> list[pd.Timestamp]:
    frequency = _normalize_frequency(frequency)
    grouped = prices.resample(frequency).last().dropna(how="all")
    return [prices.index[prices.index <= date][-1] for date in grouped.index if len(prices.index[prices.index <= date]) > 0]


def _normalize_frequency(frequency: str) -> str:
    aliases = {
        "M": "ME",
        "Q": "QE",
        "Y": "YE",
        "A": "YE",
    }
    return aliases.get(frequency.upper(), frequency)
