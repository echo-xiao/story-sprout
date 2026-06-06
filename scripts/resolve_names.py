#!/usr/bin/env python3
"""Resolve character names and coreferences in segments.

Two phases:
1. Coreference (algorithm) — alias replacement + pronoun resolution +
   characters_in_scene tagging (src/analysis/coreference.py)
2. LLM refinement — Gemini adds multi-word aliases, gender info,
   and filters non-characters

Usage:
    python scripts/resolve_names.py --book A_TALE_OF_TWO_CITIES
    python scripts/resolve_names.py --book A_TALE_OF_TWO_CITIES --algo-only
    python scripts/resolve_names.py --book A_TALE_OF_TWO_CITIES --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from src.config import GEMINI_API_KEY, GEMINI_MODEL, GENERATED_DIR
from src.analysis.coreference import (
    build_alias_map,
    infer_gender,
    pick_canonical_names,
    resolve_coreferences,
)


def _load_preprocess(book_id: str) -> dict:
    preprocess_dir = GENERATED_DIR / book_id / "preprocess"
    if not preprocess_dir.exists():
        print(f"Error: No preprocessed data at {preprocess_dir}")
        sys.exit(1)
    data = {}
    for name in ["meta", "analysis", "chapters", "chapter_segments", "full_text"]:
        path = preprocess_dir / f"{name}.json"
        if path.exists():
            data[name] = json.loads(path.read_text(encoding="utf-8"))
    return data


# ═══════════════════════════════════════════════════════════════
# LLM refinement
# ═══════════════════════════════════════════════════════════════

def _extract_sample_passages(data: dict, max_chars: int = 8000) -> str:
    """Extract representative text passages for Gemini context."""
    analysis = data.get("analysis", {})
    segments = analysis.get("segments", [])
    chars_lower = {
        c.get("name", "").lower()
        for c in analysis.get("characters", [])
        if c.get("mention_count", 0) >= 5
    }
    good = [s.get("text", "") for s in segments
            if any(n in s.get("text", "").lower() for n in chars_lower if len(n) >= 3)]
    if len(good) > 20:
        step = len(good) // 20
        good = good[::step][:20]
    return "\n---\n".join(good)[:max_chars]


def llm_refine(data: dict, alias_map: dict[str, str], renames: dict[str, str]) -> dict:
    """Use Gemini to review algorithm results: canonical names, aliases, genders."""
    from google import genai

    analysis = data.get("analysis", {})
    characters = analysis.get("characters", [])
    meta = data.get("meta", {})
    title = meta.get("title", "Unknown")

    char_lines = [
        f"- {c.get('name', '')} ({c.get('role', '?')}, {c.get('mention_count', 0)} mentions, aliases: {c.get('aliases', [])})"
        for c in characters if c.get("mention_count", 0) >= 5
    ]

    rename_lines = "\n".join(f"  {old} -> {new} (by frequency)" for old, new in renames.items())
    algo_aliases = "\n".join(f"  {a} -> {c}" for a, c in sorted(alias_map.items()))
    sample_text = _extract_sample_passages(data)

    prompt = f"""Review character name resolution for the novel "{title}".

An algorithm chose canonical names by text frequency and built alias mappings.
Please review and correct any mistakes.

Characters (canonical name chosen by frequency):
{chr(10).join(char_lines)}

Canonical name changes (algorithm renamed these by frequency):
{rename_lines or '  (none)'}

Alias mappings (algorithm-built):
{algo_aliases or '  (none)'}

Sample passages:
{sample_text}

Review and return:

1. **canonical_fixes**: If any canonical name is wrong, provide the correct one.
   The canonical name should be the most recognizable form used in the novel.
   Example: {{"current": "Thérèse Defarge", "correct": "Madame Defarge", "reason": "..."}}

2. **additional_aliases**: Multi-word (2+ words) aliases the algorithm missed.
   Example: {{"alias": "Doctor Manette", "canonical": "Dr. Manette"}}

3. **corrections**: Wrong alias mappings to fix or remove.
   Example: {{"wrong_alias": "...", "correct_target": "... or null", "reason": "..."}}

