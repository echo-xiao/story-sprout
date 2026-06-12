"""Generation & quality check endpoints."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from src.config import GENERATED_DIR
from src.generation.character_sheet import _safe_filename
from src.routes.helpers import (
    _active_regens, _load_json, _require_user_key, _save_json,
    segment_page_num, write_json_atomic,
)
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

router = APIRouter()

# Chapters currently generating (book_id, ch_idx). Prevents a second subprocess
# from being spawned for a chapter that's already running (which would double-hit
# Gemini and race on progress.json / agent_log.json). Single-instance scope —
# matches the Cloud Run min-instances=1 deployment.
_active_generations: set[tuple[str, int]] = set()


def _restore_from_history(current_stem: Path, history_stem: Path) -> None:
    """Put back the file a regen moved to history when no new file appeared.

    Every regen endpoint moves the current image to history BEFORE generating
    (so the frontend's "new file appeared" poll works). If generation then
    fails — including the silent path where all Gemini attempts fail without
    raising — the asset would simply vanish and every dependent feature
    (quality check, reference feeding) starts 404ing until a manual restore.
    """
    if any(Path(f"{current_stem}{ext}").exists() for ext in (".png", ".jpg")):
        return  # a new file was generated — nothing to restore
    import shutil
    for ext in (".png", ".jpg"):
        h = Path(f"{history_stem}{ext}")
        if h.exists():
            try:
                shutil.copy2(h, Path(f"{current_stem}{ext}"))
                logger.warning("Regen produced no file — restored %s from history", current_stem.name)
            except OSError:
                pass
            return


@router.get("/api/book/{book_id}/chapter/{ch_idx}/agent-log")
async def get_agent_log(book_id: str, ch_idx: int) -> list[dict]:
    """Get agent activity log for a chapter generation session."""
    from src.agents.agent_log import get_log
    return get_log(book_id, ch_idx)


@router.get("/api/book/{book_id}/chapter/{ch_idx}/stale-pages")
async def get_stale_pages(book_id: str, ch_idx: int) -> dict[str, Any]:
    """Pages whose image is OLDER than a character/scene it depends on.

    A page is stale when any character in its `characters_in_scene` (exact), or a
    location matched in its `scene_background` (heuristic), has a reference-sheet
    file newer than the page image — i.e. the page should be regenerated. Pure
    mtime comparison; no persisted state.
    """

    analysis = _load_json(book_id, "analysis.json") or {}
    segments = analysis.get("segments", [])
    ch_segs = sorted(
        [s for s in segments if s.get("chapter_idx") == ch_idx],
        key=lambda s: s.get("id", 0),
    )
    base = GENERATED_DIR / book_id
    chars_dir = base / "characters"
    scenes_dir = base / "scenes"
    pages_dir = base / "chapters" / f"ch{ch_idx:02d}" / "pages"

    def _mtime(stem: Path) -> float | None:
        for ext in (".png", ".jpg"):
            p = Path(f"{stem}{ext}")
            if p.exists():
                return p.stat().st_mtime
        return None

    char_cache: dict[str, float | None] = {}
    scene_cache: dict[str, float | None] = {}
    locations = [loc for loc in (_load_json(book_id, "llm_locations.json") or {}).get("locations", []) if loc.get("name")]

    stale = []
    for idx, seg in enumerate(ch_segs):
        page_num = idx + 1
        page_mtime = _mtime(pages_dir / f"page_{page_num:03d}")
        if page_mtime is None:
            continue  # not generated yet — handled by the existing grey/green dot
        reasons = []
        for name in seg.get("characters_in_scene", []):
            if name not in char_cache:
                char_cache[name] = _mtime(chars_dir / f"{_safe_filename(name)}_sheet")
            m = char_cache[name]
            if m and m > page_mtime:
                reasons.append({"type": "character", "name": name})
        haystack = " ".join(
            (seg.get(f) or "")
            for f in ("scene_background", "scene_summary", "scene_direction", "text")
        ).lower()
        for loc in locations:
            ln = loc.get("name", "")
            needles = [ln.lower()] + [str(a).lower() for a in loc.get("aliases", [])]
            if any(n and n in haystack for n in needles):
                if ln not in scene_cache:
                    scene_cache[ln] = _mtime(scenes_dir / f"{_safe_filename(ln)}_scene")
                m = scene_cache[ln]
                if m and m > page_mtime:
                    reasons.append({"type": "scene", "name": ln})
        if reasons:
            stale.append({"page": page_num, "segment_id": seg.get("id"), "reasons": reasons})
    return {"stale": stale}


@router.post("/api/book/{book_id}/segment/{seg_id}/regenerate")
async def regenerate_segment_illustration(
    book_id: str, seg_id: int, background_tasks: BackgroundTasks,
    user_key: str = Depends(_require_user_key),
) -> dict[str, Any]:
    """Regenerate illustration for a single segment."""
    claim = (book_id, "segment", seg_id)
    if claim in _active_regens:
        raise HTTPException(status_code=409, detail="This page is already regenerating.")

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
    page_num = segment_page_num(segments, ch_idx, seg_id)

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
        from src.generation.text_simplifier import simplify_text
        from src.generation.illustration import generate_illustrations
        from src.generation.character_sheet import generate_character_sheets

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
            new_sheets = await run_in_threadpool(generate_character_sheets, chars_to_generate, book_id)
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
            result = await run_in_threadpool(simplify_text, [scene], "4-6")
            if result:
                simplified_text = result[0].get("page_text", "")
                scene_direction = result[0].get("scene_direction", "")
                # Save back under the editor's book lock, merging into a FRESH
                # read — writing the whole pre-LLM `analysis` snapshot here
                # clobbered any segment edit the user made during the call
                # (the exact anti-pattern editor.py's LLM endpoints fixed).
                from src.routes.editor import _analysis_lock
                async with _analysis_lock(book_id):
                    fresh = _load_json(book_id, "analysis.json") or {}
                    fseg = next((s for s in fresh.get("segments", []) if s.get("id") == seg_id), None)
                    if fseg is not None and not fseg.get("simplified_text"):
                        fseg["simplified_text"] = simplified_text
                        fseg["scene_direction"] = scene_direction
                        _save_json(book_id, "analysis.json", fresh)
                    elif fseg is not None:
                        # The user typed their own text mid-flight — theirs wins,
                        # and the illustration should embed it too.
                        simplified_text = fseg.get("simplified_text", simplified_text)
                        scene_direction = fseg.get("scene_direction", scene_direction)
                target["simplified_text"] = simplified_text
                target["scene_direction"] = scene_direction

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

        await run_in_threadpool(
            generate_illustrations,
            [page_prompt], character_sheets, book_id,
            pages_dir=str(ch_dir),
        )
        logger.info("Regeneration complete for segment %d (page %d)", seg_id, page_num)

        # Auto QA + bounded self-correction via the SHARED page service — same
        # policy/threshold the pipeline uses; the frontend only triggers regen.
        try:
            from src.generation.page_service import qa_and_self_correct

            def _find_page_image() -> str:
                for ext in (".png", ".jpg"):
                    img = ch_dir / f"page_{page_num:03d}{ext}"
                    if img.exists():
                        return str(img)
                return ""

            ill_path = _find_page_image()
            if ill_path:
                def _regen_fn(feedback: str) -> str:
                    generate_illustrations(
                        [page_prompt], character_sheets, book_id, None, str(ch_dir), feedback,
                    )
                    return _find_page_image()

                await run_in_threadpool(
                    qa_and_self_correct,
                    image_path=ill_path,
                    character_sheets=character_sheets,
                    expected_text=simplified_text or target.get("text", ""),
                    expected_characters=target.get("characters_in_scene", []),
                    page_num=page_num,
                    seg_id=seg_id,
                    history_dir=history_dir,
                    quality_path=ch_base / "quality" / f"page_{page_num:03d}_quality.json",
                    regenerate_fn=_regen_fn,
                )
                logger.info("Auto quality check done for segment %d", seg_id)
        except Exception as e:
            logger.warning("Auto quality check failed for segment %d: %s", seg_id, e)

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

        # The page image changed — the chapter-level consistency.json (served
        # verbatim by GET /chapter/{ch}/consistency) is stale now; drop it.
        try:
            consistency_path = ch_base / "consistency.json"
            if consistency_path.exists():
                consistency_path.unlink()
        except OSError:
            pass

        # Write completion marker (atomically — the frontend polls this file)
        import time as _t
        marker = ch_base / f"regen_{seg_id}.json"
        write_json_atomic(marker, {"status": "complete", "segment_id": seg_id, "page_number": page_num, "timestamp": _t.time()})

    # Clear old marker BEFORE starting task so status check returns "generating"
    marker = ch_base / f"regen_{seg_id}.json"
    if marker.exists():
        marker.unlink()

    async def _regen_safe():
        from src.gemini_backend import set_user_api_key, reset_user_api_key
        # BYOK — route this task's Gemini calls to the user's key, and reset the
        # contextvar afterwards so the key doesn't leak into the worker context.
        token = set_user_api_key(user_key)
        try:
            await _regen()
        except Exception as e:
            # Without this, any exception left no completion marker → the status
            # endpoint returned "generating" forever and the page image (already
            # moved to history) stayed blank. Restore it + write an error marker.
            logger.exception("Regen failed for segment %d", seg_id)
            try:
                import shutil as _sh
                for ext in (".png", ".jpg"):
                    h = history_dir / f"page_{page_num:03d}_{ts}{ext}"
                    if h.exists():
                        _sh.copy2(h, ch_dir / f"page_{page_num:03d}{ext}")
                        break
            except Exception:
                pass
            write_json_atomic(ch_base / f"regen_{seg_id}.json",
                              {"status": "error", "segment_id": seg_id, "error": str(e)[:300]})
        finally:
            reset_user_api_key(token)
            _active_regens.discard(claim)

    _active_regens.add(claim)
    background_tasks.add_task(_regen_safe)

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
        try:
            return json.loads(marker.read_text())
        except (json.JSONDecodeError, OSError):
            # Torn read racing the (now atomic) write, or a marker left by an
            # older version — report still-generating rather than a 500.
            return {"status": "generating"}
    return {"status": "generating"}


@router.post("/api/book/{book_id}/chapter/{ch_idx}/generate")
async def generate_chapter_endpoint(
    book_id: str, ch_idx: int, background_tasks: BackgroundTasks,
    user_key: str = Depends(_require_user_key),
) -> dict[str, Any]:
    """Generate illustrations for a chapter (text simplification + illustration)."""
    preprocess_dir = GENERATED_DIR / book_id / "preprocess"
    if not preprocess_dir.exists():
        raise HTTPException(status_code=404, detail="No preprocess data. Run preprocess first.")

    # Refuse to start a second subprocess for a chapter already generating.
    key = (book_id, ch_idx)
    if key in _active_generations:
        return {"status": "already_generating", "book_id": book_id, "chapter": ch_idx}

    # Initialize progress file BEFORE claiming the key: if this raises (e.g.
    # disk full), the key must not stay claimed — only _gen's finally releases
    # it, and _gen never runs. No await between the check above and the add
    # below, so the claim stays race-free on the event loop.
    progress_file = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "progress.json"
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    progress_file.write_text(json.dumps({"status": "starting", "progress": 0, "current_step": "Starting...", "total_pages": 0, "completed_pages": 0}))
    _active_generations.add(key)

    async def _gen():
        import asyncio as _asyncio
        import os
        import sys
        env = os.environ.copy()
        if user_key:  # BYOK — bill the user's key; else fall back to project Vertex
            env["GEMINI_API_KEY"] = user_key
            env["GEMINI_BACKEND"] = "api_key"
        try:
            proc = await _asyncio.create_subprocess_exec(
                # --self-correct: without it the QA stage is report-only — a
                # failing page is still marked complete and never retried.
                sys.executable, "scripts/generate_chapter.py", "--book", book_id, "--chapter", str(ch_idx),
                "--self-correct",
                cwd=str(Path(__file__).parent.parent.parent),
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                _stdout, _stderr = await _asyncio.wait_for(proc.communicate(), timeout=900)
            except _asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                logger.error("Chapter generation timed out for %s ch%02d", book_id, ch_idx)
                progress_file.write_text(json.dumps({"status": "failed", "progress": 100, "current_step": "Generation timed out", "total_pages": 0, "completed_pages": 0}))
                return
            if proc.returncode == 0:
                progress_file.write_text(json.dumps({"status": "complete", "progress": 100, "current_step": "Done", "total_pages": 0, "completed_pages": 0}))
            else:
                err_tail = (_stderr or b"").decode("utf-8", errors="replace")[-800:]
                logger.error("Chapter generation failed for %s ch%02d: %s", book_id, ch_idx, err_tail)
                progress_file.write_text(json.dumps({"status": "failed", "progress": 100, "current_step": "Generation failed", "total_pages": 0, "completed_pages": 0, "error": err_tail}))
        finally:
            _active_generations.discard(key)

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
        # Count only segments that actually become pages. build_scenes() skips
        # segments under 10 words, so counting every segment makes `total`
        # larger than the page files that ever get generated, and the
        # "completed >= total" completion shortcut below could never fire.
        total = sum(
            1 for s in analysis.get("segments", [])
            if s.get("chapter_idx") == ch_idx
            and len((s.get("text") or "").split()) >= 10
        )

    completed = len(list(ch_dir.glob("page_*.*"))) if ch_dir.exists() else 0

    # Consult progress.json FIRST: when an already-complete chapter is being
    # re-generated, the old page files still exist, so the file-count shortcut
    # below would instantly (and wrongly) report "complete" for the new run.
    progress_data = None
    progress_file = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "progress.json"
    if progress_file.exists():
        try:
            progress_data = json.loads(progress_file.read_text())
        except (json.JSONDecodeError, OSError):
            progress_data = None
    if progress_data is not None and progress_data.get("status") in ("starting", "generating"):
        # In-flight run — report its live status, with actual file counts.
        progress_data["completed_pages"] = completed
        progress_data["total_pages"] = total
        return progress_data

    # No in-flight run: if all pages exist, it's complete regardless of progress.json
    if completed >= total and total > 0:
        return {
            "status": "complete", "progress": 100,
            "current_step": "Done", "agent": "complete",
            "total_pages": total, "completed_pages": completed,
        }

    if progress_data is not None:
        # Terminal status (complete/failed) — override with actual file count for accuracy
        progress_data["completed_pages"] = completed
        progress_data["total_pages"] = total
        return progress_data

    if not ch_dir.exists() or completed == 0:
        return {"status": "not_started", "progress": 0, "current_step": "Not started", "total_pages": total, "completed_pages": 0}

    progress = int(completed / total * 100) if total > 0 else 0
    return {
        "status": "generating", "progress": progress,
        "current_step": f"Page {completed}/{total}",
        "total_pages": total, "completed_pages": completed,
    }


@router.post("/api/book/{book_id}/segment/{seg_id}/quality")
async def check_segment_quality(
    book_id: str, seg_id: int,
    _user_key: str | None = Depends(_require_user_key),  # belt to the middleware's suffix match
) -> dict[str, Any]:
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
    page_num = segment_page_num(segments, ch_idx, seg_id)

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
        result = await run_in_threadpool(check_page_quality, ill_path, character_sheets, page_text, scene_chars, page_num)
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
async def check_chapter_consistency(
    book_id: str, ch_idx: int,
    _user_key: str | None = Depends(_require_user_key),  # belt to the middleware's suffix match
) -> dict[str, Any]:
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

        result = await run_in_threadpool(check_page_quality, ill_path, relevant_sheets, page_text, scene_chars, page_num)
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
    style_result = await run_in_threadpool(check_style_consistency, ill_paths, reference_path=cover_path)

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
    user_key: str = Depends(_require_user_key),
) -> dict[str, Any]:
    """Regenerate a special page (book_cover, chapter_cover, chapter_ending, back_cover)."""
    claim = (book_id, "special", f"{page_type}:{chapter}")
    if claim in _active_regens:
        raise HTTPException(status_code=409, detail="This page is already regenerating.")

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

    async def _gen_inner():
        if page_type == "book_cover":
            char_profiles = [{"name": c["canonical_name"], "visual_identity": c.get("appearance", "")} for c in characters[:5]]
            await run_in_threadpool(generate_book_cover, title, char_profiles, book_id, character_sheets=character_sheets)
        elif page_type == "chapter_cover":
            ch_info = ch_segments.get(str(chapter), {})
            ch_title = ch_info.get("chapter_title", f"Chapter {chapter + 1}")
            ch_summary = ch_info.get("chapter_summary", "")
            char_profiles = [{"name": c["canonical_name"], "visual_identity": c.get("appearance", "")} for c in characters[:3]]
            # Pass 1-based chapter number to match the pipeline/PDF file naming.
            await run_in_threadpool(generate_chapter_cover, ch_title, chapter + 1, ch_summary, char_profiles, book_id, character_sheets=character_sheets)
        elif page_type == "chapter_ending":
            ch_info = ch_segments.get(str(chapter), {})
            ch_title = ch_info.get("chapter_title", f"Chapter {chapter + 1}")
            char_profiles = [{"name": c["canonical_name"], "visual_identity": c.get("appearance", "")} for c in characters[:3]]
            await run_in_threadpool(generate_chapter_ending, ch_title, chapter + 1, "", char_profiles, book_id, character_sheets=character_sheets)
        elif page_type == "back_cover":
            await run_in_threadpool(generate_back_cover, title, book_id, character_sheets=character_sheets)
        else:
            logger.error("Unknown special page type: %s", page_type)

    # Move the existing image aside FIRST (like the other regens) so the frontend's
    # "url appeared" completion check waits for the NEW image instead of instantly
    # "completing" on the unchanged old one ("regenerated but nothing changed").
    _base = {
        "book_cover": "book_cover",
        "chapter_cover": f"chapter_{chapter + 1:02d}_cover",
        "chapter_ending": f"chapter_{chapter + 1:02d}_ending",
        "back_cover": "back_cover",
    }.get(page_type)
    special_dir = GENERATED_DIR / book_id / "special"
    hist = special_dir / "history"
    import time as _t
    _ts = int(_t.time())
    if _base:
        hist.mkdir(parents=True, exist_ok=True)
        for _ext in (".png", ".jpg"):
            _old = special_dir / f"{_base}{_ext}"
            if _old.exists():
                try:
                    _old.rename(hist / f"{_base}_{_ts}{_ext}")
                except OSError:
                    pass

    async def _gen():
        from src.gemini_backend import set_user_api_key, reset_user_api_key
        # BYOK — set the user's key for this task and reset afterwards so it
        # doesn't leak into the worker context.
        token = set_user_api_key(user_key)
        try:
            await _gen_inner()
        except Exception:
            # Best-effort: the frontend's poll timeout covers the UI; log so the
            # failure isn't swallowed by the background-task runner.
            logger.exception("Special page regen failed for %s/%s", book_id, page_type)
        finally:
            reset_user_api_key(token)
            if _base:
                _restore_from_history(special_dir / _base, hist / f"{_base}_{_ts}")
            _active_regens.discard(claim)

    _active_regens.add(claim)
    background_tasks.add_task(_gen)
    return {"status": "generating", "page_type": page_type, "chapter": chapter}


@router.post("/api/book/{book_id}/scenes/{scene_name}/regenerate")
async def regenerate_scene_sheet(
    book_id: str, scene_name: str, background_tasks: BackgroundTasks,
    user_key: str = Depends(_require_user_key),
) -> dict[str, Any]:
    """Generate/regenerate a scene reference image for a location."""
    claim = (book_id, "scene", scene_name)
    if claim in _active_regens:
        raise HTTPException(status_code=409, detail="This scene is already regenerating.")

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

    async def _gen_inner():
        from src.config import IMAGE_LLM, DEFAULT_STYLE, NEGATIVE_PROMPT

        # Load location details
        llm_locs = _load_json(book_id, "llm_locations.json") or {}
        locations = llm_locs.get("locations", [])
        loc = next((loc for loc in locations if loc.get("name") == scene_name), None)
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

        def _gen_scene_image():
            from google import genai
            from src.config import GEMINI_IMAGE_MODEL
            from src.gemini_backend import make_genai_client
            client = make_genai_client()
            try:
                response = client.models.generate_content(
                    model=GEMINI_IMAGE_MODEL,
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                        # Keep scene sheets square like character sheets and book
                        # pages — the Vertex image model defaults to landscape.
                        image_config=genai.types.ImageConfig(aspect_ratio="1:1"),
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

        # Run the blocking Gemini call off the event loop.
        await run_in_threadpool(_gen_scene_image)

    async def _gen():
        from src.gemini_backend import set_user_api_key, reset_user_api_key
        # BYOK — set the user's key for this task and reset afterwards so it
        # doesn't leak into the worker context.
        token = set_user_api_key(user_key)
        try:
            await _gen_inner()
        except Exception:
            logger.exception("Scene sheet regen failed for %s/%s", book_id, scene_name)
        finally:
            reset_user_api_key(token)
            _restore_from_history(scenes_dir / f"{safe}_scene", history_dir / f"{safe}_scene_{ts}")
            _active_regens.discard(claim)

    _active_regens.add(claim)
    background_tasks.add_task(_gen)
    return {"status": "generating", "scene": scene_name}


def _run_character_sheet_quality(
    book_id: str, char_name: str, regenerate_fn=None,
) -> dict | None:
    """QA (and optionally self-correct) a character's reference sheet.

    Delegates to the SHARED sheet policy in page_service (lenient threshold).
    With regenerate_fn=None this is report-only — used by the manual quality
    endpoint; the regen flow passes a feedback-retry function.
    Returns the result, or None if no sheet exists yet.
    """
    from src.generation.page_service import sheet_qa_and_self_correct
    from src.routes.helpers import load_characters

    chars_dir = GENERATED_DIR / book_id / "characters"
    safe = _safe_filename(char_name)
    sheet_path = ""
    for ext in (".png", ".jpg"):
        candidate = chars_dir / f"{safe}_sheet{ext}"
        if candidate.exists():
            sheet_path = str(candidate)
            break
    if not sheet_path:
        return None

    appearance, visual_details, gender, role = "", {}, "unknown", "supporting"
    for c in load_characters(book_id):
        if c.get("canonical_name") == char_name:
            appearance = c.get("appearance", "")
            visual_details = c.get("visual_details", {})
            gender = c.get("gender", "unknown")
            role = c.get("role", "supporting")
            break

    return sheet_qa_and_self_correct(
        sheet_path=sheet_path,
        char_name=char_name,
        appearance=appearance,
        visual_details=visual_details,
        gender=gender,
        role=role,
        history_dir=chars_dir / "history",
        quality_path=chars_dir / "quality" / f"{safe}_quality.json",
        regenerate_fn=regenerate_fn,
    )


@router.post("/api/book/{book_id}/characters/{char_name}/regenerate")
async def regenerate_character_sheet(
    book_id: str, char_name: str, background_tasks: BackgroundTasks,
    user_key: str = Depends(_require_user_key),
) -> dict[str, Any]:
    """Regenerate character sheet for a specific character."""
    claim = (book_id, "character", char_name)
    if claim in _active_regens:
        raise HTTPException(status_code=409, detail="This character sheet is already regenerating.")

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

    async def _regen_inner():
        from src.generation.character_sheet import generate_character_sheets
        from src.routes.helpers import load_characters
        characters = load_characters(book_id)

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
            await run_in_threadpool(generate_character_sheets, [profile], book_id)

            # Auto QA + bounded self-correction (shared sheet policy, lenient
            # threshold — only a truly broken sheet retries with the feedback).
            def _sheet_regen_fn(feedback: str) -> str:
                sheets = generate_character_sheets(
                    [profile], book_id, correction_feedback=feedback,
                )
                return (sheets[0].get("sheet_path", "") if sheets else "") or ""

            try:
                await run_in_threadpool(
                    _run_character_sheet_quality, book_id, char_name, _sheet_regen_fn,
                )
            except Exception as e:
                logger.warning("Auto quality-check failed for %s: %s", char_name, e)

    async def _regen():
        from src.gemini_backend import set_user_api_key, reset_user_api_key
        # BYOK — set the user's key for this task and reset afterwards so it
        # doesn't leak into the worker context.
        token = set_user_api_key(user_key)
        try:
            await _regen_inner()
        except Exception:
            logger.exception("Character sheet regen failed for %s/%s", book_id, char_name)
        finally:
            reset_user_api_key(token)
            _restore_from_history(chars_dir / f"{safe}_sheet", history_dir / f"{safe}_sheet_{ts}")
            _active_regens.discard(claim)

    _active_regens.add(claim)
    background_tasks.add_task(_regen)
    return {"status": "regenerating", "character": char_name}


@router.post("/api/book/{book_id}/characters/{char_name}/quality")
async def check_character_sheet_quality_endpoint(
    book_id: str, char_name: str,
    _user_key: str | None = Depends(_require_user_key),  # belt to the middleware's suffix match
) -> dict[str, Any]:
    """Run quality check on a character's reference sheet."""
    result = await run_in_threadpool(_run_character_sheet_quality, book_id, char_name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No sheet found for '{char_name}'.")
    return result
