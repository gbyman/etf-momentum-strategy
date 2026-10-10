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


# Task 2: correlation groups (complete linkage)
from etf_momentum.universe_builder import correlation_groups, recent_returns  # noqa: E402


def correlated(n: int = 252, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 0.01, n)
    other = rng.normal(0, 0.01, n)
    noise = lambda scale: rng.normal(0, scale, n)  # noqa: E731
    return pd.DataFrame(
        {
            "A": base + noise(0.001),
            "B": base + noise(0.001),
            "C": other + noise(0.001),
            "D": other + noise(0.001),
            "E": rng.normal(0, 0.01, n),
        },
        index=pd.bdate_range("2025-01-01", periods=n),
    )


def test_highly_correlated_pairs_share_a_group_and_others_stay_alone() -> None:
    groups = correlation_groups(correlated())
    assert groups["A"] == groups["B"] and groups["C"] == groups["D"] and groups["A"] != groups["C"]
    assert pd.isna(groups["E"])


def test_chain_does_not_link_the_ends() -> None:
    n = 400
    rng = np.random.default_rng(1)
    x, y = rng.normal(0, 0.01, n), rng.normal(0, 0.01, n)
    # corr(A,B) and corr(B,C) about 0.95, corr(A,C) about 0.81
    a = x
    b = x * 0.95 + y * 0.31
    c = x * 0.81 + y * 0.59
    groups = correlation_groups(pd.DataFrame({"A": a, "B": b, "C": c}))
    assert not (pd.notna(groups["A"]) and groups["A"] == groups["C"])


def test_short_or_flat_history_is_never_grouped() -> None:
    returns = correlated()
    returns.loc[returns.index[:-40], "B"] = np.nan  # only 40 days: below min_overlap
    returns["F"] = 0.0  # flat price: correlation undefined
    groups = correlation_groups(returns)
    assert pd.isna(groups["B"]) and pd.isna(groups["F"]) and pd.isna(groups["A"])


def test_groups_do_not_depend_on_column_order() -> None:
    returns = correlated()
    first = correlation_groups(returns)
    second = correlation_groups(returns[["E", "D", "C", "B", "A"]])
    pd.testing.assert_series_equal(first, second)


def test_recent_returns_use_benchmark_trading_days() -> None:
    index = pd.bdate_range("2025-01-01", periods=300)
    raw = pd.DataFrame({"069500.KS": 100.0, "X": np.linspace(100, 130, 300)}, index=index)
    raw.loc[index[-1], "069500.KS"] = np.nan  # a row the benchmark did not trade (stray holiday row)
    returns = recent_returns(raw, window=252)
    assert len(returns) == 252 and returns.index[-1] == index[-2]


# Task 3: universe rows
from etf_momentum.data import load_universe  # noqa: E402
from etf_momentum.universe_builder import UNIVERSE_COLUMNS, build_universe  # noqa: E402


def listing_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ticker": "069500.KS", "name": "KODEX 200", "tab": 1, "traded_value": 2_000_000.0},
            {"ticker": "102110.KS", "name": "TIGER 200", "tab": 1, "traded_value": 500_000.0},
            {"ticker": "153130.KS", "name": "KODEX 단기채권", "tab": 6, "traded_value": 50_000.0},
            {"ticker": "143850.KS", "name": "TIGER 미국S&P500선물(H)", "tab": 4, "traded_value": 0.0},
            {"ticker": "218420.KS", "name": "KODEX 미국S&P500에너지(합성)", "tab": 4, "traded_value": 0.0},
            {"ticker": "458730.KS", "name": "TIGER 미국배당다우존스", "tab": 4, "traded_value": 90_000.0},
        ]
    )


def test_build_universe_marks_accounts_assets_groups_and_proxies(tmp_path) -> None:
    groups = pd.Series({"069500.KS": 0, "102110.KS": 0, "143850.KS": 1, "218420.KS": 1, "153130.KS": pd.NA, "458730.KS": pd.NA})
    universe = build_universe(listing_frame(), groups, {"458730.KS": "SCHD", "999999.KS": "SPY"}).set_index("ticker")
    assert list(universe.reset_index().columns) == UNIVERSE_COLUMNS
    assert universe.loc["102110.KS", "group"] == "KODEX 200"  # largest traded value names the group
    assert universe.loc["218420.KS", "group"] == "TIGER 미국S&P500선물(H)"  # all 0: first ticker (143850) names it
    assert universe.loc["458730.KS", "group"] == "" and universe.loc["458730.KS", "proxy"] == "SCHD"
    assert universe.loc["143850.KS", "retirement_ok"] == 0 and universe.loc["218420.KS", "retirement_ok"] == 0
    assert universe.loc["069500.KS", "retirement_ok"] == 1 and (universe["pension_ok"] == 1).all()
    assert universe.loc["153130.KS", "risk_asset"] == 0 and universe["risk_asset"].sum() == len(universe) - 1
    assert universe.loc["458730.KS", "category"] == "해외 주식" and (universe["enabled"] == 1).all()
    path = tmp_path / "u.csv"
    universe.reset_index().to_csv(path, index=False)
    members = {m.ticker: m for m in load_universe(path)}
    assert members["153130.KS"].risk_asset is False and members["458730.KS"].proxy == "SCHD"
