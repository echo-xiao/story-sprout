"""Coreference resolution for character tracking.

Handles everything related to "who is in this scene":
1. Multi-word alias replacement in segment text
   (e.g., "Charles Darnay" → "Charles Evrémonde")
2. Pronoun resolution via gender + recency
   (e.g., "he" → most recent male character)
3. Tagging each segment with characters_in_scene

Algorithm inspired by coreferee's three-stage approach:
- Stage 1: Anaphor detection (find pronouns)
- Stage 2: Candidate antecedent selection (gender + distance filter)
- Stage 3: Antecedent ranking (recency wins)

Usage:
    from src.analysis.coreference import resolve_coreferences

    resolve_coreferences(segments, characters, profiles,
                         alias_map=alias_map, gender_map=gender_map)
"""

from __future__ import annotations

import re
from collections import defaultdict

# ── Pronoun sets ──

_MALE_PRONOUNS = {"he", "him", "his", "himself"}
_FEMALE_PRONOUNS = {"she", "her", "hers", "herself"}
_NEUTRAL_PRONOUNS = {"they", "them", "their", "themselves"}

# ── Honorifics ──

_HONORIFICS = {
    "monsieur", "madame", "mademoiselle", "monseigneur", "mme", "mlle",
    "mr", "mr.", "mrs", "mrs.", "ms", "ms.", "miss", "dr", "dr.",
    "sir", "lord", "lady", "captain", "colonel", "major", "general",
    "sergeant", "citizen", "comrade", "father", "mother", "brother",
    "sister", "uncle", "aunt", "king", "queen", "prince", "princess",
    "duke", "duchess", "count", "countess", "baron", "baroness",
    "don", "doña", "señor", "señora", "saint", "st", "st.",
    "old", "young", "little", "big", "master", "citizeness",
}

_MALE_TITLES = {
    "monsieur", "mr", "mr.", "sir", "lord", "king", "prince",
    "duke", "count", "baron", "don", "señor", "captain", "colonel",
    "major", "general", "sergeant", "master", "monseigneur",
}
_FEMALE_TITLES = {
    "madame", "mademoiselle", "mrs", "mrs.", "ms", "ms.", "miss",
    "lady", "queen", "princess", "duchess", "countess", "baroness",
    "doña", "señora", "citizeness", "mme", "mlle",
}


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _strip_honorific(name: str) -> str:
    """Remove leading honorific/title from a name."""
    words = name.strip().split()
    while words and words[0].lower().rstrip(".") in _HONORIFICS:
        words.pop(0)
    return " ".join(words) if words else name


def infer_gender(name: str, profiles: list[dict] | None = None) -> str:
    """Infer character gender from name title or profile descriptions.

    Returns 'male', 'female', or 'unknown'.
    """
    if profiles:
        for p in profiles:
            if p.get("name", "") == name:
                desc = " ".join(
                    p.get("appearance_description", []) +
                    p.get("personality_traits", [])
                ).lower()
                female_clues = {"woman", "girl", "lady", "wife", "mother",
                                "daughter", "sister", "feminine", "beautiful",
                                "her hair", "her eyes", "her face"}
                male_clues = {"man", "boy", "gentleman", "husband", "father",
                              "son", "brother", "masculine", "handsome",
                              "his hair", "his eyes", "his face"}
                f_score = sum(1 for w in female_clues if w in desc)
                m_score = sum(1 for w in male_clues if w in desc)
                if f_score > m_score:
                    return "female"
                if m_score > f_score:
                    return "male"

    first_word = name.lower().split()[0] if name.split() else ""
    first_word = first_word.rstrip(".")
    if first_word in _MALE_TITLES:
        return "male"
    if first_word in _FEMALE_TITLES:
        return "female"

    return "unknown"


def _find_names_in_text(text: str, character_names: list[str]) -> list[str]:
    """Find which character names appear in text.

    Checks full name and honorific-stripped name.
    Returns list of matched canonical names (no duplicates).
    """
    text_lower = text.lower()
    found = []
    seen = set()
    for name in character_names:
        if name in seen:
            continue
        name_lower = name.lower()
        if name_lower in text_lower:
            found.append(name)
            seen.add(name)
            continue
        base = _strip_honorific(name)
        base_lower = base.lower()
        if (base != name and len(base_lower) >= 4 and
                re.search(r'\b' + re.escape(base_lower) + r'\b', text_lower)):
            found.append(name)
            seen.add(name)
    return found


