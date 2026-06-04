"""Key event extraction using entity-action triples and importance scoring."""

from __future__ import annotations

from typing import Optional

import spacy

from src.config import SPACY_MODEL

_nlp: Optional[spacy.language.Language] = None


def _get_nlp() -> spacy.language.Language:
    global _nlp
    if _nlp is None:
        try:
            _nlp = spacy.load(SPACY_MODEL)
        except OSError:
            from spacy.cli import download
            download(SPACY_MODEL)
            _nlp = spacy.load(SPACY_MODEL)
    return _nlp


def _extract_svo_triples(doc) -> list[dict]:
    """Extract subject-verb-object triples from a spaCy Doc."""
    triples: list[dict] = []
    for token in doc:
        if token.pos_ != "VERB" or token.dep_ in ("aux", "auxpass"):
            continue

        subject: Optional[str] = None
        obj: Optional[str] = None
        verb = token.lemma_

        for child in token.children:
            if child.dep_ in ("nsubj", "nsubjpass"):
                # Use the subtree for multi-word subjects
                subject = ' '.join(t.text for t in child.subtree
                                   if t.pos_ not in ("DET", "PUNCT"))
            elif child.dep_ in ("dobj", "attr", "oprd", "pobj"):
                obj = ' '.join(t.text for t in child.subtree
                               if t.pos_ not in ("DET", "PUNCT"))
            elif child.dep_ == "prep":
                # Follow prepositional phrases for indirect objects
                for grandchild in child.children:
                    if grandchild.dep_ == "pobj" and obj is None:
                        obj = (child.text + ' ' +
                               ' '.join(t.text for t in grandchild.subtree
                                        if t.pos_ not in ("DET", "PUNCT")))

        if subject:
            triples.append({
                "subject": subject.strip(),
                "verb": verb,
                "object": obj.strip() if obj else None,
            })

    return triples


def _classify_event_type(verb: str, obj: Optional[str]) -> str:
    """Classify an event by its type based on verb semantics."""
    movement = {"go", "run", "walk", "travel", "move", "fly", "swim",
                "climb", "ride", "leave", "arrive", "return", "come"}
    communication = {"say", "tell", "ask", "speak", "shout", "whisper",
                     "call", "sing", "announce", "reply", "answer"}
    conflict = {"fight", "hit", "attack", "chase", "catch", "grab",
                "throw", "push", "defeat", "escape", "struggle"}
    discovery = {"find", "discover", "see", "notice", "realize", "learn",
                 "understand", "know", "reveal", "uncover"}
    creation = {"make", "build", "create", "draw", "paint", "cook",
                "grow", "plant", "write"}
    emotional = {"love", "hate", "fear", "cry", "laugh", "smile",
                 "hug", "miss", "wish", "hope", "worry"}
    transformation = {"become", "change", "turn", "transform", "grow",
                      "shrink", "appear", "disappear", "wake"}

    v = verb.lower()
    if v in movement:
        return "movement"
    if v in communication:
        return "dialogue"
    if v in conflict:
        return "conflict"
    if v in discovery:
        return "discovery"
    if v in creation:
        return "creation"
    if v in emotional:
        return "emotional"
    if v in transformation:
        return "transformation"
    return "action"


def _compute_importance(
    seg_idx: int,
    entity_count: int,
    sentiment_scores: list[float],
    sentiment_peaks: list[int],
    sentiment_valleys: list[int],
    total_segments: int,
    reference_count: int,
) -> float:
    """Score event importance: sentiment_peak_proximity * entity_density * reference_count."""
    if total_segments == 0:
        return 0.0

    # Sentiment peak proximity (higher = closer to a peak or valley)
    critical_points = sentiment_peaks + sentiment_valleys
    if critical_points:
        min_dist = min(abs(seg_idx - cp) for cp in critical_points)
        peak_proximity = max(0.1, 1.0 - min_dist / max(1, total_segments))
    else:
        peak_proximity = 0.5

    # Entity density (normalized)
    entity_density = min(1.0, entity_count / 5.0)

    # Reference count factor
    ref_factor = min(1.0, reference_count / 3.0)

    score = peak_proximity * 0.4 + entity_density * 0.3 + ref_factor * 0.3
    return round(score, 3)


def extract_key_events(
    segments: list[dict],
    characters: list[dict],
    sentiment: dict,
) -> list[dict]:
    """Extract key events from the story.

    Args:
        segments: List of segment dicts (must have ``text`` and ``id``).
        characters: Character list from ``extract_characters``.
        sentiment: Sentiment analysis result from ``analyze_sentiment``.

    Returns:
        List of event dicts with keys: segment_id, summary,
        importance_score, characters_involved, event_type.
    """
    if not segments:
        return []

    nlp = _get_nlp()
    char_names = {c["name"].lower() for c in characters}
    # Include aliases
    for c in characters:
        for alias in c.get("aliases", []):
            char_names.add(alias.lower())

    sentiment_scores = sentiment.get("scores", [])
    sentiment_peaks = sentiment.get("peaks", [])
    sentiment_valleys = sentiment.get("valleys", [])

    events: list[dict] = []

    for seg in segments:
        text = seg.get("text", "")
        seg_id = seg.get("id", 0)
        if not text.strip():
            continue

        doc = nlp(text)
        triples = _extract_svo_triples(doc)

        if not triples:
            continue

        # Find entities in this segment
        seg_entities = [ent.text for ent in doc.ents]
        entity_count = len(seg_entities)

        # Identify characters in this segment
        chars_here: set[str] = set()
        text_lower = text.lower()
        for c in characters:
            if c["name"].lower() in text_lower:
                chars_here.add(c["name"])
            for alias in c.get("aliases", []):
                if alias.lower() in text_lower:
                    chars_here.add(c["name"])

        # Group triples by significance and build event summaries
        # Use the most significant triple (character-involved ones first)
        scored_triples: list[tuple[float, dict]] = []
        for triple in triples:
            subj_lower = triple["subject"].lower()
            is_char = any(cn in subj_lower for cn in char_names)
            ref_count = 1
            if is_char:
                ref_count += 2
            if triple["object"]:
                ref_count += 1

            importance = _compute_importance(
                seg_idx=seg_id,
                entity_count=entity_count,
                sentiment_scores=sentiment_scores,
                sentiment_peaks=sentiment_peaks,
                sentiment_valleys=sentiment_valleys,
                total_segments=len(segments),
                reference_count=ref_count,
            )
            scored_triples.append((importance, triple))

        if not scored_triples:
            continue

        # Take top event(s) per segment
        scored_triples.sort(key=lambda x: x[0], reverse=True)
        top_score, top_triple = scored_triples[0]

        # Build summary
        parts = [top_triple["subject"], top_triple["verb"]]
        if top_triple["object"]:
            parts.append(top_triple["object"])
        summary = ' '.join(parts)

        event_type = _classify_event_type(
            top_triple["verb"], top_triple["object"]
        )

        events.append({
            "segment_id": seg_id,
            "summary": summary,
            "importance_score": top_score,
            "characters_involved": sorted(chars_here),
            "event_type": event_type,
        })

    # Sort by importance
    events.sort(key=lambda e: e["importance_score"], reverse=True)
    return events
