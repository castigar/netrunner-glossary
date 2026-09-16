# 인계 문서 — Netrunner 번역 보조 에이전트

**작성 2026-09-16.** 새 세션이 이 문서만 읽고 이어받을 수 있도록 쓴다.
사양의 단일 진실 원천은 여전히 [`SERVICE.md`](../SERVICE.md)이고, 이 문서는
**지금 어디까지 됐고 다음에 뭘 해야 하는지**만 다룬다.

---

## 0. 30초 요약

- 기획은 끝났다. `SERVICE.md` 9절 완성, Seed 28 AC 확정.
- 구현은 **1단계(자산 구축)만** 있고, 그마저 **결함 2건이 남아 있다.**
- 코드는 `netrunner-glossary` repo의 `ooo/orch_a6bc6099bdae` 브랜치에 있다.
- 다음 할 일은 §4의 작업 1(추출 경로 분리)이다. 그게 끝나야 LLM 판정이 의미를 갖는다.
- **인터뷰·기획을 다시 하지 말 것.** 사양 결정은 §3에 전부 확정돼 있다.

## 1. 좌표

| 항목 | 값 |
|---|---|
| 코드 repo | `castigar/netrunner-glossary` (public) |
| 작업 브랜치 | `ooo/orch_a6bc6099bdae` ← **여기에 구현이 있다** |
| `main` | 기획 문서만 (`SERVICE.md`, `.ouroboros/`) |
| 로컬 워크트리 | `C:\Users\SDS\.ouroboros\worktrees\mini_pjt\orch_a6bc6099bdae` |
| 로컬 main | `C:\Users\SDS\Desktop\sds-ax-practice\mini_pjt` |
| 코퍼스 | `C:\Users\SDS\Desktop\netrunner-corpus\netrunner-cards-json` (`CORPUS_ROOT`) |
| 사용자 이력 repo | `...\netrunner-corpus\netrunner-cards-json-old` — **학습·평가에 쓰지 말 것** (§5 참조) |
| Bedrock 자격증명 | `sds-ax-practice/.env` (AWS 3종). 가용 모델은 `availableModelsOnBedrock.md` |
| Seed | `.ouroboros/seed.yaml` (28 AC) + `seed-phase1.yaml` (8 AC) |

실행 시 반드시:
```bash
export CORPUS_ROOT="C:/Users/SDS/Desktop/netrunner-corpus/netrunner-cards-json"
```

## 2. 지금까지의 진행

### 기획 (완료)

`SERVICE.md` 9절 + `pm.md`(PRD) + `corpus-findings.md`(코퍼스 실측).
실측이 초기 전제 3개를 뒤집었고, 그 결과가 전부 사양에 반영돼 있다.

### Ouroboros 실행 — `ooo run` **총 4회, 성공 1회 / 실패 3회**

실패 원인이 매번 달랐다. 같은 실수를 반복하지 않도록 job id까지 남긴다.

| # | Seed | AC | job_id | 소요 | 결과 · 원인 |
|---|---|---|---|---|---|
| 1 | seed.yaml | 28 | `job_8a6b42e25586` | 5초 | **failed** — 빈 git 저장소라 worktree 생성 불가 |
| 2 | seed.yaml | 28 | `job_198c7c27903d` | 47초 | **failed** — 플래너가 28 AC를 7 스테이지에 배정하다 자기 위상 정렬 위반 (`AC 10 depends on AC 20, but both are assigned to stage 7`) |
| 3 | seed-phase1.yaml | 8 | `job_87e022af4109` | 0.1초 | **failed** — `semantic_ac_key`가 `^ac_[a-f0-9]{16}$` 위반 |
| 4 | seed-phase1.yaml | 8 | `job_88f831f9be6c` | 31분 | **completed** — 8/8 AC, 테스트 통과 |

1~3회는 코드를 한 줄도 생산하지 못했다. 실제 산출은 4회차 하나뿐이고,
그마저 §2 "검수 결과"의 결함 3건이 있었다.
누적 실행 시간은 약 32분이지만, 결함 수정에 든 시간이 그보다 길었다.

