# 국내 상장 ETF 전체 유니버스 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 네이버 ETF 전체 목록에서 연금 전략용 유니버스(`config/universe_kr_all.csv`)를 자동으로 만들고, 연금 전략 파일이 그것을 쓰게 한다.

**Architecture:** 네트워크가 없는 순수 함수(`src/etf_momentum/universe_builder.py`: 제외 규칙, 가격 누락 제거, 최근 수익률, 완전 연결 상관 그룹, 유니버스 행 생성)와 그것을 엮는 얇은 스크립트(`scripts/build_universe.py`: 네이버 목록·야후 가격 받기, 원자적 저장, 메타데이터)로 나눈다. 전략 파일 두 개는 새 유니버스로 바꾸고 version 2로 올린다.

**Tech Stack:** Python 3.11+, pandas, numpy, urllib(표준 라이브러리), 기존 `download_prices(raw=True)`, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-10-10-full-etf-universe-design.md`

## Global Constraints

- 네이버 목록 URL: `https://finance.naver.com/api/sise/etfItemList.nhn`, 응답 인코딩 cp949, 항목 `result.etfItemList[]`의 `itemcode`, `itemname`, `etfTabCode`, `amonut`(거래대금, 백만 원).
- 야후 티커 = `<itemcode>.KS`.
- 제외 분류: `etfTabCode` 3, 6, 7. 제외 이름: `레버리지`, `인버스`, `2X`(대소문자 무시).
- 항상 포함: `153130.KS`(현금), `069500.KS`(벤치마크). 목록이나 가격에 없으면 오류.
- 그룹: 벤치마크 실제 거래일 최근 252일 일간 수익률, 함께 관측 63일 이상만 상관 계산, 완전 연결로 모든 쌍 ≥ 0.9일 때만 합침, 가장 높은 쌍부터, 동률은 정렬된 티커 순서.
- 그룹 이름 = 그룹에서 거래대금 최대 ETF의 이름. 혼자인 그룹은 빈 값.
- `pension_ok=1` 전부; `retirement_ok=0`은 이름에 `합성` 또는 `선물`; `risk_asset=0`은 `153130.KS`만; `proxy`는 `config/universe_kr_pension.csv`에서 같은 티커만 이전.
- 출력 열 순서: `ticker,name,category,enabled,group,pension_ok,retirement_ok,risk_asset,proxy,note`.
- 저장은 임시 파일 → `os.replace`. 실패 시 기존 파일 보존.
- 기존 `universe_kr_pension.csv`, `universe_kr_etfs.csv`, 기존 연구 결과·문서 수치는 바꾸지 않는다.
- 테스트는 네트워크 없이 실행(`uv run --offline pytest -q`).
- 커밋은 main에 직접, 영어 메시지, 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. 결과 생성 전 코드 커밋, 생성 중 파일 수정 금지.

## Review Focus

1. **네이버 거래대금이 비어 있거나 0인 ETF**(신규 상장 당일 등): 그룹 이름을 정할 때 오류 없이 다른 멤버를 고르고, 모두 0이면 정렬상 첫 티커 이름을 쓴다 → Task 3 테스트.
2. **상관을 계산할 수 없는 ETF**(상장 63일 미만, 가격 모두 같음 → 분산 0으로 NaN 상관): 어떤 그룹에도 합쳐지지 않고 혼자 남는다 → Task 2 테스트.
3. **현금 ETF가 야후 가격에서 빠진 경우**: 유니버스를 쓰지 않고 오류로 멈춘다(기존 파일 보존) → Task 1 테스트.
4. **이름에 소문자 `2x`나 `인버스2X`처럼 붙은 표기**: 제외된다 → Task 1 테스트.
5. **같은 종목코드가 네이버 목록에 두 번**: 한 번만 남는다(중복 티커는 `load_universe`가 거부하므로) → Task 1 테스트.

---

### Task 1: 목록 필터와 가격 누락 제거

**Files:**
- Create: `src/etf_momentum/universe_builder.py`
- Test: `tests/test_universe_builder.py`

**Interfaces:**
- Produces: `filter_etf_list(items: list[dict], always_include: tuple[str, ...] = ("153130.KS", "069500.KS")) -> tuple[pd.DataFrame, dict[str, int]]` — 열 `ticker, name, tab, traded_value`; 사유별 제외 개수 `{"tab_3": n, "tab_6": n, "tab_7": n, "name": n, "duplicate": n}`.
- Produces: `drop_unpriced(listing: pd.DataFrame, prices: pd.DataFrame, required: tuple[str, ...] = ("153130.KS", "069500.KS")) -> tuple[pd.DataFrame, list[str]]` — 가격이 하나도 없는 티커 제거, 필수 티커 누락 시 `ValueError`.
- Produces: 상수 `CASH = "153130.KS"`, `BENCHMARK = "069500.KS"`, `TAB_NAMES: dict[int, str]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_universe_builder.py`:

```python
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from etf_momentum.universe_builder import drop_unpriced, filter_etf_list


def item(code: str, name: str, tab: int, amount: float | None = 100.0) -> dict:
    return {"itemcode": code, "itemname": name, "etfTabCode": tab, "amonut": amount}


ITEMS = [
    item("069500", "KODEX 200", 1, 2_000_000),
    item("153130", "KODEX 단기채권", 6, 50_000),
    item("122630", "KODEX 레버리지", 3),
    item("114800", "KODEX 인버스", 3),
    item("233740", "KODEX 코스닥150레버리지", 1),
    item("396520", "TIGER 미국나스닥100선물2x", 4),  # lower-case 2x, no 인버스/레버리지 in the name
    item("114260", "KODEX 국고채3년", 6),
    item("459580", "KODEX CD금리액티브(합성)", 6),
    item("448290", "KODEX 삼성전자SK하이닉스채권혼합50", 7),
    item("091160", "KODEX 반도체", 2, 800_000),
    item("472150", "TIGER 배당커버드콜액티브", 2),
    item("218420", "KODEX 미국S&P500에너지(합성)", 4),
    item("091160", "KODEX 반도체", 2, 800_000),  # listed twice
]


# Task 1: exclusion rules
def test_filter_keeps_equity_commodity_covered_call_and_synthetic() -> None:
    listing, excluded = filter_etf_list(ITEMS)
    assert listing["ticker"].tolist() == ["069500.KS", "091160.KS", "153130.KS", "218420.KS", "472150.KS"]
    assert excluded == {"tab_3": 2, "tab_6": 2, "tab_7": 1, "name": 2, "duplicate": 1}


def test_cash_etf_is_kept_although_its_tab_is_bonds() -> None:
    listing, _ = filter_etf_list(ITEMS)
    row = listing.set_index("ticker").loc["153130.KS"]
    assert row["name"] == "KODEX 단기채권" and row["tab"] == 6


def test_missing_required_etf_in_the_list_is_an_error() -> None:
    with pytest.raises(ValueError, match="153130.KS"):
        filter_etf_list([i for i in ITEMS if i["itemcode"] != "153130"])


def test_missing_traded_value_becomes_zero() -> None:
    listing, _ = filter_etf_list([*ITEMS, item("999990", "NEW ETF", 2, None)])
    assert listing.set_index("ticker").loc["999990.KS", "traded_value"] == 0.0


# Task 1: tickers without Yahoo prices
def test_drop_unpriced_removes_and_reports() -> None:
    listing, _ = filter_etf_list(ITEMS)
    index = pd.bdate_range("2026-01-01", periods=5)
    prices = pd.DataFrame({t: 1.0 for t in listing["ticker"] if t != "472150.KS"}, index=index)
    prices["218420.KS"] = np.nan
    kept, dropped = drop_unpriced(listing, prices)
    assert dropped == ["218420.KS", "472150.KS"]
    assert "472150.KS" not in kept["ticker"].tolist()


def test_unpriced_cash_etf_is_an_error() -> None:
    listing, _ = filter_etf_list(ITEMS)
    prices = pd.DataFrame({"069500.KS": [1.0]}, index=pd.to_datetime(["2026-01-02"]))
    with pytest.raises(ValueError, match="153130.KS"):
        drop_unpriced(listing, prices)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --offline pytest tests/test_universe_builder.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'etf_momentum.universe_builder'`

- [ ] **Step 3: Implement**

`src/etf_momentum/universe_builder.py`:

