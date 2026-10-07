from __future__ import annotations

import json

import pytest

from etf_momentum.strategy import StrategyError, load_strategy, strategy_hash


def valid(universe: str) -> dict:
    return {
        "id": "pension",
        "version": 1,
        "description": "test",
        "universe": universe,
        "account": "pension",
        "benchmark": "069500.KS",
        "cash": "153130.KS",
        "use_proxies": True,
        "signal": {
            "lookbacks": [63, 126, 252],
            "weights": [0.3, 0.3, 0.4],
            "skip_days": 21,
            "sma_window": 200,
            "volatility_window": 63,
            "rank_by": "weighted_momentum",
            "min_avg_traded_value": 1e9,
        },
        "selection": {"top_n": 4, "max_per_group": 1, "risk_on_benchmark": True, "risk_on_tickers": None},
    }


@pytest.fixture
def universe(tmp_path):
    path = tmp_path / "u.csv"
    path.write_text("ticker,name,category,enabled\nA,A,X,1\n")
    return str(path)


def write(tmp_path, raw) -> str:
    path = tmp_path / "s.json"
    path.write_text(json.dumps(raw) if isinstance(raw, dict) else raw)
    return str(path)


def test_valid_file_loads_with_typed_fields(tmp_path, universe) -> None:
    strategy = load_strategy(write(tmp_path, valid(universe)))
    assert strategy.id == "pension" and strategy.version == 1
    assert strategy.lookbacks == (63, 126, 252) and strategy.weights == (0.3, 0.3, 0.4)
    assert strategy.top_n == 4 and strategy.risk_on_tickers is None
    assert strategy.min_avg_traded_value == 1e9
    assert len(strategy.config_hash) == 12


def test_hash_ignores_whitespace_and_key_order(tmp_path, universe) -> None:
    raw = valid(universe)
    compact = write(tmp_path, json.dumps(raw))
    spaced_path = tmp_path / "spaced.json"
    spaced_path.write_text(json.dumps(dict(reversed(list(raw.items()))), indent=4))
    assert load_strategy(compact).config_hash == load_strategy(spaced_path).config_hash


def test_hash_changes_when_a_rule_changes(universe) -> None:
    raw = valid(universe)
    changed = valid(universe)
    changed["selection"]["top_n"] = 3
    assert strategy_hash(raw) != strategy_hash(changed)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda r: r.update(extra=1), "extra"),  # unknown top-level field (typo guard)
        (lambda r: r["signal"].update(skipdays=21), "skipdays"),  # unknown nested field
        (lambda r: r.pop("benchmark"), "benchmark"),  # missing field
        (lambda r: r.update(id="Pension!"), "id"),  # not a safe folder name
        (lambda r: r.update(version=0), "version"),
        (lambda r: r["selection"].update(top_n=0), "top_n"),
        (lambda r: r["selection"].update(max_per_group=-1), "max_per_group"),
        (lambda r: r["signal"].update(lookbacks=[63, 126]), "lookbacks"),
        (lambda r: r["signal"].update(weights=[0, 0, 0]), "weights"),
        (lambda r: r["signal"].update(rank_by="sharpe"), "rank_by"),
        (lambda r: r["signal"].update(min_avg_traded_value=-1), "min_avg_traded_value"),
        (lambda r: r.update(account="brokerage"), "account"),
        (lambda r: r.update(use_proxies="yes"), "use_proxies"),
        (lambda r: r["selection"].update(risk_on_tickers=[]), "risk_on_tickers"),
    ],
)
def test_invalid_values_are_rejected_with_the_field_name(tmp_path, universe, mutate, message) -> None:
    raw = valid(universe)
    mutate(raw)
    with pytest.raises(StrategyError, match=message):
        load_strategy(write(tmp_path, raw))


def test_missing_universe_file_is_rejected(tmp_path) -> None:
    with pytest.raises(StrategyError, match="universe"):
        load_strategy(write(tmp_path, valid(str(tmp_path / "nope.csv"))))


def test_broken_json_names_the_file(tmp_path, universe) -> None:
    path = write(tmp_path, json.dumps(valid(universe))[:-1] + ",}")
    with pytest.raises(StrategyError, match="s.json"):
        load_strategy(path)
