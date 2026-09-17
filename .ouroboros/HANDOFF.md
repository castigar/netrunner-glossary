# 인계 문서 — Netrunner 번역 보조 에이전트

**작성 2026-09-16 · 갱신 2026-09-17.** 새 세션이 이 문서만 읽고 이어받을 수 있도록 쓴다.
사양의 단일 진실 원천은 [`SERVICE.md`](../SERVICE.md)이고, 이 문서는
**지금 어디까지 됐고 다음에 뭘 해야 하는지**만 다룬다.

---

## 0. 30초 요약

- 기획 완료. 1·2단계 구현 완료. **3단계(확정 절차)만 남았다.**
- 최신 브랜치는 `ooo/ralph-6ec830bda576f952b7b2375d3c3868` @ `3db2f79`, **테스트 814개 통과.**
  파이프라인이 실제로 돌아 `pipeline_output.jsonl`·`approved.jsonl`을 만든다.
- 다음 할 일은 §4 작업 1(**Ralph 한 세대 더**). 사용자가 새 세션에서 돌리기로 했다.
- **origin에 안 올라간 게 많다.** `main`이 4커밋 앞서 있고 `ooo/*` 브랜치 대부분이 로컬 전용이다.
- **Ralph를 돌리기 전에 §5의 워크트리 위생을 반드시 확인한다.** 2026-09-17 Ralph 실패
  9회 중 대부분이 이것 때문이었다.

## 1. 좌표

| 항목 | 값 |
|---|---|
| 코드 repo | `castigar/netrunner-glossary` (public) |
| 로컬 main | `C:\Users\SDS\Desktop\sds-ax-practice\mini_pjt` |
| 코퍼스 | `C:\Users\SDS\Desktop\netrunner-corpus\netrunner-cards-json` (`CORPUS_ROOT`) |
| **파이썬 환경** | `C:\Users\SDS\Desktop\sds-ax-practice\.venv\Scripts\python.exe` — **시스템 기본 python 3.14에는 pytest가 없다** |
| Bedrock 자격증명 | `sds-ax-practice/.env` (AWS 3종). 가용 모델은 `availableModelsOnBedrock.md` |
| ouroboros CLI | `C:\Users\SDS\AppData\Local\uv\cache\archive-v0\qVBiREO0OQDRTyJo\Scripts\ouroboros.exe` (PATH에 없음) |
| ouroboros 설정 | `~/.ouroboros/config.yaml` — `auto_evaluate: true`, `auto_evolve: true`가 **이미 켜져 있다** |

실행 시 반드시:

```bash
export CORPUS_ROOT="C:/Users/SDS/Desktop/netrunner-corpus/netrunner-cards-json"
"C:/Users/SDS/Desktop/sds-ax-practice/.venv/Scripts/python.exe" -m pytest -q
```

### 브랜치

| 브랜치 | HEAD | 테스트 | 내용 |
|---|---|---|---|
| **`ooo/ralph-6ec8…`** | **`3db2f79`** | **814 통과** | **최신. 1·2단계 전부 + Ralph 3세대 + 게이트 통합** |
| `feat/rule-gold-eval` | `4dfe72a` | 미측정 | 룰 용어 정답셋 채점기 (별도 세션 산출) |
| `main` | `c1e350d` | — | 기획 문서·Seed·골든 셋. **origin보다 4커밋 앞** |
| `ooo/orch_a3466c696fe8` | `71cdbda` | 694 | phase2c 실행 원본 |
| `ooo/orch_fa2a47fe46cf` | `3ad61ca` | 623 | 게이트 수정 — **`3db2f79`에 통합 완료, 역할 끝남** |
| `ooo/orch_3b1f1bf54116` | `60be3f4` | — | phase2b 이전 단계 |
| `ooo/ralph-83c344…` | `d4b6ff9` | — | 실패한 phase2b 리니지. 버려도 된다 |

**origin에는 `main`(cb6006c)과 `ooo/orch_a6bc6099bdae`만 있다.** 나머지는 전부 로컬이다.

## 2. 2026-09-17에 한 일