**교훈 — 다음 페이즈 실행 시 반드시 지킬 것:**
- AC를 **10개 이하**로 유지한다. 28개는 플래너가 못 넘는다.
- `semantic_ac_key`는 `ac_` + 소문자 hex 16자리. 슬러그를 쓰면 검증에서 죽는다.
- 대상 repo에 커밋이 최소 1개 있어야 한다.
- `generate_seed`의 `session_context` 경로는 **파일을 쓰지 않는다.** 응답 YAML을
  직접 디스크에 저장해야 한다.

### 검수 결과 (중요)

실행은 8/8 통과로 보고했지만 **실질은 통과가 아니었다.** 결함 3건을 수정했고
(커밋 `1ed1204`), 그 과정에서 **결함 2건을 새로 발견했다.**

수정 완료:

1. **TM 검색기** — dense 성분이 임베딩이 아니라 bag-of-words 빈도 벡터였다.
   BM25와 둘 다 어휘 신호라 의미 검색이 없었고, 자명한 difflib보다 나빴다.
   후보 구성을 전부 측정해 BM25 + dense(MiniLM) + char 3-5gram TF-IDF로 교체.
   **0.4375 → 0.3850.**
2. **산출물** — `glossary.json`/`patterns.json`/`conflicts.json`이 하나도 없었고
   `patterns.json` 생성기는 존재조차 안 했다. `pattern_extractor.py`와
   `build_assets.py`를 추가해 실제로 산출한다.
3. **게이트 완화** — 테스트가 `[0.25, 0.55]`로 넓혀져 자기 회귀를 통과시키고 있었다.
   `0.385 ± 0.025`로 좁혀 이전 구현의 0.4375가 반드시 실패하게 했다.

현재 테스트 145개 전부 통과.

## 3. 확정된 사양 결정 (재논의 불필요)

2026-09-16에 사용자가 확정했다.

| # | 결정 |
|---|---|
| ① | **추출 경로를 둘로 분리.** 부제는 `keywords` 필드에서, 룰 용어는 `text` 필드에서 추출. 정답셋 88항목은 `keywords` 경로에만 적용. 룰 경로는 **수동 라벨링 정답셋 30~50개**를 따로 만들어 채점하고, 하드 게이트 1이 간접 검증을 겸한다 |
| ② | 부제 경로는 필드 단위라 후보가 수백 규모로 떨어진다. 룰 경로는 **Dice 상위 N개 상한** |
| ③ | 하드 게이트 3 임계 **≤ 0.27** (실측 기준선 0.385 × 0.7) |

## 4. 다음에 할 일 (순서대로)

### 작업 1 — 추출 경로 분리 ★ 최우선

**왜 최우선인가:** 지금 후보가 76,309개다. 이대로는 LLM 판정이 비용 이전에 무의미하고,
노이즈 n-gram 쌍끼리 충돌로 잡혀 충돌 목록이 1,822건이다(용어집에 남은 건 174개).

**할 일:**
- `corpus_split.load_clean_corpus()`가 `keywords` 필드도 싣도록 확장한다.
  현재 레코드 키는 `id / date / en_text / ko_text` 뿐이다.
- 부제 추출기를 새로 만든다. `keywords`는 ` - ` 구분 리스트이고 EN↔KO가 위치로
  1:1 대응한다. 원소 수가 다른 카드는 정렬이 어긋나므로 건너뛴다.
- `term_extraction_eval.evaluate_extraction()`을 부제 경로에 물린다.
- 룰 경로에 Dice 상위 N개 상한을 넣는다. `term_candidate_extractor.generate_candidates()`가
  Dice/PMI를 **계산만 하고 거르지 않는다** — 이걸 고친다.

**검증:** 부제 경로 정밀도·재현율을 보고한다. 앞선 세션에서 같은 데이터를 직접 측정했을 때
필터 적용 후 부제 충돌은 87개 중 12개(15%)였다. 그 근방이 나와야 정상이다.

### 작업 2 — LLM 판정 실행

작업 1로 후보가 정상 규모가 된 뒤에 한다.

- Bedrock **Haiku 4.5** (`us.anthropic.claude-haiku-4-5-20251001-v1:0`)를 쓴다.
  단순 가부 분류를 대량 반복하는 작업이라 Sonnet은 과하다.
