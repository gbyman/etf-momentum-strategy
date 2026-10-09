"""Walk-forward parameter selection.

Each column of ``returns`` is the daily return stream of one parameter set, produced by a
backtest whose signals only use data up to each day. For every test window we pick the column
with the best score over the preceding training window and splice its returns for the test
window. Switching costs between parameter sets at window boundaries are not modelled.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


TRADING_DAYS_PER_YEAR = 252
METRICS = ("calmar", "cagr", "sharpe")


def walk_forward(
    returns: pd.DataFrame,
    train_years: int = 5,
    test_months: int = 12,
    metric: str = "calmar",
    anchored: bool = False,
    min_coverage: float = 0.95,
    risk_free: pd.Series | None = None,
) -> tuple[pd.Series, pd.DataFrame]:
    """Return spliced out-of-sample daily returns and one row per test window.

    `risk_free` (daily returns, e.g. the cash ETF) makes the "sharpe" metric use excess returns,
    the same definition summarize_performance reports."""
    if train_years < 1 or test_months < 1:
        raise ValueError(f"train_years and test_months must be positive, got {train_years} and {test_months}")
    if metric not in METRICS:
        raise ValueError(f"Unsupported metric: {metric}; choose from {', '.join(METRICS)}")
    returns = returns.sort_index()
    first_date = returns.index[0]
    last_date = returns.index[-1]

    oos_parts: list[pd.Series] = []
    rows: list[dict[str, object]] = []
    test_start = first_date + pd.DateOffset(years=train_years)
    while test_start <= last_date:
        test_end_exclusive = test_start + pd.DateOffset(months=test_months)
        train_start = first_date if anchored else max(first_date, test_start - pd.DateOffset(years=train_years))
        train = returns.loc[(returns.index >= train_start) & (returns.index < test_start)]
        test = returns.loc[(returns.index >= test_start) & (returns.index < test_end_exclusive)]
        test_start = test_end_exclusive
        if train.empty or test.empty:
            continue

        covered = train.columns[train.notna().mean() >= min_coverage]
        scores = pd.Series({column: score(train[column].dropna(), metric, risk_free=risk_free) for column in covered}, dtype=float)
        scores = scores.dropna()  # +inf (gains with no drawdown/volatility) stays selectable
        if scores.empty:
            continue
        # Ties (e.g. several +inf scores) go to the higher training CAGR.
        cagrs = pd.Series({column: score(train[column].dropna(), "cagr") for column in scores.index})
        selected = pd.DataFrame({"score": scores, "cagr": cagrs}).sort_values(["score", "cagr"], ascending=False).index[0]
        test_returns = test[selected]
        if test_returns.isna().any():
            # Missing is not a 0% return; picking another config here would use test-period knowledge.
            gap = test_returns[test_returns.isna()].index
            raise ValueError(
                f"Selected config {selected} has {len(gap)} missing test returns "
                f"({gap[0].date()} ~ {gap[-1].date()}); check the input data"
            )
        oos_parts.append(test_returns)
        rows.append(
            {
                "train_start": train.index[0],
                "train_end": train.index[-1],
                "test_start": test.index[0],
                "test_end": test.index[-1],
                "selected": selected,
                "train_score": float(scores[selected]),
                "test_return": float((1 + test_returns).prod() - 1),
                "candidates": len(scores),
            }
        )

    oos = pd.concat(oos_parts) if oos_parts else pd.Series(dtype=float)
    return oos.rename("walk_forward"), pd.DataFrame(rows)


def oos_equity(returns: pd.DataFrame, base_date: pd.Timestamp) -> pd.DataFrame:
    """Growth curves of daily returns, with a 1.0 row at `base_date` (the close before the first
    out-of-sample day) so the first out-of-sample return is part of every metric computed from it."""
    equity = (1 + returns).cumprod()
    base = pd.DataFrame(1.0, index=pd.DatetimeIndex([base_date]), columns=returns.columns)
    return pd.concat([base, equity])


def score(daily_returns: pd.Series, metric: str, risk_free: pd.Series | None = None) -> float:
    """Training score. With zero drawdown (calmar) or zero volatility (sharpe) the ratio is
    +inf for gains and 0 for a flat series, so risk-free winners are preferred, never dropped."""
    if daily_returns.empty:
        return np.nan
    years = len(daily_returns) / TRADING_DAYS_PER_YEAR
    growth = float((1 + daily_returns).prod())
    cagr = growth ** (1 / years) - 1 if growth > 0 else -1.0
    if metric == "cagr":
        return cagr
    if metric == "sharpe":
        excess = daily_returns - (risk_free.reindex(daily_returns.index).fillna(0.0) if risk_free is not None else 0.0)
        std = float(excess.std())
        mean = float(excess.mean())
        if std > 1e-12:
            return mean / std * np.sqrt(TRADING_DAYS_PER_YEAR)
        return float(np.sign(mean) * np.inf) if mean else 0.0
    curve = (1 + daily_returns).cumprod()
    peak = curve.cummax().clip(lower=1.0)  # the starting value 1.0 counts as a peak
    max_drawdown = float((curve / peak - 1).min())
    if max_drawdown < 0:
        return cagr / abs(max_drawdown)
    return np.inf if cagr > 0 else 0.0
