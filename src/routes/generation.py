"""Generation & quality check endpoints."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

from src.config import GENERATED_DIR
from src.routes.helpers import _load_json, _save_json

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/book/{book_id}/chapter/{ch_idx}/agent-log")
async def get_agent_log(book_id: str, ch_idx: int) -> list[dict]:
    """Get agent activity log for a chapter generation session."""
    from src.agents.agent_log import get_log
    return get_log(book_id, ch_idx)


@router.post("/api/book/{book_id}/segment/{seg_id}/regenerate")
async def regenerate_segment_illustration(
    book_id: str, seg_id: int, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Regenerate illustration for a single segment."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data found.")

    segments = analysis.get("segments", [])
    target = None
    for seg in segments:
        if seg.get("id") == seg_id:
            target = seg
            break

    if target is None:
        raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")

    ch_idx = target.get("chapter_idx", 0)

    # Find page number within chapter
    ch_segments = [s for s in segments if s.get("chapter_idx") == ch_idx]
    ch_segments.sort(key=lambda s: s.get("id", 0))
    page_num = next((i + 1 for i, s in enumerate(ch_segments) if s.get("id") == seg_id), 1)

    # Move existing illustration + quality file to history before regenerating
    ch_base = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}"
    ch_dir = ch_base / "pages"
    history_dir = ch_base / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    import time as _time
    ts = int(_time.time())
    for ext in (".png", ".jpg"):
        old_img = ch_dir / f"page_{page_num:03d}{ext}"
        if old_img.exists():
            old_img.rename(history_dir / f"page_{page_num:03d}_{ts}{ext}")
    # Move quality file too
    quality_file = ch_base / "quality" / f"page_{page_num:03d}_quality.json"
    if quality_file.exists():
        quality_file.rename(history_dir / f"page_{page_num:03d}_{ts}_quality.json")

    async def _regen():
        from src.agent.text_simplifier import simplify_text
        from src.generation.illustration import generate_illustrations
        from src.generation.character_sheet import _safe_filename, generate_character_sheets

        # Step 1: Generate character sheets if missing
        chars_dir = GENERATED_DIR / book_id / "characters"
        chars_dir.mkdir(parents=True, exist_ok=True)
        character_sheets = []
        chars_to_generate = []

        for name in target.get("characters_in_scene", []):
            safe = _safe_filename(name)
            found = False
            for ext in (".png", ".jpg"):
                sheet_path = chars_dir / f"{safe}_sheet{ext}"
                if sheet_path.exists():
                    character_sheets.append({
                        "character_name": name,
                        "sheet_path": str(sheet_path),
                    })
                    found = True
                    break
            if not found:
                # Find character profile from LLM data
                llm_chars = _load_json(book_id, "llm_characters.json") or {}
                for c in llm_chars.get("characters", []):
                    if c.get("canonical_name") == name:
                        chars_to_generate.append({
                            "name": name,
                            "role": c.get("role", "supporting"),
                            "gender": c.get("gender", "unknown"),
                            "appearance_description": [c.get("appearance", ""), c.get("description", "")],
                            "visual_details": c.get("visual_details", {}),
                        })
                        break

        if chars_to_generate:
            new_sheets = generate_character_sheets(chars_to_generate, book_id)
            character_sheets.extend(new_sheets)

        # Step 2: Simplify text if not done yet
        simplified_text = target.get("simplified_text", "")
        if not simplified_text:
            scene = {
                "page_number": page_num,
                "original_text": target.get("text", ""),
                "key_characters": target.get("characters_in_scene", []),
                "scene_summary": target.get("scene_summary", ""),
            }
            result = simplify_text([scene], "4-6")
            if result:
                simplified_text = result[0].get("page_text", "")
                scene_direction = result[0].get("scene_direction", "")
                # Save back to analysis
                target["simplified_text"] = simplified_text
                target["scene_direction"] = scene_direction
                _save_json(book_id, "analysis.json", analysis)

        # Step 3: Generate illustration
        ch_dir.mkdir(parents=True, exist_ok=True)
        page_prompt = {
            "page_number": page_num,
            "text": simplified_text or target.get("text", ""),
            "scene_description": target.get("scene_direction", target.get("scene_summary", "")),
            "scene_background": target.get("scene_background", ""),
            "key_characters": target.get("characters_in_scene", []),
            "character_actions": target.get("character_actions", []),
        }

        generate_illustrations(
            [page_prompt], character_sheets, book_id,
            pages_dir=str(ch_dir),
        )
        logger.info("Regeneration complete for segment %d (page %d)", seg_id, page_num)

        # Sync to MongoDB
        try:
            from src.core.db import save_illustration
            for ext in (".png", ".jpg"):
                img = ch_dir / f"page_{page_num:03d}{ext}"
                if img.exists():
                    save_illustration(book_id, seg_id, str(page_prompt), str(img))
                    break
        except Exception as e:
            logger.warning("MongoDB sync failed for segment %d: %s", seg_id, e)

        # Write completion marker
        import time as _t
        marker = ch_base / f"regen_{seg_id}.json"
        marker.write_text(json.dumps({"status": "complete", "segment_id": seg_id, "page_number": page_num, "timestamp": _t.time()}))

    # Clear old marker BEFORE starting task so status check returns "generating"
    marker = ch_base / f"regen_{seg_id}.json"
    if marker.exists():
        marker.unlink()

    background_tasks.add_task(_regen)

    return {"status": "regenerating", "segment_id": seg_id, "page_number": page_num}


@router.get("/api/book/{book_id}/segment/{seg_id}/regen-status")
async def get_regen_status(book_id: str, seg_id: int) -> dict[str, Any]:
    """Check if a segment regeneration is complete."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        return {"status": "unknown"}
    target = next((s for s in analysis.get("segments", []) if s.get("id") == seg_id), None)
    if not target:
        return {"status": "unknown"}
    ch_idx = target.get("chapter_idx", 0)
    marker = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / f"regen_{seg_id}.json"
    if marker.exists():
        result = json.loads(marker.read_text())
        return result
    return {"status": "generating"}


