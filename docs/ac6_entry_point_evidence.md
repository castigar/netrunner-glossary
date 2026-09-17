# AC6 — 평가 진입점 배선 증거

**주장:** `src/evaluate_pipeline.py`가 data/hold_out.json 전량에 대한 판정 진입점이며,
두 예측 계열을 이름으로 구분하고, 하드 게이트 3개 + TM 에코 게이트를 pipeline_predictions로
채점하며, 게이트 3의 임계를 같은 실행에서 측정한 tm_only_baseline × 0.7로 유도하고,
실행 모드가 real이 아니면 어떤 게이트 판정도 내지 않는다.

## 재현 명령

```
# 1) 배선 검증 (호출부의 실재 + 제거 시 실패)
uv run pytest tests/test_ac6_entry_point.py tests/test_ac6_run_provenance.py -q

# 2) 실제 모델 실행 — hold_out 전량 (장수는 파일에서 유도)
uv run python src/run_pipeline.py --cards 100 \
  --llm-model global.anthropic.claude-haiku-4-5-20251001-v1:0 \
  --eval-autoresume --output pipeline_output.jsonl

# 3) 판정 보고
uv run python src/evaluate_pipeline.py --pipeline-output pipeline_output.jsonl --skip-llm-judge

# 4) 2+3 한 번에
uv run python src/evaluate_pipeline.py --run-pipeline --skip-llm-judge \
  --llm-model global.anthropic.claude-haiku-4-5-20251001-v1:0
```

전체 보고 원문은 `docs/ac6_real_run_report.txt`에 있다.

## 호출부

| AC 요구 | 호출부 |
|---|---|
| 게이트 1 채점 (predictions 확장 인자) | `evaluate_pipeline.run_evaluation` → `gate1_term_compliance.score_hold_out(..., predictions=predictions)` |
| 게이트 2 채점 (predictions 확장 인자) | `evaluate_pipeline.run_evaluation` → `gate2_symbol_preservation.score_hold_out(..., predictions=predictions)` |
| 게이트 3 채점 (유도 임계 확장 인자) | `evaluate_pipeline.run_evaluation` → `gate3_edit_distance.score_hold_out(..., threshold=gate3_derived_threshold)` |
| 임계 유도 | `gate3_edit_distance.derive_threshold(tm_baseline_median)` = baseline × `THRESHOLD_FACTOR`(0.70) |
| 최종 단일 판정 | `gate_verdict.combine(g1, g2, g3)` + `EvaluationReport.final_passed`(에코 게이트 결합) |
| TM 에코 게이트 | `evaluate_pipeline.score_echo_gate(field_records, hold_out_cards)` |
| 실행 출처 | `evaluate_pipeline._read_run_header` → `RunProvenance` |
| 모델별 게이트 수치 | `evaluate_pipeline.per_model_gate_numbers(verdict, field_records)` |
| 필드→카드 합성 | `field_card_synthesis.synthesize_field_to_card` (`_load_pipeline_output`) |
| 관찰 지표 | `obs_llm_judge.score_hold_out` (실패 시 `EvaluationReport.obs_error`로 노출) |

## 두 예측 계열

`EvaluationReport`가 두 계열을 별도 필드로 보유한다 — 라벨 문자열이 아니라 구조로 구분된다.

- `pipeline_predictions` — 게이트 1·2·3의 채점 대상 (`gate_scoring_subject`)
- `tm_only_baseline_predictions` / `tm_baseline_median` — 게이트 3 임계 유도 전용
- `gate3_threshold_provenance` — 베이스라인 출처. 진입점 실행에서는 `measured_in_run`

## 스텁 진입구 봉쇄 (진입구 3곳)

스텁 실행은 탐지 대상이 아니라 발생 불가여야 하므로 진입구 세 곳을 전부 막았다.

