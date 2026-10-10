"""Fill Yahoo's missing Korean trading days from Naver Finance daily closes.

Yahoo has no close for KODEX 200 on 32 real KRX trading days between 2015 and 2024 (12 in July 2023
alone, including the 2023-07-31 month-end); Naver has them. For each Korean ticker, a trading day
Naver reports (KODEX 200's Naver days are the reference calendar) that falls strictly between the
ticker's first and last Yahoo close but has no Yahoo close gets one: the previous close times Naver's
close-to-close change. Yahoo's adjusted level is kept; only Naver's raw daily change is borrowed.
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


def fill_gaps(frame: pd.DataFrame, fetch: Fetch | None = None) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Fill each Korean ticker's interior missing trading days; returns the frame and the filled dates."""
    fetch = fetch or naver_closes
    korean = [column for column in frame.columns if is_korean(column)]
    if not korean or frame.empty:
        return frame, {}
    sessions = fetch(REFERENCE, frame.index.min(), frame.index.max()).index
    out = frame.copy()
    report: dict[str, list[str]] = {}
    for ticker in korean:
        known = out[ticker].dropna()
        if len(known) < 2:
            continue
        days = sessions[(sessions > known.index[0]) & (sessions < known.index[-1])]
        missing = days.difference(known.index)
        if missing.empty:
            continue
        reference = fetch(ticker, known.index[0], known.index[-1])
        series = known.copy()
        added: list[pd.Timestamp] = []
        for day in missing.sort_values():
            previous = series.index[series.index < day][-1]
            if day in reference.index and previous in reference.index:
                series.loc[day] = series[previous] * reference[day] / reference[previous]
                series = series.sort_index()
                added.append(day)
        if added:
            out = out.reindex(out.index.union(added))
            out.loc[added, ticker] = series.loc[added].to_numpy()
            report[ticker] = [str(day.date()) for day in added]
    return out.sort_index(), report
