"""Character persona profiling: appearance, dialogue, traits, relationships."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
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


def _find_character_mentions(text: str, name: str, aliases: list[str]) -> list[int]:
    """Find all character positions in text (by name or alias)."""
    positions: list[int] = []
    search_terms = [name] + aliases
    text_lower = text.lower()
    for term in search_terms:
        start = 0
        term_lower = term.lower()
        while True:
            idx = text_lower.find(term_lower, start)
            if idx == -1:
                break
            positions.append(idx)
            start = idx + 1
    return sorted(positions)


def _extract_appearance(doc, mention_positions: list[int], text: str) -> list[str]:
    """Extract adjectives and descriptive phrases near character mentions."""
    appearance: list[str] = []
    appearance_words = {
        "tall", "short", "big", "small", "little", "tiny", "huge",
        "thin", "fat", "young", "old", "beautiful", "pretty", "handsome",
        "ugly", "cute", "round", "long", "curly", "straight", "dark",
        "light", "bright", "pale", "brown", "black", "white", "red",
        "blonde", "golden", "silver", "gray", "grey", "blue", "green",
        "spotted", "striped", "furry", "fluffy", "hairy", "bald",
        "freckled", "wrinkled", "smooth", "rough", "soft", "strong",
        "weak", "muscular", "slender",
    }
    body_parts = {
        "hair", "eye", "eyes", "face", "nose", "mouth", "ear", "ears",
        "hand", "hands", "arm", "arms", "leg", "legs", "foot", "feet",
        "skin", "beard", "mustache", "tail", "wing", "wings", "fur",
        "feather", "feathers", "paw", "paws", "claw", "claws",
    }

    window = 100  # characters around mention
    for pos in mention_positions:
        start = max(0, pos - window)
        end = min(len(text), pos + window)
        snippet = text[start:end]
        snippet_doc = _get_nlp()(snippet)

        for token in snippet_doc:
            if token.pos_ == "ADJ" and token.lemma_.lower() in appearance_words:
                # Check if it modifies a body part or the character
                head_lemma = token.head.lemma_.lower()
                if head_lemma in body_parts or token.head.ent_type_ == "PERSON":
                    desc = f"{token.lemma_.lower()} {head_lemma}"
                    appearance.append(desc)
                elif token.dep_ in ("amod", "acomp", "attr"):
                    appearance.append(token.lemma_.lower())

    return list(dict.fromkeys(appearance))  # deduplicate preserving order


def _extract_dialogue(text: str, name: str, aliases: list[str]) -> list[str]:
    """Extract dialogue lines attributed to the character."""
    dialogues: list[str] = []
    search_terms = [name] + aliases

    # Pattern: "dialogue" said Character / Character said "dialogue"
    patterns = [
        # "..." said Name
        re.compile(
            r'["\u201c]([^"\u201d]+)["\u201d]\s*(?:said|asked|replied|exclaimed|whispered|shouted|cried|called|yelled|answered|murmured)\s+'
            + '(?:' + '|'.join(re.escape(t) for t in search_terms) + ')',
            re.IGNORECASE,
        ),
        # Name said "..."
        re.compile(
            '(?:' + '|'.join(re.escape(t) for t in search_terms) + ')'
            + r'\s+(?:said|asked|replied|exclaimed|whispered|shouted|cried|called|yelled|answered|murmured)\s*[,:]?\s*["\u201c]([^"\u201d]+)["\u201d]',
            re.IGNORECASE,
        ),
    ]

    for pattern in patterns:
        for match in pattern.finditer(text):
            line = match.group(1).strip()
            if line and line not in dialogues:
                dialogues.append(line)

    return dialogues


def _analyze_speech_patterns(dialogues: list[str]) -> str:
    """Summarize speech patterns from collected dialogue."""
    if not dialogues:
        return "no dialogue found"

    total_words = 0
    exclamations = 0
    questions = 0
    word_counts: list[int] = []

    for line in dialogues:
        words = line.split()
        word_counts.append(len(words))
        total_words += len(words)
        if line.endswith('!'):
            exclamations += 1
        if line.endswith('?'):
            questions += 1

    avg_len = total_words / len(dialogues) if dialogues else 0
    patterns: list[str] = []

    if avg_len < 5:
        patterns.append("brief")
    elif avg_len > 15:
        patterns.append("verbose")
    else:
        patterns.append("moderate length")

    if exclamations > len(dialogues) * 0.3:
        patterns.append("exclamatory")
    if questions > len(dialogues) * 0.3:
        patterns.append("inquisitive")

    return ", ".join(patterns) if patterns else "neutral"


def _extract_actions(doc, mention_positions: list[int], text: str) -> list[str]:
    """Extract actions performed by the character."""
    actions: list[str] = []
    nlp = _get_nlp()
    window = 150

    for pos in mention_positions:
        start = max(0, pos - 20)
        end = min(len(text), pos + window)
        snippet = text[start:end]
        snippet_doc = nlp(snippet)

        for token in snippet_doc:
            if token.pos_ == "VERB" and token.dep_ not in ("aux", "auxpass"):
                # Check if subject could be the character
                has_char_subj = False
                for child in token.children:
                    if child.dep_ in ("nsubj", "nsubjpass"):
                        has_char_subj = True
                        break
                if has_char_subj:
                    action = token.lemma_.lower()
                    if action not in ("be", "have", "do", "say"):
                        actions.append(action)

    return list(dict.fromkeys(actions))


def _infer_personality(
    appearance: list[str],
    actions: list[str],
    dialogues: list[str],
) -> list[str]:
    """Infer personality traits from behavior and dialogue."""
    traits: list[str] = []

    kind_actions = {"help", "share", "give", "hug", "comfort", "care", "save", "protect"}
    brave_actions = {"fight", "face", "stand", "confront", "rescue", "dare", "defend"}
    curious_actions = {"explore", "discover", "ask", "wonder", "search", "investigate", "look"}
    playful_actions = {"play", "laugh", "dance", "sing", "jump", "skip", "joke", "giggle"}
    creative_actions = {"create", "build", "draw", "paint", "imagine", "invent", "design"}

    action_set = set(actions)
    if action_set & kind_actions:
        traits.append("kind")
    if action_set & brave_actions:
        traits.append("brave")
    if action_set & curious_actions:
        traits.append("curious")
    if action_set & playful_actions:
        traits.append("playful")
    if action_set & creative_actions:
        traits.append("creative")

    # Dialogue-based traits
    question_count = sum(1 for d in dialogues if d.strip().endswith('?'))
    if question_count > len(dialogues) * 0.3 and dialogues:
        if "curious" not in traits:
            traits.append("curious")

    exclaim_count = sum(1 for d in dialogues if d.strip().endswith('!'))
    if exclaim_count > len(dialogues) * 0.4 and dialogues:
        traits.append("enthusiastic")

    if not traits:
        traits.append("neutral")

    return traits


def _extract_relationships(
    name: str,
    characters: list[dict],
) -> dict[str, str]:
    """Extract relationships from co-occurrence data."""
    relationships: dict[str, str] = {}
    char_data = None
    for c in characters:
        if c["name"] == name:
            char_data = c
            break

    if not char_data:
        return relationships

    co_occurring = char_data.get("co_occurring_characters", {})
    for other_name, count in co_occurring.items():
        if count >= 3:
            relationships[other_name] = "close associate"
        elif count >= 1:
            relationships[other_name] = "acquaintance"

    return relationships


def build_character_profiles(
    text: str,
    characters: list[dict],
) -> list[dict]:
    """Build detailed character profiles.

    Args:
        text: Full story text.
        characters: Character list from ``extract_characters``.

    Returns:
        List of profile dicts with keys: name, appearance,
        personality_traits, speech_patterns, typical_actions,
        relationships.
    """
    if not text or not characters:
        return []

    profiles: list[dict] = []
    nlp = _get_nlp()
    doc = nlp(text)

    for char in characters:
        name = char["name"]
        aliases = char.get("aliases", [])

        mention_positions = _find_character_mentions(text, name, aliases)

        if not mention_positions:
            profiles.append({
                "name": name,
                "appearance": [],
                "personality_traits": ["unknown"],
                "speech_patterns": "no dialogue found",
                "typical_actions": [],
                "relationships": {},
            })
            continue

        appearance = _extract_appearance(doc, mention_positions, text)
        dialogues = _extract_dialogue(text, name, aliases)
        speech_patterns = _analyze_speech_patterns(dialogues)
        actions = _extract_actions(doc, mention_positions, text)
        personality = _infer_personality(appearance, actions, dialogues)
        relationships = _extract_relationships(name, characters)

        # Extract full description sentences (much richer than adjective extraction)
        description_sentences = _extract_description_sentences(text, name, aliases)

        profiles.append({
            "name": name,
            "appearance": appearance,
            "appearance_description": description_sentences,
            "personality_traits": personality,
            "speech_patterns": speech_patterns,
            "typical_actions": actions,
            "relationships": relationships,
            "role": char.get("role", "unknown"),
        })

    return profiles


def _extract_description_sentences(text: str, name: str, aliases: list[str]) -> list[str]:
    """Extract full sentences that describe a character's physical appearance."""
    import re

    appearance_indicators = [
        'eyes', 'hair', 'face', 'mouth', 'nose', 'voice', 'smile',
        'tall', 'short', 'thin', 'sturdy', 'slender', 'muscular',
        'wore', 'wearing', 'dress', 'suit', 'clothes', 'riding',
        'body', 'shoulder', 'hand', 'arm', 'lip', 'chin', 'skin',
        'beautiful', 'handsome', 'pretty', 'young', 'old',
        'blonde', 'dark', 'brown', 'straw', 'golden', 'grey', 'gray',
        'arrogant', 'supercilious', 'charming', 'bright',
        'looked like', 'appearance', 'looked at',
    ]

    search_terms = [name.lower()] + [a.lower() for a in aliases]
    sentences = re.split(r'(?<=[.!?])\s+', text)
    descriptions = []

    for sent in sentences:
        sent_lower = sent.lower()
        # Must mention the character
        if not any(term in sent_lower for term in search_terms):
            continue
        # Must contain appearance-related words
        if not any(word in sent_lower for word in appearance_indicators):
            continue
        # Clean up and limit length
        clean = ' '.join(sent.split())[:300]
        if len(clean) > 20:
            descriptions.append(clean)

    return descriptions[:5]  # Max 5 description sentences
