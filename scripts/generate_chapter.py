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

    # Step 1: Character sheets (check if already exist)
    ch_dir = GENERATED_DIR / book_id / "characters"
    existing_sheets = list(ch_dir.glob("*_sheet.*")) if ch_dir.exists() else []

    if existing_sheets:
        print(f"\n[1/5] Character sheets: using {len(existing_sheets)} existing sheets")
        # Load sheet info
        main_chars = [p for p in profiles if p.get("role") in ("main", "supporting")][:5]
        if not main_chars:
            main_chars = profiles[:5]
        main_chars = _assign_visual_identities(main_chars)
        character_sheets = []
        for p in main_chars:
            from src.generation.character_sheet import _safe_filename
            safe = _safe_filename(p.get("name", ""))
            for ext in (".png", ".jpg"):
                sheet_path = ch_dir / f"{safe}_sheet{ext}"
                if sheet_path.exists():
                    character_sheets.append({
                        "character_name": p["name"],
                        "sheet_path": str(sheet_path),
                        "visual_identity": p.get("visual_identity", ""),
                    })
                    break
    else:
        print(f"\n[1/5] Generating character sheets...")
        t0 = time.time()
        character_sheets = generate_character_sheets(profiles, book_id)
        print(f"  Generated {len(character_sheets)} sheets in {time.time() - t0:.1f}s")

    # Step 2: Simplify text
    print(f"\n[2/5] Simplifying text ({len(scenes)} pages)...")
    t0 = time.time()
    simplified = simplify_text(scenes, age_group, full_text, characters=profiles[:5])
    print(f"  Done in {time.time() - t0:.1f}s")

    # Step 3: Generate illustration prompts
    print(f"\n[3/5] Generating illustration prompts...")
    t0 = time.time()
    # Convert sheets to profile format for prompter
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
    print(f"  Generated {len(page_prompts)} prompts in {time.time() - t0:.1f}s")

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
    print(f"  Generated {len(illustrations)} illustrations in {time.time() - t0:.1f}s")

    # Step 5: Special pages + PDF
    if with_special:
        print(f"\n[5/5] Generating special pages + PDF...")
        generate_special_pages(book_id, data, chapter_idx)
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

    # Get cover image
    cover_image = ""
    special_dir = GENERATED_DIR / book_id / "special"
    for ext in (".png", ".jpg"):
        candidate = special_dir / f"book_cover{ext}"
        if candidate.exists():
            cover_image = str(candidate)
            break

    pdf_path = str(GENERATED_DIR / book_id / "book.pdf")
    export_pdf(pdf_pages, f"{title} - {ch_title}", pdf_path, cover_image=cover_image)
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
