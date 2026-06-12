from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# Patterns that indicate chapter boundaries
_CHAPTER_PATTERNS = [
    re.compile(r"^(chapter\s+\d+[.:]?\s*.*)", re.IGNORECASE),
    re.compile(r"^(chapter\s+[ivxlcdm]+[.:]?\s*.*)", re.IGNORECASE),
    re.compile(r"^(part\s+\d+[.:]?\s*.*)", re.IGNORECASE),
    re.compile(r"^(part\s+[ivxlcdm]+[.:]?\s*.*)", re.IGNORECASE),
    re.compile(r"^(\d+\.\s+\S.*)"),  # "1. Title"
    re.compile(r"^(\d+\)\s+\S.*)"),  # "1) Title"
]

# Standalone Roman numerals as chapter markers, valid for 1-39 (I..XXXIX).
# The old alternation capped at XV (plus bare XX/XXX), so chapters XVI+ were
# not detected and silently merged into the previous chapter. The lookahead
# rejects the empty string; tens then units composition rejects invalid forms
# like IIII, VX or XXXX.
_ROMAN_NUMERAL_PATTERN = re.compile(r"^(?=[IVX])(X{0,3})(IX|IV|V?I{0,3})$")

# Blank-line threshold: N+ consecutive blank lines suggest a section break
_BLANK_LINE_BREAK = re.compile(r"\n{4,}")


def parse(source: str | Path) -> dict[str, Any]:
    source_str = str(source)
    is_file = False
    if len(source_str) < 500:
        try:
            path = Path(source_str)
            if path.exists() and path.is_file():
                is_file = True
        except OSError:
            pass

    if is_file:
        text = path.read_text(encoding="utf-8")
        title = path.stem
    else:
        text = source_str
        title = _extract_title_from_text(text)

    chapters = _detect_chapters(text)

    if not chapters:
        chapters = [
            {
                "title": title,
                "text": text.strip(),
                "page_range": (0, 0),
            }
        ]

    return {
        "title": title,
        "chapters": chapters,
        "full_text": text,
    }


def _extract_title_from_text(text: str) -> str:
    for line in text.split("\n"):
        line = line.strip()
        if line and 3 < len(line) < 120:
            return line
    return "Untitled"


def _detect_chapters(text: str) -> list[dict[str, Any]]:
    lines = text.split("\n")

    # First pass: detect chapters via heading patterns
    chapter_indices: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        for pattern in _CHAPTER_PATTERNS:
            m = pattern.match(stripped)
            if m:
                chapter_indices.append((i, m.group(1).strip()))
                break

    if len(chapter_indices) >= 2:
        return _build_chapters_from_indices(lines, chapter_indices)

    # Second pass: standalone Roman numerals (I, II, III, ...) with blank lines around them
    roman_indices: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        m = _ROMAN_NUMERAL_PATTERN.match(stripped)
        if m:
            prev_blank = i == 0 or not lines[i - 1].strip()
            next_blank = i >= len(lines) - 1 or not lines[i + 1].strip()
            if prev_blank and next_blank:
                title = f"Chapter {stripped}"
                roman_indices.append((i, title))

    if len(roman_indices) >= 2:
        return _build_chapters_from_indices(lines, roman_indices)

    # Second pass: fall back to blank-line heuristic
    segments = _BLANK_LINE_BREAK.split(text)
    if len(segments) >= 2:
        chapters: list[dict[str, Any]] = []
        for idx, segment in enumerate(segments):
            segment = segment.strip()
            if not segment:
                continue
            first_line = segment.split("\n", 1)[0].strip()
            chapter_title = first_line if len(first_line) < 100 else f"Section {idx + 1}"
            chapters.append(
                {
                    "title": chapter_title,
                    "text": segment,
                    "page_range": (0, 0),
                }
            )
        if len(chapters) >= 2:
            return chapters

    return []


def _build_chapters_from_indices(
    lines: list[str],
    chapter_indices: list[tuple[int, str]],
) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []

    for i, (line_idx, heading) in enumerate(chapter_indices):
        start = line_idx
        end = (
            chapter_indices[i + 1][0] - 1
            if i + 1 < len(chapter_indices)
            else len(lines) - 1
        )
        chapter_text = "\n".join(lines[start : end + 1]).strip()
        chapters.append(
            {
                "title": heading,
                "text": chapter_text,
                "page_range": (0, 0),
            }
        )

    return chapters
