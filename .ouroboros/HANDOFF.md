# 인계 문서 — Netrunner 번역 보조 에이전트

**작성 2026-09-16 · 갱신 2026-09-17.** 새 세션이 이 문서만 읽고 이어받을 수 있도록 쓴다.
사양의 단일 진실 원천은 여전히 [`SERVICE.md`](../SERVICE.md)이고, 이 문서는
**지금 어디까지 됐고 다음에 뭘 해야 하는지**만 다룬다.

---

## 0. 30초 요약

- 기획은 끝났다. `SERVICE.md` 9절 완성, Seed 28 AC 확정.
- **1단계(자산 구축)는 끝났다.** 추출 경로 분리와 LLM 판정까지 완료됐고, 산출물 3종이 실재한다.
- 코드는 `netrunner-glossary` repo의 `ooo/orch_a6bc6099bdae` 브랜치, HEAD `143f1cd`.
  **origin에 푸시돼 있다.** 테스트 211개 전부 통과(2026-09-17 재확인).
- 다음 할 일은 §4 **작업 1(phase2a 실행)**. 작업 2(룰 용어 수동 정답셋)는 번역가 작업이라 병렬로 간다.
- **인터뷰·기획을 다시 하지 말 것.** 사양 결정은 §3에 전부 확정돼 있다.

## 1. 좌표

| 항목 | 값 |
|---|---|
| 코드 repo | `castigar/netrunner-glossary` (public) |
| 작업 브랜치 | `ooo/orch_a6bc6099bdae` ← **여기에 구현이 있다.** HEAD `143f1cd`, origin과 동기 |
| `main` | 기획 문서만 (`SERVICE.md`, `.ouroboros/`) |
| 로컬 워크트리 | `C:\Users\SDS\.ouroboros\worktrees\mini_pjt\orch_a6bc6099bdae` |
| 로컬 main | `C:\Users\SDS\Desktop\sds-ax-practice\mini_pjt` |
| 코퍼스 | `C:\Users\SDS\Desktop\netrunner-corpus\netrunner-cards-json` (`CORPUS_ROOT`) |
| 사용자 이력 repo | `...\netrunner-corpus\netrunner-cards-json-old` — **학습·평가에 쓰지 말 것** (§5 참조) |
| **파이썬 환경** | `C:\Users\SDS\Desktop\sds-ax-practice\.venv\Scripts\python.exe` — **시스템 기본 python 3.14에는 pytest가 없다** |
| Bedrock 자격증명 | `sds-ax-practice/.env` (AWS 3종). 가용 모델은 `availableModelsOnBedrock.md` |
| Seed | `seed.yaml`(28 AC) · `seed-phase1.yaml`(8 AC, 실행됨) · `seed-phase2a.yaml`(8 AC, 대기) |

실행 시 반드시:

```bash
export CORPUS_ROOT="C:/Users/SDS/Desktop/netrunner-corpus/netrunner-cards-json"
cd "C:/Users/SDS/.ouroboros/worktrees/mini_pjt/orch_a6bc6099bdae"
"C:/Users/SDS/Desktop/sds-ax-practice/.venv/Scripts/python.exe" -m pytest -q
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

**교훈 — 다음 페이즈 실행 시 반드시 지킬 것:**

- AC를 **10개 이하**로 유지한다. 28개는 플래너가 못 넘는다.
- `semantic_ac_key`는 `ac_` + 소문자 hex 16자리. 슬러그를 쓰면 검증에서 죽는다.
- 대상 repo에 커밋이 최소 1개 있어야 한다.
- `generate_seed`의 `session_context` 경로는 **파일을 쓰지 않는다.** 응답 YAML을
  직접 디스크에 저장해야 한다.

### phase1 검수·보강 — 수작업 커밋 7개

4회차 실행은 8/8 통과로 보고했지만 **실질은 통과가 아니었다.** 이후 손으로 고친 것이
아래 7개이고, 자동 실행분보다 이쪽이 실제 산출의 대부분이다.

| 커밋 | 내용 |
|---|---|
| `1ed1204` | TM 검색기 교체(bag-of-words → BM25+MiniLM+char n-gram, 0.4375→**0.385**), 산출물 생성기 신설, 완화된 게이트를 `0.385 ± 0.025`로 복구 |
| `6d01fcf` | **추출 경로 분리**(결정 ①②). 부제=`keywords` / 룰=`text`. 후보 76,309→4,914, 부제 정밀도 **1.000** 재현율 **0.909** |
| `ae9bacb` | 판정 배치를 청크(250)·체크포인트(`judgments.jsonl`)·진행 보고로 전환. 실패분은 `rejected`가 아니라 `dropped`로 분리 |
| `00e73b9` | 룰 경로 상한을 Dice 전역 순위 → **2단계**(EN 빈도 상위 1,500 → EN별 Dice 상위 k)로 교체. 전역 Dice는 `trash`를 55,471위로 떨어뜨려 핵심 용어를 전부 잘라냈다 |
| `7026260` | **LLM 판정 실행**(Haiku 4.5, 4,355건, 86분). `llm_judged=true` |
| `9cfe206` | KO **형태소 정규화**(kiwipiepy) + 프롬프트 지문(SHA-256 16자). 재판정 4,396건으로 자산 갱신, `install` 누락 해소 |
| `143f1cd` | phase2a Seed의 `context_references`를 `ContextReference` 객체로 교정 |

### 현재 실측치 (2026-09-17 직접 검증)

| 항목 | 값 |
|---|---|
| 테스트 | **211개 전부 통과** (54초) |
| 산출물 | `assets/glossary.json` · `patterns.json`(51) · `conflicts.json`(25) — 모두 커밋됨 |
| `judgments.jsonl` | 4,396행 (체크포인트, 미추적) |
| `llm_judged` | **true** — submitted 4,396 / accepted 158 / rejected 4,238 / **dropped 0** |
| 용어집 | 공식 154 + 부제 68 + 룰 123 |
| 충돌 | 25건 (룰 13 + 부제 12) — `source` 필드로 구분된다 |
| 부제 경로 채점 | 정밀도 1.000 / 재현율 0.909 (tp 80, fn 8, gold 88) |

## 3. 확정된 사양 결정 (재논의 불필요)

2026-09-16에 사용자가 확정했다.

| # | 결정 |
|---|---|
| ① | **추출 경로를 둘로 분리.** 부제는 `keywords` 필드에서, 룰 용어는 `text` 필드에서 추출. 정답셋 88항목은 `keywords` 경로에만 적용. 룰 경로는 **수동 라벨링 정답셋 30~50개**를 따로 만들어 채점하고, 하드 게이트 1이 간접 검증을 겸한다 |
| ② | 부제 경로는 필드 단위라 후보가 수백 규모로 떨어진다. 룰 경로는 **Dice 상위 N개 상한** — 단, 전역 Dice 순위는 목적에 맞지 않아 2단계 상한으로 구현했다(`00e73b9`) |
| ③ | 하드 게이트 3 임계 **≤ 0.27** (실측 기준선 0.385 × 0.7) |

## 4. 다음에 할 일 (순서대로)

> 이전 문서의 작업 1(추출 경로 분리)과 작업 2(LLM 판정)는 **완료됐다.** §2의 커밋표를 보라.

### 작업 1 — phase2a 실행 ★ 최우선

2단계 온라인 경로(번역 큐 · 가드레일 · MCP 4도구 · HITL) 8 AC.
Seed는 `seed-phase2a.yaml`에 있고 브랜치에 커밋돼 있다. 선행조건(자산 구축)은 충족됐다.

**주의 — Seed 사본이 둘인데 내용이 다르다.** 정본은 **브랜치 커밋본**
(`143f1cd`에서 `context_references`를 `ContextReference` 객체로 교정한 판)이다.
로컬 main 디렉터리(`mini_pjt/.ouroboros/seed-phase2a.yaml`)에 같은 이름의 **미추적 구버전**이
남아 있는데, `context_references`가 문자열 리스트이고 `HEAD 7026260`을 박아둔 낡은 판이다.
**실행에 쓰지 말 것.** 워크트리 경로로 지정해서 돌린다.

### 작업 2 — 룰 용어 수동 정답셋 (번역가, 병렬 가능)

번역가 1~2시간. 룰 텍스트 핵심 용어 30~50개를 EN→KO로 라벨링한다.
결정 ①에 따라 룰 경로 채점의 유일한 근거다. 지금 룰 용어 123개는 **채점된 적이 없다.**
(`install`→`설치`, `trash`→`폐기`, `rez`→`레즈` 같은 것들 — `전개`가 아니다, §5 참조)

**포맷은 준비돼 있다** (2026-09-17, 로컬 main의 `mini_pjt/`):

| 파일 | 용도 |
|---|---|
| `data/README-rule-gold.md` | 번역가용 작성 안내 — 이것부터 읽는다 |
| `data/rule_terms_gold.json` | 채울 파일. `en` / `ko`(복수 허용) / `source` / `note` |
| `data/rule_terms_freq_aid.tsv` | EN 빈도 보조표 500행. **KO도 시스템 출력도 없다** |
| `tools/make_freq_aid.py` | 보조표 생성기 (재현용) |

설계의 핵심은 **1단계 무보조 라벨링 30개 → 2단계 빈도표 보조 보충**이고,
`source` 필드로 둘을 구분한다. 시스템이 뽑은 목록을 보고 정답셋을 만들면
재현율이 구조적으로 1.0이 나와 아무것도 측정하지 못하기 때문이다.

**phase2b에서 할 일:** 기존 `evaluate_extraction()`은 **EN만 비교하고 KO는 보지
않는다.** 수동 라벨링의 KO를 실제로 쓰려면 채점기를 확장해야 한다. 또한
닿지 않는 항목(4단어 이상 / DF<5 / DF≤6 / `R&D`처럼 토크나이저가 쪼개는 표기)을
"못 맞힌 것"과 분리해 보고해야 한다.

### 작업 3 — phase2b / phase3

§6 분할대로. 작업 1이 끝난 뒤.

## 5. 함정 모음 (직접 밟은 것들)

- **`Trace[N]` ↔ `<trace>추적 N</trace>`** — 기호 보존율을 리터럴 멀티셋 비교로
  구현하면 공식 번역의 13.7%가 불합격한다. upstream 커밋
  `use <trace> in Asian languages`(2016-06-01)가 이게 의도된 규약임을 확인해 준다.
  정규화 대응표로 구현하고 `<strong>/<em>`은 게이트에서 제외한다.
- **미번역 레코드 필터링을 통계보다 먼저** — 안 하면 부제 용어의 54%가 용어집에서
  사라진다(필터 후 15%). 미번역 표현이 세 가지다: 키 없음 / 값이 `null` / 한글 없음.
- **Dice는 용어다움이 아니라 배타성을 잰다** — 2·공기빈도/(EN빈도+KO빈도)이므로 널리 쓰이는
  핵심 용어일수록 점수가 낮다. 전역 Dice 상위 5,000으로 자르면 `trash`(55,471위),
  `trace`(19,305위), `install`(17,436위)이 전부 잘린다. EN 용어를 빈도로 먼저 고르고
  **그 안에서** Dice를 쓸 것.
- **형태소 분석기 없이 한국어 용어를 뽑지 말 것** — 어절을 공백으로만 자르면 설치할/설치된/
  설치한다가 전부 다른 용어가 되고, 판정자가 활용형을 근거로 거부해 `install`이 용어집에서
  누락된다. kiwipiepy 도입 시 함정 셋(게임 기호 파괴 / 분리 분석 오태깅 / 경동사 태깅
  불일치)은 `9cfe206` 커밋 메시지에 실측과 함께 적혀 있다.
- **few-shot 예시를 코퍼스로 검증할 것** — 판정 프롬프트가 `trash`→`파기`를 정답 예시로
  쓰고 있었는데 코퍼스 실측은 폐기 232건 / 파기 **0건**이었다. 존재하지 않는 역어를
  가르치고 있었다. (`rez`→`레즈`는 132건으로 확인. 구 인계 문서의 `전개`는 틀렸다.)
- **장시간 LLM 배치는 체크포인트 없이 돌리지 말 것** — 첫 판정 실행이 35분 돌고 산출물 0을
  남겼다. 청크마다 디스크에 append하고, 실패분은 `rejected`가 아니라 `dropped`로 따로 센다.
  모델이 내리지 않은 판정을 거부로 기록하는 것이 이 단계 최악의 실패다.
- **`netrunner-cards-json-old`는 초벌본이 아니다** — 현재 `ko`와 98.9~100% 동일한
  최종본 스냅샷이고, `translations/kr` 커밋 9건 중 8건이 upstream 포맷팅이다.
  편집거리 기준선으로 쓸 수 없다.
- **0.359는 폐기된 수치다** — legacy `pack/` 레이아웃의 다른 카드 집합에서 나왔다.
  v2 split의 실제 천장은 0.385이고 어떤 검색기로도 0.359는 재현되지 않는다.
- **정답셋이 닿는 범위** — 88항목은 부제만 채점한다. 룰 용어 추출은 채점하지 않는다(작업 2).
- **`.env`가 상위 경로에 있다** — 이 repo는 public이다. `.gitignore`에 넣어뒀지만
  새 파일을 추가할 때 주의한다.
- **pytest는 워크트리 기본 python으로 안 돌아간다** — §1의 `.venv`를 쓸 것.

## 6. 준비된 Seed

`phase1` 실행 완료. `phase2a`는 작성돼 브랜치에 커밋돼 있다.
`phase2b`·`phase3`는 **아직 만들지 않았다.** 분할 기준은 아래와 같다.

| Seed | 범위 | AC | 원본 seed.yaml의 0-index | 상태 |
|---|---|---|---|---|
| phase1 | 자산 구축 + TM 베이스라인 | 8 | 4,5,6,7,8,9,24 + 종료 게이트 | 실행 완료 |
| phase2a | 번역 큐 · 가드레일 · MCP 4도구 · HITL | 8 | 15,16,17,18,19,20,23,25 | 파일 존재, 실행 대기 |
| phase2b | 하드 게이트 3개 채점 · 관찰 지표 | 5 | 0,1,2,3,26 | 미작성 |
| phase3 | Issue 동기화 · Pages 검수 뷰 · 패턴 매핑 | 7 | 10,11,12,13,14,22,27 | 미작성 |

AC#22(0-index 21, "구현 순서를 고정한다")는 페이즈 분할 자체가 그 구현이므로
수용 기준으로 중복 채점하지 않고 각 페이즈에 **제약으로** 넣었다. 버린 것이 아니다.

## 7. 아직 안 정한 것

1. 2021–2022 번역분 142장의 코퍼스 편입 여부 (`keywords` 필드가 없어 다른
   파이프라인으로 작업된 것으로 보인다. 표본 검수 후 결정)
2. 편집거리 감소폭의 납품 이후 추적 방법·주기
3. 가드레일 통과율을 하드 게이트로 승격할지 관찰 지표로 둘지
4. `ooo/orch_a6bc6099bdae`를 `main`에 병합할 시점

### 알려진 사소한 결함 (미수정)

- `glossary.json`의 `counts.official`이 **160**인데 실제 `official` 항목은 **154**개다.
  `OfficialGlossary.__len__`이 카테고리별 합을 세는 반면 `all_terms()`는 평탄화하면서
  중복 id 6개를 합치기 때문이다(`src/load_official_glossary.py:46-54`).
  용어집 내용에는 영향이 없고 보고 숫자만 어긋난다.
