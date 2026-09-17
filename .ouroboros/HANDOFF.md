# 인계 문서 — Netrunner 번역 보조 에이전트

**작성 2026-09-16 · 갱신 2026-09-17(Gen 4 세션).** 새 세션이 이 문서만 읽고 이어받을 수 있도록 쓴다.
사양의 단일 진실 원천은 [`SERVICE.md`](../SERVICE.md)이고, 이 문서는
**지금 어디까지 됐고 다음에 뭘 해야 하는지**만 다룬다.

---

## 0. 30초 요약

- 기획 완료. 1·2단계 구현 완료. **3단계(확정 절차)만 남았다.**
- **Gen 4까지 돌았다.** 최신 코드는 `ooo/ralph-c7ac33666c6a487f8e9ace45aebd9a` @ `31d3995`,
  **테스트 878개 통과**(지정 인터프리터 실측, 자기 보고와 일치). AC 8개를 전부 실행하고 4커밋을 남겼다.
- **그러나 평가 score는 0.0이었고, 그것은 에이전트가 코드를 쓰기 전에 이미 확정돼 있었다.**
  ralph 내부 평가자 `spec_verifier`는 산출물 판정기가 아니라 **소스 정규식 스캐너**다. 행위·주관
  assertion은 코드 실행 없이 무조건 SKIPPED가 되고, 한 AC에 SKIPPED가 하나라도 섞이면 그 AC는
  NOT_EVALUATED = 점수상 FAIL이다. Gen 4는 AC 8개 **전부**가 그 상태였다. **§2.6을 먼저 읽는다.**
- **`approved.jsonl`이 처음 생겼다(215건).** 다만 215/215가 코퍼스의 *다른* 카드 KO와 바이트 단위로
  동일한 **스텁 산출물**이다. HITL 통과율 0%는 `eval_autoresume`으로 우회된 것이지 해결된 것이 아니다.
- **Seed v3가 완성돼 있다** — `.ouroboros/seed-phase2c-v3.yaml`(AC 8 · 제약 53 · 온톨로지 20).
  미확정 상수는 없다. 다음 할 일은 §4 작업 1(Gen 5 착수).
- **미푸시 커밋이 있다.** Ralph 브랜치 2개가 전부 origin에 없다 — §1 브랜치 표.
- **Ralph를 돌리기 전에 §5의 워크트리 위생을 반드시 확인한다.**

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

| 브랜치 | HEAD | 테스트 | origin | 내용 |
|---|---|---|---|---|
| **`ooo/ralph-c7ac3366…`** | **`31d3995`** | **878 통과** | ✕ | **여기서 작업한다.** Gen 4 산출 4커밋 |
| `ooo/ralph-6ec830bd…` | `1230fad` | 850 | ○ (ahead 1) | Gen 4의 기반. 프롬프트 수정 커밋이 미푸시 |
| `main` | `6246db7` | — | ○ | 기획 문서·Seed·골든 셋·이 문서 |
| `feat/rule-gold-eval` | `4dfe72a` | — | ✕ | 채점기 원본 — 통합 완료, 역할 끝남 |
| `ooo/orch_fa2a47fe46cf` | `3ad61ca` | 623 | ✕ | 게이트 수정 — 통합 완료, 역할 끝남 |
| `ooo/orch_a3466c696fe8` | `71cdbda` | 694 | ✕ | phase2c 실행 원본 — 최신 브랜치 이력에 포함됨 |
| `ooo/orch_3b1f1bf54116` · `ooo/orch_a474c765ee60` · `ooo/orch_a6bc6099bdae` | — | — | 일부 | 중간 단계 |
| `ooo/ralph-83c344…` | `d4b6ff9` | — | ✕ | 실패한 phase2b 리니지. 버려도 된다 |

**작업 워크트리 경로**
`C:\Users\SDS\.ouroboros\worktrees\ralph-6ec830bda576f952b7b2375d3c3868\ralph-c7ac33666c6a487f8e9ace45aebd9a`
(바깥 디렉터리 이름이 부모 리니지라 헷갈린다. `git worktree list`로 매번 확인한다.)

**Gen 4의 4커밋**

```
31d3995 feat(AC8): eval_autoresume 생산자 측 구현 + 테스트 복구
43d20b6 feat(AC5): approved.jsonl 적재 + new_terms 필드 + glossary.json 불변 증명
0210142 fix(AC3): 검토 큐 레코드에 가드 위반 상세 보존 + ApprovedRecord 스키마 확장
be480aa test(AC1): expand_card_to_field_inputs DraftRecord count + route tests
```

