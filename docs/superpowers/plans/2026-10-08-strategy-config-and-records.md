# 전략 설정 파일과 전략별 기록 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `rank`·`weekly`·`backtest`가 같은 전략 파일(`--strategy`)로 같은 선정 규칙을 쓰고, 주간 기록과 월말 신호 기록을 전략별로 덮어쓰지 않게 남긴다.

**Architecture:** 새 모듈 `strategy.py`가 전략 JSON을 검증·해시하고, CLI는 그 값을 기존 `argparse.Namespace`에 덮어써서 지금의 실행 경로를 그대로 쓴다. 새 모듈 `records.py`가 `signals.csv`를 추가 전용으로 기록하고, `weekly.py`에 해시별 주간 기록 함수를 더한다. `--strategy` 없이 실행하면 지금과 똑같이 동작한다.

**Tech Stack:** Python 3.11+, pandas, argparse, pytest, uv (`uv run --offline pytest`).

**Spec:** `docs/superpowers/specs/2026-10-08-strategy-config-and-records-design.md`

## Global Constraints

- 전략 파일 위치: `config/strategies/<id>.json`. `id`는 정규식 `^[a-z0-9_-]+$`.
- config_hash: 파일 내용을 `json.dumps(raw, sort_keys=True, separators=(",", ":"))`로 직렬화한 SHA-256 16진수의 앞 12자리.
- 기록 위치: `<output-dir>/strategies/<id>/weekly_history.csv`, `<output-dir>/strategies/<id>/signals.csv`.
- `signals.csv`는 추가 전용: 기존 바이트를 바꾸지 않고 파일 끝에만 덧붙인다.
- `--strategy`와 함께 줄 수 없는 옵션: `--universe`, `--benchmark`, `--cash`, `--account`, `--use-proxies`, `--lookbacks`, `--weights`, `--skip-days`, `--sma-window`, `--volatility-window`, `--rank-by`, `--min-avg-traded-value`, `--top-n`, `--max-per-group`, `--risk-on-benchmark`, `--risk-on-ticker`, `--min-weighted-momentum`, `--min-trend-strength`, `--min-relative-momentum`, `--require-relative-strength`, `--max-risky-weight`, `--proxy-fx`, `--history-file`. 충돌 시 종료 코드 2, 다운로드 전.
- `--strategy`는 `rank`, `weekly`, `backtest`에만 있다. `walkforward`, `compare`, `portfolio`에는 없다.
- 사용자 출력 문구는 한국어, 커밋 메시지는 영어 + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- 테스트는 네트워크 없이 돈다: `cli.download_prices`를 monkeypatch한다.
- 투자 비중·계좌 한도·주문 기능은 추가하지 않는다(CLAUDE.md).

## Review Focus

1. **`--end`로 과거 날짜를 준 `rank --strategy`**: 과거 조회일 뿐이므로 `signals.csv`에 기록하지 않고 `과거 기준일 조회라 신호를 기록하지 않습니다`를 출력해야 한다 → Task 3에 테스트.
2. **사람이 엑셀 등으로 열어 컬럼을 바꾼 `signals.csv`/`weekly_history.csv`**: 덮어쓰지 않고 멈추고 파일은 그대로여야 한다 → Task 3, Task 4에 테스트.
3. **문법이 틀린 전략 JSON(끝의 쉼표 등)**: 파일 경로를 적은 오류로 멈춰야 한다 → Task 1에 테스트.
4. **값 없는 플래그 충돌(`--strategy ... --risk-on-benchmark`)과 `--top-n=3` 형태**: 둘 다 충돌로 잡아야 한다 → Task 2에 테스트.
5. **같은 날 다른 설정으로 `weekly`를 두 번 실행**: 두 기록이 공존하고, 다음 주 비교는 실행한 설정의 해시와만 해야 한다 → Task 4에 테스트.

---

## File Structure

