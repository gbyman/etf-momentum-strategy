from __future__ import annotations

import hashlib
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd
import yfinance as yf


# Caches without an explicit end date are re-downloaded once they are older than this.
LATEST_CACHE_MAX_AGE_HOURS = 12.0
# Price data whose last date is more than this many calendar days old is reported as stale.
STALE_PRICE_DAYS = 4


@dataclass(frozen=True)
class UniverseMember:
    ticker: str
    name: str
    category: str
    group: str = ""


def load_universe(path: str | Path) -> list[UniverseMember]:
    universe_path = Path(path)
    frame = pd.read_csv(universe_path)
    required = {"ticker", "name", "category", "enabled"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Universe file is missing columns: {', '.join(sorted(missing))}")

    enabled = frame[frame["enabled"].astype(str).str.lower().isin(["1", "true", "yes", "y"])]
    members = [
        UniverseMember(
            ticker=str(row.ticker).strip(),
            name=str(row.name).strip(),
            category=str(row.category).strip(),
            group=_optional_text(getattr(row, "group", "")),
        )
        for row in enabled.itertuples(index=False)
        if str(row.ticker).strip()
    ]
    if not members:
        raise ValueError(f"No enabled tickers found in {universe_path}")
    return members


def _optional_text(value: object) -> str:
    return "" if pd.isna(value) else str(value).strip()


def unique_tickers(members: Iterable[UniverseMember], extras: Iterable[str] = ()) -> list[str]:
    tickers: list[str] = []
    seen: set[str] = set()
    for ticker in [member.ticker for member in members] + [ticker for ticker in extras if ticker]:
        if ticker not in seen:
            tickers.append(ticker)
            seen.add(ticker)
    return tickers


def download_prices(
    tickers: Iterable[str],
    start: str | None = None,
    end: str | None = None,
    cache_dir: str | Path = "data",
    refresh: bool = False,
    max_age_hours: float = LATEST_CACHE_MAX_AGE_HOURS,
) -> pd.DataFrame:
    """Download adjusted close prices and cache them by ticker set.

    A cache with an explicit ``end`` never changes, so it is reused as is. A ``latest`` cache is
    re-downloaded once it is older than ``max_age_hours``; if that download fails, the stale cache
    is used with a warning.
    """
    return _cached_download(tickers, start, end, cache_dir, refresh, max_age_hours, kind="prices")


def download_traded_value(
    tickers: Iterable[str],
    start: str | None = None,
    end: str | None = None,
    cache_dir: str | Path = "data",
    refresh: bool = False,
    max_age_hours: float = LATEST_CACHE_MAX_AGE_HOURS,
) -> pd.DataFrame:
    """Daily traded value (unadjusted close x volume) per ticker, cached like prices.

    Days without volume stay NaN rather than being forward-filled, so they never look liquid.
    """
    return _cached_download(tickers, start, end, cache_dir, refresh, max_age_hours, kind="traded_value")


def _cached_download(
    tickers: Iterable[str],
    start: str | None,
    end: str | None,
    cache_dir: str | Path,
    refresh: bool,
    max_age_hours: float,
    kind: str,
) -> pd.DataFrame:
    ticker_list = sorted(set(tickers))
    if not ticker_list:
        raise ValueError("No tickers supplied")
    clean = _clean_prices if kind == "prices" else _clean_traded_value

    cache_path = _cache_path(ticker_list, start, end, Path(cache_dir), prefix=kind)
    stale_cache: Path | None = None
    if cache_path.exists() and not refresh:
        age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
        if end is not None or age_hours <= max_age_hours:
            return _read_cache(cache_path, clean)
        stale_cache = cache_path

    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    try:
        raw = _yf_download(ticker_list, start, end, auto_adjust=kind == "prices")
    except Exception as exc:
        if stale_cache is None:
            raise
        raw = pd.DataFrame()
        warnings.warn(f"Download failed ({exc})", stacklevel=3)
    if raw.empty and stale_cache is not None:
        warnings.warn(f"Using stale {kind} cache {stale_cache.name}; download returned no data", stacklevel=3)
        return _read_cache(stale_cache, clean)
    if raw.empty:
        raise RuntimeError(f"No {kind} data downloaded. Check tickers, dates, and network access.")

    if kind == "prices":
        frame = _extract_field(raw, ticker_list, "Close")
    else:
        frame = _extract_field(raw, ticker_list, "Close") * _extract_field(raw, ticker_list, "Volume")
    frame = clean(frame)
    frame.to_csv(cache_path)
    return frame


def staleness_message(prices: pd.DataFrame, today: pd.Timestamp | None = None) -> str | None:
    """Return a warning when the last price date lags today by more than STALE_PRICE_DAYS."""
    if prices.empty:
        return "No price data"
    last = pd.Timestamp(prices.index[-1]).normalize()
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    lag = (today - last).days
    if lag > STALE_PRICE_DAYS:
        return f"Price data ends {last.date()}, {lag} days before {today.date()}. Rerun with --refresh."
    return None


def _read_cache(path: Path, clean: Callable[[pd.DataFrame], pd.DataFrame]) -> pd.DataFrame:
    return clean(pd.read_csv(path, index_col=0, parse_dates=True))


def _yf_download(ticker_list: list[str], start: str | None, end: str | None, auto_adjust: bool = True) -> pd.DataFrame:
    return yf.download(
        tickers=ticker_list,
        start=start,
        end=end,
        auto_adjust=auto_adjust,
        progress=False,
        group_by="column",
        threads=True,
    )


def _extract_field(raw: pd.DataFrame, ticker_list: list[str], field: str) -> pd.DataFrame:
    if isinstance(raw.columns, pd.MultiIndex):
        if field in raw.columns.get_level_values(0):
            frame = raw[field]
        elif field == "Close" and "Adj Close" in raw.columns.get_level_values(0):
            frame = raw["Adj Close"]
        else:
            raise RuntimeError(f"Downloaded data does not include {field}")
    else:
        frame = raw[[field]].copy()
        frame.columns = ticker_list[:1]
    if isinstance(frame, pd.Series):
        frame = frame.to_frame(name=ticker_list[0])
    return frame


def _clean_traded_value(value: pd.DataFrame) -> pd.DataFrame:
    return value.sort_index().dropna(axis=1, how="all")


def _clean_prices(prices: pd.DataFrame) -> pd.DataFrame:
    prices = prices.sort_index()
    prices = prices.dropna(axis=1, how="all")
    prices = prices.ffill().dropna(axis=0, how="all")
    return prices


def _cache_path(tickers: list[str], start: str | None, end: str | None, cache_dir: Path, prefix: str = "prices") -> Path:
    safe_key = "_".join(tickers).replace("/", "-").replace(".", "_")
    if len(safe_key) > 160:
        digest = hashlib.sha256(",".join(tickers).encode("utf-8")).hexdigest()[:16]
        safe_key = f"{len(tickers)}_tickers_{digest}"
    start_key = start or "earliest"
    end_key = end or "latest"
    return cache_dir / f"{prefix}_{safe_key}_{start_key}_{end_key}.csv"