5파일 +957 / −12. **AC8 유실은 재발하지 않았다** — `tests/test_ac8_eval_autoresume.py`(450줄)가
디스크와 git 이력 양쪽에 있다. Gen 3에서 통째로 날아갔던 바로 그 파일이다.

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
5ffeb2c feat(gold): 룰 용어 정답셋 채점기 통합            (사람)
3db2f79 merge: 게이트 수정 3건 통합                      (사람)
eacf9f5 fix: 부분 커밋으로 깨진 두 곳                     (사람)
bb83664 feat(AC6): 게이트 3 임계를 실행 중 측정 베이스라인으로 유도
28084e7 feat(AC6): 평가 진입점 — 예측 두 계열·3단 집계
d5056b9 feat(AC7): 필드→카드 합성
f6d96ae feat(AC4): 분포 기반 TM 임계 유도 및 interrupt() 배선
da7aa36 feat(AC6): 진입점 evaluate_pipeline
481916f feat(AC2): 초벌 레코드 계약·safe_wrap 외연·라우팅 실질 구현
```

Ralph 자율 산출은 `481916f`~`bb83664` 6개이고, phase2c 원본 대비 **21파일
+4,985 / −286**이다. 나머지 3개는 사람이 붙인 수정·통합이다.
현재 src 모듈 31개 / 테스트 파일 31개, 테스트 848개 통과.

**Ralph가 사양을 스스로 확장했다.** 이건 예상 밖이었다.
- AC6 문언에서 하드코딩된 "hold-out 100장"을 "파일에서 유도한다"로 고쳤다
- Seed에 없던 **AC7(필드→카드 합성)** 을 추가했다. 라우팅은 필드 단위인데 게이트는
  카드 단위라 그 사이를 잇는 단계가 없었다 — Seed를 쓴 사람이 놓친 빈틈이다
- **AC8(eval_autoresume)·AC9(verification manifest)** 도 추가했다. 즉 Ralph가 스스로
  늘린 AC는 2개가 아니라 **3개(7·8·9)** 이고, Gen 3는 AC 9개를 돌렸다(`ac_index` 0~8,
  전부 completed). 문언은 이벤트 스토어에만 남아 있다 — §2.5
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

번역가가 32항목을 라벨링했고(unaided 28 + aided 4), 별도 세션이 채점기를 만들었다.
둘 다 최신 브랜치에 통합돼 있다 — `data/rule_terms_gold.json`, `src/rule_gold_eval.py`.

**채점 전에 알아야 할 것:**
- 32항목 중 **16개가 이미 공식 용어집에 있다.** 룰 추출 경로는 이들을 `excluded_ids`로
  의도적으로 뺀다. 채점기가 성적표를 **두 장** 내는 이유다 — 룰 추출 경로(추출기가
  실제로 뽑은 것)와 납품 용어집 전체(공식+부제+룰 합본)
- **도달 불가는 5개가 아니라 1개다(2026-09-17 후속 세션 실측 정정).** 룰 경로에서
  분모 밖으로 빠지는 것은 `identity`(`below_rank_cut`) 하나뿐이다. `AP`·`killer`·`AI`·
  `link`는 그 전에 `official_excluded` 16개에 걸러지고, 납품 뷰에서는 전부 맞는다
- **`R&D`는 도달 불가가 아니다.** 토크나이저가 `\w+`로 잘라 `r d`로 만드는 것은 맞지만
  그 형태 그대로 후보에 오르고(85장), 채점기가 정답셋의 EN도 같은 토크나이저에
  통과시키므로 정상 채점된다. 표기가 손상됐다는 사실만 따로 표시한다.
  (2026-09-17 세션이 `df.get("R&D")==0`만 보고 도달 불가로 분류했던 것을 바로잡음)
- 정답셋이 **추출기 결함 2건을 이미 잡았다** — `Archives`에서 41회짜리 대신 1회짜리
  희귀 변형을 골랐고, `HQ`에 `본부 (HQ)` 병기 표기가 역어로 들어왔다
- 채점기는 양쪽 KO를 같은 형태소 정규화에 통과시킨다. `호스트된`/`호스트`,
  `런을 종료한다`/`런 종료`처럼 활용형이 달라도 맞는 것으로 센다

## 2.5 2026-09-17 후속 세션 — 실측으로 드러난 것

이 절이 이 문서에서 가장 중요하다. **테스트 850개가 전부 통과하는데도 파이프라인은
쓸 만한 초벌을 낸 적이 없었다.**

### ① 번역이 실행된 적이 없었다

`run_pipeline.py:53`의 `_StubLLM`은 프롬프트에서 TM 상위 1건의 KO를 정규식으로 꺼내
그대로 돌려준다. `--llm-model`을 주지 않으면 이것이 기본값이다. 지금까지 모든 실행이
스텁이었다.

검증: `pipeline_output.jsonl`의 165개 필드 레코드 전부가 TM 상위 1건의 `ko_text`와
**바이트 단위로 동일**하다(165/165).

그래서 phase2c 평가에 찍힌 아래 수치는 **TM 복사본을 채점한 값**이고 의미가 없다.

```
게이트 1 용어 준수율     53.3%  (기준 >= 95%)
게이트 2 기호 보존율     46.0%  (기준 100%)
게이트 3 편집거리 중앙값 0.4067 (기준 <= 0.2847)
```

**§3 결정 ③(게이트 3 임계 = TM 베이스라인 × 0.7)은 잘못되지 않았다.** 예측이
베이스라인과 같아서 정의상 못 넘은 것이지 임계가 틀린 게 아니다. 실제 모델로 돌리면
초벌이 TM과 전부 달라진다(17/17). 임계를 손대기 전에 실제 모델로 재측정한다.

실행 방법 — 자격증명은 `sds-ax-practice/.env`에 있고 모델은 아래를 쓴다:

```bash
cd <ralph 워크트리>
set -a && . "C:/Users/SDS/Desktop/sds-ax-practice/.env" && set +a
export CORPUS_ROOT="C:/Users/SDS/Desktop/netrunner-corpus/netrunner-cards-json"
export PYTHONPATH=src
python src/run_pipeline.py --cards 10 --output pipeline_real10.jsonl \
  --llm-model "global.anthropic.claude-haiku-4-5-20251001-v1:0"
```

`run_pipeline.py`의 docstring 예시(`us.anthropic...`)는 가용 목록에 없다.
`availableModelsOnBedrock.md`의 `global.anthropic.claude-haiku-4-5-20251001-v1:0`를 쓴다.

### ② 출력 계약 결함 — 고쳤다

실제 모델 첫 실행에서 **17건 중 16건이 역어에 `<card-text>` 태그를 달고 나왔고**,
2건은 한국어가 아니라 영어 논평이었다(`"the card text ... appears to be incomplete"`).

원인은 `_build_draft_prompt`다. TM 히트의 EN·KO를 각각 `safe_wrap`으로 감싸 넣어
모델이 태그 블록을 여러 개 보는데, 프롬프트가 `## Korean translation`으로만 끝나고
**출력 형식 지시가 없었다.** 모델이 본 형식을 그대로 흉내 냈다.