| 파일 | 역할 |
|---|---|
| Create `src/etf_momentum/strategy.py` | 전략 JSON 로드·검증·해시 (`Strategy`, `load_strategy`, `StrategyError`) |
| Create `src/etf_momentum/records.py` | 월말 신호 추가 전용 기록 (`record_signal`, `SignalRecordResult`) |
| Modify `src/etf_momentum/weekly.py` | 해시별 주간 기록 (`load_strategy_snapshot`, `append_strategy_snapshot`) |
| Modify `src/etf_momentum/cli.py` | `--strategy` 옵션, 충돌 검사, 전략 값 덮어쓰기, 기록 연결 |
| Create `config/strategies/pension.json`, `config/strategies/retirement.json` | 현재 추천 설정 |
| Create `tests/test_strategy.py`, `tests/test_records.py`, `tests/test_strategy_cli.py` | 각 모듈과 CLI 연결 테스트 |
| Modify `README.md`, `CLAUDE.md` | 추천 명령을 `--strategy` 형태로 |

---

### Task 1: 전략 파일 로드·검증·해시

**Files:**
- Create: `src/etf_momentum/strategy.py`
- Test: `tests/test_strategy.py`

**Interfaces:**
- Consumes: `etf_momentum.data.ACCOUNT_CHOICES`, `etf_momentum.signals.RANK_BY_CHOICES`
- Produces:
  - `class StrategyError(ValueError)`
  - `@dataclass(frozen=True) class Strategy` with fields: `id: str`, `version: int`, `description: str`, `universe: str`, `account: str`, `benchmark: str`, `cash: str | None`, `use_proxies: bool`, `lookbacks: tuple[int, int, int]`, `weights: tuple[float, float, float]`, `skip_days: int`, `sma_window: int`, `volatility_window: int`, `rank_by: str`, `min_avg_traded_value: float | None`, `top_n: int`, `max_per_group: int`, `risk_on_benchmark: bool`, `risk_on_tickers: tuple[str, ...] | None`, `config_hash: str`, `path: str`
  - `def load_strategy(path: str | Path) -> Strategy` (raises `StrategyError`)
  - `def strategy_hash(raw: dict) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/test_strategy.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --offline pytest tests/test_strategy.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'etf_momentum.strategy'`

- [ ] **Step 3: Write the implementation**

`src/etf_momentum/strategy.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --offline pytest tests/test_strategy.py -q`
Expected: all pass (19 tests)

- [ ] **Step 5: Commit**

```bash
git add src/etf_momentum/strategy.py tests/test_strategy.py
git commit -m "Add strategy file loader with validation and content hash

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `--strategy` 옵션과 충돌 검사, 초기 전략 파일

**Files:**
- Modify: `src/etf_momentum/cli.py` (`main`, rank/weekly/backtest 파서, 새 헬퍼 `_apply_strategy`, `_conflicting_options`)
- Create: `config/strategies/pension.json`, `config/strategies/retirement.json`
- Test: `tests/test_strategy_cli.py`

**Interfaces:**
- Consumes: `load_strategy`, `Strategy`, `StrategyError` (Task 1)
- Produces:
  - `STRATEGY_CONFLICTS: tuple[str, ...]` (Global Constraints의 목록)
  - `def _conflicting_options(argv: list[str]) -> list[str]` — argv에서 충돌 옵션 이름 목록
  - `def _apply_strategy(args: argparse.Namespace, strategy: Strategy) -> None` — args에 전략 값을 쓰고 `args.strategy_info = strategy`를 둔다
  - `args.strategy` (str 경로 또는 None), `args.strategy_info` (`Strategy` 또는 None) — Task 3, 4가 쓴다

- [ ] **Step 1: Write the failing tests**

`tests/test_strategy_cli.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --offline pytest tests/test_strategy_cli.py -q`
Expected: argparse rejects the unknown `--strategy` option (`error: unrecognized arguments: --strategy`). The conflict tests also exit with code 2 at this point, so they only prove something after Step 3; the tests that must fail now are `test_rank_and_backtest_pick_the_same_etfs_from_one_strategy_file`, `test_invalid_strategy_file_stops_before_download` (exit code 2 instead of a message naming `bad.json`) and `test_weekly_history_file_conflicts_with_strategy` is not yet meaningful either. Confirm the first two fail.

- [ ] **Step 3: Write the implementation**

In `src/etf_momentum/cli.py`:

1. Imports (top):

```python
from etf_momentum.strategy import Strategy, StrategyError, load_strategy
```

2. After the imports, the conflict list:

```python
STRATEGY_CONFLICTS = (
    "--universe", "--benchmark", "--cash", "--account", "--use-proxies", "--lookbacks", "--weights",
    "--skip-days", "--sma-window", "--volatility-window", "--rank-by", "--min-avg-traded-value",
    "--top-n", "--max-per-group", "--risk-on-benchmark", "--risk-on-ticker", "--min-weighted-momentum",
    "--min-trend-strength", "--min-relative-momentum", "--require-relative-strength", "--max-risky-weight",
    "--proxy-fx", "--history-file",
)
```

3. In `main()`, create the three strategy-capable subparsers with `allow_abbrev=False` so an abbreviation can't slip past the conflict check, and add the option to each:

```python
    rank_parser = subparsers.add_parser("rank", help="Rank ETFs by momentum", allow_abbrev=False)
