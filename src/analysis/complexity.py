"""Text complexity assessment with Flesch-Kincaid and word frequency analysis."""

from __future__ import annotations

import re
import math


# Common word list (top ~2000 English words approximation)
# We use a frequency-based heuristic instead of bundling a full list.
_COMMON_PREFIXES = frozenset({
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i",
    "it", "for", "not", "on", "with", "he", "as", "you", "do", "at",
    "this", "but", "his", "by", "from", "they", "we", "say", "her",
    "she", "or", "an", "will", "my", "one", "all", "would", "there",
    "their", "what", "so", "up", "out", "if", "about", "who", "get",
    "which", "go", "me", "when", "make", "can", "like", "time", "no",
    "just", "him", "know", "take", "people", "into", "year", "your",
    "good", "some", "could", "them", "see", "other", "than", "then",
    "now", "look", "only", "come", "its", "over", "think", "also",
    "back", "after", "use", "two", "how", "our", "work", "first",
    "well", "way", "even", "new", "want", "because", "any", "these",
    "give", "day", "most", "us", "is", "was", "are", "were", "been",
    "has", "had", "did", "said", "got", "went", "came", "made", "took",
    "very", "much", "too", "here", "where", "why", "let", "put", "old",
    "man", "woman", "child", "boy", "girl", "house", "home", "hand",
    "head", "eye", "face", "door", "room", "water", "long", "little",
    "big", "great", "high", "small", "large", "next", "early", "young",
    "right", "left", "last", "own", "never", "still", "down", "may",
    "should", "call", "world", "life", "tell", "ask", "try", "need",
    "feel", "become", "leave", "seem", "help", "show", "hear", "play",
    "run", "move", "live", "find", "stand", "own", "turn", "keep",
    "begin", "seem", "help", "start", "might", "part", "place", "end",
    "love", "each", "hold", "thing", "open", "point", "set", "every",
    "read", "sit", "walk", "eat", "sleep", "stop", "name", "city",
    "tree", "dog", "cat", "bird", "fish", "sun", "moon", "star",
    "book", "story", "friend", "family", "mother", "father",
    "brother", "sister", "baby", "animal", "flower", "garden",
    "school", "color", "red", "blue", "green", "happy", "sad",
})


def _count_syllables(word: str) -> int:
    """Estimate syllable count for English words."""
    word = word.lower().strip()
    if not word:
        return 0
    if len(word) <= 2:
        return 1

    # Remove trailing silent e
    if word.endswith('e') and len(word) > 2:
        word = word[:-1]

    # Count vowel groups
    count = 0
    prev_vowel = False
    vowels = set('aeiouAEIOU')
    for char in word:
        is_vowel = char in vowels
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel

    return max(1, count)


def _tokenize_words(text: str) -> list[str]:
    """Extract words from text."""
    return re.findall(r"[a-zA-Z']+", text)


def _tokenize_sentences(text: str) -> list[str]:
    """Split into sentences."""
    sentences = re.split(r'[.!?]+', text)
    return [s.strip() for s in sentences if s.strip()]


def assess_complexity(text: str) -> dict:
    """Assess reading complexity of text.

    Args:
        text: The text to analyze.

    Returns:
        Dict with keys: flesch_kincaid_grade, avg_sentence_length,
        avg_word_length, rare_word_ratio, difficult_segments.
    """
    if not text or not text.strip():
        return {
            "flesch_kincaid_grade": 0.0,
            "avg_sentence_length": 0.0,
            "avg_word_length": 0.0,
            "rare_word_ratio": 0.0,
            "difficult_segments": [],
        }

    words = _tokenize_words(text)
    sentences = _tokenize_sentences(text)

    if not words or not sentences:
        return {
            "flesch_kincaid_grade": 0.0,
            "avg_sentence_length": 0.0,
            "avg_word_length": 0.0,
            "rare_word_ratio": 0.0,
            "difficult_segments": [],
        }

    total_words = len(words)
    total_sentences = len(sentences)
    total_syllables = sum(_count_syllables(w) for w in words)

    # Flesch-Kincaid Grade Level
    avg_sentence_length = total_words / total_sentences
    avg_syllables_per_word = total_syllables / total_words
    fk_grade = (
        0.39 * avg_sentence_length
        + 11.8 * avg_syllables_per_word
        - 15.59
    )
    fk_grade = round(max(0.0, fk_grade), 2)

    # Average word length in characters
    avg_word_length = round(
        sum(len(w) for w in words) / total_words, 2
    )

    # Rare word ratio
    rare_count = sum(
        1 for w in words
        if w.lower() not in _COMMON_PREFIXES and len(w) > 2
    )
    rare_word_ratio = round(rare_count / total_words, 3)

    # Identify difficult segments (sentences with high complexity)
    difficult_segments: list[int] = []
    for i, sent in enumerate(sentences):
        sent_words = _tokenize_words(sent)
        if not sent_words:
            continue
        sent_syllables = sum(_count_syllables(w) for w in sent_words)
        sent_avg_syl = sent_syllables / len(sent_words)
        # Flag if sentence is long or uses complex words
        if len(sent_words) > 15 or sent_avg_syl > 2.0:
            difficult_segments.append(i)

    return {
        "flesch_kincaid_grade": fk_grade,
        "avg_sentence_length": round(avg_sentence_length, 2),
        "avg_word_length": avg_word_length,
        "rare_word_ratio": rare_word_ratio,
        "difficult_segments": difficult_segments,
    }
