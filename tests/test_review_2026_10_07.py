from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
import pytest

import etf_momentum.cli as cli
from etf_momentum.data import UniverseMember
from etf_momentum.portfolio import PortfolioConfig, run_portfolio, sleeve_returns_on_calendar


KR = pd.bdate_range("2021-01-04", periods=8)  # evaluation calendar (Korean trading days)


# 1. evaluation calendar: same-day sleeves follow the calendar, foreign sleeves use the previous close
def test_same_day_sleeve_compounds_over_calendar_dates_only() -> None:
    curve = pd.Series(100 * 1.01 ** np.arange(len(KR)), index=KR)
    returns = sleeve_returns_on_calendar(curve, KR, timing="same_day")
    assert returns.index.equals(KR)
    assert returns.iloc[0] == 0.0  # base row
    assert (1 + returns).prod() == pytest.approx(curve.iloc[-1] / curve.iloc[0])


def test_previous_close_sleeve_uses_last_observation_before_each_date() -> None:
    us_days = KR[[0, 1, 2, 4, 5, 6, 7]]  # US closed on KR[3]
    us = pd.Series([10, 11, 12, 13, 14, 15, 16], index=us_days, dtype=float)
    fx = pd.Series(1000.0, index=KR)
    returns = sleeve_returns_on_calendar(us, KR, timing="previous_close", fx=fx)
    # KR[1] knows US KR[0] close (10) -> base; KR[2] knows 11; KR[3] knows 12; KR[4] still 12 (US closed KR[3])
    values = (1 + returns.dropna()).cumprod()
    assert values.loc[KR[2]] / values.loc[KR[1]] == pytest.approx(1.1)
    assert values.loc[KR[4]] / values.loc[KR[3]] == pytest.approx(1.0)


def test_previous_close_sleeve_ends_when_its_data_goes_stale() -> None:
    us = pd.Series([10.0, 11.0], index=KR[:2])
    returns = sleeve_returns_on_calendar(us, KR, timing="previous_close", max_stale_days=5)
    assert returns.loc[KR[2]] == pytest.approx(0.1)  # knows KR[1]
    assert returns.iloc[-1:].isna().all()  # more than 5 days after the last US observation


def test_unused_sleeve_does_not_change_a_scenario() -> None:
    a = sleeve_returns_on_calendar(pd.Series(100 * 1.01 ** np.arange(len(KR)), index=KR), KR, "same_day")
    unused = sleeve_returns_on_calendar(pd.Series(range(1, 9), index=KR, dtype=float), KR, "same_day")
    alone, _ = run_portfolio(a.to_frame("A"), PortfolioConfig(weights={"A": 1.0}))
    both, _ = run_portfolio(pd.DataFrame({"A": a, "U": unused}), PortfolioConfig(weights={"A": 1.0}))
    pd.testing.assert_series_equal(alone, both)


# 2. gaps inside a same-day sleeve are errors, not holidays
def test_missing_calendar_date_inside_sleeve_is_an_error() -> None:
    curve = pd.Series(100.0, index=KR.delete(3))
    with pytest.raises(ValueError, match="missing"):
        sleeve_returns_on_calendar(curve, KR, timing="same_day")


def test_explicit_nan_inside_sleeve_is_an_error() -> None:
    curve = pd.Series([100.0, np.nan, 121.0] + [121.0] * 5, index=KR)
    with pytest.raises(ValueError, match="NaN"):
        sleeve_returns_on_calendar(curve, KR, timing="same_day")


# 3. retirement cash ETF must be an enabled, safe, retirement-eligible universe member
def members() -> list[UniverseMember]:
    return [
        UniverseMember("153130.KS", "Short bond", "Korea Cash", risk_asset=False),
        UniverseMember("360750.KS", "S&P500", "Overseas Equity"),
        UniverseMember("BADCASH", "Futures cash", "Cash", risk_asset=False, retirement_ok=False),
    ]


def args(cash: str) -> argparse.Namespace:
    return argparse.Namespace(cash=cash, account="retirement", max_risky_weight=None)


def test_retirement_cash_must_be_in_universe() -> None:
    with pytest.raises(SystemExit, match="not an enabled member"):
        cli._safe_tickers(members(), args("SPY"))


@pytest.mark.parametrize("cash, message", [("360750.KS", "risk_asset=1"), ("BADCASH", "retirement_ok=0")])
def test_retirement_cash_must_be_safe_and_eligible(cash: str, message: str) -> None:
    with pytest.raises(SystemExit, match=message):
        cli._safe_tickers(members(), args(cash))


def test_valid_retirement_cash_passes() -> None:
    assert "153130.KS" in cli._safe_tickers(members(), args("153130.KS"))


# 4. fewer than two common dates is an error, not a zero portfolio
def test_portfolio_needs_two_common_dates() -> None:
    with pytest.raises(ValueError, match="two"):
        run_portfolio(pd.DataFrame({"A": [0.01]}, index=KR[:1]), PortfolioConfig(weights={"A": 1.0}))