```

(same `allow_abbrev=False` for `weekly_parser` and `backtest_parser`), and for each of the three parsers:

```python
    rank_parser.add_argument(
        "--strategy", default=None, help="Strategy JSON (config/strategies/*.json) fixing every selection setting"
    )
```

4. Replace the end of `main()`:

```python
    args = parser.parse_args()
    args.func(args)
```

with:

```python
    args = parser.parse_args()
    args.strategy_info = None
    if getattr(args, "strategy", None):
        conflicts = _conflicting_options(sys.argv[2:])
        if conflicts:
            parser.error(f"--strategy fixes the selection rule; remove {', '.join(conflicts)}")
        try:
            _apply_strategy(args, load_strategy(args.strategy))
        except StrategyError as exc:
            raise SystemExit(f"Strategy file error: {exc}") from exc
    args.func(args)
```

and add `import sys` to the imports.

5. Helpers (next to `_risk_on_tickers`):

```python
def _conflicting_options(argv: list[str]) -> list[str]:
    """Selection options given on the command line, in either `--opt value` or `--opt=value` form."""
    given = {token.split("=", 1)[0] for token in argv if token.startswith("--")}
    return [option for option in STRATEGY_CONFLICTS if option in given]


def _apply_strategy(args: argparse.Namespace, strategy: Strategy) -> None:
    """Write the strategy's selection settings into args, so the normal command code runs unchanged."""
    args.strategy_info = strategy
    args.universe = strategy.universe
    args.account = strategy.account
    args.benchmark = strategy.benchmark
    args.cash = strategy.cash
    args.use_proxies = strategy.use_proxies
    args.proxy_fx = "KRW=X"
    args.lookbacks = ",".join(str(v) for v in strategy.lookbacks)
    args.weights = ",".join(str(v) for v in strategy.weights)
    args.skip_days = strategy.skip_days
    args.sma_window = strategy.sma_window
    args.volatility_window = strategy.volatility_window
    args.rank_by = strategy.rank_by
    args.min_avg_traded_value = strategy.min_avg_traded_value
    args.min_weighted_momentum = 0.0
    args.min_trend_strength = 0.0
    args.min_relative_momentum = None
    args.require_relative_strength = False
    args.max_risky_weight = None
    args.top_n = strategy.top_n
    args.max_per_group = strategy.max_per_group
    args.risk_on_benchmark = strategy.risk_on_benchmark
    args.risk_on_ticker = ",".join(strategy.risk_on_tickers) if strategy.risk_on_tickers else None
