# 인계 문서 — Netrunner 번역 보조 에이전트

**갱신 2026-09-18.** 새 세션이 이 문서만 읽고 이어받을 수 있도록 쓴다.
사양의 단일 진실 원천은 [`SERVICE.md`](../SERVICE.md)이고, 이 문서는 **지금 어디까지 됐고 다음에 뭘 해야 하는지**만 다룬다. 게이트 정의·임계·평가집합 조건은 여기서 다시 쓰지 않는다 — SERVICE.md §5를 본다.

---

## 0. 30초 요약

- 기획 완료. 1·2단계 구현 완료. 파이프라인이 **처음으로 실제 모델로 전량 실행됐다.**
- 최신 코드는 `ooo/ralph-3436c74545374d8296a877f5d4589e` @ `d100840`, **테스트 939개 통과**(실측). 검증용 워크트리가 `~/.ouroboros/worktrees/verify-gen5`에 그 커밋으로 열려 있다.
- 브랜치는 origin과 동기화돼 있다(2026-09-18 푸시). **미푸시 커밋은 없다.**
- **이번 세션의 발견: 공식 KO 정답 번역이 하드 게이트 1·2를 통과하지 못한다**(천장 74.69% / 95.00%). 도달 불가능한 기준을 향해 7세대를 태우고 있었다. 이 발견을 반영해 **SERVICE.md §5를 개정했다**(`24a2ba4`).
- 남은 일은 그 개정을 **코드에 반영**하는 것이다. 게이트를 통과시키는 작업이 아니라 게이트가 무엇을 재는지 고치는 작업이다. **계측기와 평가 데이터는 사람이 고치고 제품 구현만 Seed에 맡긴다** — 이유는 §4 머리의 경계 표.
- **ralph 내부 score를 품질 신호로 쓰지 않는다** — §5 첫 항목. 이걸 모르면 또 한 세대를 버린다.

## 1. 좌표

| 항목 | 값 |
|---|---|
| 코드 repo | `castigar/netrunner-glossary` (public — `.env`·토큰을 절대 커밋하지 않는다) |
| 로컬 main | `C:\Users\SDS\Desktop\sds-ax-practice\mini_pjt` |
| 검증 워크트리 | `C:\Users\SDS\.ouroboros\worktrees\verify-gen5` (detached @ `d100840`) |
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
| **`ooo/ralph-3436c74…`** | **`d100840`** | ○ | **최신. 여기서 작업한다.** Gen 5 산출 10커밋, 테스트 939 |
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

`main`의 `.ouroboros/`에 Seed가 있다. **phase2a Seed만 `main`에 없고 `ooo/orch_a6bc6099bdae`에 있다.** `main` 작업 디렉터리에 같은 이름의 미추적 파일이 굴러다니는데 그건 **교정 전 옛 판본**이다 — 아무것도 읽지 않으므로 그냥 두되, Seed를 찾을 때 그것을 집지 않도록 주의한다.

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
