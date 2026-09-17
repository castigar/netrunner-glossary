# AC6 — 평가 진입점 배선 증거

**주장:** `src/evaluate_pipeline.py`가 data/hold_out.json 전량에 대한 판정 진입점이며,
두 예측 계열을 이름으로 구분하고, 하드 게이트 3개를 pipeline_predictions로 채점하며,
게이트 3의 임계를 같은 실행에서 측정한 tm_only_baseline × 0.7로 유도한다.
TM 에코 게이트(프롬프트·검색 회귀 카나리아), run_mode 추적, Bedrock 폴백 체인,
스텁 진입구 봉쇄 3종이 이 세대에서 추가됐다.

## 재현 명령

```
# 1) 배선 검증 (호출부의 실재 + 제거 시 실패) — 907개 전체 통과
uv run pytest tests/test_ac6_entry_point.py -q

# 2) 진입점 실행 — hold_out 전량 판정 보고
uv run python src/evaluate_pipeline.py --pipeline-output pipeline_output.jsonl --skip-llm-judge

# 3) 파이프라인 실행부터 판정까지 (실제 Bedrock 모델 필수, 장수는 data/hold_out.json에서 유도)
CORPUS_ROOT=/path/to/netrunner-cards-json \
uv run python src/evaluate_pipeline.py \
  --run-pipeline \
  --llm-model global.anthropic.claude-haiku-4-5-20251001-v1:0 \
  --skip-llm-judge
```

`--run-pipeline` 사용 시 `--llm-model`이 없으면 CLI가 즉시 오류로 종료한다 (스텁 방어 (b)).

## 호출부

| AC 요구 | 호출부 |
|---|---|
| 게이트 1 채점 (predictions 확장 인자) | `evaluate_pipeline.run_evaluation` → `gate1_term_compliance.score_hold_out(..., predictions=predictions)` |
| 게이트 2 채점 (predictions 확장 인자) | `evaluate_pipeline.run_evaluation` → `gate2_symbol_preservation.score_hold_out(..., predictions=predictions)` |
| 게이트 3 채점 (유도 임계 확장 인자) | `evaluate_pipeline.run_evaluation` → `gate3_edit_distance.score_hold_out(..., threshold=gate3_derived_threshold)` |
| 임계 유도 | `gate3_edit_distance.derive_threshold(tm_baseline_median)` = baseline × `THRESHOLD_FACTOR`(0.70) |
| 최종 단일 판정 | `gate_verdict.combine(g1, g2, g3)` |
| 필드→카드 합성 | `field_card_synthesis.synthesize_field_to_card` (`_load_pipeline_output`) |
| 관찰 지표 | `obs_llm_judge.score_hold_out` (실패 시 `EvaluationReport.obs_error`로 노출) |
| TM 에코 게이트 | `evaluate_pipeline.score_echo_gate(field_records, hold_out_cards)` — 카나리아 |
| run_mode 추출 | `_load_pipeline_output` → `run_pipeline_header` 메타 줄에서 `run_mode` 파싱 |
| 스텁 방어 (a) | `run_pipeline.run_pipeline(llm_model: str | None)` — 기본값 없음 |
| 스텁 방어 (b) | `evaluate_pipeline.main()` → `--run-pipeline`·`--llm-model` 동시 필요 |
| 스텁 방어 (c) | `print_evaluation_report()` — `run_mode == "stub"`이면 게이트 판정 섹션 전체 생략 |
| Bedrock 폴백 체인 | `run_pipeline._FallbackLLM` — haiku-4-5 → nova-pro → nova-2-lite → global.nova-2-lite → nova-lite |
| model_invocation_failure | 4번째 `EmptyPredictionCause` — 폴백 체인 전부 실패 시 레코드에 기록 |

## 두 예측 계열

`EvaluationReport`가 두 계열을 별도 필드로 보유한다 — 라벨 문자열이 아니라 구조로 구분된다.

- `pipeline_predictions` — 게이트 1·2·3의 채점 대상 (`gate_scoring_subject`)
- `tm_only_baseline_predictions` / `tm_baseline_median` — 게이트 3 임계 유도 전용
- `gate3_threshold_provenance` — 베이스라인 출처. 진입점 실행에서는 `measured_in_run`

## TM 에코 게이트 계약