```

6. `config/strategies/pension.json`:

```json
{
  "id": "pension",
  "version": 1,
  "description": "연금저축: 상위 4개, 그룹당 1개, KODEX 200 시장 필터, 유동성 10억 원",
  "universe": "config/universe_kr_pension.csv",
  "account": "pension",
  "benchmark": "069500.KS",
  "cash": "153130.KS",
  "use_proxies": true,
  "signal": {
    "lookbacks": [63, 126, 252],
    "weights": [0.3, 0.3, 0.4],
    "skip_days": 21,
    "sma_window": 200,
    "volatility_window": 63,
    "rank_by": "weighted_momentum",
    "min_avg_traded_value": 1000000000
  },
  "selection": {
    "top_n": 4,
    "max_per_group": 1,
    "risk_on_benchmark": true,
    "risk_on_tickers": null
  }
}
```

`config/strategies/retirement.json`: identical except `"id": "retirement"`, `"account": "retirement"`, `"description": "IRP·DC: 상위 4개, 그룹당 1개, KODEX 200 시장 필터, 유동성 10억 원"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --offline pytest tests/test_strategy_cli.py tests/test_strategy.py -q`
Expected: all pass

Run: `uv run --offline python -c "from etf_momentum.strategy import load_strategy as l; print(l('config/strategies/pension.json').config_hash, l('config/strategies/retirement.json').config_hash)"`
Expected: two different 12-character hashes

- [ ] **Step 5: Run the full suite**

Run: `uv run --offline pytest -q`
Expected: all pass (no regressions in the existing 133 tests)

- [ ] **Step 6: Commit**

```bash
git add src/etf_momentum/cli.py config/strategies tests/test_strategy_cli.py
git commit -m "Add --strategy to rank, weekly and backtest

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 월말 신호 기록 `signals.csv`

**Files:**
- Create: `src/etf_momentum/records.py`
- Modify: `src/etf_momentum/cli.py` (`_month_end_selection` 컬럼 추가, `run_rank_command`에서 기록)
- Test: `tests/test_records.py`, `tests/test_strategy_cli.py` (rank 기록 테스트 추가)

**Interfaces:**
- Consumes: `args.strategy_info: Strategy | None` (Task 2), `etf_momentum.reporting._code_revision() -> dict | None`
- Produces:
  - `SIGNAL_COLUMNS: list[str]`
  - `@dataclass(frozen=True) class SignalRecordResult: action: str` (`"added"` / `"unchanged"` / `"revised"`), `revision: int`, `changes: list[str]`
  - `def record_signal(path: Path, selection: pd.DataFrame, meta: dict[str, object]) -> SignalRecordResult`
  - `_month_end_selection` 결과에 컬럼 `rank_in_selection`, `market_filter` 추가

- [ ] **Step 1: Write the failing tests**

`tests/test_records.py`:

```python
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
```

Append to `tests/test_strategy_cli.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --offline pytest tests/test_records.py tests/test_strategy_cli.py -q`
Expected: `ModuleNotFoundError: No module named 'etf_momentum.records'`; after creating an empty module, the rank tests fail because `signals.csv` doesn't exist.

- [ ] **Step 3: Write the implementation**

`src/etf_momentum/records.py`:

```python
"""Append-only log of month-end selections per strategy (results/strategies/<id>/signals.csv).

Rows are never modified or deleted. Re-running the same month with the same rule and the same result
adds nothing; a different result (e.g. revised market data) is appended as the next revision.
"""

from __future__ import annotations

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
    missing = [c for c in SIGNAL_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"{path} is missing columns {', '.join(missing)}; not overwriting it")
    return frame


def _append(path: Path, rows: pd.DataFrame, write_header: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
```

In `src/etf_momentum/cli.py`:

1. `from etf_momentum.records import record_signal` and `from etf_momentum.reporting import _code_revision` (extend the existing reporting import line).

2. In `_month_end_selection`, give each selected row its order and add the filter state. Change the row construction:

```python
        rows.append({"status": "selected", "ticker": ticker, "group": groups.get(ticker, "")})
```

to

```python
        rows.append({"status": "selected", "rank_in_selection": number, "ticker": ticker, "group": groups.get(ticker, "")})
```

and the frame assembly:

```python
    frame = pd.DataFrame(rows, columns=["status", "ticker", "group", "slots"])
```

to

```python
    frame = pd.DataFrame(rows, columns=["status", "rank_in_selection", "ticker", "group", "slots"])
```

