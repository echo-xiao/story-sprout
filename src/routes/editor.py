"""Editor/segment endpoints."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

import asyncio

from src.config import GENERATED_DIR
from src.core import storage
from src.core.provenance import TEXT_SOURCE_USER, TEXT_SOURCE_WRITER
from src.generation.character_sheet import _safe_filename
from src.routes.helpers import (
    _active_regens, _load_json, _require_user_key, _save_json, book_generation_active,
    invalidate_chapter_consistency, segment_page_num, update_chapter_data_page,
    versioned_static_url, write_local_preprocess,
)
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

router = APIRouter()


def _load_quality(rel_key: str) -> dict | None:
    """GCS-first quality-JSON loader with local-file fallback.

    `rel_key` is the GENERATED_DIR-relative path to the quality file
    (e.g. "<book_id>/chapters/ch00/quality/page_001_quality.json").
    Returns the parsed dict or None if neither source has it.
    """
    from src.core import store
    try:
        data = store.get_json(rel_key)
        if data is not None:
            return data
    except Exception as e:
        logger.warning("QA GCS read failed for %s: %s", rel_key, e)
    p = GENERATED_DIR / rel_key
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except (OSError, ValueError):
        return None

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


def _canonical_current(book_id: str, asset_type: str, asset_key: str):
    """(local_dir, filename_base, static_key_base) for an asset's live 'current'
    image — where regen writes it and the display panels / page-gen / PDF read it.
    The caller appends the extension. (None, None, None) if the key is unknown."""
    import re as _re
    from src.generation.character_sheet import _safe_filename
    base = GENERATED_DIR / book_id
    if asset_type == "scene":
        safe = _re.sub(r'[^\w\s一-鿿-]', '', asset_key)
        safe = _re.sub(r'\s+', '_', safe.strip()).lower()[:50]
        return base / "scenes", f"{safe}_scene", f"{book_id}/scenes/{safe}_scene"
    if asset_type == "character":
        safe = _safe_filename(asset_key)
        return base / "characters", f"{safe}_sheet", f"{book_id}/characters/{safe}_sheet"
    if asset_type == "page":
        m = _re.match(r"ch(\d+):p(\d+)", asset_key)
        if not m:
            return None, None, None
        ci, pn = int(m.group(1)), int(m.group(2))
        return (base / "chapters" / f"ch{ci:02d}" / "pages", f"page_{pn:03d}",
                f"{book_id}/chapters/ch{ci:02d}/pages/page_{pn:03d}")
    if asset_type == "special":
        from src.generation.special_page_data import special_file_base
        pt, _, ch = asset_key.partition(":")
        b = special_file_base(pt, int(ch or 0))
        if not b:
            return None, None, None
        return base / "special", b, f"{book_id}/special/{b}"
    return None, None, None


def _promote_selected(book_id: str, asset_type: str, asset_key: str) -> None:
    """Copy the selected version's bytes onto the asset's live 'current' image
    (local + GCS) so every existing reader uses the picked version with no
    read-path changes. The keystone of 'pick a version -> it's the one used'."""
    from src.core.store import get_selected_version
    from src.core import storage
    sel = get_selected_version(book_id, asset_type, asset_key)
    if not sel or not sel.get("storage_key"):
        return
    data = storage.get_image(sel["storage_key"])
    if not data:
        return
    cdir, fbase, static_base = _canonical_current(book_id, asset_type, asset_key)
    if cdir is None:
        return
    ext = ".png" if str(sel["storage_key"]).endswith(".png") else ".jpg"
    ctype = "image/png" if ext == ".png" else "image/jpeg"
    cdir.mkdir(parents=True, exist_ok=True)
    # Drop a stale other-extension current file so the glob can't serve it.
    for _e in (".png", ".jpg"):
        _p = cdir / f"{fbase}{_e}"
        if _e != ext and _p.exists():
            try:
                _p.unlink()
            except OSError:
                pass
    (cdir / f"{fbase}{ext}").write_bytes(data)
    try:
        storage.put_image(f"{static_base}{ext}", data, ctype)
    except Exception:
        pass
    # Pages feed the combined PDF via chapter_data.json — keep its path in step.
    if asset_type == "page":
        import re as _re
        m = _re.match(r"ch(\d+):p(\d+)", asset_key)
        if m:
            update_chapter_data_page(book_id, int(m.group(1)), int(m.group(2)),
                                     image_path=str(cdir / f"{fbase}{ext}"))


