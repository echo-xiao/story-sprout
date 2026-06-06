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
    """Generate all pages for a chapter.

    Pipeline (minimized LLM usage):
    1. Character sheets (LLM image gen, cached)
    2. Text simplification (LLM text, per-page)
    3. Illustration prompts (ALGORITHM — template-based, no LLM)
    4. Generate illustrations (LLM image gen)
    5. Special pages + PDF
    """
    from src.agent.text_simplifier import simplify_text
    from src.generation.character_sheet import generate_character_sheets, _assign_visual_identities
    from src.generation.illustration import generate_illustrations
    from src.renderer.pdf_export import export_pdf

    # Chapter-specific output directory
    chapter_dir = GENERATED_DIR / book_id / "chapters" / f"ch{chapter_idx:02d}"
    chapter_dir.mkdir(parents=True, exist_ok=True)

    # Step logger for saving intermediate results
    steps_dir = chapter_dir / "steps"
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
        # Also save to MongoDB
        try:
            import pymongo
            from src.config import MONGODB_URI, MONGODB_DB
            client = pymongo.MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3000)
            db = client[MONGODB_DB]
            mongo_doc = {
                "book_id": book_id,
                "step": _step_num[0],
                "name": name,
                "duration_s": round(duration_s, 1),
                "data": truncated.get("data"),
            }
            db.steps.update_one(
                {"book_id": book_id, "step": _step_num[0], "name": name},
                {"$set": mongo_doc}, upsert=True,
            )
            client.close()
        except Exception:
            pass  # MongoDB is best-effort
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
    # Use precomputed characters_in_scene from coreference resolution if available,
    # otherwise fall back to text search
    scenes = []
    char_names = [c["name"] for c in characters[:10]]
    for i, seg in enumerate(segments):
        seg_text = seg.get("text", "")
        if len(seg_text.split()) < 10:
            continue

        # Prefer precomputed coreference results
        present_chars = seg.get("characters_in_scene")
        if present_chars is None:
            # Fallback: text search
            text_lower = seg_text.lower()
            present_chars = [n for n in char_names if n.lower() in text_lower]

        sentences = [s.strip() for s in seg_text.replace("\n", " ").split(".") if s.strip()]
        summary = ". ".join(sentences[:2]) + "." if sentences else seg_text[:200]

        scenes.append({
            "page_number": len(scenes) + 1,
            "source_segment_id": seg.get("id", i),
            "scene_summary": seg.get("scene_summary", summary),
            "scene_background": seg.get("scene_background", ""),
            "key_characters": present_chars[:5],
            "character_actions": seg.get("character_actions", []),
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

    # Step 1: Characters from preprocess (already identified by LLM in preprocess)
    chapter_char_names = set()
    for s in scenes:
        for name in s.get("key_characters", []):
            chapter_char_names.add(name)

    chapter_chars = []
    for p in profiles:
        name = p.get("name", "")
        if name and name in chapter_char_names:
            chapter_chars.append(p)

    print(f"\n[1/4] Characters in this chapter (from preprocess): {len(chapter_chars)}")
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

    # Step 2: Simplify text (LLM, per-page to avoid mixing)
    print(f"\n[2/4] Simplifying text ({len(scenes)} pages, one at a time)...")
    t0 = time.time()
    simplified = simplify_text(scenes, age_group, characters=chapter_chars, character_sheets=character_sheets)
    dt = time.time() - t0
    print(f"  Done in {dt:.1f}s")
    _save_step("simplified_text", [
        {"page": s.get("page_number"), "text": s.get("page_text", ""),
         "scene_direction": s.get("scene_direction", "")}
        for s in simplified
    ], dt)

    # Step 3: Build illustration prompts (ALGORITHM — no LLM)
    print(f"\n[3/4] Building illustration prompts (template-based)...")
    t0 = time.time()
    page_prompts = []
    for s in simplified:
        page_prompts.append({
            "page_number": s.get("page_number", 0),
            "text": s.get("page_text", s.get("text", "")),
            "scene_description": s.get("scene_direction", s.get("scene_summary", "")),
            "scene_direction": s.get("scene_direction", ""),
            "scene_background": s.get("scene_background", ""),
            "key_characters": s.get("key_characters", []),
            "character_actions": s.get("character_actions", []),
        })
    dt = time.time() - t0
    print(f"  Built {len(page_prompts)} prompts in {dt:.1f}s")
    _save_step("illustration_prompts", [
        {"page": p.get("page_number"), "text": p.get("text", "")[:200],
         "scene": p.get("scene_direction", "")[:200]}
        for p in page_prompts
    ], dt)

    # Step 4: Generate illustrations
    print(f"\n[4/4] Generating illustrations ({len(page_prompts)} pages)...")
    t0 = time.time()
    chapter_pages_dir = chapter_dir / "pages"
    illustrations = generate_illustrations(page_prompts, character_sheets, book_id, pages_dir=chapter_pages_dir)
    dt = time.time() - t0
    print(f"  Generated {len(illustrations)} illustrations in {dt:.1f}s")
    _save_step("illustrations", [
        {"page": ill.get("page_number"), "path": ill.get("image_path", "")}
        for ill in illustrations
    ], dt)

    # Save chapter data for later PDF merge
    chapter_data = {
        "chapter_idx": chapter_idx,
        "chapter_title": ch_title,
        "pages": [],
    }
    for idx, scene in enumerate(simplified):
        ill = illustrations[idx] if idx < len(illustrations) else {}
        chapter_data["pages"].append({
            "text": scene.get("page_text", scene.get("text", "")),
            "image_path": ill.get("image_path", ""),
        })

    chapter_data_path = chapter_dir / "chapter_data.json"
    chapter_data_path.write_text(
        json.dumps(chapter_data, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Chapter data saved: {chapter_data_path}")

    # Generate chapter-specific special pages
    if with_special:
        print(f"  Generating special pages...")
        t0 = time.time()
        generate_special_pages(book_id, data, chapter_idx)
        _save_step("special_pages", {"chapter": chapter_idx}, time.time() - t0)

    # Save to MongoDB
    try:
        import pymongo
        from src.config import MONGODB_URI, MONGODB_DB
        mongo_client = pymongo.MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
        db = mongo_client[MONGODB_DB]
        book_doc = {
            "book_id": book_id,
            "title": title,
            "chapter": chapter_idx,
            "chapter_title": ch_title,
            "num_pages": len(chapter_data["pages"]),
            "pages": chapter_data["pages"],
        }
        db.books.update_one({"book_id": book_id, "chapter": chapter_idx}, {"$set": book_doc}, upsert=True)
        mongo_client.close()
        print(f"  MongoDB: saved ✓")
    except Exception:
        pass

    print(f"  Chapter {chapter_idx} done: {len(chapter_data['pages'])} pages")
    return chapter_data


def build_combined_pdf(book_id: str, data: dict, chapter_indices: list[int] | None = None):
    """Build a combined PDF from all generated chapters.

    Scans chapters/ directory for chapter_data.json files,
    combines them in order into a single PDF.
    """
    from src.renderer.pdf_export import export_pdf

    meta = data.get("meta", {})
    title = meta.get("title", "Untitled")
    chapters_root = GENERATED_DIR / book_id / "chapters"
    special_dir = str(GENERATED_DIR / book_id / "special")

    # Find all generated chapters
    chapter_dirs = sorted(chapters_root.glob("ch*"))
    if not chapter_dirs:
        print("No chapters found to combine.")
        return

    all_chapters = []
    for ch_dir in chapter_dirs:
        data_file = ch_dir / "chapter_data.json"
        if data_file.exists():
            ch_data = json.loads(data_file.read_text(encoding="utf-8"))
            ch_idx = ch_data.get("chapter_idx", 0)
            # If specific chapters requested, filter
            if chapter_indices and ch_idx not in chapter_indices:
                continue
            all_chapters.append(ch_data)

    if not all_chapters:
        print("No matching chapters found.")
        return

    # Sort by chapter index
    all_chapters.sort(key=lambda c: c.get("chapter_idx", 0))

    # Combine all pages, tagging each with its chapter number
    combined_pages = []
    chapter_nums = []
    for ch in all_chapters:
        ch_idx = ch.get("chapter_idx", 0)
        ch_num = ch_idx + 1
        chapter_nums.append(ch_num)
        for p in ch.get("pages", []):
            p["_chapter_num"] = ch_num
            combined_pages.append(p)

    pdf_path = str(GENERATED_DIR / book_id / "book.pdf")
    export_pdf(
        combined_pages, title, pdf_path,
        special_dir=special_dir,
        chapter_nums=chapter_nums,
    )

    print(f"\n=== Combined PDF ===")
    print(f"  Chapters: {[c.get('chapter_title','?') for c in all_chapters]}")
    print(f"  Total pages: {len(combined_pages)}")
    print(f"  PDF: {pdf_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate a picture book chapter.")
    parser.add_argument("--book", required=True, help="Book ID (folder name in data/generated/)")
    parser.add_argument("--chapter", type=str, default=None,
                        help="Chapter index (0-based). Comma-separated for multiple: 0,4")
    parser.add_argument("--pages", type=str, default=None, help="Comma-separated page numbers to generate")
    parser.add_argument("--age", type=str, default="4-6", help="Target age group")
    parser.add_argument("--with-special", action="store_true", help="Also generate special pages")
    parser.add_argument("--special-only", action="store_true", help="Only generate special pages")
    parser.add_argument("--cover-only", action="store_true", help="Only generate book cover")
    parser.add_argument("--pdf-only", action="store_true", help="Only rebuild PDF from existing chapters")
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
        ch = int(args.chapter) if args.chapter else None
        generate_special_pages(args.book, data, ch)
        return

    if args.pdf_only:
        ch_list = [int(c.strip()) for c in args.chapter.split(",")] if args.chapter else None
        build_combined_pdf(args.book, data, ch_list)
        return

    if args.chapter is None:
        print("Error: --chapter is required (unless using --special-only, --cover-only, or --pdf-only)")
        sys.exit(1)

    # Parse chapter list (supports "0,4" or single "0")
    chapter_indices = [int(c.strip()) for c in args.chapter.split(",")]

    page_filter = None
    if args.pages:
        page_filter = [int(p.strip()) for p in args.pages.split(",")]

    # Generate each chapter
    for ch_idx in chapter_indices:
        generate_chapter(
            args.book, data, ch_idx,
            page_filter=page_filter,
            age_group=args.age,
            with_special=args.with_special,
        )

    # Build combined PDF with all requested chapters
    build_combined_pdf(args.book, data, chapter_indices)


if __name__ == "__main__":
    main()