### ooo 실행 3회 — 전부 completed

| 페이즈 | AC | 평가 | 테스트 실측 |
|---|---|---|---|
| phase2a (전날) | 8/8 | 4/8 (2회) | 보고 "452 통과" ↔ 실측 23 실패 |
| phase2b | 5/5 | 3/5 → 2/5 | 613 → 623 |
| phase2c | 6/6 | (Ralph로 체인) | 694 |

### Ralph 루프 — 처음으로 완주

`ralph-6ec830bda576f952b7b2375d3c3868` 리니지가 **3세대를 돌았다.** 산출:

```
3db2f79 merge: 게이트 수정 3건 통합 (사람)
eacf9f5 fix: 부분 커밋으로 깨진 두 곳 (사람)
bb83664 feat(AC6): 게이트 3 임계를 실행 중 측정 베이스라인으로 유도
28084e7 feat(AC6): 평가 진입점 — 예측 두 계열·3단 집계
d5056b9 feat(AC7): 필드→카드 합성
f6d96ae feat(AC4): 분포 기반 TM 임계 유도 및 interrupt() 배선
da7aa36 feat(AC6): 진입점 evaluate_pipeline
481916f feat(AC2): 초벌 레코드 계약·safe_wrap 외연·라우팅 실질 구현
```

phase2c 원본 대비 **21파일 +4,985 / −286**, src 모듈 30개 / 테스트 파일 30개.

**Ralph가 사양을 스스로 확장했다.** 이건 예상 밖이었다.
- AC6 문언에서 하드코딩된 "hold-out 100장"을 "파일에서 유도한다"로 고쳤다
- Seed에 없던 **AC7(필드→카드 합성)** 을 추가했다. 라우팅은 필드 단위인데 게이트는
  카드 단위라 그 사이를 잇는 단계가 없었다 — Seed를 쓴 사람이 놓친 빈틈이다
- **AC9(verification manifest)** 도 추가했으나 내용 미확인
- 온톨로지가 0 → 10 → **17필드**로 자랐다. `gate_scoring_subject`,
  `prediction_fallback_policy`, `baseline_provenance`, `ac_freeze_precondition` 등

### 사람이 직접 고친 결함 7건

1. **MCP 도구 호출 규약** — `@mcp.tool()` 데코레이터가 이름을 `FunctionTool`로
   재바인딩해 테스트 23개가 죽고 있었다. `def` 이후 `mcp.tool()(fn)` 등록으로 교체
2. **flavor 미탑재** — `corpus_split`이 플레이버를 안 실어 AC5를 범위 내에서 고칠 수
   없었다. `v2/printings`에서 조인해 980장 중 698장에 실었다
3. **게이트 3 임계** — `THRESHOLD = 0.27` 리터럴을 유도식으로 교체(0.2695)
4. **플레이버 미소비** — 플레이버 판정기가 룰 텍스트를 받고 있었다
5. **게이트 2 참조 채점** — `predictions` 인자가 없어 공식 번역만 채점했다
6. **`check_injection` 시그니처** — Ralph가 호출부만 커밋하고 정의부를 빠뜨려
   테스트 40개가 죽었다. `ko_text` 인자 추가 + `side` 필드
7. **AC7 테스트 언팩** — 5-튜플 ↔ 6-튜플 불일치 2곳

### 룰 용어 정답셋 완성

번역가가 32항목을 라벨링했다(`main` @ `c1e350d`, `data/rule_terms_gold.json`).
unaided 28 + aided 4. 별도 세션에서 채점기(`feat/rule-gold-eval`)도 나왔다.

**채점 전에 알아야 할 것 두 가지:**
- 32항목 중 **16개가 이미 공식 용어집에 있다.** 순수 룰 경로 대상은 16개
  (`extracted` 11 + 미등재 5). 채점기는 두 수치를 나눠 보고해야 한다
- **도달 불가 6개** — `R&D`(토크나이저가 `r`/`d`로 쪼갬), `AP`·`killer`(DF 1),
  `AI`(DF 4), `identity`·`link`(DF 6, 상한 밖). 일부러 남겼다
