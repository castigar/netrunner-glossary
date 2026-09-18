# 인계 문서 — Netrunner 번역 보조 에이전트

**갱신 2026-09-18.** 새 세션이 이 문서만 읽고 이어받을 수 있도록 쓴다.
사양의 단일 진실 원천은 [`SERVICE.md`](../SERVICE.md)이고, 이 문서는 **지금 어디까지 됐고 다음에 뭘 해야 하는지**만 다룬다. 게이트 정의·임계·평가집합 조건은 여기서 다시 쓰지 않는다 — SERVICE.md §5를 본다.

---

## 0. 30초 요약

- 기획 완료. 1·2단계 구현 완료. 파이프라인이 **처음으로 실제 모델로 전량 실행됐다.**
- 최신 코드는 `ooo/ralph-3436c74545374d8296a877f5d4589e` @ **`f160d4a`**(`d100840` → `12c355a` → `e6ac521` → `f160d4a`). 마지막 실측 테스트 수는 `12c355a`에서 **952개 통과**이고 `d100840`에서는 939개였다. `f160d4a`는 문서 커밋이라 952에서 바뀌지 않았을 것으로 보이나 **재실측하지 않았다.**
- ⚠️ **검증 워크트리 `~/.ouroboros/worktrees/verify-gen5`가 그 브랜치를 점유하고 있다**(detached 아님 — `git worktree list`가 `f160d4a [ooo/ralph-3436c74…]`로 표시). Ralph는 브랜치가 다른 워크트리에 잡혀 있으면 시작하지 못한다(§5 체크리스트). 착수 전 그 워크트리에서 `git checkout --detach` + `git worktree prune`을 해야 한다.
- 브랜치는 origin과 동기화돼 있다(2026-09-18 푸시). **미푸시 커밋은 없다.**
- **이번 세션의 발견: 공식 KO 정답 번역이 하드 게이트 1·2를 통과하지 못한다**(천장 74.69% / 95.00%). 도달 불가능한 기준을 향해 7세대를 태우고 있었다. 이 발견을 반영해 **SERVICE.md §5를 개정했다**(`24a2ba4`).
- 남은 일은 그 개정을 **코드에 반영**하는 것이다. 게이트를 통과시키는 작업이 아니라 게이트가 무엇을 재는지 고치는 작업이다. **계측기와 평가 데이터는 사람이 고치고 제품 구현만 Seed에 맡긴다** — 이유는 §4 머리의 경계 표.
- **ralph 내부 score를 품질 신호로 쓰지 않는다** — §5 첫 항목. 이걸 모르면 또 한 세대를 버린다.

## 1. 좌표

| 항목 | 값 |
|---|---|
| 코드 repo | `castigar/netrunner-glossary` (public — `.env`·토큰을 절대 커밋하지 않는다) |
| 로컬 main | `C:\Users\SDS\Desktop\sds-ax-practice\mini_pjt` |
| 검증 워크트리 | `C:\Users\SDS\.ouroboros\worktrees\verify-gen5` — **`ooo/ralph-3436c74…` 브랜치를 점유 중** @ `f160d4a` (detached 아님. Ralph 착수 전 해제 필요) |
| 코퍼스 | `C:\Users\SDS\Desktop\netrunner-corpus\netrunner-cards-json` (`CORPUS_ROOT`) |
| 파이썬 | `C:\Users\SDS\Desktop\sds-ax-practice\.venv\Scripts\python.exe` — 시스템 python 3.14에는 pytest가 없다 |
| Bedrock 자격증명 | `sds-ax-practice/.env`. 가용 모델 목록은 `availableModelsOnBedrock.md` |
| ouroboros CLI | `C:\Users\SDS\AppData\Local\uv\cache\archive-v0\qVBiREO0OQDRTyJo\Scripts\ouroboros.exe` (PATH에 없음) |
| ouroboros 설정 | `~/.ouroboros/config.yaml` — `auto_evaluate`·`auto_evolve`가 **켜져 있다**(§5 참조) |

테스트 실행:

```bash
export CORPUS_ROOT="C:/Users/SDS/Desktop/netrunner-corpus/netrunner-cards-json"
cd "C:/Users/SDS/.ouroboros/worktrees/verify-gen5"
"C:/Users/SDS/Desktop/sds-ax-practice/.venv/Scripts/python.exe" -m pytest -q
```

실제 모델 실행(비용 발생):

```bash
set -a && . "C:/Users/SDS/Desktop/sds-ax-practice/.env" && set +a
export PYTHONPATH=src
python src/run_pipeline.py --cards 10 --output out.jsonl \
  --llm-model "global.anthropic.claude-haiku-4-5-20251001-v1:0"
```

`--llm-model`은 필수다. 스텁은 `--allow-stub`으로만 들어가고 둘을 함께 주면 거부된다.

### 브랜치

| 브랜치 | HEAD | origin | 내용 |
|---|---|---|---|
| **`ooo/ralph-3436c74…`** | **`f160d4a`** | ○ | **최신. 여기서 작업한다.** Gen 5 산출 10커밋(`d100840`) + 사람의 계측기 수정 3커밋(`12c355a`·`e6ac521`·`f160d4a`). `12c355a`에서 테스트 952 |
| `main` | — | ○ | 기획 문서·Seed·골든 셋·이 문서 |
| `ooo/ralph-c7ac3366…` | `31d3995` | ○ | Gen 4 결과. Gen 5의 기반 |
| `ooo/ralph-6ec830bd…` | `1230fad` | ○ | Gen 3 결과 |
| `ooo/orch_a6bc6099bdae` | `143f1cd` | ○ | phase2a Seed 교정본 보관 |
| `feat/rule-gold-eval` · `ooo/orch_3b1f1bf54116` · `ooo/orch_a3466c696fe8` · `ooo/orch_a474c765ee60` · `ooo/orch_fa2a47fe46cf` · `ooo/ralph-83c344…` | — | ✕ | 통합 완료, 역할 끝남. 정리 대상 |

## 2. 지금 산출물은 어떤 상태인가

### 실측 판정 세 가지

ralph 내부 score는 판정에 쓰지 않는다. 합격 판정은 항상 이 셋으로 한다.

| # | 판정 | 결과 |
|---|---|---|
| 1 | `.venv`로 pytest 직접 실행 | **939 passed / 0 failed** (Gen 4는 878) |
| 2 | TM 에코율 — `draft_ko` vs 그 레코드 자신의 `tm_hits[0].ko_text`, 공백 정규화 후 | **0.0069** (n=145, 임계 `max(0.05, 실행 중 측정 우연일치율)`) — 통과 |
| 3 | 끝난 워크트리에 `ouroboros_evaluate` 별도 실행 | **REJECTED** — Stage 1 PASS, Stage 2 score 0.42 · `ac_compliance` NO |

