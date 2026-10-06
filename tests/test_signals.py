from __future__ import annotations

import numpy as np
import pandas as pd

from etf_momentum.backtest import BacktestConfig, run_backtest
from etf_momentum.signals import MomentumConfig, rank_momentum


def synthetic_prices(days: int = 600) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=days)
    strong = 100 * np.power(1.0015, np.arange(days))
    weak = np.linspace(100, 80, days)
    cash = np.full(days, 100.0)
    return pd.DataFrame({"STRONG": strong, "WEAK": weak, "CASH": cash}, index=index)


def test_rank_momentum_prefers_positive_trend() -> None:
    prices = synthetic_prices()
    ranking = rank_momentum(
        prices,
        benchmark="STRONG",
        config=MomentumConfig(skip_days=0, sma_window=100),
    )
    assert ranking.iloc[0]["ticker"] == "STRONG"
    assert bool(ranking.iloc[0]["eligible"])
    assert ranking.iloc[0]["return_1m"] > 0
    assert ranking.iloc[0]["return_3m"] > ranking.iloc[0]["return_1m"]
    assert pd.notna(ranking.iloc[0]["return_ytd"])
    assert not bool(ranking[ranking["ticker"] == "WEAK"].iloc[0]["eligible"])


def test_backtest_runs_on_synthetic_prices() -> None:
    prices = synthetic_prices()
    equity, positions, metrics = run_backtest(
        prices,
        BacktestConfig(
            top_n=1,
            rebalance="M",
            cash_ticker="CASH",
            benchmark="STRONG",
            momentum=MomentumConfig(skip_days=0, sma_window=100),
        ),
    )
    assert not equity.empty
    assert not positions.empty
    assert "strategy_cagr" in metrics


def test_relative_strength_filter_requires_benchmark_outperformance() -> None:
    prices = synthetic_prices()
    ranking = rank_momentum(
        prices,
        benchmark="STRONG",
        config=MomentumConfig(skip_days=0, sma_window=100, min_relative_momentum=0.01),
    )
    strong_row = ranking[ranking["ticker"] == "STRONG"].iloc[0]
    assert not bool(strong_row["eligible"])


def test_inverse_vol_weighting_concentrates_less_in_volatile_asset() -> None:
    index = pd.bdate_range("2020-01-01", periods=700)
    trend = 100 * np.power(1.001, np.arange(len(index)))
    volatile = trend * (1 + 0.12 * np.sin(np.arange(len(index)) / 4))
    steady = trend * (1 + 0.01 * np.sin(np.arange(len(index)) / 12))
    prices = pd.DataFrame({"VOL": volatile, "STEADY": steady, "CASH": 100.0}, index=index)
    _, positions, _ = run_backtest(
        prices,
        BacktestConfig(
            top_n=2,
            rebalance="M",
            cash_ticker="CASH",
            benchmark="STEADY",
            weighting="inverse_vol",
            momentum=MomentumConfig(skip_days=0, sma_window=100),
        ),
    )
    first_stock_position = positions[positions["tickers"].str.contains("VOL")].iloc[0]["weights"]
    weights = dict(item.split(":") for item in first_stock_position.split(","))
    assert float(weights["STEADY"]) > float(weights["VOL"])
