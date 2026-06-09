#!/usr/bin/env python3
"""Preprocess a book: 6-layer data pipeline (run once per book).

Layer 1: Raw text → chapters
Layer 2: LLM character identification → characters + alias map
Layer 3: Character sheets + visual identity (Gemini Image, main+supporting only)
Layer 4: Alias replacement → cleaned text
Layer 5: TextTiling segmentation (on cleaned text)
Layer 6: LLM annotation → characters_in_scene, sentiment, key events per segment

Usage:
    python scripts/preprocess_book.py --input data/sample_books/a_tale_of_two_cities.txt
    python scripts/preprocess_book.py --input data/sample_books/a_tale_of_two_cities.txt --skip-sheets
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from tqdm import tqdm

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from src.config import GENERATED_DIR


# ═══════════════════════════════════════════════════════════════
# Layer 2: LLM character identification
# ═══════════════════════════════════════════════════════════════

def _llm_identify_characters(title: str, chapters: list[dict]) -> list[dict]:
    """LLM reads chapters in batches of 5 → merge and deduplicate characters."""
    from src.llm_client import generate_json

    BATCH_SIZE = 5
    all_raw_characters = []

    for batch_start in range(0, len(chapters), BATCH_SIZE):
        batch = chapters[batch_start:batch_start + BATCH_SIZE]
        batch_end = min(batch_start + BATCH_SIZE, len(chapters))
        print(f"    Batch {batch_start}-{batch_end - 1} / {len(chapters) - 1}...")

        chapter_text = "\n\n".join(
            f"[{ch.get('title', f'Chapter {batch_start + j + 1}')}]\n{ch.get('text', '')}"
            for j, ch in enumerate(batch)
        )

        try:
            result = generate_json(f"""Analyze these chapters from the novel "{title}" and list ALL named characters that APPEAR.

Text:
{chapter_text}

For each character provide:
- canonical_name: Most recognizable name form (include title if commonly used, e.g. "Mr. Lorry", "Dr. Manette")
- aliases: All other name forms (full names, titles, shortened forms, descriptive references like "the prisoner")
- gender: "male" or "female"
- role: "main", "supporting", or "minor"
- description: One sentence about who they are
- appearance: Physical appearance from the text (hair, clothing, build, age, distinctive features). Be as detailed as possible.
- visual_details: Structured appearance breakdown:
  - age: approximate age or age description (e.g. "60", "elderly", "young woman")
  - ethnicity: ethnic background if mentioned (e.g. "French", "English")
  - skin_tone: skin description (e.g. "pale", "rosy cheeks", "dark")
  - hair: hair color, style, length (e.g. "crisp flaxen wig", "long dark curly hair")
  - eyes: eye color and description (e.g. "bright moist eyes", "dark sharp eyes")
  - build: body type (e.g. "stout", "tall and thin", "short slight figure")
  - clothing: typical outfit (e.g. "brown suit with large square cuffs")
  - accessories: notable items (e.g. "round spectacles", "knitting needles", "cane")
  - distinctive: most recognizable feature (e.g. "healthy colour in cheeks", "wild white hair")

Rules:
- "Monsieur Defarge" and "Madame Defarge" are DIFFERENT characters
- "Jacques One", "Jacques Two", "Jacques Three" are SEPARATE people
- Do NOT include places, objects, or abstract concepts
- For visual_details, extract ONLY what the text actually describes. Leave fields empty if not mentioned.