```python
"""Build the full Korean ETF universe from Naver's ETF list (pure functions; no network here).

scripts/build_universe.py fetches the list and prices and calls these in order:
filter_etf_list -> drop_unpriced -> recent_returns -> correlation_groups -> build_universe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CASH = "153130.KS"
BENCHMARK = "069500.KS"
REQUIRED = (CASH, BENCHMARK)
TAB_NAMES = {1: "국내 시장지수", 2: "국내 업종/테마", 3: "국내 파생", 4: "해외 주식", 5: "원자재", 6: "채권", 7: "기타"}
EXCLUDED_TABS = (3, 6, 7)  # leveraged/inverse, bonds/rates/money market, bond-mixed/TDF
EXCLUDED_NAME_PARTS = ("레버리지", "인버스", "2X")


def filter_etf_list(items: list[dict], always_include: tuple[str, ...] = REQUIRED) -> tuple[pd.DataFrame, dict[str, int]]:
    """Candidates from Naver's ETF list: no leveraged/inverse, bond, rate or bond-mixed ETFs.

    The cash and benchmark ETFs are kept whatever their tab. Returns the listing (ticker, name, tab,
    traded_value in million KRW) sorted by ticker, and how many ETFs each rule removed."""
    excluded = {f"tab_{tab}": 0 for tab in EXCLUDED_TABS} | {"name": 0, "duplicate": 0}
    rows: dict[str, dict] = {}
    for entry in items:
        ticker = f"{str(entry['itemcode']).strip()}.KS"
        name = str(entry["itemname"]).strip()
        tab = int(entry["etfTabCode"])
        if ticker in rows:
            excluded["duplicate"] += 1
            continue
        if ticker not in always_include:
            if tab in EXCLUDED_TABS:
                excluded[f"tab_{tab}"] += 1
                continue
            if any(part in name.upper() for part in EXCLUDED_NAME_PARTS):
                excluded["name"] += 1
                continue
        amount = entry.get("amonut")
        traded = float(amount) if amount is not None and pd.notna(amount) else 0.0
        rows[ticker] = {"ticker": ticker, "name": name, "tab": tab, "traded_value": traded}
    missing = [t for t in always_include if t not in rows]
    if missing:
        raise ValueError(f"Naver ETF list has no {', '.join(missing)}; not building the universe")
    listing = pd.DataFrame(sorted(rows.values(), key=lambda row: row["ticker"]))
    return listing.reset_index(drop=True), excluded


def drop_unpriced(
    listing: pd.DataFrame, prices: pd.DataFrame, required: tuple[str, ...] = REQUIRED
) -> tuple[pd.DataFrame, list[str]]:
    """Remove ETFs Yahoo has no price for (a partial download is never cached, so they would force a
    full re-download on every run). A missing cash or benchmark ETF is an error."""
    priced = {t for t in prices.columns if prices[t].notna().any()}
    dropped = sorted(t for t in listing["ticker"] if t not in priced)
    missing_required = [t for t in required if t in dropped]
    if missing_required:
        raise ValueError(f"No Yahoo prices for required {', '.join(missing_required)}; not building the universe")
    return listing[~listing["ticker"].isin(dropped)].reset_index(drop=True), dropped
```

(`EXCLUDED_NAME_PARTS` holds `2X`; `name.upper()` turns `2x` into `2X`, and Korean text is unaffected by `upper()`.)

- [ ] **Step 4: Run tests**

Run: `uv run --offline pytest tests/test_universe_builder.py -q` → 6 passed
Run: `uv run --offline pytest -q` → all pass

- [ ] **Step 5: Commit**

```bash
git add src/etf_momentum/universe_builder.py tests/test_universe_builder.py
git commit -m "Filter Naver's ETF list into momentum candidates and drop tickers without Yahoo prices

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 최근 수익률과 완전 연결 상관 그룹

**Files:**
- Modify: `src/etf_momentum/universe_builder.py`
- Test: `tests/test_universe_builder.py`

**Interfaces:**
- Consumes: `BENCHMARK` (Task 1)
- Produces: `recent_returns(raw: pd.DataFrame, benchmark: str = BENCHMARK, window: int = 252) -> pd.DataFrame` — 벤치마크가 실제로 거래된 날 중 최근 `window`일의 일간 수익률(원자료, 빈 값 유지).
- Produces: `correlation_groups(returns: pd.DataFrame, threshold: float = 0.9, min_overlap: int = 63) -> pd.Series` — index = 정렬된 티커, 값 = 그룹 번호(int, 2개 이상 그룹) 또는 `pd.NA`(혼자).

- [ ] **Step 1: Write the failing tests** (append)

```python
# Task 2: correlation groups (complete linkage)
from etf_momentum.universe_builder import correlation_groups, recent_returns  # noqa: E402


def correlated(n: int = 252, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 0.01, n)
    other = rng.normal(0, 0.01, n)
    noise = lambda scale: rng.normal(0, scale, n)  # noqa: E731
    return pd.DataFrame(
        {
            "A": base + noise(0.001),
            "B": base + noise(0.001),
            "C": other + noise(0.001),
            "D": other + noise(0.001),
            "E": rng.normal(0, 0.01, n),
        },
        index=pd.bdate_range("2025-01-01", periods=n),
    )


