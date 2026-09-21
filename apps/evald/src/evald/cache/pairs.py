"""Labelled pairs for calibrating the similarity threshold.

A cache threshold is only as trustworthy as the pairs it was fitted on, and the
easiest way to get a flattering number is to calibrate against easy negatives.
Two prompts drawn at random from a 1,500-item corpus are almost never similar,
so ANY threshold separates them and the resulting false-hit rate looks superb
while telling you nothing about production, where the dangerous cases are
prompts that are close but not the same.

So negatives here are HARD by construction: for each item, its nearest
*different* neighbour in embedding space. Those are exactly the pairs that sit
near the decision boundary, and they are the only ones that carry information
about where to put it (DECISIONS.md D-045).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

PairLabel = Literal["duplicate", "different"]


@dataclass(frozen=True, slots=True)
class LabelledPair:
    """Two prompts and whether a cache SHOULD serve one for the other."""

    pair_id: str
    left_slug: str
    right_slug: str
    left_text: str
    right_text: str
    #: "duplicate" -> a hit here is CORRECT. "different" -> a hit is a FALSE HIT.
    label: PairLabel
    #: How the pair was produced, so a reader can judge the label's provenance.
    source: Literal["paraphrase", "hard_negative", "human"]
    #: Cosine similarity, filled in once both sides are embedded.
    similarity: float | None = None
    #: Set when a human has confirmed the auto-assigned label.
    human_verified: bool = False


def write_pairs(pairs: list[LabelledPair], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(asdict(p), sort_keys=True) + "\n" for p in pairs),
        encoding="utf-8",
    )
    return path


def read_pairs(path: Path) -> list[LabelledPair]:
    if not path.is_file():
        return []
    return [
        LabelledPair(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Raises on a dimension mismatch rather than truncating.

    A silent dimension mismatch would compare two different embedding spaces and
    return a plausible-looking number, which is worse than an error.
    """
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(dot / (na * nb))


def hardest_negatives(
    slugs: list[str],
    texts: dict[str, str],
    embeddings: dict[str, list[float]],
    per_item: int = 1,
) -> list[LabelledPair]:
    """For each item, its most similar DIFFERENT item.

    Different corpus items are different questions by construction — the corpus
    rejects duplicate prompts at assembly (D-025) — so a cache hit between any
    two of them is a false hit, and no human labelling is needed to know that.
    """
    pairs: list[LabelledPair] = []
    for left in slugs:
        scored = [
            (cosine(embeddings[left], embeddings[right]), right) for right in slugs if right != left
        ]
        scored.sort(reverse=True)
        for sim, right in scored[:per_item]:
            pairs.append(
                LabelledPair(
                    pair_id=f"neg:{left}|{right}",
                    left_slug=left,
                    right_slug=right,
                    left_text=texts[left],
                    right_text=texts[right],
                    label="different",
                    source="hard_negative",
                    similarity=round(sim, 6),
                )
            )
    return pairs


def paraphrase_positives(
    paraphrases: dict[str, str],
    texts: dict[str, str],
    embeddings: dict[str, list[float]],
    paraphrase_embeddings: dict[str, list[float]],
) -> list[LabelledPair]:
    """Item vs a reworded version of itself. A hit here is correct."""
    pairs: list[LabelledPair] = []
    for slug, reworded in sorted(paraphrases.items()):
        if slug not in embeddings or slug not in paraphrase_embeddings:
            continue
        pairs.append(
            LabelledPair(
                pair_id=f"pos:{slug}",
                left_slug=slug,
                right_slug=f"{slug}#paraphrase",
                left_text=texts[slug],
                right_text=reworded,
                label="duplicate",
                source="paraphrase",
                similarity=round(cosine(embeddings[slug], paraphrase_embeddings[slug]), 6),
            )
        )
    return pairs


PARAPHRASE_INSTRUCTION = (
    "Reword the request below so it asks for exactly the same thing in different words. "
    "Keep every constraint, number, name and requirement identical — a correct answer to the "
    "original must be a correct answer to your version, and vice versa. Do not add, remove or "
    "soften anything. Reply with the reworded request and nothing else."
)
