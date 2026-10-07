"""Strategy files: one JSON per strategy that fixes every setting deciding which ETFs get picked.

rank, weekly and backtest read the same file with --strategy, so their selection rule can't drift.
config_hash identifies the exact rule (content-based, so a forgotten version bump still shows).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from etf_momentum.data import ACCOUNT_CHOICES
from etf_momentum.signals import RANK_BY_CHOICES

TOP_KEYS = {"id", "version", "description", "universe", "account", "benchmark", "cash", "use_proxies", "signal", "selection"}
SIGNAL_KEYS = {"lookbacks", "weights", "skip_days", "sma_window", "volatility_window", "rank_by", "min_avg_traded_value"}
SELECTION_KEYS = {"top_n", "max_per_group", "risk_on_benchmark", "risk_on_tickers"}
ID_PATTERN = re.compile(r"^[a-z0-9_-]+$")


class StrategyError(ValueError):
    """A strategy file that can't be used; the message names the file and the field."""


@dataclass(frozen=True)
class Strategy:
    id: str
    version: int
    description: str
    universe: str
    account: str
    benchmark: str
    cash: str | None
    use_proxies: bool
    lookbacks: tuple[int, int, int]
    weights: tuple[float, float, float]
    skip_days: int
    sma_window: int
    volatility_window: int
    rank_by: str
    min_avg_traded_value: float | None
    top_n: int
    max_per_group: int
    risk_on_benchmark: bool
    risk_on_tickers: tuple[str, ...] | None
    config_hash: str
    path: str


def strategy_hash(raw: dict) -> str:
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def load_strategy(path: str | Path) -> Strategy:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StrategyError(f"{path}: can't read strategy file ({exc})") from exc
    if not isinstance(raw, dict):
        raise StrategyError(f"{path}: top level must be a JSON object")

    def fail(field: str, problem: str) -> None:
        raise StrategyError(f"{path}: {field} {problem}")

    _check_keys(raw, TOP_KEYS, "", fail)
    signal, selection = raw["signal"], raw["selection"]
    if not isinstance(signal, dict):
        fail("signal", "must be an object")
    if not isinstance(selection, dict):
        fail("selection", "must be an object")
    _check_keys(signal, SIGNAL_KEYS, "signal.", fail)
    _check_keys(selection, SELECTION_KEYS, "selection.", fail)

    if not isinstance(raw["id"], str) or not ID_PATTERN.match(raw["id"]):
        fail("id", "must use only lowercase letters, digits, '-' and '_'")
    version = _int(raw["version"], 1, "version", fail)
    if not isinstance(raw["description"], str):
        fail("description", "must be a string")
    if not isinstance(raw["universe"], str) or not Path(raw["universe"]).is_file():
        fail("universe", f"file not found: {raw['universe']} (paths are relative to the project root)")
    if raw["account"] not in ACCOUNT_CHOICES:
        fail("account", f"must be one of {', '.join(ACCOUNT_CHOICES)}")
    if not isinstance(raw["benchmark"], str) or not raw["benchmark"]:
        fail("benchmark", "must be a ticker")
    if raw["cash"] is not None and (not isinstance(raw["cash"], str) or not raw["cash"]):
        fail("cash", "must be a ticker or null")
    if not isinstance(raw["use_proxies"], bool):
        fail("use_proxies", "must be true or false")

    lookbacks = signal["lookbacks"]
    if not isinstance(lookbacks, list) or len(lookbacks) != 3:
        fail("signal.lookbacks", "must be a list of 3 integers")
    lookbacks = tuple(_int(v, 1, "signal.lookbacks", fail) for v in lookbacks)
    weights = signal["weights"]
    if not isinstance(weights, list) or len(weights) != 3:
        fail("signal.weights", "must be a list of 3 numbers")
    weights = tuple(_number(v, "signal.weights", fail) for v in weights)
    if sum(weights) <= 0:
        fail("signal.weights", "must sum to a positive number")
    if signal["rank_by"] not in RANK_BY_CHOICES:
        fail("signal.rank_by", f"must be one of {', '.join(RANK_BY_CHOICES)}")
    min_value = signal["min_avg_traded_value"]
    if min_value is not None:
        min_value = _number(min_value, "signal.min_avg_traded_value", fail)
        if min_value < 0:
            fail("signal.min_avg_traded_value", "must be >= 0 or null")

    if not isinstance(selection["risk_on_benchmark"], bool):
        fail("selection.risk_on_benchmark", "must be true or false")
    tickers = selection["risk_on_tickers"]
    if tickers is not None:
        if not isinstance(tickers, list) or not tickers or not all(isinstance(t, str) and t for t in tickers):
            fail("selection.risk_on_tickers", "must be a non-empty list of tickers or null")
        tickers = tuple(tickers)

    return Strategy(
        id=raw["id"],
        version=version,
        description=raw["description"],
        universe=raw["universe"],
        account=raw["account"],
        benchmark=raw["benchmark"],
        cash=raw["cash"],
        use_proxies=raw["use_proxies"],
        lookbacks=lookbacks,
        weights=weights,
        skip_days=_int(signal["skip_days"], 0, "signal.skip_days", fail),
        sma_window=_int(signal["sma_window"], 1, "signal.sma_window", fail),
        volatility_window=_int(signal["volatility_window"], 1, "signal.volatility_window", fail),
        rank_by=signal["rank_by"],
        min_avg_traded_value=min_value,
        top_n=_int(selection["top_n"], 1, "selection.top_n", fail),
        max_per_group=_int(selection["max_per_group"], 0, "selection.max_per_group", fail),
        risk_on_benchmark=selection["risk_on_benchmark"],
        risk_on_tickers=tickers,
        config_hash=strategy_hash(raw),
        path=str(path),
    )


def _check_keys(section: dict, expected: set[str], prefix: str, fail) -> None:
    unknown = sorted(set(section) - expected)
    missing = sorted(expected - set(section))
    if unknown:
        fail(prefix + unknown[0], "is not a known field")
    if missing:
        fail(prefix + missing[0], "is missing")


def _int(value, minimum: int, field: str, fail) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        fail(field, f"must be an integer >= {minimum}")
    return value


def _number(value, field: str, fail) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        fail(field, "must be a finite number")
    return float(value)
