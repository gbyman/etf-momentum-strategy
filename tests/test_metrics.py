from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from etf_momentum.backtest import BacktestConfig, run_backtest, summarize_performance
from etf_momentum.signals import MomentumConfig


def curves() -> tuple[pd.DataFrame, pd.Series]:
    index = pd.bdate_range("2020-01-01", periods=505)
    strategy = pd.Series(np.where(np.arange(505) % 2 == 0, 0.004, -0.002), index=index)
    benchmark = pd.Series(np.where(np.arange(505) % 2 == 0, 0.003, -0.002), index=index)
    risk_free = pd.Series(0.0001, index=index)
    strategy.iloc[0] = benchmark.iloc[0] = 0.0
    equity = pd.DataFrame({"strategy": (1 + strategy).cumprod(), "benchmark": (1 + benchmark).cumprod()})
    return equity, risk_free


def test_sharpe_uses_excess_return_over_risk_free() -> None:
    equity, risk_free = curves()
    metrics = summarize_performance(equity, risk_free=risk_free)
    excess = equity["strategy"].pct_change().dropna() - risk_free.iloc[1:]
    assert metrics["strategy_sharpe"] == pytest.approx(excess.mean() / excess.std() * np.sqrt(252))
    assert metrics["strategy_sharpe"] < summarize_performance(equity)["strategy_sharpe"]  # rf=0 when omitted


def test_legacy_sharpe_like_is_kept() -> None:
    equity, _ = curves()
    metrics = summarize_performance(equity)
    assert metrics["strategy_sharpe_like"] == pytest.approx(metrics["strategy_cagr"] / metrics["strategy_volatility"])


def test_excess_cagr_versus_benchmark() -> None:
    equity, _ = curves()
    metrics = summarize_performance(equity)
    assert metrics["excess_cagr"] == pytest.approx(metrics["strategy_cagr"] - metrics["benchmark_cagr"])


def rotating_prices(days: int = 900) -> pd.DataFrame:
    """Two assets whose leadership flips every ~60 days, so the strategy trades."""
    index = pd.bdate_range("2019-01-01", periods=days)
    steps = np.arange(days)
    phase = np.sin(steps / 60)
    return pd.DataFrame(
        {
            "A": 100 * np.exp(np.cumsum(0.0008 + 0.004 * phase)),
            "B": 100 * np.exp(np.cumsum(0.0008 - 0.004 * phase)),
            "BENCH": 100 * np.power(1.0004, steps),
            "CASH": 100 * np.power(1.0001, steps),
        },
        index=index,
    )


def test_backtest_reports_transaction_costs_and_turnover() -> None:
    config = BacktestConfig(
        top_n=1,
        cash_ticker="CASH",
        benchmark="BENCH",
        transaction_cost_bps=10,
        momentum=MomentumConfig(skip_days=0, sma_window=50, lookbacks=(21, 42, 63)),
        candidate_tickers=("A", "B"),
    )
    equity, positions, metrics = run_backtest(rotating_prices(), config)
    assert positions["transaction_cost"].tolist() == pytest.approx((positions["traded_notional"] * 0.001).tolist())
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    assert metrics["total_transaction_cost"] == pytest.approx(positions["transaction_cost"].sum())
    assert metrics["annual_transaction_cost"] == pytest.approx(positions["transaction_cost"].sum() / years)
    assert metrics["annual_turnover"] == pytest.approx(positions["turnover"].sum() / years)
    assert metrics["total_transaction_cost"] > 0


def test_backtest_sharpe_uses_cash_ticker_as_risk_free() -> None:
    prices = rotating_prices()
    config = BacktestConfig(
        top_n=1,
        cash_ticker="CASH",
        benchmark="BENCH",
        momentum=MomentumConfig(skip_days=0, sma_window=50, lookbacks=(21, 42, 63)),
        candidate_tickers=("A", "B"),
    )
    equity, _, metrics = run_backtest(prices, config)
    risk_free = prices["CASH"].pct_change().reindex(equity.index)
    assert metrics["strategy_sharpe"] == pytest.approx(summarize_performance(equity, risk_free=risk_free)["strategy_sharpe"])