판정 3의 거부 사유는 산출물 자체의 판정선("최종 판정: 불합격")이다. 증거 공백이 아니라 실제 게이트 미달을 지적한 것이고, 그래서 타당하다.

### 파이프라인이 실제로 하는 일

```
run_mode: real   primary_model_id: global.anthropic.claude-haiku-4-5-20251001-v1:0
models_used: 1종   mixed_run: false   model_failure_count: 0
model_success_counts: {haiku-4-5: 165}
tm_threshold_derivation: p20, provenance measured_in_run, 0.0323, ko_text 미사용
```

- **스텁 시대가 끝났다.** 진입구 셋을 전부 막았다 — `run_pipeline(llm_model)`에 기본값 없음, CLI는 `--allow-stub` 없이는 진입 불가, 둘 동시 지정 거부. 에코율이 100% → 0.69%로 떨어진 것이 그 증거다.
- **HITL 보류율 98~99% → 20.0%.** 결정 ④(차단형/기록형 분리)가 구현됐고 Seed가 예측한 21%와 맞았다. `new_term_guard`는 분류만 하고 차단 판정은 `hitl_interrupt`가 한다.
- 트리거 발화 120 / 자동 승인 100 / 자동 보류 20. 빈 예측 20건은 전부 `approval_incomplete`, 모델 호출 실패 0건, 정답 `ko_text` 대체 없음.

### 하드 게이트 실측

| 게이트 | 에이전트 100장 | 에이전트 납품 80장 | 공식 KO 천장 | 기준 |
|---|---|---|---|---|
| 1 용어 준수 | 63.05% | **81.67%** | **74.69%** | ≥ 95% |
| 2 기호 보존 | 82.00% | 91.25% | **95.00%** | 100% |
| 3 편집거리 중앙값 | 0.1771 | — | — | ≤ 0.2847 (통과) |

**이 표가 이 세션의 핵심이다.** 공식 KO 정답을 자기 게이트로 채점하면 둘 다 미달한다 — 사람이 만든 번역이 납품 거부된다. 그리고 납품분만 보면 **게이트 1에서 에이전트가 정답을 9.16pp 이긴다**(에이전트가 정답보다 위반이 많은 카드 0장, 반대 27장).

진단은 두 게이트가 서로 다르다. 상세와 개정 내용은 **SERVICE.md §5**에 있다.

- **게이트 1 = 채점 정의의 설계 결함.** 위반 161건 중 교착어 부적합이 65건(40.4%)이고, 육안 검토상 번역 오류로 분류할 항목이 사실상 없다. 관찰 지표로 강등했다.
- **게이트 2 = 채점기는 타당하고 평가집합이 결함.** 정답 실패 5건이 전부 EN/KO 판본 불일치 카드다. 반면 **에이전트의 과잉 기호 5건은 진짜 모델 결함이며 게이트가 유효하게 잡은 것**이다.

### 발견한 자산 결함

| 결함 | 규모 |
|---|---|
| 용어집 역어에 한글이 아예 없음 | **15** / 279 (`corp → 'Corporation'`, `ambush → 'Ambush'`, `nbn → 'NBN'` …) |
| 괄호가 리터럴로 박힌 역어 | 1 (`hq → '본부 (HQ)'` — 본문은 `본부(HQ)`, 차이는 공백 하나) |
| 사전형 용언 역어 | 12 (`break → '서브루틴 깨다'`, `shuffle → '섞다'`) |
| hold_out의 EN/KO 판본 불일치 | **10** / 100 (`breach` 5, `[interrupt]` EN만 4, `refill to` 1) |
| 테스트가 저장소 루트 `new_term_candidates.json`을 픽스처로 덮어씀 | 20건, `ko_rendering` 전부 빈 문자열 |

판본 불일치 실례 — KO가 틀린 게 아니라 **다른(이전) EN을 번역한 것**이다:

```
scrubber
  EN  2[recurring-credit] (When you install this card and before your turn begins,
      refill to 2 hosted credits.) You can spend hosted credits to pay trash costs.
  KO  2[recurring-credit] 카드를 폐기할 때만 이 크레딧을 사용한다.
```

### Gen 5 실행 자체

lineage `ralph-3436c74545374d8296a877f5d4589e`, generation 1. 10세대를 걸었으나 **1세대만 돌았다.**

```
20:19:33  AC6(index 5) attempt 2도 provider stall → recovery_exhausted
20:19:43  AC 8개 acceptance_finalized (7 succeeded / 1 blocked)
          "Partial failure: 0 failed, 1 blocked, 0 invalid"
20:20:36  lineage.generation.completed — 진화 Seed(79KB) 저장됨
20:24:05  mcp.job.failed — WinError 32, 워크트리 삭제 실패
```

**실패가 둘이고 서로 독립이다.** AC6은 정리 단계 전에 이미 막혔고, 워크트리 크래시가 앗아간 것은 세대 2~10이다. 산출물 유실은 없다 — 워크트리 오브젝트는 main repo의 `.git`에 있다. AC6은 `blocked`로 찍혔지만 **코드는 커밋됐다**(`91a0c58`, `d100840`).

## 3. 확정된 사양 결정 (재논의 불필요)

| # | 결정 |
|---|---|
| ① | **추출 경로를 둘로 분리.** 부제는 `keywords`, 룰 용어는 `text`. 정답셋 88항목은 부제 경로만 채점하고, 룰 경로는 수동 정답셋 32항목으로 채점 |
| ② | 룰 경로 후보 상한은 Dice 전역 순위가 아니라 **2단계** — EN 빈도 상위 N → EN별 Dice 상위 k |
| ③ | **측정에서 유도된 수치를 수용 기준에 상수로 박지 않는다.** 관계로 쓰고 값은 실행 시점에 측정한다 |
| ④ | **신규 용어는 차단형/기록형으로 나눈다.** 차단형만 interrupt를 발화시킨다. 차단형은 (a) 용어집 미등재 부제, (b) 룰 텍스트의 비문장초 대문자 미등재 토큰 둘뿐. 나머지는 탐지 시점에 적재하고 통과시킨다. 분류는 `new_term_guard`, 차단 판정은 `hitl_interrupt` — **구현 완료, 실측 보류율 20.0%** |
| ⑤ | **Bedrock 모델은 폴백한다.** 1순위 `global.anthropic.claude-haiku-4-5-20251001-v1:0`, 실패 시 `us.amazon.nova-pro-v1:0` → `us.amazon.nova-2-lite-v1:0` → `global.amazon.nova-2-lite-v1:0` → `us.amazon.nova-lite-v1:0`. **스텁으로는 절대 폴백하지 않는다** — 전부 실패하면 빈 예측을 남기고 `model_invocation_failure`로 보고한다. 목록 밖 모델 금지, 폴백 사실 은닉 금지 |
| ⑥ | **하드 게이트는 천장 검사를 선행한다.** 정답이 임계 미달이면 '불합격'이 아니라 '계측 무효'다. 게이트 1은 천장이 95%를 넘길 때까지 관찰 지표다. 상세는 SERVICE.md §5 |