Return JSON: {{"characters": [{{...}}]}}""")
            all_raw_characters.extend(result.get("characters", []))
        except Exception as e:
            print(f"    WARNING: Batch {batch_start}-{batch_end - 1} failed: {e}")

    # Merge and deduplicate by canonical_name (case-insensitive)
    merged: dict[str, dict] = {}
    for c in all_raw_characters:
        name = c.get("canonical_name", "").strip()
        if not name:
            continue
        key = name.lower()
        if key not in merged:
            merged[key] = c
        else:
            # Merge: keep the longer/richer version of each field
            existing = merged[key]
            for field in ("description", "appearance"):
                if len(c.get(field) or "") > len(existing.get(field) or ""):
                    existing[field] = c[field]
            # Merge aliases
            existing_aliases = set(a.lower() for a in existing.get("aliases", []))
            for alias in c.get("aliases", []):
                if alias.lower() not in existing_aliases:
                    existing.setdefault("aliases", []).append(alias)
            # Upgrade role: main > supporting > minor
            role_priority = {"main": 3, "supporting": 2, "minor": 1}
            if role_priority.get(c.get("role", ""), 0) > role_priority.get(existing.get("role", ""), 0):
                existing["role"] = c["role"]
            # Merge visual_details
            if c.get("visual_details"):
                existing.setdefault("visual_details", {})
                for vk, vv in c["visual_details"].items():
                    if vv and not existing["visual_details"].get(vk):
                        existing["visual_details"][vk] = vv

    characters = list(merged.values())
    print(f"    Merged → {len(characters)} characters (pre-dedup)")

    # Second pass: LLM deduplicates and unifies naming across batches
    if len(characters) > 3:
        char_summary = "\n".join(
            f"- {c['canonical_name']} (aliases: {', '.join(c.get('aliases', [])[:5])}; role: {c.get('role','?')}; desc: {c.get('description','')[:80]})"
            for c in characters
        )
        try:
            dedup_result = generate_json(f"""You are given a list of characters extracted from the novel "{title}" in batches.
Some characters may be duplicated under different names. Merge them.

Characters found:
{char_summary}

Return a JSON object with:
- "merge_map": a dict mapping each duplicate canonical_name to the CORRECT canonical_name it should merge into.
  Only include entries that need merging. If "Sydney Carton" and "Carton" are the same person, return {{"Carton": "Sydney Carton"}}.
  The target name should be the most complete/recognizable form.
- "role_updates": a dict mapping canonical_name to corrected role ("main"/"supporting"/"minor") based on overall importance in the full novel, not just one chapter.

Example: {{"merge_map": {{"Carton": "Sydney Carton"}}, "role_updates": {{"Sydney Carton": "main"}}}}

Only merge characters that are truly the SAME PERSON. Do NOT merge different people (e.g. "Monsieur Defarge" and "Madame Defarge" are different).
Return JSON: {{"merge_map": {{}}, "role_updates": {{}}}}""")

            merge_map = dedup_result.get("merge_map", {})
            role_updates = dedup_result.get("role_updates", {})

            if merge_map:
                print(f"    Dedup merges: {merge_map}")
                # Apply merges
                final = {}
                for c in characters:
                    name = c["canonical_name"]
                    target = merge_map.get(name, name)
                    if target not in final:
                        final[target] = {**c, "canonical_name": target}
                    else:
                        # Merge fields into target
                        existing = final[target]
                        for field in ("description", "appearance"):
                            if len(c.get(field) or "") > len(existing.get(field) or ""):
                                existing[field] = c[field]
                        existing_aliases = set(a.lower() for a in (existing.get("aliases") or []))
                        for alias in c.get("aliases", []):
                            if alias.lower() not in existing_aliases:
                                existing.setdefault("aliases", []).append(alias)
                        if c.get("visual_details"):
                            existing.setdefault("visual_details", {})
                            for vk, vv in c["visual_details"].items():
                                if vv and not existing["visual_details"].get(vk):
                                    existing["visual_details"][vk] = vv
                characters = list(final.values())

            # Apply role updates
            for c in characters:
                if c["canonical_name"] in role_updates:
                    c["role"] = role_updates[c["canonical_name"]]

            print(f"    After dedup → {len(characters)} unique characters")
        except Exception as e:
            print(f"    WARNING: Dedup pass failed (using raw merge): {e}")

    return characters


def _llm_identify_locations(title: str, chapters: list[dict]) -> list[dict]:
    """LLM identifies key recurring locations/settings from the novel."""
    from src.llm_client import generate_json

    chapter_text = "\n\n".join(
        f"[{ch.get('title', f'Chapter {i+1}')}]\n{ch.get('text', '')[:2000]}"
        for i, ch in enumerate(chapters)
    )

    result = generate_json(f"""Analyze the novel "{title}" and list the KEY RECURRING LOCATIONS where important scenes happen.

Text (excerpts):
{chapter_text}

Only list locations that appear in MULTIPLE chapters or are central to the story. NOT every room or street — only the most important 5-15 locations.

