"""test_gate2_symbol_preservation.py — Hard Gate 2 symbol preservation scorer.

AC: hold-out 100장 기준 게임 기호 보존율 100%, 정규화 대응표 기반 자동 채점.

Key normalization rules under test:
  - Trace[N] (EN) ≡ <trace>추적 N</trace> (KO) — equivalence rule
  - <strong> <em> <ul> <li> excluded from gate
  - Standard [symbol] tokens preserved 1:1
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gate2_symbol_preservation import (
    CardSymbolResult,
    Gate2Result,
    THRESHOLD,
    _canonical_symbols_en,
    _canonical_symbols_ko,
    _check_card,
    score_hold_out,
    score_reference,
)

DATA_HOLD_OUT = Path(__file__).parent.parent / "data" / "hold_out.json"


# ---------------------------------------------------------------------------
# _canonical_symbols_en
# ---------------------------------------------------------------------------


def test_en_extracts_credit_symbol():
    assert _canonical_symbols_en("[credit]") == {"credit": 1}


def test_en_extracts_multiple_same_symbol():
    text = "[subroutine] End the run.\n[subroutine] End the run."
    assert _canonical_symbols_en(text)["subroutine"] == 2


def test_en_normalizes_trace_numeric():
    syms = _canonical_symbols_en("Trace[3].")
    assert syms == {"trace_3": 1}


def test_en_normalizes_trace_variable():
    syms = _canonical_symbols_en("Trace[X].")
    assert syms == {"trace_X": 1}


def test_en_extracts_credit_with_cost_prefix():
    # The cost "3" is NOT inside brackets — only [credit] is extracted
    syms = _canonical_symbols_en("Gain 3[credit].")
    assert "3" not in syms
    assert syms["credit"] == 1


def test_en_excludes_strong_tag():
    syms = _canonical_symbols_en("<strong>Rez</strong> this card.")
    assert "strong" not in syms


def test_en_excludes_em_tag():
    syms = _canonical_symbols_en("<em>(Flavour text.)</em>")
    assert "em" not in syms


def test_en_trace_does_not_double_extract():
    # Trace[3] must not also produce a [3] symbol entry
    syms = _canonical_symbols_en("Trace[3].")
    assert "3" not in syms
    assert syms.get("trace_3") == 1


def test_en_multiple_symbols():
    text = "[subroutine] Trace[2]. If successful, give the Runner 1 tag.\n[subroutine] Trace[2]. If successful, give the Runner 1 tag."
    syms = _canonical_symbols_en(text)
    assert syms["subroutine"] == 2
    assert syms["trace_2"] == 2


def test_en_recurring_credit():
    syms = _canonical_symbols_en("2[recurring-credit]")
    assert syms["recurring-credit"] == 1


def test_en_interrupt_symbol():
    syms = _canonical_symbols_en("[interrupt] → Prevent 1 net damage.")
    assert syms["interrupt"] == 1


def test_en_mu_symbol():
    syms = _canonical_symbols_en("+1[mu]")
    assert syms["mu"] == 1


def test_en_click_symbol():
    syms = _canonical_symbols_en("[click]: Draw 1 card.")
    assert syms["click"] == 1


def test_en_empty_text():
    assert _canonical_symbols_en("") == {}


def test_en_no_symbols():
    syms = _canonical_symbols_en("Make a run on HQ.")
    assert dict(syms) == {}


# ---------------------------------------------------------------------------
# _canonical_symbols_ko
# ---------------------------------------------------------------------------


def test_ko_extracts_credit_with_cost_prefix():
    syms = _canonical_symbols_ko("3[credit]를 얻는다.")
    assert syms["credit"] == 1


def test_ko_normalizes_trace_numeric():
    syms = _canonical_symbols_ko("<trace>추적 3</trace>")
    assert syms == {"trace_3": 1}


def test_ko_normalizes_trace_variable():
    syms = _canonical_symbols_ko("<trace>추적 X</trace>")
    assert syms == {"trace_X": 1}


def test_ko_extracts_subroutine_symbol():
    syms = _canonical_symbols_ko("[subroutine] 런을 종료한다.")
    assert syms["subroutine"] == 1


def test_ko_excludes_strong_tag():
    syms = _canonical_symbols_ko("<strong>카드</strong>를 폐기한다.")
    assert "strong" not in syms


def test_ko_trace_does_not_double_extract():
    syms = _canonical_symbols_ko("<trace>추적 3</trace>")
    assert "3" not in syms
    assert syms.get("trace_3") == 1


def test_ko_multiple_traces():
    text = "[subroutine] <trace>추적 X</trace> 성공하면, 태그.\n[subroutine] <trace>추적 X</trace> 성공하면, 태그."
    syms = _canonical_symbols_ko(text)
    assert syms["subroutine"] == 2
    assert syms["trace_X"] == 2


def test_ko_empty_text():
    assert _canonical_symbols_ko("") == {}


# ---------------------------------------------------------------------------
# _check_card — equivalence rules and violation detection
# ---------------------------------------------------------------------------


def test_check_trace_equivalence_passes():
    result = _check_card(
        "test",
        en_text="Trace[3]. If successful, do something.",
        ko_text="<trace>추적 3</trace> 성공하면, 무언가를 한다.",
    )
    assert result.passed is True
    assert result.missing == {}
    assert result.extra == {}


def test_check_trace_variable_equivalence_passes():
    result = _check_card(
        "searchlight",
        en_text="[subroutine]Trace[X]. If successful, give the Runner 1 tag.\n[subroutine]Trace[X]. If successful, give the Runner 1 tag.",
        ko_text="[subroutine] <trace>추적 X</trace> 성공하면, 러너에게 태그 1개를 준다.\n[subroutine] <trace>추적 X</trace> 성공하면, 러너에게 태그 1개를 준다.",
    )
    assert result.passed is True


def test_check_missing_symbol_fails():
    result = _check_card(
        "test",
        en_text="[subroutine] End the run.\n[subroutine] End the run.",
        ko_text="[subroutine] 런을 종료한다.",
    )
    assert result.passed is False
    assert result.missing.get("subroutine") == 1


def test_check_extra_symbol_fails():
    result = _check_card(
        "test",
        en_text="Pay [credit] to rez.",
        ko_text="[credit]을 지불해 레즈한다. [credit]",
    )
    assert result.passed is False
    assert result.extra.get("credit") == 1


def test_check_perfect_credit_preservation_passes():
    result = _check_card(
        "restructure",
        en_text="Gain 15[credit].",
        ko_text="15[credit]를 얻는다.",
    )
    assert result.passed is True


def test_check_no_symbols_passes():
    result = _check_card(
        "test",
        en_text="When this card is installed, draw 1 card.",
        ko_text="이 카드가 설치될 때, 카드 1장을 뽑는다.",
    )
    assert result.passed is True


def test_check_markup_excluded_from_gate():
    # <strong> wraps a [credit] symbol in EN; KO strips <strong> but keeps [credit]
    result = _check_card(
        "test",
        en_text="<strong>1[credit]:</strong> Do something.",
        ko_text="1[credit]: 무언가를 한다.",
    )
    assert result.passed is True


def test_check_two_subroutines_two_traces_pass():
    result = _check_card(
        "taurus",
        en_text="[subroutine] Trace[2]. If successful, trash 1 piece of hardware. If your trace strength is 5 or greater, trash 1 piece of hardware.",
        ko_text="[subroutine] <trace>추적 2</trace> 성공하면, 하드웨어 하나를 폐기한다. 당신의 추적 힘이 5 이상이라면, 하드웨어 하나를 폐기한다.",
    )
    assert result.passed is True


def test_check_interrupt_preserved_passes():
    result = _check_card(
        "test",
        en_text="[interrupt] → Prevent 1 meat damage.",
        ko_text="[interrupt] → 육체 피해 1을 방지한다.",
    )
    assert result.passed is True


def test_check_interrupt_missing_fails():
    result = _check_card(
        "muresh_bodysuit",
        en_text="[interrupt] → The first time each turn you would suffer meat damage, prevent 1 meat damage.",
        ko_text="매 차례 첫 번째 육체 피해를 방지한다.",
    )
    assert result.passed is False
    assert result.missing.get("interrupt") == 1


def test_check_result_has_card_id():
    result = _check_card("my_card", en_text="Run.", ko_text="런.")
    assert result.card_id == "my_card"


def test_check_result_is_card_symbol_result():
    result = _check_card("c1", en_text="Run.", ko_text="런.")
    assert isinstance(result, CardSymbolResult)


def test_check_click_preserved():
    result = _check_card(
        "same_old_thing",
        en_text="[click], [click], [trash]: Play an event from your heap.",
        ko_text="[click], [click], [trash]: 힙으로부터 이벤트 하나를 플레이한다.",
    )
    assert result.passed is True


def test_check_recurring_credit_preserved():
    result = _check_card(
        "net_celebrity",
        en_text="1[recurring-credit]\nUse this credit during a run.",
        ko_text="1[recurring-credit]\n런 중에만 이 크레딧을 사용한다.",
    )
    assert result.passed is True


# ---------------------------------------------------------------------------
# score_hold_out
# ---------------------------------------------------------------------------


def test_score_returns_gate2_result(tmp_path):
    cards = [{"id": "c1", "en_text": "Gain 3[credit].", "ko_text": "3[credit]를 얻는다."}]
    p = tmp_path / "hold_out.json"
    p.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    result = score_hold_out(p)
    assert isinstance(result, Gate2Result)


def test_score_all_pass_is_true(tmp_path):
    cards = [
        {"id": "c1", "en_text": "[click]: Draw 1 card.", "ko_text": "[click]: 카드 1장을 뽑는다."},
        {"id": "c2", "en_text": "Gain 15[credit].", "ko_text": "15[credit]를 얻는다."},
    ]
    p = tmp_path / "hold_out.json"
    p.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    result = score_hold_out(p)
    assert result.passed is True
    assert result.preservation_rate == 1.0
    assert result.cards_passed == 2


def test_score_one_failure_means_failed(tmp_path):
    cards = [
        {"id": "c1", "en_text": "[subroutine] End the run.", "ko_text": "런을 종료한다."},
    ]
    p = tmp_path / "hold_out.json"
    p.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    result = score_hold_out(p)
    assert result.passed is False
    assert result.preservation_rate < 1.0
    assert result.cards_passed == 0


def test_score_partial_failure(tmp_path):
    cards = [
        {"id": "ok", "en_text": "Gain 3[credit].", "ko_text": "3[credit]를 얻는다."},
        {"id": "bad", "en_text": "[interrupt] → Prevent damage.", "ko_text": "피해를 방지한다."},
    ]
    p = tmp_path / "hold_out.json"
    p.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    result = score_hold_out(p)
    assert result.passed is False
    assert result.cards_passed == 1
    assert result.cards_total == 2


def test_score_card_results_count(tmp_path):
    cards = [{"id": f"c{i}", "en_text": "Run.", "ko_text": "런."} for i in range(7)]
    p = tmp_path / "hold_out.json"
    p.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    result = score_hold_out(p)
    assert len(result.card_results) == 7
    assert result.cards_total == 7


def test_score_threshold_is_one():
    assert THRESHOLD == 1.0


def test_score_trace_equivalence_integration(tmp_path):
    cards = [
        {
            "id": "snatch_and_grab",
            "en_text": "Trace[3]. If successful, trash 1 <strong>connection</strong>. The Runner can take 1 tag to prevent this.",
            "ko_text": "<trace>추적 3</trace> 성공하면, <strong>연결</strong> 하나를 폐기한다. 러너는 이를 방지하기 위해 태그 1개를 받을 수 있다.",
        }
    ]
    p = tmp_path / "hold_out.json"
    p.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    result = score_hold_out(p)
    assert result.passed is True


def test_score_empty_hold_out(tmp_path):
    p = tmp_path / "hold_out.json"
    p.write_text("[]", encoding="utf-8")
    result = score_hold_out(p)
    assert result.passed is True
    assert result.preservation_rate == 1.0
    assert result.cards_total == 0


# ---------------------------------------------------------------------------
# Integration: real hold-out
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not DATA_HOLD_OUT.exists(),
    reason="data/hold_out.json not present",
)
def test_real_hold_out_gate2_runs_and_returns_result():
    """Hard Gate 2 scorer runs on real hold-out and returns a valid Gate2Result.

    The gate mechanism is correct when it:
      - produces a Gate2Result without error
      - returns a preservation_rate in [0, 1]
      - sets passed=True iff preservation_rate == 1.0

    Note: existing hold-out translations may have legitimate violations such as
    [interrupt] absent from some KO texts. The gate is designed to score AGENT
    output; the reference translations serve as gold standard for gate 3
    (edit distance), not gate 2.
    """
    result = score_reference(DATA_HOLD_OUT)
    assert isinstance(result, Gate2Result)
    assert result.cards_total == 100

    # The official KO translations drop [interrupt] on these three cards, so the
    # reference itself cannot reach 100%.  Asserting the known shortfall is what
    # makes this test able to fail: the previous version restated
    # `passed == (rate >= THRESHOLD)`, which the implementation defines to be true.
    failed_ids = {cr.card_id for cr in result.card_results if not cr.passed}
    assert {"muresh_bodysuit", "sacrificial_construct", "disrupter"} <= failed_ids
    assert result.preservation_rate < 1.0
    assert result.passed is False


@pytest.mark.skipif(
    not DATA_HOLD_OUT.exists(),
    reason="data/hold_out.json not present",
)
def test_real_hold_out_has_100_cards():
    cards = json.loads(DATA_HOLD_OUT.read_text(encoding="utf-8"))
    assert len(cards) == 100


@pytest.mark.skipif(
    not DATA_HOLD_OUT.exists(),
    reason="data/hold_out.json not present",
)
def test_real_hold_out_trace_equivalence_applied():
    """Cards with Trace[N] in EN and <trace>추적 N</trace> in KO must not
    be flagged as violations — the normalization equivalence rule is applied."""
    result = score_reference(DATA_HOLD_OUT)
    by_id = {cr.card_id: cr for cr in result.card_results}

    # snatch_and_grab: EN "Trace[3]" vs KO "<trace>추적 3</trace>".
    # searchlight: two "[subroutine]Trace[X]" vs "[subroutine] <trace>추적 X</trace>".
    # A literal comparison would report one missing and one extra on each.
    for card_id in ("snatch_and_grab", "searchlight"):
        cr = by_id[card_id]
        assert cr.en_symbols.get("trace_3") or cr.en_symbols.get("trace_X")
        assert not [s for s in list(cr.missing) + list(cr.extra) if s.startswith("trace_")], (
            f"{card_id}: trace equivalence not applied — {cr.missing} / {cr.extra}"
        )
        assert cr.passed, f"{card_id} should pass once Trace is normalized"


@pytest.mark.skipif(
    not DATA_HOLD_OUT.exists(),
    reason="data/hold_out.json not present",
)
def test_predictions_are_scored_not_the_reference():
    """The gate must grade agent output when predictions are supplied.

    Without this the gate reads the official ko_text no matter what the agent
    produced, so it could never fail because of the agent — the defect the
    signature change fixes.
    """
    cards = json.loads(DATA_HOLD_OUT.read_text(encoding="utf-8"))
    stripped = ["기호가 전혀 없는 번역." for _ in cards]

    result = score_hold_out(DATA_HOLD_OUT, predictions=stripped)

    assert result.cards_passed < result.cards_total
    assert result.passed is False
    # Cards whose EN carries no symbol at all still pass; the ones that do must not.
    with_symbols = [cr for cr in result.card_results if cr.en_symbols]
    assert with_symbols and all(not cr.passed for cr in with_symbols)


@pytest.mark.skipif(
    not DATA_HOLD_OUT.exists(),
    reason="data/hold_out.json not present",
)
def test_predictions_length_mismatch_is_rejected():
    with pytest.raises(ValueError):
        score_hold_out(DATA_HOLD_OUT, predictions=["하나뿐"])