`us.*`가 전부 금지인 것이 아니다 — 없는 것은 `us.anthropic.*`뿐이고 `us.amazon.*` 4개는 실재한다.

## 3.5 Seed v4 인터뷰에서 확정된 것 (2026-09-18, `interview_20260918_013149`)

**인터뷰는 2026-09-18에 종결됐다**(6라운드, ambiguity 0.08, MCP가 seed-ready 신호를 냈다).
다만 **수락 게이트가 통과하지 못해 Seed를 아직 발행하지 않았다** — 차단 사유는 §3.6.
확정된 것은 아래 ①~⑥이다(①②③은 라운드 1·2, ④⑤⑥은 라운드 4·5·6).

**① 판정 기준 = 매 세대 독립적인 천장 대비 판정.** 세대 간 추세선은 약속하지 않는다.
근거는 셋이다 — 추세선은 데이터 선택이 아니라 구현 주문이고(리포트 영속화·평가집합
지문·`card_id` 키 결과 보관·고정 비교 모집단이 전부 없다), 종료 기록 10세대 중 실모델
산출물은 **1개뿐**이며(스텁 2·산출물 없음 7), 2026-09-18 개정이 자 자체를 바꿔 저장된
과거 숫자는 같은 자로 잰 값이 아니다. 반면 천장 대비 판정은 추가 구현이 0이다.

**② hold_out = 현행 100장에서 EN 판본 불일치 10장을 뺀 90장.** 재분할하지 않는다.
`card_id` 조인이라 기존 실모델 실행을 **Bedrock 재호출 0회**로 재채점할 수 있고,
게이트2 천장이 1.0000이 되어 게이트2가 처음으로 유효한 하드 게이트가 된다.
재분할은 14장만 겹쳐 89장 유료 재실행이 필요한 데다, 신규 hold_out 약 86장이 과거
`train`에 있어 §5의 "자산 구축 입력에서 완전 배제"를 지키려면 1단계 자산까지
재생성해야 하고, 그러면 원인 귀속이 불가능해진다.

제외 10장: `account_siphon` `code_siphon` `disrupter` `expert_schedule_analyzer`
`exploratory_romp` `heartbeat` `muresh_bodysuit` `sacrificial_construct` `scrubber` `singularity`

**③ Seed v4 종료 조건 = 게이트2 위반 0건 + 처리율 비후퇴 가드.**
처리율과 게이트1 달성률은 목표가 아니라 보고 의무다. 가드가 필수인 이유는 게이트2가
납품분만 채점하고 **천장도 납품분으로 계산**되기 때문이다 — 어려운 카드를 보류시키면
점수와 천장이 같이 올라가 번역 개선 없이 100%를 살 수 있다.

**90장 기준 실측**

| 게이트 | 에이전트 | 정답 천장 | 임계 | 판정 |
|---|---|---|---|---|
| 1 용어 준수 | 0.8481 | 0.7453 | 0.95 | UNREACHABLE (달성률 113.8%) |
| 2 기호 보존 | 0.9296 | **1.0000** | 1.0 | **FAIL** |
| 3 편집거리 | 0.1324 | — | 0.2847 | 통과 |

납품 71 / 미납품 19 · 처리율 78.9%

### 이 인터뷰에서 새로 밝혀진 것

- **미납품 19장은 에이전트가 움직일 수 없다.** 실측상 트리거가 전부 `new_term`이고
  (`term_conflict` `rule_violation` `low_tm_confidence` 모두 0), 원인은 전부
  `approval_incomplete`다. 차단형 `new_term`은 원문 EN으로 정해지므로 초벌과 무관하다.
  해소하려면 사람이 용어집을 등재해야 한다. 경계 상수는
  `run_pipeline.py`의 `AUTO_RESPONDER_HOLD_TRIGGERS = frozenset({'new_term'})` 하나다.
- **게이트2 남은 5건 = 과잉 4 + 누락 1.** "개수까지 1:1" 프롬프트 제약은 과잉 4건만
  겨냥한다. 위반 0건을 종료 조건으로 걸면 누락 1건(`trace_1`)도 범위에 들어간다.
- **게이트1 113.8%는 계측 뒤집힘과 직역 편향이 동시에 참이다.** 같은 428개 검사에서
  에이전트 위반 65 / 사람 위반 109인데, 카드를 열면 에이전트가 점수를 더 받는 이유가
  곧 번역이 더 나쁜 이유다:

  | 사람 공식 번역 | 에이전트 |
  |---|---|
  | `[subroutine] 런을 종료한다.` | `[subroutine] 런 종료.` |
  | `적대적 매수를 득점할 때` | `이 아젠다를 득점할 때` |
  | `사이버-사이퍼는 … 사용할 수 있다` | `이 프로그램을 사용한다` |
  | `러너는 이를 방지하기 위해 태그 1개를 받을 수 있다` | `러너는 태그 1개를 받아 이를 피해 방지할 수 있다` |

  달성률 100% 초과를 "계측 경보"로만 닫으면 편향을 놓치고, "품질 우위"로 읽으면 정반대로 틀린다.
- **hold-out을 루프의 정지 조건으로 쓰면 시험지가 탄다.** 반복해서 보면 훈련 신호가
  되어 §5의 "자산 구축 입력에서 완전 배제" 전제가 깨진다. *측정당하는 쪽이 자기 자를
  고치면 안 된다*와 같은 논리다.
- **90장 평가집합이 아직 코드로 재현되지 않는다.** `corpus_split.py`에 EN 개정 필터가
  없어 지금은 제외 id를 스크립트로 뺀 산물이다. 사양 근거가 되려면 코드화가 선행돼야 한다(사람 일).
- `evaluate_pipeline.main()`은 비영 종료코드를 내지 않아 **기계가 읽는 완료 신호가 없다.**

### ④ 실행 전제 = 감독형 단일 실행 (라운드 3)

무인 Ralph 루프를 전제하지 않는다. 사람이 띄우고 결과를 직접 읽는다. 근거는 실적과
불변식이다 — 이 호스트 EventStore 직접 집계로 **ralph 12건 created 중 완료 0 / 실패 11 /
취소 1**이고(evaluate 8/10, execute_seed 5/9), `lineage.generation.completed`는 6 리니지에
8회로 리니지당 평균 1.33세대다. 게다가 무인 자가판정은 순서 문제가 아니라 불변식 충돌이다 —
게이트2를 기계가 판정하려면 유일한 기계 채널인 pytest에 실모델 호출과 hold_out 열람을
넣어야 하고, 그러면 §5의 "자산 구축 입력에서 완전 배제"가 깨진다. `seed_content`는
generation 1에서만 읽히므로 무인 다세대는 세대 2~N을 Seed v4 없이 돌린다.