def _detect_pronouns(text: str) -> dict[str, bool]:
    """Detect presence of gendered pronouns in text."""
    words = set(re.findall(r'\b\w+\b', text.lower()))
    return {
        "male": bool(words & _MALE_PRONOUNS),
        "female": bool(words & _FEMALE_PRONOUNS),
        "neutral": bool(words & _NEUTRAL_PRONOUNS),
    }


# ═══════════════════════════════════════════════════════════════
# Canonical name selection (by frequency)
# ═══════════════════════════════════════════════════════════════

def pick_canonical_names(
    characters: list[dict],
    full_text: str,
) -> dict[str, str]:
    """For each character, pick the most frequently used name form as canonical.

    Counts occurrences of the NLP canonical name + all aliases in the full text.
    The most frequent form becomes the new canonical name.

    Returns:
        {old_name: new_canonical_name} for characters whose canonical changed.
    """
    renames: dict[str, str] = {}
    text_lower = full_text.lower()

    for c in characters:
        name = c.get("name", "")
        if not name or c.get("mention_count", 0) < 3:
            continue

        # Collect all name variants: canonical + aliases
        variants = [name]
        for alias in c.get("aliases", []):
            if alias and alias != name:
                variants.append(alias)

        # Count occurrences of each variant in the full text
        counts: dict[str, int] = {}
        for v in variants:
            # Use word boundary matching for accurate counts
            pattern = r'\b' + re.escape(v) + r'\b'
            count = len(re.findall(pattern, full_text, flags=re.IGNORECASE))
            if count > 0:
                counts[v] = count

        if not counts:
            continue

        # Pick the most frequent variant, but prefer multi-word names
        # Single-word names are bad canonicals (readers won't recognize "Lorry")
        # Only pick a single-word name if there are NO multi-word alternatives
        multi_word = {v: c for v, c in counts.items() if len(v.split()) >= 2}
        if multi_word:
            best = max(multi_word, key=multi_word.get)
        else:
            best = max(counts, key=counts.get)

        if best != name:
            renames[name] = best
            # Update the character dict in-place
            old_aliases = c.get("aliases", [])
            new_aliases = [a for a in old_aliases if a != best] + [name]
            c["name"] = best
            c["aliases"] = new_aliases

    # Merge duplicate characters (same canonical name after renaming)
    seen: dict[str, dict] = {}  # name -> merged character dict
    to_remove = []
    for c in characters:
        name = c.get("name", "")
        if name in seen:
            # Merge into existing entry
            target = seen[name]
            target["mention_count"] = target.get("mention_count", 0) + c.get("mention_count", 0)
            # Merge aliases (deduplicate)
            existing_aliases = set(target.get("aliases", []))
            for a in c.get("aliases", []):
                if a != name:
                    existing_aliases.add(a)
            target["aliases"] = list(existing_aliases)
            # Keep the better role (main > supporting > minor)
            role_rank = {"main": 3, "supporting": 2, "minor": 1}
            if role_rank.get(c.get("role"), 0) > role_rank.get(target.get("role"), 0):
                target["role"] = c.get("role")
            # Merge co_occurring_characters
            for co_name, co_count in c.get("co_occurring_characters", {}).items():
                target.setdefault("co_occurring_characters", {})[co_name] = (
                    target.get("co_occurring_characters", {}).get(co_name, 0) + co_count
                )
            to_remove.append(c)
        else:
            seen[name] = c

    for c in to_remove:
        characters.remove(c)

    return renames


# ═══════════════════════════════════════════════════════════════
# Alias map building (from NLP character data)
# ═══════════════════════════════════════════════════════════════