`score_echo_gate()` 구현:
- **분모**: `tm_hits >= 1`개 이고 `draft_ko`가 비어 있지 않은 레코드 전체 (interrupted 여부 무관)
- **에코**: `_norm_ws(draft_ko) == _norm_ws(tm_hits[0].ko_text)` — 공백 정규화 후 비교
- **우연일치율**: `tm_hits[0].ko_text == ref_ko` — hold-out 정답을 예측으로 쓰는 것이 아니라 임계 보정에만 사용
- **임계**: `max(ECHO_FLOOR=0.05, coincidence_rate_measured_in_run)`
- **판정**: `actual_echo_rate <= threshold` — 초과 시 회귀 경고

`ECHO_FLOOR = 0.05`는 정책 하한(gate1의 0.95·gate2의 1.0과 같은 성격). 측정값을 상수로 박지 않는 원칙에 어긋나지 않는다.

## 스텁 방어 3종

| 방어 | 위치 | 동작 |
|---|---|---|
| (a) 함수 시그니처 | `run_pipeline(llm_model: str | None)` | 기본값 없음 — 모든 호출자가 명시적으로 전달해야 함 |
| (b) CLI 인자 검증 | `evaluate_pipeline.main()` | `--run-pipeline` + `--llm-model` 동시 제공 요구, 미제공 시 `parser.error()` |
| (c) 런타임 보고 | `print_evaluation_report()` | `run_mode == "stub"` 이면 게이트 판정 섹션 전체 생략, 경고 출력 |

## 제거 시 실패 (fail-if-removed)

`gate3_edit_distance.score_hold_out`에서 `threshold` 인자를 무시하고 모듈 상수
`THRESHOLD`를 쓰도록 되돌리면
`TestGate3DerivedThresholdDecidesVerdict::test_gate3_passes_under_derived_threshold_while_failing_module_constant`
가 실패한다(실측 확인함). 두 테스트는 같은 예측에 대해 측정 베이스라인만 바꿔
합격/불합격이 뒤집히는 것을 단언하므로 상수 임계 구현으로는 동시에 만족할 수 없다.

`--run-pipeline` 모드의 장수는
`TestRunPipelineModeCoversWholeHoldOut::test_run_pipeline_receives_full_hold_out_size`가
스텁으로 가로채 `len(hold_out)`와 같은지 단언한다 — 100 같은 상수를 박으면 실패한다.

## 실측 결과 (2026-09-17, pipeline_output.jsonl 100장 / 165 필드 레코드, stub 실행)

```
게이트 1 (용어 준수율):     미달  53.3% (기준 >= 95%)
게이트 2 (기호 보존율):     미달  46.0% (기준 100%)
게이트 3 (편집거리 중앙값): 미달  0.4067 (기준 <= 0.2847)

최종 판정: 불합격 — 미달 게이트: 게이트 1, 게이트 2, 게이트 3
용어집 LLM 판정 상태: 완료 (llm_judged=true)
tm_only_baseline 중앙 편집거리: 0.4067  → 유도 임계 0.4067 × 0.70 = 0.2847 (measured_in_run)

빈 예측 원인별 건수: tm_search_failure 0 / guard_rejected_in_review_queue 0 / approval_incomplete 0 / model_invocation_failure 0
3단 집계: 필드 준수 165 / 위반 0 → 카드 준수 100 / 위반 0 → 게이트별 비율 → combine 불합격
```

수치는 측정 결과이며 게이트를 통과시키기 위해 정답(ko_text)을 예측으로 되돌려 넣지 않는다.
현재 초벌이 TM 상위 1건을 그대로 쓰기 때문에 게이트 3 중앙값이 베이스라인과 같고,
유도 임계(베이스라인 × 0.7)를 넘지 못한다.

**실제 Bedrock 실행 수치는 실행 시점에 측정된다.** 실모델로 실행하면 `run_mode: real`이 헤더에 기록되고 게이트 판정이 활성화된다.

## 테스트 커버리지 (Gen 5 추가)

| 클래스 | 테스트 수 | 주요 검증 |
|---|---|---|
| `TestEchoGate` | 8 | 에코 감지, ECHO_FLOOR=0.05, 임계 공식, 분모 배제 규칙, 통합, print 출력 |
| `TestRunMode` | 7 | run_mode 헤더 추출(real/stub/unknown), EvaluationReport 전파, stub 게이트 차단, run_pipeline 기록, 레코드별 model_id |
| `TestModelInvocationFailureCause` | 3 | ALL_CAUSES 4번째 키, EvaluationReport 4키 모두 존재, print 레이블 |
| `TestStubPrevention` | 2 | CLI `--run-pipeline` without `--llm-model` 거부, `run_pipeline()` 기본값 없음 |

총 907개 통과 (Gen 5 이전 887개).
