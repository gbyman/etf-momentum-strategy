from __future__ import annotations

import hashlib
import json
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pandas as pd
import yfinance as yf

from etf_momentum import naver


# Caches without an explicit end date are re-downloaded once they are older than this.
LATEST_CACHE_MAX_AGE_HOURS = 12.0
# Price data whose last date is more than this many calendar days old is reported as stale.
STALE_PRICE_DAYS = 4
# Price caches hold raw closes (no forward fill), so days without an observation stay visible.
PRICE_CACHE_PREFIX = "prices_raw"
# Korean price caches get Yahoo's missing trading days filled from Naver (etf_momentum.naver) and a
# `<cache>.naver_fill.json` record; a Korean cache without that record is downloaded again.
NAVER_FILL = True
NAVER_FILL_VERSION = 2  # 2: adjustment factor across the gap (1 chained raw returns onto adjusted prices)


@dataclass(frozen=True)
class UniverseMember:
    ticker: str
    name: str
    category: str
    group: str = ""
    # Optional account columns (default: allowed / risk asset / no proxy).
    pension_ok: bool = True  # personal pension (연금저축)
    retirement_ok: bool = True  # retirement pension (IRP / DC)
    risk_asset: bool = True  # counts toward the retirement-account 70% risky-asset limit
    proxy: str = ""  # US ticker whose KRW-converted history fills the period before listing


ACCOUNT_CHOICES = ("all", "pension", "retirement")


