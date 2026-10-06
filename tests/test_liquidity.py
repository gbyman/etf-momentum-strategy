from __future__ import annotations

import numpy as np
import pandas as pd

import etf_momentum.data as data
from etf_momentum.backtest import BacktestConfig, run_backtest
from etf_momentum.signals import MomentumConfig, rank_momentum


def two_winners(days: int = 700) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=days)
    steps = np.arange(days)
    return pd.DataFrame(
        {
            "THIN": 100 * np.power(1.0020, steps),  # best momentum, but barely trades
            "DEEP": 100 * np.power(1.0015, steps),
            "BENCH": 100 * np.power(1.0005, steps),
            "CASH": 100.0,
        },
        index=index,
    )


def traded_value(prices: pd.DataFrame, thin: float = 1e6, deep: float = 5e9) -> pd.DataFrame:
    return pd.DataFrame({"THIN": thin, "DEEP": deep}, index=prices.index)


LIQUID = MomentumConfig(skip_days=0, sma_window=100, min_avg_traded_value=1e9)


def test_illiquid_candidate_is_not_eligible() -> None:
    prices = two_winners()
    ranking = rank_momentum(prices, config=LIQUID, candidate_tickers=["THIN", "DEEP"], traded_value=traded_value(prices))
    rows = ranking.set_index("ticker")
    assert not rows.loc["THIN", "eligible"] and not rows.loc["THIN", "liquid"]
    assert rows.loc["DEEP", "eligible"]
    assert rows.loc["DEEP", "avg_traded_value"] == 5e9


def test_liquidity_uses_only_data_up_to_rebalance_date() -> None:
    prices = two_winners()
    value = traded_value(prices)
    as_of = prices.index[500]
    value.loc[value.index > as_of, "THIN"] = 1e12  # becomes liquid only after the decision date
    ranking = rank_momentum(prices, as_of=as_of, config=LIQUID, candidate_tickers=["THIN"], traded_value=value)
    assert not ranking.iloc[0]["liquid"]


def test_missing_traded_value_counts_as_illiquid_when_filter_is_on() -> None:
    prices = two_winners()
    ranking = rank_momentum(prices, config=LIQUID, candidate_tickers=["THIN"], traded_value=traded_value(prices).drop(columns="THIN"))
    assert not ranking.iloc[0]["liquid"]


def test_no_threshold_means_no_liquidity_filter() -> None:
    prices = two_winners()
    ranking = rank_momentum(prices, config=MomentumConfig(skip_days=0, sma_window=100), candidate_tickers=["THIN"])
    assert ranking.iloc[0]["liquid"] and ranking.iloc[0]["eligible"]


def test_backtest_never_holds_illiquid_etf() -> None:
    prices = two_winners()
    _, positions, _ = run_backtest(
        prices,
        BacktestConfig(top_n=1, cash_ticker="CASH", benchmark="BENCH", momentum=LIQUID, candidate_tickers=("THIN", "DEEP")),
        traded_value=traded_value(prices),
    )
    held = {ticker for tickers in positions["tickers"] for ticker in tickers.split(",")}
    assert held == {"DEEP"}


def test_download_traded_value_multiplies_unadjusted_close_by_volume(tmp_path, monkeypatch) -> None:
    index = pd.to_datetime(["2020-01-02", "2020-01-03"])
    columns = pd.MultiIndex.from_product([["Close", "Volume"], ["AAA", "BBB"]])
    raw = pd.DataFrame([[10.0, 20.0, 100, 5], [11.0, 21.0, 200, np.nan]], index=index, columns=columns)
    calls = []

    def fake_download(**kwargs):
        calls.append(kwargs)
        return raw

    monkeypatch.setattr(data.yf, "download", fake_download)
    value = data.download_traded_value(["AAA", "BBB"], start="2020-01-01", end="2020-01-04", cache_dir=tmp_path)
    assert calls[0]["auto_adjust"] is False
    assert value.loc["2020-01-03", "AAA"] == 2200.0
    assert np.isnan(value.loc["2020-01-03", "BBB"])  # no volume is not forward-filled into fake liquidity
    # cached separately from prices
    assert data.download_traded_value(["AAA", "BBB"], start="2020-01-01", end="2020-01-04", cache_dir=tmp_path).equals(value)
    assert len(calls) == 1
    assert all(path.name.startswith("traded_value_") for path in tmp_path.iterdir())


def test_threshold_without_traded_value_data_is_an_error() -> None:
    import pytest

    with pytest.raises(ValueError, match="traded_value"):
        rank_momentum(two_winners(), config=LIQUID, candidate_tickers=["THIN"])
