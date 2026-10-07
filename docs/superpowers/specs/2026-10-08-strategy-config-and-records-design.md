# 전략 설정 파일과 전략별 기록 설계

작성일: 2026-10-08
관련: `docs/concept_improvements_2026-10-07.md` 1·6번, `docs/review_concept_2026-10-07_codex.md` 6번

## 목적

- 같은 ETF 선정 규칙을 `rank`, `weekly`, `backtest`에서 **그대로** 쓰게 한다. 긴 명령어를 매번 입력하다 규칙이 달라지는 일을 막는다.
- 주간 기록이 시장·계좌·설정끼리 섞이지 않게 **전략별로 분리**한다.
- 매월 무엇을 골랐는지를 **덮어쓰지 않는 기록**으로 남긴다. 기록 보관만 하고, 성과 추적은 하지 않는다(나중에 붙일 수 있는 형식만 맞춘다).

관리할 전략은 연금저축(`pension`)과 IRP(`retirement`) 두 개다. 투자 비중·계좌 한도·주문은 사용자가 직접 정하므로 이 설계의 범위가 아니다.

## 1. 전략 설정 파일

### 위치와 형식

전략마다 JSON 파일 하나: `config/strategies/<id>.json`.

```json
{
  "id": "pension",
  "version": 1,
  "description": "연금저축: 상위 4개, 그룹당 1개, KODEX 200 시장 필터",
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
    "min_avg_traded_value": 1e9
  },
  "selection": {
    "top_n": 4,
    "max_per_group": 1,
    "risk_on_benchmark": true,
    "risk_on_tickers": null
  }
}
```

`retirement.json`은 `"id": "retirement"`, `"account": "retirement"`, 설명만 다르고 나머지는 같다.

### 필드

| 필드 | 필수 | 의미 |
|---|---|---|
| `id` | 예 | 기록 폴더 이름. 소문자·숫자·`-`·`_`만 허용 |
| `version` | 예 | 규칙을 바꿀 때 사용자가 올리는 정수(1 이상) |
| `description` | 예 | 사람이 읽는 설명 |
| `universe` | 예 | 유니버스 CSV 경로(프로젝트 루트 기준) |
| `account` | 예 | `all` / `pension` / `retirement` |
| `benchmark` | 예 | 벤치마크·기본 시장 필터 티커 |
| `cash` | 예 | 현금 ETF 티커. `null`이면 미투자 현금 |
| `use_proxies` | 예 | 상장 전 프록시 사용 여부 |
| `signal.lookbacks`, `signal.weights` | 예 | 3개씩, 가중치 합은 양수 |
| `signal.skip_days` | 예 | 0 이상 정수 |
| `signal.sma_window`, `signal.volatility_window` | 예 | 1 이상 정수 |
| `signal.rank_by` | 예 | `weighted_momentum` / `risk_adjusted_score` |
| `signal.min_avg_traded_value` | 예 | 0 이상 숫자 또는 `null`(필터 없음) |
| `selection.top_n` | 예 | 1 이상 정수 |
| `selection.max_per_group` | 예 | 0 이상 정수(0이면 제한 없음) |
| `selection.risk_on_benchmark` | 예 | 시장 필터 사용 여부 |
| `selection.risk_on_tickers` | 예 | 시장 필터 티커 목록 또는 `null`(벤치마크 사용) |

명시하지 않은 신호 설정(`min_weighted_momentum`, `min_trend_strength`, `min_relative_momentum`, `liquidity_window`, `volatility_floor`)은 현재 코드 기본값을 쓴다. 이 필드들은 이번 설계에서 파일에 넣지 않는다(지금 연구에서 바꾼 적이 없는 값이라서).

### 식별

- **config_hash**: 파일 내용을 키 정렬한 JSON으로 직렬화한 SHA-256의 앞 12자리. 공백·키 순서가 달라도 같은 내용이면 같은 값이다. `version`을 올리지 않고 규칙을 바꿔도 해시가 바뀐다.
- `id`는 기록 위치를 정하고, `version`·`config_hash`는 기록마다 남는다.

### 명령 동작

- `rank`, `weekly`, `backtest`에 `--strategy <파일>`을 주면 **선정 규칙 설정 전부를 파일에서** 가져온다.
- 선정 규칙 옵션을 명령줄에도 주면 **사용법 오류(종료 코드 2)로 멈춘다.** 대상 옵션: `--universe`, `--benchmark`, `--cash`, `--account`, `--use-proxies`, `--lookbacks`, `--weights`, `--skip-days`, `--sma-window`, `--volatility-window`, `--rank-by`, `--min-avg-traded-value`, `--top-n`, `--max-per-group`, `--risk-on-benchmark`, `--risk-on-ticker`, `--min-weighted-momentum`, `--min-trend-strength`, `--min-relative-momentum`, `--require-relative-strength`, `--max-risky-weight`, `--proxy-fx`.
- 함께 줄 수 있는 옵션(선정 결과를 바꾸지 않음):
  - 공통: `--end`, `--refresh`, `--top`, `--output-dir`, `--cache-dir`, `--lookback-years`
  - `weekly`: `--watch-rank`, `--leader-rank`, `--rise-threshold`, `--drop-threshold`, `--no-save-history`
  - `backtest`: `--start`, `--rebalance`, `--transaction-cost-bps`, `--weighting`, `--accounting`, `--target-vol`, `--max-position-weight`
