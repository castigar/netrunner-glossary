"""field_card_synthesis.py — AC7: field-level to card-level judgment synthesis.

The pipeline outputs one DraftRecord per (card_id, field) pair.
A card may produce up to 2 records: text field (route="rule") and
flavor field (route="flavor").

Synthesis rule (AC7, prediction_unit ontology):
  - A card is compliant only when ALL its field predictions are non-empty.
  - A card is violated when ANY field has an empty prediction.
  - Empty predictions are classified by cause and counted separately.
  - Empty fields are NOT excluded from the gate denominator silently.

Empty prediction causes (empty_prediction_cause ontology):
  tm_search_failure:              draft_ko="" and interrupted=False.
  guard_rejected_in_review_queue: draft_ko="" and interrupted=True.
  approval_incomplete:            field record entirely absent from pipeline output.
  model_invocation_failure:       all Bedrock fallback models failed for this record.

Card-level gate prediction (used by gate1/2/3 for scoring):
  - All fields compliant → text field draft_ko (or "" for flavor-only cards).
  - Any field violated  → "" (card counted as violated in all three gates).
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Literal

EmptyPredictionCause = Literal[
    "tm_search_failure",
    "guard_rejected_in_review_queue",
    "approval_incomplete",
    "model_invocation_failure",
]

ALL_CAUSES: tuple[EmptyPredictionCause, ...] = (
    "tm_search_failure",
    "guard_rejected_in_review_queue",
    "approval_incomplete",
    "model_invocation_failure",
)


@dataclass
class CardSynthesisResult:
    """Card-level synthesis result for one hold-out card.

    Attributes:
        card_id:         Hold-out card identifier.
        field_count:     Number of expected fields (1 = text only, 2 = text + flavor).
        compliant:       True only when every expected field has a non-empty prediction.
        violated_fields: Field names that had empty predictions ("text" | "flavor").
        empty_causes:    List of (field_name, cause) pairs for each violated field.
        card_prediction: The prediction passed to gate scoring:
                         text-field draft_ko when compliant, "" when any field violated.
    """

    card_id: str
    field_count: int
    compliant: bool
    violated_fields: list[str] = dc_field(default_factory=list)
    empty_causes: list[tuple[str, EmptyPredictionCause]] = dc_field(default_factory=list)
    card_prediction: str = ""


@dataclass
class FieldSynthesisReport:
    """Aggregated report from field-to-card synthesis over the full hold-out set.

    Attributes:
        card_results:     Per-card synthesis results (one per hold-out card).
        compliant_count:  Cards where all fields were non-empty.
        violated_count:   Cards where at least one field was empty.
        total_count:      Total hold-out cards processed (== len(card_results)).
        empty_by_cause:   Count of empty field predictions by cause.
        card_predictions: Gate-scoring predictions aligned with hold-out order.
        field_records:    Raw field-level records from pipeline_output.jsonl.
                          Populated by _load_pipeline_output; used by echo gate.
        run_mode:         "real" | "stub" | "unknown". Populated from pipeline header.
    """

    card_results: list[CardSynthesisResult] = dc_field(default_factory=list)
    compliant_count: int = 0
    violated_count: int = 0
    total_count: int = 0
    empty_by_cause: dict[str, int] = dc_field(default_factory=dict)
    card_predictions: list[str] = dc_field(default_factory=list)
    field_records: list[dict] = dc_field(default_factory=list)
    run_mode: str = "unknown"


def synthesize_field_to_card(
    field_records: list[dict],
    hold_out_cards: list[dict],
) -> FieldSynthesisReport:
    """Synthesize field-level pipeline records into card-level judgments.

    Implements the verdict_aggregation_unit ontology concept:
      (card_id, field) pairs → card-level compliant/violated
      → gate-level ratio → gate pass/fail → gate_verdict.combine.

    A card is compliant only when ALL its expected field predictions are
    non-empty.  One field violated makes the whole card violated.  Empty
    predictions are counted by cause (empty_prediction_cause ontology) and
    are never excluded from the gate denominator silently.

    The prediction_unit contract requires that cards with one field and cards
    with two fields are processed by identical rules — only the expected-field
    set differs, not the synthesis logic.

    Args:
        field_records:   Field-level records from pipeline_output.jsonl.
                         Each entry: {card_id, route ("rule"|"flavor"),
                         draft_ko, interrupted, ...}.
        hold_out_cards:  Hold-out records from data/hold_out.json.
                         Each entry: {id, en_text, en_flavor?, ...}.

    Returns:
        :class:`FieldSynthesisReport` with card-level compliance breakdown
        and a ``card_predictions`` list aligned with hold_out_cards order,
        suitable for passing directly to gate1/2/3 scoring functions.
    """
    # Group field records by (card_id, field_name).
    # route "rule" → field "text"; route "flavor" → field "flavor".
    records_by_card: dict[str, dict[str, dict]] = {}
    for rec in field_records:
        card_id = rec.get("card_id", "")
        route = rec.get("route", "rule")
        field_name = "text" if route == "rule" else "flavor"
        if card_id not in records_by_card:
            records_by_card[card_id] = {}
        records_by_card[card_id][field_name] = rec

    empty_by_cause: dict[str, int] = {c: 0 for c in ALL_CAUSES}
    card_results: list[CardSynthesisResult] = []
    card_predictions: list[str] = []

    for hold_out_card in hold_out_cards:
        card_id = hold_out_card.get("id", "")
        en_text = (hold_out_card.get("en_text") or "").strip()
        en_flavor = (hold_out_card.get("en_flavor") or "").strip()

        # Identify expected fields for this card.
        expected_fields: list[str] = []
        if en_text:
            expected_fields.append("text")
        if en_flavor:
            expected_fields.append("flavor")

        card_recs = records_by_card.get(card_id, {})
        violated_fields: list[str] = []
        empty_causes: list[tuple[str, EmptyPredictionCause]] = []
        text_prediction = ""

        for field_name in expected_fields:
            rec = card_recs.get(field_name)
            if rec is None:
                # Field record entirely absent — approval never completed.
                cause: EmptyPredictionCause = "approval_incomplete"
                violated_fields.append(field_name)
                empty_causes.append((field_name, cause))
                empty_by_cause[cause] += 1
            else:
                draft_ko = (rec.get("draft_ko") or "").strip()
                interrupted = rec.get("interrupted", False)

                if not draft_ko:
                    # An explicit cause recorded by the producer wins: the
                    # runner knows why it left the prediction empty (AC8
                    # auto-hold → approval_incomplete; exhausted fallback
                    # chain → model_invocation_failure).  Only when the record
                    # carries none do we infer it from interruption status.
                    recorded_cause = rec.get("empty_cause")
                    if recorded_cause in ALL_CAUSES:
                        cause = recorded_cause  # type: ignore[assignment]
                    else:
                        cause = (
                            "guard_rejected_in_review_queue" if interrupted
                            else "tm_search_failure"
                        )
                    violated_fields.append(field_name)
                    empty_causes.append((field_name, cause))
                    empty_by_cause[cause] += 1
                # else: field is non-empty → not violated.

                if field_name == "text":
                    text_prediction = draft_ko  # "" when the text field is empty

        field_count = len(expected_fields)
        compliant = len(violated_fields) == 0

        # Gate-scoring prediction:
        #   compliant → text_prediction (may be "" for flavor-only cards)
        #   any violation → "" so the card registers as a gate miss
        gate_prediction = text_prediction if compliant else ""

        card_results.append(CardSynthesisResult(
            card_id=card_id,
            field_count=field_count,
            compliant=compliant,
            violated_fields=violated_fields,
            empty_causes=empty_causes,
            card_prediction=gate_prediction,
        ))
        card_predictions.append(gate_prediction)

    compliant_count = sum(1 for r in card_results if r.compliant)
    violated_count = len(card_results) - compliant_count

    return FieldSynthesisReport(
        card_results=card_results,
        compliant_count=compliant_count,
        violated_count=violated_count,
        total_count=len(card_results),
        empty_by_cause=empty_by_cause,
        card_predictions=card_predictions,
    )
