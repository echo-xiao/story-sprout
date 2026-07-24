"""Generation & quality check endpoints."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from src.config import GENERATED_DIR
from src.core import storage
from src.core.provenance import TEXT_SOURCE_WRITER, is_user_edited
from src.generation.character_sheet import _safe_filename
from src.routes.helpers import (
    _active_generations, _active_regens, _last_regen_errors, _load_json,
    _require_user_key, _save_json, book_generation_active, book_regen_active,
    invalidate_chapter_consistency, load_characters, make_character_name_resolver,
    segment_page_num, update_chapter_data_page, write_json_atomic,
)
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

router = APIRouter()


def _sheets_for(book_id: str, names: list[str]) -> list[dict]:
    """Reference-sheet entries for the named characters (exact filename match).
    The one sheet-lookup used by story pages AND special pages — keeps the two
    flows referencing characters identically.

    Prefers the selected immutable version (content-addressed, cross-page
    consistent) over the current mutable file; falls back to the current file
    when no version has been selected yet."""
    chars_dir = GENERATED_DIR / book_id / "characters"
    # Resolve each scene name (e.g. "Remarkable Rocket") to the character's
    # canonical name (e.g. "the Remarkable Rocket") before looking up its sheet
    # / version — the scene lists a short form the sheet file is NOT keyed by.
    # character_name stays the ORIGINAL scene name so downstream scene-based
    # matching (QA / consistency) still lines up with characters_in_scene.
    resolve = make_character_name_resolver(load_characters(book_id))
    out: list[dict] = []
    for name in names:
        canonical = resolve(name)
        sel = storage.selected_version_image(book_id, "character", canonical)
        if sel:
            out.append({"character_name": name, "sheet_path": sel})
            continue
        safe = _safe_filename(canonical)
        for ext in (".png", ".jpg"):
            # Materialize the durable (GCS) sheet to /tmp before the local read
            # — on a cold serverless invocation nothing is on local disk yet.
            storage.localize(f"{book_id}/characters/{safe}_sheet{ext}")
            p = chars_dir / f"{safe}_sheet{ext}"
            if p.exists():
                out.append({"character_name": name, "sheet_path": str(p)})
                break
    return out


def _restore_from_history(current_stem: Path, history_stem: Path,
                          quality_pair: tuple[Path, Path] | None = None) -> bool:
    """Put back the file a regen moved to history when no new file appeared.

    Every regen endpoint moves the current image to history BEFORE generating
    (so the frontend's "new file appeared" poll works). If generation then
    fails — including the silent path where all Gemini attempts fail without
    raising — the asset would simply vanish and every dependent feature
    (quality check, reference feeding) starts 404ing until a manual restore.

    quality_pair=(current_quality_path, history_quality_path): when the regen
    also archived the page's quality JSON, restore it alongside the image —
    otherwise the restored image shows as never-QA'd and the next chapter run
    burns a redundant vision call re-checking it.

    Returns True when the regen produced NO new file (whether or not a
    history copy existed to restore) — i.e. the run failed; callers use this
    to record a failure reason for GET /regen-active.
    """
    if any(Path(f"{current_stem}{ext}").exists() for ext in (".png", ".jpg")):
        return False  # a new file was generated — nothing to restore
    import shutil
    for ext in (".png", ".jpg"):
        h = Path(f"{history_stem}{ext}")
        if h.exists():
            try:
                shutil.copy2(h, Path(f"{current_stem}{ext}"))
                logger.warning("Regen produced no file — restored %s from history", current_stem.name)
            except OSError:
                pass
            if quality_pair is not None:
                cur_q, hist_q = quality_pair
                if hist_q.exists() and not cur_q.exists():
                    try:
                        cur_q.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(hist_q, cur_q)
                    except OSError:
                        pass
            return True
    return True


@router.get("/api/book/{book_id}/regen-active")
async def get_regen_active(book_id: str, kind: str, key: str) -> dict[str, Any]:
    """Whether a regeneration claim is currently held for one asset.

    Sheet/scene/special regens have no marker file — on failure they restore
    the old image, so to a file-existence poll failure looks identical to
    success. The frontend polls this: claim gone + no new file = failed.
    kind: "segment" | "character" | "scene" | "special"; key: seg id /
    character name / scene name / "type:chapter". `error` carries the last
    failure reason for the asset (cleared when a new regen claims it).
    """
    ident: Any = int(key) if kind == "segment" and key.lstrip("-").isdigit() else key
    claim = (book_id, kind, ident)
    return {"active": claim in _active_regens,
            "error": _last_regen_errors.get(claim)}


@router.get("/api/book/{book_id}/chapter/{ch_idx}/stale-pages")
async def get_stale_pages(book_id: str, ch_idx: int) -> dict[str, Any]:
    """Pages whose recorded provenance version-id differs from the currently-selected version.

    A page is stale when any character or scene ref it was generated against (stored in
    chapter_data.json as `refs`) no longer matches the currently-selected version-id for
    that asset. Pages with no `refs` (legacy, pre-Task-7) are never marked stale —
    fallback avoids false reds on a serverless regen-but-keep-selection.
    """
    from src.core import store as _store

    analysis = _load_json(book_id, "analysis.json") or {}
    segments = analysis.get("segments", [])
    ch_segs = sorted(
        [s for s in segments if s.get("chapter_idx") == ch_idx],
        key=lambda s: s.get("id", 0),
    )

    # Load chapter_data once — contains per-page refs (provenance) written at regen time.
    chapter_data = _store.get_json(f"{book_id}/chapters/ch{ch_idx:02d}/chapter_data.json") or {}
    pages_by_num: dict[int, dict] = {
        p["page_number"]: p
        for p in chapter_data.get("pages", [])
        if "page_number" in p
    }

    # Cache selected version ids to avoid repeated store reads for the same asset.
    _sel_cache: dict[tuple[str, str], str | None] = {}

    def _selected_id(asset_type: str, name: str) -> str | None:
        k = (asset_type, name)
        if k not in _sel_cache:
            _sel_cache[k] = (_store.get_selected_version(book_id, asset_type, name) or {}).get("id")
        return _sel_cache[k]

    stale = []
    for idx, seg in enumerate(ch_segs):
        page_num = idx + 1
        page_entry = pages_by_num.get(page_num)
        if page_entry is None:
            continue  # page not yet generated — grey dot; not stale
        refs = page_entry.get("refs")
        if refs is None:
            continue  # legacy page (no provenance recorded) — skip, avoid false red

        reasons = []
        for name, recorded_vid in (refs.get("characters") or {}).items():
            if not recorded_vid:
                continue  # a ref with no version at gen time is never stale (avoids false red); it won't turn red on a later change — accepted edge.
            if _selected_id("character", name) != recorded_vid:
                reasons.append({"type": "character", "name": name})
        for name, recorded_vid in (refs.get("scenes") or {}).items():
            if not recorded_vid:
                continue  # a ref with no version at gen time is never stale (avoids false red); it won't turn red on a later change — accepted edge.
            if _selected_id("scene", name) != recorded_vid:
                reasons.append({"type": "scene", "name": name})

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
    if book_generation_active(book_id):
        # The chapter subprocess writes page_NNN.* and chapter_data.json itself;
        # a concurrent single-page regen would move the same file to history
        # mid-run and the two writers would race.
        raise HTTPException(status_code=409,
                            detail="A chapter is currently generating for this book — wait for it to finish.")

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

    # Shared with _regen_safe's error box — generators append their real
    # failure reasons here (they swallow exceptions internally).
    _err_box: list[str] = []

    async def _regen():
        from src.generation.text_simplifier import simplify_text
        from src.generation.illustration import generate_illustrations
        from src.generation.character_sheet import generate_character_sheets

        # Step 1: Generate character sheets if missing
        chars_dir = GENERATED_DIR / book_id / "characters"
        chars_dir.mkdir(parents=True, exist_ok=True)
        character_sheets = []
        chars_to_generate = []

        # Read profiles from the consistency hub (characters collection,
        # file-fallback) — the SAME canonical source the editor reads, so a
        # rename/appearance edit can't leave generation drawing the old look.
        _all_chars = load_characters(book_id)
        by_canonical = {c.get("canonical_name"): c for c in _all_chars}
        # Resolve short scene names to canonical before every sheet/version/record
        # lookup, so a character the segment names by a short form isn't dropped.
        resolve = make_character_name_resolver(_all_chars)

        for name in target.get("characters_in_scene", []):
            canonical = resolve(name)
            sel = storage.selected_version_image(book_id, "character", canonical)
            if sel:
                character_sheets.append({"character_name": name, "sheet_path": sel})
                continue
            safe = _safe_filename(canonical)
            found = False
            for ext in (".png", ".jpg"):
                # Pull the sheet from GCS to /tmp before the local read — on a
                # cold serverless invocation the durable copy lives only in GCS.
                storage.localize(f"{book_id}/characters/{safe}_sheet{ext}")
                sheet_path = chars_dir / f"{safe}_sheet{ext}"
                if sheet_path.exists():
                    character_sheets.append({
                        "character_name": name,
                        "sheet_path": str(sheet_path),
                    })
                    found = True
                    break
            if not found:
                c = by_canonical.get(canonical)
                if c:
                    chars_to_generate.append({
                        "name": canonical,
                        "role": c.get("role", "supporting"),
                        "gender": c.get("gender", "unknown"),
                        "appearance_description": [c.get("appearance", ""), c.get("description", "")],
                        "visual_details": c.get("visual_details", {}),
                    })

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
            result = await run_in_threadpool(simplify_text, [scene])
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
                        fseg["text_source"] = TEXT_SOURCE_WRITER
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

        # generate_illustrations does NOT raise when every Gemini attempt fails —
        # it returns image_path="" and only logs. The current image was already
        # moved to history above, so without this check the page silently loses
        # its image while the marker below still reports "complete".
        if not any((ch_dir / f"page_{page_num:03d}{ext}").exists() for ext in (".png", ".jpg")):
            from src.gemini_backend import friendly_gen_error
            _restore_from_history(ch_dir / f"page_{page_num:03d}",
                                  history_dir / f"page_{page_num:03d}_{ts}",
                                  quality_pair=(ch_base / "quality" / f"page_{page_num:03d}_quality.json",
                                                history_dir / f"page_{page_num:03d}_{ts}_quality.json"))
            write_json_atomic(ch_base / f"regen_{seg_id}.json",
                              {"status": "error", "segment_id": seg_id,
                               "error": friendly_gen_error(_err_box)
                                        or "Image generation failed (all attempts)."})
            return
        logger.info("Regeneration complete for segment %d (page %d)", seg_id, page_num)

        # Auto QA + bounded self-correction via the SHARED page service — same
        # policy/threshold the pipeline uses; the frontend only triggers regen.
        # Capture the QA result so we can attach it to the recorded version below.
        _page_qa_result: dict | None = None
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

                _page_qa_result = await run_in_threadpool(
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

        # Durable storage + register the final page image as a pickable version.
        # Attach the QA result to the version so it survives across regens/version-switches.
        for _ext in (".png", ".jpg"):
            _pimg = ch_dir / f"page_{page_num:03d}{_ext}"
            if _pimg.exists():
                try:
                    from src.core.storage import record_image_version
                    _vid = record_image_version(
                        book_id, "page", f"ch{ch_idx:02d}:p{page_num:03d}",
                        _pimg.read_bytes(),
                        content_type="image/png" if _ext == ".png" else "image/jpeg",
                    )
                    if _page_qa_result and _page_qa_result.get("overall_score") is not None:
                        try:
                            from src.core import store as _store
                            _store.set_version_quality(
                                book_id, "page", f"ch{ch_idx:02d}:p{page_num:03d}",
                                _vid, _page_qa_result,
                            )
                        except Exception as _qe:
                            logger.warning("page version QA attach failed: %s", _qe)
                except Exception as _e:
                    logger.warning("page version record failed: %s", _e)
                break


        # Keep chapter_data.json (what the combined PDF reads) pointing at the
        # new image + text — a regen that switched extensions used to leave a
        # dead path there, silently blanking this page in the next book.pdf.
        # Also record provenance (which version-ids were used) so get_stale_pages
        # can do a version-id comparison instead of fragile file-mtime comparison.
        from src.core import store as _store
        _char_refs = {
            n: (_store.get_selected_version(book_id, "character", n) or {}).get("id")
            for n in target.get("characters_in_scene", [])
        }
        _scene_refs: dict = {}
        _bg = " ".join(
            (target.get(f) or "")
            for f in ("scene_background", "scene_summary", "scene_direction", "text")
        ).lower()
        for _loc in (_load_json(book_id, "llm_locations.json") or {}).get("locations", []):
            _ln = _loc.get("name", "")
            if _ln and any(
                nm and nm.lower() in _bg
                for nm in [_ln] + [str(a) for a in _loc.get("aliases", [])]
            ):
                _scene_refs[_ln] = (_store.get_selected_version(book_id, "scene", _ln) or {}).get("id")
                break  # records one scene, 1:1 with _find_scene_sheet's first-match behavior.
        _refs = {"characters": _char_refs, "scenes": _scene_refs}
        for ext in (".png", ".jpg"):
            img = ch_dir / f"page_{page_num:03d}{ext}"
            if img.exists():
                update_chapter_data_page(book_id, ch_idx, page_num,
                                         image_path=str(img),
                                         text=simplified_text or target.get("text", ""),
                                         refs=_refs)
                break

        # The page image changed — the chapter-level consistency.json (served
        # verbatim by GET /chapter/{ch}/consistency) is stale now; drop it.
        invalidate_chapter_consistency(book_id, ch_idx)

        # Write completion marker (atomically — the frontend polls this file)
        import time as _t
        marker = ch_base / f"regen_{seg_id}.json"
        write_json_atomic(marker, {"status": "complete", "segment_id": seg_id, "page_number": page_num, "timestamp": _t.time()})

    # Clear old marker BEFORE starting task so status check returns "generating"
    marker = ch_base / f"regen_{seg_id}.json"
    if marker.exists():
        marker.unlink()

    async def _regen_safe():
        from src.gemini_backend import (
            reset_gen_error_box, set_gen_error_box,
            set_user_api_key, reset_user_api_key,
        )
        # BYOK — route this task's Gemini calls to the user's key, and reset the
        # contextvar afterwards so the key doesn't leak into the worker context.
        token = set_user_api_key(user_key)
        # _err_box collects the generators' real errors (they swallow their own
        # exceptions) so the marker can say WHY — e.g. free-tier key, zero quota.
        box_token = set_gen_error_box(_err_box)
        try:
            await _regen()
        except Exception as e:
            # Without this, any exception left no completion marker → the status
            # endpoint returned "generating" forever and the page image (already
            # moved to history) stayed blank. Restore it + write an error marker.
            logger.exception("Regen failed for segment %d", seg_id)
            # Restores ONLY if no new file was generated — the old inline copy
            # here clobbered a freshly generated image when the exception came
            # from a later step (e.g. the marker write).
            _restore_from_history(ch_dir / f"page_{page_num:03d}",
                                  history_dir / f"page_{page_num:03d}_{ts}",
                                  quality_pair=(ch_base / "quality" / f"page_{page_num:03d}_quality.json",
                                                history_dir / f"page_{page_num:03d}_{ts}_quality.json"))
            write_json_atomic(ch_base / f"regen_{seg_id}.json",
                              {"status": "error", "segment_id": seg_id, "error": str(e)[:300]})
        finally:
            reset_gen_error_box(box_token)
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

    # Don't QA an image that's being replaced right now — the regen moves it to
    # history and writes a new one, so a check here would score the old image
    # and overwrite the fresh quality cache.
    if (book_id, "segment", seg_id) in _active_regens or book_generation_active(book_id):
        raise HTTPException(status_code=409, detail="This page is regenerating — check quality when it finishes.")

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
    # Consistency hub (characters collection, file-fallback) — same source the
    # editor reads, so appearance text stays in sync with the sheet image.
    char_profiles = load_characters(book_id)
    resolve = make_character_name_resolver(char_profiles)
    by_canonical = {c.get("canonical_name"): c for c in char_profiles}
    scene_chars = target.get("characters_in_scene", [])
    character_sheets = []
    for name in scene_chars:
        canonical = resolve(name)
        safe = _safe_filename(canonical)
        for ext in (".png", ".jpg"):
            sheet_path = chars_dir / f"{safe}_sheet{ext}"
            if sheet_path.exists():
                # Appearance for visual_identity — keyed off the resolved record.
                appearance = (by_canonical.get(canonical) or {}).get("appearance", "")
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
    try:
        from src.core import store
        store.put_json(str(quality_file.relative_to(GENERATED_DIR)), result)
    except Exception as e:
        logger.warning("QA result GCS persist failed for %s: %s", quality_file, e)

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
    character_sheets = []
    for char in load_characters(book_id):
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
    """Regenerate a special page (book_cover, chapter_cover, back_cover).

    Record-driven: characters/background/texts come from the page's editable
    record (preprocess-derived, editor-updated) — the SAME flow story pages
    use — and matching character sheets + scene sheet are fed as references.
    """
    from src.generation.special_page_data import SPECIAL_TYPES, special_key

    if page_type not in SPECIAL_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown special page type '{page_type}'.")
    claim = (book_id, "special", f"{page_type}:{chapter}")
    if claim in _active_regens:
        raise HTTPException(status_code=409, detail="This page is already regenerating.")

    from src.generation.special_pages import (
        generate_book_cover, generate_chapter_cover, generate_back_cover,
    )
    from src.routes.editor import load_special_records

    record = load_special_records(book_id).get(special_key(page_type, chapter), {})
    rec_chars: list[str] = list(record.get("characters_in_scene") or [])
    background = record.get("scene_background", "")

    # Character sheets: exactly the record's characters (same lookup as story
    # pages). Fall back to all sheets when the record names none.
    character_sheets = _sheets_for(book_id, rec_chars)
    if not character_sheets:
        chars_dir = GENERATED_DIR / book_id / "characters"
        if chars_dir.exists():
            for f in chars_dir.glob("*_sheet.*"):
                name = f.stem.replace("_sheet", "").replace("_", " ").title()
                character_sheets.append({"character_name": name, "sheet_path": str(f)})

    # Scene reference sheet matched from the record's background (story pages'
    # matcher — covers now reference scenes the same way).
    from src.generation.illustration import _find_scene_sheet
    scene_sheet = _find_scene_sheet(book_id, background) if background else None

    # Visual identities for the record's characters (canonical store first).
    from src.routes.helpers import load_characters
    all_chars = load_characters(book_id)
    by_name = {c.get("canonical_name"): c for c in all_chars}
    if rec_chars:
        char_profiles = [
            {"name": n, "visual_identity": (by_name.get(n) or {}).get("appearance", "")}
            for n in rec_chars
        ]
    else:
        char_profiles = [
            {"name": c.get("canonical_name", ""), "visual_identity": c.get("appearance", "")}
            for c in all_chars[:5]
        ]

    meta = _load_json(book_id, "meta.json") or {}
    title = record.get("title_text") or meta.get("title", book_id)
    subtitle = record.get("subtitle_text", "")
    summary = record.get("scene_summary", "")
    ch_segments = _load_json(book_id, "chapter_segments.json") or {}

    async def _gen_inner():
        if page_type == "book_cover":
            await run_in_threadpool(
                generate_book_cover, title, char_profiles, book_id,
                character_sheets=character_sheets, scene_sheet_path=scene_sheet,
                subtitle=subtitle or "A Picture Book", background=background,
            )
        elif page_type == "chapter_cover":
            ch_info = ch_segments.get(str(chapter), {})
            ch_title = record.get("title_text") or ch_info.get("chapter_title", f"Chapter {chapter + 1}")
            ch_summary = summary or ch_info.get("chapter_summary", "")
            # Pass 1-based chapter number to match the pipeline/PDF file naming.
            await run_in_threadpool(
                generate_chapter_cover, ch_title, chapter + 1, ch_summary,
                char_profiles, book_id,
                character_sheets=character_sheets, scene_sheet_path=scene_sheet,
                background=background,
            )
        elif page_type == "back_cover":
            await run_in_threadpool(
                generate_back_cover, meta.get("title", book_id), book_id,
                character_sheets=character_sheets, scene_sheet_path=scene_sheet,
                title_text=record.get("title_text") or "The End",
                subtitle_text=subtitle or "Thank you for reading!",
                background=background,
            )

    # Move the existing image aside FIRST (like the other regens) so the frontend's
    # "url appeared" completion check waits for the NEW image instead of instantly
    # "completing" on the unchanged old one ("regenerated but nothing changed").
    from src.generation.special_page_data import special_file_base
    _base = special_file_base(page_type, chapter)
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
        from src.gemini_backend import (
            friendly_gen_error, reset_gen_error_box, set_gen_error_box,
            set_user_api_key, reset_user_api_key,
        )
        # BYOK — set the user's key for this task and reset afterwards so it
        # doesn't leak into the worker context.
        token = set_user_api_key(user_key)
        # Error box: the generators retry + swallow exceptions internally, so
        # str(e) here is usually empty — the box captures the REAL reason
        # (e.g. free-tier key with zero image quota → "use a billing-enabled key").
        box: list[str] = []
        box_token = set_gen_error_box(box)
        err = ""
        try:
            await _gen_inner()
            # Durable storage + register the new special page as a pickable version.
            if _base:
                for _ext in (".png", ".jpg"):
                    _sp = special_dir / f"{_base}{_ext}"
                    if _sp.exists():
                        try:
                            from src.core.storage import record_image_version
                            # No QA on this path — special page regen skips quality-check intentionally.
                            record_image_version(
                                book_id, "special", f"{page_type}:{chapter}",
                                _sp.read_bytes(),
                                content_type="image/png" if _ext == ".png" else "image/jpeg",
                            )
                        except Exception as _e:
                            logger.warning("special version record failed: %s", _e)
                        break
        except Exception as e:
            # Best-effort: the frontend's poll timeout covers the UI; log so the
            # failure isn't swallowed by the background-task runner.
            logger.exception("Special page regen failed for %s/%s", book_id, page_type)
            err = str(e)[:300]
        finally:
            reset_gen_error_box(box_token)
            reset_user_api_key(token)
            if _base and _restore_from_history(special_dir / _base, hist / f"{_base}_{_ts}"):
                _last_regen_errors[claim] = (
                    friendly_gen_error(box) or err
                    or "Generation produced no image (check API key / quota)."
                )
            _active_regens.discard(claim)

    _active_regens.add(claim)
    _last_regen_errors.pop(claim, None)
    background_tasks.add_task(_gen)
    return {"status": "generating", "page_type": page_type, "chapter": chapter}


@router.post("/api/book/{book_id}/special/{page_type}/quality")
async def check_special_page_quality(
    book_id: str, page_type: str, chapter: int = 0,
    _user_key: str | None = Depends(_require_user_key),  # belt to the middleware's suffix match
) -> dict[str, Any]:
    """Quality check for a special page — story pages' checker against the
    page's record (expected text = its title/subtitle, expected characters =
    its characters_in_scene)."""
    from src.generation.gemini_consistency_check import check_page_quality
    from src.generation.special_page_data import SPECIAL_TYPES, special_file_base, special_key
    from src.routes.editor import load_special_records
    from src.routes.helpers import load_characters

    if page_type not in SPECIAL_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown special page type '{page_type}'.")
    if (book_id, "special", f"{page_type}:{chapter}") in _active_regens:
        raise HTTPException(status_code=409, detail="This page is regenerating — check quality when it finishes.")
    base = special_file_base(page_type, chapter) or ""
    special_dir = GENERATED_DIR / book_id / "special"
    ill_path = ""
    for ext in (".png", ".jpg"):
        candidate = special_dir / f"{base}{ext}"
        if candidate.exists():
            ill_path = str(candidate)
            break
    if not ill_path:
        raise HTTPException(status_code=404, detail="No image found for this special page.")

    record = load_special_records(book_id).get(special_key(page_type, chapter), {})
    names = list(record.get("characters_in_scene") or [])
    sheets = _sheets_for(book_id, names)
    by_name = {c.get("canonical_name"): c for c in load_characters(book_id)}
    for s in sheets:
        s["visual_identity"] = (by_name.get(s["character_name"]) or {}).get("appearance", "")
    expected_text = " ".join(
        t for t in (record.get("title_text", ""), record.get("subtitle_text", "")) if t
    )

    try:
        result = await run_in_threadpool(
            check_page_quality, ill_path, sheets, expected_text, names, 0,
        )
    except Exception as e:
        logger.error("Quality check failed for special %s/%s: %s", book_id, page_type, e)
        raise HTTPException(status_code=500, detail=f"Quality check failed: {str(e)}")
    result["special_type"] = page_type
    result["chapter"] = chapter

    quality_dir = special_dir / "quality"
    write_json_atomic(quality_dir / f"{base}_quality.json", result)
    try:
        from src.core import store
        store.put_json(str((quality_dir / f"{base}_quality.json").relative_to(GENERATED_DIR)), result)
    except Exception as e:
        logger.warning("QA result GCS persist failed for %s: %s", quality_dir / f"{base}_quality.json", e)
    return result


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
        from src.config import DEFAULT_STYLE, NEGATIVE_PROMPT

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

        def _gen_scene_image():
            from google import genai
            from src.config import GEMINI_IMAGE_MODEL
            from src.gemini_backend import (
                make_genai_client, call_gemini_with_backoff, note_gen_failure,
            )
            from src.generation.illustration import _build_reference_content
            from src.generation.special_pages import get_style_ref
            from src.generation.image_utils import save_inline_image
            client = make_genai_client()
            # Anchor every scene to the SAME book-wide style reference (the cover)
            # through the shared reference builder pages already use. This path
            # used to send contents=prompt (text only, zero visual anchor), so
            # each regen drifted in style. Empty character_sheets — a scene sheet
            # is background-only. If the cover is missing the builder degrades to
            # a plain text prompt, so this is safe before a cover exists.
            cover = get_style_ref(book_id)
            scene_contents = _build_reference_content(prompt, [], style_ref_path=cover)
            save_path = scenes_dir / f"{safe}_scene"

            # Go through the SHARED retry + save path (like pages/characters):
            # call_gemini_with_backoff retries a 200-with-no-image reply, and
            # save_inline_image mirrors to GCS + logs why a reply had no image.
            # Previously a raw call silently no-op'd on a no-image reply, and the
            # outer finally restored the OLD scene — the "regen does nothing" bug.
            def _attempt() -> str:
                response = client.models.generate_content(
                    model=GEMINI_IMAGE_MODEL,
                    contents=scene_contents,
                    config=genai.types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                        # Keep scene sheets square like character sheets and book
                        # pages — the image model defaults to landscape.
                        image_config=genai.types.ImageConfig(aspect_ratio="1:1"),
                    ),
                )
                return save_inline_image(response, save_path)

            try:
                final = call_gemini_with_backoff(_attempt, max_retries=3, label=f"scene:{safe}")
            except Exception as e:
                logger.error("Scene generation failed for %s: %s", scene_name, e)
                note_gen_failure(e)
                return
            if final:
                logger.info("Scene sheet saved: %s", final)
                try:
                    from src.core.storage import record_image_version
                    # No QA on this path — scene regen skips quality-check intentionally.
                    record_image_version(
                        book_id, "scene", scene_name, Path(final).read_bytes(),
                        content_type="image/png" if final.endswith(".png") else "image/jpeg",
                    )
                except Exception as _e:
                    logger.warning("scene version record failed: %s", _e)

        # Run the blocking Gemini call off the event loop.
        await run_in_threadpool(_gen_scene_image)

    async def _gen():
        from src.gemini_backend import (
            friendly_gen_error, reset_gen_error_box, set_gen_error_box,
            set_user_api_key, reset_user_api_key,
        )
        # BYOK — set the user's key for this task and reset afterwards so it
        # doesn't leak into the worker context.
        token = set_user_api_key(user_key)
        box: list[str] = []
        box_token = set_gen_error_box(box)
        err = ""
        try:
            await _gen_inner()
        except Exception as e:
            logger.exception("Scene sheet regen failed for %s/%s", book_id, scene_name)
            err = str(e)[:300]
        finally:
            reset_gen_error_box(box_token)
            reset_user_api_key(token)
            if _restore_from_history(scenes_dir / f"{safe}_scene", history_dir / f"{safe}_scene_{ts}"):
                _last_regen_errors[claim] = (
                    friendly_gen_error(box) or err
                    or "Generation produced no image (check API key / quota)."
                )
            _active_regens.discard(claim)

    _active_regens.add(claim)
    _last_regen_errors.pop(claim, None)
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
            # force=True: a user-initiated regen must ALWAYS redraw, never reuse a
            # stale /tmp image that storage.localize left behind on a warm
            # serverless instance (the "regen does nothing / no new version" bug).
            await run_in_threadpool(generate_character_sheets, [profile], book_id, force=True)

            # Auto QA + bounded self-correction (shared sheet policy, lenient
            # threshold — only a truly broken sheet retries with the feedback).
            def _sheet_regen_fn(feedback: str) -> str:
                sheets = generate_character_sheets(
                    [profile], book_id, correction_feedback=feedback,
                )
                return (sheets[0].get("sheet_path", "") if sheets else "") or ""

            _char_qa_result = None
            try:
                _char_qa_result = await run_in_threadpool(
                    _run_character_sheet_quality, book_id, char_name, _sheet_regen_fn,
                )
            except Exception as e:
                logger.warning("Auto quality-check failed for %s: %s", char_name, e)

            # Durable storage: register the final post-QA sheet as a pickable
            # version (spec §11.2 rule 2 — mirrors the page regen path).
            # Attach the QA result to the version so it survives regens/switches.
            for _ext in (".png", ".jpg"):
                _sheet = chars_dir / f"{safe}_sheet{_ext}"
                if _sheet.exists():
                    try:
                        from src.core.storage import record_image_version
                        _char_vid = record_image_version(
                            book_id, "character", char_name,
                            _sheet.read_bytes(),
                            content_type="image/png" if _ext == ".png" else "image/jpeg",
                        )
                        if _char_qa_result and _char_qa_result.get("overall_score") is not None:
                            try:
                                from src.core import store as _cstore
                                _cstore.set_version_quality(
                                    book_id, "character", char_name,
                                    _char_vid, _char_qa_result,
                                )
                            except Exception as _qe:
                                logger.warning("character version QA attach failed: %s", _qe)
                    except Exception as _e:
                        logger.warning("character sheet version record failed: %s", _e)
                    break

    async def _regen():
        from src.gemini_backend import (
            friendly_gen_error, reset_gen_error_box, set_gen_error_box,
            set_user_api_key, reset_user_api_key,
        )
        # BYOK — set the user's key for this task and reset afterwards so it
        # doesn't leak into the worker context.
        token = set_user_api_key(user_key)
        box: list[str] = []
        box_token = set_gen_error_box(box)
        err = ""
        try:
            await _regen_inner()
        except Exception as e:
            logger.exception("Character sheet regen failed for %s/%s", book_id, char_name)
            err = str(e)[:300]
        finally:
            reset_gen_error_box(box_token)
            reset_user_api_key(token)
            if _restore_from_history(chars_dir / f"{safe}_sheet", history_dir / f"{safe}_sheet_{ts}"):
                _last_regen_errors[claim] = (
                    friendly_gen_error(box) or err
                    or "Generation produced no image (check API key / quota)."
                )
            _active_regens.discard(claim)

    _active_regens.add(claim)
    _last_regen_errors.pop(claim, None)
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
