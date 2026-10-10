"""Regression tests for docs/review_2026-10-11_full_codex.md (N01-N06)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from etf_momentum.backtest import BacktestConfig, StaleDataError, run_backtest
from etf_momentum.signals import MomentumConfig

SMALL = MomentumConfig(lookbacks=(21, 42, 63), skip_days=0, sma_window=50, volatility_window=20)


def panel(start: str, end: str) -> pd.DataFrame:
    index = pd.bdate_range(start, end)
    steps = np.arange(len(index))
    return pd.DataFrame({"A": 100 * 1.001**steps, "BENCH": 100 * 1.0005**steps, "CASH": 100.0}, index=index)


def config(**kw) -> BacktestConfig:
    return BacktestConfig(top_n=1, cash_ticker="CASH", benchmark="BENCH", candidate_tickers=("A",), momentum=SMALL, **kw)


# N01: next_close trades on the benchmark's next trading day, not on a stray holiday row
def test_next_close_skips_a_row_the_benchmark_did_not_trade() -> None:
    prices = panel("2025-01-01", "2026-02-27").drop(pd.Timestamp("2026-01-01"))
    observed = prices.notna()
    observed.loc["2025-12-31", ["BENCH", "CASH"]] = False  # only A has a (stray) row on the KRX year-end closure
    _, positions, _ = run_backtest(prices, config(execution="next_close"), observed=observed)
    row = positions[pd.to_datetime(positions["rebalance_date"]) == pd.Timestamp("2025-12-30")].iloc[0]
    assert pd.Timestamp(row["trade_date"]) == pd.Timestamp("2026-01-02")
    previous = positions[pd.to_datetime(positions["rebalance_date"]) == pd.Timestamp("2025-11-28")].iloc[0]
    assert pd.Timestamp(previous["hold_until"]) == pd.Timestamp("2026-01-02")


# N02-A: a long benchmark gap inside the evaluation stops the run (it is not 0% returns)
def test_benchmark_gap_inside_the_evaluation_is_an_error() -> None:
    prices = panel("2023-01-02", "2023-12-29")
    observed = prices.notna()
    observed.loc["2023-09-04":"2023-09-18", "BENCH"] = False  # 11 rows
    with pytest.raises(StaleDataError, match="BENCH"):
        run_backtest(prices, config(), observed=observed)


# N02-B: with exchange sessions, a month-end session the benchmark lacks is an error
def test_missing_month_end_session_is_an_error_with_sessions() -> None:
    prices = panel("2023-01-02", "2023-12-29")
    observed = prices.notna()
    observed.loc["2023-07-31", "BENCH"] = False  # Yahoo's real gap
    with pytest.raises(StaleDataError, match="2023-07-31"):
        run_backtest(prices, config(), observed=observed, sessions=prices.index)
    _, positions, _ = run_backtest(prices, config(), observed=observed)  # without sessions: old rule
    assert pd.Timestamp("2023-07-28") in set(pd.to_datetime(positions["rebalance_date"]))


def test_holiday_month_end_and_a_lagging_last_day_are_fine_with_sessions() -> None:
    prices = panel("2023-01-02", "2023-12-29")
    observed = prices.notna()
    observed.loc["2023-12-29", "BENCH"] = False  # last data day not yet published for the benchmark
    sessions = prices.index.drop(pd.Timestamp("2023-06-30"))  # June 30 was a closure; a stray row exists
    observed.loc["2023-06-30", "BENCH"] = False
    _, positions, _ = run_backtest(prices, config(), observed=observed, sessions=sessions)
    dates = set(pd.to_datetime(positions["rebalance_date"]))
    assert pd.Timestamp("2023-06-29") in dates and pd.Timestamp("2023-06-30") not in dates


# N04: the first month-end with exactly enough history is a signal date
def test_first_signal_is_the_first_month_end_with_enough_rows() -> None:
    index = pd.bdate_range("2023-01-24", "2023-04-28")
    steps = np.arange(len(index))
    prices = pd.DataFrame({"A": 100 * 1.01**steps, "BENCH": 100 * 1.005**steps, "CASH": 100.0}, index=index)
    momentum = MomentumConfig(lookbacks=(2, 3, 4), skip_days=0, sma_window=3, volatility_window=3)
    assert len(prices.loc[:"2023-01-31"]) == 6  # max(4 + 0 + 2, 3, 3 + 1)
    _, positions, _ = run_backtest(prices, BacktestConfig(top_n=1, cash_ticker="CASH", benchmark="BENCH", candidate_tickers=("A",), momentum=momentum))
    assert pd.Timestamp(positions["rebalance_date"].iloc[0]) == pd.Timestamp("2023-01-31")


# N02-B through the CLI: backtest passes the benchmark's exchange sessions
def test_backtest_command_stops_on_a_missing_month_end_session(tmp_path, monkeypatch) -> None:
    import sys

    import etf_momentum.cli as cli

    closes = panel("2022-01-03", "2023-12-29")
    closes.loc["2023-07-31", "BENCH"] = np.nan  # A and CASH traded: a real session the benchmark lacks
    monkeypatch.setattr(cli, "download_prices", lambda tickers, raw=False, **kw: (lambda f: f if raw else f.ffill())(closes[[t for t in tickers if t in closes]]))
    monkeypatch.setattr(cli, "_exchange_sessions", lambda benchmark: pd.bdate_range("2020-01-01", "2024-12-31"))
    (tmp_path / "u.csv").write_text("ticker,name,category,enabled\nA,Alpha,X,1\nCASH,Cash,Cash,1\n")
    monkeypatch.setattr(sys, "argv", ["etf-momentum", "backtest", "--universe", str(tmp_path / "u.csv"), "--benchmark", "BENCH",
                                      "--cash", "CASH", "--start", "2022-01-01", "--output-dir", str(tmp_path / "o")])
    with pytest.raises(StaleDataError, match="2023-07-31"):
        cli.main()
