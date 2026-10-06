from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd
import pytest

from etf_momentum.backtest import BacktestConfig, run_backtest
from etf_momentum.data import splice_proxies
from etf_momentum.portfolio import PortfolioConfig, align_sleeves, annualized_cost, curve_to_returns, run_portfolio
from etf_momentum.reporting import save_run_metadata
from etf_momentum.signals import MomentumConfig
from etf_momentum.walkforward import oos_equity, score, walk_forward


# 1. proxy uses only US closes known before the Korean close (strictly earlier calendar date)
def test_proxy_uses_previous_us_close() -> None:
    days = pd.bdate_range("2021-01-04", periods=5)
    local = pd.DataFrame({"K": [np.nan, np.nan, np.nan, 100.0, 101.0]}, index=days)
    us = pd.Series([10.0, 10.0, 15.0, 15.0, 15.0], index=days)  # +50% on US day days[2]
    spliced, _ = splice_proxies(local, {"K": us}, None)
    assert spliced["K"].iloc[2] / spliced["K"].iloc[1] - 1 == pytest.approx(0.0)  # not yet known in Korea
    assert spliced["K"].iloc[3] == 100.0  # listing day keeps the real price


def test_proxy_lag_is_holiday_safe() -> None:
    kr = pd.to_datetime(["2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07"])
    local = pd.DataFrame({"K": [np.nan, np.nan, np.nan, 50.0]}, index=kr)
    us = pd.Series([10.0, 20.0], index=pd.to_datetime(["2021-01-01", "2021-01-05"]))  # US closed 01-04
    spliced, _ = splice_proxies(local, {"K": us}, None)
    # 01-05 KR sees the 01-01 US close; 01-06 KR sees the 01-05 US close
    assert spliced["K"].iloc[1] / spliced["K"].iloc[0] == pytest.approx(1.0)
    assert spliced["K"].iloc[2] / spliced["K"].iloc[1] == pytest.approx(2.0)


# 2. an ended sleeve stops the portfolio instead of being extended with zero returns
def test_portfolio_stops_at_earliest_sleeve_end() -> None:
    days = pd.bdate_range("2021-01-04", periods=5)
    aligned = align_sleeves({"A": pd.Series(0.01, index=days), "B": pd.Series(0.01, index=days[:3])})
    assert aligned["B"].iloc[3:].isna().all()
    equity, _ = run_portfolio(aligned, PortfolioConfig(weights={"A": 0.5, "B": 0.5}))
    assert equity.index[-1] == days[2]


def test_holiday_inside_sleeve_is_zero_return() -> None:
    a = pd.Series(0.01, index=pd.to_datetime(["2021-01-04", "2021-01-05", "2021-01-06"]))
    b = pd.Series(0.02, index=pd.to_datetime(["2021-01-04", "2021-01-06"]))
    assert align_sleeves({"A": a, "B": b}).loc["2021-01-05", "B"] == 0.0


# 3. the first out-of-sample return is part of the curve and survives into the portfolio
def test_oos_equity_starts_at_base_and_keeps_first_return() -> None:
    index = pd.bdate_range("2010-01-01", "2016-12-31")
    returns = pd.DataFrame({"A": 0.0005, "B": -0.0002}, index=index)
    returns.iloc[::7] = 0.003
    oos, windows = walk_forward(returns, train_years=3, test_months=12, metric="cagr")
    equity = oos_equity(pd.DataFrame({"walk_forward": oos}), windows["train_end"].iloc[0])
    assert equity.iloc[0, 0] == 1.0 and equity.index[0] == windows["train_end"].iloc[0]
    assert equity["walk_forward"].iloc[-1] == pytest.approx((1 + windows["test_return"]).prod())

    single, _ = run_portfolio(curve_to_returns(equity["walk_forward"]).to_frame("WF"), PortfolioConfig(weights={"WF": 1.0}))
    assert single.iloc[-1] / single.iloc[0] == pytest.approx(equity["walk_forward"].iloc[-1])


# 4. Calmar drawdown includes a loss from the starting value
def test_calmar_counts_first_day_loss() -> None:
    assert score(pd.Series([-0.5, 0.1, 0.1]), "calmar") < 0
    assert score(pd.Series([-0.1, 0.2, 0.2]), "calmar") < np.inf  # recovered above start, still had a drawdown
    assert score(pd.Series([0.0, 0.0]), "calmar") == 0.0


# 6. retirement risky cap counts only risk assets, safe holdings don't use the 70%
def test_risky_cap_ignores_safe_assets() -> None:
    index = pd.bdate_range("2020-01-01", periods=700)
    steps = np.arange(index.size)
    prices = pd.DataFrame(
        {
            "RISKY": 100 * np.power(1.002, steps),
            "BOND": 100 * np.power(1.0015, steps),
            "BENCH": 100 * np.power(1.0005, steps),
            "CASH": 100 * np.power(1.0001, steps),
        },
        index=index,
    )
    _, positions, _ = run_backtest(
        prices,
        BacktestConfig(
            top_n=2, cash_ticker="CASH", benchmark="BENCH", momentum=MomentumConfig(skip_days=0, sma_window=100),
            candidate_tickers=("RISKY", "BOND"), max_risky_weight=0.4, safe_tickers=("BOND",),
        ),
    )
    weights = {k: float(v) for k, v in (x.split(":") for x in positions["weights"].iloc[0].split(","))}
    assert weights["RISKY"] == pytest.approx(0.4)
    assert weights["BOND"] == pytest.approx(0.5)  # safe asset keeps its full slot
    assert weights["CASH"] == pytest.approx(0.1)


# 7. rebalance cost is measured inside each evaluation window
def test_annualized_cost_uses_only_window_rebalances() -> None:
    history = pd.DataFrame({"date": pd.to_datetime(["2020-01-31", "2020-12-31", "2021-12-31"]), "cost": [0.01, 0.02, 0.04]})
    window = pd.date_range("2020-01-01", "2020-12-31")
    assert annualized_cost(history, window) == pytest.approx(0.03 / ((window[-1] - window[0]).days / 365.25))


# 8. metadata can carry command-specific inputs, execution notes and code revision
def test_run_metadata_records_extra_inputs_and_revision(tmp_path) -> None:
    args = argparse.Namespace(command="portfolio", spec="x.json")
    prices = pd.DataFrame({"A": [1.0, 2.0]}, index=pd.bdate_range("2021-01-04", periods=2))
    path = save_run_metadata(tmp_path, args, prices, extra={"spec": {"k": 1}}, execution="month-end sleeve rebalance")
    meta = json.loads(path.read_text())
    assert meta["spec"] == {"k": 1}
    assert meta["execution"] == "month-end sleeve rebalance"
    assert "code_revision" in meta