For each location provide:
- name: Short recognizable name (e.g. "Defarge Wine Shop", "The Bastille", "Tellson's Bank")
- aliases: Other ways this location is referred to
- description: One sentence about what this place is
- visual_details: Structured visual breakdown:
  - setting: indoor/outdoor/both
  - time_period: historical era (e.g. "1780s France", "1780s England")
  - architecture: building style (e.g. "narrow stone staircase", "grand courtroom")
  - lighting: typical lighting (e.g. "dim candlelight", "bright daylight", "gloomy")
  - atmosphere: mood/feeling (e.g. "oppressive", "bustling", "eerie")
  - key_objects: notable objects always present (e.g. "wine barrels", "workbench", "guillotine")
  - colors: dominant color palette (e.g. "grey stone, dark wood", "red and gold")
- chapters_appeared: list of chapter numbers where this location appears (0-indexed)
- importance: "major" or "minor"

Rules:
- Focus on PHYSICAL locations, not abstract concepts
- Merge duplicates (e.g. "the wine shop" and "Defarge's shop" are the same)
- Include both French and English locations if the story spans countries

Return JSON: {{"locations": [{{...}}]}}""")

    return result.get("locations", [])


# ═══════════════════════════════════════════════════════════════
# Layer 4: Alias replacement
# ═══════════════════════════════════════════════════════════════

def _build_alias_map(characters: list[dict]) -> dict[str, str]:
    """Build multi-word alias → canonical_name map."""
    alias_map = {}
    for char in characters:
        canonical = char.get("canonical_name", "")
        if not canonical:
            continue
        for alias in char.get("aliases", []):
            alias_lower = alias.lower().strip()
            if not alias_lower or alias_lower == canonical.lower():
                continue
            if len(alias_lower.split()) < 2:
                continue  # Single words too ambiguous for global replace
            if alias_lower in alias_map and alias_map[alias_lower] != canonical:
                del alias_map[alias_lower]
                continue
            alias_map[alias_lower] = canonical
    return alias_map


def _replace_aliases(text: str, alias_map: dict[str, str]) -> str:
    """Replace multi-word aliases in text with canonical names."""
    for alias, canonical in sorted(alias_map.items(), key=lambda x: -len(x[0])):
        pattern = r'\b' + re.escape(alias) + r'\b'
        if canonical.lower() not in text.lower():
            text = re.sub(pattern, canonical, text, flags=re.IGNORECASE)
    return text


# ═══════════════════════════════════════════════════════════════
# Layer 5: TextTiling + post-process
# ═══════════════════════════════════════════════════════════════

def _segment_text(full_text: str, chapters: list[dict], max_words: int = 400) -> list[dict]:
    """TextTiling on full text, then split oversized segments by sentences."""
    from src.analysis.chapter_split import split_into_segments

    segments = split_into_segments(full_text, chapters=chapters)

    # Post-process: split segments over max_words by sentences
    result = []
    for seg in segments:
        text = seg.get("text", "")
        if len(text.split()) <= max_words:
            result.append(seg)
            continue

        normalized = re.sub(r'\s+', ' ', text)
        sentences = re.split(r'(?<=[.!?])\s+', normalized)
        if len(sentences) <= 1:
            result.append(seg)
            continue

        chunks = []
        current = ""
        for sent in sentences:
            if current and len((current + " " + sent).split()) > max_words:
                chunks.append(current.strip())
                current = sent
            else:
                current = (current + " " + sent).strip() if current else sent
        if current.strip():
            chunks.append(current.strip())

        if len(chunks) <= 1:
            result.append(seg)
            continue

        for chunk in chunks:
            result.append({**seg, "text": chunk.strip()})

    return result


# ═══════════════════════════════════════════════════════════════
# Layer 6: LLM annotation
# ═══════════════════════════════════════════════════════════════

def _llm_annotate_chapter(title: str, ch_title: str, segments: list[dict], characters: list[dict]) -> list[dict]:
    """LLM annotates segments with full detail: characters, actions, background, summary, simplified text."""
    from src.llm_client import generate_json

    char_list = "\n".join(f"- {c['canonical_name']} ({c.get('role','?')}, {c.get('gender','?')}): {c.get('description','')[:100]}" for c in characters)

    seg_texts = [f"[Scene {i+1}]\n{seg['text']}" for i, seg in enumerate(segments)]

    prompt = f"""You are annotating scenes from the novel "{title}", {ch_title}, for a children's picture book.