and after `frame["risk_on"] = picked.risk_on` add:

```python
    frame["market_filter"] = ("on" if picked.risk_on else "off") if args.risk_on_benchmark else "unused"
```

3. At the end of `run_rank_command`, after `selection.to_csv(...)` and its print:

```python
    strategy = getattr(args, "strategy_info", None)
    if strategy is not None:
        _record_month_end_signal(output_dir, strategy, selection, prices, args)
```

and the helper:

```python
def _record_month_end_signal(
    output_dir: Path, strategy: Strategy, selection: pd.DataFrame, prices: pd.DataFrame, args: argparse.Namespace
) -> None:
    if args.end:
        print("과거 기준일 조회라 신호를 기록하지 않습니다 (--end)")
        return
    revision = _code_revision() or {}
    meta = {
        "signal_date": selection["as_of"].iloc[0],
        "recorded_at": pd.Timestamp.now(tz="Asia/Seoul").isoformat(timespec="seconds"),
        "strategy_id": strategy.id,
        "version": strategy.version,
        "config_hash": strategy.config_hash,
        "code_commit": revision.get("commit", ""),
        "code_dirty": revision.get("dirty", ""),
        "price_last_date": str(pd.Timestamp(prices.index[-1]).date()),
    }
    path = output_dir / "strategies" / strategy.id / "signals.csv"
    try:
        result = record_signal(path, selection, meta)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if result.action == "added":
        print(f"신호 기록: {meta['signal_date']} (버전 {strategy.version}, {strategy.config_hash}) → {path}")
    elif result.action == "unchanged":
        print(f"이미 기록됨: {meta['signal_date']} (revision {result.revision})")
    else:
        print(f"WARNING: 같은 월말·같은 규칙인데 결과가 달라 revision {result.revision}로 추가했습니다")
        for change in result.changes:
            print(f"  {change}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --offline pytest tests/test_records.py tests/test_strategy_cli.py tests/test_selection.py -q`
Expected: all pass (`test_selection.py` still passes with the two new columns in `latest_selection.csv`)

- [ ] **Step 5: Run the full suite**

Run: `uv run --offline pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/etf_momentum/records.py src/etf_momentum/cli.py tests/test_records.py tests/test_strategy_cli.py
git commit -m "Record month-end selections per strategy in an append-only log

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 전략별 주간 기록

**Files:**
- Modify: `src/etf_momentum/weekly.py` (새 함수 2개)
- Modify: `src/etf_momentum/cli.py` (`run_weekly_command`)
- Test: `tests/test_records.py` (주간 기록 함수), `tests/test_strategy_cli.py` (weekly 연결)

**Interfaces:**
- Consumes: `args.strategy_info` (Task 2), existing `SNAPSHOT_COLUMNS`, `build_weekly_report`
- Produces:
  - `def load_strategy_snapshot(history_path: Path, current_as_of: pd.Timestamp, config_hash: str, version: int) -> tuple[pd.DataFrame, str | None]` — (비교할 이전 스냅샷, 안내 문구 또는 None)
  - `def append_strategy_snapshot(history_path: Path, ranking: pd.DataFrame, version: int, config_hash: str) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_records.py`:

```python
from etf_momentum.weekly import append_strategy_snapshot, load_strategy_snapshot  # noqa: E402


def ranking(as_of: str, *tickers: str) -> pd.DataFrame:
    return pd.DataFrame({"as_of": pd.Timestamp(as_of), "ticker": list(tickers), "rank": range(1, len(tickers) + 1)})


def test_weekly_compares_only_with_same_rule(tmp_path) -> None:
    path = tmp_path / "weekly_history.csv"
    append_strategy_snapshot(path, ranking("2026-09-25", "A", "B"), version=1, config_hash="h1")
    previous, notice = load_strategy_snapshot(path, pd.Timestamp("2026-10-02"), config_hash="h1", version=1)
    assert previous["ticker"].tolist() == ["A", "B"] and notice is None


