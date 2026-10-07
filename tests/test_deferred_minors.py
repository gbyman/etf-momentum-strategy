from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd
import pytest

import etf_momentum.cli as cli
from etf_momentum.strategy import StrategyError, load_strategy
from etf_momentum.weekly import append_strategy_snapshot, load_strategy_snapshot


def ranking(as_of: str, *tickers: str) -> pd.DataFrame:
    return pd.DataFrame({"as_of": pd.Timestamp(as_of), "ticker": list(tickers), "rank": range(1, len(tickers) + 1)})


# minor 1: an unparseable date in weekly_history.csv is a clean "can't read" stop, not a traceback
def test_weekly_history_with_bad_date_stops_cleanly(tmp_path) -> None:
    path = tmp_path / "weekly_history.csv"
    path.write_text("as_of,ticker,rank,version,config_hash\ngarbage,A,1,1,h1\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="weekly_history.csv"):
        load_strategy_snapshot(path, pd.Timestamp("2026-10-02"), config_hash="h1", version=1)
    assert path.read_bytes() == before


# minor 2: the notice names every rule version recorded on the latest earlier day
def test_rule_change_notice_lists_all_previous_versions(tmp_path) -> None:
    path = tmp_path / "weekly_history.csv"
    append_strategy_snapshot(path, ranking("2026-10-02", "A"), version=2, config_hash="zz")
    append_strategy_snapshot(path, ranking("2026-10-02", "B"), version=1, config_hash="aa")
    _, notice = load_strategy_snapshot(path, pd.Timestamp("2026-10-09"), config_hash="h3", version=3)
    assert "1, 2→3" in notice


# minor 3: --end today or later is a live run (recorded, judged as of today); only a past --end skips recording
@pytest.mark.parametrize("end, past", [("2000-01-03", True), ("2999-01-01", False), (None, False)])
def test_only_a_past_end_date_is_a_past_lookup(end, past) -> None:
    today = cli._selection_today(argparse.Namespace(end=end))
    assert (today is not None) == past


# minor 4: code_dirty is lowercase true/false and recorded_at carries the local UTC offset
def test_signal_meta_formats(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_code_revision", lambda: {"commit": "abc", "dirty": True})
    meta = cli._signal_meta(
        pd.DataFrame({"as_of": ["2026-09-30"]}),
        pd.DataFrame({"A": [1.0]}, index=pd.to_datetime(["2026-10-07"])),
        argparse.Namespace(),
        type("S", (), {"id": "p", "version": 1, "config_hash": "h"})(),
    )
    assert meta["code_dirty"] == "true"
    from datetime import datetime

    assert meta["recorded_at"].replace(":", "").endswith(datetime.now().astimezone().strftime("%z"))


# minor 5: the universe-not-found message says paths are relative to the current directory
def test_universe_path_hint_mentions_current_directory(tmp_path) -> None:
    raw = json.loads(open("config/strategies/pension.json").read())
    raw["universe"] = "no/such/universe.csv"
    path = tmp_path / "s.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(StrategyError, match="current directory"):
        load_strategy(path)
