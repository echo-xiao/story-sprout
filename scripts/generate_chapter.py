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
import copy
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


def _generate_character_sheets(
    book_id: str,
    data: dict,
    segments: list[dict],
    chapter_idx: int,
) -> list[dict]:
    """Generate (or reuse) character sheets for characters appearing in the chapter.

    Returns a list of character sheet dicts, each with keys:
        character_name, sheet_path, visual_identity, background.
    """
    from src.generation.character_sheet import (
        generate_character_sheets, _assign_visual_identities, _safe_filename,
    )

    analysis = data.get("analysis", {})
    characters = analysis.get("characters", [])
    profiles = analysis.get("character_profiles", [])

    # Collect character names from scenes
    chapter_char_names: set[str] = set()
    char_names = [c["name"] for c in characters[:10]]
    for seg in segments:
        seg_text = seg.get("text", "")
        if len(seg_text.split()) < 10:
            continue
        present = seg.get("characters_in_scene")
        if present is None:
            text_lower = seg_text.lower()
            present = [n for n in char_names if n.lower() in text_lower]
        for name in (present or [])[:5]:
            chapter_char_names.add(name)

    chapter_chars = [p for p in profiles if p.get("name") in chapter_char_names]

    print(f"\n[1/4] Characters in this chapter (from preprocess): {len(chapter_chars)}")
    for c in chapter_chars:
        print(f"    - {c.get('name')} ({c.get('role', '?')})")

    ch_dir = GENERATED_DIR / book_id / "characters"
    chapter_chars = _assign_visual_identities(chapter_chars)
    character_sheets: list[dict] = []
    chars_to_generate: list[dict] = []

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
        print(f"  All {len(character_sheets)} sheets already exist")

    return character_sheets


def _simplify_texts(
    scenes: list[dict],
    age_group: str,
    chapter_chars: list[dict],
    character_sheets: list[dict],
    save_step_fn,
) -> list[dict]:
    """Simplify original text for each scene page via LLM.

    Returns the list of simplified scene dicts (with page_text, scene_direction, etc.).
    """
    from src.agent.text_simplifier import simplify_text

    print(f"\n[2/4] Simplifying text ({len(scenes)} pages, one at a time)...")
    t0 = time.time()
    simplified = simplify_text(scenes, age_group, characters=chapter_chars, character_sheets=character_sheets)
    dt = time.time() - t0
    print(f"  Done in {dt:.1f}s")
    save_step_fn("simplified_text", [
        {"page": s.get("page_number"), "text": s.get("page_text", ""),
         "scene_direction": s.get("scene_direction", "")}
        for s in simplified
    ], dt)
    return simplified


def _build_prompts(
    simplified: list[dict],
    character_sheets: list[dict],
    save_step_fn,
) -> list[dict]:
    """Build illustration prompts from simplified scenes (template-based, no LLM).

    Returns list of page prompt dicts.
    """
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
    save_step_fn("illustration_prompts", [
        {"page": p.get("page_number"), "text": p.get("text", "")[:200],
         "scene": p.get("scene_direction", "")[:200]}
        for p in page_prompts
    ], dt)
    return page_prompts