@router.post("/api/book/{book_id}/chapter/{ch_idx}/generate")
async def generate_chapter_endpoint(
    book_id: str, ch_idx: int, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Generate illustrations for a chapter (text simplification + illustration)."""
    preprocess_dir = GENERATED_DIR / book_id / "preprocess"
    if not preprocess_dir.exists():
        raise HTTPException(status_code=404, detail="No preprocess data. Run preprocess first.")

    # Initialize progress file
    progress_file = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "progress.json"
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    progress_file.write_text(json.dumps({"status": "starting", "progress": 0, "current_step": "Starting...", "total_pages": 0, "completed_pages": 0}))

    async def _gen():
        import subprocess
        subprocess.run(
            ["python", "scripts/generate_chapter.py", "--book", book_id, "--chapter", str(ch_idx), "--with-special"],
            cwd=str(Path(__file__).parent.parent.parent),
        )
        # Mark complete
        progress_file.write_text(json.dumps({"status": "complete", "progress": 100, "current_step": "Done", "total_pages": 0, "completed_pages": 0}))

    background_tasks.add_task(_gen)
    return {"status": "generating", "book_id": book_id, "chapter": ch_idx}


@router.get("/api/book/{book_id}/chapter/{ch_idx}/progress")
async def get_chapter_progress(book_id: str, ch_idx: int) -> dict[str, Any]:
    """Get generation progress for a chapter."""
    # Always check actual files first to determine true completion
    ch_dir = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "pages"
    analysis = _load_json(book_id, "analysis.json")
    total = 0
    if analysis:
        total = sum(1 for s in analysis.get("segments", []) if s.get("chapter_idx") == ch_idx)

    completed = len(list(ch_dir.glob("page_*.*"))) if ch_dir.exists() else 0

    # If all pages exist, it's complete regardless of progress.json
    if completed >= total and total > 0:
        return {
            "status": "complete", "progress": 100,
            "current_step": "Done", "agent": "complete",
            "total_pages": total, "completed_pages": completed,
        }

    # Check progress.json for live updates during generation
    progress_file = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "progress.json"
    if progress_file.exists():
        try:
            data = json.loads(progress_file.read_text())
            # Override with actual file count for accuracy
            data["completed_pages"] = completed
            data["total_pages"] = total
            return data
        except (json.JSONDecodeError, OSError):
            pass

    if not ch_dir.exists() or completed == 0:
        return {"status": "not_started", "progress": 0, "current_step": "Not started", "total_pages": total, "completed_pages": 0}

    progress = int(completed / total * 100) if total > 0 else 0
    return {
        "status": "generating", "progress": progress,
        "current_step": f"Page {completed}/{total}",
        "total_pages": total, "completed_pages": completed,
    }


@router.get("/api/book/{book_id}/segment/{seg_id}/quality")
async def get_segment_quality(book_id: str, seg_id: int, version: str = "current") -> dict[str, Any]:
    """Get cached quality check result for a segment. Use version=current or a timestamp."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        return {}

    segments = analysis.get("segments", [])
    target = next((s for s in segments if s.get("id") == seg_id), None)
    if not target:
        return {}

    ch_idx = target.get("chapter_idx", 0)
    ch_segments = sorted([s for s in segments if s.get("chapter_idx") == ch_idx], key=lambda s: s.get("id", 0))
    page_num = next((i + 1 for i, s in enumerate(ch_segments) if s.get("id") == seg_id), 1)

    ch_base = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}"

    if version == "current":
        quality_file = ch_base / "quality" / f"page_{page_num:03d}_quality.json"
    else:
        quality_file = ch_base / "history" / f"page_{page_num:03d}_{version}_quality.json"

    if quality_file.exists():
        return json.loads(quality_file.read_text(encoding="utf-8"))
    return {}


