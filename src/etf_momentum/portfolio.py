"""Combine several return streams ("sleeves") into one portfolio in a single base currency.

Sleeves can be buy-and-hold ETFs or strategy equity curves (e.g. walk-forward out-of-sample
returns). The portfolio is set to target weights at the first date and at the last trading day
of each month, and drifts with prices in between. Exposure above 1x borrows at `borrow_rate`;
exposure below 1x leaves the rest in cash earning `cash_returns`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PortfolioConfig:
    weights: Mapping[str, float]
    leverage: float = 1.0
    borrow_rate: float = 0.07  # annual rate paid on the borrowed amount, accrued by calendar days
    rebalance_cost_bps: float = 0.0  # one-way cost on the notional traded between sleeves


def to_base_currency(returns: pd.Series, fx: pd.Series | None) -> pd.Series:
    """Convert daily returns using `fx` quoted as base-currency units per sleeve-currency unit."""
    if fx is None:
        return returns
    fx = fx.reindex(returns.index.union(fx.index)).ffill().reindex(returns.index)
    return ((1 + returns) * (fx / fx.shift(1)) - 1).fillna(returns)


def align_sleeves(sleeves: Mapping[str, pd.Series]) -> pd.DataFrame:
    """Put sleeves on the union calendar; a sleeve's non-trading day is a zero-return day.

    Rows before a sleeve's first observation stay NaN so the portfolio can start once all exist.
    """
    frame = pd.DataFrame(dict(sleeves)).sort_index()
    started = frame.notna().cummax()
    return frame.where(~started, frame.fillna(0.0))


def run_portfolio(
    returns: pd.DataFrame,
    config: PortfolioConfig,
    cash_returns: pd.Series | None = None,
) -> tuple[pd.Series, pd.DataFrame]:
    """Return (portfolio value series starting at 1.0, one row per rebalance)."""
    if abs(sum(config.weights.values()) - 1.0) > 1e-9:
        raise ValueError(f"Sleeve weights must sum to 1, got {sum(config.weights.values()):.4f}")
    missing = set(config.weights) - set(returns.columns)
    if missing:
        raise ValueError(f"No returns for sleeves: {', '.join(sorted(missing))}")

    names = list(config.weights)
    data = returns[names].dropna()  # start once every sleeve has data
    if data.empty:
        raise ValueError("Sleeves have no overlapping dates")
    cash = (cash_returns.reindex(data.index).fillna(0.0) if cash_returns is not None else pd.Series(0.0, index=data.index))
    target = np.array([config.weights[name] for name in names])
    rebalance_days = set(data.index.to_series().groupby(data.index.to_period("M")).max()) | {data.index[0]}
    cost_rate = config.rebalance_cost_bps / 10_000.0

    holdings = np.zeros(len(names))
    reserve = 0.0  # cash (>0) or debt (<0)
    values: list[float] = []
    history: list[dict[str, object]] = []
    previous_date = None
    for date, row in zip(data.index, data.to_numpy()):
        if previous_date is not None:
            holdings = holdings * (1 + row)
            if reserve < 0:
                reserve *= 1 + config.borrow_rate * (date - previous_date).days / 365.0
            else:
                reserve *= 1 + cash.loc[date]
        value = float(holdings.sum() + reserve)
        if date in rebalance_days and date != data.index[-1]:
            value = value if previous_date is not None else 1.0
            new_holdings = value * config.leverage * target
            cost = float(np.abs(new_holdings - holdings).sum()) * cost_rate
            value -= cost
            holdings = value * config.leverage * target
            reserve = value * (1 - config.leverage)
            history.append({"date": date, "value": value, "cost": cost / (value + cost)})
        values.append(value)
        previous_date = date
    return pd.Series(values, index=data.index, name="portfolio"), pd.DataFrame(history)