`translation_graph.py`의 지시 블록에 두 줄(순수 텍스트 출력·논평 금지)을 추가해
고쳤다. 재측정 결과 **태그 누출 16 → 0, 비한국어 2 → 0.** 회귀 테스트 2개를
`tests/test_ac2_draft_generation.py`에 넣었다(850 통과).

**이 결함은 스텁으로는 절대 보이지 않는다.** 스텁이 코퍼스의 깨끗한 KO를 돌려주기
때문이다. 앞으로 초벌 품질 판단은 반드시 실제 모델 실행으로 한다.

**증거는 Ralph 브랜치에 커밋돼 있다.** Bedrock을 다시 부르지 않고 확인할 수 있다:

| 파일 | 내용 |
|---|---|
| `pipeline_output.jsonl` | 스텁 실행 165건 — 전부 TM 상위 1건과 동일 |
| `pipeline_real10_before_prompt_fix.jsonl` | 실제 모델 17건 — 16건에 `<card-text>` 누출 |
| `pipeline_real10_after_prompt_fix.jsonl` | 수정 후 17건 — 누출 0 |

스텁 동일성 재확인은 `tm_hits[0]['id']`로 코퍼스의 `ko_text`를 찾아 `draft_ko`와
문자열 비교하면 된다(165/165 일치).

### ③ HITL 통과율이 0%다 — 미해결

수정 후에도 **17/17이 interrupt에 걸린다.** 트리거 분포(17건 중):

| 트리거 | 건수 |
|---|---|
| `new_term` | **15** |
| `rule_violation` | 9 |
| `term_conflict` | 7 |
| `low_tm_confidence` | 6 |

`new_term_guard`가 용어집 279개에 없는 영단어를 전부 신규 용어로 올린다. 가장 자주
잡힌 것이 **`strong`(5회) — `<strong>` 태그**이고, 나머지도 `trashed`·`played`·
`scored`·`fifteen`·`fame`·`thing`·`saw` 같은 평범한 단어다. 건당 중앙값 4개.

**그래서 모든 카드가 반드시 걸리고 자동 승인이 구조적으로 불가능하다.**
`approved.jsonl`이 한 번도 생기지 않은 이유이고, AC5(승인 적재)에 실행 증거가
없는 이유다. **어디서 끊을지는 2026-09-17에 정했다 — §3 결정 ④.**

### ④ AC8·AC9 산출물이 유실됐다

Gen 3는 AC 9개를 돌렸는데 커밋은 AC2·AC4·AC6·AC7만 남았다. 이벤트 스토어에 따르면
AC9 담당이 만든 `docs/ac_verification_manifest.md`,
`tests/test_ac9_verification_manifest.py`(42개), AC8의
`tests/test_ac8_eval_autoresume.py`(18개)는 **git 이력에도 디스크에도 없다.**

AC6 담당의 자기 보고는 `901 passed`였고 실측 브랜치는 848이었다. 차이 53이 유실분과
맞아떨어진다. §5 "체크포인트 커밋은 원자적이지 않다"의 재발인데 이번엔 AC 두 개가
통째로 날아갔다.

**AC8은 절반만 남았다.** `evaluate_pipeline.py`는 `eval_autoresume_header`를 읽을 줄
아는데 그것을 만드는 `run_pipeline` 쪽이 없다. 소비자만 있고 생산자가 없다.

### ⑤ AC3 문언과 구현이 어긋난다

AC3은 "검토 큐 레코드는 어느 가드가 왜 실패했는지를 보존한다"고 요구하는데,
`_state_to_draft_record`가 남기는 것은 `interrupted: true` 뿐이다. 어느 트리거가
발화했는지 레코드에 없다. 위 ③의 분포는 `check_hitl_triggers`를 손으로 다시 돌려
얻은 것이다.

### ⑥ Ralph가 늘린 AC의 성격 판정 (§4 작업 3의 답)

| AC | 내용 | 판정 |
|---|---|---|
| AC7 | 필드→카드 합성 | **범위 안.** AC6의 3단 집계에 빠져 있던 연결고리 |
| AC8 | `eval_autoresume` | **범위 안.** AC4(interrupt로 정지)와 AC6(hold-out 무인 실행)이 서로 모순인데 AC8이 그것을 해소한다 |
| AC9 | 검증 명령 ↔ AC 대응표, 세대 간 PASS 승계 조건 | **범위 밖.** 제품에 아무것도 더하지 않는 프로세스 AC다. ouroboros 하네스 쪽 관심사 |

사용자 결정: **AC9은 Seed에서 빼고 AC 8개로 고정한다. AC8은 복구한다.**

## 2.6 Gen 4 — 완주했으나 평가가 구조적으로 불가능했다

**이 절이 §2.5만큼 중요하다. 여기 적힌 것을 모르면 다음 세대도 똑같이 score 0.0을 받는다.**

### ① ralph 내부 평가자는 소스 정규식 스캐너다

`ouroboros` 0.54.4 실측(설치 경로 `…/uv/cache/archive-v0/qVBiREO0OQDRTyJo/Lib/site-packages/ouroboros`):

| 확인한 것 | 위치 |
|---|---|
| T3(행위)·T4(주관) assertion은 **코드 실행 없이 무조건 SKIPPED** | `verification/verifier.py:1022-1028` |
| PASS는 **집합 동등** `outcomes == {VERIFIED}` — SKIPPED가 하나라도 섞이면 NOT_EVALUATED | `mcp/server/spec_verification_adapter.py:233`, `:244` |
| `score = passed_count / total` — UNRESOLVED와 FAIL은 점수상 동일 | `:268-269` |
| 추출기 기본값이 **"if unsure, classify as t3_behavioral"** | `verification/extractor.py:65` |
| 파일 읽기 상한 **50KB** | `verifier.py:33` |
| `evolve_step` 안에서 우회 불가 — AC가 0개일 때만 건너뛴다 | `mcp/server/adapter.py:1974-1976` |