- 정답셋이 **추출기 결함 2건을 이미 잡았다** — `Archives`에서 41회짜리 대신 1회짜리
  희귀 변형을 골랐고, `HQ`에 `본부 (HQ)` 병기 표기가 역어로 들어왔다

## 3. 확정된 사양 결정 (재논의 불필요)

| # | 결정 |
|---|---|
| ① | **추출 경로를 둘로 분리.** 부제는 `keywords`, 룰 용어는 `text`. 정답셋 88항목은 부제 경로만 채점. 룰 경로는 수동 정답셋 32항목으로 채점 |
| ② | 룰 경로 후보 상한은 Dice 전역 순위가 아니라 **2단계** — EN 빈도 상위 N → EN별 Dice 상위 k |
| ③ | 하드 게이트 3 임계 = **TM 베이스라인 × 0.7**. 상수로 박지 않는다 |

## 4. 다음에 할 일

### 작업 1 — Ralph 한 세대 더 ★ 최우선

사용자가 **새 세션에서 돌리기로 했다.** 2026-09-17 세션에서 한 번 걸었다가 취소했다.
리니지는 Gen 1~3이 completed이므로 다음은 **Gen 4**다.

```
start_ralph(
  lineage_id = "ralph-6ec830bda576f952b7b2375d3c3868",
  project_dir = "C:\Users\SDS\.ouroboros\worktrees\orch_fa2a47fe46cf\orch_a3466c696fe8",
  per_iteration_timeout_seconds = 7200,   # 상한값. 기본 1800은 짧다
  max_generations = 1,                    # 한 세대만 → Gen 4
  max_total_seconds = 7800
)
```

**세대 상한 두 곳을 모두 풀어야 한다.**

| 위치 | 값 | 비고 |
|---|---|---|
| `~/.ouroboros/config.yaml` → `execution.auto_evolve_max_generations` | `3` → **`4`로 변경 완료** (2026-09-17) | 평가가 거부해 Ralph가 **자동 체인될 때** 적용되는 전역 상한. 백업은 `config.yaml.bak-20260917` |
| `start_ralph`의 `max_generations` 인자 | 호출마다 지정 | 이 호출에서 돌릴 세대 수. 리니지 누적이 아니라 **이번 호출분**이다 |

**타임아웃은 반드시 명시한다.** `per_iteration_timeout_seconds` 기본값이 1800초(30분)라
Gen 2가 작업 중 잘렸다. Gen 3는 **80분** 걸렸으므로 최대값 7200(2시간)을 쓴다.
`max_total_seconds`는 `max_generations × per_iteration`보다 크게 잡는다 — 여러 세대를
돌릴 거면 그만큼 늘려야 한다(예: 2세대면 15000).

**시작 전 §5의 워크트리 위생을 확인한다.**

### 작업 2 — 룰 용어 채점 실행

정답셋(32항목)과 채점기(`feat/rule-gold-eval`)가 모두 준비됐다. 아직 돌리지 않았다.
§2의 "채점 전에 알아야 할 것 두 가지"를 반영해 결과를 읽는다.

### 작업 3 — AC9 내용 확인

Ralph가 추가한 AC9(verification manifest)의 내용을 확인해 **페이즈 범위 안인지
판정한다.** AC7은 빈틈 메우기로 판단했으나 AC9은 미확인이다. 확장이 무제한이면
페이즈 경계가 무너진다.

### 작업 4 — 푸시

`main` 4커밋과 `ooo/ralph-6ec8…`가 로컬 전용이다. 유실 위험이 있다.

### 작업 5 — phase3 (Issue 동기화 · Pages 검수 뷰)

Seed 미작성. 분할 기준은 §6.

## 5. 함정 모음 (직접 밟은 것들)

### Ralph는 깨끗한 워크트리를 전제한다 ★ 가장 자주 걸린 것

2026-09-17 Ralph 시도 **9회 중 정상 시작 3회.** 실패 원인이 전부 워크트리 상태였다.

