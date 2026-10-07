from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
import pytest

import etf_momentum.backtest as backtest
import etf_momentum.cli as cli


def prices() -> pd.DataFrame:
    index = pd.bdate_range(end="2026-10-06", periods=700)
    steps = np.arange(index.size)
    return pd.DataFrame(
        {
            "A": 100 * np.power(1.0020, steps),
            "B": 100 * np.power(1.0018, steps),
            "C": 100 * np.power(1.0010, steps),
            "D": np.linspace(100, 80, index.size),
            "BENCH": 100 * np.power(1.0005, steps),
            "CASH": 100 * np.power(1.0001, steps),
        },
        index=index,
    )


def strategy_file(tmp_path, **selection) -> str:
    universe = tmp_path / "u.csv"
    universe.write_text(
        "ticker,name,category,enabled,group\nA,Alpha,X,1,g1\nB,Beta,X,1,g1\nC,Gamma,X,1,\nD,Delta,X,1,\nCASH,Cash,Cash,1,\n"
    )
    raw = {
        "id": "test",
        "version": 1,
        "description": "test",
        "universe": str(universe),
        "account": "all",
        "benchmark": "BENCH",
        "cash": "CASH",
        "use_proxies": False,
        "signal": {
            "lookbacks": [63, 126, 252], "weights": [0.3, 0.3, 0.4], "skip_days": 21,
            "sma_window": 200, "volatility_window": 63, "rank_by": "weighted_momentum", "min_avg_traded_value": None,
        },
        "selection": {"top_n": 3, "max_per_group": 1, "risk_on_benchmark": True, "risk_on_tickers": None, **selection},
    }
    path = tmp_path / "test.json"
    path.write_text(json.dumps(raw))
    return str(path)


@pytest.fixture
def offline(monkeypatch):
    data = prices()
    monkeypatch.setattr(cli, "download_prices", lambda tickers, **kw: data[[t for t in tickers if t in data]])
    monkeypatch.setattr(
        cli, "previous_month_end", lambda dates, today=None: backtest.previous_month_end(dates, pd.Timestamp("2026-10-07"))
    )
    return data


def run(monkeypatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["etf-momentum", *argv])
    cli.main()


@pytest.mark.parametrize("extra", [["--top-n", "2"], ["--top-n=2"], ["--risk-on-benchmark"], ["--benchmark", "SPY"], ["--proxy-fx", "X"]])
def test_strategy_with_selection_option_is_a_usage_error(tmp_path, monkeypatch, extra) -> None:
    def no_download(*args, **kwargs):
        raise AssertionError("must stop before downloading")

    monkeypatch.setattr(cli, "download_prices", no_download)
    with pytest.raises(SystemExit) as exc:
        run(monkeypatch, "rank", "--strategy", strategy_file(tmp_path), "--output-dir", str(tmp_path / "o"), *extra)
    assert exc.value.code == 2


def test_weekly_history_file_conflicts_with_strategy(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "download_prices", lambda *a, **k: (_ for _ in ()).throw(AssertionError("download")))
    with pytest.raises(SystemExit) as exc:
        run(monkeypatch, "weekly", "--strategy", strategy_file(tmp_path), "--history-file", str(tmp_path / "h.csv"))
    assert exc.value.code == 2


def test_invalid_strategy_file_stops_before_download(tmp_path, monkeypatch) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    monkeypatch.setattr(cli, "download_prices", lambda *a, **k: (_ for _ in ()).throw(AssertionError("download")))
    with pytest.raises(SystemExit, match="bad.json"):
        run(monkeypatch, "rank", "--strategy", str(bad))


def test_rank_and_backtest_pick_the_same_etfs_from_one_strategy_file(tmp_path, monkeypatch, offline) -> None:
    path = strategy_file(tmp_path)
    run(monkeypatch, "rank", "--strategy", path, "--output-dir", str(tmp_path / "rank"))
    run(monkeypatch, "backtest", "--strategy", path, "--output-dir", str(tmp_path / "bt"))

    selection = pd.read_csv(tmp_path / "rank" / "latest_selection.csv")
    picked = selection.loc[selection["status"] == "selected", "ticker"].tolist()
    assert picked == ["A", "C"]  # B shares A's group; D is in a downtrend

    positions = pd.read_csv(tmp_path / "bt" / "positions.csv")
    held = positions.loc[positions["rebalance_date"] == "2026-09-30", "tickers"].iloc[0].split(",")
    assert sorted(t for t in held if t != "CASH") == sorted(picked)


def test_without_strategy_commands_behave_as_before(tmp_path, monkeypatch, offline) -> None:
    universe = tmp_path / "u2.csv"
    universe.write_text("ticker,name,category,enabled\nA,Alpha,X,1\nC,Gamma,X,1\n")
    run(monkeypatch, "rank", "--universe", str(universe), "--benchmark", "BENCH", "--cash", "CASH",
        "--top-n", "2", "--output-dir", str(tmp_path / "plain"))
    assert (tmp_path / "plain" / "latest_selection.csv").exists()
    assert not (tmp_path / "plain" / "strategies").exists()


def test_rank_with_strategy_records_signal_once(tmp_path, monkeypatch, offline) -> None:
    path = strategy_file(tmp_path)
    out = tmp_path / "out"
    run(monkeypatch, "rank", "--strategy", path, "--output-dir", str(out))
    signals = out / "strategies" / "test" / "signals.csv"
    first = pd.read_csv(signals, dtype=str, keep_default_na=False)
    assert set(first["signal_date"]) == {"2026-09-30"}
    assert first.loc[first["status"] == "selected", "ticker"].tolist() == ["A", "C"]
    assert set(first["market_filter"]) == {"on"}
    before = signals.read_bytes()
    run(monkeypatch, "rank", "--strategy", path, "--output-dir", str(out))
    assert signals.read_bytes() == before  # same month, same rule, same result


def test_rank_with_past_end_date_does_not_record(tmp_path, monkeypatch, offline, capsys) -> None:
    out = tmp_path / "out"
    run(monkeypatch, "rank", "--strategy", strategy_file(tmp_path), "--end", "2026-09-15", "--output-dir", str(out))
    assert not (out / "strategies" / "test" / "signals.csv").exists()
    assert "기록하지 않습니다" in capsys.readouterr().out