Gen 4의 AC 8개 evidence 문자열이 **전부** `"Behavioral assertion requires test execution or
semantic analysis"`로 끝난다. 즉 8개 모두 T3를 물고 있었고 **어떤 AC도 PASS가 될 수 없었다.**

**그래서 AC 문언을 이 스캐너가 좋아하는 형태로 깎지 않기로 했다.** 이벤트 스토어 7세대 실측이
그 판단을 뒷받침한다 — AC FAIL 58건 중 증거 공백 25 / 기능 실패 33이고, `spec_verifier`가 낸 FAIL
23건은 **23건 전부가 증거 공백**이며 기능 결함을 지적한 적이 한 번도 없다. non-verdict 비율은
`formal_evaluation` 경로 **0/47**, `spec_verifier` 경로 **18/23**이다. Gen2→Gen3에서 AC 문언을
전부 갈아치웠는데도 비율은 0.833 → 0.778로만 움직였다.

**대안: `formal_evaluation`은 별도 진입점이다.** `ouroboros_evaluate` 계열
(`mcp/tools/evaluate_ralph_chain.py:83,99`)이 그것을 쓴다. ralph 루프는 그대로 두고 **끝난
워크트리에 `ouroboros_evaluate`를 따로 돌려** 판정을 받는다.

### ② 인터프리터 제약은 무시된 게 아니라 인코딩이 불가능했다

Ralph 워크트리의 `.ouroboros/mechanical.toml`에 `# auto-generated by ouroboros evaluate detector`
/ `test = "uv run pytest"`가 들어 있고 평가자가 그 명령을 실행한다. **`uv run pytest`는 에이전트의
일탈이 아니라 ouroboros가 스스로 써넣은 명령이다.**

게다가 `evaluation/detector.py:497-509`가 절대경로 실행파일을 명시적으로 거부하고
(`"absolute paths ... are refused. Only bare names and ./-prefixed project-local wrappers survive"`),
`evaluation/languages.py:37-57`의 허용 목록에도 bare name만 있다. 즉 Seed에 적었던
`C:/…/.venv/Scripts/python.exe -m pytest`는 **설정에 넣어도 드롭된다.**

→ **Seed v3에서 이 제약을 뺐다.** 지킬 수 없는 제약을 남겨 두면 위반으로만 집계된다.

### ③ AC4가 폐기된 설계를 명령하고 있었다

§3 결정 ④(차단형/기록형 분리)가 구현되지 않은 이유는 "제약에 주인이 없어서"가 아니다.
**AC4 문언이 반대말을 하고 있었다** — "네 트리거(신규 EN 용어, 용어 충돌, 규칙 검증 실패,
TM 최고 유사도 임계 미만)가 그래프 노드에서 발화해 실행이 멈춘다". AC 단위로 일하는 실행자는
AC를 따랐고 그게 정상이다.

**AC 동결은 개수 동결이지 문언 동결이 아니다.** 제약 문장 자체가 "AC는 8개로 고정한다. 새 AC를
추가하지 않는다"이다. AC4·AC6 문언 수정은 동결을 깨지 않는다. Seed v3가 그렇게 했다.

### ④ 스텁으로 들어가는 문이 셋이다

`--llm-model`을 required로 만드는 것만으로는 막히지 않는다.

| 진입구 | 위치 |
|---|---|
| `run_pipeline()` 라이브러리 기본값 `llm_model=None` → `_StubLLM` | `src/run_pipeline.py:231`, `:272-278` |
| `evaluate_pipeline`의 **별도** `--llm-model`(기본 None) | `src/evaluate_pipeline.py:598`, `:647` |
| `--pipeline-output`이 임의 jsonl 수용 — **스텁 시대 파일이 저장소에 그대로 있다** | `src/evaluate_pipeline.py:566` |

### ⑤ 산출물이 자기를 증명하지 못한다

`_state_to_draft_record`의 `slim_hits`가 `tm_hits`에서 **`ko_text`를 버린다**
(`src/run_pipeline.py:183-186`). 그래서 `pipeline_output.jsonl`만으로는 에코율을 계산할 수 없다.
그런데 **Seed 온톨로지의 `draft_record`는 이미 `tm_hits(id·en_text·ko_text·score)`를 요구한다** —
구현이 Seed를 어기고 있는 것이고, 복원은 새 요구가 아니라 복구다. 헤더에 모델 id·실행 모드도 없다.

### ⑥ TM 에코 실측 — 새 게이트의 근거

| 실행 | 레코드 | TM 최상위와 바이트 동일 |
|---|---|---|
| 스텁 `pipeline_output.jsonl` | 165 | **165 (100%)** |
| 스텁 `approved.jsonl` | 215 | **215 (100%)** |
| 실모델 `pipeline_real10_before_prompt_fix.jsonl` | 17 | **0 (0%)** |
| 실모델 `pipeline_real10_after_prompt_fix.jsonl` | 17 | **0 (0%)** |

- **정당한 우연일치는 0이 아니다** — hold_out 100장 중 **1장(1.0%)** 이 TM 최상위 KO와 자기 정답 KO가
  바이트 동일하다. 완벽한 번역기도 이 split에서 1% 복사한다. 그래서 임계를 상수로 박지 않고
  `max(ECHO_FLOOR, 실행 중 측정한 우연일치율)` 관계로 쓴다(gate3의 `THRESHOLD_FACTOR`와 같은 위상).
- **바이트 동일은 적대적 게이트가 아니다** — 실모델 rule 경로 유사도 p90 0.903 / max 0.912로 근사
  에코가 이미 있고 문자 하나로 회피된다. **정직한 퇴행 카나리아**로 포지셔닝한다.
- **`ECHO_FLOOR = 0.05` 확정(2026-09-17).** 관계식은 `max(0.05, 실행 중 측정한 우연일치율)`.
  정책 하한이며 실측 표본에 적합시킨 값이 아니다. §7-8 참조.
