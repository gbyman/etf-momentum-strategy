from __future__ import annotations

import pandas as pd
import pytest

from etf_momentum.records import SIGNAL_COLUMNS, record_signal


def selection(*tickers: str, cash_slots: int = 0) -> pd.DataFrame:
    rows = [{"status": "selected", "rank_in_selection": i, "ticker": t, "name": t.lower(), "group": "", "slots": ""}
            for i, t in enumerate(tickers, 1)]
    if cash_slots:
        rows.append({"status": "cash", "rank_in_selection": "", "ticker": "CASH", "name": "cash", "group": "", "slots": cash_slots})
    frame = pd.DataFrame(rows)
    frame["market_filter"] = "on"
    return frame


META = {
    "signal_date": "2026-09-30", "recorded_at": "2026-10-08T09:00:00+09:00", "strategy_id": "pension",
    "version": 1, "config_hash": "abc123abc123", "code_commit": "deadbeef", "code_dirty": False,
    "price_last_date": "2026-10-07",
}


def test_first_record_is_revision_one_with_all_columns(tmp_path) -> None:
    path = tmp_path / "signals.csv"
    result = record_signal(path, selection("A", "C"), META)
    assert (result.action, result.revision) == ("added", 1)
    saved = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert list(saved.columns) == SIGNAL_COLUMNS
    assert saved["ticker"].tolist() == ["A", "C"] and set(saved["revision"]) == {"1"}


def test_same_result_again_adds_nothing(tmp_path) -> None:
    path = tmp_path / "signals.csv"
    record_signal(path, selection("A", "C"), META)
    before = path.read_bytes()
    result = record_signal(path, selection("A", "C"), {**META, "recorded_at": "2026-10-09T09:00:00+09:00"})
    assert result.action == "unchanged" and path.read_bytes() == before


def test_changed_result_appends_revision_and_keeps_original_bytes(tmp_path) -> None:
    path = tmp_path / "signals.csv"
    record_signal(path, selection("A", "C"), META)
    before = path.read_bytes()
    result = record_signal(path, selection("A", "D", cash_slots=1), META)
    assert (result.action, result.revision) == ("revised", 2)
    assert any("C" in change and "D" in change for change in result.changes)
    after = path.read_bytes()
    assert after.startswith(before)  # append-only
    saved = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert saved.loc[saved["revision"] == "2", "ticker"].tolist() == ["A", "D", "CASH"]


def test_new_rule_hash_is_recorded_separately(tmp_path) -> None:
    path = tmp_path / "signals.csv"
    record_signal(path, selection("A", "C"), META)
    result = record_signal(path, selection("A", "C"), {**META, "config_hash": "fff000fff000", "version": 2})
    assert (result.action, result.revision) == ("added", 1)


def test_unreadable_record_file_stops_and_is_not_overwritten(tmp_path) -> None:
    path = tmp_path / "signals.csv"
    path.write_text("signal_date,ticker\n2026-09-30,A\n")  # columns edited by hand
    before = path.read_bytes()
    with pytest.raises(ValueError, match="signals.csv"):
        record_signal(path, selection("A"), META)
    assert path.read_bytes() == before