def test_highly_correlated_pairs_share_a_group_and_others_stay_alone() -> None:
    groups = correlation_groups(correlated())
    assert groups["A"] == groups["B"] and groups["C"] == groups["D"] and groups["A"] != groups["C"]
    assert pd.isna(groups["E"])


def test_chain_does_not_link_the_ends() -> None:
    n = 400
    rng = np.random.default_rng(1)
    x, y = rng.normal(0, 0.01, n), rng.normal(0, 0.01, n)
    # corr(A,B) and corr(B,C) about 0.95, corr(A,C) about 0.81
    a = x
    b = x * 0.95 + y * 0.31
    c = x * 0.81 + y * 0.59
    groups = correlation_groups(pd.DataFrame({"A": a, "B": b, "C": c}))
    assert not (pd.notna(groups["A"]) and groups["A"] == groups["C"])


def test_short_or_flat_history_is_never_grouped() -> None:
    returns = correlated()
    returns.loc[returns.index[:-40], "B"] = np.nan  # only 40 days: below min_overlap
    returns["F"] = 0.0  # flat price: correlation undefined
    groups = correlation_groups(returns)
    assert pd.isna(groups["B"]) and pd.isna(groups["F"]) and pd.isna(groups["A"])


def test_groups_do_not_depend_on_column_order() -> None:
    returns = correlated()
    first = correlation_groups(returns)
    second = correlation_groups(returns[["E", "D", "C", "B", "A"]])
    pd.testing.assert_series_equal(first, second)


def test_recent_returns_use_benchmark_trading_days() -> None:
    index = pd.bdate_range("2025-01-01", periods=300)
    raw = pd.DataFrame({"069500.KS": 100.0, "X": np.linspace(100, 130, 300)}, index=index)
    raw.loc[index[-1], "069500.KS"] = np.nan  # a row the benchmark did not trade (stray holiday row)
    returns = recent_returns(raw, window=252)
    assert len(returns) == 252 and returns.index[-1] == index[-2]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --offline pytest tests/test_universe_builder.py -q`
Expected: FAIL — `ImportError: cannot import name 'correlation_groups'`

- [ ] **Step 3: Implement** (append to `universe_builder.py`)

```python
def recent_returns(raw: pd.DataFrame, benchmark: str = BENCHMARK, window: int = 252) -> pd.DataFrame:
    """Daily returns over the last `window` days the benchmark really traded; gaps stay NaN."""
    traded = raw[raw[benchmark].notna()]
    return traded.pct_change(fill_method=None).iloc[1:].tail(window)


def correlation_groups(returns: pd.DataFrame, threshold: float = 0.9, min_overlap: int = 63) -> pd.Series:
    """Complete-linkage clusters: a group's every pair has correlation >= threshold.

    Pairs observed together on fewer than `min_overlap` days, or with a flat series, have no
    correlation and are never merged. The most correlated pair of groups merges first; ties go to the
    sorted ticker order, so the result does not depend on column order. Returns a group number per
    ticker (sorted), or NA for an ETF alone."""
    tickers = sorted(returns.columns)
    corr = returns[tickers].corr(min_periods=min_overlap).to_numpy(dtype=float)
    link = np.where(np.isnan(corr), -np.inf, corr)  # group-to-group similarity = weakest pair
    np.fill_diagonal(link, -np.inf)
    members = {i: [i] for i in range(len(tickers))}
    while members:
        i, j = np.unravel_index(int(np.argmax(link)), link.shape)
        if link[i, j] < threshold:
            break
        i, j = min(i, j), max(i, j)
        members[i] += members.pop(j)
        merged = np.minimum(link[i], link[j])
        link[i, :] = merged
        link[:, i] = merged
        link[i, i] = -np.inf
        link[j, :] = -np.inf
        link[:, j] = -np.inf
    labels = pd.Series(pd.NA, index=tickers, dtype="object")
    for number, (_, group) in enumerate(sorted((g[0], g) for g in members.values() if len(g) > 1)):
        labels.iloc[sorted(group)] = number
    return labels
```

- [ ] **Step 4: Run tests**

Run: `uv run --offline pytest tests/test_universe_builder.py -q` → 11 passed
Run: `uv run --offline pytest -q` → all pass

- [ ] **Step 5: Commit**

```bash
git add src/etf_momentum/universe_builder.py tests/test_universe_builder.py
git commit -m "Group ETFs by complete-linkage return correlation over the benchmark's trading days

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 유니버스 행 만들기