- **중단은 분모에 영향을 주지 않는다** — 초벌 생성이 가드 검사보다 먼저라 중단된 레코드도 초벌을
  갖는다. 실측: 165건 중 164건이 중단이었으나 **빈 초벌은 0건**.
- **TM 히트 커버리지 하한은 두지 않는다** — 검색기가 구조상 항상 상위 k건을 돌려주어 커버리지가
  항상 100%다(165/165, 17/17). 판정에 쓰면 영원히 통과하는 장식이 된다.

### ⑦ 한국어 플레이버의 위치 (정정)

`v2/translations/ko/cards/*.json`에는 `flavor` 키가 **0건**이라 "한국어 플레이버가 없다"고 오판하기
쉽다. 실제로는 **`v2/translations/ko/printings/*.json`에 710건** 있고 `data/hold_out.json`도
`ko_flavor`를 100장 중 65장 보유한다. EN 쪽이 `v2/printings`에 있는 것과 같은 구조다.

### ⑧ 결정 ④는 여전히 미구현이다

`blocking_keyword`·`logged_only` 등 온톨로지 값이 `src/` 어디에도 없고,
`new_term_candidates.json` 1100건 중 두 번째 항목이 그대로 `strong`(마크업 태그명)이며
`ko_rendering`은 1100건 전부 빈 문자열이다. **Seed v3의 AC4가 이것을 요구하도록 고쳐졌다.**

## 3. 확정된 사양 결정 (재논의 불필요)

| # | 결정 |
|---|---|
| ① | **추출 경로를 둘로 분리.** 부제는 `keywords`, 룰 용어는 `text`. 정답셋 88항목은 부제 경로만 채점. 룰 경로는 수동 정답셋 32항목으로 채점 |
| ② | 룰 경로 후보 상한은 Dice 전역 순위가 아니라 **2단계** — EN 빈도 상위 N → EN별 Dice 상위 k |
| ③ | 하드 게이트 3 임계 = **TM 베이스라인 × 0.7**. 상수로 박지 않는다 |
| ④ | **신규 용어는 차단형/기록형으로 나눈다.** 차단형만 interrupt를 발화시키고 기록형은 통과시킨다 (2026-09-17 결정, 아래) |
| ⑤ | **Bedrock 모델은 폴백한다.** 1순위 global.anthropic.claude-haiku-4-5-20251001-v1:0이 쓰로틀링·미가용으로 실패하면 availableModelsOnBedrock.md의 다른 모델로 재시도한다. 스텁으로는 절대 폴백하지 않는다 (2026-09-17 결정, 아래) |

### 결정 ④ — 신규 용어 판정 경계

**차단형** (interrupt 발화) 은 둘뿐이다.

1. 카드 `en_keywords`(부제) 중 용어집 미등재 항목
2. **룰 텍스트**에서 문장 첫 단어가 아닌 위치의 대문자 시작 미등재 토큰 — 룰이 참조하는 카드 이름·고유명사

**기록형**은 나머지 전부다. 탐지 즉시 `new_term_candidates.json`에 적재하고 초벌 레코드에도 남기되
파이프라인을 멈추지 않는다. **승인 시점이 아니라 탐지 시점에 적재한다** — 지금은 승인 후에만 쌓이는데
승인이 한 번도 일어나지 않아 저장소가 비어 있다.

마크업 태그명(`strong`·`em`·`trace`)은 어느 쪽도 아니다. 토크나이저가 `<strong>`에서 `strong`을 뽑는 것은
결함이고 태그를 먼저 제거해 고친다. `new_term_guard.py:69`의 주석은 태그를 제거한다고 적어 놓았지만
실제로는 제거하지 않는다.

**판정 위치**: 가드는 탐지·분류만 하고 차단 여부는 `hitl_interrupt`가 정한다. 정책을 한 곳에 모으면
`test_new_term_guard.py`의 기존 29개 단언이 그대로 살아 있다.

#### 근거 — hold_out 100장 / 165필드 실측

불용어 목록을 늘리는 방향은 **효과가 없다**. 이미 불용어 약 150개가 들어 있고, 거기에 마크업 제거와
굴절 정규화(불용어의 활용형까지 등록으로 간주)를 더해도:

| 정책 | 필드 발화 | 카드 발화 | 남는 후보 |
|---|---|---|---|
| 현행 | 92.7% | 99.0% | 495종 |
| +마크업 제거 | 88.5% | 98.0% | 494 |
| +굴절 정규화 | 83.6% | 97.0% | 454 |

남는 것이 `cannot`·`game`·`equal`·`worth`·`different`·`least`·`bottom`이다. **"게임 용어가 아닌 영단어"는
열린 집합이라 목록으로 닫을 수 없다.** 경로별로는 룰 76.0% / 플레이버 98.5%(중앙값 5개)로 플레이버가 훨씬
심하다.

반대로 "용어다움"을 양의 신호로 **요구**하면 급격히 좁아진다.

| 차단 정의 | 필드 발화 | 카드 발화 |
|---|---|---|
| 미등재 부제만 | 0.0% | 0.0% |
| **+ 룰의 비문장초 대문자 (채택)** | **12.7%** | **21.0%** |
| + 플레이버 대문자까지 | 26.7% | 39.0% |

채택안의 차단 후보 27종은 `Searchlight`·`Changeling`·`Rebirth`·`Rolodex`·`Chimera` 등 **룰 텍스트가
참조하는 카드 이름**으로, 역어 고정이 실제로 필요한 것들이다. 부제는 phase1에서 전량 등록돼 0건이지만
새 카드가 새 부제를 들고 오면 발화한다.

**천장 주의.** 이렇게 고쳐도 자동 승인이 크게 열리지는 않는다. EN만으로 판정되는 트리거(①+②) 합집합
기준 100장 중 **49장**만 통과한다. ②`term_conflict`가 단독으로 24.8%를 먹기 때문이다(`spend` 10건,
`code gate` 9건, `sentry` 7건 …). ③`rule_violation`은 초벌 KO가 있어야 재므로 아직 미측정이다.