- `--strategy` 없이 실행하면 지금과 똑같이 동작한다. `walkforward`, `compare`, `portfolio`는 `--strategy`를 받지 않는다(여러 설정을 비교하는 연구용이라서).
- `account: retirement`인 전략의 백테스트는 지금처럼 위험자산 70% 상한을 적용한다(`--max-risky-weight`는 파일 규칙에 포함되므로 명령줄에서 바꿀 수 없다).

## 2. 전략별 기록

기록 위치: `<output-dir>/strategies/<id>/` (기본 `results/strategies/<id>/`).

### 주간 기록 `weekly_history.csv`

- 현재 주간 스냅샷 컬럼 + `version`, `config_hash`.
- `weekly --strategy`는 **같은 config_hash인 가장 최근 이전 기록과만** 비교한다.
- 가장 최근 이전 기록의 해시가 다르면 비교하지 않고 `설정이 바뀌어(버전 1→2) 새 기준으로 시작합니다`를 출력한다. 이번 실행은 새 기준 스냅샷으로 저장한다.
- 같은 날짜·같은 해시로 다시 실행하면 그 행만 교체한다. 다른 해시의 같은 날짜 기록은 그대로 둔다.
- `--strategy` 없는 `weekly`는 지금처럼 `results/rank_history.csv`를 쓴다. 기존 파일은 옮기거나 고치지 않는다.

### 월말 신호 기록 `signals.csv` (추가만 함)

`rank --strategy`를 실행할 때마다 직전 월말 선택을 기록한다. ETF 하나당 한 행:

| 컬럼 | 내용 |
|---|---|
| `signal_date` | 직전 월말 기준일 |
| `recorded_at` | 기록 시각(초, 로컬 시간대 포함) |
| `strategy_id`, `version`, `config_hash` | 선택을 만든 규칙 |
| `code_commit`, `code_dirty` | 코드 버전과 커밋 안 된 변경 여부 |
| `price_last_date` | 기록 시점의 가격 데이터 마지막 날짜 |
| `market_filter` | `on` / `off` / `unused`(필터 미사용) |
| `revision` | 1부터 |
| `status` | `selected` / `group_limit` / `cash` |
| `rank_in_selection` | `selected`의 순서(1부터), 나머지는 비움 |
| `ticker`, `name`, `group` | 종목 정보(`cash`는 현금 ETF 티커) |
| `slots` | `cash` 행의 빈 자리 수 |

기록 규칙(**기존 행은 절대 수정·삭제하지 않는다**):

1. 같은 `signal_date` + `config_hash` 기록이 없으면 `revision=1`로 추가한다.
2. 있으면, 최신 revision의 내용(`status`, `ticker`, 순서, `market_filter`, `slots`)과 비교한다.
   - 같으면 추가하지 않고 `이미 기록됨`을 출력한다.
   - 다르면 원래 기록을 두고 `revision=최신+1`로 추가한다. 무엇이 달라졌는지 경고로 출력한다.
3. 규칙이 바뀌어 해시가 다르면 같은 달이라도 별도 기록(해시별 revision 1부터)이다.

`latest_ranking.csv`, `latest_selection.csv`는 지금처럼 출력 폴더에 저장한다.

## 3. 오류 처리

모두 다운로드 전에 멈춘다.

- 전략 파일: 필수 필드 누락, 모르는 필드(오타 방지), 범위 밖 값, 잘못된 `id`, 없는 유니버스 파일 → 무엇이 문제인지 적고 멈춘다.
- `--strategy`와 선정 옵션 동시 지정 → 충돌한 옵션 이름을 적고 사용법 오류(종료 코드 2).
- 기록 파일을 읽을 수 없거나 필요한 컬럼이 없음 → 덮어쓰지 않고 멈춘다. 기록을 조용히 새로 만들지 않는다.
- 커밋 안 된 코드 변경이 있어도 기록은 남기고 `code_dirty=true`로 표시한다.

## 4. 테스트

| 영역 | 확인할 것 |
|---|---|
| 전략 파일 | 정상 파일 로드, 모르는 필드, 범위 밖 값, 잘못된 id, 없는 유니버스, 같은 내용 다른 공백의 해시 동일 |
| 충돌 | `--strategy` + `--top-n` → 종료 코드 2, 다운로드 없음 |
| 신호 기록 | 첫 실행 revision 1, 같은 결과 재실행 시 추가 없음, 데이터가 바뀌면 revision 2 추가하고 원래 행 그대로, 규칙 변경 시 새 해시로 별도 기록 |
| 주간 기록 | 같은 해시끼리만 비교, 해시가 바뀌면 비교 없이 안내, pension·retirement 폴더 분리, 같은 날짜 다른 해시 공존 |
| 일관성 | 같은 전략 파일로 `rank`의 직전 월말 선택과 `backtest`의 그 월말 보유가 일치 |
| 기존 동작 | `--strategy` 없이 기존 테스트 전부 통과 |

## 5. 처음 만들 파일

- `config/strategies/pension.json`, `config/strategies/retirement.json`: 현재 추천 설정(상위 4개, 그룹 1, KODEX 200 시장 필터, 유동성 10억 원, 프록시 사용).
- README·CLAUDE.md의 추천 명령을 `--strategy` 형태로 바꾼다.

## 범위 밖

- 기록된 신호의 성과 추적(모의 운용 수익률).
- 투자 비중, 계좌 한도 맞추기, 주문 수량.
- `walkforward`·`compare`·`portfolio`의 전략 파일 지원.
- 기존 `results/rank_history.csv` 정리·이전.
