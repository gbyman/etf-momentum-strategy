# 재현 자료

이 폴더는 각 담당자의 임시 실험 중 핵심 코드와 출력을 보존한 사본이다. 운영 캐시·신호·공식 연구 결과가 아니다. 소스 지문은 상위 `source_snapshot.json`, 복사한 파일의 지문은 `evidence_hashes.json`, 사용 가능한 로컬 가격/거래대금 입력의 지문은 `input_hashes.json`을 참고한다.

| 파일 | 의미 |
|---|---|
| `claude_g01_paths.py`, `claude_g01_before.log`, `claude_g01_after.log` | G01 실제 월말 선정/필터의 수정 전후 대조 |
| `claude_g02_check.py`, `claude_g02_check.log` | 네이버 실패·다음 복구·retry 잔여물·최신 부분 조회 |
| `claude_normal_check.py`, `claude_normal_check.log` | 정상 모멘텀·보유 회계·초기 비용 검산 |
| `claude_new_tests_before.log` | 신규 회귀 테스트의 수정 전 4실패/2통과 |
| `claude_related_tests.log`, `claude_full_tests.log` | 구현 단계의 관련 81개 및 전체 374개 테스트 |
| `gemini_verify_all.py`, `gemini_results.json` | 독립 합성 경계·회계 계산·로컬 가격 스칼라 검산 |
| `gemini_related_tests.log`, `gemini_full_tests.log` | 검증 단계의 관련 55개 및 전체 374개 테스트 |
| `gemini_snapshot_before.json`, `gemini_snapshot_after.json` | 검증 전후 소스 확인. 최초 before의 집계는 compact JSON을 써 지정된 집계와 다르지만, 개별 68개 파일은 모두 일치했다. after는 지정한 기본 직렬화로 일치했다. |
| `coordinator_g01_baseline.log`, `coordinator_g01_fixed.log` | GPT가 정확한 기존 HEAD의 임시 소스 사본과 수정 작업 트리를 직접 비교한 결과 |
| `coordinator_gemini_results.json`, `coordinator_gemini_replay_source.json` | GPT가 Gemini의 최종 스크립트를 별도 출력 경로로 재실행한 결과와 원 스크립트 지문 |

## 독립 스크립트 다시 실행

현재 작업 공간의 프로젝트 루트에서 실행한다. 스크립트는 원 실험의 절대 프로젝트 경로와 임시 출력 경로를 사용한다. 다른 체크아웃으로 옮겼다면 코드의 프로젝트 경로를 맞추고, 그 변경을 기록한다. 로컬 실제 자료의 검산에는 `input_hashes.json`에 적힌 입력이 필요하다.

```sh
mkdir -p /private/tmp/etf-orca-g01-g03-20261017/gemini/mpl /private/tmp/etf-orca-g01-g03-20261017/gemini/cache
env PYTHONPATH=src MPLCONFIGDIR=/private/tmp/etf-orca-g01-g03-20261017/gemini/mpl XDG_CACHE_HOME=/private/tmp/etf-orca-g01-g03-20261017/gemini/cache .venv/bin/python docs/workflows/2026-10-17-g01-g03/evidence/gemini_verify_all.py
```

기대/실제 대조는 출력 JSON의 `matches_expected` 항목을 확인한다. `price_fresh=False` 같은 값은 결측 사례의 올바른 예상 결과일 수 있으므로 모든 False를 실패로 집계하지 않는다.

관련·전체 테스트 묶음은 서로 겹친다. Claude와 Gemini의 374개 실행도 같은 저장소 테스트 집합이므로 개수를 더하지 않는다. 별도 스크립트의 12개 기대값 대조 묶음 역시 pytest 12개라는 뜻은 아니다.
