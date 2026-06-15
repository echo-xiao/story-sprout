"""Editor/segment endpoints."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import asyncio

from src.config import GENERATED_DIR
from src.generation.character_sheet import _safe_filename
from src.routes.helpers import (
    _active_regens, _load_json, _require_user_key, _save_json, book_generation_active,
    invalidate_chapter_consistency, segment_page_num, update_chapter_data_page,
)
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

router = APIRouter()

# Per-book lock so concurrent edits serialize their analysis.json read-modify-write
# instead of clobbering each other (last-writer-wins lost updates).
_analysis_locks: dict[str, asyncio.Lock] = {}


def _analysis_lock(book_id: str) -> asyncio.Lock:
    return _analysis_locks.setdefault(book_id, asyncio.Lock())


# ---------------------------------------------------------------------------
# Version selection — pick which generated version of an image is the one used.
# ONE endpoint for pages / scenes / characters (replaces the per-type
# restore-version endpoints). Pure pointer write: it never generates, so
# clicking a thumbnail can't spawn a new version. Owner-gated by the app.py
# middleware (any write to /api/book/{id}/...) plus the BYOK key gate.
# ---------------------------------------------------------------------------

class SelectVersionRequest(BaseModel):
    version_id: str


@router.post("/api/book/{book_id}/asset/{asset_type}/{asset_key:path}/select")
async def select_asset_version(
    book_id: str, asset_type: str, asset_key: str, req: SelectVersionRequest,
    _user_key: str | None = Depends(_require_user_key),
) -> dict[str, Any]:
    """Set the selected version for an asset. asset_type ∈ page|scene|character;
    asset_key is the page key / location name / canonical character name."""
    if asset_type not in ("page", "scene", "character", "special"):
        raise HTTPException(status_code=400, detail="Invalid asset type.")
    from src.core.db import set_selected_version
    ok = await run_in_threadpool(
        set_selected_version, book_id, asset_type, asset_key, req.version_id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Version not found for this asset.")
    return {"status": "selected", "version_id": req.version_id}


@router.get("/api/book/{book_id}/asset/{asset_type}/{asset_key:path}/versions")
async def list_asset_versions_endpoint(
    book_id: str, asset_type: str, asset_key: str,
) -> dict[str, Any]:
    """All versions + the selected id for an asset (read-only, open)."""
    if asset_type not in ("page", "scene", "character", "special"):
        raise HTTPException(status_code=400, detail="Invalid asset type.")
    from src.core.db import list_asset_versions
    return await run_in_threadpool(list_asset_versions, book_id, asset_type, asset_key)


def _find_segment(analysis: dict, seg_id: int) -> dict | None:
    return next((s for s in analysis.get("segments", []) if s.get("id") == seg_id), None)


def _load_segment_or_404(book_id: str, seg_id: int) -> dict:
    """Read-only snapshot of a segment, e.g. for building an LLM prompt."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data.")
    target = _find_segment(analysis, seg_id)
    if not target:
        raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")
    return target


def _invalidate_page_quality(book_id: str, segments: list[dict], seg_id: int) -> None:
    """Drop the cached text-image-match verdict after a text change (best-effort)."""
    try:
        target = next((s for s in segments if s.get("id") == seg_id), None)
        ch_idx = (target or {}).get("chapter_idx", 0)
        page_num = segment_page_num(segments, ch_idx, seg_id)
        quality_file = (
            GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}"
            / "quality" / f"page_{page_num:03d}_quality.json"
        )
        if quality_file.exists():
            quality_file.unlink()
    except OSError as e:
        logger.debug("Could not invalidate quality cache for segment %d: %s", seg_id, e)


async def _merge_segment_fields(book_id: str, seg_id: int, fields: dict) -> None:
    """Re-read analysis under the book lock, apply `fields` to one segment, save.

    The LLM endpoints call this AFTER their (seconds-long) LLM call: writing the
    whole-file snapshot taken before the call would clobber any concurrent
    segment edits made while the LLM was running.
    """
    async with _analysis_lock(book_id):
        analysis = _load_json(book_id, "analysis.json")
        if not analysis:
            raise HTTPException(status_code=404, detail="No analysis data.")
        target = _find_segment(analysis, seg_id)
        if not target:
            raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")
        target.update(fields)
        _save_json(book_id, "analysis.json", analysis)
    if "simplified_text" in fields or "text" in fields:
        _invalidate_page_quality(book_id, analysis.get("segments", []), seg_id)
        ch_idx = target.get("chapter_idx", 0)
        # Keep chapter_data.json (the PDF's text source) in step — the manual
        # edit endpoint does this, but the LLM paths (simplify / chat) route
        # through here and used to leave the PDF printing the old text.
        if "simplified_text" in fields:
            update_chapter_data_page(
                book_id, ch_idx,
                segment_page_num(analysis.get("segments", []), ch_idx, seg_id),
                text=fields["simplified_text"],
            )
        invalidate_chapter_consistency(book_id, ch_idx)


@router.get("/api/book/{book_id}/preprocess/chapters")
async def get_chapters(book_id: str) -> dict[str, Any]:
    """Get chapter list with segment counts."""
    chapter_segments = _load_json(book_id, "chapter_segments.json")
    meta = _load_json(book_id, "meta.json")
    if not chapter_segments:
        raise HTTPException(status_code=404, detail="No preprocess data found.")
    return {"meta": meta, "chapters": chapter_segments}


