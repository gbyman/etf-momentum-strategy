from __future__ import annotations

import pandas as pd

from etf_momentum.weekly import append_snapshot, build_weekly_report, load_previous_snapshot


def test_weekly_report_flags_leadership_changes() -> None:
    previous = pd.DataFrame(
        [
            {
                "as_of": pd.Timestamp("2026-01-03"),
                "ticker": "OLD",
                "name": "Old Leader",
                "category": "Sector",
                "rank": 2,
                "return_1m": 0.20,
            },
            {
                "as_of": pd.Timestamp("2026-01-03"),
                "ticker": "KEEP",
                "name": "Persistent Leader",
                "category": "Sector",
                "rank": 3,
                "return_1m": 0.15,
            },
        ]
    )
    current = pd.DataFrame(
        [
            {
                "as_of": pd.Timestamp("2026-01-10"),
                "ticker": "NEW",
                "name": "New Leader",
                "category": "Sector",
                "rank": 1,
                "return_1w": 0.05,
                "return_1m": 0.18,
                "return_3m": 0.40,
                "weighted_momentum": 0.30,
                "trend_strength_vs_sma": 0.12,
            },
            {
                "as_of": pd.Timestamp("2026-01-10"),
                "ticker": "KEEP",
                "name": "Persistent Leader",
                "category": "Sector",
                "rank": 4,
                "return_1w": 0.01,
                "return_1m": 0.12,
                "return_3m": 0.25,
                "weighted_momentum": 0.20,
                "trend_strength_vs_sma": 0.08,
            },
            {
                "as_of": pd.Timestamp("2026-01-10"),
                "ticker": "OLD",
                "name": "Old Leader",
                "category": "Sector",
                "rank": 12,
                "return_1w": -0.03,
                "return_1m": 0.10,
                "return_3m": 0.15,
                "weighted_momentum": 0.05,
                "trend_strength_vs_sma": 0.02,
            },
        ]
    )

    report = build_weekly_report(current, previous, watch_rank=10, leader_rank=5)
    by_ticker = report.set_index("ticker")

    assert bool(by_ticker.loc["NEW", "new_or_rising"])
    assert bool(by_ticker.loc["KEEP", "still_top"])
    assert bool(by_ticker.loc["OLD", "leader_fading"])
    assert bool(by_ticker.loc["OLD", "one_month_rolling_over"])


def test_snapshot_history_roundtrip(tmp_path) -> None:
    path = tmp_path / "rank_history.csv"
    first = pd.DataFrame(
        [
            {
                "as_of": pd.Timestamp("2026-01-03"),
                "ticker": "AAA",
                "name": "A",
                "category": "Sector",
                "rank": 1,
                "eligible": True,
                "weighted_momentum": 0.2,
                "return_1w": 0.01,
                "return_1m": 0.05,
                "return_3m": 0.10,
                "return_6m": 0.20,
                "return_12m": 0.30,
                "return_ytd": 0.02,
                "trend_strength_vs_sma": 0.08,
                "relative_to_benchmark": 0.04,
            }
        ]
    )
    second = first.copy()
    second["as_of"] = pd.Timestamp("2026-01-10")
    second["rank"] = 2

    append_snapshot(path, first)
    append_snapshot(path, second)
    previous = load_previous_snapshot(path, pd.Timestamp("2026-01-10"))

    assert len(previous) == 1
    assert previous.iloc[0]["rank"] == 1

