"""Character extraction using spaCy NER with co-occurrence analysis."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Optional

import spacy
from spacy.tokens import Doc

from src.config import SPACY_MODEL

_nlp: Optional[spacy.language.Language] = None


def _get_nlp() -> spacy.language.Language:
    """Lazy-load the spaCy model."""
    global _nlp
    if _nlp is None:
        try:
            _nlp = spacy.load(SPACY_MODEL)
        except OSError:
            from spacy.cli import download
            download(SPACY_MODEL)
            _nlp = spacy.load(SPACY_MODEL)
    return _nlp


def _normalize_name(name: str) -> str:
    """Normalize whitespace, capitalization, possessives, articles, and strip non-name words."""
    name = re.sub(r'\s+', ' ', name).strip()
    # Remove possessives: "Daisy's" "Daisy\u2019s" → "Daisy"
    name = re.sub(r"[\u2019\u2018'']s\b", '', name)
    name = re.sub(r"'s\b", '', name)
    # Remove leading articles: "The Tom" → "Tom"
    name = re.sub(r'^(the|a|an)\s+', '', name, flags=re.IGNORECASE)
    # Remove trailing punctuation
    name = name.rstrip('.,;:!?')

    # Strip non-name words that spaCy sometimes absorbs into PERSON entities
    # e.g., "Daisy Cynically" → "Daisy", "Suppose Tom" → "Tom"
    words = name.split()
    if len(words) >= 2:
        cleaned = []
        for w in words:
            w_lower = w.lower()
            if w_lower in _NON_NAME_WORDS:
                continue
            # Skip words that look like adverbs (end in -ly) unless they're common names
            if w_lower.endswith('ly') and len(w_lower) > 3 and w_lower not in _VALID_LY_NAMES:
                continue
            cleaned.append(w)
        if cleaned:
            words = cleaned

    name = ' '.join(words)

    # Remove trailing 's' from family names used as plural (e.g., "The Tom Buchanans" → "Tom Buchanan")
    # But keep single-word names ending in s (e.g., "James")
    if ' ' in name and name.endswith('s') and not name.endswith('ss'):
        name = name[:-1]
    return name.strip().title()


# Words that are NOT part of a person's name but spaCy sometimes includes
_NON_NAME_WORDS = {
    # Adverbs
    "cynically", "genially", "suddenly", "quietly", "angrily", "softly",
    "slowly", "quickly", "nervously", "eagerly", "reluctantly", "sadly",
    "happily", "curiously", "anxiously", "wearily", "bitterly", "absently",
    "gravely", "cheerfully", "coldly", "warmly", "briskly", "calmly",
    "desperately", "excitedly", "furiously", "gently", "hastily",
    "impatiently", "lazily", "miserably", "passionately", "politely",
    "proudly", "rudely", "sharply", "shyly", "sternly", "stubbornly",
    "suspiciously", "tenderly", "thoughtfully", "timidly", "violently",
    # Adjectives/other non-name words
    "old", "young", "little", "big", "poor", "rich", "dear", "good",
    "bad", "great", "new", "other", "certain", "late",
    # Verbs/conjunctions that spaCy sometimes absorbs into names
    "suppose", "said", "asked", "replied", "answered", "cried",
    "whispered", "shouted", "called", "told", "thought", "knew",
    "looked", "turned", "went", "came", "got", "let", "made",
    "put", "saw", "took", "began", "seemed", "felt", "heard",
    "oh", "ah", "well", "why", "now", "then", "just", "even",
    "yes", "no", "very", "quite", "rather", "indeed",
    # Titles already handled but just in case
    "mr", "mrs", "ms", "miss", "dr", "sir", "lord", "lady",
    "professor", "captain", "colonel", "major", "general",
}

# Names that end in -ly but are actually valid names
_VALID_LY_NAMES = {
    "lily", "emily", "molly", "polly", "sally", "holly", "kelly",
    "billy", "willy", "dolly", "nelly", "ally", "rally", "connolly",
    "beverly", "kimberly", "shirley", "stanley", "bentley", "bradley",
    "dudley", "hartley", "ridley", "riley", "finley", "hadley",
}


def _merge_aliases(names: list[str]) -> dict[str, str]:
    """Map variant names to a canonical form.

    Heuristic: if one name is a substring of another (e.g. "Tom" in
    "Tom Buchanan"), merge under the longer form. Also merges when
    first names match (e.g. "Tom" and "Tom Buchanan").
    """
    canonical: dict[str, str] = {}
    sorted_names = sorted(names, key=len, reverse=True)
    for name in sorted_names:
        norm = _normalize_name(name)
        if not norm:
            continue
        merged = False
        for canon in list(set(canonical.values())):
            # Check if this name is part of an existing canonical name
            if norm.lower() in canon.lower() or canon.lower() in norm.lower():
                # Use the longer form as canonical
                target = canon if len(canon) >= len(norm) else norm
                canonical[norm] = target
                # Update any existing mappings pointing to the shorter form
                if target != canon:
                    for k, v in list(canonical.items()):
                        if v == canon:
                            canonical[k] = target
                merged = True
                break
            # Check if first names match (e.g. "Tom" == first word of "Tom Buchanan")
            norm_first = norm.split()[0].lower()
            canon_first = canon.split()[0].lower()
            if len(norm_first) >= 3 and norm_first == canon_first:
                # Merge under the longer (more specific) form
                target = canon if len(canon) >= len(norm) else norm
                canonical[norm] = target
                if target != canon:
                    for k, v in list(canonical.items()):
                        if v == canon:
                            canonical[k] = target
                merged = True
                break
        if not merged:
            canonical[norm] = norm
    return canonical


def _split_into_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs for co-occurrence analysis."""
    paras = re.split(r'\n\s*\n|\n', text)
    return [p.strip() for p in paras if p.strip()]


