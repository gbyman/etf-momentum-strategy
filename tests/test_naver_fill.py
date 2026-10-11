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
    assert report == {"filled": {"A.KS": ["2023-07-31", "2023-08-01"]}, "approximate": {}, "unfilled": {}}
    assert filled["SPY"].equals(yahoo["SPY"])  # non-Korean tickers are left alone


def test_rows_are_added_when_no_ticker_has_the_day() -> None:
    yahoo = pd.DataFrame({"A.KS": np.arange(500.0, 500.0 + 5 * len(DAYS), 5)}, index=DAYS).drop(pd.Timestamp("2023-07-31"))
    filled, report = naver.fill_gaps(yahoo, fetch=fake_fetch(NAVER))
    assert pd.Timestamp("2023-07-31") in filled.index and report["filled"]["A.KS"] == ["2023-07-31"]


def test_no_fill_before_the_first_or_after_the_last_yahoo_close() -> None:
    yahoo = pd.DataFrame({"A.KS": np.arange(500.0, 500.0 + 5 * len(DAYS), 5)}, index=DAYS)
    yahoo.loc[:"2023-07-25", "A.KS"] = np.nan  # not listed yet (in Yahoo)
    yahoo.loc["2023-08-03":, "A.KS"] = np.nan  # not published yet (could be an intraday price on Naver)
    filled, report = naver.fill_gaps(yahoo, fetch=fake_fetch(NAVER))
    assert report["filled"] == {} and filled["A.KS"].isna().sum() == 4


def test_day_naver_lacks_stays_missing_and_unfilled_tickers_are_not_fetched() -> None:
    closes = {**NAVER, "A.KS": NAVER["A.KS"].drop(pd.Timestamp("2023-07-31"))}
    yahoo = pd.DataFrame({"A.KS": np.arange(500.0, 500.0 + 5 * len(DAYS), 5), "B.KS": 1.0}, index=DAYS)
    yahoo.loc["2023-07-31", "A.KS"] = np.nan
    fetch = fake_fetch(closes)
    filled, report = naver.fill_gaps(yahoo, fetch=fetch)
    assert np.isnan(filled.loc["2023-07-31", "A.KS"]) and report["filled"] == {}
    assert report["unfilled"] == {"A.KS": ["2023-07-31"]}
    assert fetch.calls == ["069500.KS", "A.KS"]  # B.KS had no gap


THREE = pd.bdate_range("2024-01-02", periods=3)


def distribution_case(raw: list[float], adjusted: list[float]):
    yahoo = pd.DataFrame({"A.KS": adjusted}, index=THREE)
    closes = {"069500.KS": pd.Series(100.0, index=THREE), "A.KS": pd.Series(raw, index=THREE)}
    return naver.fill_gaps(yahoo, fetch=fake_fetch(closes))


def test_distribution_inside_the_gap_is_not_counted_as_a_loss() -> None:
    # raw closes 100 -> 90 -> 90 with a distribution of 10 on day 2: adjusted closes are flat at 90
    filled, report = distribution_case([100.0, 90.0, 90.0], [90.0, np.nan, 90.0])
    assert filled["A.KS"].tolist() == pytest.approx([90.0, 90.0, 90.0])
    assert report["filled"] == {"A.KS": ["2024-01-03"]}
    assert report["approximate"]["A.KS"] == {"2024-01-03": pytest.approx(1 / 0.9 - 1)}


def test_split_inside_the_gap_keeps_the_adjusted_level() -> None:
    filled, report = distribution_case([100.0, 50.0, 50.0], [50.0, np.nan, 50.0])
    assert filled["A.KS"].tolist() == pytest.approx([50.0, 50.0, 50.0])
    assert "2024-01-03" in report["approximate"]["A.KS"]


def test_constant_adjustment_factor_is_exact_and_not_approximate() -> None:
    # Yahoo adjusted = raw x 0.9 on both sides of the gap (a distribution before the window)
    filled, report = distribution_case([100.0, 95.0, 98.0], [90.0, np.nan, 88.2])
    assert filled.loc["2024-01-03", "A.KS"] == pytest.approx(85.5)
    assert report["approximate"] == {}


def test_empty_reference_calendar_is_an_error() -> None:
    yahoo = pd.DataFrame({"A.KS": [90.0, 90.0]}, index=THREE[[0, 2]])  # Yahoo lacks the middle day
    closes = {"069500.KS": pd.Series(dtype=float, index=pd.DatetimeIndex([])), "A.KS": pd.Series([100.0, 100.0, 100.0], index=THREE)}
    with pytest.raises(ValueError, match="reference"):
        naver.fill_gaps(yahoo, fetch=fake_fetch(closes))


def test_reference_calendar_missing_a_yahoo_trading_day_is_an_error() -> None:
    yahoo = pd.DataFrame({"A.KS": np.arange(500.0, 500.0 + 5 * len(DAYS), 5)}, index=DAYS)
    closes = {**NAVER, "069500.KS": NAVER["069500.KS"].drop(pd.Timestamp("2023-07-28"))}  # truncated response
    with pytest.raises(ValueError, match="2023-07-28"):
        naver.fill_gaps(yahoo, fetch=fake_fetch(closes))


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
    assert record["approximate"] == {} and record["unfilled"] == {} and record["version"] == data.NAVER_FILL_VERSION


