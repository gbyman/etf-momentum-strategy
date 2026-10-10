from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from etf_momentum.universe_builder import drop_unpriced, filter_etf_list


def item(code: str, name: str, tab: int, amount: float | None = 100.0) -> dict:
    return {"itemcode": code, "itemname": name, "etfTabCode": tab, "amonut": amount}


ITEMS = [
    item("069500", "KODEX 200", 1, 2_000_000),
    item("153130", "KODEX 단기채권", 6, 50_000),
    item("122630", "KODEX 레버리지", 3),
    item("114800", "KODEX 인버스", 3),
    item("233740", "KODEX 코스닥150레버리지", 1),
    item("396520", "TIGER 미국나스닥100선물2x", 4),  # lower-case 2x, no 인버스/레버리지 in the name
    item("114260", "KODEX 국고채3년", 6),
    item("459580", "KODEX CD금리액티브(합성)", 6),
    item("448290", "KODEX 삼성전자SK하이닉스채권혼합50", 7),
    item("091160", "KODEX 반도체", 2, 800_000),
    item("472150", "TIGER 배당커버드콜액티브", 2),
    item("218420", "KODEX 미국S&P500에너지(합성)", 4),
    item("091160", "KODEX 반도체", 2, 800_000),  # listed twice
]


# Task 1: exclusion rules
def test_filter_keeps_equity_commodity_covered_call_and_synthetic() -> None:
    listing, excluded = filter_etf_list(ITEMS)
    assert listing["ticker"].tolist() == ["069500.KS", "091160.KS", "153130.KS", "218420.KS", "472150.KS"]
    assert excluded == {"tab_3": 2, "tab_6": 2, "tab_7": 1, "name": 2, "duplicate": 1}


def test_cash_etf_is_kept_although_its_tab_is_bonds() -> None:
    listing, _ = filter_etf_list(ITEMS)
    row = listing.set_index("ticker").loc["153130.KS"]
    assert row["name"] == "KODEX 단기채권" and row["tab"] == 6


def test_missing_required_etf_in_the_list_is_an_error() -> None:
    with pytest.raises(ValueError, match="153130.KS"):
        filter_etf_list([i for i in ITEMS if i["itemcode"] != "153130"])


def test_missing_traded_value_becomes_zero() -> None:
    listing, _ = filter_etf_list([*ITEMS, item("999990", "NEW ETF", 2, None)])
    assert listing.set_index("ticker").loc["999990.KS", "traded_value"] == 0.0


# Task 1: tickers without Yahoo prices
def test_drop_unpriced_removes_and_reports() -> None:
    listing, _ = filter_etf_list(ITEMS)
    index = pd.bdate_range("2026-01-01", periods=5)
    prices = pd.DataFrame({t: 1.0 for t in listing["ticker"] if t != "472150.KS"}, index=index)
    prices["218420.KS"] = np.nan
    kept, dropped = drop_unpriced(listing, prices)
    assert dropped == ["218420.KS", "472150.KS"]
    assert "472150.KS" not in kept["ticker"].tolist()


def test_unpriced_cash_etf_is_an_error() -> None:
    listing, _ = filter_etf_list(ITEMS)
    prices = pd.DataFrame({"069500.KS": [1.0]}, index=pd.to_datetime(["2026-01-02"]))
    with pytest.raises(ValueError, match="153130.KS"):
        drop_unpriced(listing, prices)