def extract_characters(
    text: str,
    segments: list[dict],
) -> list[dict]:
    """Extract and profile characters from text.

    Args:
        text: Full text of the story.
        segments: Segmented text from ``split_into_segments``.

    Returns:
        List of character dicts with keys: name, aliases, role,
        mention_count, first_appearance, co_occurring_characters.
    """
    if not text or not text.strip():
        return []

    nlp = _get_nlp()
    doc = nlp(text)

    # Non-character names to filter out
    _SKIP_NAMES = {
        "god", "jesus", "christ", "sir", "mr", "mrs", "miss", "dr",
        "chapter", "part", "section", "table", "contents", "page",
    }

    # Collect all PERSON entities
    raw_names: list[str] = []
    for ent in doc.ents:
        if ent.label_ == "PERSON":
            norm = _normalize_name(ent.text)
            # Skip single-word names that are common non-character words
            if norm.lower() in _SKIP_NAMES:
                continue
            # Skip very short names (likely NER errors)
            if len(norm) < 2:
                continue
            # Skip names that are all lowercase (likely common nouns misclassified)
            if norm == norm.lower() and len(norm) < 5:
                continue
            raw_names.append(norm)

    if not raw_names:
        return []

    # Merge aliases
    alias_map = _merge_aliases(list(set(raw_names)))
    # Count mentions under canonical names
    mention_counts: Counter[str] = Counter()
    for name in raw_names:
        canon = alias_map.get(name, name)
        mention_counts[canon] += 1

    # First appearance (segment id)
    first_appearance: dict[str, int] = {}
    for seg in segments:
        seg_doc = nlp(seg["text"])
        for ent in seg_doc.ents:
            if ent.label_ == "PERSON":
                norm = _normalize_name(ent.text)
                canon = alias_map.get(norm, norm)
                if canon not in first_appearance:
                    first_appearance[canon] = seg["id"]

    # Co-occurrence matrix (paragraph level)
    paragraphs = _split_into_paragraphs(text)
    co_occur: dict[str, Counter[str]] = defaultdict(Counter)
    for para in paragraphs:
        para_doc = nlp(para)
        chars_in_para: set[str] = set()
        for ent in para_doc.ents:
            if ent.label_ == "PERSON":
                norm = _normalize_name(ent.text)
                canon = alias_map.get(norm, norm)
                chars_in_para.add(canon)
        for c1 in chars_in_para:
            for c2 in chars_in_para:
                if c1 != c2:
                    co_occur[c1][c2] += 1

    # Classify roles based on mention count distribution
    canonical_names = list(mention_counts.keys())
    if not canonical_names:
        return []

    max_mentions = max(mention_counts.values())
    total_mentions = sum(mention_counts.values())

    # Detect first-person narrator — they should be "main" even if rarely named
    first_person_count = text.lower().count(" i ") + text.lower().count('"i ')
    has_first_person_narrator = first_person_count > 20

    def _classify_role(count: int, name: str) -> str:
        if max_mentions <= 1:
            return "main" if count == max_mentions else "minor"
        ratio = count / max_mentions
        if ratio >= 0.4:
            return "main"
        elif ratio >= 0.15 or count >= max(3, total_mentions * 0.1):
            return "supporting"
        return "minor"

    # Build alias lists per canonical name
    alias_groups: dict[str, set[str]] = defaultdict(set)
    for variant, canon in alias_map.items():
        if variant != canon:
            alias_groups[canon].add(variant)

    # Build result
    characters: list[dict] = []
    for name in sorted(canonical_names, key=lambda n: mention_counts[n], reverse=True):
        count = mention_counts[name]
        characters.append({
            "name": name,
            "aliases": sorted(alias_groups.get(name, set())),
            "role": _classify_role(count, name),
            "mention_count": count,
            "first_appearance": first_appearance.get(name, 0),
            "co_occurring_characters": dict(co_occur.get(name, {})),
        })

    # If there's a first-person narrator, look for a character named like the narrator
    # and promote them to "main". Common narrator names in fiction.
    if has_first_person_narrator:
        narrator_found = False
        for char in characters:
            # Check if any character has very few mentions but is likely the narrator
            if char["role"] == "minor" and char["mention_count"] <= 10:
                # Characters with names like Nick, narrator references
                char["role"] = "supporting"  # at minimum supporting
        # If no clear narrator character, add a synthetic one
        narrator_names = [c["name"] for c in characters if c["role"] in ("main", "supporting")]
        if not any(c["mention_count"] <= 10 and c["role"] == "supporting" for c in characters):
            # Check if "Nick" or similar short name exists as minor
            for char in characters:
                if char["mention_count"] <= 10 and len(char["name"].split()) == 1:
                    char["role"] = "main"
                    narrator_found = True
                    break

    return characters
