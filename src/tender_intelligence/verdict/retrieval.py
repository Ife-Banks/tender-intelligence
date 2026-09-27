"""Deterministic bounded retrieval over immutable KB snapshots.

The abstraction deliberately has no dependency on a vector store; callers can
replace its implementation while preserving the retrieval result contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetrievedEvidence:
    item_id: str
    document_type: str
    heading: str
    text: str
    signals: tuple[str, ...]


def retrieve_relevant_evidence(
    tender_requirements: list[Any],
    knowledge_base: str,
    *,
    metadata: dict[str, Any] | None = None,
    max_items: int = 8,
    max_chars: int = 12000,
) -> list[RetrievedEvidence]:
    """Select relevant heading-delimited KB evidence, bounded by item count and characters."""
    sections: list[tuple[str, str]] = []
    heading, body = "Knowledge base", []
    for line in knowledge_base.splitlines():
        if line.lstrip().startswith("#"):
            if body:
                sections.append((heading, "\n".join(body).strip()))
            heading, body = line.lstrip("# ").strip() or "Untitled", []
        else:
            body.append(line)
    if body:
        sections.append((heading, "\n".join(body).strip()))
    if not sections and knowledge_base.strip():
        sections = [("Knowledge base", knowledge_base.strip())]

    req_text = " ".join(
        str(getattr(r, "requirement", r)) for r in tender_requirements
    ).casefold()
    req_words = set(re.findall(r"[a-z0-9]{3,}", req_text))
    results: list[tuple[int, RetrievedEvidence]] = []
    metadata = metadata or {}
    metadata_text = " ".join(
        str(metadata.get(key, "")) for key in ("document_type", "date", "tags", "summary", "source")
    )
    for index, (title, text) in enumerate(sections):
        all_text = f"{title} {text} {metadata_text}"
        words = set(re.findall(r"[a-z0-9]{3,}", all_text.casefold()))
        overlap = sorted(req_words & words)
        if not overlap:
            continue
        signals = tuple(f"keyword:{word}" for word in overlap)
        results.append((len(overlap), RetrievedEvidence(
            item_id=f"kb-item-{index + 1}",
            document_type=str(metadata.get("document_type") or "structured_note"),
            heading=title, text=text, signals=signals,
        )))
    results.sort(key=lambda item: (-item[0], item[1].item_id))
    selected: list[RetrievedEvidence] = []
    used = 0
    for _, evidence in results:
        serialized_size = len(evidence.heading) + len(evidence.text)
        if len(selected) >= max_items or used + serialized_size > max_chars:
            continue
        selected.append(evidence)
        used += serialized_size
    return selected
