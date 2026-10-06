from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from etf_momentum.portfolio import PortfolioConfig, align_sleeves, run_portfolio, to_base_currency


def bdays(n: int = 300) -> pd.DatetimeIndex:
    return pd.bdate_range("2021-01-01", periods=n)


def test_usd_returns_are_converted_with_fx_moves() -> None:
    index = bdays(3)
    usd = pd.Series([0.0, 0.0, 0.10], index=index)
    krw_per_usd = pd.Series([1000.0, 1100.0, 1100.0], index=index)
    krw = to_base_currency(usd, krw_per_usd)
    assert krw.iloc[1] == pytest.approx(0.10)  # asset flat, dollar +10%
    assert krw.iloc[2] == pytest.approx(0.10)  # asset +10%, fx flat


def test_sleeves_on_different_calendars_get_zero_return_on_missing_days() -> None:
    a = pd.Series(0.01, index=pd.to_datetime(["2021-01-04", "2021-01-05", "2021-01-06"]))
    b = pd.Series(0.02, index=pd.to_datetime(["2021-01-04", "2021-01-06"]))
    aligned = align_sleeves({"A": a, "B": b})
    assert aligned.loc["2021-01-05", "B"] == 0.0
    assert not aligned.isna().any().any()


def test_monthly_rebalance_restores_target_weights_and_drifts_in_between() -> None:
    index = bdays(60)
    returns = pd.DataFrame({"UP": 0.01, "FLAT": 0.0}, index=index)
    equity, history = run_portfolio(returns, PortfolioConfig(weights={"UP": 0.5, "FLAT": 0.5}))
    dates = list(history["date"])
    assert dates[0] == index[0]  # initial allocation
    assert dates[1] == index[index.month == index[0].month][-1]  # then the last trading day of each month
    first, second = dates[0], dates[1]
    days = ((index > first) & (index <= second)).sum()
    expected = 0.5 * 1.01**days + 0.5
    assert equity.loc[second] / equity.loc[first] == pytest.approx(expected)


def test_leverage_pays_borrow_rate_on_borrowed_amount() -> None:
    index = pd.date_range("2021-01-01", "2021-12-31", freq="D")
    returns = pd.DataFrame({"ZERO": 0.0}, index=index)
    equity, _ = run_portfolio(returns, PortfolioConfig(weights={"ZERO": 1.0}, leverage=1.5, borrow_rate=0.07))
    # half of equity borrowed at 7% a year for ~one year -> about -3.5%
    assert equity.iloc[-1] / equity.iloc[0] - 1 == pytest.approx(-0.035, abs=0.002)


def test_exposure_below_one_earns_cash_return() -> None:
    index = bdays(250)
    returns = pd.DataFrame({"ZERO": 0.0}, index=index)
    cash = pd.Series(0.0001, index=index)
    equity, _ = run_portfolio(returns, PortfolioConfig(weights={"ZERO": 1.0}, leverage=0.5), cash_returns=cash)
    # half the money sits in cash (re-split every month), the other half earns nothing
    assert equity.iloc[-1] / equity.iloc[0] - 1 == pytest.approx(0.5 * (1.0001**249 - 1), rel=0.01)


def test_rebalance_cost_is_charged_on_traded_amount() -> None:
    index = bdays(60)
    returns = pd.DataFrame({"UP": 0.01, "FLAT": 0.0}, index=index)
    free, _ = run_portfolio(returns, PortfolioConfig(weights={"UP": 0.5, "FLAT": 0.5}))
    costly, history = run_portfolio(returns, PortfolioConfig(weights={"UP": 0.5, "FLAT": 0.5}, rebalance_cost_bps=10))
    assert costly.iloc[-1] < free.iloc[-1]
    assert (history["cost"] >= 0).all() and history["cost"].iloc[0] > 0


def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        run_portfolio(pd.DataFrame({"A": [0.0, 0.0]}, index=bdays(2)), PortfolioConfig(weights={"A": 0.8}))
