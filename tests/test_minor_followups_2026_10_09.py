"""Deferred minors from the 2026-10-09 reviews."""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd
import pytest

import etf_momentum.cli as cli
from etf_momentum.backtest import BacktestConfig, run_backtest
from etf_momentum.signals import MomentumConfig


# 1. a candidate excluded for missing observations says so, with its last observation date
def test_unobserved_exclusion_names_the_last_observation() -> None:
    row = pd.Series({"price_fresh": False, "last_observed": pd.Timestamp("2026-08-14")})
    assert cli._exclusion_reason(row, observed=True) == "최근 관측 없음 (마지막 2026-08-14)"
    assert cli._exclusion_reason(pd.Series({"price_fresh": False, "last_observed": None}), observed=True) == "관측 없음"
    assert cli._exclusion_reason(row) == "가격 정체"  # without an observation table: unchanged price rule


# 2. the real month-end path through `rank`: the benchmark alone lacks the last session
def test_rank_stops_when_only_the_benchmark_lacks_the_month_end(tmp_path, monkeypatch) -> None:
    index = pd.bdate_range(end="2026-10-06", periods=700)
    steps = np.arange(700)
    closes = pd.DataFrame({"A": 100 * 1.002**steps, "BENCH": 100 * 1.0005**steps, "CASH": 100.0}, index=index)
    closes.loc["2026-09-30", "BENCH"] = np.nan  # A and CASH traded that day: not a holiday
    monkeypatch.setattr(cli, "download_prices", lambda tickers, raw=False, **kw: (lambda f: f if raw else f.ffill())(closes[[t for t in tickers if t in closes]]))
    (tmp_path / "u.csv").write_text("ticker,name,category,enabled\nA,Alpha,X,1\nCASH,Cash,Cash,1\n")
    monkeypatch.setattr(sys, "argv", ["etf-momentum", "rank", "--universe", str(tmp_path / "u.csv"), "--benchmark", "BENCH",
                                      "--cash", "CASH", "--end", "2026-10-07", "--output-dir", str(tmp_path / "o")])
    with pytest.raises(SystemExit, match="2026-09-30.*--refresh"):
        cli.main()


# 3. walk-forward comparison baselines never turn missing returns into 0%
def test_fixed_baselines_leave_out_configs_with_missing_returns() -> None:
    period = pd.bdate_range("2021-01-01", periods=10)
    grid = pd.DataFrame({"full": 0.001, "gap": 0.002}, index=period)
    grid.loc[period[3], "gap"] = np.nan
    fixed, dropped = cli._fixed_baselines(grid, period)
    assert list(fixed.columns) == ["full"] and dropped == ["gap"]
    with pytest.raises(SystemExit, match="gap"):
        cli._fixed_baselines(grid[["gap"]], period)


def test_benchmark_baseline_with_missing_returns_stops() -> None:
    period = pd.bdate_range("2021-01-01", periods=5)
    benchmark = pd.Series(0.001, index=period.drop(period[2]))
    with pytest.raises(SystemExit, match="benchmark"):
        cli._benchmark_baseline(benchmark, period)


# 4. backtest rebalances on the benchmark's last real trading day, not a stray holiday row
def test_backtest_month_end_skips_a_row_the_benchmark_did_not_trade() -> None:
    index = pd.bdate_range("2025-01-01", "2026-02-27")
    steps = np.arange(len(index))
    prices = pd.DataFrame({"A": 100 * 1.001**steps, "BENCH": 100 * 1.0005**steps, "CASH": 100.0}, index=index)
    observed = prices.notna()
    observed.loc["2025-12-31", ["A", "BENCH", "CASH"]] = False  # Yahoo row on the KRX year-end closure
    config = BacktestConfig(top_n=1, cash_ticker="CASH", benchmark="BENCH", candidate_tickers=("A",),
                            momentum=MomentumConfig(lookbacks=(21, 42, 63), skip_days=0, sma_window=50, volatility_window=20))
    _, positions, _ = run_backtest(prices, config, observed=observed)
    december = pd.to_datetime(positions["rebalance_date"])
    assert pd.Timestamp("2025-12-30") in set(december) and pd.Timestamp("2025-12-31") not in set(december)
    _, plain, _ = run_backtest(prices, config)  # without observations: the old last-row rule
    assert pd.Timestamp("2025-12-31") in set(pd.to_datetime(plain["rebalance_date"]))


def test_observed_rebalance_dates_match_the_old_rule_when_everything_traded() -> None:
    index = pd.bdate_range("2020-01-01", "2021-12-31")
    index = index[(index < "2020-05-01") | (index >= "2020-07-01")]  # two months without any rows
    steps = np.arange(len(index))
    prices = pd.DataFrame({"A": 100 * 1.001**steps, "BENCH": 100 * 1.0005**steps, "CASH": 100.0}, index=index)
    config = BacktestConfig(top_n=1, cash_ticker="CASH", benchmark="BENCH", candidate_tickers=("A",),
                            momentum=MomentumConfig(lookbacks=(21, 42, 63), skip_days=0, sma_window=50, volatility_window=20))
    _, with_obs, _ = run_backtest(prices, config, observed=prices.notna())
    _, without, _ = run_backtest(prices, config)
    pd.testing.assert_series_equal(with_obs["rebalance_date"], without["rebalance_date"])