| 진입구 | 봉쇄 | 검증 |
|---|---|---|
| (a) `run_pipeline.run_pipeline()` 함수 직접 호출 | `llm_model`에 기본값이 없다 — 호출자가 명시해야 한다 | `TestStubPrevention::test_run_pipeline_no_default_llm_model` |
| (a') `run_pipeline.py` CLI | `--llm-model` 또는 `--allow-stub` 중 하나가 필수 | `TestRunPipelineCLIStubOptIn` 2건 |
| (b) `evaluate_pipeline --run-pipeline` | `--llm-model` 없으면 `parser.error` | `TestStubPrevention::test_run_pipeline_mode_requires_llm_model` |
| (c) `evaluate_pipeline --pipeline-output <임의 jsonl>` | 헤더의 `run_mode`가 `real`이 아니면 채점 전에 거부. `--allow-stub-output`로만 열람 가능하며 그때도 판정은 내지 않는다 | `TestStubOutputCannotBeScored` 4건 |

(c)가 닫은 구체적 회귀: 저장소에 남아 있던 Gen 4의 스텁 시대 `pipeline_output.jsonl`이
플래그 없이 전체 게이트 수치를 재현했다. 이번 세대에서 그 파일은 실제 모델 실행 산출물로
교체됐고, 헤더가 없는 파일은 `run_mode=unknown`으로 읽혀 거부된다
(`test_headerless_output_is_refused_without_explicit_flag`).

`_StubLLM`은 어떤 경우에도 폴백 경로가 아니다 — 가용 모델이 전부 실패하면
`_FallbackLLM.invoke`가 `RuntimeError`를 올리고, 그 필드는 `draft_ko=""`와
`empty_cause="model_invocation_failure"`를 가진 빈 예측으로 남는다
(`TestFallbackChain::test_all_models_failing_raises_instead_of_degrading_to_the_stub`).

## 모델 폴백과 혼합 실행

`run_pipeline.FALLBACK_MODELS`는 availableModelsOnBedrock.md 기재 순서를 따른다:
haiku-4-5 → nova-pro → nova-2-lite(us) → nova-2-lite(global) → nova-lite.
쓰로틀링·용량 부족은 지수 백오프로 같은 모델을 3회까지 재시도한 뒤 다음 모델로 내려가고,
비재시도성 오류는 곧바로 다음 모델로 넘어간다.

산출물 헤더에 `run_mode`, `primary_model_id`, `models_used`, `model_success_counts`,
`model_attempts_log`(실패만), `mixed_run`, `model_failure_count`가 남고,
각 DraftRecord에 그 레코드를 실제로 생성한 `model_id`가 남는다.
둘 이상의 모델이 쓰이면 보고가 혼합 실행임을 명시하고 모델별 레코드 수와
모델별 게이트 수치(게이트1·2·3·에코)를 함께 낸다 — `TestMixedRunReporting` 3건.

## TM 에코 게이트

세는 규칙은 제약이 고정한 것을 따른다.

- 피연산자: `draft_ko` vs **그 레코드 자신의** `tm_hits[0].ko_text`
- 분모: `tm_hits`가 1건 이상이고 `draft_ko`가 비어 있지 않은 레코드 전체.
  `interrupted` 여부는 분모에 영향을 주지 않는다
  (`test_echo_gate_denominator_ignores_the_interrupted_flag`)
- 비교 전 저장소 공통의 공백 정규화 적용
- 임계 = `max(ECHO_FLOOR, 우연일치율)`. `ECHO_FLOOR = 0.05`는 gate1의 0.95·gate2의 1.0과
  같은 위상의 정책 상수이고, 우연일치율은 `measured_in_run`이다
- TM 히트 커버리지 하한은 두지 않는다 — 검색기가 항상 상위 k건을 돌려주어
  판정에 쓰면 영원히 통과하는 장식이 된다. 수치는 기록만 한다

이 게이트는 적대적 방어 장치가 아니라 프롬프트·검색 회귀 카나리아다.
문자 하나로 회피되며, 실모델 rule 경로 유사도 p90이 이미 0.90이다.
스텁 방어는 위 진입구 봉쇄가 담당하고 이 게이트는 정직한 퇴행을 잡는다.

## 제거 시 실패 (fail-if-removed)

- `gate3_edit_distance.score_hold_out`에서 `threshold` 인자를 무시하고 모듈 상수
  `THRESHOLD`를 쓰도록 되돌리면
  `TestGate3DerivedThresholdDecidesVerdict::test_gate3_passes_under_derived_threshold_while_failing_module_constant`
  가 실패한다. 두 테스트가 같은 예측에 대해 측정 베이스라인만 바꿔 합격/불합격을 뒤집으므로
  상수 임계 구현으로는 동시에 만족할 수 없다.
- `--run-pipeline` 모드의 장수는
  `TestRunPipelineModeCoversWholeHoldOut::test_run_pipeline_receives_full_hold_out_size`가
  스텁으로 가로채 `len(hold_out)`와 같은지 단언한다 — 100 같은 상수를 박으면 실패한다.
- 헤더의 `run_mode` 검사를 제거하면 `TestStubOutputCannotBeScored` 2건이 실패한다.
- 에코 게이트를 최종 판정에서 빼면
  `TestFinalVerdictCombinesEchoGate::test_echo_gate_failure_makes_the_final_verdict_fail`이
  실패한다(세 하드 게이트가 아니라 에코 게이트만으로 불합격이 결정되는 경우를 단언한다).

## 실측 결과 — 실제 Bedrock 모델 실행

```
실행 모드:  real
1순위 모델: global.anthropic.claude-haiku-4-5-20251001-v1:0
사용 모델:  global.anthropic.claude-haiku-4-5-20251001-v1:0 (단일 모델, 혼합 실행 아님)
모델 성공:  165건 / 실패 0건 / 폴백 0회
대상:       hold_out.json 100장 → 필드 레코드 165건 (text 100 + flavor 65)

게이트 1 (용어 준수율):     미달  63.1%  (기준 >= 95%)
게이트 2 (기호 보존율):     미달  82.0%  (기준 100%)
게이트 3 (편집거리 중앙값): 통과  0.1771 (기준 <= 0.2847)
TM 에코 게이트:             통과  1/145 = 0.007 (임계 0.050)

최종 판정(하드 게이트 3 + TM 에코 게이트): 불합격 — 미달 게이트 1, 2

tm_only_baseline 중앙 편집거리: 0.4067
  → 유도 임계 0.4067 × 0.70 = 0.2847 (provenance: measured_in_run)
용어집 LLM 판정 상태: 완료 (llm_judged=true)

TM 에코 게이트 상세
  ECHO_FLOOR(정책 상수): 0.05
  우연일치율(measured_in_run, TM top-1 == 참조 KO): 1/145 = 0.007
  유도 임계 = max(0.05, 0.007) = 0.050
  분모 n = 145, 실측 에코 1건, 실측 비율 0.007

빈 예측 원인별 건수 (필드 단위)
  tm_search_failure               0건
  guard_rejected_in_review_queue  0건
  approval_incomplete            20건
  model_invocation_failure        0건
  합계 20건 — 정답 ko_text 대체 없음

3단 집계
  1단 필드별: 준수 145건 / 위반 20건 (총 165개 (카드 id, 필드) 쌍)
  2단 카드별: 준수  80장 / 위반 20장 (총 100장, 80.0%) — 모든 필드 준수일 때만 카드 준수
  3단 게이트별: 게이트1 63.1% / 게이트2 82.0% / 게이트3 중앙 0.1771
  4단 combine: 불합격
```

수치는 측정 결과이며 게이트를 통과시키기 위해 정답(ko_text)을 예측으로 되돌려 넣지 않았다.

스텁 시대 수치와의 대비가 실모델 실행의 의미를 보여준다 — 스텁 실행에서는
게이트 3 중앙값이 베이스라인과 같은 0.4067이었다(초벌이 TM 상위 1건의 복사본이었으므로 당연하다).
실모델 실행에서는 0.1771로 내려가 유도 임계 0.2847을 통과하고, 에코율도 0.7%에 그친다.
같은 채점기로 잰 두 수치의 차이가 곧 스텁 실행 수치를 근거로 쓸 수 없는 이유다.