단 "감독형이므로 정지 신호 불필요"는 받지 않는다. **JSON verdict 리포트 + 비영 종료코드는
사람이 소유한 계측기 항목으로 만들되 발행 blocking에서는 뺀다**(§5의 "자기 보고와 실측이
4회 중 2회만 일치" 때문). 반면 **90장 코드화는 blocking 선행 조건이다.** 무인 자가판정은
v5 이후로 미룬다. 실행 시 `auto_evolve: false`를 명시적으로 넘긴다.

### ⑤ 처리율 비후퇴 가드 = 미납품 card_id 집합 조건 (라운드 4)

숫자를 Seed에 쓰지 않는다. **이 실행의 미납품 card_id 집합 ⊆ 기준 기록의 미납품 집합**이고
기준에 없는 card_id가 1건이라도 미납품이면 통과 무효다(fail-closed). 부분집합이므로 "더
많이 납품"은 허용하고 치환은 금지한다. 보조 증거 셋 — 자산 diff 0 / 미납품 전건 원인 분류 /
건수 보존. `empty_cause`가 `model_invocation_failure`인 건이 1건이라도 있으면 불합격이
아니라 **계측 무효(재실행)**다.

**문언 함정 둘.** `new_term`은 `empty_cause` 값이 **아니다** —
`field_card_synthesis.py`의 `EmptyPredictionCause`는 `tm_search_failure` /
`guard_rejected_in_review_queue` / `approval_incomplete` / `model_invocation_failure`
4값뿐이고 `new_term`은 `hold_reasons`에 산다. 코드로 참인 문장은
`empty_cause == 'approval_incomplete'` **AND** `hold_reasons == ['new_term']`이다.
그리고 관용 밴드를 두지 않는다 — `tests/test_tm_baseline.py`에 "이전 구현이 이 단언을
[0.25, 0.55]로 넓혀 스스로 통과시켰다"는 기록이 있고, 같은 파일 `:57`이 `hold_out.json`이
없으면 `pytest.skip`으로 **fail-open**한다.

### ⑥ AC 8칸 배치와 기준 기록 (라운드 5·6)

AC6을 두 칸으로 쪼개고 v3 AC7(3단 집계)을 계측층으로 흡수해 8칸을 유지한다. **혼합 슬롯을
0개로 만든다** — `verdict_is_authoritative`가 슬롯 단위 필드라서, 밀폐 pytest 항목과 사람
판정 항목을 한 슬롯에 섞으면 사람이 읽기 전까지 그 슬롯 전체가 unresolved가 된다.

| 칸 | 내용 | 판정 주체 | 무효 분기 |
|---|---|---|---|
| 1 | 라우팅 — v3 AC1 승계 | 밀폐 pytest | 없음 |
| 2 | 초벌 생성 + **기호를 개수까지 1:1 보존하라는 프롬프트 제약 추가** | 밀폐 pytest | 없음 |
| 3 | 가드레일 5종 + 검토 큐 — v3 AC3 승계 | 밀폐 pytest | 없음 |
| 4 | HITL interrupt + 차단형/기록형 — v3 AC4 승계 | 밀폐 pytest | 없음 |
| 5 | 승인 후 resume + approved_store. **자산 diff 0으로 한 줄 확장** | 밀폐 pytest | 없음 |
| 6 | 계측층 — 90장 실행·납품분 채점·게이트2 위반 0건·미납품 ⊆ 기준·3단 집계 흡수 | 밀폐 pytest | **있음** |
| 7 | 보고층 — 게이트1 3열·처리율·원인별 건수·TM 에코·모델 내역 | 사람 판정 | 게이트1은 UNREACHABLE |
| 8 | eval_autoresume — v3 AC8 승계(낡은 "테스트 없다" 서술 삭제) | 밀폐 pytest | 없음 |

성공 = **8칸 전건 통과.** 하니스가 `ac_gate_mode="all"` / `ac_min_pass_ratio=1.0`이고
오버라이드 호출부가 0건이며 `final_approved = all(ac.authoritative_pass)`이므로 "7/8이면
성공"은 표현 자체가 불가능하다. "통과"의 뜻만 칸마다 고정한다 — 관찰 지표의 **값** 미달은
실패가 아니고, 관찰 지표 **필드의 누락**은 7번 실패이며, 천장 미달·`model_invocation_failure`
는 계측 무효로 통과도 실패도 아니다.

**이미 구현된 셋은 AC가 아니라 constraints로 내린다** — 천장 검사 선행은 이미 커밋된 사람
소유 코드다(`src/gate_ceiling.py`, `tests/test_gate_ceiling.py`, 커밋 `12c355a`·`e6ac521`).
AC로 명령하면 Seed가 에이전트에게 자기를 채점하는 자를 다시 쓰라고 시키는 것이 된다.

**기준 기록** = `data/throughput_baseline.json`, **Seed가 돌 브랜치에 커밋한다**(main에는
`hold_out.json`이 없어 가드가 조용히 아무것도 읽지 않는다). 형식은
`data/rule_terms_gold.json` 선례를 따르고 필드는 `population`(source·sha256·card_count
90·excluded_card_ids 10개·exclusion_point `post_split_id_filter`·excluded_disposition
`dropped_not_moved_to_train`) / `instrument`(glossary_path·glossary_sha256·commit·
llm_judged·hold_triggers) / `withheld`(derivation·card_ids·count 19·empty_cause·
hold_reasons) / `delivered_count` / `throughput` / `on_missing: "fail"`이다.
**파일 자체의 해시는 파일 안에 넣지 않고 Seed 제약에 리터럴 sha256으로 박는다** — 하니스에
쓰기 범위 제한 장치가 전혀 없고(`claude_permissions.py:35`가 `WORKSPACE_WRITE`를
`acceptEdits`로 매핑, `write_scope`·`allowed_paths`·`protected_path`·`read_only_paths`
검색 0건) Seed 텍스트만이 런 중 수정 불가이기 때문이다. 이 sha256은 측정값이 아니라
**동결된 사람 입력의 정체성**이므로 결정 ③의 대상이 아니다 — 이 문장을 Seed에 명시한다.

미납품 집합은 **사람이 90장 코드화 직후 `check_new_terms`를 오프라인 순수 함수로 한 번
도출해 리터럴로 동결한다. 런 안에서 재도출하지 않는다.** 재도출을 판정 정의로 쓰면
`AUTO_RESPONDER_HOLD_TRIGGERS`와 `new_term_guard`가 에이전트 소유 제품 코드이고 v3 AC4가
토크나이저 변경을 명령하므로, 분류기를 바꾸면 기준선이 같이 움직여 `⊆`가 항상 참이 된다.
실측상 파생 == 관측이다(100장 20건 / 90장 19건, 차이 1장은 `disrupter`로 제외 10 안).

EN 개정 10장 가지치기는 **split 이후 id 배제**로 정확히 90장을 만든다. 코퍼스 조건으로
올리면 980 → 970이 되어 seed 42 셔플이 달라지고 결정 ②("재분할하지 않는다")를 깬다.
제외 10장은 train으로 옮기지 않고 버린다(train 880 유지 — 옮기면 phase-1 자산이 오염되고
§5의 "hold-out은 자산 구축 입력에서 완전 배제"가 깨진다).

**사람 선행 작업 순서** ① split-후 필터로 90장 `hold_out.json` 재발행 ② 100→90으로 움직이는
테스트 갱신 ③ 90장에서 순수 도출 → 19건 일치 확인 ④ `throughput_baseline.json` 생성·커밋
⑤ Seed 제약에 sha256 리터럴 기입 ⑥ Seed v4 발행.

갱신할 테스트: `test_tm_baseline.py:67`(len 100→90, `:68`의 880은 유지),
`test_gate1_term_compliance.py:223`, `test_gate2_symbol_preservation.py:402·420`,
`test_gate3_edit_distance.py:263·275`, `test_evaluate_pipeline.py:109`(`gate3.n` 100→90)과
`:127-128`(rate 0.95→1.0, 95/100→90/90 — **이것이 게이트2 천장 1.0 회귀 테스트가 된다**),
`test_ac6_entry_point.py:129`. `tests/test_corpus_split.py`는 `:101-102`가 `build_split`
함수 수준 호출이라 split-후 필터에서는 그대로 살아남는다(실측 확인). 이 갱신은 계측기
작업이므로 **사람 몫**이다 — 빨간 트리로 Seed를 넘기면 에이전트의 첫 작업이 자기 채점기
테스트 수정이 되어 §4 경계가 무력화된다.

TM 베이스라인은 기준 기록에 담지 않는다 — 실측상 100장·90장 모두 median 0.3850이고
게이트3 유도 임계 0.2695가 동일해 `BASELINE_MEDIAN 0.385 ± 0.025`가 그대로 생존한다.

## 3.6 수락 게이트가 막은 것 — Seed 발행 전에 닫아야 한다

MCP가 seed-ready를 냈지만 **3레인 수락 게이트가 통과하지 못했다.** `closer`는 `seed_ready`
였으나 `contrarian`과 `gap_hunter`가 HIGH를 올렸고, 아래 넷은 **호스트가 직접 재현해
확인했다.** 다음 세션은 여기서부터 시작한다.

**1. 슬롯 6의 판정 주체가 스스로 모순이다 (최우선).** 라운드 3이 "AC6 밀폐층은 고정
픽스처만 쓴다. Bedrock을 호출하지 않고 hold_out의 ko_text를 읽지 않는다"고 제약했는데,
라운드 5가 슬롯 6(밀폐 pytest)에 90장 전량 실모델 실행이 필요한 항목을 넣었다. 채점
*로직*은 픽스처로 검증 가능하지만(`gate2_symbol_preservation.score_*`는 `cards`와
`predictions`를 함께 받으면 `hold_out.json`을 읽지 않는다 — 실측 확인) **이번 실행의 위반
0건은 실모델 산출물이 있어야 잰다.** 그리고 슬롯 6은 전 이력에서 유일하게 `blocked`·
`recovery_exhausted`를 기록한 슬롯이다(stall AC6 3회 / 나머지 각 1회 이하, judged failure
AC6 2회). 선택지는 (a) 슬롯 6을 진짜 밀폐 항목만 남기고 종료 조건 판정을 사람 소유
스크립트로 빼되 그 스크립트의 비영 종료코드를 발행 blocking으로 올린다 (b) 실모델을 pytest
안에 허용하고 라운드 3 제약을 철회한다 (c) 슬롯 6을 사람 판정으로 내린다(fail-closed 포기).
**미결.**

**2. 미납품 card_id를 내보내는 코드가 없다.** 하드 가드가 card_id 집합 비교인데
`evaluate_pipeline.py`는 `withheld_count`(숫자)만 내보낸다 — card_id 목록 배출 경로가 없다
(실측 확인). 그리고 라운드 3이 JSON verdict 배출을 non-blocking으로 내렸다. **유일한 하드
가드가 non-blocking 항목에 의존한다.** 1번과 함께 닫아야 한다.

**3. diff-0 기준 커밋이 비어 있고 후보 `d100840`은 무효다.**
`git diff --stat d100840..f160d4a -- src/` 실측: `gate1 +58` · `gate2 +4` · `gate3 +4` ·
**`gate_ceiling.py +105`(신설)** · `evaluate_pipeline +125` · `glossary_guard +16` — 얼리려는
목록 그 자체다. `d100840`을 기준으로 잡으면 가드가 t=0에 이미 위반이고 에이전트가 "고치는"
방향은 사람의 계측기 수정을 되돌리는 것이다. 권장은 **사람 선행 작업(①~④) 완료 커밋의
sha를 기준으로 Seed 제약에 리터럴로 박는 것**이다. 아울러 납품 판정
(`evaluate_pipeline.py:631`)·원인 분류(`field_card_synthesis.py:156-172`)·차단형 분류
경로에는 앵커가 전혀 없다. **미결.**

**4. Seed가 돌 브랜치가 워크트리에 점유돼 있다.** §0·§1 참조. 착수 전 해제가 선행 조건이다.

### 아직 닫히지 않은 그 밖의 미결

- 계측 무효(재실행)의 횟수 상한. 상한이 없으면 `model_invocation_failure`가 무한 재실행의
  합법적 출구가 되고, 그 원인 값을 기록하는 주체가 채점당하는 제품 코드 자신이다
  (`run_pipeline.py:319`, 그리고 `field_card_synthesis.py`의 "생산자가 기록한 원인이 이긴다").
- 용어집 결함 수정(§4 작업1-4)과 "채점용/주입용 용어집 분리"(§7-2)를 기준 기록 생성 전에
  끝낼지. 역어(값) 수정은 표제어(키)를 바꾸지 않으므로 미납품 집합은 불변이어야 하지만,
  키 구성을 바꾸는 분리안을 채택하면 기준 기록과 Seed의 sha 리터럴을 다시 찍어야 한다.
  더 무거운 것 — 용어집 수정으로 **게이트1 천장이 0.95를 넘기면 게이트1이 UNREACHABLE을
  벗어나 `final_passed`를 차단하기 시작한다**(`evaluate_pipeline.py`의 `blocks_delivery`
  경로). Seed가 에이전트에게 개선을 금지한 게이트다. 무효/실패/보고 중 무엇으로 처리할지
  정해야 한다.
- **SERVICE.md 내부 모순** — §5는 게이트1을 관찰 지표로 강등했는데 §8(`:397`)은 여전히
  "hold-out 100장, 하드 게이트 3개"라고 적혀 있다. v3 Seed의 goal 문장도 "하드 게이트 3개와
  관찰 지표에 통과시켜"라고 명령한다. 둘 다 발행 전에 고쳐야 에이전트가 폐기된 기준을 쫓지
  않는다(§4 작업3의 "goal은 그대로 둔다"도 함께 정정 대상).
- §4 소유 표에 기준 기록 행 추가. 기준 기록은 계측기도 정답지도 아닌 **제4 자산 부류**
  (에이전트의 과거 행동을 동결해 가드 기준선으로 쓰는 것)다. 미등재 자산은 표 논리상
  "아무나"로 떨어져 Ralph가 자기가 채점받는 기준선을 소유하게 된다.
- AC5 감시 목록 확장은 §4 작업3의 "나머지 AC 6개와 goal은 그대로 둔다"와 충돌한다.
  재작성·이동 슬롯(2·6·7·8)은 새 `semantic_ac_key`를 발급해야 한다(`focus.py` 동결이 위치
  기반이고 `evaluation_coverage`가 `ac_content` 축자 일치를 요구한다).
- `withheld.card_ids`를 문자열 배열로 둘지 `{card_id, triggers, cause}` 객체 배열로 둘지.

### 이어받는 방법

인터뷰 상태는 `~/.ouroboros/data/interview_interview_20260918_013149.json`에 있다
(status `completed`, 6라운드, ambiguity 0.082). 재개는

```
/ouroboros:interview 20260918_013149 resume
```

이지만 **이미 completed이므로 새 라운드가 아니라 위 §3.6 미결을 사람이 정하고 Seed를 직접
쓰는 편이 빠르다.** `ooo seed`를 돌리려면 `session_id="interview_20260918_013149"`를 준다.
단 §3.6의 1~4가 열려 있는 채로 발행하면 "초록불인데 측정당하는 쪽이 만든 초록불"이 될 수
있다.

## 4. 다음에 할 일

> **일의 경계 — 누가 하느냐가 순서보다 중요하다**
>
> **측정당하는 쪽이 자기 자를 고치면 안 된다.** 계측기와 평가 데이터는 사람이 고치고,
> 제품 구현만 Seed에 맡긴다. Ralph에 계측기 수정을 시키면 에이전트가 자기를 채점하는
> 자와 정답지를 스스로 만지게 되고, 그러면 "게이트가 통과하도록 정답이나 임계를 바꾸지
> 않는다"는 제약을 검증할 수단 자체가 오염된다.
>
> | 일 | 성격 | 주체 |
> |---|---|---|
> | 채점 모집단 분리 · 게이트 1 `score_reference` · 천장 테스트 | 계측기 | **사람** |
> | hold_out 재생성 · 용어집 결함 수정 | 평가 데이터·정답지 | **사람** |
> | 초벌 프롬프트 기호 1:1 제약 | 제품 | **Seed** |
> | 테스트 픽스처 누출 차단 | 위생 | 아무나 |

### 작업 1 — 계측기와 평가 데이터를 고친다 (사람) ★ 본체

사양은 고쳤고 코드는 아직 옛 정의대로다. 순서는 효과 크기 순이다.

1. **채점 모집단 분리** — 하드 게이트를 납품분에만 적용하고 미납품은 처리율 지표로 뺀다. 임계를 건드리지 않고 왜곡만 제거한다. 재현 기대치: 게이트1 63.05 → 81.67%, 게이트2 82.0 → 91.25%
2. **게이트 1에 `score_reference()` 신설 + 천장 회귀 테스트** — 게이트 2에는 이미 있는데 판정 경로에 결선되지 않았다. 천장 < 임계면 `UNREACHABLE`로 실패시켜 도달 불가 기준이 다시 여러 세대를 버티지 못하게 한다
3. **hold_out 재생성** — EN 개정 조건을 적용해 판본 불일치 10장을 뺀다. 게이트 2의 천장이 100%로 올라가 하드 게이트로 유효해진다. `tests/test_tm_baseline.py`의 베이스라인 감시선이 함께 움직이므로 같이 갱신한다
4. **용어집 결함 수정** — 한글 없는 역어 15개, 괄호 리터럴 1개, 사전형 용언 12개. 채점용과 주입용의 역할을 가르는 것도 함께 검토한다(같은 자산이 프롬프트 주입원·HITL 차단 트리거·게이트 정답지 셋을 겸하고 있어 한 노브로 셋을 동시에 만족시킬 수 없다)
5. **테스트 픽스처 누출 차단** — 테스트가 저장소 루트 `new_term_candidates.json`에 쓰지 못하게 한다. `e0d23e5`의 `tmp_path` 리다이렉트가 일부만 덮었다

이 중 어느 것도 게이트를 녹색으로 만들지 못한다. 그게 정상이고, 그 사실을 측정으로 확정하는 것이 산출물이다.

### 작업 2 — 고친 계측기로 재측정한다 (사람)

작업 1이 끝나면 게이트 수치가 처음으로 실제 값이 된다. **작업 3의 입력이 이 수치다.**

- 게이트 2 천장이 100%인지 확인 — 맞으면 하드 게이트로 복귀
- 용어집 수정 후 게이트 1 천장이 어디까지 오르는지 실측 — 95%를 넘으면 게이트 1도 복귀, 못 넘으면 관찰 지표 유지가 확정된다
- 세 판정(§2)을 다시 돌려 기준선을 갱신한다

### 작업 3 — Seed v4를 쓴다

> **2026-09-18 갱신** — 인터뷰가 끝나 v4의 내용은 **§3.5 ①~⑥에 확정돼 있다.** 아래 문단이
> 말하는 "AC6 문언과 AC2만 고치고 나머지 6개는 그대로 둔다"는 **낡았다** — §3.5 ⑥이 AC6을
> 두 칸으로 쪼개고 v3 AC7을 흡수하며 AC5·AC8 문언도 고친다. 발행 전에 닫아야 할 것은
> **§3.6**에 있다.

**`seed-phase2c-v3.yaml`의 AC6은 옛 게이트 설계를 명령한다** — 게이트 1을 ≥95% 하드 게이트로 전제한다. SERVICE.md가 바뀐 이상 그대로 다시 돌리면 에이전트가 폐기된 기준을 쫓는다. Gen 4에서 AC4가 반대말을 하고 있어 결정 ④가 구현되지 않았던 것과 같은 함정이다.

**작업 2 이후에 쓴다.** 지금 쓰면 곧 움직일 숫자 위에 쓰는 셈이고, 작동하는 계측기를 전제로 써야 AC6이 의미를 갖는다. 수치는 상수로 박지 않고 관계로 쓴다(결정 ③).

v3에서 고칠 곳은 AC6 문언과 관련 제약, 그리고 **초벌 프롬프트의 기호 1:1 제약을 AC2에 넣는 것**이다. 나머지 AC 6개와 goal은 그대로 둔다. AC는 8개로 고정한다.

### 작업 4 — 브랜치 정리

로컬 브랜치 11개 중 6개가 역할이 끝났다(§1 표). Gen 5가 푸시됐으므로 유실 위험 없이 정리할 수 있다.

### 작업 5 — phase3 (Issue 동기화 · Pages 검수 뷰)

Seed는 있다 — `main`의 `.ouroboros/seed-phase3.yaml`(AC 7개). 다만 `brownfield_context.context_references[0].path`가 낡은 워크트리를 가리키므로 돌리기 전에 최신으로 고친다.

## 5. 함정 모음 (직접 밟은 것들)

### ralph 내부 score는 품질 신호가 아니다 ★ 가장 비싸게 배운 것

`evolve_step`의 `spec_verifier`는 산출물 판정기가 아니라 **소스 정규식 스캐너**다. 행위·주관 assertion은 코드 실행 없이 SKIPPED가 되고(`verification/verifier.py:1022-1028`), 한 AC에 SKIPPED가 하나라도 섞이면 그 AC는 NOT_EVALUATED = 점수상 FAIL이다(`spec_verification_adapter.py:233,244`). Gen 4는 AC 8개 전부가 그 상태여서 **score 0.0이 코드를 쓰기 전에 확정돼 있었다.**

7세대 동안 이 경로가 기능 결함을 지적한 적은 0건이다. non-verdict 비율은 `formal_evaluation` 0/47 대 `spec_verifier` 18/23. **AC 문언을 이 스캐너가 좋아하는 형태로 깎지 않는다** — Gen2→Gen3에서 문언을 전부 갈아치웠는데도 비율은 0.833 → 0.778로만 움직였다.

판정은 §2의 세 가지로 한다.

### 자기 보고와 서브에이전트 보고를 실측 없이 인용하지 않는다

2026-09-16~17 실행 4회 중 자기 보고와 실측이 일치한 것은 2회뿐이다. phase2a는 "All 452 tests pass"라고 했으나 실측은 23 failed였다. 서브에이전트 레인 보고도 3건이 틀렸다.

이번 세션의 5인 토론은 보고가 전부 맞았지만, 그것도 **호스트가 같은 수치를 직접 재현한 뒤에** 문서에 넣었다. 그 습관을 유지한다.

### 스텁은 조용히 성공한다

`_StubLLM`은 프롬프트에서 TM 상위 1건의 KO를 정규식으로 꺼내 그대로 돌려준다. 테스트 850개가 전부 통과하는데도 파이프라인이 쓸 만한 초벌을 낸 적이 없던 시기가 있었다. 지금은 진입구가 막혔지만, **초벌 품질과 게이트 수치는 반드시 실제 모델 실행으로만 판단한다.** 저장소에 남은 옛 `pipeline_output.jsonl`을 채점해도 같은 착시가 재현된다.

### Ralph는 깨끗한 워크트리를 전제한다

2026-09-17 시도 9회 중 정상 시작 3회. 실패 원인이 전부 워크트리 상태였다.

착수 전 체크리스트:

1. `git worktree list`로 대상 브랜치를 **아무 워크트리도 점유하지 않는지** 확인. 점유 중이면 그 워크트리에서 `git checkout --detach`
2. `git worktree prune`
3. 대상 워크트리가 **클린**해야 한다. 손으로 만든 파일 하나에도 안 돈다
4. `.gitignore`에 `.ouroboros/context_pack.json`, `.ouroboros/mechanical.toml`, `.ouroboros_eval_artifact.md`가 있어야 한다 — 브랜치마다 따로 필요하다

시작 직후 확인할 것 둘: 새 워크트리가 의도한 커밋 기반인지, 이벤트 스토어의 `ac_index`가 0~7(8개)인지.

### 세대 경계에서 잡이 죽을 수 있다 ★ 이번에 새로 밟았다

Gen 5는 세대 1을 정상 완료한 뒤 워크트리를 지우다 `WinError 32`(다른 프로세스가 파일 사용 중)로 죽었다. **재발하면 몇 세대를 걸든 1세대마다 잡이 끝난다.** 감시가 이걸 못 잡으면 밤을 통째로 날린다 — 실제로 12.5시간을 날렸다.

감시 중에는 워크트리 안에서 git을 돌리지 말고 main repo에서 브랜치 ref로 본다:

```bash
git -C <main repo> log --oneline <base>..<branch>
```

### 잡 완료 판정은 교차 확인한다

`job_wait`은 워커가 죽은 뒤에도 한동안 `running`을 반환한다. `~/.ouroboros/detached-jobs/job_<id>.status.json`의 `worker_pid`로 프로세스 생존을 확인한다. `timeout_seconds`는 5를 넘겨도 5로 잘린다.

세션 프로젝션(`ouroboros_session_status`)의 `completed_count`는 뒤처진다 — 이벤트 스토어(`~/.ouroboros/ouroboros.db`)의 `execution.ac.completed`를 센다.

### 체크포인트 커밋은 원자적이지 않다

Ralph가 AC 단위로 커밋하면서 그 AC가 건드린 파일 일부를 빠뜨린 적이 있다. Gen 3에서는 AC 두 개가 통째로 날아갔다. **"커밋됐으니 안전하다"는 커밋 기준으로만 참이다** — `git log <범위> -- <파일>`로 확인한다.

### `auto_evolve`가 켜져 있다

`~/.ouroboros/config.yaml`의 `auto_evolve: true` 때문에 `ouroboros_evaluate`가 REJECTED를 내면 Ralph 루프가 자동으로 시작된다. 판정만 받고 싶으면 `auto_evolve: false`를 명시적으로 넘긴다. `auto_evolve_max_generations`는 10이다(이전 값 4는 `config.yaml.bak-20260917-gen5`).

### 쓰면 안 되는 두 경로

- **`ooo auto`(`start_auto`)** — 인터뷰를 다시 돌려 **새 Seed를 만든다.** 기존 Seed를 넣는 인자가 없다.
- **`execute_seed` + `auto_evolve` 체인** — `evaluate_ralph_chain.py:332`가 ralph에 `max_generations`만 넘겨 타임아웃이 기본 30분으로 적용된다. 세대당 80분이 걸리므로 매번 잘린다.

이어붙이려면 `start_ralph`에 `per_iteration_timeout_seconds`·`max_total_seconds`를 직접 준다. `seed_content`는 **generation 1에서만 읽힌다** — 기존 리니지를 이어 붙이면 새 Seed가 한 줄도 반영되지 않는다.

### Seed 제약에 절대경로 실행파일을 적지 않는다

`evaluation/detector.py:497-509`가 절대경로를 거부해 그 명령은 조용히 드롭된다. 테스트 명령은 `.ouroboros/mechanical.toml`이 소유하고 ouroboros가 스스로 `uv run pytest`를 써넣는다.

### 드리프트 지표는 그대로 믿을 게 못 된다

목표문을 거의 복창한 대조군도 goal drift 0.71(임계 0.3)이 나왔다. Gen 5의 자동 측정치도 combined 0.68이었다. **지표보다 AC 목록과 대조하는 편이 낫다.**

### evaluate 점수는 재현되지 않는다

같은 세션을 두 번 채점해 3/5 → 2/5가 나왔다. 임계 0.80 근처에서 `ac_compliance=true`인데 점수만으로 갈린 항목이 특히 흔들린다. 반면 `ac_compliance=false`로 찍힌 항목은 근거가 구체적이고 재현 가능했다.

### Git Bash가 `/FLAG`를 경로로 바꾸고 백틱을 먹는다

`tasklist /FI` → `/FI`가 `C:/Program Files/Git/FI`로 변환된다. PowerShell `Get-Process -Id <pid>`를 쓴다. 스크립트는 인라인 대신 파일로 써서 실행한다 — 인라인 heredoc이 깨져 문서가 훼손된 적이 있다. **`2>/dev/null`로 에러를 지우면 실패가 음성 결과로 위장한다.**

### 웹 대시보드는 MCP 경로에서 뜨지 않는다

응답의 `dashboard_url`은 주소를 계산해 넣을 뿐 서버를 띄우지 않는다. EventStore의 picker 프로젝션이 깨져 있어 수동 기동해도 데이터가 안 보인다. TUI는 프로젝션을 우회하지만 의존성이 빠져 있다:

```bash
uvx --python ">=3.12" --from "ouroboros-ai[tui]" ouroboros tui monitor --db-path "C:\Users\SDS\.ouroboros\ouroboros.db"
```

### 코퍼스·데이터

- **플레이버는 `v2/cards`가 아니라 `v2/printings`에 있다** — KO도 마찬가지로 `ko/printings`에 710건. `ko/cards`에 `flavor` 키가 0건인 것만 보고 "한국어 플레이버가 없다"고 오판하기 쉽다
- **미번역 레코드 필터링을 통계보다 먼저** — 안 하면 부제 용어의 54%가 사라진다
- **Dice는 용어다움이 아니라 배타성을 잰다** — 전역 상한으로 자르면 `trash`(55,471위)가 잘린다
- **형태소 분석기 없이 한국어 용어를 뽑지 않는다** — `ko_morphology.normalize_eojeol`
- **`netrunner-cards-json-old`는 초벌본이 아니다** — 최종본 스냅샷이라 편집거리 기준선으로 쓸 수 없다
- **임베딩 교체(bge-m3)는 실측 후 기각됐다** — 실제 출하 구성에서 오히려 나빠지고 인코딩이 14배 느리다. 같은 실험을 반복하지 않는다
- **`.env`가 상위 경로에 있다** — 이 repo는 public이다

## 6. Seed 현황

| Seed | 범위 | AC | 상태 |
|---|---|---|---|
| phase1 | 자산 구축 + TM 베이스라인 | 8 | 실행 완료 |
| phase2a | 번역 검토 큐 · 가드레일 · MCP 4도구 | 8 | 실행 완료, 평가 4/8 |
| phase2b | 하드 게이트 채점 · 관찰 지표 | 5 | 실행 완료, 평가 2/5 |
| phase2c · v2 | 온라인 파이프라인 본체 | 6→9 / 8 | 실행 완료 (Gen 1~4) |
| **phase2c-v3** | 같은 범위, Gen 5 입력 | 8 | **실행 완료.** 판정은 §2 — **AC6이 옛 게이트 설계를 전제하므로 재사용 전 v4가 필요하다(§4 작업 3)** |
| phase3 | Issue 동기화 · Pages 검수 뷰 | 7 | 작성 완료, 미실행. `context_references`가 낡음 |

`main`의 `.ouroboros/`에 Seed가 전부 있다. **phase2a도 2026-09-18에 `main`으로 올렸다** —
`ooo/orch_a6bc6099bdae`의 교정본(blob `eb7fc68`, 14,682바이트 147줄)을 그대로 커밋했다.

그전까지 `main` 작업 디렉터리에 같은 이름의 **미추적 옛 판본**(13,941바이트 137줄)이
굴러다녔고, 그게 브랜치를 오갈 때마다 "untracked working tree file would be overwritten"
충돌을 일으켰다. 두 판본은 전체가 다르다(`diff`가 `1,137c1,147`). 교정본을 추적 상태로
올려 그 충돌 부류를 없앴다. 옛 판본은 어디서도 읽지 않으므로 보존하지 않았다.

## 7. 아직 안 정한 것

1. **게이트 1을 하드 게이트로 복귀시킬 조건** — 사양은 "천장이 95% 이상임이 실측되면"이라고 적었다. 매칭을 형태소 기반으로 고쳐도 천장은 약 87%로 추정되므로, 용어집 수정이 어디까지 천장을 올리는지 먼저 재야 한다
2. **`glossary.json`의 역할 분리 여부** — 프롬프트 주입원·HITL 차단 트리거·게이트 정답지 셋을 한 자산이 겸한다. 채점용을 `official`+`subtype_extracted`로 좁히고 주입용은 전체로 두는 안이 나와 있다
3. **TM 검색 품질** — `tm_confidence`가 0.023~0.033이라는 극히 좁은 띠에 뭉쳐 있어 p20 임계가 사실상 임의 분할이다. TM 융합 설계를 다시 볼지, 임계 유도 방식을 바꿀지 미정
4. 2021–2022 번역분 142장의 코퍼스 편입 여부
5. 편집거리 감소폭의 납품 이후 추적 방법·주기
6. **AC 확장을 어디서 멈출지** — Ralph가 AC7·AC8·AC9를 스스로 추가했고 AC9만 범위 밖으로 판정해 뺐다. 일반 기준은 아직 없다. "AC 동결"은 개수 동결이지 문언 동결이 아니다
7. **자율 루프를 계속 쓸지** — 세대 경계 크래시로 무인 다세대 실행이 이 환경에서 불안정하다. 이번 세션의 실질적 진전은 자율 루프가 아니라 표적 조사에서 나왔다

### 알려진 사소한 결함 (미수정)

- `glossary.json`의 `counts.official`이 **160**인데 실제 항목은 **154**개다. `OfficialGlossary.__len__`이 카테고리별 합을 세고 `all_terms()`는 평탄화하며 중복 id 6개를 합친다(`src/load_official_glossary.py:46-54`). 보고 숫자만 어긋난다
- `obs_llm_judge`에 청크·체크포인트·프롬프트 지문·dropped 집계가 없다. `src/term_judge.py`가 그 규율의 기존 구현이다
- 룰 용어 정답셋 채점 최신 실측 — 룰 추출 경로 EN 재현율 73.3% / KO 정확도 90.9%, 납품 용어집 전체 87.1% / 96.3%. 못 맞힌 4개 중 2개는 추출 실패가 아니라 표제어 단위 불일치(`credit` 단수형 부재, `gain` 바이그램만 존재)이고, KO 오답은 `HQ`(`본부 (HQ)`) 하나뿐이다. 재실행은 `python -m rule_gold_eval`
