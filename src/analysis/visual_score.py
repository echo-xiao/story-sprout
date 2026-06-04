"""Visual concreteness scoring for text segments."""

from __future__ import annotations

from typing import Optional

import spacy

from src.config import SPACY_MODEL

_nlp: Optional[spacy.language.Language] = None

# Words that are inherently abstract / hard to draw
_ABSTRACT_NOUNS = frozenset({
    "time", "way", "thing", "idea", "thought", "mind", "moment", "fact",
    "reason", "sense", "kind", "sort", "type", "part", "point", "case",
    "question", "problem", "issue", "matter", "situation", "example",
    "result", "effect", "effort", "ability", "need", "feeling", "love",
    "hope", "fear", "anger", "joy", "peace", "truth", "knowledge",
    "freedom", "justice", "power", "chance", "change", "end", "beginning",
    "purpose", "meaning", "nature", "experience", "process", "state",
})

# Strong action verbs that translate well to illustration
_STRONG_ACTION_VERBS = frozenset({
    "run", "jump", "fly", "swim", "climb", "dance", "fight", "throw",
    "catch", "fall", "ride", "build", "break", "open", "close", "push",
    "pull", "carry", "hold", "grab", "drop", "kick", "hit", "cut",
    "draw", "paint", "cook", "eat", "drink", "sleep", "wake", "walk",
    "crawl", "skip", "hop", "dig", "plant", "grow", "bloom", "shine",
    "wave", "hug", "kiss", "cry", "laugh", "smile", "frown", "sing",
    "play", "hide", "seek", "chase", "race", "splash", "pour",
    "sprinkle", "spin", "twirl", "soar", "dive", "slide", "swing",
    "bounce", "roll", "tumble", "stomp", "tiptoe", "peek", "stare",
    "whisper", "shout", "roar", "whistle", "clap", "snap", "knock",
})


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


def _is_concrete_noun(token) -> bool:
    """Heuristic: a noun is concrete if it is not in the abstract set
    and is a common or proper noun."""
    if token.pos_ not in ("NOUN", "PROPN"):
        return False
    lemma = token.lemma_.lower()
    if lemma in _ABSTRACT_NOUNS:
        return False
    # Short single-character "nouns" are usually parsing artifacts
    if len(lemma) <= 1:
        return False
    return True


def _is_action_verb(token) -> bool:
    """Check if a token is a visually depictable action verb."""
    if token.pos_ != "VERB":
        return False
    lemma = token.lemma_.lower()
    # Auxiliaries and light verbs are not visual
    if token.dep_ in ("aux", "auxpass"):
        return False
    if lemma in ("be", "have", "do", "get", "make", "let", "say", "tell",
                  "know", "think", "seem", "become", "would", "could",
                  "should", "may", "might", "must", "shall", "will", "can"):
        return False
    # Boost if it's a known strong action verb
    return True


def score_visual_concreteness(segments: list[dict]) -> list[dict]:
    """Score each segment for visual concreteness.

    Adds ``visual_score``, ``concrete_nouns``, and ``action_verbs``
    to each segment dict.

    Args:
        segments: List of segment dicts (must have ``text`` key).

    Returns:
        The same list with added visual scoring fields.
    """
    if not segments:
        return segments

    nlp = _get_nlp()

    for seg in segments:
        text = seg.get("text", "")
        if not text.strip():
            seg["visual_score"] = 0.0
            seg["concrete_nouns"] = []
            seg["action_verbs"] = []
            continue

        doc = nlp(text)
        total_tokens = max(1, len([t for t in doc if not t.is_punct and not t.is_space]))

        concrete_nouns: list[str] = []
        action_verbs: list[str] = []

        for token in doc:
            if _is_concrete_noun(token):
                concrete_nouns.append(token.lemma_.lower())
            elif _is_action_verb(token):
                action_verbs.append(token.lemma_.lower())

        # Deduplicate for output but keep counts for scoring
        noun_count = len(concrete_nouns)
        verb_count = len(action_verbs)
        strong_verb_count = sum(1 for v in action_verbs if v in _STRONG_ACTION_VERBS)

        # Visual score components (0-1 scale)
        noun_density = min(1.0, noun_count / total_tokens * 3)
        verb_density = min(1.0, verb_count / total_tokens * 4)
        strong_verb_bonus = min(0.3, strong_verb_count * 0.05)

        # Check for color words, size words, shape words
        visual_adj_words = frozenset({
            "red", "blue", "green", "yellow", "orange", "purple", "pink",
            "white", "black", "brown", "golden", "silver", "bright", "dark",
            "big", "small", "tiny", "huge", "tall", "short", "round",
            "square", "long", "wide", "narrow", "thick", "thin", "fat",
            "shiny", "sparkly", "fluffy", "fuzzy", "smooth", "rough",
            "spotted", "striped", "colorful",
        })
        visual_adj_count = sum(
            1 for t in doc
            if t.pos_ == "ADJ" and t.lemma_.lower() in visual_adj_words
        )
        adj_bonus = min(0.2, visual_adj_count * 0.04)

        visual_score = round(
            min(1.0, noun_density * 0.4 + verb_density * 0.3 + strong_verb_bonus + adj_bonus),
            3,
        )

        seg["visual_score"] = visual_score
        seg["concrete_nouns"] = sorted(set(concrete_nouns))
        seg["action_verbs"] = sorted(set(action_verbs))

    return segments
