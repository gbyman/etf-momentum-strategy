from __future__ import annotations

import numpy as np
import pandas as pd

from etf_momentum.backtest import BacktestConfig, run_backtest
from etf_momentum.signals import MomentumConfig


def prices(days: int = 700) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=days)
    steps = np.arange(days)
    return pd.DataFrame(
        {
            "A": 100 * np.power(1.002, steps),
            "KR": np.linspace(100, 60, days),  # home market in a downtrend
            "US": 100 * np.power(1.001, steps),  # other market trending up
            "CASH": 100.0,
        },
        index=index,
    )


def held(risk_on_tickers: tuple[str, ...] | None) -> set[str]:
    _, positions, _ = run_backtest(
        prices(),
        BacktestConfig(
            top_n=1,
            cash_ticker="CASH",
            benchmark="KR",
            risk_on_benchmark=True,
            risk_on_tickers=risk_on_tickers,
            momentum=MomentumConfig(skip_days=0, sma_window=100),
            candidate_tickers=("A",),
        ),
    )
    return {ticker for tickers in positions["tickers"] for ticker in tickers.split(",")}


def test_default_filter_uses_benchmark() -> None:
    assert held(None) == {"CASH"}


def test_filter_can_use_another_ticker() -> None:
    assert held(("US",)) == {"A"}


def test_filter_is_risk_on_if_any_ticker_is_above_its_sma() -> None:
    assert held(("KR", "US")) == {"A"}
    assert held(("KR",)) == {"CASH"}
