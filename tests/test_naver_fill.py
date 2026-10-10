"""Filling Yahoo's missing Korean trading days from Naver closes (no network: fetch is faked)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import etf_momentum.data as data
import etf_momentum.naver as naver

DAYS = pd.bdate_range("2023-07-24", "2023-08-04")


def fake_fetch(closes: dict[str, pd.Series]):
    calls = []

    def fetch(ticker, start, end):
        calls.append(ticker)
        series = closes[ticker]
        return series[(series.index >= pd.Timestamp(start)) & (series.index <= pd.Timestamp(end))]

    fetch.calls = calls
    return fetch


NAVER = {
    "069500.KS": pd.Series(100.0, index=DAYS),  # Naver's KRX trading days
    "A.KS": pd.Series(np.arange(1000.0, 1000.0 + 10 * len(DAYS), 10), index=DAYS),  # raw closes 1000, 1010, ...
}


def test_interior_gaps_are_chained_from_naver_returns() -> None:
    yahoo = pd.DataFrame({"A.KS": np.arange(500.0, 500.0 + 5 * len(DAYS), 5), "SPY": 1.0}, index=DAYS)  # adjusted = raw / 2
    yahoo.loc[["2023-07-31", "2023-08-01"], "A.KS"] = np.nan
    filled, report = naver.fill_gaps(yahoo, fetch=fake_fetch(NAVER))
    assert filled.loc["2023-07-31", "A.KS"] == pytest.approx(520.0 * 1050 / 1040)
    assert filled.loc["2023-08-01", "A.KS"] == pytest.approx(520.0 * 1060 / 1040)
    assert report == {"A.KS": ["2023-07-31", "2023-08-01"]}
    assert filled["SPY"].equals(yahoo["SPY"])  # non-Korean tickers are left alone


def test_rows_are_added_when_no_ticker_has_the_day() -> None:
    yahoo = pd.DataFrame({"A.KS": np.arange(500.0, 500.0 + 5 * len(DAYS), 5)}, index=DAYS).drop(pd.Timestamp("2023-07-31"))
    filled, report = naver.fill_gaps(yahoo, fetch=fake_fetch(NAVER))
    assert pd.Timestamp("2023-07-31") in filled.index and report["A.KS"] == ["2023-07-31"]


def test_no_fill_before_the_first_or_after_the_last_yahoo_close() -> None:
    yahoo = pd.DataFrame({"A.KS": np.arange(500.0, 500.0 + 5 * len(DAYS), 5)}, index=DAYS)
    yahoo.loc[:"2023-07-25", "A.KS"] = np.nan  # not listed yet (in Yahoo)
    yahoo.loc["2023-08-03":, "A.KS"] = np.nan  # not published yet (could be an intraday price on Naver)
    filled, report = naver.fill_gaps(yahoo, fetch=fake_fetch(NAVER))
    assert report == {} and filled["A.KS"].isna().sum() == 4


def test_day_naver_lacks_stays_missing_and_unfilled_tickers_are_not_fetched() -> None:
    closes = {**NAVER, "A.KS": NAVER["A.KS"].drop(pd.Timestamp("2023-07-31"))}
    yahoo = pd.DataFrame({"A.KS": np.arange(500.0, 500.0 + 5 * len(DAYS), 5), "B.KS": 1.0}, index=DAYS)
    yahoo.loc["2023-07-31", "A.KS"] = np.nan
    fetch = fake_fetch(closes)
    filled, report = naver.fill_gaps(yahoo, fetch=fetch)
    assert np.isnan(filled.loc["2023-07-31", "A.KS"]) and report == {}
    assert fetch.calls == ["069500.KS", "A.KS"]  # B.KS had no gap


# integration with the price cache
def yahoo_frame() -> pd.DataFrame:
    frame = pd.DataFrame({("Close", "A.KS"): np.arange(500.0, 500.0 + 5 * len(DAYS), 5), ("Close", "B.KS"): 1.0}, index=DAYS)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    frame.loc["2023-07-31", ("Close", "A.KS")] = np.nan
    return frame


def test_download_fills_caches_and_records(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(data, "NAVER_FILL", True)
    monkeypatch.setattr(naver, "naver_closes", fake_fetch(NAVER))
    monkeypatch.setattr(data.yf, "download", lambda **kw: yahoo_frame())
    raw = data.download_prices(["A.KS", "B.KS"], start="2023-07-24", end="2023-08-05", cache_dir=tmp_path, raw=True)
    assert raw.loc["2023-07-31", "A.KS"] == pytest.approx(520.0 * 1050 / 1040)
    cache = data._cache_path(["A.KS", "B.KS"], "2023-07-24", "2023-08-05", tmp_path, prefix=data.PRICE_CACHE_PREFIX)
    record = json.loads(cache.with_name(cache.name + ".naver_fill.json").read_text())
    assert record["filled"] == {"A.KS": ["2023-07-31"]}


def test_cache_without_a_fill_record_is_downloaded_again(tmp_path, monkeypatch) -> None:
    cache = data._cache_path(["A.KS", "B.KS"], "2023-07-24", "2023-08-05", tmp_path, prefix=data.PRICE_CACHE_PREFIX)
    yahoo_frame()["Close"].to_csv(cache)  # written before the Naver fill existed
    monkeypatch.setattr(data, "NAVER_FILL", True)
    monkeypatch.setattr(naver, "naver_closes", fake_fetch(NAVER))
    monkeypatch.setattr(data.yf, "download", lambda **kw: yahoo_frame())
    raw = data.download_prices(["A.KS", "B.KS"], start="2023-07-24", end="2023-08-05", cache_dir=tmp_path, raw=True)
    assert not np.isnan(raw.loc["2023-07-31", "A.KS"])


def test_naver_failure_warns_and_does_not_cache(tmp_path, monkeypatch) -> None:
    def broken(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(data, "NAVER_FILL", True)
    monkeypatch.setattr(naver, "naver_closes", broken)
    monkeypatch.setattr(data.yf, "download", lambda **kw: yahoo_frame())
    with pytest.warns(UserWarning, match="Naver"):
        raw = data.download_prices(["A.KS", "B.KS"], start="2023-07-24", end="2023-08-05", cache_dir=tmp_path, raw=True)
    assert np.isnan(raw.loc["2023-07-31", "A.KS"])
    assert not list(tmp_path.glob("prices_raw_*"))
