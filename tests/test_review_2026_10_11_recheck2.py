"""Second recheck review of 2026-10-11 (docs/review_2026-10-11_recheck2_codex.md)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from etf_momentum.backtest import BacktestConfig, StaleDataError, run_backtest
from etf_momentum.signals import MomentumConfig

INDEX = pd.bdate_range("2023-01-02", "2023-12-29")
SMALL = MomentumConfig(lookbacks=(21, 42, 63), skip_days=0, sma_window=50, volatility_window=20)


def tail_backtest(lag: int, jump: float, execution: str = "same_close"):
    prices = pd.DataFrame(
        {"A": 100 * 1.001 ** np.arange(len(INDEX)), "BENCH": 100 * 1.0005 ** np.arange(len(INDEX)), "CASH": 100.0},
        index=INDEX,
    )
    prices.loc[INDEX[-1], "A"] *= jump
    prices.loc[INDEX[-lag:], "BENCH"] = np.nan  # the benchmark's last closes are not published yet
    config = BacktestConfig(top_n=1, benchmark="BENCH", cash_ticker="CASH", candidate_tickers=("A",), momentum=SMALL,
                            execution=execution)
    equity, positions, _ = run_backtest(prices, config, observed=prices.notna(), sessions=INDEX)
    return prices, equity, positions


# D02: the held asset's real returns after the benchmark's last close are not dropped
@pytest.mark.parametrize("execution", ["same_close", "next_close"])
@pytest.mark.parametrize("lag", [1, 3, 5])
@pytest.mark.parametrize("jump", [1.1, 0.9])
def test_returns_after_the_last_benchmark_close_are_kept(lag: int, jump: float, execution: str) -> None:
    prices, equity, positions = tail_backtest(lag, jump, execution)
    assert equity.index[-1] == INDEX[-1]
    assert positions["hold_until"].iloc[-1] == INDEX[-1]
    assert equity["strategy"].pct_change().iloc[-1] == pytest.approx(prices["A"].pct_change().iloc[-1])


def test_benchmark_missing_longer_than_the_freshness_limit_still_stops() -> None:
    with pytest.raises(StaleDataError, match="BENCH"):
        tail_backtest(6, 1.1)
