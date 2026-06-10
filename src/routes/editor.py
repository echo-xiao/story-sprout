"""Editor/segment endpoints."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.config import GENERATED_DIR
from src.routes.helpers import _load_json, _save_json
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

router = APIRouter()


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


@router.put("/api/book/{book_id}/preprocess/characters/{char_name}")
async def update_character(book_id: str, char_name: str, update: CharacterUpdate) -> dict[str, Any]:
    """Update a character's profile."""
    llm_chars = _load_json(book_id, "llm_characters.json")
    if not llm_chars:
        raise HTTPException(status_code=404, detail="No character data.")

    target = next((c for c in llm_chars.get("characters", []) if c.get("canonical_name") == char_name), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Character '{char_name}' not found.")

    update_dict = update.model_dump(exclude_none=True)
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

    # Sync to MongoDB
    try:
        from src.core.db import update_character as db_update_char
        db_update_char(book_id, char_name, update_dict)
    except Exception as e:
        logger.debug("MongoDB sync skipped for character %s: %s", char_name, e)

    return {"status": "updated", "character": char_name, "updated_fields": list(update_dict.keys())}


@router.post("/api/book/{book_id}/preprocess/characters/{char_name}/autofill")
async def autofill_character_details(book_id: str, char_name: str) -> dict[str, Any]:
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

    # Update the character data
    if result.get("appearance"):
        target["appearance"] = result["appearance"]
    if result.get("visual_details"):
        target["visual_details"] = result["visual_details"]

    _save_json(book_id, "llm_characters.json", llm_chars)

    return {
        "appearance": target.get("appearance", ""),
        "visual_details": target.get("visual_details", {}),
    }


@router.get("/api/book/{book_id}/special-pages")
async def get_special_pages(book_id: str) -> dict[str, Any]:
    """List all special pages (book cover, chapter covers/endings, back cover)."""
    special_dir = GENERATED_DIR / book_id / "special"
    pages = []

    # Book cover
    for ext in (".png", ".jpg"):
        p = special_dir / f"book_cover{ext}"
        if p.exists():
            pages.append({"type": "book_cover", "label": "Book Cover", "url": f"/static/{book_id}/special/{p.name}"})
            break
    else:
        pages.append({"type": "book_cover", "label": "Book Cover", "url": None})

    # Chapter covers and endings
    ch_segments = _load_json(book_id, "chapter_segments.json") or {}
    for ch_key in sorted(ch_segments.keys(), key=lambda x: int(x)):
        ch_info = ch_segments[ch_key]
        ch_num = int(ch_key)

        # Chapter cover
        cover_url = None
        for ext in (".png", ".jpg"):
            p = special_dir / f"chapter_{ch_num:02d}_cover{ext}"
            if p.exists():
                cover_url = f"/static/{book_id}/special/{p.name}"
                break
        pages.append({
            "type": "chapter_cover",
            "chapter": ch_num,
            "label": f"Ch {ch_num + 1} Cover",
            "chapter_title": ch_info.get("chapter_title", ""),
            "chapter_summary": ch_info.get("chapter_summary", ""),
            "url": cover_url,
        })

        # Chapter ending
        ending_url = None
        for ext in (".png", ".jpg"):
            p = special_dir / f"chapter_{ch_num:02d}_ending{ext}"
            if p.exists():
                ending_url = f"/static/{book_id}/special/{p.name}"
                break
        pages.append({
            "type": "chapter_ending",
            "chapter": ch_num,
            "label": f"Ch {ch_num + 1} Ending",
            "url": ending_url,
        })

    # Back cover
    for ext in (".png", ".jpg"):
        p = special_dir / f"back_cover{ext}"
        if p.exists():
            pages.append({"type": "back_cover", "label": "Back Cover", "url": f"/static/{book_id}/special/{p.name}"})
            break
    else:
        pages.append({"type": "back_cover", "label": "Back Cover", "url": None})

    return {"pages": pages}


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
            images.append({
                "url": f"/static/{book_id}/scenes/history/{f.name}",
                "version": f.stem.split("_")[-1],
                "timestamp": float(f.stem.split("_")[-1]),
            })

    return {"images": images}


@router.get("/api/book/{book_id}/preprocess/characters/{char_name}/history")
async def get_character_sheet_history(book_id: str, char_name: str) -> dict[str, Any]:
    """Get current + historical character sheet images."""
    import re as _re
    from src.generation.character_sheet import _safe_filename

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
            images.append({
                "url": f"/static/{book_id}/characters/history/{f.name}",
                "version": f.stem.split("_")[-1],
                "timestamp": float(f.stem.split("_")[-1]),
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

    # Add illustration paths if they exist
    ch_dir = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}"
    for seg in ch_segments:
        page_num = seg.get("id", 0) - min((s.get("id", 0) for s in ch_segments), default=0) + 1
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

    # Apply updates
    update_dict = update.model_dump(exclude_none=True)
    for key, value in update_dict.items():
        target[key] = value

    # Save back to JSON
    _save_json(book_id, "analysis.json", analysis)

    # Sync to MongoDB
    try:
        from src.core.db import update_segment as db_update_segment
        db_update_segment(book_id, seg_id, update_dict)
    except Exception as e:
        logger.debug("MongoDB sync skipped for segment %d: %s", seg_id, e)

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
    ch_segments = sorted([s for s in segments if s.get("chapter_idx") == ch_idx], key=lambda s: s.get("id", 0))
    page_num = next((i + 1 for i, s in enumerate(ch_segments) if s.get("id") == seg_id), 1)

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


@router.post("/api/book/{book_id}/segment/{seg_id}/simplify")
async def simplify_segment_text(book_id: str, seg_id: int) -> dict[str, Any]:
    """Generate simplified text for a single segment."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data.")
    target = next((s for s in analysis["segments"] if s.get("id") == seg_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")

    from src.agent.text_simplifier import simplify_text
    scene = {
        "page_number": 1,
        "original_text": target.get("text", ""),
        "key_characters": target.get("characters_in_scene", []),
        "scene_summary": target.get("scene_summary", ""),
    }
    result = await run_in_threadpool(simplify_text, [scene], "4-6")
    simplified = result[0].get("page_text", "") if result else ""
    scene_direction = result[0].get("scene_direction", "") if result else ""

    # Save back
    target["simplified_text"] = simplified
    target["scene_direction"] = scene_direction
    _save_json(book_id, "analysis.json", analysis)

    return {"simplified_text": simplified, "scene_direction": scene_direction}


@router.post("/api/book/{book_id}/segment/{seg_id}/background")
async def generate_segment_background(book_id: str, seg_id: int) -> dict[str, Any]:
    """Generate scene background description for a single segment."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data.")
    target = next((s for s in analysis["segments"] if s.get("id") == seg_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")

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

    target["scene_background"] = background
    _save_json(book_id, "analysis.json", analysis)

    return {"scene_background": background}


@router.post("/api/book/{book_id}/segment/{seg_id}/summarize")
async def summarize_segment(book_id: str, seg_id: int) -> dict[str, Any]:
    """Generate summary and sentiment for a single segment."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data.")
    target = next((s for s in analysis["segments"] if s.get("id") == seg_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")

    from src.llm_client import generate_json
    result = await run_in_threadpool(generate_json,
        f"""Summarize this scene in one sentence. Also determine the sentiment.

Scene text:
{target.get('text', '')[:1000]}

Return JSON: {{"scene_summary": "one sentence summary", "sentiment": "positive/negative/neutral/tense/emotional"}}"""
    )
    summary = result.get("scene_summary", "")
    sentiment = result.get("sentiment", "neutral")

    target["scene_summary"] = summary
    target["sentiment"] = sentiment
    _save_json(book_id, "analysis.json", analysis)

    return {"scene_summary": summary, "sentiment": sentiment}


class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []  # [{"role": "user"/"assistant", "content": "..."}]


@router.post("/api/book/{book_id}/segment/{seg_id}/chat")
async def chat_segment_prompt(book_id: str, seg_id: int, req: ChatRequest) -> dict[str, Any]:
    """AI assistant to help generate/refine illustration prompt fields via chat."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data.")
    target = next((s for s in analysis["segments"] if s.get("id") == seg_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")

    # Build context from current segment
    context = (
        f"Original text:\n{target.get('text', '')[:1500]}\n\n"
        f"Current simplified_text: {target.get('simplified_text', '')}\n"
        f"Current scene_background: {target.get('scene_background', '')}\n"
        f"Current characters & actions: {json.dumps(target.get('character_actions', []), ensure_ascii=False)}\n"
        f"Current scene_summary: {target.get('scene_summary', '')}\n"
        f"Current sentiment: {target.get('sentiment', 'neutral')}\n"
    )

    system_prompt = """You are an illustration prompt assistant for a children's picture book generator.
The user is editing a page of a picture book adapted from a novel. They will describe what they want the illustration to look like, or ask you to adjust specific fields.

You have access to the current segment data (original text, simplified text, scene background, characters & actions, summary, sentiment).

Based on the user's request, return a JSON object with TWO keys:
1. "reply": a short, helpful response to the user (in the same language the user uses)
2. "updates": an object containing ONLY the fields that should be updated. Possible fields:
   - "simplified_text": the picture-book text for this page
   - "scene_background": visual description of the setting
   - "character_actions": array of {"name": "...", "action": "..."} objects
   - "scene_summary": one-sentence summary
   - "sentiment": one of "positive", "negative", "neutral", "tense", "emotional"

Only include fields in "updates" that the user wants to change. If the user is just asking a question, return empty updates {}.

Example response:
{"reply": "I've updated the background to a rainy night scene.", "updates": {"scene_background": "A dark, rainy night in London..."}}"""

    # Build conversation for LLM
    conversation = f"Current segment context:\n{context}\n\n"
    for msg in req.history[-10:]:  # keep last 10 messages
        role = msg.get("role", "user")
        conversation += f"{'User' if role == 'user' else 'Assistant'}: {msg['content']}\n"
    conversation += f"User: {req.message}"

    from src.llm_client import generate_json
    result = await run_in_threadpool(generate_json,conversation, system=system_prompt)

    reply = result.get("reply", "")
    updates = result.get("updates", {})

    # Apply updates to analysis
    if updates:
        for field in ("simplified_text", "scene_background", "scene_summary", "sentiment"):
            if field in updates:
                target[field] = updates[field]
        if "character_actions" in updates:
            target["character_actions"] = updates["character_actions"]
            target["characters_in_scene"] = [
                a["name"] for a in updates["character_actions"] if a.get("name")
            ]
        _save_json(book_id, "analysis.json", analysis)

    return {"reply": reply, "updates": updates}
