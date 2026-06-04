#!/usr/bin/env python3
"""Preprocess a book: extract text, run NLP analysis, save results to disk.

This only needs to run ONCE per book. Results are saved to:
  data/generated/{book_id}/preprocess/

Usage:
    python scripts/preprocess_book.py --input data/sample_books/the_great_gatsby.txt
    python scripts/preprocess_book.py --input data/sample_books/a_tale_of_two_cities.txt
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from src.config import GENERATED_DIR


def main():
    parser = argparse.ArgumentParser(description="Preprocess a book for picture book generation.")
    parser.add_argument("--input", required=True, help="Path to book .txt file")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    source = input_path.read_text(encoding="utf-8", errors="replace")
    print(f"Loaded {len(source)} chars from {input_path.name}")

    # Step 1: Extract text
    print("\n[1/2] Extracting text...")
    t0 = time.time()
    from src.extraction import extract_text
    from src.mcp_server import _strip_book_metadata

    result = extract_text(source)
    full_text = _strip_book_metadata(result.get("full_text", ""))
    chapters = result.get("chapters", [])
    title = result.get("title", input_path.stem)

    # Sanitize title to book_id
    import re
    sanitized = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', title)
    book_id = re.sub(r'\s+', '_', sanitized.strip())[:60] or input_path.stem

    # Filter out empty/stub chapters (TOC entries, headers with no body)
    real_chapters = [ch for ch in chapters if len(ch.get("text", "")) > 200]
    if len(real_chapters) < len(chapters):
        print(f"  Filtered {len(chapters) - len(real_chapters)} stub chapters (TOC entries)")
        chapters = real_chapters

    print(f"  Title: {title}")
    print(f"  Book ID: {book_id}")
    print(f"  Chapters: {len(chapters)}")
    print(f"  Text length: {len(full_text)} chars")
    print(f"  Time: {time.time() - t0:.1f}s")

    # Step 2: Analyze
    print("\n[2/2] Analyzing (NLP: segmentation, characters, sentiment, events)...")
    t0 = time.time()
    from src.analysis import analyze_text

    analysis = analyze_text(full_text, chapters if chapters else None)
    print(f"  Segments: {len(analysis.get('segments', []))}")
    print(f"  Characters: {len(analysis.get('characters', []))}")
    for c in analysis.get('characters', [])[:8]:
        print(f"    - {c['name']} ({c.get('role', '?')}, {c.get('mention_count', 0)} mentions)")
    print(f"  Key events: {len(analysis.get('key_events', []))}")
    print(f"  Character profiles: {len(analysis.get('character_profiles', []))}")
    print(f"  Time: {time.time() - t0:.1f}s")

    # Save everything
    preprocess_dir = GENERATED_DIR / book_id / "preprocess"
    preprocess_dir.mkdir(parents=True, exist_ok=True)

    def _save(name, data):
        path = preprocess_dir / f"{name}.json"
        path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
        print(f"  Saved: {path}")

    _save("meta", {
        "title": title,
        "book_id": book_id,
        "source_file": str(input_path),
        "num_chapters": len(chapters),
        "text_length": len(full_text),
    })
    _save("chapters", chapters)
    _save("full_text", {"text": full_text})
    _save("analysis", analysis)

    # Per-chapter segment breakdown (using chapter_idx from segmentation)
    chapter_segments = {}
    segments = analysis.get("segments", [])
    if chapters:
        for ch_idx, ch in enumerate(chapters):
            ch_segs = [s for s in segments if s.get("chapter_idx") == ch_idx]
            chapter_segments[str(ch_idx)] = {
                "chapter_title": ch.get("title", f"Chapter {ch_idx + 1}"),
                "num_segments": len(ch_segs),
                "segment_ids": [s.get("id") for s in ch_segs],
            }
    _save("chapter_segments", chapter_segments)

    print(f"\n=== Preprocess complete ===")
    print(f"Output: {preprocess_dir}")
    print(f"\nChapter breakdown:")
    for ch_idx, info in chapter_segments.items():
        print(f"  Chapter {ch_idx}: {info['chapter_title']} ({info['num_segments']} segments)")

    print(f"\nNext step: python scripts/generate_chapter.py --book {book_id} --chapter 0")


if __name__ == "__main__":
    main()
