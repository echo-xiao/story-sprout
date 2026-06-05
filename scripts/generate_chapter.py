#!/usr/bin/env python3
"""Generate a picture book chapter from preprocessed data.

Loads preprocessed analysis and generates illustrations for a specific chapter.
All segments in the chapter become pages — no filtering.

Usage:
    # Generate chapter 0 (first chapter)
    python scripts/generate_chapter.py --book The_Great_Gatsby --chapter 0

    # Generate specific pages only
    python scripts/generate_chapter.py --book The_Great_Gatsby --chapter 0 --pages 1,2,3

    # Generate with special pages (cover, chapter cover, ending, back cover)
    python scripts/generate_chapter.py --book The_Great_Gatsby --chapter 0 --with-special

    # Generate only special pages
    python scripts/generate_chapter.py --book The_Great_Gatsby --special-only

    # Generate only the book cover
    python scripts/generate_chapter.py --book The_Great_Gatsby --cover-only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from src.config import GENERATED_DIR

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
)
logger = logging.getLogger(__name__)


def _load_preprocess(book_id: str) -> dict:
    """Load all preprocessed data for a book."""
    preprocess_dir = GENERATED_DIR / book_id / "preprocess"
    if not preprocess_dir.exists():
        print(f"Error: No preprocessed data found at {preprocess_dir}")
        print(f"Run: python scripts/preprocess_book.py --input <book_file>")
        sys.exit(1)

    data = {}
    for name in ["meta", "chapters", "full_text", "analysis", "chapter_segments"]:
        path = preprocess_dir / f"{name}.json"
        if path.exists():
            data[name] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _get_chapter_segments(data: dict, chapter_idx: int) -> tuple[list[dict], str]:
    """Get segments for a specific chapter. Returns (segments, chapter_title)."""
    analysis = data.get("analysis", {})
    all_segments = analysis.get("segments", [])
    chapter_segments_map = data.get("chapter_segments", {})
    chapters = data.get("chapters", [])

    ch_info = chapter_segments_map.get(str(chapter_idx), {})
    ch_title = ch_info.get("chapter_title", f"Chapter {chapter_idx + 1}")
    seg_ids = set(ch_info.get("segment_ids", []))

    if seg_ids:
        segments = [s for s in all_segments if s.get("id") in seg_ids]
    else:
        # Fallback: use all segments
        segments = all_segments

    return segments, ch_title


def generate_special_pages(book_id: str, data: dict, chapter_idx: int | None = None):
    """Generate special page illustrations."""
    from src.generation.special_pages import (
        generate_book_cover, generate_chapter_cover,
        generate_chapter_ending, generate_back_cover,
    )
    from src.generation.character_sheet import _assign_visual_identities

    meta = data.get("meta", {})
    title = meta.get("title", "Untitled")
    analysis = data.get("analysis", {})
    characters = analysis.get("characters", [])
    profiles = analysis.get("character_profiles", [])

    # Assign visual identities to characters
    main_chars = [p for p in profiles if p.get("role") in ("main", "supporting")][:5]
    if not main_chars:
        main_chars = profiles[:5]
    main_chars = _assign_visual_identities(main_chars)

    print(f"\n--- Generating special pages ---")

    # Book cover
    print("  Generating book cover...")
    cover_path = generate_book_cover(title, main_chars, book_id)
    print(f"  Book cover: {cover_path}")

    # Back cover
    print("  Generating back cover...")
    back_path = generate_back_cover(title, book_id)
    print(f"  Back cover: {back_path}")

    # Chapter-specific pages
    if chapter_idx is not None:
        _, ch_title = _get_chapter_segments(data, chapter_idx)
        segments, _ = _get_chapter_segments(data, chapter_idx)

        # Chapter cover
        summary = segments[0].get("text", "")[:200] if segments else ""
        print(f"  Generating chapter {chapter_idx} cover...")
        ch_cover = generate_chapter_cover(ch_title, chapter_idx + 1, summary, main_chars, book_id)
        print(f"  Chapter cover: {ch_cover}")

        # Chapter ending
        ending_text = segments[-1].get("text", "")[:200] if segments else ""
        print(f"  Generating chapter {chapter_idx} ending...")
        ch_ending = generate_chapter_ending(ch_title, chapter_idx + 1, ending_text, main_chars, book_id)
        print(f"  Chapter ending: {ch_ending}")


def generate_chapter(
    book_id: str,
    data: dict,
    chapter_idx: int,
    page_filter: list[int] | None = None,
    age_group: str = "4-6",
    with_special: bool = False,
):
    """Generate all pages for a chapter."""
    from src.agent.text_simplifier import simplify_text
    from src.agent.illustration_prompter import generate_illustration_prompts
    from src.generation.character_sheet import generate_character_sheets, _assign_visual_identities
    from src.generation.illustration import generate_illustrations
    from src.renderer.pdf_export import export_pdf

    # Step logger for saving intermediate results
    steps_dir = GENERATED_DIR / book_id / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)
    _step_num = [0]

    def _save_step(name: str, data_to_save: any, duration_s: float = 0):
        _step_num[0] += 1
        step_file = steps_dir / f"{_step_num[0]:02d}_{name}.json"
        doc = {
            "step": _step_num[0],
            "name": name,
            "duration_s": round(duration_s, 1),
        }
        # Handle different data types
        if isinstance(data_to_save, (dict, list)):
            doc["data"] = data_to_save
        else:
            doc["data"] = str(data_to_save)
        # Truncate large text fields for readability
        import copy
        truncated = copy.deepcopy(doc)
        _truncate(truncated, max_str=2000)
        step_file.write_text(
            json.dumps(truncated, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  [saved] {step_file.name}")

    def _truncate(obj, max_str=2000):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and len(v) > max_str:
                    obj[k] = v[:max_str] + f"... ({len(v)} chars)"
                else:
                    _truncate(v, max_str)
        elif isinstance(obj, list):
            for item in obj:
                _truncate(item, max_str)

    analysis = data.get("analysis", {})
    characters = analysis.get("characters", [])
    profiles = analysis.get("character_profiles", [])
    full_text = data.get("full_text", {}).get("text", "")
    meta = data.get("meta", {})
    title = meta.get("title", "Untitled")

    segments, ch_title = _get_chapter_segments(data, chapter_idx)
    print(f"\n=== Generating Chapter {chapter_idx}: {ch_title} ===")
    print(f"  Segments: {len(segments)}")

    # Build scenes from ALL segments (no filtering)
    scenes = []
    char_names = [c["name"] for c in characters[:10]]
    for i, seg in enumerate(segments):
        seg_text = seg.get("text", "")
        if len(seg_text.split()) < 10:
            continue
        text_lower = seg_text.lower()
        present_chars = [n for n in char_names if n.lower() in text_lower]
        sentences = [s.strip() for s in seg_text.replace("\n", " ").split(".") if s.strip()]
        summary = ". ".join(sentences[:2]) + "." if sentences else seg_text[:200]

        scenes.append({
            "page_number": len(scenes) + 1,
            "source_segment_id": seg.get("id", i),
            "scene_summary": summary[:300],
            "key_characters": present_chars[:5],
            "original_text": seg_text,
        })

    print(f"  Pages to generate: {len(scenes)}")

    # Filter pages if specified
    if page_filter:
        scenes = [s for s in scenes if s["page_number"] in page_filter]
        print(f"  Filtered to pages: {page_filter}")

    if not scenes:
        print("  No pages to generate.")
        return

    # Step 1: Character sheets — only for characters appearing in THIS chapter
    # Find which characters appear in this chapter's text
    chapter_text_lower = " ".join(s.get("original_text", "") for s in scenes).lower()
    chapter_chars = []
    for p in profiles:
        name = p.get("name", "")
        if not name:
            continue
        # Check if character name or first name appears in chapter text
        name_lower = name.lower()
        first_name = name_lower.split()[0]
        if name_lower in chapter_text_lower or (len(first_name) >= 3 and first_name in chapter_text_lower):
            chapter_chars.append(p)

    print(f"\n[1/5] Characters in this chapter: {len(chapter_chars)}")
    for c in chapter_chars:
        print(f"    - {c.get('name')} ({c.get('role', '?')})")

    # Generate sheets only for chapter characters (reuse existing ones)
    from src.generation.character_sheet import _safe_filename
    ch_dir = GENERATED_DIR / book_id / "characters"
    chapter_chars = _assign_visual_identities(chapter_chars)
    character_sheets = []
    chars_to_generate = []

    for p in chapter_chars:
        safe = _safe_filename(p.get("name", ""))
        existing = None
        for ext in (".png", ".jpg"):
            sheet_path = ch_dir / f"{safe}_sheet{ext}"
            if sheet_path.exists():
                existing = str(sheet_path)
                break
        if existing:
            character_sheets.append({
                "character_name": p["name"],
                "sheet_path": existing,
                "visual_identity": p.get("visual_identity", ""),
                "background": p.get("background", ""),
            })
        else:
            chars_to_generate.append(p)

    if chars_to_generate:
        print(f"  Generating {len(chars_to_generate)} new sheets (reusing {len(character_sheets)} existing)...")
        t0 = time.time()
        new_sheets = generate_character_sheets(chars_to_generate, book_id)
        character_sheets.extend(new_sheets)
        dt = time.time() - t0
        print(f"  Generated {len(new_sheets)} sheets in {dt:.1f}s")
    else:
        dt = 0
        print(f"  All {len(character_sheets)} sheets already exist")
    _save_step("character_sheets", [
        {"name": s["character_name"], "path": s.get("sheet_path", ""),
         "visual_identity": s.get("visual_identity", ""), "background": s.get("background", "")}
        for s in character_sheets
    ], dt)

    # Step 1.5: Generate chapter story outline (LLM) for coherent rewriting
    print(f"\n[1.5/5] Generating chapter story outline...")
    t0 = time.time()
    from src.agent.gemini_client import generate_json as _gen_json
    chapter_original = "\n".join(s.get("original_text", "")[:500] for s in scenes)
    char_list = ", ".join(c.get("name", "") for c in chapter_chars[:8])
    outline_prompt = f"""You are adapting a chapter of a classic novel into a children's picture book.

