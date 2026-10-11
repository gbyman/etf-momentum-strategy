"""Small follow-ups listed after the 2026-10-11 reviews (deferred minors)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import etf_momentum.cli as cli
import etf_momentum.data as data
import etf_momentum.universe_builder as builder

INDEX = pd.to_datetime(["2020-01-02", "2020-01-03"])


def yahoo(columns: dict[str, list[float]]) -> pd.DataFrame:
    frame = pd.DataFrame(columns, index=INDEX)
    frame.columns = pd.MultiIndex.from_product([["Close"], list(columns)])
    return frame


# A pinned-period download missing a ticker is cached; later runs retry only that ticker
def test_pinned_partial_download_is_cached_with_its_missing_tickers(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(data.yf, "download", lambda **kw: yahoo({"AAA": [1.0, 2.0], "NEW": [np.nan, np.nan]}))
    with pytest.warns(UserWarning, match="NEW"):
        data.download_prices(["AAA", "NEW"], start="2020-01-01", end="2020-01-04", cache_dir=tmp_path)
    cache = data._cache_path(["AAA", "NEW"], "2020-01-01", "2020-01-04", tmp_path, prefix=data.PRICE_CACHE_PREFIX)
    assert cache.exists()
    assert json.loads(data._missing_record_path(cache).read_text())["missing"] == ["NEW"]

    calls = []
    monkeypatch.setattr(data.yf, "download", lambda **kw: calls.append(kw["tickers"]) or pd.DataFrame())
    with pytest.warns(UserWarning, match="NEW"):
        prices = data.download_prices(["AAA", "NEW"], start="2020-01-01", end="2020-01-04", cache_dir=tmp_path)
    assert calls == [["NEW"]]  # only the missing ticker is asked for again
    assert list(prices.columns) == ["AAA"]


def test_missing_ticker_found_later_is_merged_into_the_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(data.yf, "download", lambda **kw: yahoo({"AAA": [1.0, 2.0], "NEW": [np.nan, np.nan]}))
    with pytest.warns(UserWarning):
        data.download_prices(["AAA", "NEW"], start="2020-01-01", end="2020-01-04", cache_dir=tmp_path)
    monkeypatch.setattr(data.yf, "download", lambda **kw: yahoo({"NEW": [5.0, 6.0]}))
    prices = data.download_prices(["AAA", "NEW"], start="2020-01-01", end="2020-01-04", cache_dir=tmp_path)
    assert prices["NEW"].tolist() == [5.0, 6.0] and prices["AAA"].tolist() == [1.0, 2.0]
    cache = data._cache_path(["AAA", "NEW"], "2020-01-01", "2020-01-04", tmp_path, prefix=data.PRICE_CACHE_PREFIX)
    assert not data._missing_record_path(cache).exists()
    monkeypatch.setattr(data.yf, "download", lambda **kw: pytest.fail("complete cache should be reused"))
    assert list(data.download_prices(["AAA", "NEW"], start="2020-01-01", end="2020-01-04", cache_dir=tmp_path).columns) == ["AAA", "NEW"]


# exchange_calendars 4.13.2 still lists two 2026 KRX holidays as sessions
def test_known_krx_holidays_are_not_sessions() -> None:
    kr = cli._exchange_sessions("069500.KS")
    assert pd.Timestamp("2026-06-03") not in kr and pd.Timestamp("2026-07-17") not in kr
    assert pd.Timestamp("2026-06-02") in kr
    assert pd.Timestamp("2026-06-03") in cli._exchange_sessions("SPY")


# universe builder
def item(code, name, tab=1, amount=100):
    return {"itemcode": code, "itemname": name, "etfTabCode": tab, "amonut": amount}


REQUIRED_ITEMS = [item("153130", "KODEX 단기채권", tab=6), item("069500", "KODEX 200")]


def test_bond_mixed_etfs_outside_tab_7_are_excluded() -> None:
    listing, excluded = builder.filter_etf_list(REQUIRED_ITEMS + [item("448540", "KODEX 삼성전자채권혼합", tab=2)])
    assert "448540.KS" not in set(listing["ticker"]) and excluded["name"] == 1


def test_integer_item_codes_keep_leading_zeros() -> None:
    listing, _ = builder.filter_etf_list([item(153130, "KODEX 단기채권", tab=6), item(69500, "KODEX 200")])
    assert set(listing["ticker"]) == {"069500.KS", "153130.KS"}


def test_repeated_excluded_etf_counts_as_a_duplicate() -> None:
    lev = item("122630", "KODEX 레버리지", tab=3)
    _, excluded = builder.filter_etf_list(REQUIRED_ITEMS + [lev, lev])
    assert excluded["tab_3"] == 1 and excluded["duplicate"] == 1


def test_benchmark_without_yahoo_prices_stops_the_build() -> None:
    listing, _ = builder.filter_etf_list(REQUIRED_ITEMS)
    prices = pd.DataFrame({"153130.KS": [1.0, 1.0], "069500.KS": [np.nan, np.nan]}, index=INDEX)
    with pytest.raises(ValueError, match="069500.KS"):
        builder.drop_unpriced(listing, prices)


def test_committed_pension_strategy_and_full_universe_load() -> None:
    from etf_momentum.data import load_universe
    from etf_momentum.strategy import load_strategy

    strategy = load_strategy("config/strategies/pension.json")
    members = load_universe(strategy.universe)
    tickers = [member.ticker for member in members]
    assert len(tickers) == len(set(tickers)) > 700
    assert {"069500.KS", "153130.KS"} <= set(tickers)


# rank --holdings: a held ETF outside the universe is said to be outside it
def test_held_etf_outside_the_universe_is_marked() -> None:
    changes = cli._holdings_changes(["A", "OLD"], ["A"], cash=None, universe={"A", "B"})
    assert changes["drop"] == ["OLD"] and changes["outside"] == ["OLD"]


# weekly: same version, different content
def test_weekly_notice_for_a_content_change_under_the_same_version(tmp_path) -> None:
    from etf_momentum.weekly import load_strategy_snapshot

    path = tmp_path / "weekly_history.csv"
    pd.DataFrame({"as_of": ["2026-10-02"], "ticker": ["A"], "rank": [1], "version": [3], "config_hash": ["old"]}).to_csv(path, index=False)
    _, notice = load_strategy_snapshot(path, pd.Timestamp("2026-10-09"), "new", 3)
    assert "버전 3→3" not in notice and "버전 3" in notice
