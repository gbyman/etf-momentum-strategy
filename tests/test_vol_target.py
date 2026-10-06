from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from etf_momentum.backtest import BacktestConfig, run_backtest
from etf_momentum.signals import MomentumConfig


FAST = MomentumConfig(skip_days=0, sma_window=100)


def prices(daily_swing: float, days: int = 700) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=days)
    steps = np.arange(days)
    return pd.DataFrame(
        {
            "RISKY": 100 * np.power(1.002, steps) * (1 + daily_swing * (-1.0) ** steps),
            "BENCH": 100 * np.power(1.0005, steps),
            "CASH": 100 * np.power(1.0001, steps),
        },
        index=index,
    )


def run(data: pd.DataFrame, target: float | None) -> pd.DataFrame:
    _, positions, _ = run_backtest(
        data,
        BacktestConfig(top_n=1, cash_ticker="CASH", benchmark="BENCH", momentum=FAST, candidate_tickers=("RISKY",), target_volatility=target),
    )
    return positions


def weight(positions: pd.DataFrame, ticker: str, row: int = 0) -> float:
    weights = dict(item.split(":") for item in positions["weights"].iloc[row].split(","))
    return float(weights.get(ticker, 0.0))


def test_exposure_scales_to_target_and_rest_goes_to_cash() -> None:
    data = prices(daily_swing=0.02)  # roughly 60% annualized volatility
    positions = run(data, target=0.10)
    first = positions.iloc[0]
    date = pd.Timestamp(first["rebalance_date"])
    realized = data["RISKY"].pct_change().loc[:date].tail(FAST.volatility_window).std() * np.sqrt(252)
    assert first["est_portfolio_vol"] == pytest.approx(realized)
    assert first["vol_scale"] == pytest.approx(0.10 / realized)
    assert weight(positions, "RISKY") == pytest.approx(0.10 / realized, abs=1e-4)
    assert weight(positions, "CASH") == pytest.approx(1 - 0.10 / realized, abs=1e-4)


def test_no_leverage_when_portfolio_is_calmer_than_target() -> None:
    positions = run(prices(daily_swing=0.001), target=0.50)
    assert positions["vol_scale"].iloc[0] == 1.0
    assert weight(positions, "RISKY") == pytest.approx(1.0)


def test_estimate_uses_only_returns_up_to_rebalance_date() -> None:
    calm = prices(daily_swing=0.001)
    first_date = pd.Timestamp(run(calm, target=0.10)["rebalance_date"].iloc[0])
    spiky = calm.copy()
    after = spiky.index > first_date
    spiky.loc[after, "RISKY"] = spiky.loc[after, "RISKY"] * (1 + 0.2 * (-1.0) ** np.arange(after.sum()))
    assert run(spiky, target=0.10)["vol_scale"].iloc[0] == run(calm, target=0.10)["vol_scale"].iloc[0]


def test_target_off_leaves_weights_unchanged() -> None:
    positions = run(prices(daily_swing=0.02), target=None)
    assert weight(positions, "RISKY") == pytest.approx(1.0)
    assert positions["vol_scale"].iloc[0] == 1.0


def test_risk_off_cash_is_not_scaled() -> None:
    data = prices(daily_swing=0.02)
    _, positions, _ = run_backtest(
        data,
        BacktestConfig(top_n=1, cash_ticker="CASH", benchmark="BENCH", momentum=FAST, candidate_tickers=("NOPE",), target_volatility=0.10),
    )
    assert weight(positions, "CASH") == pytest.approx(1.0)
