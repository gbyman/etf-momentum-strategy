from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


TRADING_DAYS_PER_YEAR = 252
TRAILING_RETURN_WINDOWS = {
    "return_1w": 5,
    "return_1m": 21,
    "return_3m": 63,
    "return_6m": 126,
    "return_12m": 252,
}
RANK_BY_CHOICES = ("weighted_momentum", "risk_adjusted_score")
# Traded-value data whose last observation is more than this many business days before the
# rebalance date is treated as missing, so the ETF counts as illiquid.
LIQUIDITY_MAX_STALE_DAYS = 5
# A candidate whose price has not changed for more than this many trading days is treated as stale:
# forward-filled gaps (missing data, suspension, delisting) show up as exact repeats of the last close.
PRICE_MAX_STALE_DAYS = 5
RANKING_COLUMNS = [
    "as_of",
    "ticker",
    "price",
    *TRAILING_RETURN_WINDOWS,
    "return_ytd",
    "momentum_3m",
    "momentum_6m",
    "momentum_12m",
    "weighted_momentum",
    "trend_strength_vs_sma",
    "realized_volatility",
    "volatility_ok",
    "last_price_change",
    "last_observed",
    "price_fresh",
    "risk_adjusted_score",
    "relative_to_benchmark",
    "relative_ok",
    "above_sma",
    "positive_momentum",
    "avg_traded_value",
    "liquid",
    "eligible",
    "rank",
    "eligible_rank",
]


@dataclass(frozen=True)
class MomentumConfig:
    lookbacks: tuple[int, int, int] = (63, 126, 252)
    weights: tuple[float, float, float] = (0.3, 0.3, 0.4)
    skip_days: int = 21
    sma_window: int = 200
    volatility_window: int = 63
    min_weighted_momentum: float = 0.0
    min_trend_strength: float = 0.0
    min_relative_momentum: float | None = None
    rank_by: str = "weighted_momentum"
    # Minimum average daily traded value (price x volume, in the ETF's currency) over liquidity_window
    # days up to the rebalance date. None disables the filter.
    min_avg_traded_value: float | None = None
    liquidity_window: int = 63
    # Annualized volatility below this is treated as this value in risk_adjusted_score and inverse-vol
    # weights, so near-zero volatility (bond ETFs, float noise) cannot produce extreme scores or weights.
    volatility_floor: float = 0.05

    def normalized_weights(self) -> tuple[float, ...]:
        total = sum(self.weights)
        if total <= 0:
            raise ValueError("Momentum weights must sum to a positive number")
        return tuple(weight / total for weight in self.weights)