@router.get("/api/book/{book_id}/preprocess/characters")
async def get_characters(book_id: str) -> dict[str, Any]:
    """Get character list with sheets and gender info.

    Read from the canonical `characters` collection first \u2014 it survives a
    failed re-preprocess that may have blanked the preprocess_files JSON \u2014
    and fall back to llm_characters.json only if the collection is empty.
    """
    chars: list = []
    try:
        from src.core.db import get_characters as _get_chars_db
        chars = _get_chars_db(book_id)
    except Exception:
        chars = []
    if not chars:
        llm_chars = _load_json(book_id, "llm_characters.json")
        chars = llm_chars.get("characters", []) if llm_chars else []
    genders = _load_json(book_id, "character_genders.json") or {}
    alias_map = _load_json(book_id, "alias_map.json") or {}

    # Find character sheet + portrait images
    import re as _re
    chars_dir = GENERATED_DIR / book_id / "characters"
    sheets = {}
    portraits = {}
    if chars_dir.exists():
        sheet_files = {f.stem.replace("_sheet", ""): f for f in chars_dir.glob("*_sheet.*")}
        portrait_files = {f.stem.replace("_portrait", ""): f for f in chars_dir.glob("*_portrait.*")}
        for char in chars:
            name = char.get("canonical_name", "")
            safe = _re.sub(r'[^\w\s\u4e00-\u9fff-]', '', name)
            safe = _re.sub(r'\s+', '_', safe.strip()).lower()[:50]
            if safe in sheet_files:
                sheets[name] = f"/static/{book_id}/characters/{sheet_files[safe].name}"
            if safe in portrait_files:
                portraits[name] = f"/static/{book_id}/characters/{portrait_files[safe].name}"

    return {
        "characters": chars,
        "genders": genders,
        "alias_map": alias_map,
        "sheets": sheets,
        "portraits": portraits,
    }


class CharacterUpdate(BaseModel):
    canonical_name: Optional[str] = None
    gender: Optional[str] = None
    role: Optional[str] = None
    appearance: Optional[str] = None
    description: Optional[str] = None
    aliases: Optional[list[str]] = None
    visual_details: Optional[dict[str, Any]] = None


def _cascade_character_rename(book_id: str, old_name: str, new_name: str) -> None:
    """Propagate a character rename everywhere the old name is referenced.

    update_character only renames the one character row; without this every
    segment's characters_in_scene / character_actions (across ALL chapters), the
    alias map, the gender map and the sheet/portrait image files keep the stale
    name, so a rename silently half-applies and reverts on refresh.
    """
    if not new_name or old_name == new_name:
        return

    # 1) Segments across ALL chapters (analysis.json is the editor's source of truth)
    analysis = _load_json(book_id, "analysis.json")
    changed_ids: list = []
    if analysis:
        for seg in analysis.get("segments", []):
            touched = False
            cis = seg.get("characters_in_scene")
            if isinstance(cis, list) and old_name in cis:
                seg["characters_in_scene"] = [new_name if c == old_name else c for c in cis]
                touched = True
            for action in seg.get("character_actions", []) or []:
                if isinstance(action, dict) and action.get("name") == old_name:
                    action["name"] = new_name
                    touched = True
            if touched:
                changed_ids.append(seg.get("id"))
        if changed_ids:
            _save_json(book_id, "analysis.json", analysis)

    # 2) Alias map — repoint both alias keys and canonical values
    alias_map = _load_json(book_id, "alias_map.json") or {}
    if alias_map:
        remapped = {}
        for alias, canon in alias_map.items():
            alias = new_name if alias == old_name else alias
            canon = new_name if canon == old_name else canon
            remapped[alias] = canon
        _save_json(book_id, "alias_map.json", remapped)

    # 3) Gender map key
    genders = _load_json(book_id, "character_genders.json") or {}
    if old_name in genders:
        genders[new_name] = genders.pop(old_name)
        _save_json(book_id, "character_genders.json", genders)

    # 4) Rename sheet / portrait / history image files
    chars_dir = GENERATED_DIR / book_id / "characters"
    old_safe, new_safe = _safe_filename(old_name), _safe_filename(new_name)
    if chars_dir.exists() and old_safe != new_safe:
        targets = list(chars_dir.glob(f"{old_safe}_*"))
        hist = chars_dir / "history"
        if hist.exists():
            targets += list(hist.glob(f"{old_safe}_*"))
        for f in targets:
            try:
                f.rename(f.with_name(f.name.replace(old_safe, new_safe, 1)))
            except OSError as e:
                logger.warning("Sheet rename failed for %s: %s", f.name, e)

    # 5) Chapter consistency summaries embed the old name in their per-page /
    # per-character scores — drop every cached one so they recompute.
    chapters_root = GENERATED_DIR / book_id / "chapters"
    if chapters_root.exists():
        for ch_dir in chapters_root.glob("ch*"):
            try:
                (ch_dir / "consistency.json").unlink(missing_ok=True)
            except OSError:
                pass


