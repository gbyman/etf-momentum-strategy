from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd
import pytest

from etf_momentum.backtest import BacktestConfig, run_backtest
from etf_momentum.reporting import save_run_metadata
from etf_momentum.signals import MomentumConfig, rank_momentum
from etf_momentum.weekly import build_weekly_report


def prices(days: int = 700) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=days)
    steps = np.arange(days)
    zigzag = 1 + 0.02 * (-1.0) ** steps
    return pd.DataFrame(
        {
            "LOSE": np.linspace(100, 80, days) * zigzag,
            "WIN": 100 * np.power(1.0015, steps) * zigzag,
            "SMOOTH_A": 100 * np.power(1.0006, steps),  # ~zero volatility
            "SMOOTH_B": 100 * np.power(1.0005, steps),  # ~zero volatility
            "BENCH": 100 * np.power(1.0005, steps),
            "CASH": 100.0,
        },
        index=index,
    )


FAST = MomentumConfig(skip_days=0, sma_window=100)


# 1. volatility that cannot be computed makes a candidate ineligible
def test_uncomputable_volatility_is_not_eligible() -> None:
    config = MomentumConfig(skip_days=0, sma_window=100, volatility_window=400, rank_by="risk_adjusted_score")
    ranking = rank_momentum(prices(), as_of=prices().index[350], config=config, candidate_tickers=["WIN"])
    row = ranking.iloc[0]
    assert np.isnan(row["realized_volatility"])
    assert not row["volatility_ok"] and not row["eligible"]


# 2. warm-up covers the volatility window
def test_backtest_warm_up_includes_volatility_window() -> None:
    data = prices()
    config = MomentumConfig(skip_days=0, sma_window=100, volatility_window=400)
    _, positions, _ = run_backtest(
        data, BacktestConfig(top_n=1, cash_ticker="CASH", benchmark="BENCH", momentum=config, candidate_tickers=("WIN",))
    )
    assert data.index.get_loc(pd.Timestamp(positions["rebalance_date"].iloc[0])) >= 400
    assert positions["tickers"].iloc[0] == "WIN"


# 3. volatility floor keeps scores and inverse-vol weights out of float-noise territory
def test_volatility_floor_bounds_risk_adjusted_score() -> None:
    ranking = rank_momentum(prices(), config=FAST, candidate_tickers=["SMOOTH_A"]).iloc[0]
    assert ranking["realized_volatility"] < 1e-6  # raw value is kept for inspection
    assert ranking["risk_adjusted_score"] == pytest.approx(ranking["weighted_momentum"] / FAST.volatility_floor)


def test_inverse_vol_weights_of_near_zero_vol_assets_are_floored_not_noise() -> None:
    _, positions, _ = run_backtest(
        prices(),
        BacktestConfig(
            top_n=2,
            cash_ticker="CASH",
            benchmark="BENCH",
            weighting="inverse_vol",
            momentum=FAST,
            candidate_tickers=("SMOOTH_A", "SMOOTH_B"),
        ),
    )
    weights = dict(item.split(":") for item in positions["weights"].iloc[0].split(","))
    assert float(weights["SMOOTH_A"]) == pytest.approx(0.5, abs=1e-4)
    assert float(weights["SMOOTH_B"]) == pytest.approx(0.5, abs=1e-4)


# 5. stale traded value counts as illiquid
def test_stale_traded_value_is_illiquid() -> None:
    data = prices()
    value = pd.DataFrame({"WIN": 5e9}, index=data.index)
    as_of = data.index[600]
    value.loc[value.index > data.index[580], "WIN"] = np.nan  # volume data stops 20 trading days before as_of
    config = MomentumConfig(skip_days=0, sma_window=100, min_avg_traded_value=1e9)
    row = rank_momentum(data, as_of=as_of, config=config, candidate_tickers=["WIN"], traded_value=value).iloc[0]
    assert not row["liquid"] and np.isnan(row["avg_traded_value"])


# 4. run metadata records traded-value data
def test_run_metadata_records_traded_value_data(tmp_path) -> None:
    data = prices()
    value = pd.DataFrame({"WIN": 5e9, "LOSE": 1e6}, index=data.index[:-3])
    args = argparse.Namespace(command="backtest", universe="missing.csv", min_avg_traded_value=1e9)
    meta = json.loads(save_run_metadata(tmp_path, args, data, traded_value=value).read_text())
    section = meta["traded_value_data"]
    assert section["last_date"] == str(data.index[-4].date())
    assert section["tickers"] == ["LOSE", "WIN"]
    assert "SMOOTH_A" in section["missing_tickers"]

    meta_without = json.loads(save_run_metadata(tmp_path, args, data).read_text())
    assert meta_without["traded_value_data"] is None


# 6. positions record liquidity decisions
def test_positions_record_liquidity_exclusions_and_held_traded_value() -> None:
    data = prices()
    value = pd.DataFrame({"WIN": 5e9, "SMOOTH_A": 1e6}, index=data.index)
    config = MomentumConfig(skip_days=0, sma_window=100, min_avg_traded_value=1e9)
    _, positions, _ = run_backtest(
        data,
        BacktestConfig(top_n=2, cash_ticker="CASH", benchmark="BENCH", momentum=config, candidate_tickers=("WIN", "SMOOTH_A")),
        traded_value=value,
    )
    first = positions.iloc[0]
    assert first["liquidity_excluded"] == "SMOOTH_A"
    assert first["held_avg_traded_value"] == "WIN:5000000000"


def test_weekly_report_carries_liquidity_columns() -> None:
    current = pd.DataFrame(
        [{"as_of": pd.Timestamp("2026-01-10"), "ticker": "A", "name": "A", "category": "x", "rank": 1, "liquid": False,
          "avg_traded_value": 1e6}]
    )
    report = build_weekly_report(current, pd.DataFrame())
    assert not bool(report.iloc[0]["liquid"])
    assert report.iloc[0]["avg_traded_value"] == 1e6
