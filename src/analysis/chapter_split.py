"""TextTiling-based text segmentation with rule-based fallback."""

from __future__ import annotations

import re
import math
from typing import Optional


def _tokenize_sentences(text: str) -> list[str]:
    """Split text into sentences using simple regex."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s for s in sentences if s.strip()]


def _compute_block_scores(
    sentences: list[str],
    block_size: int = 3,
) -> list[float]:
    """Compute vocabulary similarity scores between adjacent blocks.

    Uses a simplified TextTiling approach: compare word overlap between
    consecutive blocks of sentences.
    """
    if len(sentences) < 2 * block_size:
        return []

    def _word_set(sents: list[str]) -> dict[str, int]:
        freq: dict[str, int] = {}
        for s in sents:
            for w in re.findall(r'\b\w+\b', s.lower()):
                freq[w] = freq.get(w, 0) + 1
        return freq

    scores: list[float] = []
    for i in range(block_size, len(sentences) - block_size + 1):
        left = _word_set(sentences[i - block_size : i])
        right = _word_set(sentences[i : i + block_size])
        # Cosine similarity
        all_words = set(left) | set(right)
        if not all_words:
            scores.append(0.0)
            continue
        dot = sum(left.get(w, 0) * right.get(w, 0) for w in all_words)
        mag_l = math.sqrt(sum(v * v for v in left.values()))
        mag_r = math.sqrt(sum(v * v for v in right.values()))
        if mag_l == 0 or mag_r == 0:
            scores.append(0.0)
        else:
            scores.append(dot / (mag_l * mag_r))
    return scores


def _find_boundaries(scores: list[float], threshold_factor: float = 0.6) -> list[int]:
    """Identify segment boundaries where similarity drops below threshold."""
    if not scores:
        return []
    mean = sum(scores) / len(scores)
    std = math.sqrt(sum((s - mean) ** 2 for s in scores) / len(scores)) if len(scores) > 1 else 0.0
    threshold = mean - threshold_factor * std

    boundaries: list[int] = []
    for i, score in enumerate(scores):
        if score < threshold:
            # Check it's a local minimum
            is_min = True
            if i > 0 and scores[i - 1] < score:
                is_min = False
            if i < len(scores) - 1 and scores[i + 1] < score:
                is_min = False
            if is_min:
                boundaries.append(i)
    return boundaries


def _rule_based_split(text: str) -> list[str]:
    """Fallback: split on blank lines, chapter headings, or length thresholds."""
    # Try chapter/heading patterns first
    heading_pattern = re.compile(
        r'^(?:chapter\s+\w+|part\s+\w+|#{1,3}\s+.+|\d+\.\s+.+)$',
        re.IGNORECASE | re.MULTILINE,
    )
    headings = list(heading_pattern.finditer(text))
    if headings:
        segments: list[str] = []
        for i, match in enumerate(headings):
            start = match.start()
            end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            seg = text[start:end].strip()
            if seg:
                segments.append(seg)
        # Include any text before the first heading
        pre = text[: headings[0].start()].strip()
        if pre:
            segments.insert(0, pre)
        return segments

    # Split on blank lines
    parts = re.split(r'\n\s*\n', text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) >= 2:
        return parts

    # Length-based split: aim for ~200-word chunks
    words = text.split()
    if len(words) <= 50:
        return [text.strip()]
    target = 200
    segments = []
    current: list[str] = []
    for word in words:
        current.append(word)
        if len(current) >= target and word.endswith(('.', '!', '?')):
            segments.append(' '.join(current))
            current = []
    if current:
        segments.append(' '.join(current))
    return segments


def _extract_title(segment_text: str) -> Optional[str]:
    """Try to extract a title from the first line of a segment."""
    lines = segment_text.strip().split('\n')
    if not lines:
        return None
    first = lines[0].strip()
    heading = re.match(
        r'^(?:chapter\s+\w+[:\s]*(.*)|(#{1,3})\s+(.+)|\d+\.\s+(.+))$',
        first,
        re.IGNORECASE,
    )
    if heading:
        return (heading.group(1) or heading.group(3) or heading.group(4) or first).strip() or first
    # Short first line might be a title
    if len(first) < 60 and len(lines) > 1 and not first.endswith(('.', ',', ';')):
        return first
    return None


def split_into_segments(
    text: str,
    chapters: list[dict] | None = None,
) -> list[dict]:
    """Split text into semantically coherent segments.

    Args:
        text: The full text to segment.
        chapters: Optional pre-defined chapter boundaries, each with
                  at least ``text`` and optionally ``title``.

    Returns:
        List of segment dicts with keys: id, text, title, start_char, end_char.
    """
    if not text or not text.strip():
        return []

    # If chapters are provided, use them directly
    if chapters:
        segments: list[dict] = []
        for i, ch in enumerate(chapters):
            ch_text = ch.get("text", "")
            start = text.find(ch_text) if ch_text else -1
            if start == -1:
                start = 0
            segments.append({
                "id": i,
                "text": ch_text,
                "title": ch.get("title"),
                "start_char": start,
                "end_char": start + len(ch_text),
            })
        return segments

    # Try TextTiling first
    sentences = _tokenize_sentences(text)
    block_size = min(3, max(1, len(sentences) // 6))
    scores = _compute_block_scores(sentences, block_size=block_size)
    boundaries = _find_boundaries(scores)

    if boundaries and len(sentences) >= 6:
        # Map sentence-level boundaries back to text segments
        raw_segments: list[str] = []
        # Boundaries are offset by block_size in the sentence list
        boundary_indices = [b + block_size for b in boundaries]
        prev = 0
        for bi in boundary_indices:
            if prev < bi <= len(sentences):
                seg = ' '.join(sentences[prev:bi])
                if seg.strip():
                    raw_segments.append(seg)
                prev = bi
        remaining = ' '.join(sentences[prev:])
        if remaining.strip():
            raw_segments.append(remaining)
    else:
        # Fallback to rule-based
        raw_segments = _rule_based_split(text)

    # Build result dicts with char offsets
    result: list[dict] = []
    search_start = 0
    for i, seg_text in enumerate(raw_segments):
        # Find approximate position in original text
        # Use first few words to locate
        first_words = seg_text.split()[:5]
        search_key = ' '.join(first_words) if first_words else seg_text[:30]
        pos = text.find(search_key, search_start)
        if pos == -1:
            pos = search_start

        # Find end by looking for last words
        last_words = seg_text.split()[-5:]
        end_key = ' '.join(last_words) if last_words else seg_text[-30:]
        end_pos = text.find(end_key, pos)
        if end_pos == -1:
            end_char = pos + len(seg_text)
        else:
            end_char = end_pos + len(end_key)

        title = _extract_title(seg_text)

        result.append({
            "id": i,
            "text": seg_text,
            "title": title,
            "start_char": pos,
            "end_char": end_char,
        })
        search_start = max(search_start + 1, pos + 1)

    return result