4. **non_characters**: Names that are NOT real people in the story.

5. **character_genders**: Gender for each character.
   Example: {{"name": "Lucie Manette", "gender": "female"}}

Return JSON:
{{
  "canonical_fixes": [{{"current": "...", "correct": "...", "reason": "..."}}],
  "additional_aliases": [{{"alias": "...", "canonical": "..."}}],
  "corrections": [{{"wrong_alias": "...", "correct_target": "... or null", "reason": "..."}}],
  "non_characters": ["name1"],
  "character_genders": [{{"name": "...", "gender": "male/female/unknown"}}]
}}"""

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=genai.types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )
    return json.loads(response.text)


def merge_llm_results(
    characters: list[dict],
    profiles: list[dict],
    alias_map: dict[str, str],
    gender_map: dict[str, str],
    llm_result: dict,
) -> list[str]:
    """Merge LLM results into characters, alias map, and gender map. Returns log."""
    log = []

    # Canonical name fixes
    for fix in llm_result.get("canonical_fixes", []):
        current = fix.get("current", "")
        correct = fix.get("correct", "")
        reason = fix.get("reason", "")
        if not current or not correct or current == correct:
            continue
        # Rename in character list
        for c in characters:
            if c.get("name") == current:
                old_aliases = c.get("aliases", [])
                c["name"] = correct
                if current not in old_aliases:
                    old_aliases.append(current)
                c["aliases"] = [a for a in old_aliases if a != correct]
                log.append(f"  canonical fix: '{current}' -> '{correct}' ({reason})")
                break
        # Rename in profiles
        for p in profiles:
            if p.get("name") == current:
                p["name"] = correct
        # Update alias map: any alias pointing to old name should point to new
        for k, v in list(alias_map.items()):
            if v == current:
                alias_map[k] = correct
        # Old canonical becomes an alias
        alias_map[current.lower()] = correct

    # Corrections
    for fix in llm_result.get("corrections", []):
        wrong = fix.get("wrong_alias", "").lower()
        correct = fix.get("correct_target")
        reason = fix.get("reason", "")
        if wrong in alias_map:
            if correct:
                alias_map[wrong] = correct
                log.append(f"  corrected: '{wrong}' -> '{correct}' ({reason})")
            else:
                del alias_map[wrong]
                log.append(f"  removed: '{wrong}' ({reason})")

    # Additional aliases
    for item in llm_result.get("additional_aliases", []):
        alias = item.get("alias", "").lower().strip()
        canonical = item.get("canonical", "")
        if not alias or not canonical or len(alias.split()) < 2:
            continue
        if alias in alias_map and alias_map[alias] != canonical:
            log.append(f"  conflict: '{alias}' (kept original)")
        else:
            alias_map[alias] = canonical
            log.append(f"  added: '{alias}' -> '{canonical}'")

    # Genders
    for item in llm_result.get("character_genders", []):
        name = item.get("name", "")
        gender = item.get("gender", "unknown")
        if name and gender in ("male", "female"):
            gender_map[name] = gender
            log.append(f"  gender: {name} = {gender}")

    return log


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Resolve names + coreferences.")
    parser.add_argument("--book", required=True, help="Book ID")
    parser.add_argument("--algo-only", action="store_true", help="Skip LLM phase")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()

    book_id = args.book
    data = _load_preprocess(book_id)
    preprocess_dir = GENERATED_DIR / book_id / "preprocess"
    analysis = data.get("analysis", {})
    characters = analysis.get("characters", [])
    profiles = analysis.get("character_profiles", [])
    segments = analysis.get("segments", [])

    full_text = data.get("full_text", {}).get("text", "")
    title = data.get("meta", {}).get("title", book_id)
    print(f"=== Name Resolution: {title} ===")
    print(f"  {len(characters)} characters, {len(segments)} segments\n")

    # ── Step 1: Pick canonical names by frequency ──
    print("[1] Picking canonical names by text frequency...")
    renames = pick_canonical_names(characters, full_text)
    if renames:
        for old, new in renames.items():
            print(f"    {old} -> {new}")
    else:
        print("    (no changes)")
    # Also update profiles to match, and deduplicate
    for p in profiles:
        name = p.get("name", "")
        if name in renames:
            p["name"] = renames[name]
    # Deduplicate profiles (keep first occurrence, merge appearance_description)
    seen_profiles: dict[str, dict] = {}
    profiles_to_remove = []
    for p in profiles:
        name = p.get("name", "")
        if name in seen_profiles:
            # Merge appearance descriptions
            target = seen_profiles[name]
            for desc in p.get("appearance_description", []):
                if desc not in target.get("appearance_description", []):
                    target.setdefault("appearance_description", []).append(desc)
            profiles_to_remove.append(p)
        else:
            seen_profiles[name] = p
    for p in profiles_to_remove:
        profiles.remove(p)

    # ── Step 2: Build alias map ──
    print(f"\n[2] Building multi-word alias map...")
    alias_map = build_alias_map(characters)
    print(f"  {len(alias_map)} aliases:")
    for a, c in sorted(alias_map.items()):
        print(f"    {a} -> {c}")

    # ── Step 3: LLM review ──
    gender_map: dict[str, str] = {}
    non_characters: list[str] = []

    if not args.algo_only:
        print(f"\n[3] LLM review (Gemini)...")
        t0 = time.time()
        llm_result = llm_refine(data, alias_map, renames)
        print(f"  Done in {time.time() - t0:.1f}s")

        llm_path = preprocess_dir / "name_resolution_llm.json"
        llm_path.write_text(json.dumps(llm_result, indent=2, ensure_ascii=False), encoding="utf-8")

        non_characters = llm_result.get("non_characters", [])
        llm_log = merge_llm_results(characters, profiles, alias_map, gender_map, llm_result)
        for msg in llm_log:
            print(f"  {msg}")

    print(f"\n  Final alias map ({len(alias_map)} entries):")
    for a, c in sorted(alias_map.items()):
        print(f"    {a} -> {c}")

    # Print final character list
    print(f"\n  Final characters:")
    for c in characters:
        if c.get("mention_count", 0) >= 5:
            print(f"    {c['name']} ({c.get('role','?')}, {c.get('mention_count',0)})")

    if args.dry_run:
        print("\n[DRY RUN] Not applying.")
        return

    # ── Remove non-characters ──
    if non_characters:
        non_lower = {n.lower() for n in non_characters}
        characters[:] = [c for c in characters if c.get("name", "").lower() not in non_lower]
        profiles[:] = [p for p in profiles if p.get("name", "").lower() not in non_lower]
        print(f"\n  Removed {len(non_characters)} non-characters")

    # ── Coreference resolution (alias replace + pronoun resolve + tag) ──
    print(f"\n[3] Coreference resolution...")
    t0 = time.time()
    resolve_coreferences(
        segments, characters, profiles,
        alias_map=alias_map, gender_map=gender_map,
    )
    dt = time.time() - t0

    segs_with_chars = sum(1 for s in segments if s.get("characters_in_scene"))
    total_chars = sum(len(s.get("characters_in_scene", [])) for s in segments)
    print(f"  Done in {dt:.1f}s")
    print(f"  {segs_with_chars}/{len(segments)} segments have characters")
    print(f"  {total_chars} total character appearances")

    # Sample
    print(f"\n  Sample:")
    shown = 0
    for s in segments:
        chars = s.get("characters_in_scene", [])
        if chars and shown < 5:
            print(f"    [{s.get('id','?')}] {chars} — \"{s.get('text','')[:80]}...\"")
            shown += 1

    # ── Save ──
    preprocess_dir / "alias_map.json" and (preprocess_dir / "alias_map.json").write_text(
        json.dumps(alias_map, indent=2, ensure_ascii=False), encoding="utf-8")
    (preprocess_dir / "analysis.json").write_text(
        json.dumps(analysis, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    (preprocess_dir / "character_genders.json").write_text(
        json.dumps(gender_map, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n  Saved: alias_map.json, analysis.json, character_genders.json")
    print(f"\n=== Done ===")
    print(f"Next: python scripts/generate_chapter.py --book {book_id} --chapter 0")


if __name__ == "__main__":
    main()