```
Cannot start task worktree from a dirty checkout        (3회)
Task branch already checked out in another worktree     (1회)
Git command failed: worktree add ...                    (3회)
```

**돌리기 전 체크리스트:**
1. `git worktree list`로 대상 브랜치를 **아무 워크트리도 점유하지 않는지** 확인.
   점유 중이면 그 워크트리에서 `git checkout --detach`
2. `git worktree prune`으로 잔존 등록 제거 (디렉터리가 사라져도 등록은 남는다)
3. 대상 워크트리가 **클린**해야 한다. 손으로 파일 하나 만들어 두면 루프가 안 돈다
4. `.gitignore`에 런타임 내부 파일이 들어 있어야 한다 — 브랜치마다 따로 필요하다
   ```
   .ouroboros/context_pack.json
   .ouroboros/mechanical.toml
   .ouroboros_eval_artifact.md
   ```

### 체크포인트 커밋은 원자적이지 않다

Ralph가 AC 단위로 커밋하면서 **그 AC가 건드린 파일 일부를 빠뜨렸다.**
`translation_graph.py`의 호출부는 커밋됐는데 `injection_guard.py`의 정의부는
커밋되지 않아 테스트 40개가 죽었다. 워크트리가 삭제되며 그 수정만 사라졌다.

**"커밋됐으니 안전하다"는 커밋 기준으로만 참이다.** 동작은 별도로 확인해야 한다.
`git log <범위> -- <파일>`이 비어 있으면 그 파일 변경이 커밋에 없다.

### 자기 보고를 믿지 않는다

2026-09-16~17 실행 4회 중 **자기 보고와 실측이 일치한 것은 2회뿐**이다.
phase2a는 "All 452 tests pass"라고 했으나 실측은 23 failed / 442 passed였고,
수집 수 465조차 보고와 달랐다. Ralph 브랜치도 3세대를 돌고 40개 실패 상태였다.

**합격을 선언하기 전에 `.venv`로 pytest를 직접 돌린다.**

### evaluate 점수는 재현되지 않는다

같은 세션 `orch_fa2a47fe46cf`를 두 번 채점해 3/5 → 2/5가 나왔다. 임계 0.80 근처에서
`ac_compliance=true`인데 점수만으로 갈린 항목은 특히 흔들린다. 반면
`ac_compliance=false`로 찍힌 항목은 근거가 구체적이고 재현 가능했다.

**`ooo run`은 2026-09-16~17 이틀간 `all_passed=True`를 한 번도 내지 않았다**
(4/8, 3/5, 2/5, 4/6).

### 드리프트 지표는 그대로 믿을 게 못 된다

목표문을 거의 그대로 복창한 대조군 입력도 goal drift 0.71(임계 0.3)이 나왔다.
`constraint_violations`와 `current_concepts`는 호출자가 값을 넣으므로 그 성분(50%)은
입력이 결과를 정한다. **지표보다 AC 목록과 대조하는 편이 낫다.**

### Git Bash가 `/FLAG`를 경로로 바꾼다

`tasklist /FI "PID eq N"` → `/FI`가 `C:/Program Files/Git/FI`로 변환돼 실패한다.
`//FI`를 쓰거나 `MSYS_NO_PATHCONV=1`, 또는 PowerShell을 쓴다.
**`2>/dev/null`로 에러를 지우면 실패가 음성 결과로 위장한다** — 완료 감시가 이 때문에
오탐을 냈다.

### 잡 완료 판정은 교차 확인한다

MCP `job_wait`은 워커가 죽은 뒤에도 한동안 `running`을 반환한다.
`~/.ouroboros/detached-jobs/job_<id>.status.json`의 `worker_pid`로 프로세스 생존을
확인하고, `job_result`도 함께 본다. `timeout_seconds`는 5를 넘겨도 5로 잘린다.

### 웹 대시보드는 MCP 경로에서 뜨지 않는다

응답의 `dashboard_url`은 **주소를 계산해 넣을 뿐 서버를 띄우지 않는다.**
`dashboard_url` 생성 코드는 CLI(`run`/`auto`)에만 있고 MCP 모듈에는 없다.