def rank_momentum(
    prices: pd.DataFrame,
    as_of: pd.Timestamp | None = None,
    benchmark: str | None = None,
    config: MomentumConfig | None = None,
    candidate_tickers: Iterable[str] | None = None,
    traded_value: pd.DataFrame | None = None,
    observed: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Rank candidate ETFs. The benchmark only feeds relative strength unless it is itself a candidate.

    `observed` (date x ticker, True where a real close exists) makes price freshness come from real
    observations; without it, a price unchanged for more than PRICE_MAX_STALE_DAYS days counts as stale."""
    config = config or MomentumConfig()
    if config.rank_by not in RANK_BY_CHOICES:
        raise ValueError(f"Unsupported rank_by: {config.rank_by}; choose from {', '.join(RANK_BY_CHOICES)}")
    if config.min_avg_traded_value is not None and traded_value is None:
        raise ValueError("min_avg_traded_value is set but no traded_value data was given")
    if prices.empty:
        raise ValueError("prices is empty")
    prices = prices.sort_index()
    as_of = _resolve_as_of(prices, as_of)
    cutoff = prices.index.get_loc(as_of)
    history = prices.iloc[: cutoff + 1]
    if len(history) < max(config.lookbacks) + config.skip_days + 2:
        raise ValueError("Not enough price history to compute momentum ranking")

    rows: list[dict[str, float | str | bool | pd.Timestamp]] = []
    weights = config.normalized_weights()
    current = history.iloc[-1]
    shifted = history.shift(config.skip_days)
    sma = history.rolling(config.sma_window).mean().iloc[-1]
    daily_returns = history.pct_change()
    vol = daily_returns.rolling(config.volatility_window).std().iloc[-1] * math.sqrt(TRADING_DAYS_PER_YEAR)

    benchmark_returns = _benchmark_returns(history, benchmark, config)
    avg_value = _average_traded_value(traded_value, as_of, config.liquidity_window)

    candidates = history.columns if candidate_tickers is None else [t for t in candidate_tickers if t in history.columns]
    for ticker in candidates:
        if not np.isfinite(current.get(ticker, np.nan)):
            continue

        lookback_returns: list[float] = []
        for lookback in config.lookbacks:
            past_index = -(lookback + config.skip_days + 1)
            past_price = history[ticker].iloc[past_index]
            anchor_price = shifted[ticker].iloc[-1]
            if not np.isfinite(past_price) or not np.isfinite(anchor_price) or past_price <= 0:
                lookback_returns.append(np.nan)
            else:
                lookback_returns.append(anchor_price / past_price - 1.0)

        if any(not np.isfinite(value) for value in lookback_returns):
            continue

        weighted_momentum = float(sum(weight * ret for weight, ret in zip(weights, lookback_returns)))
        trend_strength = float(current[ticker] / sma[ticker] - 1.0) if np.isfinite(sma[ticker]) and sma[ticker] > 0 else np.nan
        last_change, price_fresh = _price_freshness(history[ticker])
        last_observed = None
        if observed is not None:
            mask = observed[ticker] if ticker in observed.columns else None
            last_observed, price_fresh = observed_freshness(mask, history.index)
        realized_vol = float(vol[ticker]) if np.isfinite(vol[ticker]) else np.nan
        volatility_ok = bool(np.isfinite(realized_vol))
        risk_adjusted_score = (
            weighted_momentum / max(realized_vol, config.volatility_floor) if volatility_ok else np.nan
        )
        relative_to_benchmark = weighted_momentum - benchmark_returns if benchmark_returns is not None else np.nan
        relative_ok = (
            True
            if config.min_relative_momentum is None
            else np.isfinite(relative_to_benchmark) and relative_to_benchmark >= config.min_relative_momentum
        )
        above_sma = bool(np.isfinite(trend_strength) and trend_strength > config.min_trend_strength)
        positive_momentum = bool(weighted_momentum > config.min_weighted_momentum)
        ticker_value = float(avg_value.get(ticker, np.nan))
        liquid = config.min_avg_traded_value is None or bool(
            np.isfinite(ticker_value) and ticker_value >= config.min_avg_traded_value
        )

        rows.append(
            {
                "as_of": as_of,
                "ticker": ticker,
                "price": float(current[ticker]),
                **_trailing_returns(history[ticker]),
                "momentum_3m": lookback_returns[0],
                "momentum_6m": lookback_returns[1],
                "momentum_12m": lookback_returns[2],
                "weighted_momentum": weighted_momentum,
                "trend_strength_vs_sma": trend_strength,
                "realized_volatility": realized_vol,
                "volatility_ok": volatility_ok,
                "last_price_change": last_change,
                "last_observed": last_observed,
                "price_fresh": price_fresh,
                "risk_adjusted_score": risk_adjusted_score,
                "relative_to_benchmark": relative_to_benchmark,
                "relative_ok": bool(relative_ok),
                "above_sma": above_sma,
                "positive_momentum": positive_momentum,
                "avg_traded_value": ticker_value,
                "liquid": liquid,
                "eligible": bool(positive_momentum and above_sma and relative_ok and liquid and volatility_ok and price_fresh),
            }
        )

    ranking = pd.DataFrame(rows)
    if ranking.empty:
        # No candidate has enough history yet; keep the schema so callers can treat it as "nothing eligible".
        return pd.DataFrame(columns=RANKING_COLUMNS)
    score = ranking[config.rank_by]
    ranking["rank"] = score.rank(ascending=False, method="first", na_option="bottom").astype(int)
    ranking["eligible_rank"] = score.where(ranking["eligible"]).rank(ascending=False, method="first")
    return ranking.sort_values(["eligible", "rank"], ascending=[False, True]).reset_index(drop=True)


def _price_freshness(series: pd.Series) -> tuple[pd.Timestamp | None, bool]:
    """Date of the last close that differed from the previous one, and whether it is recent enough."""
    values = series.dropna()
    changed = values.diff().ne(0)  # the first observation counts as a change
    if not changed.any():
        return None, False
    position = int(np.flatnonzero(changed.to_numpy())[-1])
    unchanged_after = len(values) - 1 - position
    return values.index[position], unchanged_after <= PRICE_MAX_STALE_DAYS


def observed_freshness(mask: pd.Series | None, index: pd.DatetimeIndex) -> tuple[pd.Timestamp | None, bool]:
    """Last date in `index` with a real observation, and whether at most PRICE_MAX_STALE_DAYS rows
    of `index` follow it. A ticker with no observation table column is stale."""
    if mask is None:
        return None, False
    seen = mask.reindex(index, fill_value=False).to_numpy(dtype=bool)
    positions = np.flatnonzero(seen)
    if positions.size == 0:
        return None, False
    last = int(positions[-1])
    return index[last], len(index) - 1 - last <= PRICE_MAX_STALE_DAYS


def _average_traded_value(traded_value: pd.DataFrame | None, as_of: pd.Timestamp, window: int) -> pd.Series:
    """Mean daily traded value over the last `window` rows up to as_of.

    NaN (treated as illiquid) when fewer than two thirds of those days have data, or when the
    ticker's latest observation is more than LIQUIDITY_MAX_STALE_DAYS business days before as_of.
    """
    if traded_value is None or traded_value.empty:
        return pd.Series(dtype=float)
    history = traded_value.loc[:as_of]
    if history.empty:
        return pd.Series(np.nan, index=traded_value.columns)
    recent = history.tail(window)
    trailing_missing = history.notna().iloc[::-1].cumsum().eq(0).sum()
    days_after_data = int(np.busday_count(history.index[-1].date(), pd.Timestamp(as_of).date()))
    fresh = (trailing_missing + days_after_data) <= LIQUIDITY_MAX_STALE_DAYS
    return recent.mean().where((recent.notna().sum() >= max(1, window * 2 // 3)) & fresh)


def _benchmark_returns(history: pd.DataFrame, benchmark: str | None, config: MomentumConfig) -> float | None:
    if not benchmark or benchmark not in history.columns:
        return None
    shifted = history.shift(config.skip_days)
    weights = config.normalized_weights()
    values: list[float] = []
    for lookback in config.lookbacks:
        past_price = history[benchmark].iloc[-(lookback + config.skip_days + 1)]
        anchor_price = shifted[benchmark].iloc[-1]
        if not np.isfinite(past_price) or not np.isfinite(anchor_price) or past_price <= 0:
            return None
        values.append(anchor_price / past_price - 1.0)
    return float(sum(weight * ret for weight, ret in zip(weights, values)))


def _trailing_returns(series: pd.Series) -> dict[str, float]:
    series = series.dropna()
    returns = {name: _period_return(series, days) for name, days in TRAILING_RETURN_WINDOWS.items()}
    returns["return_ytd"] = _ytd_return(series)
    return returns


def _period_return(series: pd.Series, days: int) -> float:
    if len(series) <= days:
        return np.nan
    current_price = series.iloc[-1]
    past_price = series.iloc[-days - 1]
    if not np.isfinite(current_price) or not np.isfinite(past_price) or past_price <= 0:
        return np.nan
    return float(current_price / past_price - 1.0)


def _ytd_return(series: pd.Series) -> float:
    if series.empty:
        return np.nan
    current_price = series.iloc[-1]
    year_start = pd.Timestamp(year=series.index[-1].year, month=1, day=1)
    previous_year_dates = series.index[series.index < year_start]
    if len(previous_year_dates) > 0:
        anchor_price = series.loc[previous_year_dates[-1]]
    else:
        current_year = series[series.index >= year_start]
        if current_year.empty:
            return np.nan
        anchor_price = current_year.iloc[0]
    if not np.isfinite(current_price) or not np.isfinite(anchor_price) or anchor_price <= 0:
        return np.nan
    return float(current_price / anchor_price - 1.0)


def _resolve_as_of(prices: pd.DataFrame, as_of: pd.Timestamp | None) -> pd.Timestamp:
    if as_of is None:
        return prices.index[-1]
    as_of = pd.Timestamp(as_of)
    valid_dates = prices.index[prices.index <= as_of]
    if valid_dates.empty:
        raise ValueError(f"No price data available on or before {as_of.date()}")
    return valid_dates[-1]