def load_universe(path: str | Path) -> list[UniverseMember]:
    universe_path = Path(path)
    frame = pd.read_csv(universe_path)
    required = {"ticker", "name", "category", "enabled"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Universe file is missing columns: {', '.join(sorted(missing))}")

    enabled = frame[frame["enabled"].astype(str).str.lower().isin(["1", "true", "yes", "y"])]
    _reject_duplicate_tickers(enabled, universe_path)
    members = [
        UniverseMember(
            ticker=str(row.ticker).strip(),
            name=str(row.name).strip(),
            category=str(row.category).strip(),
            group=_optional_text(getattr(row, "group", "")),
            pension_ok=_optional_flag(getattr(row, "pension_ok", 1)),
            retirement_ok=_optional_flag(getattr(row, "retirement_ok", 1)),
            risk_asset=_optional_flag(getattr(row, "risk_asset", 1)),
            proxy=_optional_text(getattr(row, "proxy", "")),
        )
        for row in enabled.itertuples(index=False)
        if str(row.ticker).strip()
    ]
    if not members:
        raise ValueError(f"No enabled tickers found in {universe_path}")
    return members


def _reject_duplicate_tickers(enabled: pd.DataFrame, path: Path) -> None:
    """A ticker enabled twice would be ranked and selected twice while holding one weight."""
    keys = enabled["ticker"].astype(str).str.strip().str.upper()
    keys = keys[keys != ""]
    repeated = keys[keys.duplicated(keep=False)]
    if not repeated.empty:
        lines = {key: [int(i) + 2 for i in group.index] for key, group in repeated.groupby(repeated, sort=False)}  # +2: header, 1-based
        detail = "; ".join(f"{key} (rows {', '.join(map(str, rows))})" for key, rows in lines.items())
        raise ValueError(f"Universe file {path} enables the same ticker more than once: {detail}")


def _optional_text(value: object) -> str:
    return "" if pd.isna(value) else str(value).strip()


def _optional_flag(value: object) -> bool:
    return True if pd.isna(value) else str(value).strip().lower() in {"1", "true", "yes", "y", "1.0"}


def account_candidates(
    members: Iterable[UniverseMember], account: str, benchmark: str | None, cash: str | None
) -> tuple[str, ...]:
    """Ranking candidates for an account type; the benchmark and cash ETF are reference assets only."""
    if account not in ACCOUNT_CHOICES:
        raise ValueError(f"Unsupported account: {account}; choose from {', '.join(ACCOUNT_CHOICES)}")
    allowed = {
        "all": lambda m: True,
        "pension": lambda m: m.pension_ok,
        "retirement": lambda m: m.retirement_ok,
    }[account]
    return tuple(m.ticker for m in members if m.ticker not in {benchmark, cash} and allowed(m))


def _asof_previous_day(series: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """For each date in `index`, the last observation of `series` dated strictly before it."""
    known = series.dropna().sort_index()
    values = known.reindex(index - pd.Timedelta(days=1), method="ffill")
    return pd.Series(values.to_numpy(), index=index)


def _new_since_previous_row(series: pd.Series, index: pd.DatetimeIndex) -> np.ndarray:
    """For each date in `index`: did `series` get an observation (strictly before that date) since the previous date?"""
    known = series.dropna().sort_index()
    source = pd.Series(known.index, index=known.index).reindex(index - pd.Timedelta(days=1), method="ffill")
    values = source.to_numpy()
    previous = np.concatenate([[np.datetime64("NaT")], values[:-1]]).astype(values.dtype)
    return ~pd.isna(values) & (pd.isna(previous) | (values != previous))


def proxy_observations(proxy: pd.Series, fx: pd.Series | None, index: pd.DatetimeIndex) -> pd.Series:
    """Which local dates in `index` a proxy-filled price really observes, under splice_proxies' as-of rule.

    A date counts only when the proxy and the FX rate both have a new value since the previous local
    date, so the freshness check downstream (at most PRICE_MAX_STALE_DAYS rows since the last True)
    applies to each source once. A proxy or FX rate that stopped updating is not observed just because its last
    value is carried forward. On days when only one source is new the row is not counted: this can only
    make the data look staler than it is, never fresher."""
    seen = _new_since_previous_row(proxy, index)
    if fx is not None:
        seen &= _new_since_previous_row(fx, index)
    return pd.Series(seen, index=index)


def splice_proxies(
    frame: pd.DataFrame, proxies: dict[str, pd.Series], fx: pd.Series | None, scale: bool = True
) -> tuple[pd.DataFrame, dict[str, pd.Timestamp]]:
    """Fill each ticker's pre-listing rows with its proxy series converted by `fx` (local units per proxy unit).

    Each local date uses the latest proxy and FX observation from a strictly earlier calendar date:
    a US close for day D is only known after the Korean close on day D, so using it would look
    ahead. This as-of rule is holiday-safe on both calendars. With `scale`
    (prices) it is rescaled to meet the real series on the first listed day; without it (traded
    value) it is only currency-converted. Returns the new frame and each ticker's first real date.
    """
    out = frame.copy()
    starts: dict[str, pd.Timestamp] = {}
    for ticker, proxy in proxies.items():
        if ticker not in out.columns or out[ticker].dropna().empty:
            continue
        first = out[ticker].first_valid_index()
        aligned = _asof_previous_day(proxy, out.index)
        if fx is not None:
            aligned = aligned * _asof_previous_day(fx, out.index)
        if scale:
            if not np.isfinite(aligned.loc[first]) or aligned.loc[first] <= 0:
                continue
            aligned = aligned * (out.loc[first, ticker] / aligned.loc[first])
        before = out.index < first
        out.loc[before, ticker] = aligned.loc[before]
        starts[ticker] = first
    return out, starts


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
    raw: bool = False,
) -> pd.DataFrame:
    """Download adjusted close prices and cache them by ticker set.

    The cache keeps raw closes. By default the result is forward-filled for calculations; with
    ``raw=True`` days without an observation stay NaN (same cache, no extra download).
    A cache with an explicit ``end`` never changes, so it is reused as is. A ``latest`` cache is
    re-downloaded once it is older than ``max_age_hours``; if that download fails, the stale cache
    is used with a warning.
    """
    frame = _cached_download(tickers, start, end, cache_dir, refresh, max_age_hours, kind="prices")
    return frame if raw else _fill_prices(frame)


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
    clean = _clean_raw_prices if kind == "prices" else _clean_traded_value
    prefix = PRICE_CACHE_PREFIX if kind == "prices" else kind

    cache_path = _cache_path(ticker_list, start, end, Path(cache_dir), prefix=prefix)
    stale_cache: Path | None = None
    if cache_path.exists() and not refresh:
        age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
        cached = _read_cache(cache_path, clean)
        missing = _recorded_missing(cache_path) if end is not None else []
        complete = set(ticker_list) <= set(cached.columns) | set(missing)  # older versions cached partial downloads
        complete = complete and not (kind == "prices" and _needs_naver_fill(cache_path, ticker_list))
        if complete and missing:
            return _retry_missing(cache_path, cached, missing, start, end, kind)
        if complete and (end is not None or age_hours <= max_age_hours):
            return cached
        if not (kind == "prices" and _needs_naver_fill(cache_path, ticker_list)):
            stale_cache = cache_path  # an older Naver fill version is wrong, not just old: never a fallback

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
    if kind == "prices" and (invalid := _invalid_prices(frame).sum()).any():
        counts = ", ".join(f"{ticker} ({count})" for ticker, count in invalid[invalid > 0].items())
        warnings.warn(f"Invalid closes (zero, negative or infinite) treated as missing: {counts}", stacklevel=3)
    frame = clean(frame)
    record = None
    if kind == "prices" and _needs_naver_fill(None, ticker_list):
        try:
            frame, report = naver.fill_gaps(frame, fetch=naver.naver_closes)
        except Exception as exc:  # network, format or incomplete-calendar problem: don't freeze the gaps in a cache
            warnings.warn(f"Naver gap fill failed ({exc}); prices not cached, the next run retries", stacklevel=3)
            return frame
        filled = report["filled"]
        record = {
            "version": NAVER_FILL_VERSION,
            "reference": naver.REFERENCE,
            "filled_days": sum(len(days) for days in filled.values()),
            "approximate_days": sum(len(days) for days in report["approximate"].values()),
            "unfilled_days": sum(len(days) for days in report["unfilled"].values()),
            **report,
        }
        if filled:
            print(
                f"Naver closes filled {record['filled_days']} missing Yahoo trading day(s) in {len(filled)} Korean ticker(s)"
                f" ({record['approximate_days']} approximate: a distribution or split fell inside the gap)"
            )
        if report["unfilled"]:
            print(f"Naver has no close for {record['unfilled_days']} missing Yahoo trading day(s); left missing")
    missing = [ticker for ticker in ticker_list if ticker not in frame.columns]
    if missing and end is None:
        # Don't cache a partial latest download: the cache would keep the gap until it expires.
        warnings.warn(f"No {kind} data downloaded for {', '.join(missing)}; not cached, the next run retries", stacklevel=3)
        return frame
    if missing:
        # A pinned period never changes, so keep what came and ask again only for the missing tickers
        # (e.g. an ETF listed after `end` never has data; re-downloading 788 tickers each run is slow).
        warnings.warn(
            f"No {kind} data downloaded for {', '.join(missing)}; cached without them, later runs retry only those",
            stacklevel=3,
        )
    frame.to_csv(cache_path)
    if record is not None:
        _fill_record_path(cache_path).write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    _write_missing_record(cache_path, missing)
    return frame


def _missing_record_path(cache_path: Path) -> Path:
    return cache_path.with_name(cache_path.name + ".missing.json")


def _recorded_missing(cache_path: Path) -> list[str]:
    try:
        return list(json.loads(_missing_record_path(cache_path).read_text(encoding="utf-8"))["missing"])
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _write_missing_record(cache_path: Path, missing: list[str]) -> None:
    path = _missing_record_path(cache_path)
    if missing:
        path.write_text(json.dumps({"missing": sorted(missing)}, ensure_ascii=False) + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


def _retry_missing(
    cache_path: Path, cached: pd.DataFrame, missing: list[str], start: str | None, end: str | None, kind: str
) -> pd.DataFrame:
    """Ask Yahoo again for the tickers a pinned cache lacks; merge any that come, keep the rest recorded."""
    retry_dir = cache_path.parent / ".retry"
    try:
        found = _cached_download(missing, start, end, retry_dir, True, 0.0, kind)
    except Exception as exc:  # still nothing (or no network): the cached tickers are complete as they are
        found = pd.DataFrame()
        reason = f" ({exc})" if not isinstance(exc, RuntimeError) else ""
    else:
        reason = ""
    found = found[[t for t in missing if t in found.columns]] if not found.empty else found
    still = [t for t in missing if t not in found.columns]
    if still:
        warnings.warn(f"Still no {kind} data for {', '.join(still)}{reason}; using the cache without them", stacklevel=4)
    if found.empty:
        return cached
    merged = cached.join(found, how="outer").sort_index()
    merged.to_csv(cache_path)
    retry_record = _fill_record_path(_cache_path(sorted(missing), start, end, retry_dir, prefix=PRICE_CACHE_PREFIX))
    if kind == "prices" and retry_record.exists():
        _merge_fill_record(_fill_record_path(cache_path), json.loads(retry_record.read_text(encoding="utf-8")))
    _write_missing_record(cache_path, still)
    return merged


def _merge_fill_record(path: Path, extra: dict) -> None:
    if not path.exists():
        return
    record = json.loads(path.read_text(encoding="utf-8"))
    for key in ("filled", "approximate", "unfilled"):
        record[key] = {**record.get(key, {}), **extra.get(key, {})}
    for key, part in (("filled_days", "filled"), ("approximate_days", "approximate"), ("unfilled_days", "unfilled")):
        record[key] = sum(len(days) for days in record[part].values())
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _fill_record_path(cache_path: Path) -> Path:
    return cache_path.with_name(cache_path.name + ".naver_fill.json")


def _needs_naver_fill(cache_path: Path | None, tickers: list[str]) -> bool:
    """Korean prices need the Naver fill; with a cache path, only if that cache has no current fill record."""
    if not NAVER_FILL or not any(naver.is_korean(t) for t in tickers):
        return False
    if cache_path is None or not _fill_record_path(cache_path).exists():
        return True
    try:
        record = json.loads(_fill_record_path(cache_path).read_text(encoding="utf-8"))
    except ValueError:
        return True
    return not isinstance(record, dict) or record.get("version") != NAVER_FILL_VERSION


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


def _invalid_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Closes no ETF can have: zero, negative or infinite (e.g. Yahoo's negative adjusted closes)."""
    values = prices.apply(pd.to_numeric, errors="coerce")
    return values.notna() & ~(np.isfinite(values) & (values > 0))


def _clean_raw_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Sorted, invalid closes made missing, without all-empty tickers or days; gaps stay NaN."""
    prices = prices.apply(pd.to_numeric, errors="coerce")
    return prices.mask(_invalid_prices(prices)).sort_index().dropna(axis=1, how="all").dropna(axis=0, how="all")


def _fill_prices(prices: pd.DataFrame) -> pd.DataFrame:
    return prices.ffill()


def _clean_prices(prices: pd.DataFrame) -> pd.DataFrame:
    return _fill_prices(_clean_raw_prices(prices))


def _cache_path(tickers: list[str], start: str | None, end: str | None, cache_dir: Path, prefix: str = "prices") -> Path:
    safe_key = "_".join(tickers).replace("/", "-").replace(".", "_")
    if len(safe_key) > 160:
        digest = hashlib.sha256(",".join(tickers).encode("utf-8")).hexdigest()[:16]
        safe_key = f"{len(tickers)}_tickers_{digest}"
    start_key = start or "earliest"
    end_key = end or "latest"
    return cache_dir / f"{prefix}_{safe_key}_{start_key}_{end_key}.csv"