def _backfill_versions(book_id: str, asset_type: str, asset_key: str) -> None:
    """Import an asset's existing versions (history + current) into asset_versions
    from DURABLE storage (GCS), so versions generated before this system — or
    after a Cloud Run redeploy wiped the local disk — are still listed and
    pickable. Registers the existing GCS objects by reference (no re-upload).
    No-op once records exist."""
    from src.core.store import list_asset_versions, add_asset_version
    from src.core import storage

    if list_asset_versions(book_id, asset_type, asset_key)["versions"]:
        return
    cdir, fbase, static_base = _canonical_current(book_id, asset_type, asset_key)
    if static_base is None:
        return
    subdir = static_base.rsplit("/", 1)[0]  # e.g. "<book_id>/scenes"
    # Page history lives at the CHAPTER level (<book>/chapters/chNN/history/),
    # NOT under the pages/ subdir — so its backfill prefix is the chapter dir.
    # Scene/character/special keep their history as a sibling of the current.
    hist_dir = subdir.rsplit("/", 1)[0] if asset_type == "page" else subdir

    # History versions first (the filename's timestamp sorts them chronologically).
    keys = [
        k for k in storage.list_prefix(f"{hist_dir}/history/{fbase}_")
        if k.endswith((".png", ".jpg")) and "_quality" not in k
    ]
    keys.sort()
    # Current last == newest == the one auto-selected.
    for _e in (".png", ".jpg"):
        ck = f"{static_base}{_e}"
        if storage.exists(ck):
            keys.append(ck)
            break
    for k in keys:
        add_asset_version(book_id, asset_type, asset_key,
                          storage.image_url(k), storage_key=k)


