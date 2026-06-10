#!/usr/bin/env python3
"""Generate a picture book chapter using the multi-agent pipeline.

Agents:
- AnalyzerAgent: loads preprocessed data, builds scenes
- WriterAgent: simplifies text for target age group
- ArtistAgent: generates character sheets + illustrations
- QAAgent: per-page quality checks + chapter summary

Usage:
    python scripts/generate_chapter.py --book The_Great_Gatsby --chapter 0
    python scripts/generate_chapter.py --book The_Great_Gatsby --chapter 0 --pages 1,2,3
    python scripts/generate_chapter.py --book The_Great_Gatsby --cover-only
    python scripts/generate_chapter.py --book The_Great_Gatsby --pdf-only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from src.config import GENERATED_DIR

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
)


def _update_progress(book_id: str, chapter_idx: int, **kwargs) -> None:
    """Write progress.json for frontend polling."""
    progress_file = GENERATED_DIR / book_id / "chapters" / f"ch{chapter_idx:02d}" / "progress.json"
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if progress_file.exists():
        try:
            existing = json.loads(progress_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    existing.update(kwargs)
    progress_file.write_text(json.dumps(existing))


def generate_chapter(
    book_id: str,
    data: dict,
    chapter_idx: int,
    page_filter: list[int] | None = None,
    age_group: str = "4-6",
) -> dict | None:
    """Generate a chapter by coordinating all agents."""
    from src.agents.analyzer import AnalyzerAgent
    from src.agents.writer import WriterAgent
    from src.agents.artist import ArtistAgent
    from src.agents.qa import QAAgent
    from src.agents.agent_log import log_event, clear_log

    analysis = data.get("analysis", {})
    characters = analysis.get("characters", [])
    profiles = analysis.get("character_profiles", [])
    title = data.get("meta", {}).get("title", "Untitled")

    # Clear previous logs
    clear_log(book_id, chapter_idx)

    # --- Analyzer Agent ---
    _update_progress(book_id, chapter_idx, status="generating", agent="analyzer", current_step="Analyzing chapter structure...", progress=5)
    log_event(book_id, chapter_idx, "analyzer", "load_chapter", f"Loading chapter {chapter_idx} data")
    analyzer = AnalyzerAgent(book_id)
    segments, ch_title = analyzer.get_chapter_segments(data, chapter_idx)
    log_event(book_id, chapter_idx, "analyzer", "load_chapter", f"Chapter: {ch_title}", result=f"{len(segments)} segments found", status="done")
    print(f"\n=== Generating Chapter {chapter_idx}: {ch_title} ===")
    print(f"  Segments: {len(segments)}")

    chapter_dir = GENERATED_DIR / book_id / "chapters" / f"ch{chapter_idx:02d}"
    chapter_dir.mkdir(parents=True, exist_ok=True)

    # --- Artist Agent: special pages ---
    _update_progress(book_id, chapter_idx, agent="artist", current_step="Generating special pages...", progress=10)
    log_event(book_id, chapter_idx, "artist", "special_pages", "Generating cover & special pages")
    artist = ArtistAgent(book_id)
    if not page_filter:
        artist.ensure_special_pages(data, chapter_idx, segments)
    log_event(book_id, chapter_idx, "artist", "special_pages", "Special pages ready", status="done")

    # --- Analyzer Agent: build scenes ---
    _update_progress(book_id, chapter_idx, agent="analyzer", current_step="Building scenes...", progress=15)
    log_event(book_id, chapter_idx, "analyzer", "build_scenes", "Converting segments to scenes")
    scenes = analyzer.build_scenes(segments, characters)
    log_event(book_id, chapter_idx, "analyzer", "build_scenes", f"{len(scenes)} pages to generate", status="done")
    print(f"  Pages to generate: {len(scenes)}")

    if page_filter:
        scenes = [s for s in scenes if s["page_number"] in page_filter]
        print(f"  Filtered to pages: {page_filter}")

    if not scenes:
        log_event(book_id, chapter_idx, "analyzer", "build_scenes", "No pages to generate", status="warn")
        print("  No pages to generate.")
        return None

    # --- Analyzer + Artist Agent: character sheets ---
    _update_progress(book_id, chapter_idx, agent="artist", current_step="Generating character sheets...", progress=20)
    _, chapter_profiles = analyzer.get_chapter_characters(data, segments)
    char_names = [c.get("name", "?") for c in chapter_profiles]
    log_event(book_id, chapter_idx, "analyzer", "find_characters", f"Found {len(chapter_profiles)} characters", result=", ".join(char_names[:8]), status="done")
    print(f"\n[Analyzer Agent] {len(chapter_profiles)} characters in this chapter")
    for c in chapter_profiles:
        print(f"    - {c.get('name')} ({c.get('role', '?')})")

    log_event(book_id, chapter_idx, "artist", "character_sheets", f"Generating sheets for {len(chapter_profiles)} characters")
    character_sheets = artist.generate_character_sheets(chapter_profiles)
    cached = len([s for s in character_sheets if s.get("_cached")])
    log_event(book_id, chapter_idx, "artist", "character_sheets", f"{len(character_sheets)} sheets ready ({cached} cached)", status="done")

    # --- Writer Agent: simplify text ---
    _update_progress(book_id, chapter_idx, agent="writer", current_step="Simplifying text for kids...", progress=30)
    log_event(book_id, chapter_idx, "writer", "simplify_text", f"Simplifying {len(scenes)} scenes for age {age_group}")
    writer = WriterAgent(age_group=age_group)
    chapter_char_names = {s["character_name"] for s in character_sheets}
    chapter_chars = [p for p in profiles if p.get("name") in chapter_char_names]
    simplified = writer.simplify(scenes, characters=chapter_chars, character_sheets=character_sheets)
    log_event(book_id, chapter_idx, "writer", "simplify_text", f"Simplified {len(simplified)} pages", status="done")

    # --- Writer Agent: build prompts ---
    _update_progress(book_id, chapter_idx, agent="writer", current_step="Building illustration prompts...", progress=35)
    log_event(book_id, chapter_idx, "writer", "build_prompts", f"Building {len(simplified)} illustration prompts")
    page_prompts = writer.build_prompts(simplified)
    log_event(book_id, chapter_idx, "writer", "build_prompts", f"{len(page_prompts)} prompts ready", status="done")

    # --- QA Agent + Artist Agent: generate illustrations with quality checks ---
    total_pages = len(page_prompts)
    _update_progress(book_id, chapter_idx, agent="artist", current_step=f"Illustrating page 1/{total_pages}...", progress=40, total_pages=total_pages, completed_pages=0)
    log_event(book_id, chapter_idx, "artist", "illustrate", f"Starting illustration of {total_pages} pages")

    def _progress_with_log(completed: int, step: str) -> None:
        agent = "artist" if "Illustrat" in step else "qa"
        _update_progress(
            book_id, chapter_idx, agent=agent,
            current_step=step, progress=40 + int(completed / max(total_pages, 1) * 50),
            completed_pages=completed, total_pages=total_pages,
        )
        log_event(book_id, chapter_idx, agent, "illustrate" if agent == "artist" else "check_page", step)

    qa = QAAgent(book_id)
    illustrations = artist.generate_illustrations(
        page_prompts, simplified, character_sheets, chapter_dir, qa_agent=qa,
        progress_callback=_progress_with_log,
    )
    log_event(book_id, chapter_idx, "artist", "illustrate", f"All {total_pages} pages illustrated", status="done")

    # --- QA Agent: chapter summary ---
    _update_progress(book_id, chapter_idx, agent="qa", current_step="Running quality checks...", progress=92)
    log_event(book_id, chapter_idx, "qa", "summarize", "Computing chapter quality summary")
    qa.summarize(illustrations, chapter_dir)
    log_event(book_id, chapter_idx, "qa", "summarize", "Quality summary complete", status="done")

    # --- Save chapter data ---
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

    # --- Artist Agent: ending pages ---
    if not page_filter:
        artist.ensure_ending_pages(data, chapter_idx, segments)

    # --- Save to MongoDB ---
    try:
        import pymongo
        from src.config import MONGODB_URI, MONGODB_DB
        client = pymongo.MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
        db = client[MONGODB_DB]
        db.books.update_one(
            {"book_id": book_id, "chapter": chapter_idx},
            {"$set": {
                "book_id": book_id, "title": title,
                "chapter": chapter_idx, "chapter_title": ch_title,
                "num_pages": len(chapter_data["pages"]),
                "pages": chapter_data["pages"],
            }},
            upsert=True,
        )
        client.close()
        print(f"  MongoDB: saved")
    except Exception:
        pass

    print(f"  Chapter {chapter_idx} done: {len(chapter_data['pages'])} pages")
    return chapter_data


def build_combined_pdf(book_id: str, data: dict, chapter_indices: list[int] | None = None):
    """Build combined PDF from all generated chapters."""
    from src.renderer.pdf_export import export_pdf

    title = data.get("meta", {}).get("title", "Untitled")
    chapters_root = GENERATED_DIR / book_id / "chapters"
    special_dir = str(GENERATED_DIR / book_id / "special")

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
            if chapter_indices and ch_idx not in chapter_indices:
                continue
            all_chapters.append(ch_data)

    if not all_chapters:
        print("No matching chapters found.")
        return

    all_chapters.sort(key=lambda c: c.get("chapter_idx", 0))

    combined_pages = []
    chapter_nums = []
    for ch in all_chapters:
        ch_num = ch.get("chapter_idx", 0) + 1
        chapter_nums.append(ch_num)
        for p in ch.get("pages", []):
            p["_chapter_num"] = ch_num
            combined_pages.append(p)

    pdf_path = str(GENERATED_DIR / book_id / "book.pdf")
    export_pdf(combined_pages, title, pdf_path, special_dir=special_dir, chapter_nums=chapter_nums)

    print(f"\n=== Combined PDF ===")
    print(f"  Chapters: {[c.get('chapter_title', '?') for c in all_chapters]}")
    print(f"  Total pages: {len(combined_pages)}")
    print(f"  PDF: {pdf_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate a picture book chapter.")
    parser.add_argument("--book", required=True, help="Book ID (folder name in data/generated/)")
    parser.add_argument("--chapter", type=str, default=None,
                        help="Chapter index (0-based). Comma-separated: 0,4")
    parser.add_argument("--pages", type=str, default=None, help="Comma-separated page numbers")
    parser.add_argument("--age", type=str, default="4-6", help="Target age group")
    parser.add_argument("--special-only", action="store_true", help="Only generate special pages")
    parser.add_argument("--cover-only", action="store_true", help="Only generate book cover")
    parser.add_argument("--pdf-only", action="store_true", help="Only rebuild PDF")
    args = parser.parse_args()

    from src.agents.analyzer import AnalyzerAgent
    analyzer = AnalyzerAgent(args.book)
    data = analyzer.load_preprocess()

    if args.cover_only:
        from src.agents.artist import ArtistAgent
        profiles = data.get("analysis", {}).get("character_profiles", [])
        main_chars = [p for p in profiles if p.get("role") in ("main", "supporting")][:5]
        if not main_chars:
            main_chars = profiles[:5]
        title = data.get("meta", {}).get("title", "Untitled")
        artist = ArtistAgent(args.book)
        artist.generate_book_cover(title, main_chars)
        return

    if args.special_only:
        from src.agents.artist import ArtistAgent
        artist = ArtistAgent(args.book)
        ch = int(args.chapter) if args.chapter else None
        if ch is not None:
            segments, _ = analyzer.get_chapter_segments(data, ch)
            artist.ensure_special_pages(data, ch, segments)
            artist.ensure_ending_pages(data, ch, segments)
        else:
            # Generate book cover + back cover only
            profiles = data.get("analysis", {}).get("character_profiles", [])
            main_chars = [p for p in profiles if p.get("role") in ("main", "supporting")][:5]
            if not main_chars:
                main_chars = profiles[:5]
            title = data.get("meta", {}).get("title", "Untitled")
            artist.generate_book_cover(title, main_chars)
            artist.generate_back_cover(title)
        return

    if args.pdf_only:
        ch_list = [int(c.strip()) for c in args.chapter.split(",")] if args.chapter else None
        build_combined_pdf(args.book, data, ch_list)
        return

    if args.chapter is None:
        print("Error: --chapter is required (unless using --special-only, --cover-only, or --pdf-only)")
        sys.exit(1)

    chapter_indices = [int(c.strip()) for c in args.chapter.split(",")]
    page_filter = [int(p.strip()) for p in args.pages.split(",")] if args.pages else None

    for ch_idx in chapter_indices:
        generate_chapter(args.book, data, ch_idx, page_filter=page_filter, age_group=args.age)

    build_combined_pdf(args.book, data, chapter_indices)


if __name__ == "__main__":
    main()
