"""Fill Yahoo's missing Korean trading days from Naver Finance daily closes.

Yahoo has no close for KODEX 200 on 32 real KRX trading days between 2015 and 2024 (12 in July 2023
alone, including the 2023-07-31 month-end); Naver has them. For each Korean ticker, a trading day
Naver reports (KODEX 200's Naver days are the reference calendar) that falls strictly between the
ticker's first and last Yahoo close but has no Yahoo close gets one: Naver's raw close times Yahoo's
adjustment factor (adjusted / raw close) on the Yahoo days around the gap. When the factors before
and after the gap differ, a distribution or split happened somewhere inside it; the day is filled
with the factor after the gap and recorded as approximate (off by at most that factor change).
Days before the first or after the last Yahoo close are never filled (no pre-listing values, no
intraday prices for today).
"""

from __future__ import annotations

import ast
import time
import urllib.request
from typing import Callable

import pandas as pd

NAVER_DAILY = (
    "https://api.finance.naver.com/siseJson.naver?symbol={code}&requestType=1"
    "&startTime={start}&endTime={end}&timeframe=day"
)
REFERENCE = "069500.KS"  # KODEX 200: its Naver trading days are the KRX calendar used here
REQUEST_PAUSE_SECONDS = 0.2
# Adjustment factors (Yahoo adjusted / Naver raw) on both sides of a gap that differ by more than this
# mean a distribution or split inside the gap (Yahoo's rounding moves the factor far less).
ADJUSTMENT_TOLERANCE = 0.0005

Fetch = Callable[[str, pd.Timestamp, pd.Timestamp], pd.Series]


def is_korean(ticker: str) -> bool:
    return str(ticker).upper().endswith((".KS", ".KQ"))


def naver_closes(ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Raw daily closes from Naver for start..end (both inclusive)."""
    url = NAVER_DAILY.format(
        code=ticker.split(".")[0], start=pd.Timestamp(start).strftime("%Y%m%d"), end=pd.Timestamp(end).strftime("%Y%m%d")
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    text = urllib.request.urlopen(request, timeout=30).read().decode("utf-8")
    time.sleep(REQUEST_PAUSE_SECONDS)
    rows = ast.literal_eval(text.strip())
    if not rows or rows[0][:5] != ["날짜", "시가", "고가", "저가", "종가"]:
        raise ValueError(f"Unexpected Naver daily price format for {ticker}")
    closes = pd.Series([float(row[4]) for row in rows[1:]], index=pd.to_datetime([row[0] for row in rows[1:]]), dtype=float)
    return closes[closes > 0].sort_index()


def fill_gaps(frame: pd.DataFrame, fetch: Fetch | None = None) -> tuple[pd.DataFrame, dict[str, dict]]:
    """Fill each Korean ticker's interior missing trading days.

    Returns the frame and a report: ``filled`` (dates per ticker), ``approximate`` (date -> adjustment
    factor change across the gap) and ``unfilled`` (missing days Naver has no close for). Raises
    ValueError if the reference calendar does not contain every day Yahoo has a Korean close for
    (an empty or cut-off Naver response), so the caller does not cache a half-filled result.
    """
    fetch = fetch or naver_closes
    report: dict[str, dict] = {"filled": {}, "approximate": {}, "unfilled": {}}
    korean = [column for column in frame.columns if is_korean(column)]
    if not korean or frame.empty:
        return frame, report
    sessions = fetch(REFERENCE, frame.index.min(), frame.index.max()).index
    yahoo_days = frame[korean].dropna(how="all").index
    absent = yahoo_days.difference(sessions)
    if not absent.empty:
        raise ValueError(
            f"Naver reference calendar ({REFERENCE}) lacks {len(absent)} Yahoo trading day(s), "
            f"e.g. {', '.join(str(day.date()) for day in absent[:3])}"
        )
    out = frame.copy()
    for ticker in korean:
        known = out[ticker].dropna()
        if len(known) < 2:
            continue
        days = sessions[(sessions > known.index[0]) & (sessions < known.index[-1])]
        missing = days.difference(known.index)
        if missing.empty:
            continue
        raw = fetch(ticker, known.index[0], known.index[-1])
        added: dict[pd.Timestamp, float] = {}
        approximate: dict[str, float] = {}
        unfilled: list[str] = []
        for day in missing:
            previous = known.index[known.index < day][-1]
            following = known.index[known.index > day][0]
            if not {day, previous, following} <= set(raw.index):
                unfilled.append(str(day.date()))
                continue
            before = known[previous] / raw[previous]
            after = known[following] / raw[following]
            added[day] = raw[day] * after
            change = after / before - 1
            if abs(change) > ADJUSTMENT_TOLERANCE:
                approximate[str(day.date())] = float(change)
        if added:
            out = out.reindex(out.index.union(list(added)))
            out.loc[list(added), ticker] = list(added.values())
            report["filled"][ticker] = [str(day.date()) for day in added]
        if approximate:
            report["approximate"][ticker] = approximate
        if unfilled:
            report["unfilled"][ticker] = unfilled
    return out.sort_index(), report
