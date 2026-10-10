"""Build the full Korean ETF universe from Naver's ETF list (pure functions; no network here).

scripts/build_universe.py fetches the list and prices and calls these in order:
filter_etf_list -> drop_unpriced -> recent_returns -> correlation_groups -> build_universe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CASH = "153130.KS"
BENCHMARK = "069500.KS"
REQUIRED = (CASH, BENCHMARK)
TAB_NAMES = {1: "국내 시장지수", 2: "국내 업종/테마", 3: "국내 파생", 4: "해외 주식", 5: "원자재", 6: "채권", 7: "기타"}
EXCLUDED_TABS = (3, 6, 7)  # leveraged/inverse, bonds/rates/money market, bond-mixed/TDF
EXCLUDED_NAME_PARTS = ("레버리지", "인버스", "2X")


def filter_etf_list(items: list[dict], always_include: tuple[str, ...] = REQUIRED) -> tuple[pd.DataFrame, dict[str, int]]:
    """Candidates from Naver's ETF list: no leveraged/inverse, bond, rate or bond-mixed ETFs.

    The cash and benchmark ETFs are kept whatever their tab. Returns the listing (ticker, name, tab,
    traded_value in million KRW) sorted by ticker, and how many ETFs each rule removed."""
    excluded = {f"tab_{tab}": 0 for tab in EXCLUDED_TABS} | {"name": 0, "duplicate": 0}
    rows: dict[str, dict] = {}
    for entry in items:
        ticker = f"{str(entry['itemcode']).strip()}.KS"
        name = str(entry["itemname"]).strip()
        tab = int(entry["etfTabCode"])
        if ticker in rows:
            excluded["duplicate"] += 1
            continue
        if ticker not in always_include:
            if tab in EXCLUDED_TABS:
                excluded[f"tab_{tab}"] += 1
                continue
            if any(part in name.upper() for part in EXCLUDED_NAME_PARTS):
                excluded["name"] += 1
                continue
        amount = entry.get("amonut")
        traded = float(amount) if amount is not None and pd.notna(amount) else 0.0
        rows[ticker] = {"ticker": ticker, "name": name, "tab": tab, "traded_value": traded}
    missing = [t for t in always_include if t not in rows]
    if missing:
        raise ValueError(f"Naver ETF list has no {', '.join(missing)}; not building the universe")
    listing = pd.DataFrame(sorted(rows.values(), key=lambda row: row["ticker"]))
    return listing.reset_index(drop=True), excluded


def drop_unpriced(
    listing: pd.DataFrame, prices: pd.DataFrame, required: tuple[str, ...] = REQUIRED
) -> tuple[pd.DataFrame, list[str]]:
    """Remove ETFs Yahoo has no price for (a partial download is never cached, so they would force a
    full re-download on every run). A missing cash or benchmark ETF is an error."""
    priced = {t for t in prices.columns if prices[t].notna().any()}
    dropped = sorted(t for t in listing["ticker"] if t not in priced)
    missing_required = [t for t in required if t in dropped]
    if missing_required:
        raise ValueError(f"No Yahoo prices for required {', '.join(missing_required)}; not building the universe")
    return listing[~listing["ticker"].isin(dropped)].reset_index(drop=True), dropped
