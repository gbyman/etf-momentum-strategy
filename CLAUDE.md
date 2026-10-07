# CLAUDE.md

## 이 도구의 용도

- 목적: 연금저축·IRP/DC 계좌 등에서 **보유할 모멘텀 ETF를 고르는 것**.
- 사용자가 직접 정하는 것: 실제 투자 비중, 핵심(S&P500)·보조(모멘텀) 비율, 계좌별 위험자산 한도(IRP·DC 70%)와 주문.
- 그래서 비중 배분이나 계좌 한도를 맞추는 기능(예: 여러 슬리브를 합친 70% 상한)은 제안하지 않는다.
- 제안은 ETF 선정의 질에 집중한다: 순위 규칙, 유니버스, 데이터 정확성, 검증.
- 사용자는 한국어로 소통한다. 문서·결과 설명도 한국어로 쓴다.

## 자주 쓰는 명령

```bash
uv run pytest                                  # 전체 테스트
uv run etf-momentum rank --universe config/universe_kr_pension.csv \
  --benchmark 069500.KS --cash 153130.KS --account pension \
  --lookback-years 3 --min-avg-traded-value 1e9 --top 15 \
  --top-n 4 --max-per-group 1 --risk-on-benchmark      # 현재 순위 + 직전 월말 기준 선택 종목
uv run python scripts/research_tables.py --end 2025-02-28  # 연구 문서 표 재생성
```

워크포워드·백테스트·조합 재현 명령은 `docs/research_summary_2026-10-06.md`의 "재현 방법"에 있다.

## 연구 결과를 만들 때의 규칙

- 데이터 기간은 `--end`(조합 spec은 `"end"`)로 고정한다. 현재 연구 기준은 `--end 2026-10-07`.
- **코드를 먼저 커밋한 뒤** 결과를 만든다. `*_run_config.json`의 `code_revision`이 `dirty=false`여야 한다.
- 결과를 만드는 동안 추적 중인 파일을 고치거나 새 파일을 두지 않는다. untracked 파일도 dirty로 기록된다.
- 시간이 오래 걸리는 워크포워드 묶음은 `run_in_background`와 긴 timeout으로 실행한다. 600초 foreground 제한에 걸리면 중단된다.
- 연구 문서 수치를 바꾸면 `scripts/research_tables.py` 출력과 일치하는지 확인한다.
- `results/`와 `data/`는 git에 포함되지 않는다.

## 데이터·모델에서 주의할 점

- 한국과 미국 티커를 한 번에 `download_prices`로 받으면, 두 시장의 날짜를 합치고 빈칸을 앞 값으로 채운다. 그래서 거래일이나 달력이 중요한 곳(포트폴리오 평가 달력, 슬리브, 환율)은 티커별로 따로 받는다.
- 국내 상장 해외 ETF의 상장 전 프록시와 조합 평가에서 미국 슬리브는 **한국 거래일보다 이른 날짜의** 미국 종가와 환율을 쓴다. 같은 날 미국 종가는 미래 정보다.
- 2025-03 이후 한국 시세(야후)는 외부 자료와 대조하지 않았다. 주 기준 기간은 2016-02-29 ~ 2025-02-28.
- 139280·139290 상품명은 확인이 필요하다.
- 계좌별 매수 가능 여부(`pension_ok`, `retirement_ok`)는 일반 규정 기준이다.

## Git

- 커밋은 `main`에 직접 한다. 푸시는 사용자가 요청할 때만 한다.
- 커밋 메시지는 영어, 끝에 `Co-Authored-By` 줄을 붙인다.
- 외부 리뷰 문서는 `docs/review_*.md`로 들어온다. 지적을 직접 재현해 확인한 뒤 수정한다.