@router.put("/api/book/{book_id}/preprocess/characters/{char_name}")
async def update_character(book_id: str, char_name: str, update: CharacterUpdate) -> dict[str, Any]:
    """Update a character's profile (cascades a rename across the whole book)."""
    update_dict = update.model_dump(exclude_none=True)
    new_name = (update_dict.get("canonical_name") or char_name).strip() or char_name
    renamed = new_name != char_name

    llm_chars = _load_json(book_id, "llm_characters.json")
    target = None
    if llm_chars:
        target = next((c for c in llm_chars.get("characters", []) if c.get("canonical_name") == char_name), None)

    # Reject a rename that collides with another existing character, rather than
    # silently merging the two (which would corrupt segment references).
    if renamed:
        # A rename races every flow that resolves this character by name: a
        # sheet regen has the current sheet parked in history under the OLD
        # name (the cascade renames it away → the regen's restore no-ops →
        # the character ends up with NO sheet at all), and a chapter run
        # probes sheets by the old name mid-generation.
        if (book_id, "character", char_name) in _active_regens:
            raise HTTPException(status_code=409,
                                detail="This character's sheet is regenerating — rename after it finishes.")
        if book_generation_active(book_id):
            raise HTTPException(status_code=409,
                                detail="A chapter is generating for this book — rename after it finishes.")
        existing = {c.get("canonical_name") for c in (llm_chars or {}).get("characters", [])}
        if not existing:
            try:
                from src.core.db import get_characters as _db_chars
                existing = {c.get("canonical_name") for c in _db_chars(book_id)}
            except Exception:
                existing = set()
        if new_name in existing:
            raise HTTPException(status_code=409, detail=f"A character named '{new_name}' already exists.")

    if target is None:
        # llm_characters.json is missing or blanked (e.g. after a failed re-preprocess).
        # The canonical `characters` collection still has the character — update it there.
        try:
            from src.core.db import update_character as db_update_char, is_available
            if is_available() and db_update_char(book_id, char_name, update_dict):
                if renamed:
                    _cascade_character_rename(book_id, char_name, new_name)
                return {"status": "updated", "character": new_name,
                        "updated_fields": list(update_dict.keys())}
        except Exception as e:
            logger.warning("MongoDB character update failed for %s: %s", char_name, e)
        raise HTTPException(status_code=404, detail=f"Character '{char_name}' not found.")

    for key, value in update_dict.items():
        target[key] = value

    _save_json(book_id, "llm_characters.json", llm_chars)

    # Update gender map if gender changed
    if "gender" in update_dict:
        genders = _load_json(book_id, "character_genders.json") or {}
        genders[char_name] = update_dict["gender"]
        _save_json(book_id, "character_genders.json", genders)

    # Update alias map if aliases changed
    if "aliases" in update_dict:
        alias_map = _load_json(book_id, "alias_map.json") or {}
        # Remove old aliases for this character
        alias_map = {k: v for k, v in alias_map.items() if v != char_name}
        # Add new aliases
        for alias in update_dict["aliases"]:
            if alias != char_name:
                alias_map[alias] = char_name
        _save_json(book_id, "alias_map.json", alias_map)

    # Sync to the `characters` collection — the consistency hub that
    # load_characters (and thus all generation) reads FIRST. If Mongo is
    # reachable but THIS write fails, the hub keeps the old data while the file
    # + analysis have the new: a silent divergence that makes generation use a
    # stale character. Surface it instead of reporting an unqualified success.
    # (A fully-down Mongo is fine — load_characters falls back to the file.)
    hub_reachable = False
    hub_ok = False
    try:
        from src.core.db import update_character as db_update_char, is_available
        hub_reachable = is_available()
        if hub_reachable:
            hub_ok = db_update_char(book_id, char_name, update_dict)
    except Exception as e:
        logger.warning("Characters-hub write failed for %s: %s", char_name, e)

    if renamed:
        _cascade_character_rename(book_id, char_name, new_name)

    result = {"status": "updated", "character": new_name, "updated_fields": list(update_dict.keys())}
    if hub_reachable and not hub_ok:
        result["degraded"] = True
        result["warning"] = (
            "Saved locally, but the MongoDB consistency hub did not confirm the write — "
            "generation may use stale character data until you retry."
        )
    return result