KNOWN CHARACTERS:
{char_list}

SCENES:
{chr(10).join(seg_texts)}

For EACH scene, provide ALL of the following fields:

1. scene_number (1-based integer)

2. characters_in_scene: Array of characters PHYSICALLY PRESENT in this scene.
   - RESOLVE ALL PRONOUNS: "he" → use full name, "she" → use full name, "his wife" → use her actual name, "the old man" → use his actual name.
   - ONLY include characters who are physically there and doing something.
   - Format: [{{"name": "Madame Defarge", "action": "knitting silently at the counter"}}]
   - NEVER use pronouns or descriptions like "his wife", "the tall man". Always use the canonical name from the character list.

3. scene_background: Detailed physical description of the setting.
   - MUST be specific and visual: location, time of day, lighting, objects, colors, atmosphere.
   - NEVER write "same as scene X" or "as before". Every scene must have its own unique, complete description.
   - Example: "Inside the Defarge wine shop. A long wooden counter with wine barrels behind it. Dim candlelight. Stone walls stained with age. A narrow door leads to a dark staircase."

4. scene_summary: One sentence summary using character FULL NAMES (no pronouns).

5. sentiment: One of "positive", "negative", "neutral", "tense", "emotional"

6. simplified_text: Rewrite this scene as a children's picture book page (age 4-6).
   - Short sentences (max 10 words each), 3-6 sentences total.
   - Use character full names, not pronouns.
   - Keep key dialogue as direct speech.
   - Simple vocabulary, vivid and concrete.

7. is_key_event: true/false
8. event_description: If key event, one sentence describing what happens (else null)

CRITICAL RULES:
- NEVER use pronouns (he/she/they/his/her) in ANY field. Always use the character's full canonical name.
- NEVER reference other scenes ("same as scene 6", "continues from before"). Each annotation must be self-contained.
- EVERY character physically present MUST be listed with a specific action.

