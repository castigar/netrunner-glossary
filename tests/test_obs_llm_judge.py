"""test_obs_llm_judge.py — Observation metrics: LLM-Judge scorer.

AC: 관찰 지표(하드 게이트 아님)로 룰 텍스트 문형 일치 LLM-Judge >= 2.5/3,
    플레이버 자연스러움 >= 2.0/3을 측정해 보고한다.

These tests use pre-built judgments (mock LLM) so they run without Bedrock
credentials. The LLM integration is isolated behind score_hold_out() and
can be tested in a separate integration suite when credentials are available.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from obs_llm_judge import (
    CardObservation,
    FlavorNaturalnessJudgment,
    ObsReport,
    RULE_PATTERN_THRESHOLD,
    FLAVOR_NATURALNESS_THRESHOLD,
    RulePatternJudgment,
    _aggregate,
    format_report,
    score_from_judgments,
    score_hold_out,
)

DATA_HOLD_OUT = Path(__file__).parent.parent / "data" / "hold_out.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_rule_judgment(card_id: str, score: int) -> RulePatternJudgment:
    return RulePatternJudgment(card_id=card_id, score=score, reasoning="test")


def _make_flavor_judgment(card_id: str, score: int) -> FlavorNaturalnessJudgment:
    return FlavorNaturalnessJudgment(card_id=card_id, score=score, reasoning="test")


def _write_hold_out(tmp_path: Path, cards: list[dict]) -> Path:
    p = tmp_path / "hold_out.json"
    p.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Threshold constants
# ---------------------------------------------------------------------------


def test_rule_pattern_threshold_value():
    assert RULE_PATTERN_THRESHOLD == pytest.approx(2.5)


def test_flavor_naturalness_threshold_value():
    assert FLAVOR_NATURALNESS_THRESHOLD == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# RulePatternJudgment schema
# ---------------------------------------------------------------------------


def test_rule_judgment_score_bounds_valid():
    for score in (1, 2, 3):
        j = _make_rule_judgment("c1", score)
        assert j.score == score


def test_rule_judgment_score_out_of_range():
    with pytest.raises(Exception):
        RulePatternJudgment(card_id="c1", score=0, reasoning="bad")


def test_rule_judgment_score_too_high():
    with pytest.raises(Exception):
        RulePatternJudgment(card_id="c1", score=4, reasoning="bad")


# ---------------------------------------------------------------------------
# FlavorNaturalnessJudgment schema
# ---------------------------------------------------------------------------


def test_flavor_judgment_score_bounds_valid():
    for score in (1, 2, 3):
        j = _make_flavor_judgment("c1", score)
        assert j.score == score


def test_flavor_judgment_score_out_of_range():
    with pytest.raises(Exception):
        FlavorNaturalnessJudgment(card_id="c1", score=0, reasoning="bad")


def test_flavor_judgment_score_too_high():
    with pytest.raises(Exception):
        FlavorNaturalnessJudgment(card_id="c1", score=4, reasoning="bad")


# ---------------------------------------------------------------------------
# CardObservation
# ---------------------------------------------------------------------------


def test_card_observation_fields():
    obs = CardObservation(
        card_id="c1",
        rule_pattern_score=3,
        flavor_naturalness_score=2,
        rule_reasoning="good",
        flavor_reasoning="ok",
    )
    assert obs.card_id == "c1"
    assert obs.rule_pattern_score == 3
    assert obs.flavor_naturalness_score == 2


# ---------------------------------------------------------------------------
# _aggregate
# ---------------------------------------------------------------------------


def test_aggregate_empty_returns_zero_report():
    report = _aggregate([])
    assert report.n == 0
    assert report.rule_pattern_mean == pytest.approx(0.0)
    assert report.flavor_naturalness_mean == pytest.approx(0.0)


def test_aggregate_single_card():
    obs = [CardObservation(card_id="c1", rule_pattern_score=3, flavor_naturalness_score=2)]
    report = _aggregate(obs)
    assert report.n == 1
    assert report.rule_pattern_mean == pytest.approx(3.0)
    assert report.flavor_naturalness_mean == pytest.approx(2.0)


def test_aggregate_multiple_cards_mean():
    obs = [
        CardObservation(card_id="c1", rule_pattern_score=3, flavor_naturalness_score=3),
        CardObservation(card_id="c2", rule_pattern_score=2, flavor_naturalness_score=1),
        CardObservation(card_id="c3", rule_pattern_score=3, flavor_naturalness_score=2),
    ]
    report = _aggregate(obs)
    assert report.rule_pattern_mean == pytest.approx(8 / 3)
    assert report.flavor_naturalness_mean == pytest.approx(2.0)


def test_aggregate_median_even_count():
    obs = [
        CardObservation(card_id="c1", rule_pattern_score=2, flavor_naturalness_score=1),
        CardObservation(card_id="c2", rule_pattern_score=3, flavor_naturalness_score=3),
    ]
    report = _aggregate(obs)
    assert report.rule_pattern_median == pytest.approx(2.5)
    assert report.flavor_naturalness_median == pytest.approx(2.0)


def test_aggregate_n_matches_observations():
    obs = [
        CardObservation(card_id=f"c{i}", rule_pattern_score=2, flavor_naturalness_score=2)
        for i in range(7)
    ]
    report = _aggregate(obs)
    assert report.n == 7


# ---------------------------------------------------------------------------
# ObsReport.meets_target
# ---------------------------------------------------------------------------


def test_report_meets_target_both_pass():
    obs = [
        CardObservation(card_id="c1", rule_pattern_score=3, flavor_naturalness_score=3),
        CardObservation(card_id="c2", rule_pattern_score=3, flavor_naturalness_score=2),
    ]
    report = _aggregate(obs)
    # rule_pattern_mean = 3.0 >= 2.5 ✓
    # flavor_naturalness_mean = 2.5 >= 2.0 ✓
    assert report.rule_pattern_meets_target is True
    assert report.flavor_naturalness_meets_target is True


def test_report_meets_target_rule_fails():
    obs = [
        CardObservation(card_id="c1", rule_pattern_score=1, flavor_naturalness_score=3),
        CardObservation(card_id="c2", rule_pattern_score=2, flavor_naturalness_score=3),
    ]
    report = _aggregate(obs)
    # rule_pattern_mean = 1.5 < 2.5 ✗
    assert report.rule_pattern_meets_target is False
    assert report.flavor_naturalness_meets_target is True


def test_report_meets_target_flavor_fails():
    obs = [
        CardObservation(card_id="c1", rule_pattern_score=3, flavor_naturalness_score=1),
        CardObservation(card_id="c2", rule_pattern_score=3, flavor_naturalness_score=1),
    ]
    report = _aggregate(obs)
    # flavor_naturalness_mean = 1.0 < 2.0 ✗
    assert report.rule_pattern_meets_target is True
    assert report.flavor_naturalness_meets_target is False


def test_report_meets_target_at_threshold():
    # Rule at exactly 2.5: two cards with scores 2 and 3
    obs = [
        CardObservation(card_id="c1", rule_pattern_score=2, flavor_naturalness_score=2),
        CardObservation(card_id="c2", rule_pattern_score=3, flavor_naturalness_score=2),
    ]
    report = _aggregate(obs)
    assert report.rule_pattern_mean == pytest.approx(2.5)
    assert report.rule_pattern_meets_target is True
    assert report.flavor_naturalness_meets_target is True


# ---------------------------------------------------------------------------
# score_from_judgments
# ---------------------------------------------------------------------------


def test_score_from_judgments_returns_report():
    rules = [_make_rule_judgment("c1", 3), _make_rule_judgment("c2", 2)]
    flavors = [_make_flavor_judgment("c1", 3), _make_flavor_judgment("c2", 2)]
    report = score_from_judgments(rules, flavors)
    assert isinstance(report, ObsReport)
    assert report.n == 2


def test_score_from_judgments_length_mismatch():
    rules = [_make_rule_judgment("c1", 3)]
    flavors = [_make_flavor_judgment("c1", 3), _make_flavor_judgment("c2", 2)]
    with pytest.raises(ValueError, match="length"):
        score_from_judgments(rules, flavors)


def test_score_from_judgments_card_observations_preserved():
    rules = [_make_rule_judgment("mycard", 3)]
    flavors = [_make_flavor_judgment("mycard", 2)]
    report = score_from_judgments(rules, flavors)
    assert len(report.card_observations) == 1
    assert report.card_observations[0].card_id == "mycard"
    assert report.card_observations[0].rule_pattern_score == 3
    assert report.card_observations[0].flavor_naturalness_score == 2


def test_score_from_judgments_all_perfect():
    n = 10
    rules = [_make_rule_judgment(f"c{i}", 3) for i in range(n)]
    flavors = [_make_flavor_judgment(f"c{i}", 3) for i in range(n)]
    report = score_from_judgments(rules, flavors)
    assert report.rule_pattern_mean == pytest.approx(3.0)
    assert report.flavor_naturalness_mean == pytest.approx(3.0)
    assert report.rule_pattern_meets_target is True
    assert report.flavor_naturalness_meets_target is True


def test_score_from_judgments_all_minimum():
    n = 5
    rules = [_make_rule_judgment(f"c{i}", 1) for i in range(n)]
    flavors = [_make_flavor_judgment(f"c{i}", 1) for i in range(n)]
    report = score_from_judgments(rules, flavors)
    assert report.rule_pattern_mean == pytest.approx(1.0)
    assert report.flavor_naturalness_mean == pytest.approx(1.0)
    assert report.rule_pattern_meets_target is False
    assert report.flavor_naturalness_meets_target is False


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


def test_format_report_contains_metrics():
    obs = [
        CardObservation(card_id="c1", rule_pattern_score=3, flavor_naturalness_score=3)
    ]
    report = _aggregate(obs)
    text = format_report(report)
    assert "3.000" in text
    assert "관찰 지표" in text
    assert "납품 거부 사유가 아닙니다" in text


def test_format_report_meets_target_symbol():
    obs = [
        CardObservation(card_id="c1", rule_pattern_score=3, flavor_naturalness_score=1)
    ]
    report = _aggregate(obs)
    text = format_report(report)
    assert "✓" in text   # rule passes
    assert "△" in text   # flavor fails


def test_format_report_is_string():
    report = _aggregate([])
    assert isinstance(format_report(report), str)


# ---------------------------------------------------------------------------
# score_hold_out — unit test with mock LLM (no Bedrock credentials needed)
# ---------------------------------------------------------------------------


class _RecordingChain:
    """Stands in for a built LangChain chain: records inputs, returns scripted results."""

    def __init__(self, results, seen=None):
        self._results = results
        self._seen = seen

    def batch(self, inputs, config=None):
        if self._seen is not None:
            self._seen.extend(inputs)
        return self._results[: len(inputs)]


def _patch_chains(monkeypatch, rule_results, flavor_results, rule_seen=None, flavor_seen=None):
    """Replace the chain builders so score_hold_out runs without Bedrock.

    Patching at the builder boundary (not the LLM) keeps score_hold_out's own
    orchestration — which judge sees which text, which cards are skipped — under
    test. Patching the LLM instead pushed every assertion through LangChain's
    Runnable machinery, which is why the previous mock was written and then
    silently bypassed.
    """
    import obs_llm_judge as mod

    monkeypatch.setattr(
        mod, "_build_rule_chain", lambda llm: _RecordingChain(rule_results, rule_seen)
    )
    monkeypatch.setattr(
        mod, "_build_flavor_chain", lambda llm: _RecordingChain(flavor_results, flavor_seen)
    )


def test_score_hold_out_actually_runs_the_llm_path(tmp_path, monkeypatch):
    """score_hold_out itself is exercised, not routed around.

    The previous version built a mock and then never used it: it called
    score_from_judgments instead, so score_hold_out — the function that decides
    what each judge is shown — had no test at all.
    """
    cards = [
        {"id": f"c{i}", "en_text": "Install.", "ko_text": "설치한다.",
         "en_flavor": "Flavour.", "ko_flavor": "플레이버."}
        for i in range(3)
    ]
    path = _write_hold_out(tmp_path, cards)
    _patch_chains(
        monkeypatch,
        [_make_rule_judgment(f"c{i}", 3) for i in range(3)],
        [_make_flavor_judgment(f"c{i}", 2) for i in range(3)],
    )

    report = score_hold_out(
        path,
        predictions=["설치한다."] * 3,
        flavor_predictions=["플레이버."] * 3,
        llm=object(),
    )

    assert isinstance(report, ObsReport)
    assert report.n == 3
    assert report.flavor_n == 3
    assert report.rule_pattern_mean == pytest.approx(3.0)
    assert report.flavor_naturalness_mean == pytest.approx(2.0)


def test_flavor_judge_is_shown_flavor_text_not_the_rule_translation(tmp_path, monkeypatch):
    """The flavour judge must receive the flavour translation, not the rule one.

    This is the defect the metric had: both judges were handed ``predictions``,
    so "flavour naturalness" scored rule text. Asserting on what reaches the
    chain is the only way this stays fixed.
    """
    seen: list[dict] = []
    cards = [{"id": "c0", "en_text": "Install.", "ko_text": "설치한다.",
              "en_flavor": "Fifteen seconds of fame.", "ko_flavor": "15초 간의 유명세."}]
    path = _write_hold_out(tmp_path, cards)
    _patch_chains(
        monkeypatch,
        [_make_rule_judgment("c0", 3)],
        [_make_flavor_judgment("c0", 3)],
        flavor_seen=seen,
    )

    score_hold_out(
        path,
        predictions=["설치한다."],
        flavor_predictions=["15초 간의 유명세."],
        llm=object(),
    )

    assert seen == [{"card_id": "c0", "ko_text": "15초 간의 유명세."}]


def test_cards_without_flavor_are_excluded_not_scored_zero(tmp_path, monkeypatch):
    """35 of the 100 hold-out cards carry no KO flavour; they leave the denominator.

    Scoring them as 0 would drag the mean below target for a reason that has
    nothing to do with translation quality.
    """
    cards = [
        {"id": "c0", "en_text": "A.", "ko_text": "가.",
         "en_flavor": "F.", "ko_flavor": "플레이버."},
        {"id": "c1", "en_text": "B.", "ko_text": "나.",
         "en_flavor": "", "ko_flavor": ""},
    ]
    path = _write_hold_out(tmp_path, cards)
    _patch_chains(
        monkeypatch,
        [_make_rule_judgment("c0", 3), _make_rule_judgment("c1", 3)],
        [_make_flavor_judgment("c0", 2)],
    )

    report = score_hold_out(
        path,
        predictions=["가.", "나."],
        flavor_predictions=["플레이버.", ""],
        llm=object(),
    )

    assert report.n == 2
    assert report.flavor_n == 1
    assert report.flavor_naturalness_mean == pytest.approx(2.0)
    assert report.card_observations[1].flavor_naturalness_score is None


def test_flavor_not_supplied_is_reported_as_unmeasured(tmp_path, monkeypatch):
    """Omitting flavour translations must not read as 'target missed' or as 0.0."""
    cards = [{"id": "c0", "en_text": "A.", "ko_text": "가.",
              "en_flavor": "F.", "ko_flavor": "플레이버."}]
    path = _write_hold_out(tmp_path, cards)
    _patch_chains(monkeypatch, [_make_rule_judgment("c0", 3)], [])

    report = score_hold_out(path, predictions=["가."], llm=object())

    assert report.flavor_measured is False
    assert report.flavor_n == 0
    assert report.flavor_naturalness_meets_target is False
    assert "미측정" in format_report(report)


def test_card_id_comes_from_the_record_not_the_model(tmp_path, monkeypatch):
    """A hallucinated card_id in the judge output must not relabel the row."""
    cards = [{"id": "real_id", "en_text": "A.", "ko_text": "가."}]
    path = _write_hold_out(tmp_path, cards)
    _patch_chains(monkeypatch, [_make_rule_judgment("HALLUCINATED", 3)], [])

    report = score_hold_out(path, predictions=["가."], llm=object())

    assert report.card_observations[0].card_id == "real_id"


def test_score_hold_out_length_mismatch_via_from_judgments():
    """Length mismatch is caught at score_from_judgments boundary."""
    with pytest.raises(ValueError):
        score_from_judgments(
            [_make_rule_judgment("c1", 3)],
            [_make_flavor_judgment("c1", 3), _make_flavor_judgment("c2", 3)],
        )


# ---------------------------------------------------------------------------
# Integration: real hold-out data structure check (no LLM call)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not DATA_HOLD_OUT.exists(),
    reason="data/hold_out.json not present",
)
def test_hold_out_has_required_fields():
    """Hold-out cards have id, en_text, ko_text — inputs obs_llm_judge expects."""
    cards = json.loads(DATA_HOLD_OUT.read_text(encoding="utf-8"))
    for card in cards:
        assert "id" in card
        assert "en_text" in card
        assert "ko_text" in card


@pytest.mark.skipif(
    not DATA_HOLD_OUT.exists(),
    reason="data/hold_out.json not present",
)
def test_score_from_judgments_simulates_all_perfect_on_hold_out():
    """Simulate all-perfect judgments on the real hold-out set."""
    cards = json.loads(DATA_HOLD_OUT.read_text(encoding="utf-8"))
    n = len(cards)
    rule_js = [_make_rule_judgment(c["id"], 3) for c in cards]
    flavor_js = [_make_flavor_judgment(c["id"], 3) for c in cards]
    report = score_from_judgments(rule_js, flavor_js)
    assert report.n == n
    assert report.rule_pattern_meets_target is True
    assert report.flavor_naturalness_meets_target is True


@pytest.mark.skipif(
    not DATA_HOLD_OUT.exists(),
    reason="data/hold_out.json not present",
)
def test_score_from_judgments_simulates_worst_case_on_hold_out():
    """Simulate all-minimum judgments — report is populated but targets not met."""
    cards = json.loads(DATA_HOLD_OUT.read_text(encoding="utf-8"))
    n = len(cards)
    rule_js = [_make_rule_judgment(c["id"], 1) for c in cards]
    flavor_js = [_make_flavor_judgment(c["id"], 1) for c in cards]
    report = score_from_judgments(rule_js, flavor_js)
    assert report.n == n
    assert report.rule_pattern_meets_target is False
    assert report.flavor_naturalness_meets_target is False


@pytest.mark.skipif(
    not DATA_HOLD_OUT.exists(),
    reason="data/hold_out.json not present",
)
def test_format_report_on_real_hold_out():
    """format_report produces a non-empty report string for real hold-out size."""
    cards = json.loads(DATA_HOLD_OUT.read_text(encoding="utf-8"))
    rule_js = [_make_rule_judgment(c["id"], 3) for c in cards]
    flavor_js = [_make_flavor_judgment(c["id"], 2) for c in cards]
    report = score_from_judgments(rule_js, flavor_js)
    text = format_report(report)
    assert len(text) > 0
    assert str(report.n) in text
