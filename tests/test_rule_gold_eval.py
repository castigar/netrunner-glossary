"""Tests for rule_gold_eval.py — 룰 용어 수동 정답셋 채점기.

data/README-rule-gold.md §5가 약속한 두 숫자를 낸다:
  - EN 재현율: 정답셋 EN 용어 중 추출기가 올린 비율
  - KO 정확도: 그중 역어까지 맞은 비율
그리고 §4가 약속한 대로, 추출기가 구조적으로 닿지 못하는 항목을
"못 맞힌 것"과 분리해 보고한다.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rule_gold_eval import (
    BELOW_MIN_COOCCUR,
    BELOW_RANK_CUT,
    MISSED,
    OFFICIAL_EXCLUDED,
    OVER_MAX_N,
    RuleGoldEntry,
    en_document_frequency,
    evaluate_rule_gold,
    load_rule_gold,
    normalize_en_term,
    normalize_ko_term,
    pairs_from_mapping,
)


# ---------------------------------------------------------------------------
# EN 정규화 — 추출기와 같은 토크나이저를 써야 한다
# ---------------------------------------------------------------------------


def test_normalize_en_lowercases():
    assert normalize_en_term("Archives") == "archives"


def test_normalize_en_keeps_word_sequence():
    assert normalize_en_term("end the run") == "end the run"


def test_normalize_en_splits_ampersand_like_the_extractor():
    """'R&D'는 추출기 토크나이저(\\w+)가 'r d'로 쪼갠다. 정답셋도 같게 본다."""
    assert normalize_en_term("R&D") == "r d"


def test_normalize_en_keeps_game_symbols_whole():
    assert normalize_en_term("[credit]") == "[credit]"


# ---------------------------------------------------------------------------
# KO 정규화 — 추출기와 같은 형태소 정규화를 써야 한다
# ---------------------------------------------------------------------------


def test_normalize_ko_strips_conjugation():
    assert normalize_ko_term("호스트된") == "호스트"


def test_normalize_ko_phrase_drops_particles_and_endings():
    assert normalize_ko_term("런을 종료한다") == "런 종료"


def test_normalize_ko_leaves_plain_noun_alone():
    assert normalize_ko_term("폐기") == "폐기"


# ---------------------------------------------------------------------------
# 정답셋 로딩
# ---------------------------------------------------------------------------


def _write_gold(tmp_path: Path, entries: list[dict]) -> Path:
    payload = {
        "schema_version": 1,
        "scope": "rule_text",
        "labeler": "tester",
        "labeled_at": "2026-09-17",
        "corpus": "test",
        "entries": entries,
    }
    path = tmp_path / "rule_terms_gold.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_rule_gold_returns_entries(tmp_path):
    path = _write_gold(
        tmp_path,
        [{"en": "trash", "ko": ["폐기"], "source": "unaided", "note": ""}],
    )
    entries = load_rule_gold(path)
    assert entries == (RuleGoldEntry(en="trash", ko=("폐기",), source="unaided", note=""),)


def test_load_rule_gold_accepts_multiple_ko():
    """정당한 역어가 둘 이상이면 하나라도 맞으면 정답이다 (README §3)."""
    entry = RuleGoldEntry(en="gain", ko=("얻다", "얻는다"), source="aided", note="")
    assert entry.ko == ("얻다", "얻는다")


def test_load_rule_gold_rejects_missing_ko(tmp_path):
    path = _write_gold(tmp_path, [{"en": "trash", "source": "unaided"}])
    with pytest.raises(ValueError, match="ko"):
        load_rule_gold(path)


def test_load_rule_gold_rejects_unknown_source(tmp_path):
    path = _write_gold(
        tmp_path, [{"en": "trash", "ko": ["폐기"], "source": "guessed"}]
    )
    with pytest.raises(ValueError, match="source"):
        load_rule_gold(path)


def test_load_rule_gold_rejects_unknown_schema_version(tmp_path):
    path = tmp_path / "gold.json"
    path.write_text(json.dumps({"schema_version": 2, "entries": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        load_rule_gold(path)


# ---------------------------------------------------------------------------
# EN 재현율 · KO 정확도
# ---------------------------------------------------------------------------


def _gold(*specs: tuple[str, list[str]], source: str = "unaided") -> tuple[RuleGoldEntry, ...]:
    return tuple(
        RuleGoldEntry(en=en, ko=tuple(ko), source=source, note="") for en, ko in specs
    )


def test_found_term_with_right_ko_counts_both():
    gold = _gold(("trash", ["폐기"]))
    result = evaluate_rule_gold(gold, pairs_from_mapping({"trash": "폐기"}))
    assert result.found == 1
    assert result.ko_correct == 1
    assert result.en_recall == pytest.approx(1.0)
    assert result.ko_accuracy == pytest.approx(1.0)


def test_found_term_with_wrong_ko_counts_en_only():
    gold = _gold(("trash", ["폐기"]))
    result = evaluate_rule_gold(gold, pairs_from_mapping({"trash": "파기"}))
    assert result.found == 1
    assert result.ko_correct == 0
    assert result.en_recall == pytest.approx(1.0)
    assert result.ko_accuracy == pytest.approx(0.0)


def test_any_gold_ko_may_match():
    gold = _gold(("gain", ["얻다", "얻는다"]))
    result = evaluate_rule_gold(gold, pairs_from_mapping({"gain": "얻는다"}))
    assert result.ko_correct == 1


def test_ko_match_is_morphology_normalized_on_both_sides():
    """정답 '런을 종료한다'와 시스템 '런 종료'는 같은 역어다."""
    gold = _gold(("end the run", ["런을 종료한다"]))
    result = evaluate_rule_gold(gold, pairs_from_mapping({"end the run": "런 종료"}))
    assert result.ko_correct == 1


def test_multiple_system_ko_any_may_match():
    """후보 단계에서는 한 EN에 역어가 여럿이다. 하나라도 맞으면 정답."""
    gold = _gold(("trash", ["폐기"]))
    pairs = pairs_from_mapping({"trash": ["수", "폐기", "있다"]})
    result = evaluate_rule_gold(gold, pairs)
    assert result.ko_correct == 1


def test_missing_term_is_a_miss_not_an_exclusion():
    gold = _gold(("trash", ["폐기"]), ("subroutine", ["서브루틴"]))
    result = evaluate_rule_gold(
        gold, pairs_from_mapping({"trash": "폐기"}), en_doc_freq={"subroutine": 73}
    )
    assert result.found == 1
    assert result.en_recall == pytest.approx(0.5)
    assert result.unreachable == {}
    assert [o.reach for o in result.outcomes if o.en == "subroutine"] == [MISSED]


def test_ko_accuracy_is_zero_when_nothing_found():
    gold = _gold(("trash", ["폐기"]))
    result = evaluate_rule_gold(gold, [], en_doc_freq={"trash": 174})
    assert result.found == 0
    assert result.ko_accuracy == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 닿지 않는 항목 (README §4) — 분모에서 빠진다
# ---------------------------------------------------------------------------


def test_official_glossary_term_is_excluded_not_missed():
    """공식 용어집 항목은 룰 추출 경로가 의도적으로 빼는 것이다(build_assets)."""
    gold = _gold(("barrier", ["방벽"]))
    result = evaluate_rule_gold(gold, [], official_excluded={"barrier"})
    assert result.unreachable == {OFFICIAL_EXCLUDED: 1}
    assert result.scored_size == 0
    assert result.en_recall == pytest.approx(0.0)


def test_official_exclusion_matches_underscored_id_form():
    """공식 용어집 id는 'code_gate', 인쇄 표기는 'code gate'다."""
    gold = _gold(("code gate", ["코드 게이트"]))
    result = evaluate_rule_gold(gold, [], official_excluded={"code_gate"})
    assert result.unreachable == {OFFICIAL_EXCLUDED: 1}


def test_term_longer_than_max_n_is_out_of_reach():
    gold = _gold(("add 1 power counter", ["파워 카운터 1개를 올린다"]))
    result = evaluate_rule_gold(gold, [], max_n=3)
    assert result.unreachable == {OVER_MAX_N: 1}


def test_term_below_min_cooccur_is_out_of_reach():
    """EN 등장 카드 수가 min_cooccur 미만이면 공기빈도도 그보다 작다."""
    gold = _gold(("killer", ["킬러"]))
    result = evaluate_rule_gold(gold, [], en_doc_freq={"killer": 1}, min_cooccur=5)
    assert result.unreachable == {BELOW_MIN_COOCCUR: 1}


def test_term_below_rank_cut_is_out_of_reach():
    """EN 용어 상한(max_en_terms) 밖으로 잘린 항목."""
    gold = _gold(("identity", ["ID"]))
    freq = {f"filler{i}": 100 for i in range(3)}
    freq["identity"] = 6
    result = evaluate_rule_gold(
        gold, [], en_doc_freq=freq, min_cooccur=5, max_en_terms=3
    )
    assert result.unreachable == {BELOW_RANK_CUT: 1}


def test_reach_is_not_classified_for_found_terms():
    """닿지 않는다고 분류된 항목이 실제로 잡혔으면 그냥 찾은 것이다."""
    gold = _gold(("barrier", ["방벽"]))
    result = evaluate_rule_gold(
        gold, pairs_from_mapping({"barrier": "방벽"}), official_excluded={"barrier"}
    )
    assert result.found == 1
    assert result.unreachable == {}
    assert result.scored_size == 1


def test_unreachable_terms_leave_the_denominator():
    gold = _gold(("trash", ["폐기"]), ("killer", ["킬러"]))
    result = evaluate_rule_gold(
        gold,
        pairs_from_mapping({"trash": "폐기"}),
        en_doc_freq={"trash": 174, "killer": 1},
        min_cooccur=5,
    )
    assert result.gold_size == 2
    assert result.scored_size == 1
    assert result.en_recall == pytest.approx(1.0)


def test_symbol_mangled_is_flagged_but_still_scored():
    """'R&D'는 'r d'로 후보에 오른다. 못 잡는 게 아니라 표기가 손상된 채 잡힌다."""
    gold = _gold(("R&D", ["연구개발부"]))
    result = evaluate_rule_gold(gold, pairs_from_mapping({"r d": "연구개발부"}))
    assert result.found == 1
    assert [o.symbol_mangled for o in result.outcomes] == [True]


# ---------------------------------------------------------------------------
# unaided / aided 분리 (README §2)
# ---------------------------------------------------------------------------


def test_by_source_splits_unaided_and_aided():
    gold = _gold(("trash", ["폐기"])) + _gold(("turn", ["차례"]), source="aided")
    result = evaluate_rule_gold(
        gold, pairs_from_mapping({"trash": "폐기", "turn": "차례"})
    )
    assert result.by_source["unaided"]["found"] == 1
    assert result.by_source["aided"]["found"] == 1
    assert result.by_source["unaided"]["scored"] == 1


# ---------------------------------------------------------------------------
# EN 문서 빈도
# ---------------------------------------------------------------------------


def test_en_document_frequency_counts_cards_not_occurrences():
    corpus = [
        {"en_text": "Trash this card. Trash a program."},
        {"en_text": "Trash a resource."},
    ]
    freq = en_document_frequency(corpus, max_n=1)
    assert freq["trash"] == 2


def test_en_document_frequency_covers_ngrams():
    corpus = [{"en_text": "End the run."}]
    freq = en_document_frequency(corpus, max_n=3)
    assert freq["end the run"] == 1


# ---------------------------------------------------------------------------
# 보고
# ---------------------------------------------------------------------------


def test_report_is_a_string_with_both_numbers():
    gold = _gold(("trash", ["폐기"]))
    result = evaluate_rule_gold(gold, pairs_from_mapping({"trash": "폐기"}))
    report = result.report()
    assert isinstance(report, str)
    assert "EN" in report and "KO" in report


def test_report_lists_unreachable_reasons():
    gold = _gold(("killer", ["킬러"]))
    result = evaluate_rule_gold(gold, [], en_doc_freq={"killer": 1}, min_cooccur=5)
    assert BELOW_MIN_COOCCUR in result.report()


# ---------------------------------------------------------------------------
# 실제 정답셋 · 실제 자산 (있을 때만)
# ---------------------------------------------------------------------------

GOLD_PATH = Path(__file__).parent.parent / "data" / "rule_terms_gold.json"
GLOSSARY_PATH = Path(__file__).parent.parent / "assets" / "glossary.json"


@pytest.mark.skipif(not GOLD_PATH.exists(), reason="rule gold set not available")
def test_real_gold_set_loads_and_is_labelled():
    entries = load_rule_gold(GOLD_PATH)
    assert len(entries) >= 30, "README는 30~50개를 요구한다"
    assert all(e.ko for e in entries)
    assert {e.source for e in entries} <= {"unaided", "aided"}


@pytest.mark.skipif(
    not (GOLD_PATH.exists() and GLOSSARY_PATH.exists()),
    reason="gold set or built assets not available",
)
def test_real_assets_scorecard_is_consistent(capsys):
    entries = load_rule_gold(GOLD_PATH)
    glossary = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))

    result = evaluate_rule_gold(
        entries,
        pairs_from_mapping(glossary["extracted"]),
        official_excluded=set(glossary["official"]),
    )
    print("\n--- 룰 경로 채점 ---")
    print(result.report())

    assert result.gold_size == len(entries)
    assert result.scored_size + sum(result.unreachable.values()) == result.gold_size
    assert result.ko_correct <= result.found <= result.scored_size
    assert 0.0 <= result.en_recall <= 1.0
    assert 0.0 <= result.ko_accuracy <= 1.0