@router.post("/api/book/{book_id}/preprocess/characters/{char_name}/autofill")
async def autofill_character_details(
    book_id: str, char_name: str,
    _user_key: str | None = Depends(_require_user_key),  # belt to the middleware's suffix match
) -> dict[str, Any]:
    """Use LLM to generate visual details for a character based on description and book context."""
    llm_chars = _load_json(book_id, "llm_characters.json")
    if not llm_chars:
        raise HTTPException(status_code=404, detail="No character data.")

    target = next((c for c in llm_chars.get("characters", []) if c.get("canonical_name") == char_name), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Character '{char_name}' not found.")

    meta = _load_json(book_id, "meta.json") or {}
    book_title = meta.get("title", "")

    from src.llm_client import generate_json
    result = await run_in_threadpool(generate_json,
        f"""Given this character from the book "{book_title}", generate detailed visual appearance for a children's picture book illustration.

Character: {char_name}
Gender: {target.get('gender', 'unknown')}
Role: {target.get('role', 'unknown')}
Description: {target.get('description', '')}
Existing appearance: {target.get('appearance', '')}

Generate a complete visual profile. If the book doesn't describe something, invent appropriate details that fit the character's role, era, and personality.

Return JSON:
{{
  "appearance": "full physical description paragraph",
  "visual_details": {{
    "age": "specific age or age range",
    "ethnicity": "ethnicity fitting the story setting",
    "skin_tone": "specific skin description",
    "hair": "hair color, style, length",
    "eyes": "eye color and shape",
    "build": "body type",
    "clothing": "period-accurate outfit description",
    "accessories": "any accessories",
    "distinctive": "most recognizable feature"
  }}
}}"""
    )

    # Write back via the project's LLM-write protocol (same as
    # _merge_segment_fields): re-read FRESH under the book lock and merge only
    # this character's fields — the pre-call `llm_chars` snapshot is seconds
    # old, and writing it whole reverted every edit made during the LLM call.
    update_dict: dict[str, Any] = {}
    if result.get("appearance"):
        update_dict["appearance"] = result["appearance"]
    if result.get("visual_details"):
        update_dict["visual_details"] = result["visual_details"]

    if update_dict:
        async with _analysis_lock(book_id):
            fresh = _load_json(book_id, "llm_characters.json") or {}
            ftarget = next(
                (c for c in fresh.get("characters", []) if c.get("canonical_name") == char_name),
                None,
            )
            if ftarget is not None:
                ftarget.update(update_dict)
                _save_json(book_id, "llm_characters.json", fresh)
                target = ftarget
        # Sync the canonical `characters` collection too (best-effort, same as
        # update_character) — every reader prefers it, so skipping this made
        # autofill results vanish on refresh and sheet regens use the OLD look.
        try:
            from src.core.db import update_character as db_update_char
            db_update_char(book_id, char_name, update_dict)
        except Exception as e:
            logger.debug("MongoDB sync skipped for autofill %s: %s", char_name, e)

    return {
        "appearance": target.get("appearance", ""),
        "visual_details": target.get("visual_details", {}),
    }


def load_special_records(book_id: str) -> dict[str, dict]:
    """Special-page records, reconciled against current analysis.

    The derived fields (characters_in_scene, scene_background, summaries…) are
    ALWAYS recomputed from the live analysis, so a character/scene rename — or
    any segment edit — propagates to the covers with no per-rename cascade. Only
    the fields the user explicitly edited (tracked in each record's `_edited`)
    are overlaid on top. This is the single reconciliation point shared by the
    editor and the regen endpoints; special_pages.json is now an edit overlay,
    not an authoritative snapshot (legacy full-record files have no `_edited`,
    so they refresh cleanly).
    """
    from src.generation.special_page_data import derive_special_pages

    analysis = _load_json(book_id, "analysis.json") or {}
    meta = _load_json(book_id, "meta.json") or {}
    ch_map = _load_json(book_id, "chapter_segments.json") or {}
    locs = (_load_json(book_id, "llm_locations.json") or {}).get("locations", [])
    records = derive_special_pages(
        meta.get("title", book_id), analysis.get("segments", []), ch_map, locs,
    )

    stored = _load_json(book_id, "special_pages.json")
    if isinstance(stored, dict) and isinstance(stored.get("pages"), dict):
        for key, rec in stored["pages"].items():
            if key not in records or not isinstance(rec, dict):
                continue
            for field in rec.get("_edited") or []:
                if field in rec:
                    records[key][field] = rec[field]
    return records


def _special_image_url(book_id: str, base: str) -> str | None:
    special_dir = GENERATED_DIR / book_id / "special"
    for ext in (".png", ".jpg"):
        p = special_dir / f"{base}{ext}"
        if p.exists():
            return f"/static/{book_id}/special/{p.name}"
    return None


@router.get("/api/book/{book_id}/special-pages")
async def get_special_pages(book_id: str) -> dict[str, Any]:
    """List all special pages with their editable records + image urls."""
    from src.generation.special_page_data import special_file_base, special_key

    records = load_special_records(book_id)
    ch_segments = _load_json(book_id, "chapter_segments.json") or {}

    def _entry(page_type: str, chapter: int | None, label: str) -> dict:
        key = special_key(page_type, chapter)
        rec = records.get(key, {})
        base = special_file_base(page_type, chapter) or ""
        out = {
            "type": page_type, "label": label, "key": key,
            "url": _special_image_url(book_id, base),
            "title_text": rec.get("title_text", ""),
            "subtitle_text": rec.get("subtitle_text", ""),
            "scene_background": rec.get("scene_background", ""),
            "scene_summary": rec.get("scene_summary", ""),
            "characters_in_scene": rec.get("characters_in_scene", []),
        }
        if chapter is not None:
            ch_info = ch_segments.get(str(chapter), {})
            out["chapter"] = chapter
            out["chapter_title"] = ch_info.get("chapter_title", "")
            out["chapter_summary"] = ch_info.get("chapter_summary", "")
        return out

    pages = [_entry("book_cover", None, "Book Cover")]
    for ch_key in sorted(ch_segments.keys(), key=lambda x: int(x)):
        ch_num = int(ch_key)
        pages.append(_entry("chapter_cover", ch_num, f"Ch {ch_num + 1} Cover"))
    pages.append(_entry("back_cover", None, "Back Cover"))
    return {"pages": pages}


class SpecialPageUpdate(BaseModel):
    title_text: Optional[str] = None
    subtitle_text: Optional[str] = None
    scene_background: Optional[str] = None
    scene_summary: Optional[str] = None
    characters_in_scene: Optional[list[str]] = None


@router.put("/api/book/{book_id}/special/{page_type}")
async def update_special_page(
    book_id: str, page_type: str, update: SpecialPageUpdate, chapter: int = 0,
) -> dict[str, Any]:
    """Edit a special page's record. Persists ONLY the edited fields as an
    overlay (with an `_edited` marker); the derived base is recomputed on read,
    so a later rename keeps refreshing the un-edited fields."""
    from src.generation.special_page_data import SPECIAL_TYPES, special_key

    if page_type not in SPECIAL_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown special page type '{page_type}'.")
    key = special_key(page_type, chapter)
    update_dict = update.model_dump(exclude_none=True)

    async with _analysis_lock(book_id):
        records = load_special_records(book_id)
        rec = records.get(key)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"Special page '{key}' not found.")
        # Save just the user's edits, not the derived base.
        stored = _load_json(book_id, "special_pages.json")
        pages = stored.get("pages") if isinstance(stored, dict) and isinstance(stored.get("pages"), dict) else {}
        overlay = pages.get(key) if isinstance(pages.get(key), dict) else {}
        edited = set(overlay.get("_edited") or [])
        for field, value in update_dict.items():
            overlay[field] = value
            edited.add(field)
        overlay["_edited"] = sorted(edited)
        pages[key] = overlay
        _save_json(book_id, "special_pages.json", {"pages": pages})
        rec.update(update_dict)
    return {"status": "updated", "key": key, "page": rec}


