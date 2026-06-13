"""Special-page records: the editable data behind covers/endings.

Special pages (book cover, chapter covers/endings, back cover) get the same
treatment as story pages: a preprocess-derived record with characters,
background and text that the editor can modify and the generators consume.

`derive_special_pages` is DETERMINISTIC (no LLM calls) so preprocess stays
cheap and books processed before this feature existed can derive the same
records on first read without re-running preprocess.

Records live in preprocess/special_pages.json as {"pages": {key: record}}
where key is "book_cover", "back_cover", or "chapter_cover:0".
"""

from __future__ import annotations

from collections import Counter

# Fields the editor may update via PUT /special/{type}.
EDITABLE_FIELDS = (
    "title_text", "subtitle_text", "scene_background",
    "scene_summary", "characters_in_scene",
)


# Book structure: ONE front cover, ONE chapter cover per chapter, ONE closing
# page (back cover). Per-chapter ending pages were cut by design.
SPECIAL_TYPES = ("book_cover", "chapter_cover", "back_cover")


def special_key(page_type: str, chapter: int | None = None) -> str:
    if page_type == "chapter_cover":
        return f"{page_type}:{int(chapter or 0)}"
    return page_type


def special_file_base(page_type: str, chapter: int | None = None) -> str | None:
    """Image-file stem for a special page. Chapter files are 1-based
    (chapter_01_* for chapter 0) to match the pipeline + PDF naming.
    The single naming authority — editor history/restore and the regen
    endpoint must agree or restores silently target the wrong file."""
    return {
        "book_cover": "book_cover",
        "chapter_cover": f"chapter_{(chapter or 0) + 1:02d}_cover",
        "back_cover": "back_cover",
    }.get(page_type)


def _top_characters(segments: list[dict], limit: int) -> list[str]:
    counts: Counter[str] = Counter()
    for seg in segments:
        for name in seg.get("characters_in_scene", []) or []:
            if name:
                counts[name] += 1
    return [name for name, _ in counts.most_common(limit)]


def _first_background(segments: list[dict]) -> str:
    for seg in segments:
        bg = (seg.get("scene_background") or "").strip()
        if bg:
            return bg
    return ""


def derive_special_pages(
    title: str,
    segments: list[dict],
    chapter_segments: dict,
    locations: list[dict] | None = None,
) -> dict[str, dict]:
    """Build default special-page records from preprocess data. Pure function."""
    locations = locations or []
    main_loc = next(iter(locations), {}) or {}
    book_bg = (
        f"{main_loc.get('name', '')}: {main_loc.get('description', '')}".strip(": ").strip()
        or _first_background(segments)
    )

    pages: dict[str, dict] = {
        "book_cover": {
            "type": "book_cover", "chapter": None,
            "title_text": title,
            "subtitle_text": "A Picture Book",
            "scene_background": book_bg,
            "scene_summary": f"Front cover for \"{title}\" — main characters together, inviting and warm.",
            "characters_in_scene": _top_characters(segments, 4),
        },
        "back_cover": {
            "type": "back_cover", "chapter": None,
            "title_text": "The End",
            "subtitle_text": "Thank you for reading!",
            "scene_background": book_bg,
            "scene_summary": "Warm farewell scene — tiny characters waving goodbye.",
            "characters_in_scene": _top_characters(segments, 3),
        },
    }

    for ch_key in sorted(chapter_segments.keys(), key=lambda x: int(x)):
        ch_idx = int(ch_key)
        ch_info = chapter_segments[ch_key] or {}
        ch_segs = sorted(
            (s for s in segments if s.get("chapter_idx") == ch_idx),
            key=lambda s: s.get("id", 0),
        )
        ch_title = ch_info.get("chapter_title", f"Chapter {ch_idx + 1}")
        first_summary = next(
            ((s.get("scene_summary") or "").strip() for s in ch_segs
             if (s.get("scene_summary") or "").strip()), "",
        )
        pages[special_key("chapter_cover", ch_idx)] = {
            "type": "chapter_cover", "chapter": ch_idx,
            "title_text": ch_title,
            "subtitle_text": f"Chapter {ch_idx + 1}",
            "scene_background": _first_background(ch_segs),
            "scene_summary": ch_info.get("chapter_summary") or first_summary,
            "characters_in_scene": _top_characters(ch_segs, 3),
        }

    return pages