수동 기동은 되지만 데이터가 안 보인다:
```
python -m ouroboros.dashboard_web     → localhost:<자동 선택 포트>
/api/runs → {"runs": [], "error": "picker_index_contract_unavailable"}
```
EventStore의 picker 프로젝션이 깨졌다(`canonical link mismatch`). 대시보드는 읽기
전용이라 못 고치고, writer(MCP 서버)가 인덱스를 다시 세워야 한다.

TUI는 프로젝션을 우회해 EventStore를 직접 읽지만 **의존성이 빠져 있다.**
안내 문구의 `--from 'ouroboros-ai'`에는 `[tui]`가 없어 그대로 따라 하면 실패한다:
```bash
uvx --python ">=3.12" --from "ouroboros-ai[tui]" ouroboros tui monitor --db-path "C:\Users\SDS\.ouroboros\ouroboros.db"
```

### 코퍼스·데이터 관련

- **`Trace[N]` ↔ `<trace>추적 N</trace>`** — 리터럴 멀티셋 비교로 구현하면 공식 번역의
  13.7%가 불합격한다. 정규화 대응표로 구현하고 `<strong>/<em>`은 게이트에서 제외
- **미번역 레코드 필터링을 통계보다 먼저** — 안 하면 부제 용어의 54%가 사라진다
- **Dice는 용어다움이 아니라 배타성을 잰다** — 전역 상한으로 자르면 `trash`(55,471위)가
  잘린다. EN을 빈도로 먼저 고르고 그 안에서 Dice를 쓴다
- **형태소 분석기 없이 한국어 용어를 뽑지 말 것** — `ko_morphology.normalize_eojeol`
- **`0.359`는 폐기된 수치다** — v2 split 실측 천장은 0.385
- **`netrunner-cards-json-old`는 초벌본이 아니다** — 최종본 스냅샷
- **플레이버는 `v2/cards`가 아니라 `v2/printings`에 있다** — printing id로 조인
- **`.env`가 상위 경로에 있다** — 이 repo는 public이다

## 6. Seed 현황

| Seed | 범위 | AC | 상태 |
|---|---|---|---|
| phase1 | 자산 구축 + TM 베이스라인 | 8 | 실행 완료 |
| phase2a | 번역 검토 큐 · 가드레일 · MCP 4도구 | 8 | 실행 완료, 평가 4/8 |
| phase2b | 하드 게이트 채점 · 관찰 지표 | 5 | 실행 완료, 평가 2/5 |
| phase2c | 온라인 파이프라인 본체 | 6→9 | 실행 완료, Ralph 3세대 진행 |
| phase3 | Issue 동기화 · Pages 검수 뷰 · 패턴 매핑 | 7 | **미작성** |

phase2b·phase2c Seed는 `main`의 `.ouroboros/`에 있다.

## 7. 아직 안 정한 것

1. 2021–2022 번역분 142장의 코퍼스 편입 여부
2. 편집거리 감소폭의 납품 이후 추적 방법·주기
3. 가드레일 통과율을 하드 게이트로 승격할지 관찰 지표로 둘지
4. **브랜치 정리 방침** — `ooo/*` 7개가 쌓였다. `ooo/ralph-6ec8…`로 수렴시키고
   나머지를 정리할 시점
5. **AC 확장을 어디서 멈출지** — Ralph가 AC7·AC9를 스스로 추가했다.
   온톨로지의 `ac_freeze_precondition`이 그 기준을 정의하려는 것으로 보이나 미확인

### 알려진 사소한 결함 (미수정)

- `glossary.json`의 `counts.official`이 **160**인데 실제 항목은 **154**개다.
  `OfficialGlossary.__len__`이 카테고리별 합을 세고 `all_terms()`는 평탄화하며 중복
  id 6개를 합친다(`src/load_official_glossary.py:46-54`). 보고 숫자만 어긋난다
- `obs_llm_judge`에 청크·체크포인트·프롬프트 지문·dropped 집계가 없다.
  `src/term_judge.py`가 그 규율의 기존 구현이다
