"""approved_store.py — Append-only store for approved translations (SERVICE.md §6, Pattern 10).

승인 결과는 원본 repo에 쓰지 않고 approved.jsonl로만 적재한다.
승인된 번역의 신규 용어는 new_term_candidates.json으로만 적재되고,
3단계 확정 절차를 거쳐야 용어집에 등재된다. 승인만으로 다음 카드부터 강제되지 않는다.
repo 반영·커밋은 사람이 따로 한다(공식 공개 저장소이지 팀 소유가 아니기 때문).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ApprovedRecord(BaseModel):
    """One approved (or modified-then-approved) translation record."""

    card_id: str
    field: str = Field(
        default="",
        description="Source field: 'text' for rule text, 'flavor' for flavor text",
    )
    route: str = Field(
        default="",
        description="Translation route: 'rule' or 'flavor'",
    )
    en_text: str
    ko_draft: str  # original draft before human modification; may equal approved_ko
    approved_ko: str  # the final approved Korean translation
    approved_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    modified: bool  # True when the human changed the draft before approving
    interrupt_reasons: list[str] = Field(
        default_factory=list,
        description="HITL trigger reasons that caused this card to be queued, if any",
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Any additional metadata (e.g. card type, set)",
    )


def append_approved(
    record: ApprovedRecord,
    store_path: Path | str = "approved.jsonl",
) -> ApprovedRecord:
    """Append one approved translation record to the JSONL store.

    Never writes to the original corpus git repo — only to the local file.
    Repo reflection and commits are done separately by humans.

    Args:
        record:     The approved translation record to append.
        store_path: Path to approved.jsonl. Defaults to ``approved.jsonl`` in cwd.

    Returns:
        The record as written (unchanged).
    """
    store_path = Path(store_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)

    with store_path.open("a", encoding="utf-8") as fh:
        fh.write(record.model_dump_json() + "\n")

    return record


def load_approved(store_path: Path | str = "approved.jsonl") -> list[ApprovedRecord]:
    """Read all approved records from the JSONL store.

    Returns an empty list when the file does not exist.
    """
    store_path = Path(store_path)
    if not store_path.exists():
        return []

    records: list[ApprovedRecord] = []
    for line in store_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(ApprovedRecord.model_validate_json(line))
    return records


# ---------------------------------------------------------------------------
# New term candidates — pending phase-3 confirmation before glossary entry
# ---------------------------------------------------------------------------


class NewTermCandidateRecord(BaseModel):
    """A new EN term found in an approved translation, pending phase-3 confirmation.

    Not added to the glossary automatically — phase-3 (GitHub Issue) confirmation
    is required.  Until confirmed, the term continues to trigger HITL for every
    subsequent card.
    """

    en_term: str = Field(description="Unregistered EN term found in the approved translation")
    ko_rendering: str = Field(
        default="",
        description="KO rendering used for this term in the approved translation (new_term_identity.ko_rendering)",
    )
    source_card_id: str = Field(description="Card ID of the approved translation that contained this term")
    source_field: str = Field(
        default="",
        description="Source field: 'text' for rule text, 'flavor' for flavor text",
    )
    approved_ko_context: str = Field(description="The approved KO text providing context for this term")
    submitted_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


def append_new_term_candidate(
    record: NewTermCandidateRecord,
    store_path: Path | str = "new_term_candidates.json",
) -> NewTermCandidateRecord:
    """Append one new term candidate to new_term_candidates.json.

    Never updates glossary.json — phase-3 confirmation is required before a term
    is added to the glossary and enforced for subsequent cards.

    Args:
        record:     The new term candidate to store.
        store_path: Path to new_term_candidates.json. Defaults to cwd.

    Returns:
        The record as written (unchanged).
    """
    store_path = Path(store_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)

    if store_path.exists():
        existing: list[dict] = json.loads(store_path.read_text(encoding="utf-8"))
    else:
        existing = []

    existing.append(json.loads(record.model_dump_json()))
    store_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    return record


def load_new_term_candidates(
    store_path: Path | str = "new_term_candidates.json",
) -> list[NewTermCandidateRecord]:
    """Load all new term candidates from new_term_candidates.json.

    Returns an empty list when the file does not exist.
    """
    store_path = Path(store_path)
    if not store_path.exists():
        return []

    data: list[dict] = json.loads(store_path.read_text(encoding="utf-8"))
    return [NewTermCandidateRecord.model_validate(item) for item in data]
