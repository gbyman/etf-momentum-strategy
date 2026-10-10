"""Build config/universe_kr_all.csv from Naver's full ETF list (docs/superpowers/specs/2026-10-10-full-etf-universe-design.md).

    uv run python scripts/build_universe.py [--end YYYY-MM-DD] [--output config/universe_kr_all.csv]

Run it at the start of each month before `rank`, then commit the updated universe.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

from etf_momentum.data import download_prices
from etf_momentum.universe_builder import (
    BENCHMARK,
    build_universe,
    correlation_groups,
    drop_unpriced,
    filter_etf_list,
    recent_returns,
    write_universe,
)

NAVER_ETF_LIST = "https://finance.naver.com/api/sise/etfItemList.nhn"
CURATED = Path("config/universe_kr_pension.csv")  # proxies are carried over from here
WINDOW, MIN_OVERLAP, THRESHOLD = 252, 63, 0.9


def fetch_naver_list() -> list[dict]:
    request = urllib.request.Request(NAVER_ETF_LIST, headers={"User-Agent": "Mozilla/5.0"})
    payload = urllib.request.urlopen(request, timeout=30).read().decode("cp949")
    try:
        return json.loads(payload)["result"]["etfItemList"]
    except (KeyError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Unexpected Naver ETF list format ({exc}); universe not changed") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", default="config/universe_kr_all.csv")
    parser.add_argument("--end", default=None, help="Exclusive price end date (default: latest)")
    parser.add_argument("--cache-dir", default="data")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    items = fetch_naver_list()
    try:
        listing, excluded = filter_etf_list(items)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    end = pd.Timestamp(args.end) if args.end else pd.Timestamp.today().normalize()
    start = (end - pd.Timedelta(days=550)).strftime("%Y-%m-%d")
    raw = download_prices(listing["ticker"].tolist(), start=start, end=args.end, cache_dir=args.cache_dir, refresh=args.refresh, raw=True)
    try:
        listing, unpriced = drop_unpriced(listing, raw)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    returns = recent_returns(raw[listing["ticker"]], BENCHMARK, WINDOW)
    groups = correlation_groups(returns, THRESHOLD, MIN_OVERLAP)
    curated = pd.read_csv(CURATED)
    proxies = {r.ticker: r.proxy for r in curated.itertuples() if isinstance(r.proxy, str) and r.proxy.strip()}
    universe = build_universe(listing, groups, proxies)
    grouped = universe[universe["group"] != ""]
    meta = {
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": NAVER_ETF_LIST,
        "naver_etfs": len(items),
        "excluded": excluded,
        "no_yahoo_prices": unpriced,
        "etfs": len(universe),
        "price_data_last_date": str(returns.index[-1].date()),
        "groups": int(grouped["group"].nunique()),
        "etfs_in_groups": len(grouped),
        "group_rule": {"window_days": WINDOW, "min_overlap_days": MIN_OVERLAP, "min_correlation": THRESHOLD, "linkage": "complete"},
    }
    write_universe(universe, meta, Path(args.output))
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
