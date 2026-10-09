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