**플레이버 고유명사는 기록형으로 흘린다.** `gabriel`·`santiago`·`whizzard`·`haas-bioroid`·`hong kong`
같은 인물·기업·지명은 역어 일관성이 필요한 것이 맞지만, 차단할 만큼은 아니라고 판단했다. 기록형으로
쌓아 두고 배치로 검토한다.


### 결정 ⑤ — Bedrock 모델 폴백

가용 모델은 `availableModelsOnBedrock.md`에 5개 있다. 폴백 순서는 기재 순서를 따른다.

```
1  global.anthropic.claude-haiku-4-5-20251001-v1:0   (1순위)
2  us.amazon.nova-pro-v1:0
3  us.amazon.nova-2-lite-v1:0
4  global.amazon.nova-2-lite-v1:0
5  us.amazon.nova-lite-v1:0
```

쓰로틀링(`ThrottlingException`)·용량 부족·모델 미가용으로 실패하면 다음 모델로 내려간다.
재시도 전에 지수 백오프를 적용하고 재시도 횟수와 대기 시간을 산출물에 기록한다.

**금지 세 가지.**

1. 목록 밖 모델로 폴백하지 않는다.
2. **어떤 경우에도 `_StubLLM`으로 폴백하지 않는다** — 목록의 모델이 전부 실패하면 빈 예측을 남기고
   `empty_prediction_cause`에 `model_invocation_failure`로 원인을 보고한다. 스텁 출력이나
   `hold_out`의 `ko_text`로 채우지 않는다.
3. 폴백을 숨기지 않는다 — 헤더에 시도한 모델 id와 각각의 성공·실패 사유를, 각 `DraftRecord`에
   그 레코드를 실제로 생성한 모델 id를 남긴다.

**혼합 실행은 모델별로 나눠 보고한다.** Haiku와 Nova는 계열이 달라 단일 평균이 어느 쪽 품질도
대표하지 않는다. 모델별 레코드 수와 모델별 게이트 수치를 함께 내야 품질 변화가 모델 교체 탓인지
코드 변경 탓인지 구분된다.

> **정정 — `us.*`가 전부 금지인 것이 아니다.** Seed v2의 제약은 "us.* 접두 id는 이 계정에서
> 호출되지 않는다"라고 적었으나 이는 과일반화다. 실제로 없는 것은 **`us.anthropic.*`** 뿐이고
> (`run_pipeline.py` docstring의 예시가 그것이다), **`us.amazon.*` 4개는 가용 목록에 실재한다.**
> 이 문장을 그대로 두면 폴백 대상이 전부 금지되므로 v3에서 고쳤다.

## 4. 다음에 할 일

### 작업 0 — 선결 조건 ✔ 2026-09-17 완료

1. ~~프롬프트 수정을 커밋한다.~~ ✔ `1230fad`. 워크트리는 클린하다(실측 확인).
   **다만 브랜치를 워크트리가 점유한 상태는 그대로다** — 시작 전에
   `git -C <워크트리> checkout --detach`로 풀어 준다(§4 작업 1의 인용 블록).
2. ~~Seed에서 AC9을 뺀다.~~ ✔ 3~5와 함께 **`.ouroboros/seed-phase2c-v2.yaml`** 에 반영했다.
3. ~~AC8 생산자 측을 Seed에 명시한다.~~ ✔ AC8 문언에 복구 범위로 덧붙였다.
4. ~~신규 용어 판정 경계를 정한다.~~ ✔ §3 결정 ④.
5. ~~"초벌은 실제 모델로 측정한다"를 못 박는다.~~ ✔ 제약으로 들어갔다.

**Seed v2가 Gen 3의 진화 Seed에서 달라진 점** — 기반은 이벤트 스토어에서 복원한 Gen 3 Seed
(AC 9개 · 제약 39개 · 온톨로지 17필드)이고, 거기에서만 손댔다:

| 변경 | 내용 |
|---|---|
| AC 9 → **8** | AC9(검증 명령 대응표) 제거. AC1~AC8 문언은 Gen 3 그대로 |
| AC8 문언 | 생산자 측 부재와 유실된 테스트 복구를 범위에 포함한다고 덧붙임 |
| 1차 컨텍스트 | `orch_fa2a47fe46cf`(d4b6ff9) → **실제 Ralph 워크트리**(`1230fad`, 850 통과) |
| 제약 39 → **44** | 실제 모델 측정 · 신규 용어 경계 · AC 확장 중단 · 알려진 결함 3건 · 검증 환경 |
| 온톨로지 17 → **18** | `new_term_blocking_boundary` 추가 |
| 판정 모델 id | `us.anthropic…` → `global.anthropic…` (가용 목록에 us.* 없음) |

**Gen 3 Seed 전문은 이벤트 스토어에서 복원할 수 있다** — §2.5 ④가 "이벤트 스토어에만 있다"고 적은
AC7·AC8·AC9 문언뿐 아니라 Seed 전체가 남아 있다:

```python
# events.event_type='lineage.generation.started', aggregate_id=<lineage_id>
# payload의 seed_json(51KB) = 그 세대가 실제로 돌린 Seed 전문
# payload의 ac_focus.active_ac_descriptions = AC 문언 목록
```

### 작업 1 — Gen 5 착수 ★ 최우선

**Seed는 준비돼 있다 — `.ouroboros/seed-phase2c-v3.yaml`(AC 8 · 제약 50 · 온톨로지 19).**
v2에서 손댄 곳만 손댔다: AC4·AC6 문언, 제약 −1(인터프리터)/+7, 온톨로지 +1(`echo_gate_contract`),
1차 컨텍스트를 Gen 4 워크트리로 갱신. goal과 나머지 AC 6개는 그대로다.

