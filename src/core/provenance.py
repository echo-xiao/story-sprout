"""Provenance for per-page text so (re)generation knows what it may overwrite.

The editor and the printed book both read `simplified_text`, but it can come
from three places. Reconciling them by "non-empty == user edit, don't touch"
was wrong: preprocess PRE-FILLS simplified_text, so that heuristic mistook
robotic machine text for a user edit and the Writer's natural rewrite never
reached the editor (and "Gen chapter" looked like a no-op).

Each segment now carries `text_source`:
- "preprocess": robotic first-pass text from Layer-6 annotation. Replaceable.
- "writer":     natural text from the Writer / text_simplifier.    Replaceable.
- "user":       hand-edited in the editor. NEVER overwritten by (re)generation.

Legacy data has no `text_source`; treat non-empty legacy text as "preprocess"
(replaceable) so a re-gen can finally fix it, and empty as unset.
"""
from __future__ import annotations

TEXT_SOURCE_PREPROCESS = "preprocess"
TEXT_SOURCE_WRITER = "writer"
TEXT_SOURCE_USER = "user"

# Sources whose text a (re)generation is allowed to overwrite.
_REPLACEABLE = {TEXT_SOURCE_PREPROCESS, TEXT_SOURCE_WRITER, ""}


def is_user_edited(seg: dict) -> bool:
    """True if a human owns this page's text — generation must not overwrite it."""
    return seg.get("text_source") == TEXT_SOURCE_USER


def effective_source(seg: dict) -> str:
    """The segment's text_source, inferring legacy data.

    Legacy segments (no `text_source`) with text are treated as preprocess so a
    re-gen may replace them; without text the source is unset ("").
    """
    src = seg.get("text_source")
    if src:
        return src
    return TEXT_SOURCE_PREPROCESS if seg.get("simplified_text") else ""


def keeps_existing_text(seg: dict) -> bool:
    """Non-force Writer split: keep text the user owns or the Writer produced;
    re-simplify empty pages and robotic preprocess text."""
    return bool(seg.get("simplified_text")) and effective_source(seg) in (
        TEXT_SOURCE_USER,
        TEXT_SOURCE_WRITER,
    )