- `term_judge.judge_candidates(candidates, llm)`가 이미 있다.
  `build_assets.build_assets(..., llm=...)`에 넘기면 된다.
- 현재 `glossary.json`은 `llm_judged: false`로 정직하게 표시돼 있다.
  판정을 돌렸으면 이 플래그가 `true`가 되어야 한다.
- **판정 결과를 지어내지 말 것.** LLM을 못 돌렸으면 `false`로 남긴다.

### 작업 3 — 룰 용어 수동 정답셋

번역가 1~2시간. 룰 텍스트 핵심 용어 30~50개를 EN→KO로 라벨링한다.
(`install`→`설치`, `trash`→`폐기`, `rez`→`전개` 같은 것들)

### 작업 4 — phase2a 실행

Seed는 이미 준비돼 있다(§6). 작업 1~2가 끝나야 의미가 있다.

## 5. 함정 모음 (직접 밟은 것들)

- **`Trace[N]` ↔ `<trace>추적 N</trace>`** — 기호 보존율을 리터럴 멀티셋 비교로
  구현하면 공식 번역의 13.7%가 불합격한다. upstream 커밋
  `use <trace> in Asian languages`(2016-06-01)가 이게 의도된 규약임을 확인해 준다.
  정규화 대응표로 구현하고 `<strong>/<em>`은 게이트에서 제외한다.
- **미번역 레코드 필터링을 통계보다 먼저** — 안 하면 부제 용어의 54%가 용어집에서
  사라진다(필터 후 15%). 미번역 표현이 세 가지다: 키 없음 / 값이 `null` / 한글 없음.
- **`netrunner-cards-json-old`는 초벌본이 아니다** — 현재 `ko`와 98.9~100% 동일한
  최종본 스냅샷이고, `translations/kr` 커밋 9건 중 8건이 upstream 포맷팅이다.
  편집거리 기준선으로 쓸 수 없다.
- **0.359는 폐기된 수치다** — legacy `pack/` 레이아웃의 다른 카드 집합에서 나왔다.
  v2 split의 실제 천장은 0.385이고 어떤 검색기로도 0.359는 재현되지 않는다.
- **정답셋이 닿는 범위** — 88항목은 부제만 채점한다. 35개는 룰 텍스트에 아예
  등장하지 않는다. 룰 용어 추출 전체를 채점하지 않는다.
- **`.env`가 상위 경로에 있다** — 이 repo는 public이다. `.gitignore`에 넣어뒀지만
  새 파일을 추가할 때 주의한다.

## 6. 준비된 Seed

`phase1`만 실행됐다. 나머지는 스크래치패드에 만들어 뒀으나
**세션 종료 시 사라지므로 새 세션에서 다시 생성해야 한다.** 분할 기준은 아래와 같다.

| Seed | 범위 | AC | 원본 seed.yaml의 0-index |
|---|---|---|---|
| phase1 (실행됨) | 자산 구축 + TM 베이스라인 | 8 | 4,5,6,7,8,9,24 + 종료 게이트 |
| phase2a | 번역 큐 · 가드레일 · MCP 4도구 · HITL | 8 | 15,16,17,18,19,20,23,25 |
| phase2b | 하드 게이트 3개 채점 · 관찰 지표 | 5 | 0,1,2,3,26 |
| phase3 | Issue 동기화 · Pages 검수 뷰 · 패턴 매핑 | 7 | 10,11,12,13,14,22,27 |

AC#22(0-index 21, "구현 순서를 고정한다")는 페이즈 분할 자체가 그 구현이므로
수용 기준으로 중복 채점하지 않고 각 페이즈에 **제약으로** 넣었다. 버린 것이 아니다.

## 7. 아직 안 정한 것

1. 2021–2022 번역분 142장의 코퍼스 편입 여부 (`keywords` 필드가 없어 다른
   파이프라인으로 작업된 것으로 보인다. 표본 검수 후 결정)
2. 편집거리 감소폭의 납품 이후 추적 방법·주기
3. 가드레일 통과율을 하드 게이트로 승격할지 관찰 지표로 둘지
4. `ooo/orch_a6bc6099bdae`를 `main`에 병합할 시점