def _generate_illustrations(
    scenes: list[dict],
    simplified: list[dict],
    character_sheets: list[dict],
    book_id: str,
    ch_dir: Path,
    save_step_fn,
) -> list[dict]:
    """Generate illustration images and run per-page quality checks.

    Returns the list of illustration dicts (page_number, image_path, prompt_used).
    """
    from src.generation.illustration import _get_client, _generate_single_page

    page_prompts = _build_prompts(simplified, character_sheets, save_step_fn)

    print(f"\n[4/4] Generating illustrations + quality check ({len(page_prompts)} pages)...")
    chapter_pages_dir = ch_dir / "pages"
    chapter_pages_dir.mkdir(parents=True, exist_ok=True)
    quality_dir = ch_dir / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)

    valid_sheets = [s for s in character_sheets if s.get("sheet_path") and Path(s["sheet_path"]).exists()]
    img_client = _get_client()

    try:
        from src.generation.gemini_consistency_check import (
            check_page_quality,
            check_style_consistency,
        )
        quality_available = True
    except Exception:
        quality_available = False

    illustrations: list[dict] = []
    per_page_results: list[dict] = []
    per_character_scores: dict[str, list[int]] = {}

    for idx_p, page_prompt in enumerate(page_prompts):
        page_num = page_prompt.get("page_number", idx_p + 1)
        save_path = chapter_pages_dir / f"page_{page_num:03d}"
        scene = simplified[idx_p] if idx_p < len(simplified) else {}

        # Check if already exists (checkpoint)
        existing = None
        for ext in (".png", ".jpg"):
            candidate = save_path.with_suffix(ext)
            if candidate.exists():
                existing = str(candidate)
                break

        if existing:
            print(f"  Page {page_num}: cached, skipping generation")
            ill_path = existing
        else:
            t_page = time.time()
            success, ill_path, prompt = _generate_single_page(
                img_client, page_prompt, valid_sheets, save_path,
            )
            dt_page = time.time() - t_page
            if not success:
                print(f"  Page {page_num}: generation FAILED ({dt_page:.1f}s)")
                illustrations.append({"page_number": page_num, "image_path": "", "prompt_used": prompt})
                continue
            # Resolve actual path
            for ext in (".png", ".jpg"):
                candidate = save_path.with_suffix(ext)
                if candidate.exists():
                    ill_path = str(candidate)
                    break
            print(f"  Page {page_num}: generated ({dt_page:.1f}s)")

        illustrations.append({"page_number": page_num, "image_path": ill_path, "prompt_used": ""})

        # Immediate quality check
        if quality_available and ill_path:
            scene_chars = scene.get("key_characters", [])
            page_text = scene.get("page_text", scene.get("text", ""))
            relevant_sheets = [s for s in character_sheets if s["character_name"] in scene_chars]

            t_q = time.time()
            result = check_page_quality(ill_path, relevant_sheets, page_text, scene_chars, page_num)
            result["page"] = page_num
            per_page_results.append(result)

            # Save per-page quality file
            quality_file = quality_dir / f"page_{page_num:03d}_quality.json"
            quality_file.write_text(
                json.dumps(result, indent=2, default=str, ensure_ascii=False), encoding="utf-8",
            )

            for c in result.get("character_consistency", {}).get("characters", []):
                per_character_scores.setdefault(c["name"], []).append(c.get("score", 100))

            score = result.get("overall_score", 100)
            issues = []
            if result.get("spelling", {}).get("errors"):
                issues.append(f"spell:{len(result['spelling']['errors'])}")
            if result.get("duplicate_characters", {}).get("duplicates"):
                issues.append(f"dup:{len(result['duplicate_characters']['duplicates'])}")
            if result.get("name_face_mismatch", {}).get("mismatches"):
                issues.append(f"name:{len(result['name_face_mismatch']['mismatches'])}")
            if result.get("character_count", {}).get("missing"):
                issues.append(f"miss:{result['character_count']['missing']}")
            status = "OK" if score >= 80 else "WARN" if score >= 60 else "BAD"
            issues_str = f" ({', '.join(issues)})" if issues else ""
            print(f"           quality: {score}% [{status}]{issues_str} ({time.time()-t_q:.1f}s)")

    save_step_fn("illustrations", [
        {"page": ill.get("page_number"), "path": ill.get("image_path", "")}
        for ill in illustrations
    ])

    # Style coherence (across all pages, at the end) + summary
    try:
        ill_paths = [ill.get("image_path", "") for ill in illustrations if ill.get("image_path")]

        per_character_avg = []
        for name, scores in per_character_scores.items():
            avg = round(sum(scores) / len(scores)) if scores else 100
            per_character_avg.append({"name": name, "score": avg})
        char_overall = round(sum(c["score"] for c in per_character_avg) / len(per_character_avg)) if per_character_avg else 100

        # Style coherence vs book cover
        style_result = {"score": 100, "per_page": [], "issues": []}
        if quality_available and len(ill_paths) >= 2:
            cover_path = None
            special_dir = GENERATED_DIR / book_id / "special"
            for ext in (".png", ".jpg"):
                candidate = special_dir / f"book_cover{ext}"
                if candidate.exists():
                    cover_path = str(candidate)
                    break
            style_result = check_style_consistency(ill_paths, reference_path=cover_path)

        n = max(len(per_page_results), 1)
        dim_scores = {
            "character_consistency": round(sum(r.get("character_consistency", {}).get("score", 100) for r in per_page_results) / n),
            "spelling": round(sum(r.get("spelling", {}).get("score", 100) for r in per_page_results) / n),
            "duplicate_characters": round(sum(r.get("duplicate_characters", {}).get("score", 100) for r in per_page_results) / n),
            "name_face_mismatch": round(sum(r.get("name_face_mismatch", {}).get("score", 100) for r in per_page_results) / n),
            "character_count": round(sum(r.get("character_count", {}).get("score", 100) for r in per_page_results) / n),
            "style_coherence": style_result.get("score", 100),
        }

        consistency_result = {
            "overall_score": round(sum(dim_scores.values()) / len(dim_scores)),
            "dimensions": dim_scores,
            "character_match": {"score": char_overall, "per_character": per_character_avg},
            "style_coherence": style_result,
            "per_page": per_page_results,
        }

        print(f"\n  === Chapter Quality Summary ===")
        print(f"  Overall: {consistency_result['overall_score']}%")
        for dim, sc in dim_scores.items():
            st = "OK" if sc >= 80 else "WARN" if sc >= 60 else "BAD"
            print(f"    {dim}: {sc}% [{st}]")

        consistency_path = ch_dir / "consistency.json"
        consistency_path.write_text(
            json.dumps(consistency_result, indent=2, default=str, ensure_ascii=False), encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Summary quality failed: %s", e)

    return illustrations


def generate_chapter(
    book_id: str,
    data: dict,
    chapter_idx: int,
    page_filter: list[int] | None = None,
    age_group: str = "4-6",
):
    """Generate all pages for a chapter.

    Pipeline:
    0. Book cover + chapter cover (auto, cached)
    1. Character sheets (LLM image gen, cached)
    2. Text simplification (LLM text, per-page)
    3. Illustration prompts (ALGORITHM -- template-based, no LLM)
    4. Generate illustrations (LLM image gen)
    5. Quality check (Gemini Vision, auto)
    6. Chapter ending + back cover (auto, cached)
    """
    from src.generation.character_sheet import _assign_visual_identities

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
    meta = data.get("meta", {})
    title = meta.get("title", "Untitled")

    segments, ch_title = _get_chapter_segments(data, chapter_idx)
    print(f"\n=== Generating Chapter {chapter_idx}: {ch_title} ===")
    print(f"  Segments: {len(segments)}")

    # --- Auto-generate book cover (once, if not exists) ---
    if not page_filter:
        from src.generation.special_pages import (
            generate_book_cover, generate_chapter_cover,
            generate_chapter_ending, generate_back_cover,
        )
        from src.generation.character_sheet import _assign_visual_identities as _avi

        special_dir = GENERATED_DIR / book_id / "special"
        special_dir.mkdir(parents=True, exist_ok=True)
        cover_exists = any((special_dir / f"book_cover{ext}").exists() for ext in (".png", ".jpg"))
        if not cover_exists:
            main_profiles = [p for p in profiles if p.get("role") in ("main", "supporting")][:5]
            if not main_profiles:
                main_profiles = profiles[:5]
            main_profiles = _avi(main_profiles)
            print(f"\n[0] Generating book cover...")
            t0 = time.time()
            generate_book_cover(title, main_profiles, book_id)
            print(f"  Book cover generated in {time.time() - t0:.1f}s")

        # --- Chapter cover ---
        ch_cover_exists = any(
            (special_dir / f"chapter_{chapter_idx + 1}_cover{ext}").exists() for ext in (".png", ".jpg")
        )
        if not ch_cover_exists:
            main_profiles_ch = [p for p in profiles if p.get("role") in ("main", "supporting")][:5]
            if not main_profiles_ch:
                main_profiles_ch = profiles[:5]
            main_profiles_ch = _avi(main_profiles_ch)
            summary = segments[0].get("text", "")[:200] if segments else ""
            print(f"  Generating chapter {chapter_idx + 1} cover...")
            t0 = time.time()
            generate_chapter_cover(ch_title, chapter_idx + 1, summary, main_profiles_ch, book_id)
            print(f"  Chapter cover generated in {time.time() - t0:.1f}s")

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

    # Step 1: Character sheets
    character_sheets = _generate_character_sheets(book_id, data, segments, chapter_idx)
    _save_step("character_sheets", [
        {"name": s["character_name"], "path": s.get("sheet_path", ""),
         "visual_identity": s.get("visual_identity", ""), "background": s.get("background", "")}
        for s in character_sheets
    ])

    # Step 2: Simplify text
    # Collect chapter_chars for the simplifier (same logic as _generate_character_sheets)
    chapter_char_names = {s["character_name"] for s in character_sheets}
    chapter_chars = [p for p in profiles if p.get("name") in chapter_char_names]

    simplified = _simplify_texts(scenes, age_group, chapter_chars, character_sheets, _save_step)

    # Steps 3 + 4: Build prompts + generate illustrations (with quality checks)
    illustrations = _generate_illustrations(
        scenes, simplified, character_sheets, book_id, chapter_dir, _save_step,
    )

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

    # Generate chapter ending + back cover (if not single-page regen)
    if not page_filter:
        from src.generation.special_pages import (
            generate_chapter_ending, generate_back_cover,
        )
        from src.generation.character_sheet import _assign_visual_identities as _avi2
        special_dir = GENERATED_DIR / book_id / "special"

        # Chapter ending
        ch_ending_exists = any(
            (special_dir / f"chapter_{chapter_idx + 1}_ending{ext}").exists() for ext in (".png", ".jpg")
        )
        if not ch_ending_exists:
            main_profiles_end = [p for p in profiles if p.get("role") in ("main", "supporting")][:5]
            if not main_profiles_end:
                main_profiles_end = profiles[:5]
            main_profiles_end = _avi2(main_profiles_end)
            ending_text = segments[-1].get("text", "")[:200] if segments else ""
            print(f"  Generating chapter {chapter_idx + 1} ending...")
            t0 = time.time()
            generate_chapter_ending(ch_title, chapter_idx + 1, ending_text, main_profiles_end, book_id)
            print(f"  Chapter ending generated in {time.time() - t0:.1f}s")

        # Back cover (once)
        back_exists = any((special_dir / f"back_cover{ext}").exists() for ext in (".png", ".jpg"))
        if not back_exists:
            print(f"  Generating back cover...")
            t0 = time.time()
            generate_back_cover(title, book_id)
            print(f"  Back cover generated in {time.time() - t0:.1f}s")

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
        print(f"  MongoDB: saved")
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
        )

    # Build combined PDF with all requested chapters
    build_combined_pdf(args.book, data, chapter_indices)


if __name__ == "__main__":
    main()