**Files:**
- Modify: `src/etf_momentum/universe_builder.py`
- Test: `tests/test_universe_builder.py`

**Interfaces:**
- Consumes: `filter_etf_list`, `correlation_groups`, `CASH`, `TAB_NAMES` (Tasks 1-2)
- Produces: `UNIVERSE_COLUMNS: list[str]`; `build_universe(listing: pd.DataFrame, groups: pd.Series, proxies: dict[str, str]) -> pd.DataFrame` — `UNIVERSE_COLUMNS` 순서, 티커 정렬.

- [ ] **Step 1: Write the failing tests** (append)

```python
# Task 3: universe rows
from etf_momentum.data import load_universe  # noqa: E402
from etf_momentum.universe_builder import UNIVERSE_COLUMNS, build_universe  # noqa: E402


def listing_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ticker": "069500.KS", "name": "KODEX 200", "tab": 1, "traded_value": 2_000_000.0},
            {"ticker": "102110.KS", "name": "TIGER 200", "tab": 1, "traded_value": 500_000.0},
            {"ticker": "153130.KS", "name": "KODEX 단기채권", "tab": 6, "traded_value": 50_000.0},
            {"ticker": "143850.KS", "name": "TIGER 미국S&P500선물(H)", "tab": 4, "traded_value": 0.0},
            {"ticker": "218420.KS", "name": "KODEX 미국S&P500에너지(합성)", "tab": 4, "traded_value": 0.0},
            {"ticker": "458730.KS", "name": "TIGER 미국배당다우존스", "tab": 4, "traded_value": 90_000.0},
        ]
    )


def test_build_universe_marks_accounts_assets_groups_and_proxies(tmp_path) -> None:
    groups = pd.Series({"069500.KS": 0, "102110.KS": 0, "143850.KS": 1, "218420.KS": 1, "153130.KS": pd.NA, "458730.KS": pd.NA})
    universe = build_universe(listing_frame(), groups, {"458730.KS": "SCHD", "999999.KS": "SPY"}).set_index("ticker")
    assert list(universe.reset_index().columns) == UNIVERSE_COLUMNS
    assert universe.loc["102110.KS", "group"] == "KODEX 200"  # largest traded value names the group
    assert universe.loc["218420.KS", "group"] == "TIGER 미국S&P500선물(H)"  # all 0: first ticker (143850) names it
    assert universe.loc["458730.KS", "group"] == "" and universe.loc["458730.KS", "proxy"] == "SCHD"
    assert universe.loc["143850.KS", "retirement_ok"] == 0 and universe.loc["218420.KS", "retirement_ok"] == 0
    assert universe.loc["069500.KS", "retirement_ok"] == 1 and (universe["pension_ok"] == 1).all()
    assert universe.loc["153130.KS", "risk_asset"] == 0 and universe["risk_asset"].sum() == len(universe) - 1
    assert universe.loc["458730.KS", "category"] == "해외 주식" and (universe["enabled"] == 1).all()
    path = tmp_path / "u.csv"
    universe.reset_index().to_csv(path, index=False)
    members = {m.ticker: m for m in load_universe(path)}
    assert members["153130.KS"].risk_asset is False and members["458730.KS"].proxy == "SCHD"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --offline pytest tests/test_universe_builder.py -q`
Expected: FAIL — `ImportError: cannot import name 'UNIVERSE_COLUMNS'`

- [ ] **Step 3: Implement** (append to `universe_builder.py`)

```python
UNIVERSE_COLUMNS = ["ticker", "name", "category", "enabled", "group", "pension_ok", "retirement_ok", "risk_asset", "proxy", "note"]
RETIREMENT_EXCLUDED_NAME_PARTS = ("합성", "선물")  # derivative-type ETFs, approximated by name


def build_universe(listing: pd.DataFrame, groups: pd.Series, proxies: dict[str, str]) -> pd.DataFrame:
    """Universe CSV rows: every ETF enabled and allowed in pension accounts; synthetic/futures ETFs
    not allowed in retirement accounts; only the cash ETF is a safe asset. A group is named after its
    member with the largest traded value (ties and zeros: first ticker); an ETF alone has no group."""
    frame = listing.sort_values("ticker").reset_index(drop=True)
    numbers = groups.reindex(frame["ticker"]).to_numpy()
    frame["number"] = numbers
    names: dict[object, str] = {}
    for number, members in frame[pd.notna(frame["number"])].groupby("number"):
        best = members.sort_values(["traded_value", "ticker"], ascending=[False, True]).iloc[0]
        names[number] = best["name"]
    return pd.DataFrame(
        {
            "ticker": frame["ticker"],
            "name": frame["name"],
            "category": frame["tab"].map(TAB_NAMES).fillna("기타"),
            "enabled": 1,
            "group": [names[n] if pd.notna(n) else "" for n in frame["number"]],
            "pension_ok": 1,
            "retirement_ok": [0 if any(p in name for p in RETIREMENT_EXCLUDED_NAME_PARTS) else 1 for name in frame["name"]],
            "risk_asset": [0 if t == CASH else 1 for t in frame["ticker"]],
            "proxy": [proxies.get(t, "") for t in frame["ticker"]],
            "note": "",
        },
        columns=UNIVERSE_COLUMNS,
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run --offline pytest tests/test_universe_builder.py -q` → 12 passed
Run: `uv run --offline pytest -q` → all pass