def test_cache_without_a_fill_record_is_downloaded_again(tmp_path, monkeypatch) -> None:
    cache = data._cache_path(["A.KS", "B.KS"], "2023-07-24", "2023-08-05", tmp_path, prefix=data.PRICE_CACHE_PREFIX)
    yahoo_frame()["Close"].to_csv(cache)  # written before the Naver fill existed
    monkeypatch.setattr(data, "NAVER_FILL", True)
    monkeypatch.setattr(naver, "naver_closes", fake_fetch(NAVER))
    monkeypatch.setattr(data.yf, "download", lambda **kw: yahoo_frame())
    raw = data.download_prices(["A.KS", "B.KS"], start="2023-07-24", end="2023-08-05", cache_dir=tmp_path, raw=True)
    assert not np.isnan(raw.loc["2023-07-31", "A.KS"])


def test_cache_with_an_older_fill_record_is_downloaded_again(tmp_path, monkeypatch) -> None:
    cache = data._cache_path(["A.KS", "B.KS"], "2023-07-24", "2023-08-05", tmp_path, prefix=data.PRICE_CACHE_PREFIX)
    yahoo_frame()["Close"].to_csv(cache)
    record = data._fill_record_path(cache)
    record.write_text(json.dumps({"filled": {}, "filled_days": 0, "reference": naver.REFERENCE}))  # before version 2
    monkeypatch.setattr(data, "NAVER_FILL", True)
    monkeypatch.setattr(naver, "naver_closes", fake_fetch(NAVER))
    monkeypatch.setattr(data.yf, "download", lambda **kw: yahoo_frame())
    raw = data.download_prices(["A.KS", "B.KS"], start="2023-07-24", end="2023-08-05", cache_dir=tmp_path, raw=True)
    assert not np.isnan(raw.loc["2023-07-31", "A.KS"])
    assert json.loads(record.read_text())["version"] == data.NAVER_FILL_VERSION


def test_failed_download_does_not_fall_back_to_an_older_fill_version(tmp_path, monkeypatch) -> None:
    cache = data._cache_path(["A.KS"], "2024-01-02", "2024-01-05", tmp_path, prefix=data.PRICE_CACHE_PREFIX)
    pd.DataFrame({"A.KS": [90.0, 81.0, 90.0]}, index=THREE).to_csv(cache)  # version 1: distribution counted as a loss
    data._fill_record_path(cache).write_text(json.dumps({"version": 1, "filled": {"A.KS": ["2024-01-03"]}}))
    monkeypatch.setattr(data, "NAVER_FILL", True)
    monkeypatch.setattr(data.yf, "download", lambda **kw: pd.DataFrame())
    with pytest.raises(RuntimeError, match="No prices data"):
        data.download_prices(["A.KS"], start="2024-01-02", end="2024-01-05", cache_dir=tmp_path, raw=True)


def test_failed_download_still_falls_back_to_a_current_but_old_cache(tmp_path, monkeypatch) -> None:
    cache = data._cache_path(["A.KS"], "2024-01-02", None, tmp_path, prefix=data.PRICE_CACHE_PREFIX)
    pd.DataFrame({"A.KS": [90.0, 90.0, 90.0]}, index=THREE).to_csv(cache)
    data._fill_record_path(cache).write_text(json.dumps({"version": data.NAVER_FILL_VERSION, "filled": {}}))
    old = cache.stat().st_mtime - 3 * 86400
    import os

    os.utime(cache, (old, old))  # past max_age_hours: refreshed when possible
    monkeypatch.setattr(data, "NAVER_FILL", True)
    monkeypatch.setattr(data.yf, "download", lambda **kw: pd.DataFrame())
    with pytest.warns(UserWarning, match="stale"):
        prices = data.download_prices(["A.KS"], start="2024-01-02", cache_dir=tmp_path, raw=True)
    assert prices["A.KS"].tolist() == [90.0, 90.0, 90.0]


def test_empty_naver_reference_is_not_cached_and_the_next_run_retries(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(data, "NAVER_FILL", True)
    monkeypatch.setattr(naver, "naver_closes", fake_fetch({**NAVER, "069500.KS": pd.Series(dtype=float, index=pd.DatetimeIndex([]))}))
    monkeypatch.setattr(data.yf, "download", lambda **kw: yahoo_frame())
    with pytest.warns(UserWarning, match="Naver"):
        data.download_prices(["A.KS", "B.KS"], start="2023-07-24", end="2023-08-05", cache_dir=tmp_path, raw=True)
    assert not list(tmp_path.glob("prices_raw_*"))
    monkeypatch.setattr(naver, "naver_closes", fake_fetch(NAVER))  # Naver is back
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


def test_partial_download_is_still_filled(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(data, "NAVER_FILL", True)
    monkeypatch.setattr(naver, "naver_closes", fake_fetch(NAVER))
    monkeypatch.setattr(data.yf, "download", lambda **kw: yahoo_frame())
    with pytest.warns(UserWarning, match="NEW.KS"):
        raw = data.download_prices(["A.KS", "B.KS", "NEW.KS"], start="2023-07-24", end="2023-08-05", cache_dir=tmp_path, raw=True)
    assert raw.loc["2023-07-31", "A.KS"] == pytest.approx(520.0 * 1050 / 1040)
