from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import etf_momentum.data as data


def raw_download(close: list[float]):
    index = pd.bdate_range("2021-01-04", periods=len(close))
    frame = pd.DataFrame({"Close": close}, index=index)
    return lambda **kwargs: frame


# Task 1: the cache keeps raw closes; callers choose filled (default) or raw.
# Y trades every day, so X's missing day is a row of its own (an all-empty row is dropped).
def test_raw_gap_survives_in_cache_and_raw_mode(tmp_path, monkeypatch) -> None:
    index = pd.bdate_range("2021-01-04", periods=4)
    columns = pd.MultiIndex.from_product([["Close"], ["X", "Y"]])
    frame = pd.DataFrame([[100.0, 1.0], [np.nan, 2.0], [110.0, 3.0], [111.0, 4.0]], index=index, columns=columns)
    monkeypatch.setattr(data.yf, "download", lambda **kwargs: frame)
    filled = data.download_prices(["X", "Y"], start="2021-01-01", end="2021-01-09", cache_dir=tmp_path)
    raw = data.download_prices(["X", "Y"], start="2021-01-01", end="2021-01-09", cache_dir=tmp_path, raw=True)
    assert filled["X"].tolist() == [100.0, 100.0, 110.0, 111.0]
    assert np.isnan(raw["X"].iloc[1]) and raw["X"].iloc[2] == 110.0
    cache = data._cache_path(["X", "Y"], "2021-01-01", "2021-01-09", tmp_path, prefix=data.PRICE_CACHE_PREFIX)
    assert cache.exists() and pd.read_csv(cache, index_col=0)["X"].isna().sum() == 1


def test_old_filled_price_cache_is_not_reused(tmp_path, monkeypatch) -> None:
    old = data._cache_path(["X"], "2021-01-01", "2021-01-09", tmp_path, prefix="prices")
    pd.DataFrame({"X": [1.0]}, index=pd.to_datetime(["2021-01-04"])).to_csv(old)
    monkeypatch.setattr(data.yf, "download", raw_download([100.0, 101.0]))
    prices = data.download_prices(["X"], start="2021-01-01", end="2021-01-09", cache_dir=tmp_path)
    assert prices["X"].tolist() == [100.0, 101.0]
    assert old.exists()  # left alone


# Task 2: candidate freshness from real observations
from etf_momentum.signals import observed_freshness, rank_momentum  # noqa: E402


def trending(days: int = 350) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-01", periods=days)
    steps = np.arange(days)
    return pd.DataFrame({"A": 100 * 1.001**steps, "BENCH": 100 * 1.0005**steps}, index=index)


def test_unobserved_candidate_is_stale_even_though_prices_are_filled() -> None:
    prices = trending()
    observed = prices.notna()
    observed.loc[prices.index[-60]:, "A"] = False  # no real observation for 60 days
    row = rank_momentum(prices, benchmark="BENCH", candidate_tickers=("A",), observed=observed).iloc[0]
    assert not row["price_fresh"] and not row["eligible"]
    assert pd.Timestamp(row["last_observed"]) == prices.index[-61]


def test_observed_flat_price_is_fresh() -> None:
    prices = trending()
    prices.loc[prices.index[-10]:, "A"] = prices.loc[prices.index[-11], "A"]  # really unchanged for 10 days
    row = rank_momentum(prices, benchmark="BENCH", candidate_tickers=("A",), observed=prices.notna()).iloc[0]
    assert row["price_fresh"]  # observed every day, so not stale


def test_candidate_missing_from_observation_table_is_stale_not_an_error() -> None:
    prices = trending()
    row = rank_momentum(prices, benchmark="BENCH", candidate_tickers=("A",), observed=prices[["BENCH"]].notna()).iloc[0]
    assert not row["price_fresh"]


def test_observed_freshness_counts_trading_rows() -> None:
    index = pd.bdate_range("2024-01-01", periods=10)
    mask = pd.Series([True] * 4 + [False] * 6, index=index)
    assert observed_freshness(mask, index) == (index[3], False)  # 6 rows without an observation
    assert observed_freshness(mask.iloc[:9], index[:9])[1]  # 5 rows: still fresh
    assert observed_freshness(None, index) == (None, False)