Return JSON: {{"annotations": [...]}}"""

    result = generate_json(prompt)

    raw_annotations = result.get("annotations", [])
    annotations = {}
    for idx, a in enumerate(raw_annotations):
        key = a.get("scene_number", idx + 1)
        annotations[key] = a
    for i, seg in enumerate(segments):
        ann = annotations.get(i + 1, {})
        llm_chars = ann.get("characters_in_scene")
        if llm_chars is not None:
            # characters_in_scene is now [{name, action}, ...]
            # Store both the full list and a flat name list for compatibility
            if llm_chars and isinstance(llm_chars[0], dict):
                seg["characters_in_scene"] = [c["name"] for c in llm_chars]
                seg["character_actions"] = llm_chars  # [{name, action}, ...]
            else:
                # Fallback if LLM returns flat list
                seg["characters_in_scene"] = llm_chars
        seg["scene_summary"] = ann.get("scene_summary", "")
        seg["scene_background"] = ann.get("scene_background", "")
        seg["sentiment"] = ann.get("sentiment", "neutral")
        seg["simplified_text"] = ann.get("simplified_text", "")
        seg["is_key_event"] = ann.get("is_key_event", False)
        seg["event_description"] = ann.get("event_description")

    return segments


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def _save(preprocess_dir, name, data, subdir=None):
    """Save data as JSON to the preprocess directory."""
    target = preprocess_dir / subdir if subdir else preprocess_dir
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    print(f"  → {path.relative_to(GENERATED_DIR)}")


def _layer1_extract_text(input_path, book_id, preprocess_dir):
    """Layer 1: Text extraction + chapter split."""
    print("\n[Layer 1/6] Extracting text...")
    t0 = time.time()
    from src.extraction import extract_text
    from src.agent.mcp_server import _strip_book_metadata

    source = input_path.read_text(encoding="utf-8", errors="replace")
    print(f"Loaded {len(source)} chars from {input_path.name}")

    result = extract_text(source)
    full_text = _strip_book_metadata(result.get("full_text", ""))
    chapters = result.get("chapters", [])
    title = result.get("title", input_path.stem)

    sanitized = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', title)
    book_id = re.sub(r'\s+', '_', sanitized.strip()).lower()[:60] or input_path.stem.lower()

    chapters = [ch for ch in chapters if len(ch.get("text", "")) > 200]

    # Fix chapter titles: extract subtitle from text if missing
    # e.g., "CHAPTER I." + text starts with "CHAPTER I. The Period" → title = "CHAPTER I. The Period"
    for ch in chapters:
        text = ch.get("text", "")
        title_match = re.match(r'(CHAPTER\s+[IVXLC]+\.?\s+[A-Z][^\n]+)', text)
        if title_match:
            ch["title"] = title_match.group(1).strip()

    print(f"  Title: {title}")
    print(f"  Book ID: {book_id}")
    print(f"  Chapters: {len(chapters)}")
    print(f"  Time: {time.time() - t0:.1f}s")

    preprocess_dir = GENERATED_DIR / book_id / "preprocess"
    preprocess_dir.mkdir(parents=True, exist_ok=True)

    _save(preprocess_dir, "meta", {"title": title, "book_id": book_id, "source_file": str(input_path),
                    "num_chapters": len(chapters), "text_length": len(full_text)})
    _save(preprocess_dir, "chapters", chapters)
    _save(preprocess_dir, "full_text", {"text": full_text})

    return title, book_id, full_text, chapters, preprocess_dir


def _layer2_identify_characters(book_id, preprocess_dir, chapters, title):
    """Layer 2: LLM character identification + location identification."""
    provider = "DeepSeek" if os.getenv("TEXT_LLM", "deepseek") == "deepseek" else "Gemini"

    # Characters
    print(f"\n[Layer 2/6] LLM character identification ({provider})...")
    t0 = time.time()
    characters = _llm_identify_characters(title, chapters)
    print(f"  {len(characters)} characters in {time.time() - t0:.1f}s:")
    for c in characters:
        aliases = ", ".join(c.get("aliases", [])[:3])
        print(f"    {c['canonical_name']} ({c['gender']}, {c['role']}) [{aliases}]")
    _save(preprocess_dir, "llm_characters", {"characters": characters})

    # Locations
    print(f"  Identifying key locations...")
    t1 = time.time()
    locations = _llm_identify_locations(title, chapters)
    print(f"  {len(locations)} locations in {time.time() - t1:.1f}s:")
    for loc in locations:
        print(f"    {loc['name']} ({loc.get('importance', '?')})")
    _save(preprocess_dir, "llm_locations", {"locations": locations})

    return characters


def _layer3_build_aliases(book_id, preprocess_dir, characters):
    """Layer 3: Alias map building."""
    alias_map = _build_alias_map(characters)
    gender_map = {c["canonical_name"]: c.get("gender", "unknown") for c in characters}

    _save(preprocess_dir, "alias_map", alias_map)
    _save(preprocess_dir, "character_genders", gender_map)

    return alias_map, gender_map


def _generate_character_sheets(book_id, preprocess_dir, characters, skip_sheets):
    """Character sheet generation (Gemini Image) — optional step between layers."""
    if skip_sheets:
        print(f"\n[Character Sheets] SKIPPED (--skip-sheets)")
        return

    print(f"\n[Character Sheets] Generating character sheets (Gemini Image)...")
    t0 = time.time()
    from src.generation.character_sheet import generate_character_sheets

    # Build profiles for sheet generation (main + supporting only)
    sheet_profiles = []
    for c in characters:
        if c.get("role") in ("main", "supporting"):
            sheet_profiles.append({
                "name": c["canonical_name"],
                "role": c.get("role", "supporting"),
                "personality_traits": [],
                "appearance_description": [
                    c.get("appearance", ""),
                    c.get("description", ""),
                ],
            })

    print(f"  Generating sheets for {len(sheet_profiles)} characters (main + supporting)...")
    sheets = generate_character_sheets(sheet_profiles, book_id, max_characters=0)
    dt = time.time() - t0
    print(f"  Generated {len(sheets)} sheets in {dt:.1f}s")
    for s in sheets:
        print(f"    {s['character_name']}: {s.get('sheet_path', 'FAILED')}")

    _save(preprocess_dir, "character_sheets", sheets)


def _layer4_replace_aliases(book_id, preprocess_dir, chapters, full_text, alias_map):
    """Layer 4: Alias replacement in text."""
    print(f"\n[Layer 4/6] Replacing aliases in text...")
    t0 = time.time()
    print(f"  {len(alias_map)} alias mappings")

    cleaned_chapters = []
    for ch in chapters:
        cleaned_text = _replace_aliases(ch.get("text", ""), alias_map)
        cleaned_chapters.append({**ch, "text": cleaned_text})

    cleaned_full_text = _replace_aliases(full_text, alias_map)
    dt = time.time() - t0
    print(f"  Done in {dt:.1f}s")

    _save(preprocess_dir, "cleaned_full_text", {"text": cleaned_full_text})
    _save(preprocess_dir, "cleaned_chapters", cleaned_chapters)

    return cleaned_chapters, cleaned_full_text


def _layer5_segment_text(book_id, preprocess_dir, cleaned_chapters, cleaned_full_text, chapters):
    """Layer 5: TextTiling segmentation."""
    print(f"\n[Layer 5/6] TextTiling segmentation...")
    t0 = time.time()
    all_segments = _segment_text(cleaned_full_text, cleaned_chapters)
    dt = time.time() - t0
    print(f"  {len(all_segments)} segments in {dt:.1f}s")

    # Group by chapter
    ch_seg_groups: dict[int, list[dict]] = {}
    for seg in all_segments:
        ch_idx = seg.get("chapter_idx", -1)
        ch_seg_groups.setdefault(ch_idx, []).append(seg)

    for ch_idx in sorted(ch_seg_groups.keys()):
        segs = ch_seg_groups[ch_idx]
        ch_title = chapters[ch_idx].get("title", f"Ch {ch_idx}") if ch_idx < len(chapters) else "?"
        print(f"    Chapter {ch_idx} ({ch_title}): {len(segs)} segments")

    _save(preprocess_dir, "segments_raw", all_segments)

    return all_segments, ch_seg_groups


def _layer6_annotate(book_id, preprocess_dir, chapters, characters, title, ch_seg_groups, skip_sheets):
    """Layer 6: LLM annotation per segment."""
    print(f"\n[Layer 6/6] LLM annotation (characters, sentiment, events)...")
    all_events = []
    chapter_segments_map = {}
    segment_id = 0

    # Checkpoint directory for per-chapter annotations
    checkpoint_dir = preprocess_dir / "annotations"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    sorted_ch_keys = sorted(ch_seg_groups.keys())
    skipped = 0
    for ch_idx in tqdm(sorted_ch_keys, desc="  Annotating chapters", unit="ch"):
        segs = ch_seg_groups[ch_idx]
        ch_title = chapters[ch_idx].get("title", f"Ch {ch_idx}") if ch_idx < len(chapters) else "?"
        checkpoint_file = checkpoint_dir / f"ch{ch_idx:03d}.json"

        # Check if this chapter was already annotated
        if checkpoint_file.exists():
            cached = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            # Restore annotations into segments
            for i, seg in enumerate(segs):
                if i < len(cached):
                    seg.update(cached[i])
            skipped += 1
        else:
            try:
                segs = _llm_annotate_chapter(title, ch_title, segs, characters)
                # Save checkpoint
                checkpoint_file.write_text(
                    json.dumps([{
                        "characters_in_scene": s.get("characters_in_scene", []),
                        "character_actions": s.get("character_actions", []),
                        "scene_background": s.get("scene_background", ""),
                        "scene_summary": s.get("scene_summary", ""),
                        "sentiment": s.get("sentiment", "neutral"),
                        "is_key_event": s.get("is_key_event", False),
                        "event_description": s.get("event_description"),
                    } for s in segs], indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception as e:
                tqdm.write(f"  WARNING: Chapter {ch_idx} annotation failed: {e}")

        # Assign stable IDs and collect events
        seg_ids = []
        for seg in segs:
            seg["id"] = segment_id
            seg["chapter_idx"] = ch_idx
            seg_ids.append(segment_id)
            segment_id += 1

            if seg.get("is_key_event"):
                all_events.append({
                    "segment_id": seg["id"],
                    "chapter_idx": ch_idx,
                    "description": seg.get("event_description", ""),
                    "characters": seg.get("characters_in_scene", []),
                })

        chapter_segments_map[str(ch_idx)] = {
            "chapter_title": ch_title,
            "num_segments": len(segs),
            "segment_ids": seg_ids,
        }

    if skipped:
        print(f"  Skipped {skipped} chapters (already annotated)")

    # Flatten all segments (now with IDs and annotations)
    final_segments = []
    for ch_idx in sorted(ch_seg_groups.keys()):
        final_segments.extend(ch_seg_groups[ch_idx])

    segs_with_chars = sum(1 for s in final_segments if s.get("characters_in_scene"))
    total_chars = sum(len(s.get("characters_in_scene", [])) for s in final_segments)
    print(f"\n  {segs_with_chars}/{len(final_segments)} segments have characters")
    print(f"  {total_chars} total character appearances")
    print(f"  {len(all_events)} key events")

    # Build character profiles for downstream
    character_profiles = []
    for c in characters:
        character_profiles.append({
            "name": c["canonical_name"],
            "role": c.get("role", "minor"),
            "gender": c.get("gender", "unknown"),
            "personality_traits": [],
            "appearance_description": [c.get("appearance", ""), c.get("description", "")],
        })

    # Count mentions
    final_characters = []
    for c in characters:
        cn = c["canonical_name"]
        count = sum(1 for s in final_segments if cn in s.get("characters_in_scene", []))
        final_characters.append({
            "name": cn, "aliases": c.get("aliases", []),
            "role": c.get("role", "minor"), "gender": c.get("gender", "unknown"),
            "mention_count": count, "description": c.get("description", ""),
            "appearance": c.get("appearance", ""),
        })
    final_characters.sort(key=lambda x: x["mention_count"], reverse=True)

    # Save final analysis
    analysis = {
        "segments": final_segments,
        "characters": final_characters,
        "key_events": all_events,
        "character_profiles": character_profiles,
    }
    _save(preprocess_dir, "analysis", analysis)
    _save(preprocess_dir, "chapter_segments", chapter_segments_map)

    return final_segments, final_characters, all_events


def main():
    parser = argparse.ArgumentParser(description="Preprocess a book (6-layer pipeline).")
    parser.add_argument("--input", required=True, help="Path to book .txt file")
    parser.add_argument("--skip-sheets", action="store_true", help="Skip character sheet generation (layer 3)")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # Layer 1: Text extraction + chapter split
    title, book_id, full_text, chapters, preprocess_dir = _layer1_extract_text(
        input_path, None, None)

    # Layer 2: LLM character identification
    characters = _layer2_identify_characters(
        book_id, preprocess_dir, chapters, title)

    # Layer 3: Alias map building
    alias_map, gender_map = _layer3_build_aliases(
        book_id, preprocess_dir, characters)

    # Character sheets (between layer 3 and 4)
    _generate_character_sheets(book_id, preprocess_dir, characters, args.skip_sheets)

    # Layer 4: Alias replacement
    cleaned_chapters, cleaned_full_text = _layer4_replace_aliases(
        book_id, preprocess_dir, chapters, full_text, alias_map)

    # Layer 5: TextTiling segmentation
    all_segments, ch_seg_groups = _layer5_segment_text(
        book_id, preprocess_dir, cleaned_chapters, cleaned_full_text, chapters)

    # Layer 6: LLM annotation
    final_segments, final_characters, all_events = _layer6_annotate(
        book_id, preprocess_dir, chapters, characters, title, ch_seg_groups, args.skip_sheets)

    # Save to MongoDB
    from src.core.db import save_preprocess, is_available as mongo_available
    if mongo_available():
        save_preprocess(book_id, title, characters, final_segments, alias_map, gender_map)
        print(f"\n  MongoDB: saved ({len(characters)} characters, {len(final_segments)} segments)")
    else:
        print(f"\n  MongoDB: not available (data saved to files only)")

    # Summary
    print(f"\n{'='*50}")
    print(f"Preprocess complete: {title}")
    print(f"  Output: {preprocess_dir}")
    print(f"  Files:")
    for f in sorted(preprocess_dir.glob("*.json")):
        size = f.stat().st_size
        print(f"    {f.name} ({size:,} bytes)")
    print(f"\nNext: python scripts/generate_chapter.py --book {book_id} --chapter 0")


if __name__ == "__main__":
    main()
