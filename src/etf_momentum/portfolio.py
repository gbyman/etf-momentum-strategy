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

    Rows before a sleeve's first or after its last observation stay NaN, so the portfolio only runs
    while every sleeve has data (an ended strategy curve is not extended with zero returns).
    """
    frame = pd.DataFrame(dict(sleeves)).sort_index()
    active = frame.notna().cummax() & frame.notna()[::-1].cummax()[::-1]
    return frame.where(~active, frame.fillna(0.0))


TIMINGS = ("same_day", "previous_close")


def sleeve_returns_on_calendar(
    curve: pd.Series,
    calendar: pd.DatetimeIndex,
    timing: str,
    fx: pd.Series | None = None,
    max_stale_days: int = 5,
) -> pd.Series:
    """Daily returns of a value curve on a fixed evaluation calendar (e.g. Korean trading days).

    same_day: the curve trades on this calendar. Every calendar date between its first and last
      value must be present and non-NaN; a gap is an error, because a missing observation and an
      exchange holiday can't be told apart.
    previous_close: the curve trades elsewhere (e.g. a US ETF held through a KR-listed fund). Each
      calendar date uses the latest curve value and FX dated strictly before it, which is what is
      known at that date's close. Dates more than `max_stale_days` after the last observation are NaN.

    `fx` converts to the base currency (base units per curve unit). The first valued date is a 0.0
    base row; dates before it or after the sleeve ends are NaN.
    """
    if timing not in TIMINGS:
        raise ValueError(f"Unsupported timing: {timing}; choose from {', '.join(TIMINGS)}")
    calendar = pd.DatetimeIndex(calendar).sort_values()
    if timing == "same_day":
        values = _same_day_values(curve, calendar)
        if fx is not None:
            values = values * _asof_values(fx, calendar, strictly_before=False, max_stale_days=max_stale_days)
    else:
        values = _asof_values(curve, calendar, strictly_before=True, max_stale_days=max_stale_days)
        if fx is not None:
            values = values * _asof_values(fx, calendar, strictly_before=True, max_stale_days=max_stale_days)
    _reject_internal_gaps(values, curve.name, max_stale_days)
    returns = values.pct_change(fill_method=None)
    first = values.first_valid_index()
    if first is not None:
        returns.loc[first] = 0.0
    return returns.rename(curve.name)


def _reject_internal_gaps(values: pd.Series, name: object, max_stale_days: int) -> None:
    """A stale stretch followed by new data is an error: dropping it would lose the return earned
    across the gap. Before the first and after the last value is warm-up or the sleeve's end."""
    valid = values.notna()
    if not valid.any():
        return
    inside = values.loc[values.first_valid_index() : values.last_valid_index()]
    if inside.isna().any():
        gap = inside[inside.isna()].index
        raise ValueError(
            f"Sleeve {name!r} has a data gap from {gap[0].date()} to {gap[-1].date()} "
            f"(no new value for more than {max_stale_days} days, then data resumes)"
        )


def _same_day_values(curve: pd.Series, calendar: pd.DatetimeIndex) -> pd.Series:
    valid = curve.dropna()
    if valid.empty:
        raise ValueError(f"Sleeve {curve.name!r} has no values")
    inside = curve.loc[valid.index[0] : valid.index[-1]]
    if inside.isna().any():
        raise ValueError(f"Sleeve {curve.name!r} has NaN values inside its range, first at {inside[inside.isna()].index[0].date()}")
    span = calendar[(calendar >= valid.index[0]) & (calendar <= valid.index[-1])]
    missing = span.difference(valid.index)
    if len(missing):
        raise ValueError(
            f"Sleeve {curve.name!r} is missing {len(missing)} calendar date(s), first {missing[0].date()}; "
            "check the input file or use a calendar the sleeve trades on"
        )
    return valid.reindex(calendar)


def _asof_values(series: pd.Series, calendar: pd.DatetimeIndex, strictly_before: bool, max_stale_days: int) -> pd.Series:
    known = series.dropna().sort_index()
    lookup = calendar - pd.Timedelta(days=1) if strictly_before else calendar
    positions = known.index.searchsorted(lookup, side="right") - 1
    values = np.where(positions >= 0, known.to_numpy()[np.clip(positions, 0, None)], np.nan)
    observed = known.index[np.clip(positions, 0, None)]
    stale = (calendar - observed).days > max_stale_days + (1 if strictly_before else 0)
    values = np.where((positions >= 0) & ~stale, values, np.nan)
    return pd.Series(values, index=calendar, dtype=float)


def curve_to_returns(curve: pd.Series) -> pd.Series:
    """Daily returns of a value curve, keeping its first date as a 0.0 row.

    That row is the base date: run_portfolio allocates at its close, so the curve's first real
    return (the next row) is earned rather than skipped.
    """
    curve = curve.dropna()
    return curve.pct_change().fillna(0.0)


def annualized_cost(history: pd.DataFrame, window: pd.DatetimeIndex) -> float:
    """Rebalance costs paid inside `window`, per calendar year of the window."""
    if history.empty or len(window) < 2:
        return 0.0
    inside = history[(history["date"] >= window[0]) & (history["date"] <= window[-1])]
    years = max((window[-1] - window[0]).days / 365.25, 1e-9)
    return float(inside["cost"].sum()) / years


def _validate_config(config: PortfolioConfig) -> None:
    """Long-only sleeve weights; finite leverage, borrowing rate and cost."""
    for name, weight in config.weights.items():
        if not np.isfinite(weight) or weight < 0:
            raise ValueError(f"Sleeve weight for {name} must be a finite number >= 0 (long only), got {weight}")
    if not np.isfinite(config.leverage) or config.leverage <= 0:
        raise ValueError(f"leverage must be a finite number > 0, got {config.leverage}")
    if not np.isfinite(config.borrow_rate) or config.borrow_rate < 0:
        raise ValueError(f"borrow_rate must be a finite number >= 0, got {config.borrow_rate}")
    if not np.isfinite(config.rebalance_cost_bps) or config.rebalance_cost_bps < 0:
        raise ValueError(f"rebalance_cost_bps must be a finite number >= 0, got {config.rebalance_cost_bps}")


def run_portfolio(
    returns: pd.DataFrame,
    config: PortfolioConfig,
    cash_returns: pd.Series | None = None,
) -> tuple[pd.Series, pd.DataFrame]:
    """Return (portfolio value series starting at 1.0, one row per rebalance)."""
    _validate_config(config)
    if abs(sum(config.weights.values()) - 1.0) > 1e-9:
        raise ValueError(f"Sleeve weights must sum to 1, got {sum(config.weights.values()):.4f}")
    names = [name for name, weight in config.weights.items() if weight > 0]  # a zero-weight sleeve is unused
    missing = set(names) - set(returns.columns)
    if missing:
        raise ValueError(f"No returns for sleeves: {', '.join(sorted(missing))}")

    data = returns[names].dropna()  # start once every sleeve has data
    if len(data) < 2:
        raise ValueError(f"Sleeves need at least two common dates to evaluate, got {len(data)}")
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
        # The first point is the 1.0 invested before the first purchase cost, so that cost shows up in the
        # first day's return and in every summary measured from the start.
        values.append(value if previous_date is not None else 1.0)
        previous_date = date
    return pd.Series(values, index=data.index, name="portfolio"), pd.DataFrame(history)
