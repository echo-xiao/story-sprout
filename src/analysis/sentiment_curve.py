"""Sentiment analysis per segment with arc detection."""

from __future__ import annotations

from textblob import TextBlob

try:
    from scipy.signal import find_peaks
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


def _simple_find_peaks(values: list[float], min_prominence: float = 0.1) -> list[int]:
    """Fallback peak detection without scipy."""
    peaks: list[int] = []
    for i in range(1, len(values) - 1):
        if values[i] > values[i - 1] and values[i] > values[i + 1]:
            # Check prominence
            left_min = min(values[:i]) if i > 0 else values[0]
            right_min = min(values[i + 1:]) if i < len(values) - 1 else values[-1]
            prominence = values[i] - max(left_min, right_min)
            if prominence >= min_prominence:
                peaks.append(i)
    return peaks


def _classify_arc(scores: list[float]) -> str:
    """Classify the overall narrative arc shape.

    Common arcs:
    - "rising": generally increases
    - "falling": generally decreases
    - "peak": rises then falls (classic story arc)
    - "valley": falls then rises (redemption arc)
    - "flat": minimal variation
    """
    if len(scores) < 2:
        return "flat"

    n = len(scores)
    first_half = scores[: n // 2]
    second_half = scores[n // 2 :]

    avg_first = sum(first_half) / len(first_half)
    avg_second = sum(second_half) / len(second_half)
    overall_range = max(scores) - min(scores)

    if overall_range < 0.1:
        return "flat"

    # Find where the peak and valley are
    peak_idx = scores.index(max(scores))
    valley_idx = scores.index(min(scores))
    mid = n / 2

    if peak_idx < mid * 0.7 and valley_idx > mid * 1.3:
        return "falling"
    if valley_idx < mid * 0.7 and peak_idx > mid * 1.3:
        return "rising"
    if peak_idx > n * 0.25 and peak_idx < n * 0.75:
        return "peak"
    if valley_idx > n * 0.25 and valley_idx < n * 0.75:
        return "valley"
    if avg_second > avg_first + 0.05:
        return "rising"
    if avg_first > avg_second + 0.05:
        return "falling"
    return "flat"


def analyze_sentiment(segments: list[dict]) -> dict:
    """Analyze sentiment across story segments.

    Args:
        segments: List of segment dicts (must have ``text`` key).

    Returns:
        Dict with keys: scores, peaks, valleys, overall_arc.
    """
    if not segments:
        return {
            "scores": [],
            "peaks": [],
            "valleys": [],
            "overall_arc": "flat",
        }

    # Score each segment
    scores: list[float] = []
    for seg in segments:
        blob = TextBlob(seg.get("text", ""))
        scores.append(blob.sentiment.polarity)

    # Detect peaks and valleys
    if len(scores) < 3:
        peaks: list[int] = []
        valleys: list[int] = []
    elif _HAS_SCIPY:
        peak_indices, _ = find_peaks(scores, prominence=0.05, distance=1)
        peaks = peak_indices.tolist()
        # Invert scores to find valleys
        inverted = [-s for s in scores]
        valley_indices, _ = find_peaks(inverted, prominence=0.05, distance=1)
        valleys = valley_indices.tolist()
    else:
        peaks = _simple_find_peaks(scores, min_prominence=0.05)
        inverted = [-s for s in scores]
        valleys = _simple_find_peaks(inverted, min_prominence=0.05)

    arc = _classify_arc(scores)

    return {
        "scores": scores,
        "peaks": peaks,
        "valleys": valleys,
        "overall_arc": arc,
    }