def build_alias_map(characters: list[dict]) -> dict[str, str]:
    """Build multi-word alias map from NLP character data.

    Only includes multi-word aliases — single words are too ambiguous
    for safe global text replacement.

    Returns:
        {alias_lower: canonical_name}
    """
    alias_map: dict[str, str] = {}
    full_names = {c.get("name", "") for c in characters if c.get("mention_count", 0) >= 3}

    # Honorific-stripped bases (only multi-word)
    base_to_names: dict[str, list[str]] = defaultdict(list)
    for name in full_names:
        base = _strip_honorific(name)
        if base and base != name:
            base_to_names[base.lower()].append(name)

    for base_lower, names in base_to_names.items():
        if len(names) == 1 and len(base_lower.split()) >= 2:
            alias_map[base_lower] = names[0]

    # Multi-word NLP aliases
    for c in characters:
        canonical = c.get("name", "")
        if c.get("mention_count", 0) < 3:
            continue
        for alias in c.get("aliases", []):
            alias_lower = alias.lower().strip()
            if len(alias_lower.split()) < 2:
                continue
            if alias_lower == canonical.lower():
                continue
            if alias_lower in {n.lower() for n in full_names if n != canonical}:
                continue
            if alias_lower in alias_map and alias_map[alias_lower] != canonical:
                del alias_map[alias_lower]
                continue
            alias_map[alias_lower] = canonical

    return alias_map


# ═══════════════════════════════════════════════════════════════
# Main resolution function
# ═══════════════════════════════════════════════════════════════

def resolve_coreferences(
    segments: list[dict],
    characters: list[dict],
    profiles: list[dict] | None = None,
    alias_map: dict[str, str] | None = None,
    gender_map: dict[str, str] | None = None,
    min_mentions: int = 5,
    context_window: int = 3,
) -> list[dict]:
    """Full coreference resolution pipeline.

    Step 1: Apply multi-word alias replacements to segment text.
    Step 2: For each segment, find explicitly mentioned characters.
    Step 3: Resolve pronouns via gender + recency (coreferee-inspired).
    Step 4: Tag each segment with characters_in_scene.

    Args:
        segments: Segment dicts (modified in-place).
        characters: Character list from NLP analysis.
        profiles: Character profiles for gender inference.
        alias_map: {alias_lower: canonical_name} for text replacement.
                   If None, auto-builds from characters.
        gender_map: {name: "male"/"female"/"unknown"}.
                    If None, auto-infers.
        min_mentions: Minimum mentions to consider a character.
        context_window: Number of previous segments for pronoun context.

    Returns:
        segments (same list, modified in-place with alias replacements
        and 'characters_in_scene' field).
    """
    # ── Step 1: Alias replacement in segment text ──
    if alias_map is None:
        alias_map = build_alias_map(characters)

    if alias_map:
        sorted_aliases = sorted(alias_map.items(), key=lambda x: -len(x[0]))
        for seg in segments:
            text = seg.get("text", "")
            for alias, canonical in sorted_aliases:
                pattern = r'\b' + re.escape(alias) + r'\b'
                text = re.sub(pattern, canonical, text, flags=re.IGNORECASE)
            seg["text"] = text

    # ── Step 2-4: Character detection + pronoun resolution ──
    char_names = [
        c.get("name", "") for c in characters
        if c.get("mention_count", 0) >= min_mentions and c.get("name")
    ]

    # Build gender map
    genders = dict(gender_map) if gender_map else {}
    for name in char_names:
        if name not in genders:
            genders[name] = infer_gender(name, profiles)

    # Track recently active characters (most recent first)
    recent: list[str] = []
    max_recent = context_window * 4
    prev_chapter = None

    for seg in segments:
        text = seg.get("text", "")

        # Reset context at chapter boundaries
        ch_idx = seg.get("chapter_idx")
        if ch_idx is not None and ch_idx != prev_chapter:
            recent = []
            prev_chapter = ch_idx

        # Stage 1: Find explicit character mentions
        explicit = _find_names_in_text(text, char_names)

        # Stage 2+3: Resolve pronouns
        pronouns = _detect_pronouns(text)
        resolved: list[str] = []

        for gender_key in ("male", "female"):
            if not pronouns[gender_key]:
                continue
            has_explicit = any(genders.get(n) == gender_key for n in explicit)
            if has_explicit:
                continue
            # Find most recent character of matching gender
            for rc in recent:
                if genders.get(rc) == gender_key and rc not in explicit:
                    resolved.append(rc)
                    break

        # Stage 4: Tag segment
        all_chars = list(dict.fromkeys(explicit + resolved))
        seg["characters_in_scene"] = all_chars

        # Update recency tracker
        for name in reversed(explicit):
            if name in recent:
                recent.remove(name)
            recent.insert(0, name)
        recent = recent[:max_recent]

    return segments
