from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


SNAPSHOT_COLUMNS = [
    "as_of",
    "ticker",
    "name",
    "category",
    "rank",
    "eligible",
    "weighted_momentum",
    "return_1w",
    "return_1m",
    "return_3m",
    "return_6m",
    "return_12m",
    "return_ytd",
    "trend_strength_vs_sma",
    "relative_to_benchmark",
    "liquid",
    "avg_traded_value",
]


def load_previous_snapshot(history_path: str | Path, current_as_of: pd.Timestamp) -> pd.DataFrame:
    path = Path(history_path)
    if not path.exists():
        return pd.DataFrame()
    history = pd.read_csv(path, parse_dates=["as_of"])
    if history.empty:
        return pd.DataFrame()
    previous_dates = history.loc[history["as_of"] < current_as_of, "as_of"]
    if previous_dates.empty:
        return pd.DataFrame()
    previous_as_of = previous_dates.max()
    return history[history["as_of"] == previous_as_of].copy()


def append_snapshot(history_path: str | Path, ranking: pd.DataFrame) -> None:
    path = Path(history_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = ranking[[column for column in SNAPSHOT_COLUMNS if column in ranking.columns]].copy()
    if snapshot.empty:
        return

    if path.exists():
        history = pd.read_csv(path, parse_dates=["as_of"])
        current_as_of = pd.Timestamp(snapshot["as_of"].iloc[0])
        history = history[history["as_of"] != current_as_of]
        history = pd.concat([history, snapshot], ignore_index=True)
    else:
        history = snapshot
    history = history.sort_values(["as_of", "rank"])
    history.to_csv(path, index=False)


def build_weekly_report(
    current: pd.DataFrame,
    previous: pd.DataFrame,
    watch_rank: int = 10,
    leader_rank: int = 5,
    rise_threshold: int = 3,
    drop_threshold: int = 3,
) -> pd.DataFrame:
    if current.empty:
        return pd.DataFrame()

    current_indexed = current.set_index("ticker", drop=False)
    previous_indexed = previous.set_index("ticker", drop=False) if not previous.empty else pd.DataFrame()
    current_tickers = set(current[current["rank"] <= watch_rank]["ticker"])
    previous_tickers = set()
    if not previous.empty:
        previous_tickers = set(previous[previous["rank"] <= watch_rank]["ticker"])
    tickers = sorted(current_tickers | previous_tickers, key=lambda ticker: _sort_key(ticker, current_indexed, previous_indexed))

    rows: list[dict[str, object]] = []
    for ticker in tickers:
        current_row = _row_or_none(current_indexed, ticker)
        previous_row = _row_or_none(previous_indexed, ticker)
        if current_row is None and previous_row is None:
            continue

        rank = _value(current_row, "rank")
        previous_rank = _value(previous_row, "rank")
        rank_change = _rank_change(rank, previous_rank)
        return_1w = _value(current_row, "return_1w")
        return_1m = _value(current_row, "return_1m")
        previous_return_1m = _value(previous_row, "return_1m")

        still_top = _leq(rank, leader_rank) and _leq(previous_rank, leader_rank)
        newly_top = _leq(rank, watch_rank) and (previous_row is None or not _leq(previous_rank, watch_rank))
        rising_fast = np.isfinite(rank_change) and rank_change >= rise_threshold
        leader_fading = (
            previous_row is not None
            and _leq(previous_rank, watch_rank)
            and (current_row is None or not _leq(rank, watch_rank) or (np.isfinite(rank_change) and rank_change <= -drop_threshold))
        )
        one_month_rolling_over = _one_month_rolling_over(return_1w, return_1m, previous_return_1m)

        rows.append(
            {
                "ticker": ticker,
                "name": _value(current_row, "name") or _value(previous_row, "name"),
                "category": _value(current_row, "category") or _value(previous_row, "category"),
                "rank": rank,
                "previous_rank": previous_rank,
                "rank_change": rank_change,
                "still_top": still_top,
                "new_or_rising": bool(newly_top or rising_fast),
                "leader_fading": bool(leader_fading),
                "one_month_rolling_over": bool(one_month_rolling_over),
                "return_1w": return_1w,
                "return_1m": return_1m,
                "return_3m": _value(current_row, "return_3m"),
                "weighted_momentum": _value(current_row, "weighted_momentum"),
                "trend_strength_vs_sma": _value(current_row, "trend_strength_vs_sma"),
                "liquid": _value(current_row, "liquid"),
                "avg_traded_value": _value(current_row, "avg_traded_value"),
                "status": _status(still_top, newly_top, rising_fast, leader_fading, one_month_rolling_over),
            }
        )

    report = pd.DataFrame(rows)
    if report.empty:
        return report
    return report.sort_values(["rank", "previous_rank"], na_position="last").reset_index(drop=True)


def _row_or_none(frame: pd.DataFrame, ticker: str) -> pd.Series | None:
    if frame.empty or ticker not in frame.index:
        return None
    row = frame.loc[ticker]
    if isinstance(row, pd.DataFrame):
        return row.iloc[0]
    return row


def _value(row: pd.Series | None, column: str) -> object:
    if row is None or column not in row.index:
        return np.nan
    return row[column]


def _rank_change(rank: object, previous_rank: object) -> float:
    if not _finite_number(rank) or not _finite_number(previous_rank):
        return np.nan
    return float(previous_rank) - float(rank)


def _one_month_rolling_over(return_1w: object, return_1m: object, previous_return_1m: object) -> bool:
    if _finite_number(return_1m) and float(return_1m) < 0:
        return True
    if _finite_number(return_1w) and _finite_number(return_1m) and _finite_number(previous_return_1m):
        return float(return_1w) < 0 and float(return_1m) < float(previous_return_1m)
    return False


def _status(
    still_top: bool,
    newly_top: bool,
    rising_fast: bool,
    leader_fading: bool,
    one_month_rolling_over: bool,
) -> str:
    labels: list[str] = []
    if still_top:
        labels.append("still_top")
    if newly_top:
        labels.append("new_top")
    elif rising_fast:
        labels.append("rising")
    if leader_fading:
        labels.append("fading")
    if one_month_rolling_over:
        labels.append("1m_rollover")
    return ",".join(labels) if labels else "watch"


def _sort_key(ticker: str, current: pd.DataFrame, previous: pd.DataFrame) -> tuple[float, float, str]:
    current_rank = _value(_row_or_none(current, ticker), "rank")
    previous_rank = _value(_row_or_none(previous, ticker), "rank")
    current_key = float(current_rank) if _finite_number(current_rank) else np.inf
    previous_key = float(previous_rank) if _finite_number(previous_rank) else np.inf
    return current_key, previous_key, ticker


def _leq(value: object, threshold: int) -> bool:
    return _finite_number(value) and float(value) <= threshold


def _finite_number(value: object) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


STRATEGY_COLUMNS = ["version", "config_hash"]


def load_strategy_snapshot(
    history_path: str | Path, current_as_of: pd.Timestamp, config_hash: str, version: int
) -> tuple[pd.DataFrame, str | None]:
    """Most recent earlier snapshot of the same rule; if the most recent earlier day was taken under a
    different rule, nothing to compare against and a notice saying so."""
    history = _read_strategy_history(Path(history_path))
    earlier = history[history["as_of"] < current_as_of]
    if earlier.empty:
        return pd.DataFrame(), None
    latest_day = earlier[earlier["as_of"] == earlier["as_of"].max()]
    same = latest_day[latest_day["config_hash"] == config_hash]
    if not same.empty:
        return same.copy(), None
    versions = sorted({int(v) for v in latest_day["version"]})
    if versions == [version]:
        return pd.DataFrame(), f"설정 내용(유니버스 등)이 바뀌어(버전 {version} 그대로) 새 기준으로 시작합니다"
    old_versions = ", ".join(str(v) for v in versions)
    return pd.DataFrame(), f"설정이 바뀌어(버전 {old_versions}→{version}) 새 기준으로 시작합니다"


def append_strategy_snapshot(history_path: str | Path, ranking: pd.DataFrame, version: int, config_hash: str) -> None:
    """Add today's snapshot for this rule; a rerun on the same day replaces only this rule's rows."""
    path = Path(history_path)
    snapshot = ranking[[c for c in SNAPSHOT_COLUMNS if c in ranking.columns]].copy()
    if snapshot.empty:
        return
    snapshot["version"] = version
    snapshot["config_hash"] = config_hash
    history = _read_strategy_history(path)
    as_of = pd.Timestamp(snapshot["as_of"].iloc[0])
    history = history[~((history["as_of"] == as_of) & (history["config_hash"] == config_hash))]
    combined = pd.concat([history, snapshot], ignore_index=True).sort_values(["as_of", "config_hash", "rank"])
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(path, index=False)


def _read_strategy_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["as_of", "ticker", "rank", *STRATEGY_COLUMNS])
    try:
        history = pd.read_csv(path, parse_dates=["as_of"], dtype={"config_hash": str})
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise ValueError(f"Can't read {path}; not overwriting it ({exc})") from exc
    missing = [c for c in ["as_of", "ticker", "rank", *STRATEGY_COLUMNS] if c not in history.columns]
    if missing:
        raise ValueError(f"{path} is missing columns {', '.join(missing)}; not overwriting it")
    if history.empty:
        history["as_of"] = pd.to_datetime(history["as_of"])
    elif not pd.api.types.is_datetime64_any_dtype(history["as_of"]):
        raise ValueError(f"{path} has as_of values that are not dates; not overwriting it")
    return history