@router.post("/api/book/{book_id}/asset/{asset_type}/{asset_key:path}/select")
async def select_asset_version(
    book_id: str, asset_type: str, asset_key: str, req: SelectVersionRequest,
    _user_key: str | None = Depends(_require_user_key),
) -> dict[str, Any]:
    """Pick a version: set the pointer AND promote its bytes to the live image,
    so the panels / page-gen reference / PDF all use it. asset_type ∈
    page|scene|character|special; asset_key is the page key / location name /
    canonical character name / 'page_type:chapter'."""
    if asset_type not in ("page", "scene", "character", "special"):
        raise HTTPException(status_code=400, detail="Invalid asset type.")
    from src.core.store import set_selected_version
    ok = await run_in_threadpool(
        set_selected_version, book_id, asset_type, asset_key, req.version_id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Version not found for this asset.")
    await run_in_threadpool(_promote_selected, book_id, asset_type, asset_key)
    return {"status": "selected", "version_id": req.version_id}


@router.get("/api/book/{book_id}/asset/{asset_type}/{asset_key:path}/versions")
async def list_asset_versions_endpoint(
    book_id: str, asset_type: str, asset_key: str,
) -> dict[str, Any]:
    """All versions + the selected id for an asset (read-only, open)."""
    if asset_type not in ("page", "scene", "character", "special"):
        raise HTTPException(status_code=400, detail="Invalid asset type.")
    from src.core.store import list_asset_versions
    await run_in_threadpool(_backfill_versions, book_id, asset_type, asset_key)
    return await run_in_threadpool(list_asset_versions, book_id, asset_type, asset_key)


# ---------------------------------------------------------------------------
# Book-wide style reference — one uploaded image that anchors EVERY scene /
# character / page generation, so the whole book stays in one style. Default is
# the cover; uploading overrides it; deleting reverts to the cover. Resolved by
# special_pages.get_style_ref.
# ---------------------------------------------------------------------------

@router.get("/api/book/{book_id}/style-reference")
async def get_style_reference(book_id: str) -> dict[str, Any]:
    """Current style reference (uploaded image if set, else the cover). Open."""
    from src.core import storage
    for ext in ("png", "jpg"):
        key = f"{book_id}/style_reference.{ext}"
        if storage.exists(key):
            return {"url": versioned_static_url(key, GENERATED_DIR / key), "custom": True}
    for ext in ("png", "jpg"):
        ck = f"{book_id}/special/book_cover.{ext}"
        if storage.exists(ck):
            return {"url": versioned_static_url(ck, GENERATED_DIR / ck), "custom": False}
    return {"url": None, "custom": False}


@router.post("/api/book/{book_id}/style-reference")
async def upload_style_reference(
    book_id: str, file: UploadFile = File(...),
    _user_key: str | None = Depends(_require_user_key),
) -> dict[str, Any]:
    """Upload/replace the book-wide style reference. Generation anchors to it."""
    ct = (file.content_type or "").lower()
    if not ct.startswith("image/"):
        raise HTTPException(status_code=400, detail="Please upload an image.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large (max 10 MB).")
    from src.core import storage
    ext = "png" if "png" in ct else "jpg"
    # Drop the other extension so the resolver can't pick a stale one.
    await run_in_threadpool(storage.delete_key,
                            f"{book_id}/style_reference.{'jpg' if ext == 'png' else 'png'}")
    key = f"{book_id}/style_reference.{ext}"
    await run_in_threadpool(storage.put_image, key, data,
                            "image/png" if ext == "png" else "image/jpeg")
    return {"status": "ok", "url": versioned_static_url(key, GENERATED_DIR / key), "custom": True}


@router.delete("/api/book/{book_id}/style-reference")
async def delete_style_reference(
    book_id: str, _user_key: str | None = Depends(_require_user_key),
) -> dict[str, Any]:
    """Revert to the default style reference (the cover)."""
    from src.core import storage
    for ext in ("png", "jpg"):
        await run_in_threadpool(storage.delete_key, f"{book_id}/style_reference.{ext}")
    return {"status": "reverted"}


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
        from src.core.store import get_characters as _get_chars_db
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
    # Durable existence from GCS, not this instance's /tmp (which is empty on a
    # cold serverless instance \u2014 sheets would vanish on refresh otherwise).
    sheet_files: dict[str, str] = {}    # safe_name -> gcs key
    portrait_files: dict[str, str] = {}
    for key in storage.list_keys(f"{book_id}/characters/"):
        fname = key.rsplit("/", 1)[-1]
        stem = fname.rsplit(".", 1)[0]
        if stem.endswith("_sheet"):
            sheet_files[stem[:-len("_sheet")]] = key
        elif stem.endswith("_portrait"):
            portrait_files[stem[:-len("_portrait")]] = key
    for char in chars:
        name = char.get("canonical_name", "")
        safe = _re.sub(r'[^\w\s\u4e00-\u9fff-]', '', name)
        safe = _re.sub(r'\s+', '_', safe.strip()).lower()[:50]
        if safe in sheet_files:
            sheets[name] = versioned_static_url(
                sheet_files[safe], chars_dir / sheet_files[safe].rsplit("/", 1)[-1])
        if safe in portrait_files:
            portraits[name] = versioned_static_url(
                portrait_files[safe], chars_dir / portrait_files[safe].rsplit("/", 1)[-1])

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
                from src.core.store import get_characters as _db_chars
                existing = {c.get("canonical_name") for c in _db_chars(book_id)}
            except Exception:
                existing = set()
        if new_name in existing:
            raise HTTPException(status_code=409, detail=f"A character named '{new_name}' already exists.")

    if target is None:
        # llm_characters.json is missing or blanked (e.g. after a failed re-preprocess).
        # The store's characters.json still has the character — update it there.
        try:
            from src.core.store import update_character as store_update_char
            if store_update_char(book_id, char_name, update_dict):
                if renamed:
                    _cascade_character_rename(book_id, char_name, new_name)
                return {"status": "updated", "character": new_name,
                        "updated_fields": list(update_dict.keys())}
        except Exception as e:
            logger.warning("Store character update failed for %s: %s", char_name, e)
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

    # Sync to the store's characters.json — the consistency source that
    # load_characters (and thus all generation) reads FIRST.
    try:
        from src.core.store import update_character as store_update_char
        store_update_char(book_id, char_name, update_dict)
    except Exception as e:
        logger.warning("Characters store write failed for %s: %s", char_name, e)

    if renamed:
        _cascade_character_rename(book_id, char_name, new_name)

    return {"status": "updated", "character": new_name, "updated_fields": list(update_dict.keys())}


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
            from src.core.store import update_character as db_update_char
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
        key = f"{book_id}/special/{base}{ext}"
        # Existence from GCS (durable), not this instance's /tmp — empty on a
        # cold serverless instance, which made covers vanish on refresh.
        if storage.exists(key):
            # Covers overwrite in place at a stable path — version by mtime when a
            # local copy exists (fresh re-gen), else the bare durable GCS URL.
            return versioned_static_url(key, special_dir / f"{base}{ext}")
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

        # Apply the user's edits as an OVERLAY under GCS optimistic concurrency,
        # so concurrent writers (multiple serverless instances, a stale browser
        # re-saving, Save-then-Regen) never clobber each other's edits. The old
        # plain load+save (_save_json) lost updates AND swallowed a failed GCS
        # write as a 200 — the "edit doesn't persist / regen uses stale data"
        # bug. A real durable-write failure now surfaces as 500, not a fake OK.
        captured: dict = {}

        def _apply(stored: dict) -> None:
            pages = stored.get("pages")
            if not isinstance(pages, dict):
                pages = {}
                stored["pages"] = pages
            overlay = pages.get(key) if isinstance(pages.get(key), dict) else {}
            edited = set(overlay.get("_edited") or [])
            for field, value in update_dict.items():
                overlay[field] = value
                edited.add(field)
            overlay["_edited"] = sorted(edited)
            pages[key] = overlay
            captured["doc"] = stored

        from src.core import store
        try:
            store.mutate_preprocess_file(book_id, "special_pages.json", _apply)
        except Exception as e:
            logger.warning("special-page save failed to persist for %s/%s: %s", book_id, key, e)
            raise HTTPException(status_code=500, detail=f"Save failed to persist: {e}")

        # Local same-invocation mirror (reads hit GCS first; this is a fast path).
        write_local_preprocess(book_id, "special_pages.json", captured.get("doc", {"pages": {}}))
        rec.update(update_dict)
    return {"status": "updated", "key": key, "page": rec}


@router.get("/api/book/{book_id}/special/{page_type}/history")
async def get_special_page_history(book_id: str, page_type: str, chapter: int = 0) -> dict[str, Any]:
    """Version list for a special page — built from the version store.

    Every stored version carries its own QA (set at regen time via
    set_version_quality). The selected version is mapped to version="current"
    so the frontend's carousel logic is unchanged. For pages with no version
    records yet, _backfill_versions migrates legacy history/ files on first
    call; never-recorded pages fall back to the current GCS image so nothing
    regresses.
    """
    from src.generation.special_page_data import special_file_base

    base = special_file_base(page_type, chapter)
    if not base:
        raise HTTPException(status_code=400, detail=f"Unknown special page type '{page_type}'.")

    asset_key = f"{page_type}:{chapter}"

    # Migrate legacy history/ files into the store on first call (no-op if already done).
    _backfill_versions(book_id, "special", asset_key)

    from src.core.store import list_asset_versions as _list_asset_versions
    rec = _list_asset_versions(book_id, "special", asset_key)
    versions = rec["versions"]
    selected_id = rec["selected_version_id"]

    def _epoch(ts_str: str | None) -> int:
        if not ts_str:
            return 0
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(ts_str)
            return int(dt.astimezone(timezone.utc).timestamp())
        except Exception:
            return 0

    if versions:
        # Build newest-first (stored order is oldest→newest).
        images: list[dict[str, Any]] = []
        for v in reversed(versions):
            is_selected = v["id"] == selected_id
            entry: dict[str, Any] = {
                "url": v["url"],
                "version": "current" if is_selected else v["id"],
                "timestamp": _epoch(v.get("created_at")),
            }
            q = v.get("quality")
            if q is not None:
                entry["quality"] = q
            elif is_selected:
                # Legacy fallback: selected entry with no stored quality falls
                # back to the per-page quality JSON so existing QA is not lost.
                rel_q = f"{book_id}/special/quality/{base}_quality.json"
                q_legacy = _load_quality(rel_q)
                if q_legacy is not None:
                    entry["quality"] = q_legacy
            images.append(entry)
        return {"images": images}

    # Fallback for special pages that have never been recorded in the version store.
    # Return the single current GCS image so the carousel still shows something.
    from src.core import storage
    for ext in (".png", ".jpg"):
        ck = f"{book_id}/special/{base}{ext}"
        if storage.exists(ck):
            entry = {"url": storage.image_url(ck), "version": "current", "timestamp": 0}
            rel_q = f"{book_id}/special/quality/{base}_quality.json"
            q = _load_quality(rel_q)
            if q is not None:
                entry["quality"] = q
            return {"images": [entry]}
    return {"images": []}


@router.post("/api/book/{book_id}/special/{page_type}/restore-version")
async def restore_special_page_version(
    book_id: str, page_type: str, version: str, chapter: int = 0,
) -> dict[str, Any]:
    """Select a stored version as the current one for a special page.

    Delegates entirely to the version store: set_selected_version flips the
    pointer, then _promote_selected copies the stored bytes onto the live
    special image. The old history/-file rename dance is gone; all versions
    live in the store.
    """
    if (book_id, "special", f"{page_type}:{chapter}") in _active_regens:
        raise HTTPException(status_code=409, detail="This page is regenerating — try again when it finishes.")

    from src.generation.special_page_data import special_file_base

    base = special_file_base(page_type, chapter)
    if not base:
        raise HTTPException(status_code=400, detail=f"Unknown special page type '{page_type}'.")

    asset_key = f"{page_type}:{chapter}"

    from src.core.store import set_selected_version as _set_selected
    ok = _set_selected(book_id, "special", asset_key, version)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Version {version} not found.")

    await run_in_threadpool(_promote_selected, book_id, "special", asset_key)

    # Build the live URL from _canonical_current (same path _promote_selected wrote to).
    cdir, fbase, static_base = _canonical_current(book_id, "special", asset_key)
    live_url = ""
    if cdir is not None:
        for ext in (".png", ".jpg"):
            p = cdir / f"{fbase}{ext}"
            if p.exists():
                live_url = versioned_static_url(f"{static_base}{ext}", p)
                break

    return {
        "status": "restored",
        "url": live_url,
    }


@router.get("/api/book/{book_id}/preprocess/locations")
async def get_locations(book_id: str) -> dict[str, Any]:
    """Get location list with scene reference images."""
    llm_locs = _load_json(book_id, "llm_locations.json")
    locations = llm_locs.get("locations", []) if llm_locs else []

    # Find scene reference images
    scenes_dir = GENERATED_DIR / book_id / "scenes"
    scene_sheets = {}
    # Existence from GCS (durable), not this instance's /tmp.
    scene_keys = set(storage.list_keys(f"{book_id}/scenes/"))
    import re as _re
    for loc in locations:
        name = loc.get("name", "")
        safe = _re.sub(r'[^\w\s\u4e00-\u9fff-]', '', name)
        safe = _re.sub(r'\s+', '_', safe.strip()).lower()[:50]
        for ext in (".png", ".jpg"):
            key = f"{book_id}/scenes/{safe}_scene{ext}"
            if key in scene_keys:
                scene_sheets[name] = versioned_static_url(key, scenes_dir / f"{safe}_scene{ext}")
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
    # Existence MUST come from GCS (the durable store), not this instance's
    # ephemeral /tmp — on serverless a generated illustration lives in GCS but
    # the local /tmp is empty on the next (cold) instance, so a local .exists()
    # made the page look un-generated after a refresh.
    pages_prefix = f"{book_id}/chapters/ch{ch_idx:02d}/pages/"
    page_keys = set(storage.list_keys(pages_prefix))
    for seg in ch_segments:
        page_num = segment_page_num(segments, ch_idx, seg.get("id", 0))
        for ext in (".png", ".jpg"):
            key = f"{pages_prefix}page_{page_num:03d}{ext}"
            if key in page_keys:
                # versioned_static_url adds ?v=<mtime> when a local copy exists
                # (fresh redraw, same instance), else the bare durable GCS URL.
                seg["illustration_url"] = versioned_static_url(
                    key, ch_dir / "pages" / f"page_{page_num:03d}{ext}")
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
    # can't lose each other's updates. The in-process lock only serializes ONE
    # serverless instance; analysis.json is a big shared GCS blob edited by
    # segment saves AND regen's text-simplification, so a cross-instance /
    # cross-request race (the user editing while a regen runs, or two edits at
    # once) lost updates with the old plain load+save — the "编辑了之后无法保存"
    # bug. Route it through GCS optimistic concurrency (if_generation_match +
    # retry) so no edit is clobbered, and surface a real write failure as 500
    # instead of a fake 200.
    from src.core import store
    captured: dict = {}

    def _apply(analysis: dict) -> None:
        segments = analysis.get("segments") if isinstance(analysis, dict) else None
        if not segments:
            raise KeyError("no-analysis")
        target = next((s for s in segments if s.get("id") == seg_id), None)
        if target is None:
            raise KeyError("no-segment")
        for key, value in update_dict.items():
            target[key] = value
        # A hand-edit takes ownership of this page's text: mark it so a later
        # "Gen chapter" / regen keeps it instead of overwriting with new text.
        if "simplified_text" in update_dict:
            target["text_source"] = TEXT_SOURCE_USER
        captured["analysis"] = analysis
        captured["target"] = target

    async with _analysis_lock(book_id):
        try:
            store.mutate_preprocess_file(book_id, "analysis.json", _apply)
        except KeyError as e:
            if str(e.args[0]) == "no-analysis":
                raise HTTPException(status_code=404, detail="No analysis data found.")
            raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("segment save failed to persist for %s/%d: %s", book_id, seg_id, e)
            raise HTTPException(status_code=500, detail=f"Save failed to persist: {e}")
        analysis = captured["analysis"]
        # Local same-invocation mirror (reads hit GCS first; this is a fast path).
        write_local_preprocess(book_id, "analysis.json", analysis)

    # The page text changed — the cached text-image-match verdict is stale now,
    # so drop it (best-effort) rather than keep reporting the old result.
    if "simplified_text" in update_dict or "text" in update_dict:
        _invalidate_page_quality(book_id, analysis.get("segments", []), seg_id)

    # And keep chapter_data.json (the PDF's text source) in step — edited text
    # used to stay stranded in analysis.json and never reach the next book.pdf.
    if "simplified_text" in update_dict:
        segments = analysis.get("segments", [])
        ch_idx = captured["target"].get("chapter_idx", 0)
        update_chapter_data_page(
            book_id, ch_idx, segment_page_num(segments, ch_idx, seg_id),
            text=update_dict["simplified_text"],
        )
        invalidate_chapter_consistency(book_id, ch_idx)

    return {"status": "updated", "segment_id": seg_id, "updated_fields": list(update_dict.keys())}


@router.get("/api/book/{book_id}/segment/{seg_id}/history")
async def get_segment_illustration_history(book_id: str, seg_id: int) -> dict[str, Any]:
    """Get all historical illustrations for a segment, built from the version store.

    Every stored version carries its own QA (set at regen time via
    set_version_quality). The selected version is mapped to version="current" so
    the frontend's carousel logic is unchanged. For pages that have no version
    records yet (_backfill_versions migrates legacy history/ files on first call;
    never-recorded pages fall back to the current GCS image so nothing regresses).
    """
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        return {"images": []}

    segments = analysis.get("segments", [])
    target = next((s for s in segments if s.get("id") == seg_id), None)
    if not target:
        return {"images": []}

    ch_idx = target.get("chapter_idx", 0)
    page_num = segment_page_num(segments, ch_idx, seg_id)
    asset_key = f"ch{ch_idx:02d}:p{page_num:03d}"

    # Migrate legacy history/ files into the store on first call (no-op if already done).
    _backfill_versions(book_id, "page", asset_key)

    from src.core.store import list_asset_versions as _list_asset_versions
    rec = _list_asset_versions(book_id, "page", asset_key)
    versions = rec["versions"]
    selected_id = rec["selected_version_id"]

    def _epoch(ts_str: str | None) -> int:
        if not ts_str:
            return 0
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(ts_str)
            return int(dt.astimezone(timezone.utc).timestamp())
        except Exception:
            return 0

    if versions:
        # Build newest-first (stored order is oldest→newest).
        images: list[dict[str, Any]] = []
        for v in reversed(versions):
            is_selected = v["id"] == selected_id
            entry: dict[str, Any] = {
                "url": v["url"],
                "version": "current" if is_selected else v["id"],
                "timestamp": _epoch(v.get("created_at")),
            }
            q = v.get("quality")
            if q is not None:
                entry["quality"] = q
            elif is_selected:
                # Legacy fallback: selected entry with no stored quality falls
                # back to the per-page quality JSON so existing QA is not lost.
                rel_q = f"{book_id}/chapters/ch{ch_idx:02d}/quality/page_{page_num:03d}_quality.json"
                q_legacy = _load_quality(rel_q)
                if q_legacy is not None:
                    entry["quality"] = q_legacy
            images.append(entry)
        return {"images": images}

    # Fallback for pages that have never been recorded in the version store.
    # Return the single current GCS image so the carousel still shows something.
    from src.core import storage as _storage
    pdir = f"{book_id}/chapters/ch{ch_idx:02d}/pages"
    for ext in (".png", ".jpg"):
        ck = f"{pdir}/page_{page_num:03d}{ext}"
        if _storage.exists(ck):
            entry = {"url": _storage.image_url(ck), "version": "current", "timestamp": 0}
            rel_q = f"{book_id}/chapters/ch{ch_idx:02d}/quality/page_{page_num:03d}_quality.json"
            q = _load_quality(rel_q)
            if q is not None:
                entry["quality"] = q
            return {"images": [entry]}
    return {"images": []}


@router.post("/api/book/{book_id}/segment/{seg_id}/restore-version")
async def restore_segment_version(book_id: str, seg_id: int, version: str) -> dict[str, Any]:
    """Select a stored version as the current one for a page segment.

    Delegates entirely to the version store: set_selected_version flips the
    pointer, then _promote_selected copies the stored bytes onto the live page
    image and updates chapter_data.json so the PDF stays consistent. The old
    history/-file rename dance is gone; all versions live in the store.
    """
    if (book_id, "segment", seg_id) in _active_regens:
        raise HTTPException(status_code=409, detail="This page is regenerating — try again when it finishes.")
    if book_generation_active(book_id):
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
    asset_key = f"ch{ch_idx:02d}:p{page_num:03d}"

    from src.core.store import set_selected_version as _set_selected
    ok = _set_selected(book_id, "page", asset_key, version)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Version {version} not found.")

    await run_in_threadpool(_promote_selected, book_id, "page", asset_key)

    # The page image changed — the chapter summary cache describes the old one.
    invalidate_chapter_consistency(book_id, ch_idx)

    # Build the live URL from _canonical_current (same path _promote_selected wrote to).
    cdir, fbase, static_base = _canonical_current(book_id, "page", asset_key)
    live_url = ""
    if cdir is not None:
        for ext in (".png", ".jpg"):
            p = cdir / f"{fbase}{ext}"
            if p.exists():
                live_url = versioned_static_url(f"{static_base}{ext}", p)
                break

    return {
        "status": "restored",
        "segment_id": seg_id,
        "illustration_url": live_url,
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
        # LLM-generated, not a hand-edit — a later re-gen may still replace it.
        "text_source": TEXT_SOURCE_WRITER,
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