- [ ] **Step 5: Commit**

```bash
git add src/etf_momentum/universe_builder.py tests/test_universe_builder.py
git commit -m "Build universe rows: account flags, safe cash, group names, carried-over proxies

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `scripts/build_universe.py`

**Files:**
- Create: `scripts/build_universe.py`
- Modify: `src/etf_momentum/universe_builder.py` (add `write_universe`)
- Test: `tests/test_universe_builder.py`

**Interfaces:**
- Consumes: Tasks 1-3, `etf_momentum.data.download_prices(tickers, start, end, cache_dir, refresh, raw=True)`
- Produces: `write_universe(universe: pd.DataFrame, meta: dict, output: Path) -> None` — CSV and `<stem>.meta.json`를 임시 파일 → `os.replace`로 저장.

- [ ] **Step 1: Write the failing test** (append)

```python
# Task 4: atomic write
from etf_momentum.universe_builder import write_universe  # noqa: E402


def test_write_universe_replaces_files_and_keeps_old_on_failure(tmp_path) -> None:
    output = tmp_path / "universe_kr_all.csv"
    output.write_text("old")
    universe = build_universe(listing_frame(), pd.Series(dtype="object"), {})
    write_universe(universe, {"built_at": "2026-10-10"}, output)
    assert pd.read_csv(output)["ticker"].tolist()[0] == "069500.KS"
    assert '"built_at": "2026-10-10"' in (tmp_path / "universe_kr_all.meta.json").read_text()
    before = output.read_text()
    with pytest.raises(TypeError):
        write_universe(universe, {"bad": object()}, output)  # meta not JSON-serializable
    assert output.read_text() == before and not list(tmp_path.glob("*.tmp"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --offline pytest tests/test_universe_builder.py -q`
Expected: FAIL — `ImportError: cannot import name 'write_universe'`

- [ ] **Step 3: Implement**

Append to `universe_builder.py` (and add `import json`, `import os`, `from pathlib import Path` at the top):

```python
def write_universe(universe: pd.DataFrame, meta: dict, output: Path) -> None:
    """Write the CSV and its .meta.json through temporary files, so a failure leaves the old files."""
    output = Path(output)
    meta_path = output.with_name(f"{output.stem}.meta.json")
    csv_tmp = output.with_name(f"{output.name}.tmp")
    meta_tmp = meta_path.with_name(f"{meta_path.name}.tmp")
    try:
        meta_tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        universe.to_csv(csv_tmp, index=False)
        os.replace(csv_tmp, output)
        os.replace(meta_tmp, meta_path)
    finally:
        for path in (csv_tmp, meta_tmp):
            path.unlink(missing_ok=True)
```

Create `scripts/build_universe.py`:

```python
"""Build config/universe_kr_all.csv from Naver's full ETF list (docs/superpowers/specs/2026-10-10-full-etf-universe-design.md).

    uv run python scripts/build_universe.py [--end YYYY-MM-DD] [--output config/universe_kr_all.csv]

Run it at the start of each month before `rank`, then commit the updated universe.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

from etf_momentum.data import download_prices
from etf_momentum.universe_builder import (
    BENCHMARK,
    build_universe,
    correlation_groups,
    drop_unpriced,
    filter_etf_list,
    recent_returns,
    write_universe,
)

NAVER_ETF_LIST = "https://finance.naver.com/api/sise/etfItemList.nhn"
CURATED = Path("config/universe_kr_pension.csv")  # proxies are carried over from here
WINDOW, MIN_OVERLAP, THRESHOLD = 252, 63, 0.9


def fetch_naver_list() -> list[dict]:
    request = urllib.request.Request(NAVER_ETF_LIST, headers={"User-Agent": "Mozilla/5.0"})
    payload = urllib.request.urlopen(request, timeout=30).read().decode("cp949")
    try:
        return json.loads(payload)["result"]["etfItemList"]
    except (KeyError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Unexpected Naver ETF list format ({exc}); universe not changed") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", default="config/universe_kr_all.csv")
    parser.add_argument("--end", default=None, help="Exclusive price end date (default: latest)")
    parser.add_argument("--cache-dir", default="data")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    items = fetch_naver_list()
    try:
        listing, excluded = filter_etf_list(items)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    end = pd.Timestamp(args.end) if args.end else pd.Timestamp.today().normalize()
    start = (end - pd.Timedelta(days=550)).strftime("%Y-%m-%d")
    raw = download_prices(listing["ticker"].tolist(), start=start, end=args.end, cache_dir=args.cache_dir, refresh=args.refresh, raw=True)
    try:
        listing, unpriced = drop_unpriced(listing, raw)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    returns = recent_returns(raw[listing["ticker"]], BENCHMARK, WINDOW)
    groups = correlation_groups(returns, THRESHOLD, MIN_OVERLAP)
    curated = pd.read_csv(CURATED)
    proxies = {r.ticker: r.proxy for r in curated.itertuples() if isinstance(r.proxy, str) and r.proxy.strip()}
    universe = build_universe(listing, groups, proxies)
    grouped = universe[universe["group"] != ""]
    meta = {
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": NAVER_ETF_LIST,
        "naver_etfs": len(items),
        "excluded": excluded,
        "no_yahoo_prices": unpriced,
        "etfs": len(universe),
        "price_data_last_date": str(returns.index[-1].date()),
        "groups": int(grouped["group"].nunique()),
        "etfs_in_groups": len(grouped),
        "group_rule": {"window_days": WINDOW, "min_overlap_days": MIN_OVERLAP, "min_correlation": THRESHOLD, "linkage": "complete"},
    }
    write_universe(universe, meta, Path(args.output))
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `uv run --offline pytest tests/test_universe_builder.py -q` → 13 passed
Run: `uv run --offline pytest -q` → all pass

- [ ] **Step 5: Commit**

```bash
git add src/etf_momentum/universe_builder.py scripts/build_universe.py tests/test_universe_builder.py
git commit -m "Add scripts/build_universe.py with atomic universe writes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 실제 유니버스 생성, 전략 v2, 문서

**Files:**
- Create: `config/universe_kr_all.csv`, `config/universe_kr_all.meta.json` (스크립트 출력)
- Modify: `config/strategies/pension.json`, `config/strategies/retirement.json`, `README.md`, `CLAUDE.md`

- [ ] **Step 1: Build the universe (network)**

Run: `uv run python scripts/build_universe.py` (run in background with a long timeout; the first price download covers about 740 tickers)
Expected: `Saved: config/universe_kr_all.csv`; meta shows about 1,172 Naver ETFs, about 740 ETFs, a short `no_yahoo_prices` list.

- [ ] **Step 2: Inspect**

Run:
```bash
uv run --offline python -c "
import pandas as pd; from etf_momentum.data import load_universe
u = pd.read_csv('config/universe_kr_all.csv'); print(len(load_universe('config/universe_kr_all.csv')))
g = u[u.group.notna()].groupby('group').ticker.count().sort_values(ascending=False); print(g.head(15))
print(u[u.ticker.isin(['069500.KS','102110.KS','091160.KS','091230.KS','360750.KS','379800.KS','153130.KS'])][['ticker','name','group','retirement_ok','risk_asset']])"
```
Expected: KODEX 200 and TIGER 200 share a group; the S&P500 ETFs share a group; KODEX/TIGER 반도체 share a group; 153130 has `risk_asset=0`. If a group is implausibly large (e.g. > 30 unrelated ETFs), stop and report — complete linkage should not chain.

- [ ] **Step 3: Strategy files v2**

In both `config/strategies/pension.json` and `config/strategies/retirement.json`: `"version": 1` → `"version": 2`, `"universe": "config/universe_kr_pension.csv"` → `"universe": "config/universe_kr_all.csv"`, and `description` →
  - pension: `"연금저축: 국내 상장 ETF 전체, 상위 4개, 그룹당 1개, KODEX 200 시장 필터, 유동성 10억 원"`
  - retirement: `"IRP·DC: 국내 상장 ETF 전체(파생형 제외), 상위 4개, 그룹당 1개, KODEX 200 시장 필터, 유동성 10억 원"`

- [ ] **Step 4: Run smoke rank and tests**

Run: `uv run etf-momentum rank --strategy config/strategies/pension.json --top 10 --output-dir "$SCRATCH/all-smoke"` (`$SCRATCH` = session scratchpad)
Expected: ranking of the full universe, a 2026-09-30 selection with 4 ETFs from 4 different groups, no error.
Run: `uv run --offline pytest -q` → all pass

- [ ] **Step 5: Docs**

- `README.md`: in the 연금 계좌 section add a subsection "전체 ETF 유니버스" stating: 연금 전략 파일은 `config/universe_kr_all.csv`(국내 상장 ETF 전체에서 레버리지·인버스·채권·채권혼합 제외, 약 740개)를 쓴다; 매달 초 `uv run python scripts/build_universe.py` → 커밋 → `rank`; 그룹은 최근 252거래일 수익률 상관 0.9 이상(완전 연결); 퇴직연금 매수 불가는 이름의 합성·선물로 근사; 프록시는 기존 60개 설정만 이전; 기존 `universe_kr_pension.csv`는 연구 결과 재현용으로 남김.
- `CLAUDE.md` 자주 쓰는 명령에 `uv run python scripts/build_universe.py  # 매달 초 유니버스 갱신 후 커밋` 추가, 데이터 주의점에 "전체 유니버스는 현재 상장 ETF만 있어(생존 편향) 백테스트는 비교용" 추가.

- [ ] **Step 6: Commit**

```bash
git add config/universe_kr_all.csv config/universe_kr_all.meta.json config/strategies/pension.json config/strategies/retirement.json README.md CLAUDE.md
git commit -m "Build the full Korean ETF universe and point the pension strategies at it (version 2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 비교용 워크포워드와 연구 표

**Files:**
- Modify: `scripts/research_tables.py`, `docs/research_summary_2026-10-06.md`

- [ ] **Step 1: Add the comparison table to `research_tables.py`** (before writing results, so the code is committed first)

Add after `FILTER_TABLE`:

```python
# Full Korean ETF universe (current listings only: survivorship bias), compared with the 60-ETF pension universe
FULL_UNIVERSE_TABLE = [
    ("연금저축 유니버스 60개 (기존)", "kr_pension_pension_10eok_10bp_robustgrid_tvoff"),
    ("국내 상장 ETF 전체 (비교용)", "kr_all_pension_10eok_10bp_robustgrid_tvoff"),
]
```

and before `portfolio_table(...)` calls:

```python
    out.append(
        "\n### 전체 ETF 유니버스 비교 (연금저축, 워크포워드, 생존 편향 있음)\n\n" + header
    )
    for label, run in FULL_UNIVERSE_TABLE:
        curve = wf_curve(run)
        out.append(row(label, stats(curve, "kr", args.end), stats(curve, "kr", None)))
```

Commit:
```bash
git add scripts/research_tables.py
git commit -m "Add the full-universe comparison table to research_tables.py

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 2: Run the comparison walk-forward (clean tree, background, long timeout)**

Run (confirm `git status --short` is empty first):
```bash
uv run etf-momentum walkforward --start 2010-01-01 --end 2026-10-07 --top-n-grid 3,4 --max-per-group-grid 1 \
  --universe config/universe_kr_all.csv --benchmark 069500.KS --cash 153130.KS --transaction-cost-bps 10 \
  --min-avg-traded-value 1e9 --use-proxies --account pension \
  --output-dir results/walkforward/kr_all_pension_10eok_10bp_robustgrid_tvoff > results/walkforward/kr_all_pension_10eok_10bp_robustgrid_tvoff.log 2>&1
uv run python scripts/research_tables.py --end 2025-02-28
```
Expected: completes; `walkforward_run_config.json` shows the HEAD commit with `"dirty": false`; the new table appears in `results/research_tables.md`; all other tables unchanged.

- [ ] **Step 3: Research summary**

Add a section "전체 ETF 유니버스 비교 (2026-10-10)" to `docs/research_summary_2026-10-06.md` with the two rows exactly as printed by `research_tables.py`, and these caveats: 현재 상장 ETF만 포함(상장폐지 ETF 없음 → 성과 과대 가능), 그룹은 현재 시점 상관으로 정함(사후 정보), 많은 ETF가 2020년 이후 상장이라 초기 구간 후보가 적음, 실제 투자 판단의 기준 수치는 여전히 60개 결과.

- [ ] **Step 4: Commit**

```bash
git add docs/research_summary_2026-10-06.md
git commit -m "Compare the full ETF universe with the 60-ETF pension universe in the research summary

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