def test_weekly_rule_change_starts_new_baseline_with_notice(tmp_path) -> None:
    path = tmp_path / "weekly_history.csv"
    append_strategy_snapshot(path, ranking("2026-09-25", "A", "B"), version=1, config_hash="h1")
    previous, notice = load_strategy_snapshot(path, pd.Timestamp("2026-10-02"), config_hash="h2", version=2)
    assert previous.empty and "1→2" in notice


def test_same_day_two_rules_coexist_and_rerun_replaces_only_its_own(tmp_path) -> None:
    path = tmp_path / "weekly_history.csv"
    append_strategy_snapshot(path, ranking("2026-10-02", "A"), version=1, config_hash="h1")
    append_strategy_snapshot(path, ranking("2026-10-02", "B"), version=2, config_hash="h2")
    append_strategy_snapshot(path, ranking("2026-10-02", "C"), version=1, config_hash="h1")  # rerun of h1
    saved = pd.read_csv(path)
    assert sorted(zip(saved["config_hash"], saved["ticker"])) == [("h1", "C"), ("h2", "B")]
    previous, _ = load_strategy_snapshot(path, pd.Timestamp("2026-10-09"), config_hash="h2", version=2)
    assert previous["ticker"].tolist() == ["B"]


def test_weekly_history_with_edited_columns_is_not_overwritten(tmp_path) -> None:
    path = tmp_path / "weekly_history.csv"
    path.write_text("as_of,ticker\n2026-09-25,A\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="weekly_history.csv"):
        append_strategy_snapshot(path, ranking("2026-10-02", "A"), version=1, config_hash="h1")
    with pytest.raises(ValueError, match="weekly_history.csv"):
        load_strategy_snapshot(path, pd.Timestamp("2026-10-02"), config_hash="h1", version=1)
    assert path.read_bytes() == before
```

Append to `tests/test_strategy_cli.py`:

```python
def test_weekly_with_strategy_writes_history_in_strategy_folder(tmp_path, monkeypatch, offline) -> None:
    out = tmp_path / "out"
    run(monkeypatch, "weekly", "--strategy", strategy_file(tmp_path), "--output-dir", str(out))
    history = pd.read_csv(out / "strategies" / "test" / "weekly_history.csv")
    assert {"version", "config_hash"} <= set(history.columns)
    assert not (out / "rank_history.csv").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --offline pytest tests/test_records.py tests/test_strategy_cli.py -q`
Expected: `ImportError: cannot import name 'append_strategy_snapshot'`

- [ ] **Step 3: Write the implementation**

Append to `src/etf_momentum/weekly.py`:

```python
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
    old_version = latest_day["version"].iloc[0]
    return pd.DataFrame(), f"설정이 바뀌어(버전 {old_version}→{version}) 새 기준으로 시작합니다"


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
    return history
```

In `src/etf_momentum/cli.py`:

1. Extend the weekly import: `from etf_momentum.weekly import append_snapshot, append_strategy_snapshot, build_weekly_report, load_previous_snapshot, load_strategy_snapshot`.

2. In `run_weekly_command`, replace

```python
    history_path = Path(args.history_file) if args.history_file else output_dir / "rank_history.csv"
    previous = load_previous_snapshot(history_path, current_as_of)
```

with

```python
    strategy = getattr(args, "strategy_info", None)
    if strategy is not None:
        history_path = output_dir / "strategies" / strategy.id / "weekly_history.csv"
        try:
            previous, notice = load_strategy_snapshot(history_path, current_as_of, strategy.config_hash, strategy.version)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if notice:
            print(notice)
    else:
        history_path = Path(args.history_file) if args.history_file else output_dir / "rank_history.csv"
        previous = load_previous_snapshot(history_path, current_as_of)
```

and replace

```python
    if not args.no_save_history:
        append_snapshot(history_path, ranking)
```

with

```python
    if not args.no_save_history:
        if strategy is not None:
            try:
                append_strategy_snapshot(history_path, ranking, strategy.version, strategy.config_hash)
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
        else:
            append_snapshot(history_path, ranking)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --offline pytest tests/test_records.py tests/test_strategy_cli.py tests/test_weekly.py -q`
Expected: all pass

- [ ] **Step 5: Run the full suite**

Run: `uv run --offline pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/etf_momentum/weekly.py src/etf_momentum/cli.py tests/test_records.py tests/test_strategy_cli.py
git commit -m "Keep weekly history per strategy and compare only within the same rule

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 문서와 실제 데이터 확인

**Files:**
- Modify: `README.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: everything above

- [ ] **Step 1: Real-data smoke run (network/cache)**

Run:

```bash
uv run --offline etf-momentum rank --strategy config/strategies/pension.json --top 3 \
  --output-dir /private/tmp/etf-strategy-smoke
uv run --offline etf-momentum rank --strategy config/strategies/pension.json --top 3 \
  --output-dir /private/tmp/etf-strategy-smoke
```

Expected: first run prints `신호 기록: 2026-09-30 ...`; second run prints `이미 기록됨: 2026-09-30 (revision 1)`; the selected ETFs match the previous non-strategy run (`139260.KS, 292150.KS, 102780.KS, 381180.KS`) unless newer data changed them.

Run:

```bash
uv run --offline etf-momentum rank --strategy config/strategies/pension.json --top-n 3
```

Expected: `error: --strategy fixes the selection rule; remove --top-n` with exit code 2.

- [ ] **Step 2: Update README**

In `README.md`, in the `현재 주도 섹터 랭킹 보기` section right after the paragraph about `latest_selection.csv`, add:

````markdown
### 전략 파일로 실행 (권장)

선정 규칙을 `config/strategies/<id>.json`에 고정해 두고 `--strategy`로 실행합니다. `rank`, `weekly`, `backtest`가 같은 규칙을 씁니다.

```bash
uv run etf-momentum rank --strategy config/strategies/pension.json      # 연금저축
uv run etf-momentum rank --strategy config/strategies/retirement.json   # IRP·DC
uv run etf-momentum weekly --strategy config/strategies/pension.json
uv run etf-momentum backtest --strategy config/strategies/pension.json --start 2010-01-01 --transaction-cost-bps 10
```

- 선정 규칙 옵션(`--top-n`, `--benchmark` 등)을 함께 주면 멈춥니다. 규칙을 바꾸려면 파일을 고치고 `version`을 올립니다. 파일 내용으로 만든 `config_hash`가 기록마다 남아, `version`을 올리지 않아도 규칙 변경이 드러납니다.
- 기록은 `results/strategies/<id>/`에 쌓입니다.
  - `signals.csv`: `rank`를 실행할 때마다 직전 월말 선택을 기록합니다. 기존 행은 고치지 않습니다. 같은 월말·같은 규칙인데 결과가 다르면(데이터 수정 등) `revision`을 올려 추가합니다. `--end`로 과거를 조회하면 기록하지 않습니다.
  - `weekly_history.csv`: 같은 규칙의 기록끼리만 비교합니다. 규칙이 바뀌면 새 기준으로 시작합니다.
````

- [ ] **Step 3: Update CLAUDE.md**

In `CLAUDE.md`, replace the rank command block line

```
uv run etf-momentum rank --universe config/universe_kr_pension.csv \
  --benchmark 069500.KS --cash 153130.KS --account pension \
  --lookback-years 3 --min-avg-traded-value 1e9 --top 15 \
  --top-n 4 --max-per-group 1 --risk-on-benchmark      # 현재 순위 + 직전 월말 기준 선택 종목
```

with

```
uv run etf-momentum rank --strategy config/strategies/pension.json   # 현재 순위 + 직전 월말 선택, 신호 기록
```

and add under `## 연구 결과를 만들 때의 규칙`:

```
- 전략 규칙을 바꿀 때는 `config/strategies/<id>.json`을 고치고 `version`을 올린다. `results/strategies/<id>/signals.csv`의 기존 행은 고치거나 지우지 않는다.
```

- [ ] **Step 4: Full suite and commit**

Run: `uv run --offline pytest -q`
Expected: all pass

```bash
git add README.md CLAUDE.md
git commit -m "Document strategy files and per-strategy records

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
