from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd
import pytest

from etf_momentum.backtest import BacktestConfig, run_backtest
from etf_momentum.reporting import save_run_metadata
from etf_momentum.signals import MomentumConfig


FAST = MomentumConfig(skip_days=0, sma_window=100)


def two_trending_assets(days: int = 700) -> pd.DataFrame:
    """UP rises faster than SLOW, and both zigzag so daily rebalancing and drift differ."""
    index = pd.bdate_range("2020-01-01", periods=days)
    steps = np.arange(days)
    zigzag = 1 + 0.04 * (-1.0) ** steps
    return pd.DataFrame(
        {
            "UP": 100 * np.power(1.002, steps) * zigzag,
            "SLOW": 100 * np.power(1.001, steps) / zigzag,
            "BENCH": 100 * np.power(1.0005, steps),
            "CASH": 100.0,
        },
        index=index,
    )


def config(accounting: str) -> BacktestConfig:
    return BacktestConfig(
        top_n=2,
        cash_ticker="CASH",
        benchmark="BENCH",
        momentum=FAST,
        candidate_tickers=("UP", "SLOW"),
        accounting=accounting,
    )


def test_buy_and_hold_period_return_is_weighted_asset_growth() -> None:
    prices = two_trending_assets()
    equity, positions, _ = run_backtest(prices, config("buy_and_hold"))
    row = positions[positions["tickers"] == "UP,SLOW"].iloc[1]  # first period starts before the equity curve
    start, end = pd.Timestamp(row["rebalance_date"]), pd.Timestamp(row["hold_until"])
    growth = prices.loc[end, ["UP", "SLOW"]] / prices.loc[start, ["UP", "SLOW"]]
    expected = 0.5 * growth["UP"] + 0.5 * growth["SLOW"]
    assert equity.loc[end, "strategy"] / equity.loc[start, "strategy"] == pytest.approx(expected, rel=1e-9)


def test_daily_rebalanced_accounting_is_still_available_and_differs() -> None:
    prices = two_trending_assets()
    buy_hold, _, _ = run_backtest(prices, config("buy_and_hold"))
    daily, _, _ = run_backtest(prices, config("daily_rebalanced"))
    assert buy_hold["strategy"].iloc[-1] != pytest.approx(daily["strategy"].iloc[-1], rel=1e-6)


def test_drift_creates_turnover_when_holdings_do_not_change() -> None:
    prices = two_trending_assets()
    _, drifted, _ = run_backtest(prices, config("buy_and_hold"))
    _, fixed, _ = run_backtest(prices, config("daily_rebalanced"))
    # Same two ETFs every month: fixed weights need no trades, drifted weights need rebalancing back to 50/50.
    assert fixed["turnover"].iloc[1:].max() == pytest.approx(0.0, abs=1e-12)
    assert drifted["turnover"].iloc[1:].min() > 0


def test_unknown_accounting_rejected() -> None:
    with pytest.raises(ValueError, match="accounting"):
        run_backtest(two_trending_assets(), config("nope"))


def test_run_metadata_records_arguments_and_data_dates(tmp_path) -> None:
    universe = tmp_path / "universe.csv"
    universe.write_text("ticker,name,category,enabled\nUP,Up,Test,1\n")
    args = argparse.Namespace(command="backtest", universe=str(universe), top_n=3, func=print)
    prices = two_trending_assets()
    path = save_run_metadata(tmp_path, args, prices)
    meta = json.loads(path.read_text())
    assert path.name == "backtest_run_config.json"
    assert meta["arguments"]["top_n"] == 3
    assert "func" not in meta["arguments"]
    assert meta["price_data"]["last_date"] == str(prices.index[-1].date())
    assert len(meta["universe_sha256"]) == 64
    assert "execution" in meta


# Equity curve must start at the first rebalance date, rebased to 1.0, for strategy and benchmark alike.
def zero_cash_prices(days: int = 900) -> pd.DataFrame:
    prices = two_trending_assets(days)
    prices["CASH"] = 100.0  # zero-return cash, the case that used to shift the start date
    return prices


def test_equity_starts_at_first_rebalance_when_strategy_never_invests() -> None:
    prices = zero_cash_prices()
    equity, positions, metrics = run_backtest(
        prices,
        BacktestConfig(top_n=1, cash_ticker="CASH", benchmark="BENCH", momentum=FAST, candidate_tickers=("NOPE",)),
    )
    first_rebalance = pd.Timestamp(positions["rebalance_date"].iloc[0])
    assert equity.index[0] == first_rebalance
    assert equity.iloc[0].tolist() == pytest.approx([1.0, 1.0])
    assert metrics["strategy_cagr"] == pytest.approx(0.0)


def test_equity_starts_at_first_rebalance_when_starting_in_cash() -> None:
    prices = zero_cash_prices()
    prices.loc[: prices.index[300], ["UP", "SLOW"]] = np.nan  # candidates listed late: months of cash first
    equity, positions, _ = run_backtest(prices, config("buy_and_hold"))
    assert positions.iloc[0]["tickers"] == "CASH"
    assert equity.index[0] == pd.Timestamp(positions["rebalance_date"].iloc[0])


def test_benchmark_cagr_measures_growth_from_first_rebalance() -> None:
    prices = two_trending_assets()
    equity, positions, metrics = run_backtest(prices, config("buy_and_hold"))
    start = pd.Timestamp(positions["rebalance_date"].iloc[0])
    end = equity.index[-1]
    growth = prices.loc[end, "BENCH"] / prices.loc[start, "BENCH"]
    years = (end - start).days / 365.25
    assert metrics["benchmark_cagr"] == pytest.approx(growth ** (1 / years) - 1, rel=1e-9)
    assert metrics["benchmark_ending_value"] == pytest.approx(growth, rel=1e-9)
