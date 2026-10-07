from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from etf_momentum.backtest import summarize_performance
from etf_momentum.walkforward import score, walk_forward


# 3-1. walk-forward Sharpe subtracts the same risk-free rate as the reported Sharpe
def alternating(mean: float, swing: float, index: pd.DatetimeIndex) -> pd.Series:
    return pd.Series(np.where(np.arange(index.size) % 2 == 0, mean + swing, mean - swing), index=index)


def test_training_sharpe_matches_reported_sharpe_definition() -> None:
    index = pd.bdate_range("2020-01-01", periods=500)
    a, b, rf = alternating(0.0015, 0.001, index), alternating(0.0006, 0.0002, index), pd.Series(0.0005, index=index)
    reported = {
        name: summarize_performance((1 + s).cumprod().to_frame("x"), risk_free=rf)["x_sharpe"] for name, s in (("A", a), ("B", b))
    }
    trained = {name: score(s, "sharpe", risk_free=rf) for name, s in (("A", a), ("B", b))}
    assert reported["A"] > reported["B"] and trained["A"] > trained["B"]  # same preference
    assert trained["A"] == pytest.approx((a - rf).mean() / (a - rf).std() * np.sqrt(252))


def test_walk_forward_picks_by_excess_sharpe_when_risk_free_given() -> None:
    index = pd.bdate_range("2010-01-01", "2016-12-31")
    returns = pd.DataFrame({"A": alternating(0.0015, 0.001, index), "B": alternating(0.0006, 0.0002, index)})
    rf = pd.Series(0.0005, index=index)
    _, raw = walk_forward(returns, train_years=3, test_months=12, metric="sharpe")
    _, excess = walk_forward(returns, train_years=3, test_months=12, metric="sharpe", risk_free=rf)
    assert set(raw["selected"]) == {"B"}  # raw return/vol favours the calm series
    assert set(excess["selected"]) == {"A"}  # excess over cash favours A, as the report does


# 3-2. a foreign (previous_close) sleeve that goes stale and later resumes is an error, not a silent skip
from etf_momentum.portfolio import sleeve_returns_on_calendar  # noqa: E402


def test_previous_close_gap_then_resume_is_an_error() -> None:
    calendar = pd.bdate_range("2021-01-01", periods=20)
    foreign = pd.Series([100.0, 100.0, 200.0, 200.0, 200.0], index=calendar[[0, 1, 12, 13, 19]])
    with pytest.raises(ValueError, match="gap"):
        sleeve_returns_on_calendar(foreign, calendar, "previous_close")


def test_previous_close_permanent_end_still_ends_the_sleeve() -> None:
    calendar = pd.bdate_range("2021-01-01", periods=20)
    foreign = pd.Series([100.0, 110.0], index=calendar[:2])
    returns = sleeve_returns_on_calendar(foreign, calendar, "previous_close")
    assert returns.iloc[-1:].isna().all()


def test_previous_close_short_holiday_is_fine() -> None:
    calendar = pd.bdate_range("2021-01-04", periods=10)
    foreign = pd.Series(range(100, 109), index=calendar.delete(4), dtype=float)  # one foreign holiday
    returns = sleeve_returns_on_calendar(foreign, calendar, "previous_close")
    assert returns.dropna().size == 9
