# SERVICE · Netrunner 번역 보조 에이전트

EN→KO 카드게임 공식 번역가 팀을 위한, 암묵지를 검증 가능한 자산으로 바꾸는 파이프라인.

- 근거 문서: [PRD](.ouroboros/pm.md) · [코퍼스 실측](.ouroboros/corpus-findings.md)
- Ouroboros Seed: `seed_6a014a63f909` (수용 기준 20개)

---

## 1. 사용자·문제·가치

**사용자** — 같은 카드게임 IP(Android: Netrunner)를 번역하는 EN→KO 공식 번역가 팀.
본인 + 동료 번역가들. 규모는 수 명.

**문제 (한 문장)** — 지켜야 할 용어·문형이 문서로 존재하지 않고 과거 공식 번역물에만
암묵적으로 박혀 있어, 번역 일관성이 개인의 기억력에 좌우된다.

**현재 해결법** — 기억 + repo grep + 사람 간 구두 합의. 신규 합류자는 기준을 물려받을
문서가 없어 기존 카드를 직접 읽어 감을 잡아야 한다.

**이걸로 뭐가 좋아지는가** — 암묵지가 `glossary.json` / `patterns.json` / TM 인덱스라는
검증 가능한 자산으로 고정되고, 위반이 사람 눈이 아니라 규칙으로 검출된다.
번역가는 "일관성을 지키는" 일에서 "일관성 위반을 승인·반려하는" 일로 옮겨간다.

## 2. 서비스 확장 관점

**사내(팀) 기여** — 용어 일관성이 개인 기량에서 공유 자산으로 이동한다. 신규 합류자
온보딩 비용이 "기존 카드 통독"에서 "용어집 열람"으로 줄어든다.

**규모** — 번역가 N명 × 신규 카드 세트 릴리스 주기. 현재 EN 2,460장 중 KO 번역은
1,257장(51%)이고 2017년 이후 번역이 중단된 상태라, 미번역 1,203장이 곧바로 처리 대상이다.

**대체제와의 차별점** — 범용 번역기·CAT 툴은 **용어집을 입력으로 받는다**. 이 서비스는
**용어집을 만들어 낸다**. 그것이 이 팀에 용어집이 없다는 문제의 정확한 해답이다.
다른 IP로 옮길 때는 코퍼스 repo만 갈아끼우면 나머지 파이프라인이 그대로 동작한다.

## 3. 사용 예상 도구·데이터

**도구 (MCP 서버로 노출)**

| 도구 | 입력 → 출력 |
|---|---|
| `lookup_term(en)` | EN 용어 → 공식 KO 역어 + 근거 카드 id 목록 |
| `search_tm(text)` | EN 텍스트 → 유사 과거 카드 EN/KO 쌍 (하이브리드 검색) |
| `lookup_pattern(text)` | 룰 텍스트 → 해당 문형 템플릿 |
| `check_translation(en, ko)` | EN/KO 쌍 → 용어·기호 위반 리포트 |

**데이터 소스 (전부 실제 데이터)**