**Seed는 완성됐다 — 미확정 상수는 없다.** `ECHO_FLOOR = 0.05`가 2026-09-17에 확정돼
AC6 문언·제약·온톨로지에 반영됐다(§7-8). 남은 것은 실행뿐이다.

**A안(새 리니지 + `seed_content`)으로 돌린다.** Gen 4가 이 방식으로 정상 동작했고, AC 8개가 실제로
적용되는 것을 `ac_index` 0~7로 확인했다. `start_ralph`의 `seed_content`는 generation 1에서만
읽히므로 기존 리니지를 이어 붙이면 새 Seed가 한 줄도 반영되지 않는다.

```
start_ralph(
  lineage_id  = "<새 리니지 id>",
  seed_content = <.ouroboros/seed-phase2c-v3.yaml 전문>,
  project_dir  = "C:\Users\SDS\.ouroboros\worktrees\ralph-6ec830bda576f952b7b2375d3c3868\ralph-c7ac33666c6a487f8e9ace45aebd9a",
  per_iteration_timeout_seconds = 7200,
  max_generations = 1,
  max_total_seconds = 7800
)
```

> **시작 전에 브랜치 점유를 푼다.** 대상 브랜치를 워크트리가 점유 중이면
> `Task branch already checked out in another worktree`로 실패한다.
> `git -C <워크트리> checkout --detach` 후 `git worktree prune`. §5 참조.

**합격 판정은 ralph 내부 score로 하지 않는다**(§2.6 ①). 다음 셋으로 한다:

1. 워크트리에서 pytest 직접 실측 — `CORPUS_ROOT` 설정 후 `.venv` 인터프리터로
2. TM 에코율 실측 — `draft_ko` vs 자기 `tm_hits[0].ko_text`, 공백 정규화 후
3. 끝난 워크트리에 `ouroboros_evaluate`를 따로 돌려 `formal_evaluation` 판정

Gen 3·Gen 4 모두 80분 안팎 걸렸다.

### 작업 2 — 룰 용어 채점 실행 ✔ 2026-09-17 완료

**첫 실측치. 다음 세대 이후 재채점해 개선/퇴행을 잰다.**

| 성적표 | EN 재현율 | KO 정확도 |
|---|---|---|
| 룰 추출 경로 | 73.3% (11/15) | 90.9% (10/11) |
| 납품 용어집 전체 | 87.1% (27/31) | 96.3% (26/27) |

못 맞힌 4개는 원인이 서로 다르다 — **2개는 추출 실패가 아니라 표제어 단위 불일치다.**

- `credit` — 용어집에 `credits`(복수)와 `credit pool`이 있다. **역어 `크레딧`은 이미
  있고** 단수 표제어만 없다. 굴절 정규화 문제
- `gain` — `gain credits`(바이그램)만 뽑혔고 단일어가 없다. n-gram 단위 문제
- `R&D` — 토크나이저가 `r d`로 만들고 그 형태조차 최종 용어집에 없다
- `heap` — 진짜 부재

KO 오답은 `HQ` 하나뿐이다(`본부 (HQ)` ← 정답 `본부`). §2가 적어 둔 `Archives` 결함은
`기록 보관소`로 정상 채점돼 해소된 것으로 보인다.

재실행 방법:

```bash
export CORPUS_ROOT="C:/Users/SDS/Desktop/netrunner-corpus/netrunner-cards-json"
"C:/Users/SDS/Desktop/sds-ax-practice/.venv/Scripts/python.exe" -m rule_gold_eval
```

§2의 "채점 전에 알아야 할 것"을 반영해 결과를 읽는다. 특히 성적표 두 장을 구분해
보고, 도달 불가 5개를 "못 맞힌 것"과 섞지 않는다.

### 작업 3 — AC9 내용 확인 ✔ 2026-09-17 완료

§2.5 ⑥ 참조. **AC9은 범위 밖으로 판정했고 Seed에서 뺀다.** AC7·AC8은 범위 안이다.
AC9 문언은 코드에 없고 이벤트 스토어에만 있다 — `ouroboros.db`의 `events.payload`에서
`verification_manifest`로 검색하면 나온다.

### 작업 4 — 브랜치 정리

`main`과 `ooo/ralph-6ec8…`는 푸시됐다(2026-09-17). 남은 로컬 브랜치 5개는 내용이
최신 브랜치에 들어 있거나 버려도 되는 것이라, 확인 후 정리한다. §1의 표 참조.

### 작업 5 — phase3 (Issue 동기화 · Pages 검수 뷰)

**Seed는 이미 있다 — `main`의 `.ouroboros/seed-phase3.yaml`(`55d694a`, AC 7개).**
이 문서가 "미작성"이라고 적어 온 것은 틀렸다(2026-09-17 후속 세션 정정).
실행만 남았다. 다만 `brownfield_context.context_references[0].path`가
`orch_a6bc6099bdae/orch_3b1f1bf54116`(HEAD `40d8dc5`)를 가리키고 있어
**Ralph 브랜치보다 한참 뒤처져 있다.** 돌리기 전에 최신 워크트리로 고친다.

## 5. 함정 모음 (직접 밟은 것들)

### ralph 내부 score는 품질 신호가 아니다 ★ 새로 밝혀진 것

`evolve_step`의 평가자는 소스 정규식 스캐너이고 행위 assertion을 전부 SKIPPED로 떨어뜨린다.
AC 하나에 SKIPPED가 섞이면 그 AC는 NOT_EVALUATED이고 점수상 FAIL과 같다. **score 0.0은 코드 품질과
무관하게 나올 수 있다.** 7세대 동안 이 경로가 기능 결함을 지적한 적은 0건이다. 상세는 §2.6 ①.
품질 판정은 pytest 실측과 별도 `ouroboros_evaluate`로 한다.

### Seed 제약에 절대경로 실행파일을 적지 말 것