@router.post("/api/book/{book_id}/segment/{seg_id}/quality")
async def check_segment_quality(book_id: str, seg_id: int) -> dict[str, Any]:
    """Run quality check on a single segment's illustration."""
    from src.generation.gemini_consistency_check import check_page_quality

    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data found.")

    segments = analysis.get("segments", [])
    target = next((s for s in segments if s.get("id") == seg_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")

    ch_idx = target.get("chapter_idx", 0)
    ch_segments = sorted([s for s in segments if s.get("chapter_idx") == ch_idx], key=lambda s: s.get("id", 0))
    page_num = next((i + 1 for i, s in enumerate(ch_segments) if s.get("id") == seg_id), 1)

    # Find illustration
    ch_dir = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "pages"
    ill_path = ""
    for ext in (".png", ".jpg"):
        candidate = ch_dir / f"page_{page_num:03d}{ext}"
        if candidate.exists():
            ill_path = str(candidate)
            break
    if not ill_path:
        raise HTTPException(status_code=404, detail="No illustration found for this segment.")

    # Find character sheets — match by scene character name directly
    from src.generation.character_sheet import _safe_filename
    chars_dir = GENERATED_DIR / book_id / "characters"
    llm_chars = _load_json(book_id, "llm_characters.json") or {}
    scene_chars = target.get("characters_in_scene", [])
    character_sheets = []
    for name in scene_chars:
        safe = _safe_filename(name)
        for ext in (".png", ".jpg"):
            sheet_path = chars_dir / f"{safe}_sheet{ext}"
            if sheet_path.exists():
                # Find appearance from llm_characters for visual_identity
                appearance = ""
                for c in llm_chars.get("characters", []):
                    cn = c.get("canonical_name", "").lower()
                    if cn == name.lower() or name.lower() in [a.lower() for a in c.get("aliases", [])]:
                        appearance = c.get("appearance", "")
                        break
                character_sheets.append({
                    "character_name": name,
                    "sheet_path": str(sheet_path),
                    "visual_identity": appearance,
                })
                break

    page_text = target.get("simplified_text", target.get("text", ""))
    try:
        result = check_page_quality(ill_path, character_sheets, page_text, scene_chars, page_num)
    except Exception as e:
        logger.error("Quality check failed for segment %d: %s", seg_id, e)
        raise HTTPException(status_code=500, detail=f"Quality check failed: {str(e)}")
    result["page"] = page_num
    result["segment_id"] = seg_id

    # Cache result to disk
    quality_dir = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
    quality_file = quality_dir / f"page_{page_num:03d}_quality.json"
    quality_file.write_text(json.dumps(result, indent=2, default=str, ensure_ascii=False), encoding="utf-8")

    return result


@router.get("/api/book/{book_id}/chapter/{ch_idx}/consistency")
async def get_chapter_consistency(book_id: str, ch_idx: int) -> dict[str, Any]:
    """Get cached consistency/quality check results for a chapter."""
    consistency_path = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "consistency.json"
    if consistency_path.exists():
        return json.loads(consistency_path.read_text(encoding="utf-8"))
    return {}


@router.post("/api/book/{book_id}/chapter/{ch_idx}/consistency")
async def check_chapter_consistency(book_id: str, ch_idx: int) -> dict[str, Any]:
    """Run full quality check on a chapter's illustrations.

    Checks 5 dimensions per page + style coherence across pages:
    1. Character consistency (vs reference sheets)
    2. Spelling errors in embedded text
    3. Duplicate characters (same person drawn twice)
    4. Name-face mismatch (label points to wrong character)
    5. Missing/extra characters
    6. Style coherence (across all pages)
    """
    from src.generation.gemini_consistency_check import (
        check_page_quality,
        check_style_consistency,
    )

    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data found.")

    segments = analysis.get("segments", [])
    ch_segments = sorted(
        [s for s in segments if s.get("chapter_idx") == ch_idx],
        key=lambda s: s.get("id", 0),
    )

    ch_dir = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "pages"
    chars_dir = GENERATED_DIR / book_id / "characters"

    # Build character sheets
    import re as _re
    llm_chars = _load_json(book_id, "llm_characters.json") or {}
    character_sheets = []
    for char in llm_chars.get("characters", []):
        name = char.get("canonical_name", "")
        safe = _re.sub(r'[^\w\s\u4e00-\u9fff-]', '', name)
        safe = _re.sub(r'\s+', '_', safe.strip()).lower()[:50]
        for ext in (".png", ".jpg"):
            sheet_path = chars_dir / f"{safe}_sheet{ext}"
            if sheet_path.exists():
                character_sheets.append({
                    "character_name": name,
                    "sheet_path": str(sheet_path),
                    "visual_identity": char.get("appearance", ""),
                })
                break

    # Per-page quality check
    ill_paths = []
    per_page_results = []
    per_character_scores: dict[str, list[int]] = {}

    for idx, seg in enumerate(ch_segments):
        page_num = idx + 1
        ill_path = ""
        for ext in (".png", ".jpg"):
            img_path = ch_dir / f"page_{page_num:03d}{ext}"
            if img_path.exists():
                ill_path = str(img_path)
                break
        if not ill_path:
            continue
        ill_paths.append(ill_path)

        scene_chars = seg.get("characters_in_scene", [])
        page_text = seg.get("simplified_text", seg.get("text", ""))
        relevant_sheets = [s for s in character_sheets if s["character_name"] in scene_chars]

        result = check_page_quality(ill_path, relevant_sheets, page_text, scene_chars, page_num)
        result["page"] = page_num
        per_page_results.append(result)

        for c in result.get("character_consistency", {}).get("characters", []):
            per_character_scores.setdefault(c["name"], []).append(c.get("score", 100))

    # Aggregate per-character
    per_character_avg = []
    for name, scores in per_character_scores.items():
        avg = round(sum(scores) / len(scores)) if scores else 100
        per_character_avg.append({"name": name, "score": avg})
    char_overall = round(sum(c["score"] for c in per_character_avg) / len(per_character_avg)) if per_character_avg else 100

    # Style coherence
    # Use book cover as style reference if available
    cover_path = None
    special_dir = GENERATED_DIR / book_id / "special"
    if special_dir.exists():
        for ext in (".png", ".jpg"):
            candidate = special_dir / f"book_cover{ext}"
            if candidate.exists():
                cover_path = str(candidate)
                break
    style_result = check_style_consistency(ill_paths, reference_path=cover_path)

    # Aggregate dimension scores
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

    # Cache to disk
    consistency_path = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "consistency.json"
    consistency_path.parent.mkdir(parents=True, exist_ok=True)
    consistency_path.write_text(json.dumps(consistency_result, indent=2, default=str, ensure_ascii=False), encoding="utf-8")

    return consistency_result


@router.post("/api/book/{book_id}/special/{page_type}/regenerate")
async def regenerate_special_page(
    book_id: str, page_type: str, background_tasks: BackgroundTasks,
    chapter: int = 0,
) -> dict[str, Any]:
    """Regenerate a special page (book_cover, chapter_cover, chapter_ending, back_cover)."""
    from src.generation.special_pages import (
        generate_book_cover, generate_chapter_cover,
        generate_chapter_ending, generate_back_cover,
    )

    # Load character sheets
    chars_dir = GENERATED_DIR / book_id / "characters"
    character_sheets = []
    if chars_dir.exists():
        for f in chars_dir.glob("*_sheet.*"):
            name = f.stem.replace("_sheet", "").replace("_", " ").title()
            character_sheets.append({"character_name": name, "sheet_path": str(f)})

    # Load book info
    meta = _load_json(book_id, "meta.json") or {}
    title = meta.get("title", book_id)
    ch_segments = _load_json(book_id, "chapter_segments.json") or {}
    llm_chars = _load_json(book_id, "llm_characters.json") or {}
    characters = llm_chars.get("characters", [])

    async def _gen():
        if page_type == "book_cover":
            char_profiles = [{"name": c["canonical_name"], "visual_identity": c.get("appearance", "")} for c in characters[:5]]
            generate_book_cover(title, char_profiles, book_id, character_sheets=character_sheets)
        elif page_type == "chapter_cover":
            ch_info = ch_segments.get(str(chapter), {})
            ch_title = ch_info.get("chapter_title", f"Chapter {chapter + 1}")
            ch_summary = ch_info.get("chapter_summary", "")
            char_profiles = [{"name": c["canonical_name"], "visual_identity": c.get("appearance", "")} for c in characters[:3]]
            generate_chapter_cover(ch_title, chapter, ch_summary, char_profiles, book_id, character_sheets=character_sheets)
        elif page_type == "chapter_ending":
            ch_info = ch_segments.get(str(chapter), {})
            ch_title = ch_info.get("chapter_title", f"Chapter {chapter + 1}")
            char_profiles = [{"name": c["canonical_name"], "visual_identity": c.get("appearance", "")} for c in characters[:3]]
            generate_chapter_ending(ch_title, chapter, "", char_profiles, book_id, character_sheets=character_sheets)
        elif page_type == "back_cover":
            generate_back_cover(title, book_id, character_sheets=character_sheets)
        else:
            logger.error("Unknown special page type: %s", page_type)

    background_tasks.add_task(_gen)
    return {"status": "generating", "page_type": page_type, "chapter": chapter}


@router.post("/api/book/{book_id}/scenes/{scene_name}/regenerate")
async def regenerate_scene_sheet(
    book_id: str, scene_name: str, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Generate/regenerate a scene reference image for a location."""
    import re as _re
    import time as _time

    scenes_dir = GENERATED_DIR / book_id / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)

    # Safe filename
    safe = _re.sub(r'[^\w\s\u4e00-\u9fff-]', '', scene_name)
    safe = _re.sub(r'\s+', '_', safe.strip()).lower()[:50]

    # Move existing to history
    history_dir = scenes_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    ts = int(_time.time())
    for ext in (".png", ".jpg"):
        old = scenes_dir / f"{safe}_scene{ext}"
        if old.exists():
            old.rename(history_dir / f"{safe}_scene_{ts}{ext}")

    async def _gen():
        from src.config import IMAGE_LLM, DEFAULT_STYLE, NEGATIVE_PROMPT

        # Load location details
        llm_locs = _load_json(book_id, "llm_locations.json") or {}
        locations = llm_locs.get("locations", [])
        loc = next((l for l in locations if l.get("name") == scene_name), None)
        if not loc:
            logger.error("Location %s not found", scene_name)
            return

        vd = loc.get("visual_details", {})
        details = ", ".join(f"{k}: {v}" for k, v in vd.items() if v) if vd else ""

        prompt = (
            f"A background scene for a children's picture book: {scene_name}. "
            f"{loc.get('description', '')}. "
            f"Visual details: {details}. "
            f"This is a BACKGROUND ONLY — NO people, NO characters, NO figures, NO animals. "
            f"Just the empty environment, architecture, landscape, and objects. "
            f"Single clean illustration, not a grid or collage. "
            f"Style: {DEFAULT_STYLE}. "
            f"NOT: {NEGATIVE_PROMPT}, people, characters, figures, silhouettes"
        )

        out_path = scenes_dir / f"{safe}_scene"

        if IMAGE_LLM == "alicloud":
            from src.generation.alicloud_image import generate_image_alicloud
            generate_image_alicloud(prompt, out_path)
            return

        from google import genai
        from src.config import GEMINI_API_KEY, GEMINI_IMAGE_MODEL
        client = genai.Client(api_key=GEMINI_API_KEY)
        try:
            response = client.models.generate_content(
                model=GEMINI_IMAGE_MODEL,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                ),
            )
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                    ext = ".png" if "png" in part.inline_data.mime_type else ".jpg"
                    out_path = scenes_dir / f"{safe}_scene{ext}"
                    out_path.write_bytes(part.inline_data.data)
                    logger.info("Scene sheet saved: %s", out_path)
                    break
        except Exception as e:
            logger.error("Scene generation failed for %s: %s", scene_name, e)

    background_tasks.add_task(_gen)
    return {"status": "generating", "scene": scene_name}


@router.post("/api/book/{book_id}/characters/{char_name}/regenerate")
async def regenerate_character_sheet(
    book_id: str, char_name: str, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Regenerate character sheet for a specific character."""
    from src.generation.character_sheet import _safe_filename

    # Move existing sheet to history
    chars_dir = GENERATED_DIR / book_id / "characters"
    history_dir = chars_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(char_name)
    import time as _time
    ts = int(_time.time())
    for ext in (".png", ".jpg"):
        old = chars_dir / f"{safe}_sheet{ext}"
        if old.exists():
            old.rename(history_dir / f"{safe}_sheet_{ts}{ext}")

    async def _regen():
        from src.generation.character_sheet import generate_character_sheets
        llm_chars = _load_json(book_id, "llm_characters.json") or {}
        characters = llm_chars.get("characters", [])

        profile = None
        for c in characters:
            if c.get("canonical_name") == char_name:
                profile = {
                    "name": c["canonical_name"],
                    "role": c.get("role", "supporting"),
                    "gender": c.get("gender", "unknown"),
                    "personality_traits": [],
                    "appearance_description": [c.get("appearance", ""), c.get("description", "")],
                    "visual_details": c.get("visual_details", {}),
                }
                break

        if profile:
            generate_character_sheets([profile], book_id)

    background_tasks.add_task(_regen)
    return {"status": "regenerating", "character": char_name}


@router.post("/api/book/{book_id}/characters/{char_name}/quality")
async def check_character_sheet_quality_endpoint(
    book_id: str, char_name: str
) -> dict[str, Any]:
    """Run quality check on a character's reference sheet."""
    from src.generation.character_sheet import _safe_filename
    from src.generation.gemini_consistency_check import check_character_sheet_quality

    # Find the sheet image
    chars_dir = GENERATED_DIR / book_id / "characters"
    safe = _safe_filename(char_name)
    sheet_path = ""
    for ext in (".png", ".jpg"):
        candidate = chars_dir / f"{safe}_sheet{ext}"
        if candidate.exists():
            sheet_path = str(candidate)
            break
    if not sheet_path:
        raise HTTPException(status_code=404, detail=f"No sheet found for '{char_name}'.")

    # Load character info
    llm_chars = _load_json(book_id, "llm_characters.json") or {}
    appearance = ""
    visual_details = {}
    gender = "unknown"
    role = "supporting"
    for c in llm_chars.get("characters", []):
        if c.get("canonical_name") == char_name:
            appearance = c.get("appearance", "")
            visual_details = c.get("visual_details", {})
            gender = c.get("gender", "unknown")
            role = c.get("role", "supporting")
            break

    try:
        result = check_character_sheet_quality(
            sheet_path, char_name, appearance, visual_details, gender, role
        )
    except Exception as e:
        logger.error("Character sheet quality check failed for '%s': %s", char_name, e)
        raise HTTPException(status_code=500, detail=f"Quality check failed: {str(e)}")

    # Cache result
    quality_dir = chars_dir / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
    quality_file = quality_dir / f"{safe}_quality.json"
    quality_file.write_text(json.dumps(result, indent=2, default=str, ensure_ascii=False), encoding="utf-8")

    return result
