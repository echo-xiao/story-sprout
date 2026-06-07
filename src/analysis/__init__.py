"""NLP analysis modules for picture book text.

Core modules used by the pipeline:
- chapter_split: TextTiling segmentation
- visual_score: Visual concreteness scoring
- complexity: Reading level assessment
- key_events: Key event extraction
"""

from src.analysis.chapter_split import split_into_segments
from src.analysis.visual_score import score_visual_concreteness
from src.analysis.complexity import assess_complexity
from src.analysis.key_events import extract_key_events

__all__ = [
    "split_into_segments",
    "score_visual_concreteness",
    "assess_complexity",
    "extract_key_events",
]
