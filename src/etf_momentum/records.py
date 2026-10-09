"""Append-only log of month-end selections per strategy (results/strategies/<id>/signals.csv).

Rows are never modified or deleted. Re-running the same month with the same rule and the same result
adds nothing; a different result (e.g. revised market data) is appended as the next revision.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

SIGNAL_COLUMNS = [
    "signal_date", "recorded_at", "strategy_id", "version", "config_hash", "code_commit", "code_dirty",
    "price_last_date", "market_filter", "revision", "status", "rank_in_selection", "ticker", "name", "group", "slots",
]
COMPARED = ["status", "rank_in_selection", "ticker", "market_filter", "slots"]
META_KEYS = ["signal_date", "recorded_at", "strategy_id", "version", "config_hash", "code_commit", "code_dirty", "price_last_date"]


@dataclass(frozen=True)
class SignalRecordResult:
    action: str  # "added" | "unchanged" | "revised"
    revision: int
    changes: list[str]


def record_signal(path: Path, selection: pd.DataFrame, meta: dict[str, object]) -> SignalRecordResult:
    path = Path(path)
    new = _rows(selection, meta)
    existing = _read(path)
    same = existing[(existing["signal_date"] == new["signal_date"].iloc[0]) & (existing["config_hash"] == new["config_hash"].iloc[0])]
    if same.empty:
        _append(path, new.assign(revision="1"), write_header=existing.empty and not path.exists())
        return SignalRecordResult("added", 1, [])
    latest = int(same["revision"].astype(int).max())
    previous = same[same["revision"].astype(int) == latest]
    if _key(previous) == _key(new):
        return SignalRecordResult("unchanged", latest, [])
    revision = latest + 1
    _append(path, new.assign(revision=str(revision)), write_header=False)
    return SignalRecordResult("revised", revision, _describe(previous, new))


INTEGER_COLUMNS = ["version", "rank_in_selection", "slots"]


def _rows(selection: pd.DataFrame, meta: dict[str, object]) -> pd.DataFrame:
    rows = selection.copy()
    for key in META_KEYS:
        rows[key] = meta[key]
    for column in SIGNAL_COLUMNS:
        if column not in rows.columns:
            rows[column] = ""
    for column in INTEGER_COLUMNS:
        # a column with blanks comes in as float; write 1, not 1.0, so reruns compare equal
        rows[column] = rows[column].map(lambda v: "" if v is None or v == "" or pd.isna(v) else str(int(float(v))))
    return rows[SIGNAL_COLUMNS].astype(str).replace({"nan": "", "None": ""}).reset_index(drop=True)


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=SIGNAL_COLUMNS)
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise ValueError(f"Can't read {path}; not overwriting it ({exc})") from exc
    if list(frame.columns) != SIGNAL_COLUMNS:
        # New rows are appended without a header in SIGNAL_COLUMNS order, so the file's header must match exactly.
        raise ValueError(f"{path} columns differ from the expected {', '.join(SIGNAL_COLUMNS)}; not overwriting it")
    _check_rows(path)
    return frame


def _check_rows(path: Path) -> None:
    """Every row must have all SIGNAL_COLUMNS fields; pandas would read a cut-off last row with blanks."""
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            for number, row in enumerate(csv.reader(handle, strict=True), 1):
                if len(row) != len(SIGNAL_COLUMNS):
                    raise ValueError(
                        f"{path} line {number} has {len(row)} fields, expected {len(SIGNAL_COLUMNS)} "
                        "(incomplete record?); not appending to it"
                    )
    except csv.Error as exc:
        raise ValueError(f"{path} is not valid CSV ({exc}); not appending to it") from exc


def _append(path: Path, rows: pd.DataFrame, write_header: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size:
        with path.open("rb") as handle:
            handle.seek(-1, 2)
            complete_last_line = handle.read(1) == b"\n"
        if not complete_last_line:
            # _read checked every row is complete, so only the line break is missing; never join two rows.
            with path.open("a", newline="", encoding="utf-8") as handle:
                handle.write("\n")
    rows.to_csv(path, mode="a", header=write_header, index=False)


def _key(rows: pd.DataFrame) -> list[tuple[str, ...]]:
    return sorted(tuple(row) for row in rows[COMPARED].astype(str).itertuples(index=False))


def _describe(previous: pd.DataFrame, new: pd.DataFrame) -> list[str]:
    changes = []
    for status in ("selected", "group_limit", "cash"):
        old = previous.loc[previous["status"] == status, "ticker"].tolist()
        now = new.loc[new["status"] == status, "ticker"].tolist()
        if old != now:
            changes.append(f"{status}: {', '.join(old) or '-'} → {', '.join(now) or '-'}")
    old_filter, new_filter = previous["market_filter"].iloc[0], new["market_filter"].iloc[0]
    if old_filter != new_filter:
        changes.append(f"market_filter: {old_filter} → {new_filter}")
    return changes or ["순서 또는 빈 자리 수가 바뀜"]
