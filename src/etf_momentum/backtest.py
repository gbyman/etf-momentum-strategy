from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from etf_momentum.signals import MomentumConfig, rank_momentum


# buy_and_hold: target weights are set at each rebalance and then drift with prices until the next one.
# daily_rebalanced: target weights are restored every day (the original, simpler model).
ACCOUNTING_CHOICES = ("buy_and_hold", "daily_rebalanced")
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


def run_backtest(
    prices: pd.DataFrame,
    config: BacktestConfig,
    traded_value: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    if config.top_n < 1:
        raise ValueError("top_n must be at least 1")
    if config.accounting not in ACCOUNTING_CHOICES:
        raise ValueError(f"Unsupported accounting: {config.accounting}; choose from {', '.join(ACCOUNTING_CHOICES)}")
    prices = prices.sort_index().dropna(axis=1, how="all").ffill()
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

    strategy_returns = pd.Series(0.0, index=prices.index, name="strategy_return")
    positions: list[dict[str, object]] = []
    previous_weights = pd.Series(0.0, index=prices.columns)

    for idx, rebalance_date in enumerate(rebalance_dates[:-1]):
        next_rebalance_date = rebalance_dates[idx + 1]
        start_pos = prices.index.get_loc(rebalance_date) + 1
        end_pos = prices.index.get_loc(next_rebalance_date)
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
        )
        weights = _select_weights(ranking, prices, rebalance_date, config)
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
                "hold_until": next_rebalance_date,
                "tickers": ",".join(selected),
                "weights": ",".join(f"{ticker}:{weights[ticker]:.4f}" for ticker in selected),
                "turnover": turnover,
                "traded_notional": traded_notional,
                "transaction_cost": cost,
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


def _select_weights(
    ranking: pd.DataFrame,
    prices: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    config: BacktestConfig,
) -> pd.Series:
    weights = pd.Series(0.0, index=prices.columns)
    risk_on = True
    if config.risk_on_benchmark and config.benchmark in prices.columns:
        benchmark_history = prices.loc[:rebalance_date, config.benchmark]
        benchmark_sma = benchmark_history.rolling(config.momentum.sma_window).mean().iloc[-1]
        risk_on = bool(benchmark_history.iloc[-1] > benchmark_sma)

    if not risk_on:
        return _cash_weights(weights, config)

    selected = _pick_tickers(ranking, config) if not ranking.empty else []
    if not selected:
        return _cash_weights(weights, config)

    # Each of the top_n slots is worth 1/top_n; slots without an eligible ETF stay in cash.
    invested = len(selected) / config.top_n
    selected_weights = _selected_weights(ranking, selected, config) * invested
    selected_weights = _cap_weights(selected_weights, config.max_position_weight)
    for ticker, weight in selected_weights.items():
        weights[ticker] = weight
    return _cash_weights(weights, config, 1.0 - float(selected_weights.sum()))


def _pick_tickers(ranking: pd.DataFrame, config: BacktestConfig) -> list[str]:
    """Take eligible tickers in rank order, skipping any whose group already holds max_per_group names."""
    groups = config.groups or {}
    picked: list[str] = []
    group_counts: dict[str, int] = {}
    for ticker in ranking.loc[ranking["eligible"], "ticker"]:
        group = groups.get(ticker) or ticker
        if config.max_per_group is not None and group_counts.get(group, 0) >= config.max_per_group:
            continue
        picked.append(ticker)
        group_counts[group] = group_counts.get(group, 0) + 1
        if len(picked) == config.top_n:
            break
    return picked


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