@router.get("/api/book/{book_id}/special/{page_type}/history")
async def get_special_page_history(book_id: str, page_type: str, chapter: int = 0) -> dict[str, Any]:
    """Version list for a special page — same shape the segment carousel uses."""
    from src.generation.special_page_data import special_file_base

    base = special_file_base(page_type, chapter)
    if not base:
        raise HTTPException(status_code=400, detail=f"Unknown special page type '{page_type}'.")
    special_dir = GENERATED_DIR / book_id / "special"
    images: list[dict] = []
    for ext in (".png", ".jpg"):
        cur = special_dir / f"{base}{ext}"
        if cur.exists():
            entry: dict[str, Any] = {
                "url": f"/static/{book_id}/special/{cur.name}",
                "version": "current",
                "timestamp": cur.stat().st_mtime,
            }
            qf = special_dir / "quality" / f"{base}_quality.json"
            if qf.exists():
                try:
                    entry["quality"] = json.loads(qf.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
            images.append(entry)
            break
    history_dir = special_dir / "history"
    if history_dir.exists():
        for f in sorted(history_dir.glob(f"{base}_*.*"), reverse=True):
            if f.suffix == ".json":
                continue
            version = f.stem.split("_")[-1]
            if not version.isdigit():
                continue  # non-timestamp backups aren't restorable versions
            images.append({
                "url": f"/static/{book_id}/special/history/{f.name}",
                "version": version,
                "timestamp": f.stat().st_mtime,
            })
    return {"images": images}


@router.post("/api/book/{book_id}/special/{page_type}/restore-version")
async def restore_special_page_version(
    book_id: str, page_type: str, version: str, chapter: int = 0,
) -> dict[str, Any]:
    """Make a historical special-page image current (segment restore's pattern:
    copy-first so a failed copy never leaves the page imageless)."""
    import shutil
    import time as _time

    from src.generation.special_page_data import special_file_base

    if not version.isdigit():
        raise HTTPException(status_code=400, detail="Invalid version.")
    base = special_file_base(page_type, chapter)
    if not base:
        raise HTTPException(status_code=400, detail=f"Unknown special page type '{page_type}'.")
    if (book_id, "special", f"{page_type}:{chapter}") in _active_regens:
        raise HTTPException(status_code=409, detail="This page is regenerating — try again when it finishes.")

    special_dir = GENERATED_DIR / book_id / "special"
    history_dir = special_dir / "history"
    restored = None
    for ext in (".png", ".jpg"):
        candidate = history_dir / f"{base}_{version}{ext}"
        if candidate.exists():
            restored = candidate
            break
    if restored is None:
        raise HTTPException(status_code=404, detail=f"Version {version} not found.")

    special_dir.mkdir(parents=True, exist_ok=True)
    tmp_restore = special_dir / f".restore_tmp_{base}{restored.suffix}"
    shutil.copy2(restored, tmp_restore)
    quality_file = special_dir / "quality" / f"{base}_quality.json"
    try:
        ts = int(_time.time())
        history_dir.mkdir(parents=True, exist_ok=True)
        while any((history_dir / f"{base}_{ts}{sfx}").exists()
                  for sfx in (".png", ".jpg", "_quality.json")):
            ts += 1
        for ext in (".png", ".jpg"):
            current = special_dir / f"{base}{ext}"
            if current.exists():
                current.rename(history_dir / f"{base}_{ts}{ext}")
        # Archive the current quality verdict too, or it would mislabel the
        # restored image (segment restore does the same).
        if quality_file.exists():
            quality_file.rename(history_dir / f"{base}_{ts}_quality.json")
        new_current = special_dir / f"{base}{restored.suffix}"
        tmp_restore.rename(new_current)
    finally:
        tmp_restore.unlink(missing_ok=True)

    # Restore the restored version's own verdict if it was archived.
    hist_quality = history_dir / f"{base}_{version}_quality.json"
    if hist_quality.exists():
        quality_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(hist_quality, quality_file)

    return {
        "status": "restored",
        "url": f"/static/{book_id}/special/{new_current.name}",
    }


@router.get("/api/book/{book_id}/preprocess/locations")
async def get_locations(book_id: str) -> dict[str, Any]:
    """Get location list with scene reference images."""
    llm_locs = _load_json(book_id, "llm_locations.json")
    locations = llm_locs.get("locations", []) if llm_locs else []

    # Find scene reference images
    scenes_dir = GENERATED_DIR / book_id / "scenes"
    scene_sheets = {}
    if scenes_dir.exists():
        import re as _re
        for loc in locations:
            name = loc.get("name", "")
            safe = _re.sub(r'[^\w\s\u4e00-\u9fff-]', '', name)
            safe = _re.sub(r'\s+', '_', safe.strip()).lower()[:50]
            for ext in (".png", ".jpg"):
                scene_file = scenes_dir / f"{safe}_scene{ext}"
                if scene_file.exists():
                    scene_sheets[name] = f"/static/{book_id}/scenes/{scene_file.name}"
                    break

    return {"locations": locations, "scene_sheets": scene_sheets}


class SceneUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    visual_details: Optional[dict[str, Any]] = None


@router.put("/api/book/{book_id}/preprocess/scenes/{scene_name}")
async def update_scene(book_id: str, scene_name: str, update: SceneUpdate) -> dict[str, Any]:
    """Update a location's profile in llm_locations.json."""
    update_dict = update.model_dump(exclude_none=True)

    llm_locs = _load_json(book_id, "llm_locations.json")
    target = None
    if llm_locs:
        target = next((loc for loc in llm_locs.get("locations", []) if loc.get("name") == scene_name), None)

    if target is None:
        raise HTTPException(status_code=404, detail=f"Scene '{scene_name}' not found.")

    new_name = (update_dict.get("name") or scene_name).strip() or scene_name

    # Reject a rename that collides with another existing location.
    if new_name != scene_name:
        # Same race as character rename: a scene regen holds the current sheet
        # in history under the old name; renaming mid-flight strands it.
        if (book_id, "scene", scene_name) in _active_regens:
            raise HTTPException(status_code=409,
                                detail="This scene's sheet is regenerating — rename after it finishes.")
        if book_generation_active(book_id):
            raise HTTPException(status_code=409,
                                detail="A chapter is generating for this book — rename after it finishes.")
        others = {loc.get("name") for loc in (llm_locs or {}).get("locations", []) if loc is not target}
        if new_name in others:
            raise HTTPException(status_code=409, detail=f"A location named '{new_name}' already exists.")

    for key, value in update_dict.items():
        target[key] = value

    _save_json(book_id, "llm_locations.json", llm_locs)

    # Rename the scene reference sheet files so they keep matching the new name
    # (otherwise sceneSheets[new_name] finds nothing and the image "disappears").
    if new_name != scene_name:
        scenes_dir = GENERATED_DIR / book_id / "scenes"
        old_safe, new_safe = _safe_filename(scene_name), _safe_filename(new_name)
        if scenes_dir.exists() and old_safe != new_safe:
            targets = list(scenes_dir.glob(f"{old_safe}_scene*"))
            hist = scenes_dir / "history"
            if hist.exists():
                targets += list(hist.glob(f"{old_safe}_scene_*"))
            for f in targets:
                try:
                    f.rename(f.with_name(f.name.replace(old_safe, new_safe, 1)))
                except OSError as e:
                    logger.warning("Scene sheet rename failed for %s: %s", f.name, e)

        # Locations are matched to pages by the old name appearing in the segment
        # prose, so rewrite old->new in scene_background / scene_summary too —
        # otherwise a renamed location stops matching its pages.
        import re as _re2
        analysis = _load_json(book_id, "analysis.json")
        if analysis:
            pat = _re2.compile(r"\b" + _re2.escape(scene_name) + r"\b", _re2.IGNORECASE)
            changed: list = []
            for seg in analysis.get("segments", []):
                touched = False
                for fld in ("scene_background", "scene_summary"):
                    val = seg.get(fld)
                    if isinstance(val, str) and pat.search(val):
                        seg[fld] = pat.sub(new_name, val)
                        touched = True
                if touched:
                    changed.append(seg.get("id"))
            if changed:
                _save_json(book_id, "analysis.json", analysis)

    return {"status": "updated", "scene": new_name, "updated_fields": list(update_dict.keys())}


@router.get("/api/book/{book_id}/preprocess/scenes/{scene_name}/history")
async def get_scene_sheet_history(book_id: str, scene_name: str) -> dict[str, Any]:
    """Get current + historical scene sheet images."""
    import re as _re

    scenes_dir = GENERATED_DIR / book_id / "scenes"
    safe = _re.sub(r'[^\w\s\u4e00-\u9fff-]', '', scene_name)
    safe = _re.sub(r'\s+', '_', safe.strip()).lower()[:50]
    images = []

    # Current sheet
    for ext in (".png", ".jpg"):
        current = scenes_dir / f"{safe}_scene{ext}"
        if current.exists():
            images.append({
                "url": f"/static/{book_id}/scenes/{current.name}",
                "version": "current",
                "timestamp": current.stat().st_mtime,
            })
            break

    # History
    history_dir = scenes_dir / "history"
    if history_dir.exists():
        for f in sorted(history_dir.glob(f"{safe}_scene_*.*"), reverse=True):
            version = f.stem.split("_")[-1]
            if not version.isdigit():
                # e.g. *_selfcorrect_prev.png backups — not restorable versions,
                # and float(version) would 500 the whole endpoint.
                continue
            images.append({
                "url": f"/static/{book_id}/scenes/history/{f.name}",
                "version": version,
                "timestamp": float(version),
            })

    return {"images": images}


@router.get("/api/book/{book_id}/preprocess/characters/{char_name}/history")
async def get_character_sheet_history(book_id: str, char_name: str) -> dict[str, Any]:
    """Get current + historical character sheet images."""

    chars_dir = GENERATED_DIR / book_id / "characters"
    safe = _safe_filename(char_name)
    images = []

    # Current sheet
    for ext in (".png", ".jpg"):
        current = chars_dir / f"{safe}_sheet{ext}"
        if current.exists():
            images.append({
                "url": f"/static/{book_id}/characters/{current.name}",
                "version": "current",
                "timestamp": current.stat().st_mtime,
            })
            break

    # History
    history_dir = chars_dir / "history"
    if history_dir.exists():
        for f in sorted(history_dir.glob(f"{safe}_sheet_*.*"), reverse=True):
            version = f.stem.split("_")[-1]
            if not version.isdigit():
                # The sheet self-correction writes *_selfcorrect_prev backups
                # into this directory; float("prev") permanently 500'd this
                # endpoint for any character that ever self-corrected.
                continue
            images.append({
                "url": f"/static/{book_id}/characters/history/{f.name}",
                "version": version,
                "timestamp": float(version),
            })

    return {"images": images}


@router.get("/api/book/{book_id}/preprocess/chapter/{ch_idx}/segments")
async def get_chapter_segments(book_id: str, ch_idx: int) -> dict[str, Any]:
    """Get all segments for a chapter with full data."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data found.")

    segments = analysis.get("segments", [])
    ch_segments = [s for s in segments if s.get("chapter_idx") == ch_idx]

    # Add illustration paths if they exist. Page numbers MUST come from the
    # shared helper — the previous `id - min(ids) + 1` formula diverged from
    # the regen/quality endpoints as soon as chapter ids had a gap.
    ch_dir = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}"
    for seg in ch_segments:
        page_num = segment_page_num(segments, ch_idx, seg.get("id", 0))
        for ext in (".png", ".jpg"):
            img_path = ch_dir / "pages" / f"page_{page_num:03d}{ext}"
            if img_path.exists():
                seg["illustration_url"] = f"/static/{book_id}/chapters/ch{ch_idx:02d}/pages/{img_path.name}"
                break

    # Chapter info
    chapter_segments = _load_json(book_id, "chapter_segments.json") or {}
    ch_info = chapter_segments.get(str(ch_idx), {})

    return {
        "chapter_idx": ch_idx,
        "chapter_title": ch_info.get("chapter_title", f"Chapter {ch_idx + 1}"),
        "segments": ch_segments,
    }


class SegmentUpdate(BaseModel):
    text: Optional[str] = None
    simplified_text: Optional[str] = None
    characters_in_scene: Optional[list[str]] = None
    character_actions: Optional[list[dict[str, str]]] = None
    scene_background: Optional[str] = None
    scene_summary: Optional[str] = None
    sentiment: Optional[str] = None


@router.put("/api/book/{book_id}/segment/{seg_id}")
async def update_segment(book_id: str, seg_id: int, update: SegmentUpdate) -> dict[str, Any]:
    """Update a single segment's fields."""
    update_dict = update.model_dump(exclude_none=True)
    # Serialize the read-modify-write so two concurrent edits to the same book
    # can't lose each other's updates.
    async with _analysis_lock(book_id):
        analysis = _load_json(book_id, "analysis.json")
        if not analysis:
            raise HTTPException(status_code=404, detail="No analysis data found.")
        target = next((s for s in analysis.get("segments", []) if s.get("id") == seg_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")
        for key, value in update_dict.items():
            target[key] = value
        _save_json(book_id, "analysis.json", analysis)

    # The page text changed — the cached text-image-match verdict is stale now,
    # so drop it (best-effort) rather than keep reporting the old result.
    if "simplified_text" in update_dict or "text" in update_dict:
        _invalidate_page_quality(book_id, analysis.get("segments", []), seg_id)

    # And keep chapter_data.json (the PDF's text source) in step — edited text
    # used to stay stranded in analysis.json and never reach the next book.pdf.
    if "simplified_text" in update_dict:
        segments = analysis.get("segments", [])
        ch_idx = target.get("chapter_idx", 0)
        update_chapter_data_page(
            book_id, ch_idx, segment_page_num(segments, ch_idx, seg_id),
            text=update_dict["simplified_text"],
        )
        invalidate_chapter_consistency(book_id, ch_idx)

    return {"status": "updated", "segment_id": seg_id, "updated_fields": list(update_dict.keys())}


@router.get("/api/book/{book_id}/segment/{seg_id}/history")
async def get_segment_illustration_history(book_id: str, seg_id: int) -> dict[str, Any]:
    """Get all historical illustrations for a segment."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        return {"images": []}

    segments = analysis.get("segments", [])
    target = next((s for s in segments if s.get("id") == seg_id), None)
    if not target:
        return {"images": []}

    ch_idx = target.get("chapter_idx", 0)
    page_num = segment_page_num(segments, ch_idx, seg_id)

    # Find all versions in pages dir + history dir
    images = []
    ch_dir = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}"
    pages_dir = ch_dir / "pages"
    history_dir = ch_dir / "history"

    # Current image + quality
    if pages_dir.exists():
        for ext in (".png", ".jpg"):
            current = pages_dir / f"page_{page_num:03d}{ext}"
            if current.exists():
                entry: dict[str, Any] = {
                    "url": f"/static/{book_id}/chapters/ch{ch_idx:02d}/pages/{current.name}",
                    "version": "current",
                    "timestamp": current.stat().st_mtime,
                }
                # Attach quality if exists
                qf = ch_dir / "quality" / f"page_{page_num:03d}_quality.json"
                if qf.exists():
                    entry["quality"] = json.loads(qf.read_text(encoding="utf-8"))
                images.append(entry)
                break

    # Historical images + quality
    if history_dir.exists():
        for f in sorted(history_dir.glob(f"page_{page_num:03d}_*.*"), reverse=True):
            if f.suffix == ".json":
                continue  # skip quality files, they're attached below
            version_ts = f.stem.split("_")[-1]
            entry = {
                "url": f"/static/{book_id}/chapters/ch{ch_idx:02d}/history/{f.name}",
                "version": version_ts,
                "timestamp": f.stat().st_mtime,
            }
            # Attach quality for this version
            qf = history_dir / f"page_{page_num:03d}_{version_ts}_quality.json"
            if qf.exists():
                entry["quality"] = json.loads(qf.read_text(encoding="utf-8"))
            images.append(entry)

    return {"images": images}


@router.post("/api/book/{book_id}/segment/{seg_id}/restore-version")
async def restore_segment_version(book_id: str, seg_id: int, version: str) -> dict[str, Any]:
    """Make a historical illustration the current one (the editor's version
    carousel calls this — without it, picking an old version only changed
    local state and the PDF/viewer kept using the newest image)."""
    import shutil
    import time as _time

    if not version.isdigit():
        raise HTTPException(status_code=400, detail="Invalid version.")

    if (book_id, "segment", seg_id) in _active_regens:
        # A regen is mid-flight for this page; interleaving the two file
        # shuffles leaves both a .png and a .jpg current image behind.
        raise HTTPException(status_code=409, detail="This page is regenerating — try again when it finishes.")
    if book_generation_active(book_id):
        # The chapter subprocess writes this same page file and rebuilds
        # chapter_data.json at the end, which would override the restore.
        raise HTTPException(status_code=409,
                            detail="A chapter is generating for this book — restore after it finishes.")

    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data found.")
    segments = analysis.get("segments", [])
    target = next((s for s in segments if s.get("id") == seg_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")

    ch_idx = target.get("chapter_idx", 0)
    page_num = segment_page_num(segments, ch_idx, seg_id)
    ch_base = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}"
    pages_dir = ch_base / "pages"
    history_dir = ch_base / "history"

    restored = None
    for ext in (".png", ".jpg"):
        candidate = history_dir / f"page_{page_num:03d}_{version}{ext}"
        if candidate.exists():
            restored = candidate
            break
    if restored is None:
        raise HTTPException(status_code=404, detail=f"Version {version} not found.")

    # Copy the restored version to a temp name FIRST: if the copy fails (disk
    # full), the current image is still in place and nothing is lost. The old
    # order renamed the current image away before copying — a failed copy left
    # the page with no image at all.
    pages_dir.mkdir(parents=True, exist_ok=True)
    tmp_restore = pages_dir / f".restore_tmp_{page_num:03d}{restored.suffix}"
    shutil.copy2(restored, tmp_restore)

    try:
        # Archive the current image (+ its quality verdict) into history, same
        # naming scheme as the regen endpoints, so nothing is lost by restoring.
        # Bump ts past any taken slot — restoring a version archived this same
        # second would otherwise overwrite the very history file just copied.
        ts = int(_time.time())
        history_dir.mkdir(parents=True, exist_ok=True)
        while any(
            (history_dir / f"page_{page_num:03d}_{ts}{suffix}").exists()
            for suffix in (".png", ".jpg", "_quality.json")
        ):
            ts += 1
        for ext in (".png", ".jpg"):
            current = pages_dir / f"page_{page_num:03d}{ext}"
            if current.exists():
                current.rename(history_dir / f"page_{page_num:03d}_{ts}{ext}")
        quality_file = ch_base / "quality" / f"page_{page_num:03d}_quality.json"
        if quality_file.exists():
            quality_file.rename(history_dir / f"page_{page_num:03d}_{ts}_quality.json")

        new_current = pages_dir / f"page_{page_num:03d}{restored.suffix}"
        tmp_restore.rename(new_current)
    finally:
        tmp_restore.unlink(missing_ok=True)

    # The restored image may have a different extension than the entry in
    # chapter_data.json (the PDF's source) — keep it pointing at the new file.
    update_chapter_data_page(book_id, ch_idx, page_num, image_path=str(new_current))
    hist_quality = history_dir / f"page_{page_num:03d}_{version}_quality.json"
    if hist_quality.exists():
        quality_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(hist_quality, quality_file)
    # The page image changed — the chapter summary cache describes the old one.
    invalidate_chapter_consistency(book_id, ch_idx)

    return {
        "status": "restored",
        "segment_id": seg_id,
        "illustration_url": f"/static/{book_id}/chapters/ch{ch_idx:02d}/pages/{new_current.name}",
    }


@router.post("/api/book/{book_id}/segment/{seg_id}/simplify")
async def simplify_segment_text(
    book_id: str, seg_id: int,
    user_key: str = Depends(_require_user_key),  # BYOK 403 gate; key routed by BYOKMiddleware
) -> dict[str, Any]:
    """Generate simplified text for a single segment."""
    target = _load_segment_or_404(book_id, seg_id)

    from src.generation.text_simplifier import simplify_text
    scene = {
        "page_number": 1,
        "original_text": target.get("text", ""),
        "key_characters": target.get("characters_in_scene", []),
        "scene_summary": target.get("scene_summary", ""),
    }
    result = await run_in_threadpool(simplify_text, [scene])
    simplified = result[0].get("page_text", "") if result else ""
    scene_direction = result[0].get("scene_direction", "") if result else ""

    await _merge_segment_fields(book_id, seg_id, {
        "simplified_text": simplified,
        "scene_direction": scene_direction,
    })

    return {"simplified_text": simplified, "scene_direction": scene_direction}


@router.post("/api/book/{book_id}/segment/{seg_id}/background")
async def generate_segment_background(
    book_id: str, seg_id: int,
    user_key: str = Depends(_require_user_key),  # BYOK 403 gate; key routed by BYOKMiddleware
) -> dict[str, Any]:
    """Generate scene background description for a single segment."""
    target = _load_segment_or_404(book_id, seg_id)

    from src.llm_client import generate_json

    chars_in_scene = target.get("characters_in_scene", [])
    char_actions = target.get("character_actions", [])
    char_context = ""
    if char_actions:
        char_context = "\n".join(f"- {ca.get('name','')}: {ca.get('action','')}" for ca in char_actions)
    elif chars_in_scene:
        char_context = ", ".join(chars_in_scene)

    result = await run_in_threadpool(generate_json,
        f"""Describe the physical setting/environment of this scene from a novel.
Be specific and visual: location, time of day, weather, objects, atmosphere, colors.
Include details relevant to the characters and their actions in this scene.

Scene text:
{target.get('text', '')[:1000]}

Characters in this scene:
{char_context or 'None specified'}

Return JSON: {{"scene_background": "detailed visual description..."}}"""
    )
    background = result.get("scene_background", "")

    await _merge_segment_fields(book_id, seg_id, {"scene_background": background})

    return {"scene_background": background}


@router.post("/api/book/{book_id}/segment/{seg_id}/summarize")
async def summarize_segment(
    book_id: str, seg_id: int,
    user_key: str = Depends(_require_user_key),  # BYOK 403 gate; key routed by BYOKMiddleware
) -> dict[str, Any]:
    """Generate summary and sentiment for a single segment."""
    target = _load_segment_or_404(book_id, seg_id)

    from src.llm_client import generate_json
    result = await run_in_threadpool(generate_json,
        f"""Summarize this scene in one sentence. Also determine the sentiment.

Scene text:
{target.get('text', '')[:1000]}

Return JSON: {{"scene_summary": "one sentence summary", "sentiment": "positive/negative/neutral/tense/emotional"}}"""
    )
    summary = result.get("scene_summary", "")
    sentiment = result.get("sentiment", "neutral")

    await _merge_segment_fields(book_id, seg_id, {
        "scene_summary": summary,
        "sentiment": sentiment,
    })

    return {"scene_summary": summary, "sentiment": sentiment}