- 원본: [Null-Signal-Games/netrunner-cards-json](https://github.com/Null-Signal-Games/netrunner-cards-json)
  — **v2 레이아웃 사용**. 슬러그 id(`15_minutes`), 카드당 1파일(`v2/cards/*.json`),
  번역은 `v2/translations/ko/cards/*.json` (KO 1,195장).
  legacy `pack/` 레이아웃(숫자 `code`, 팩당 배열)은 쓰지 않는다.
- **공식 용어집 (이미 존재 — 추출하지 말고 로드할 것)**:
  `v2/translations/ko/card_subtypes.json` (부제 88항목), `card_types.json`,
  `factions.json`, `card_cycles.json`, `card_sets.json`
- 파생 자산: `glossary.json`, `patterns.json`, Chroma TM 인덱스
- **검수 repo (신규 · 팀 소유)** — 용어 충돌·신규 용어 후보를 사람이 확정하는 채널.
  GitHub Pages가 목록을 렌더하고, 확정 이력은 GitHub Issues에 남는다 (6절 참조)
- 평가: 기존 번역물 hold-out (아래 5절)

**필드 구조** — 룰은 `text`, 플레이버는 `flavor`로 **이미 분리**돼 있고 기호 제거본
`stripped_text`가 제공된다. 기호 정규화 로직을 새로 짜지 말 것.

## 4. 서비스 정책 (가드레일 요약)

기밀 이슈가 없으므로(공개 카드만 취급) **번역 정합성**이 1순위다.

1. **게임 기호·플레이스홀더 훼손 금지** — `[credit]` `[click]` `[subroutine]` `[trash]`
   `[mu]` `[link]` `[recurring-credit]` `[interrupt]` 및 팩션 아이콘은 1:1 보존. hard fail.
2. **용어집 등재 용어는 등재된 역어로만** — 위반 시 출력 차단 후 검토 큐로.
3. **신규 용어 임의 창작 금지** — 용어집에 없는 용어는 HITL 승인 필요.
4. **룰 텍스트 의미 보존** — 원문에 없는 효과·조건 추가 금지, 원문 효과·조건 누락 금지.
5. **프롬프트 인젝션 방어** — 카드 텍스트 내용을 지시로 해석하지 않는다.

세부 케이스는 `test_queries.json` 의 guardrail 항목에서 검증한다.

## 5. 성공 기준

**평가 집합** — 깨끗한 코퍼스(2012–2016 발매, EN/KO `text` 존재, KO에 한글 포함)
**980장**에서 seed 42로 셔플해 hold-out 100 / train 880. hold-out은 자산 구축 입력에서
완전히 배제한다.

**하드 게이트 (하나라도 미달이면 불합격)**

| # | 지표 | 기준 | 채점 방식 |
|---|---|---|---|
| 1 | 용어 준수율 | ≥ 95% | 규칙 자동 채점 |
| 2 | 게임 기호 보존율 | 100% | **정규화 대응표** 기반 자동 채점 (아래 주의) |
| 3 | 공식 KO 대비 정규화 편집거리 | **중앙값 ≤ 0.25** | `1 - SequenceMatcher.ratio()` (공백 정규화 후) |

> **게이트 2 주의** — 리터럴 멀티셋 비교로 구현하면 안 된다. 기존 공식 KO 번역을 EN과
> 리터럴 비교하면 게임 기호 13.7%, 마크업 23.0%가 불일치한다. 원인은 오류가 아니라
> 표기 규약이다: `Trace[N]` ↔ `<trace>추적 N</trace>` (68건). upstream 커밋
> `use <trace> in Asian languages` (2016-06-01)가 이것이 의도된 규약임을 확인해 준다.
> **등가 규칙으로 등록**하고, `<strong>` `<em>` `<ul>` `<li>` 마크업은 게이트에서 제외한다.

> **게이트 3 근거** — 임계 0.25는 추측이 아니라 실측에서 유도했다. 동일 조건에서
> TM 최근접 이웃만 쓴 LLM 0회 베이스라인의 중앙값이 **0.359**, 무작위 카드 베이스라인이
> **0.791**이다. 0.359의 30% 개선 = 0.251. 즉 게이트 3은 "TM만으로 되는 수준을
> 30% 이겨라"는 뜻이다. 이 베이스라인은 1단계 자산만으로 재현 가능하므로 구현 첫날
> 회귀 감시선으로 쓴다.

**관찰 지표 (측정·보고하되 납품 거부 사유 아님)**

- 룰 텍스트 문형 일치 (LLM-Judge 3점 척도) ≥ 2.5/3
- 플레이버 자연스러움 (LLM-Judge 3점 척도) ≥ 2.0/3
- 용어 추출 알고리즘의 정밀도·재현율 — `card_subtypes.json` 88항목을 정답셋으로 직접 채점

**"쓸만하다" 판단 지표** — 게이트 3의 편집거리. 번역가가 초벌을 얼마나 고쳐야 하는지를
직접 측정하는 유일한 지표다.

## 6. 아키텍처

### 1단계 · 자산 구축 (오프라인 배치, 증분 재실행)

```
v2/cards + v2/translations/ko/cards
  → id로 EN/KO 페어링
  → ★ 미번역 레코드 필터링          ← 반드시 통계보다 먼저
       (1) 키 없음  (2) 키는 있고 값이 null  (3) 값에 한글 없음
  → 룰(text) / 플레이버(flavor) 분리
  → 공식 용어집 로드 (subtypes/types/factions/cycles/sets)  ← 추출 대상에서 제외
  → 통계 후보 생성 (LLM 0회)                                [패턴 1]
       EN n-gram × KO 어절 n-gram 공기빈도, Dice/PMI, 최소 등장 5회
  → LCEL + Pydantic 검증 (TermPair) — LLM은 가부 판정만
  → 용어 충돌 검출 → conflicts.json 으로 분리, 용어집에서 제외
  → 검수 repo에 Issue 동기화 (멱등)                         ← 3단계 확정 절차
  → 확정된 Issue 회수 → glossary.json 반영
  → glossary.json / patterns.json
  → Chroma TM 인덱싱 (BM25 + dense)                         [패턴 3]
```

> **미번역 필터링을 빼면 파이프라인이 망가진다.** 필터 없이 충돌 제외 규칙을 적용하면
> 부제 용어의 **54%**(47/87)가 용어집에서 사라진다. 필터 후에는 **15%**(12/80)로 떨어지고,
> 남은 12개도 대부분 `파수×59 vs 방벽×1` 같은 단일 오타다.
> 시간 가중(최신 우선)은 **최악**이다 — 최신일수록 미번역 영문이기 때문이다.

### 2단계 · 번역 검토 큐 (온라인 API)

```
새 카드 → 종류 라우팅(룰/플레이버)
  → TM 검색 + 용어 주입 → 초벌 생성                    [패턴 2, 4]
  → 규칙 검증 (4절 가드레일)                            [패턴 6]
  → 위반·신규용어·저신뢰 → interrupt()                  [패턴 7]
  → 번역가 수정·승인 → approved.jsonl + Store 누적      [패턴 10]
```

**HITL interrupt 트리거 4가지**: ① 신규 EN 용어 발견 ② 용어 충돌(1 EN → 2개 이상 KO)
③ 규칙 검증 실패 ④ TM 최고 유사도 임계 미만.

**RAG는 하이브리드 필수** — 카드 텍스트는 짧고 고유명사가 많아 BM25의 exact match
기여가 크다.

**산출물 정책** — 승인 결과는 원본 repo에 쓰지 않고 `approved.jsonl`로만 적재한다.
repo 반영·커밋은 사람이 따로 한다(공식 공개 저장소이지 팀 소유가 아니기 때문).
승인된 번역의 신규 용어는 `new_term_candidates.json`으로만 적재되고,
아래 3단계 확정 절차를 거쳐야 용어집에 등재된다.

### 3단계 · 용어 확정 절차 (GitHub Pages + Issues)

원본 repo와 무관한 **신규 팀 소유 repo**에서 돌아간다. 백엔드를 두지 않는다.

```
conflicts.json / new_term_candidates.json
  → 배치가 검수 repo에 Issue 동기화 (멱등)
       제목 키: [term-conflict] <EN>  /  [new-term] <EN>
       라벨:   term-conflict | new-term-candidate
       본문:   후보 역어별 체크박스 + 등장 횟수 + 근거 카드 id
  → GitHub Pages: 두 JSON을 fetch해 표로 렌더, 각 행에서 해당 Issue로 링크
  → 검수자: Issue에서 채택할 역어 체크박스 선택 후 close
  → 다음 배치: closed + 체크된 Issue를 읽어 glossary.json에 반영
```

**왜 Issues인가** — Pages는 정적이라 쓰기 경로가 없다. Issues를 쓰면 백엔드·인증 없이
확정 이력이 GitHub에 남고, 동료 번역가가 댓글로 이견을 남길 수 있다.
Pages는 "지금 확정 대기 중인 것이 무엇인가"를 한눈에 보여주는 읽기 뷰 역할만 한다.

**멱등성** — Issue 제목의 키(`[term-conflict] Sentry`)로 중복 생성을 막는다.
이미 열린 Issue가 있으면 본문만 갱신하고, closed Issue는 다시 열지 않는다.

**미확정 용어의 취급** — 확정 전까지 해당 용어는 용어집에 없으므로,
2단계에서 그 용어가 나오면 HITL 트리거 ①·②가 걸려 검토 큐로 간다. 즉 확정이 밀려도
파이프라인이 멈추지 않고, 잘못된 역어가 자동 전파되지도 않는다.

**API**

```
POST /assets/build          1단계 배치 실행 (증분)
POST /translate/batch       카드 묶음 초벌 생성
GET  /review/queue          검토 대기 목록
POST /review/{id}/resolve   승인 · 수정 후 승인 · 반려
GET  /glossary/search       용어 조회
```

### 재사용할 기존 코드

| 용도 | 파일 |
|---|---|
| 그래프 뼈대 | [day7_practice/final_scenario.py](../day7_practice/final_scenario.py) |
| 트레이스 콜백 (오프라인 동작) | [day7_practice/local_tracer.py](../day7_practice/local_tracer.py) |
| 평가 골격 | [day7_practice/llm_judge.py](../day7_practice/llm_judge.py) · [run_eval.py](../day7_practice/run_eval.py) |
| MCP 서버 골격 | [day7_practice/mcp_server.py](../day7_practice/mcp_server.py) |
| 가드레일 골격 | [day7_practice/guards_input.py](../day7_practice/guards_input.py) |
| 로깅 미들웨어 | [day5_practice/](../day5_practice/) — `usage_metadata`는 `hasattr` 체크 후 접근 |

## 7. 적용 패턴 매핑

`day8/tech_stack.md` 대응. 필수 4개(1·3·11·12) 전부 포함, 총 11개 적용.

| # | 패턴 | 적용 | 위치 |
|---|---|---|---|
| 1 | LCEL chain (Pydantic) | ✅ 필수 | `TermPair` 가부 판정, 모든 구조화 출력 |
| 2 | ReAct (도구 자율 선택) | ✅ | 초벌 생성 시 TM·용어 조회 자율 결합 |
| 3 | RAG 하이브리드 | ✅ 필수 | Chroma BM25 + dense TM 검색 |
| 4 | 도구 다중 | ✅ | `lookup_term` + `search_tm` + `check_translation` 결합 |
| 5 | MCP 서버 연동 | ✅ | 4개 도구를 팀에 노출 |
| 6 | 가드레일 | ✅ | 4절 5개 규칙, 인젝션 방어 포함 |
| 7 | HITL | ✅ | `interrupt()` 4가지 트리거 |
| 8 | 미들웨어 | ✅ | 재시도·로깅 |
| 9 | Multi-Agent Supervisor | ❌ **미채택** | 아래 사유 |
| 10 | Plan-Execute | ❌ **미채택** | 아래 사유 |
| 10 | 장기 메모리 (Store) | ✅ | 승인 이력 누적 |
| 11 | Observability·Trace | ✅ 필수 | `local_tracer.py` |
| 12 | 평가 | ✅ 필수 | hold-out 100 + LLM-Judge |

**미채택 사유**

- **9 Supervisor** — 룰/플레이버 분기는 **라우팅**이지 에이전트 분할이 아니다. 두 경로가
  서로 다른 도구나 권한을 쓰지 않으므로 서브 에이전트로 나누면 비용만 늘고 트레이스가
  복잡해진다. `day8/memo.md`의 "단독 체인이나 단독 Agent가 나은 경우도 분명히 있다"와 일치.
- **10 Plan-Execute** — 카드 1장 처리 경로가 고정 파이프라인이라 분해할 계획이 없다.
  단계 수가 컴파일 타임에 결정되므로 동적 계획 수립의 이득이 0이다.
  (같은 행의 **장기 메모리는 채택**한다 — 승인 이력 누적에 쓴다.)

## 8. 범위와 컷라인

**2일 안에 (순서 고정)**

1. 1단계 자산 구축 전체 — 필터링 → 통계 후보 → LLM 판정 → glossary/patterns → TM 인덱싱
2. TM-only 베이스라인 재현 (중앙값 0.359 확인) — 회귀 감시선 확보
3. 2단계 최소 경로 — 라우팅 → 초벌 생성 → 규칙 검증 → interrupt → approved.jsonl
4. 평가 — hold-out 100장, 하드 게이트 3개 + 관찰 지표
5. Docker · API
6. 3단계 확정 절차 — Issue 동기화 → GitHub Pages 검수 뷰

> 1단계가 먼저 끝나야 자산이 확정되고 2단계·평가가 성립한다. 순서를 바꾸지 말 것.

> **컷라인** — 6번은 2일 예산에서 가장 먼저 잘릴 후보다. 잘릴 때는 통째로 버리지 말고
> **Issue 동기화만 남기고 Pages 뷰를 버린다.** Issue만 있어도 확정 기능은 완결되고,
> Pages는 "확정 대기 목록을 한눈에 보는" 편의 계층이기 때문이다.
> 반대로 Pages만 만들고 Issue 쓰기를 빼면 확정 결과가 어디에도 남지 않아 기능이 성립하지 않는다.

**이후로 미룸** — 리랭킹, 형태소 분석기 도입, 수정 피드백의 용어집 자동 승격,
용어 개정 이력 추적, 룰북·UI 텍스트 지원, 승인 결과의 repo 자동 반영·자동 커밋,
승인 시 신규 용어 자동 등재.

## 9. 코퍼스 실측 결과와 남은 미확인 항목

착수 전 점검 항목 4건은 모두 실측으로 해소됐다. 전문은
[corpus-findings.md](.ouroboros/corpus-findings.md).

| 항목 | 결과 |
|---|---|
| repo 스키마 | v2는 슬러그 `id` + 카드당 1파일. 룰(`text`)·플레이버(`flavor`) **이미 분리**, `stripped_text` 제공 |
| 카드 장수 | EN 2,460 / KO 1,257(51%). **쓸 수 있는 건 2012–2016의 980장** |
| 기호 표기법 | `[credit]`1283 `[subroutine]`734 `[click]`633 `[trash]`166 `[mu]`85 등. `Trace[N]`→`<trace>추적 N</trace>` 규약 존재 |
| 용어 개정 이력 | **거의 없음.** 충돌 54%는 미번역 잔재이고, 필터 후 15%로 감소. 카드 제목은 1,255개 중 충돌 0 |

**추가로 밝혀진 것**

- **공식 용어집이 이미 부분적으로 존재한다** (`card_subtypes.json` 88항목 등).
  PRD의 "용어집 없음" 전제는 절반만 맞다 — 부제·타입·팩션은 로드하면 되고,
  추출이 필요한 건 룰 텍스트 내 용어(install/trash/rez 등)뿐이다.
  덤으로 이 88항목이 추출 알고리즘의 **직접 채점 정답셋**이 된다.
- **"무보조 사람 초벌" 기준선은 존재하지 않는다.**
  `castigar/netrunner-cards-json-old`의 KR 956장은 전부 현재 `ko`의 부분집합이고
  내용이 98.9–100% 동일한 최종본 스냅샷이다(상이 8/728건은 오타·띄어쓰기 교정 수준).
  `translations/kr` 커밋 9건 중 8건이 upstream의 2016년 일괄 포맷팅이고 사용자 커밋은
  1건뿐이라, 초벌→최종 진행 이력이 없다. → 게이트 3 기준선을 **공식 최종 번역 대비**로 교체했다.

**남은 미확인 항목**

1. 2021–2022 번역분(142장)의 품질 — `keywords` 필드가 없어 다른 파이프라인으로 작업된
   것으로 보인다. 깨끗한 코퍼스에 편입할지는 표본 검수 후 결정한다.
2. 편집거리 감소폭을 납품 이후 지속 추적할 방법과 주기.
3. 가드레일 5개 항목의 케이스 통과율을 하드 게이트로 승격할지 관찰 지표로 둘지.
4. 검수 repo의 이름·가시성(public/private)과, Issue 생성에 쓸 토큰의 보관 위치.

> **해소됨** — "용어 확정 절차의 구체적 형식"은 6절 3단계로 확정됐다.
> 신규 팀 소유 repo에서 GitHub Pages(읽기 뷰) + GitHub Issues(확정 이력)로 처리한다.
