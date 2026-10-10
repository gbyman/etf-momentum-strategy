"""Build the full Korean ETF universe from Naver's ETF list (pure functions; no network here).

scripts/build_universe.py fetches the list and prices and calls these in order:
filter_etf_list -> drop_unpriced -> recent_returns -> correlation_groups -> build_universe.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

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


def recent_returns(raw: pd.DataFrame, benchmark: str = BENCHMARK, window: int = 252) -> pd.DataFrame:
    """Daily returns over the last `window` days the benchmark really traded; gaps stay NaN."""
    traded = raw[raw[benchmark].notna()]
    return traded.pct_change(fill_method=None).iloc[1:].tail(window)


def correlation_groups(returns: pd.DataFrame, threshold: float = 0.9, min_overlap: int = 63) -> pd.Series:
    """Complete-linkage clusters: a group's every pair has correlation >= threshold.

    Pairs observed together on fewer than `min_overlap` days, or with a flat series, have no
    correlation and are never merged. The most correlated pair of groups merges first; ties go to the
    sorted ticker order, so the result does not depend on column order. Returns a group number per
    ticker (sorted), or NA for an ETF alone."""
    tickers = sorted(returns.columns)
    corr = returns[tickers].corr(min_periods=min_overlap).to_numpy(dtype=float)
    link = np.where(np.isnan(corr), -np.inf, corr)  # group-to-group similarity = weakest pair
    np.fill_diagonal(link, -np.inf)
    members = {i: [i] for i in range(len(tickers))}
    while members:
        i, j = np.unravel_index(int(np.argmax(link)), link.shape)
        if link[i, j] < threshold:
            break
        i, j = min(i, j), max(i, j)
        members[i] += members.pop(j)
        merged = np.minimum(link[i], link[j])
        link[i, :] = merged
        link[:, i] = merged
        link[i, i] = -np.inf
        link[j, :] = -np.inf
        link[:, j] = -np.inf
    labels = pd.Series(pd.NA, index=tickers, dtype="object")
    for number, (_, group) in enumerate(sorted((g[0], g) for g in members.values() if len(g) > 1)):
        labels.iloc[sorted(group)] = number
    return labels


UNIVERSE_COLUMNS = ["ticker", "name", "category", "enabled", "group", "pension_ok", "retirement_ok", "risk_asset", "proxy", "note"]
RETIREMENT_EXCLUDED_NAME_PARTS = ("합성", "선물")  # derivative-type ETFs, approximated by name


def build_universe(listing: pd.DataFrame, groups: pd.Series, proxies: dict[str, str]) -> pd.DataFrame:
    """Universe CSV rows: every ETF enabled and allowed in pension accounts; synthetic/futures ETFs
    not allowed in retirement accounts; only the cash ETF is a safe asset. A group is named after its
    member with the largest traded value (ties and zeros: first ticker); an ETF alone has no group."""
    frame = listing.sort_values("ticker").reset_index(drop=True)
    numbers = groups.reindex(frame["ticker"]).to_numpy()
    frame["number"] = numbers
    names: dict[object, str] = {}
    for number, members in frame[pd.notna(frame["number"])].groupby("number"):
        best = members.sort_values(["traded_value", "ticker"], ascending=[False, True]).iloc[0]
        names[number] = best["name"]
    return pd.DataFrame(
        {
            "ticker": frame["ticker"],
            "name": frame["name"],
            "category": frame["tab"].map(TAB_NAMES).fillna("기타"),
            "enabled": 1,
            "group": [names[n] if pd.notna(n) else "" for n in frame["number"]],
            "pension_ok": 1,
            "retirement_ok": [0 if any(p in name for p in RETIREMENT_EXCLUDED_NAME_PARTS) else 1 for name in frame["name"]],
            "risk_asset": [0 if t == CASH else 1 for t in frame["ticker"]],
            "proxy": [proxies.get(t, "") for t in frame["ticker"]],
            "note": "",
        },
        columns=UNIVERSE_COLUMNS,
    )


def write_universe(universe: pd.DataFrame, meta: dict, output: Path) -> None:
    """Write the CSV and its .meta.json through temporary files, so a failure leaves the old files."""
    output = Path(output)
    meta_path = output.with_name(f"{output.stem}.meta.json")
    csv_tmp = output.with_name(f"{output.name}.tmp")
    meta_tmp = meta_path.with_name(f"{meta_path.name}.tmp")
    try:
        meta_tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        universe.to_csv(csv_tmp, index=False)
        os.replace(csv_tmp, output)
        os.replace(meta_tmp, meta_path)
    finally:
        for path in (csv_tmp, meta_tmp):
            path.unlink(missing_ok=True)