`detector.py`가 절대경로를 거부해 그 명령은 드롭된다. 손으로 `mechanical.toml`에 써도 마찬가지다.
인터프리터를 고정하고 싶으면 Seed가 아니라 프로젝트 의존성·래퍼로 해결한다. §2.6 ②.

### 서브에이전트 보고를 실측 없이 인용하지 말 것

이번 세션에서 레인 보고 중 최소 3건이 틀렸다 — "한국어 코퍼스에 flavor가 0건"(실제로는
`ko/printings`에 710건), "`pipeline_output.jsonl`의 interrupted가 0건"(실제 164/165),
그리고 서로 모순되는 AC 번호. **파일을 직접 열어 확인한 것만 문서에 적는다.**

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
| phase2c | 온라인 파이프라인 본체 | 6→9 | 실행 완료, Ralph 3세대. `seed-phase2c.yaml`(AC 6개)은 Gen 1 입력이고 Gen 3의 진화본은 AC 9개다 |
| phase2c-v2 | 같은 범위, Gen 4 입력 | 8 | **실행 완료**(Gen 4). 평가 0.0 · QA 0.44 — 상세는 §2.6 — `seed-phase2c-v2.yaml`. Gen 3 진화 Seed에서 AC9 제거 + 결정 3건 반영(§4 작업 0) |
| **phase2c-v3** | 같은 범위, Gen 5 입력 | **8** | **작성 완료, 미실행** — `seed-phase2c-v3.yaml`. v2에서 AC4·AC6 문언 수정 + 제약 −1/+10 + 온톨로지 +2(모델 폴백 포함). 미확정 상수 없음 |
| phase3 | Issue 동기화 · Pages 검수 뷰 · 패턴 매핑 | 7 | **작성 완료**(`55d694a`), 미실행. context_references가 낡음 |

`main`의 `.ouroboros/`에 있는 Seed: phase1 · phase2b · phase2c · phase3 · `seed.yaml`.
**phase2a Seed만 `main`에 없고 Ralph 브랜치에 있다**(`0dfda60` → `143f1cd`에서
`context_references`를 객체로 교정). `main` 작업 디렉터리에 같은 이름의 미추적 파일이
하나 굴러다니는데 그 **교정 전 옛 판본**이다 — 아무것도 읽지 않으므로 그냥 둔다.
Seed를 찾을 때 그것을 집지 않도록 주의한다.

## 7. 아직 안 정한 것

1. 2021–2022 번역분 142장의 코퍼스 편입 여부
2. 편집거리 감소폭의 납품 이후 추적 방법·주기
3. 가드레일 통과율을 하드 게이트로 승격할지 관찰 지표로 둘지
4. **브랜치 정리 방침** — `ooo/*` 8개가 쌓였다. `ooo/ralph-c7ac3366…`으로 수렴시키고
   나머지를 정리할 시점. **Ralph 브랜치 2개가 origin에 없다** — 먼저 푸시할지도 미정
5. **AC 확장을 어디서 멈출지** — Ralph가 AC7·AC8·AC9를 스스로 추가했다.
   AC9은 범위 밖으로 판정해 뺐지만(§2.5 ⑥) 일반 기준은 아직 없다.
   온톨로지의 `ac_freeze_precondition`이 그것을 정의하려는 것으로 보이나 미확인.
   **다만 "AC 동결"의 뜻은 정해졌다 — 개수 동결이지 문언 동결이 아니다(§2.6 ③).**
6. ~~신규 용어 판정을 어디서 끊을지~~ ✔ **2026-09-17 결정 — §3 결정 ④.**
   차단형/기록형을 나누고 차단형은 미등재 부제 + 룰의 비문장초 대문자 토큰으로 한정한다.
   **Gen 4도 구현하지 않았다**(§2.6 ⑧). 원인은 AC4 문언이 반대말을 하고 있어서였고,
   Seed v3의 AC4가 이것을 요구하도록 고쳐졌다. Gen 5가 구현한다
7. **초벌 품질을 무엇으로 볼지** — 실제 모델 초벌은 읽을 만하지만 TM 상위 1건이
   엉뚱한 카드인 경우가 있다(`net_celebrity`의 TM 히트는 `paywall_implementation`).
   TM 검색 품질 자체를 지표로 둘지 미정
8. ~~`ECHO_FLOOR` 값~~ ✔ **2026-09-17 결정 — `ECHO_FLOOR = 0.05`.** 관계식은
   `max(0.05, 실행 중 측정한 정당한 우연일치율)`이다. 근거는 기계적 분리이지 표본 적합이 아니다 —
   스텁 100%는 구현상 필연, 실모델 0%도 필연, 정당한 우연일치가 hold_out 실측 1.0%이므로 5배 여유다.
   17건짜리 실행에 맞춰 고른 값이 아니다. 성격은 gate1의 0.95·gate2의 1.0과 같은 **정책 하한**이라
   "측정값을 상수로 박지 않는다"는 원칙에 어긋나지 않는다
9. **TM 검색 품질 자체** — `tm_confidence` 점수가 전 레코드에서 0.023~0.033이라는 극히
   좁은 띠에 뭉쳐 있다. 이 분포로 유도한 p20 임계는 사실상 임의 분할에 가깝다.
   TM 융합 설계를 다시 볼지, 아니면 임계 유도 방식을 바꿀지 미정

### 알려진 사소한 결함 (미수정)

- `glossary.json`의 `counts.official`이 **160**인데 실제 항목은 **154**개다.
  `OfficialGlossary.__len__`이 카테고리별 합을 세고 `all_terms()`는 평탄화하며 중복
  id 6개를 합친다(`src/load_official_glossary.py:46-54`). 보고 숫자만 어긋난다
- `obs_llm_judge`에 청크·체크포인트·프롬프트 지문·dropped 집계가 없다.
  `src/term_judge.py`가 그 규율의 기존 구현이다