CHAPTER: {ch_title}
BOOK: {title}
CHARACTERS IN THIS CHAPTER: {char_list}

CHAPTER TEXT (excerpts from {len(scenes)} segments):
{chapter_original[:8000]}

Generate a coherent STORY OUTLINE for this chapter as a children's picture book.
The outline must:
1. Start with a brief SETUP that introduces the setting and characters (even if the reader hasn't read previous chapters)
2. Have a clear beginning, middle, and end within this chapter
3. Be understandable by a child who has NEVER read this book before
4. Include {len(scenes)} story beats (one per page)

Return JSON:
{{"chapter_summary": "2-3 sentence summary of what happens",
  "setup_context": "1-2 sentences a child needs to know before this chapter starts",
  "famous_quotes": ["any famous/iconic lines from this chapter that should be preserved (adapted for children)"],
  "story_beats": [
    {{"page": 1, "beat": "what happens on this page in 1 sentence"}}
  ]
}}

IMPORTANT: If this chapter contains famous or iconic quotes (e.g., "It was the best of times, it was the worst of times"), include them in famous_quotes and work them into the appropriate story beat."""
    try:
        outline = _gen_json(outline_prompt)
    except Exception as e:
        logger.warning("Outline generation failed: %s", e)
        outline = {"chapter_summary": "", "setup_context": "", "story_beats": []}
    dt = time.time() - t0
    print(f"  Summary: {outline.get('chapter_summary', 'N/A')[:120]}")
    print(f"  Setup: {outline.get('setup_context', 'N/A')[:120]}")
    print(f"  Done in {dt:.1f}s")
    _save_step("chapter_outline", outline, dt)

    # Inject outline context into scenes for better rewriting
    setup_context = outline.get("setup_context", "")
    story_beats = outline.get("story_beats", [])
    famous_quotes = outline.get("famous_quotes", [])
    beat_map = {b.get("page", 0): b.get("beat", "") for b in story_beats}
    for s in scenes:
        pn = s.get("page_number", 0)
        s["story_beat"] = beat_map.get(pn, "")
        if pn == 1:
            if setup_context:
                s["setup_context"] = setup_context
            if famous_quotes:
                s["famous_quotes"] = famous_quotes

    # Step 2: Simplify text
    print(f"\n[2/5] Simplifying text ({len(scenes)} pages)...")
    t0 = time.time()
    simplified = simplify_text(scenes, age_group, full_text, characters=profiles[:5])
    dt = time.time() - t0
    print(f"  Done in {dt:.1f}s")
    _save_step("simplified_text", [
        {"page": s.get("page_number"), "text": s.get("page_text", ""),
         "scene_direction": s.get("scene_direction", "")}
        for s in simplified
    ], dt)

    # Step 3: Generate illustration prompts
    print(f"\n[3/5] Generating illustration prompts...")
    t0 = time.time()
    sheet_profiles = [
        {
            "name": s["character_name"],
            "visual_identity": s.get("visual_identity", ""),
            "visual_description": s.get("visual_identity", ""),
        }
        for s in character_sheets
    ]
    prompts_result = generate_illustration_prompts(simplified, sheet_profiles)
    page_prompts = prompts_result.get("page_prompts", [])
    dt = time.time() - t0
    print(f"  Generated {len(page_prompts)} prompts in {dt:.1f}s")
    _save_step("illustration_prompts", [
        {"page": p.get("page_number"), "prompt": p.get("prompt", "")[:300]}
        for p in page_prompts
    ], dt)

    # Merge scene data into page prompts
    scene_map = {s.get("page_number", i + 1): s for i, s in enumerate(simplified)}
    for pp in page_prompts:
        pn = pp.get("page_number", 0)
        scene = scene_map.get(pn, {})
        pp["text"] = scene.get("page_text", scene.get("text", ""))
        pp["scene_direction"] = scene.get("scene_direction", "")
        pp["key_characters"] = scene.get("key_characters", [])

    # Step 4: Generate illustrations
    print(f"\n[4/5] Generating illustrations ({len(page_prompts)} pages)...")
    t0 = time.time()
    illustrations = generate_illustrations(page_prompts, character_sheets, book_id)
    dt = time.time() - t0
    print(f"  Generated {len(illustrations)} illustrations in {dt:.1f}s")
    _save_step("illustrations", [
        {"page": ill.get("page_number"), "path": ill.get("image_path", ""),
         "consistency": ill.get("consistency_score", -1)}
        for ill in illustrations
    ], dt)

    # Step 5: Special pages + PDF
    if with_special:
        print(f"\n[5/5] Generating special pages + PDF...")
        t0 = time.time()
        generate_special_pages(book_id, data, chapter_idx)
        _save_step("special_pages", {"chapter": chapter_idx, "with_special": True}, time.time() - t0)
    else:
        print(f"\n[5/5] Generating PDF...")

    # Build PDF pages
    pdf_pages = []
    for idx, scene in enumerate(simplified):
        ill = illustrations[idx] if idx < len(illustrations) else {}
        pdf_pages.append({
            "text": scene.get("page_text", scene.get("text", "")),
            "image_path": ill.get("image_path", ""),
        })

    special_dir = str(GENERATED_DIR / book_id / "special")
    pdf_path = str(GENERATED_DIR / book_id / "book.pdf")
    export_pdf(
        pdf_pages, f"{title} - {ch_title}", pdf_path,
        special_dir=special_dir,
        chapter_num=chapter_idx + 1,
    )
    _save_step("pdf_export", {"path": pdf_path, "num_pages": len(pdf_pages)})
    print(f"\n=== Done! ===")
    print(f"  PDF: {pdf_path}")
    print(f"  Pages: {len(pdf_pages)}")


def main():
    parser = argparse.ArgumentParser(description="Generate a picture book chapter.")
    parser.add_argument("--book", required=True, help="Book ID (folder name in data/generated/)")
    parser.add_argument("--chapter", type=int, default=None, help="Chapter index (0-based)")
    parser.add_argument("--pages", type=str, default=None, help="Comma-separated page numbers to generate")
    parser.add_argument("--age", type=str, default="4-6", help="Target age group")
    parser.add_argument("--with-special", action="store_true", help="Also generate special pages")
    parser.add_argument("--special-only", action="store_true", help="Only generate special pages")
    parser.add_argument("--cover-only", action="store_true", help="Only generate book cover")
    args = parser.parse_args()

    data = _load_preprocess(args.book)

    if args.cover_only:
        from src.generation.special_pages import generate_book_cover
        from src.generation.character_sheet import _assign_visual_identities
        analysis = data.get("analysis", {})
        profiles = analysis.get("character_profiles", [])
        main_chars = [p for p in profiles if p.get("role") in ("main", "supporting")][:5]
        if not main_chars:
            main_chars = profiles[:5]
        main_chars = _assign_visual_identities(main_chars)
        title = data.get("meta", {}).get("title", "Untitled")
        print("Generating book cover...")
        path = generate_book_cover(title, main_chars, args.book)
        print(f"Cover saved: {path}")
        return

    if args.special_only:
        generate_special_pages(args.book, data, args.chapter)
        return

    if args.chapter is None:
        print("Error: --chapter is required (unless using --special-only or --cover-only)")
        sys.exit(1)

    page_filter = None
    if args.pages:
        page_filter = [int(p.strip()) for p in args.pages.split(",")]

    generate_chapter(
        args.book, data, args.chapter,
        page_filter=page_filter,
        age_group=args.age,
        with_special=args.with_special,
    )


if __name__ == "__main__":
    main()
