"""MCP Server: exposes all pipeline layers as callable tools.

Each tool wraps a layer of the picture book pipeline so that the
Gemini Agent can invoke them via function calling. The MongoDB
tools integrate the MongoDB MCP server for data persistence.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import GENERATED_DIR, MONGODB_URI, MONGODB_DB

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool definitions (for Gemini function calling)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "extract_text",
        "description": (
            "Extract and structure text from a book source. "
            "Accepts a file path (.pdf, .epub, .txt) or raw text string. "
            "Returns the book title, detected chapters, and full text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "File path or raw text string to extract from.",
                },
            },
            "required": ["source"],
        },
    },
    {
        "name": "analyze_book",
        "description": (
            "Run full NLP analysis on extracted book text. "
            "Performs chapter segmentation (TextTiling), character extraction (spaCy NER + co-occurrence), "
            "sentiment curve analysis (peak/valley detection), visual concreteness scoring, "
            "text complexity assessment (Flesch-Kincaid), key event extraction, and character persona profiling. "
            "Returns structured analysis with segments, characters, sentiment, visual_scores, complexity, "
            "key_events, and character_profiles."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The full text to analyze.",
                },
                "chapters": {
                    "type": "string",
                    "description": "JSON string of chapter list [{title, text}], or empty string if none.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "select_scenes",
        "description": (
            "Select the best scenes from analyzed text to form a picture book. "
            "Uses the analysis data (segments, sentiment peaks, characters, visual scores) "
            "to choose scenes that cover the emotional arc, maintain causal chain, "
            "follow the story template, and prioritize visually drawable moments. "
            "Returns a list of scene dicts, one per page."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "analysis_json": {
                    "type": "string",
                    "description": "JSON string of the full analysis result from analyze_book.",
                },
                "num_pages": {
                    "type": "integer",
                    "description": "Number of pages for the picture book.",
                },
                "age_group": {
                    "type": "string",
                    "description": "Target age group: '2-4', '4-6', or '6-8'.",
                },
                "template": {
                    "type": "string",
                    "description": "Story template: 'classic', 'journey', or 'simple'.",
                },
            },
            "required": ["analysis_json", "num_pages", "age_group"],
        },
    },
    {
        "name": "simplify_text",
        "description": (
            "Rewrite scene text for children of the target age group. "
            "Simplifies vocabulary, shortens sentences, adds repetition and engagement. "
            "Validates output against age-appropriate constraints (word count, reading level). "
            "Returns scenes with added page_text, word_count, reading_level fields."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scenes_json": {
                    "type": "string",
                    "description": "JSON string of scenes list from select_scenes.",
                },
                "age_group": {
                    "type": "string",
                    "description": "Target age group.",
                },
                "original_text": {
                    "type": "string",
                    "description": "The original full text for reference.",
                },
            },
            "required": ["scenes_json", "age_group", "original_text"],
        },
    },
    {
        "name": "generate_illustration_prompts",
        "description": (
            "Generate detailed image generation prompts for character sheets and each page. "
            "Creates character reference sheet prompts (front view, expressions, distinctive features) "
            "and per-page illustration prompts (scene, characters, color palette, composition). "
            "All prompts use a consistent style prefix for visual coherence."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scenes_json": {
                    "type": "string",
                    "description": "JSON string of scenes with page_text.",
                },
                "character_profiles_json": {
                    "type": "string",
                    "description": "JSON string of character profiles from analyze_book.",
                },
                "style": {
                    "type": "string",
                    "description": "Optional style override. Default: watercolor children's book style.",
                },
            },
            "required": ["scenes_json", "character_profiles_json"],
        },
    },
    {
        "name": "generate_images",
        "description": (
            "Generate all illustrations for the picture book. "
            "First creates character reference sheets, then generates per-page illustrations "
            "referencing the character sheets for consistency. "
            "Runs CLIP-based consistency check on the results. "
            "Returns character_sheets, illustrations, and consistency scores."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompts_json": {
                    "type": "string",
                    "description": (
                        "JSON string with 'characters' (list of character sheet prompts) "
                        "and 'pages' (list of page illustration prompts)."
                    ),
                },
                "book_id": {
                    "type": "string",
                    "description": "Unique book identifier for file storage.",
                },
            },
            "required": ["prompts_json", "book_id"],
        },
    },
    {
        "name": "check_quality",
        "description": (
            "Run the full QA pipeline on the generated picture book. "
            "Checks: content safety (keyword + LLM), readability (Flesch-Kincaid vs age preset), "
            "story coverage (key events preserved), hallucination detection (new entities). "
            "Returns pass/fail with detailed per-check results. "
            "If quality check fails, the agent should consider re-generating problematic pages."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pages_json": {
                    "type": "string",
                    "description": "JSON string of page dicts with 'text' field.",
                },
                "analysis_json": {
                    "type": "string",
                    "description": "JSON string of original analysis (for key_events, segments).",
                },
                "original_text": {
                    "type": "string",
                    "description": "Original full text for hallucination comparison.",
                },
                "age_group": {
                    "type": "string",
                    "description": "Target age group.",
                },
            },
            "required": ["pages_json", "analysis_json", "original_text", "age_group"],
        },
    },
    {
        "name": "render_book",
        "description": (
            "Generate the final HTML picture book viewer with page-flip animation. "
            "Creates a standalone HTML file with cover, content pages (4 layout types), "
            "back cover, arrow-key/swipe navigation, and responsive design. "
            "Also generates a PDF version (8.5x8.5 inch square format)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pages_json": {
                    "type": "string",
                    "description": "JSON string of page dicts with 'text' and 'image_path'.",
                },
                "book_title": {
                    "type": "string",
                    "description": "Title for the book cover.",
                },
                "book_id": {
                    "type": "string",
                    "description": "Unique book identifier.",
                },
            },
            "required": ["pages_json", "book_title", "book_id"],
        },
    },
    # --- Character Sheet tool ---
    {
        "name": "generate_character_sheets",
        "description": (
            "Generate character reference sheets for main characters. "
            "Each sheet shows the character in front view, 3/4 view, side view, "
            "and 4 key expressions (happy, sad, surprised, determined). "
            "These sheets establish the visual identity of each character and "
            "MUST be generated BEFORE page illustrations for consistency. "
            "Uses character profiles from analyze_book."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "book_id": {
                    "type": "string",
                    "description": "Book identifier.",
                },
            },
            "required": [],
        },
    },
    # --- MongoDB tools ---
    {
        "name": "save_book_to_db",
        "description": (
            "Save the completed picture book to MongoDB. "
            "Stores the full book document including pages, config, and QA results."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "book_json": {
                    "type": "string",
                    "description": "JSON string of the full PictureBook document.",
                },
            },
            "required": ["book_json"],
        },
    },
    {
        "name": "get_book_from_db",
        "description": "Retrieve a picture book from MongoDB by its book_id.",
        "parameters": {
            "type": "object",
            "properties": {
                "book_id": {
                    "type": "string",
                    "description": "The book's unique identifier.",
                },
            },
            "required": ["book_id"],
        },
    },
    {
        "name": "list_books_from_db",
        "description": "List all picture books stored in MongoDB. Returns metadata only (id, title, date).",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _strip_book_metadata(text: str) -> str:
    """Remove front matter (TOC, dedication, epigraph, copyright) from book text.

    Finds the start of actual narrative content by looking for the first
    substantial paragraph (>50 words) after any preamble.
    """
    import re

    lines = text.split("\n")
    # Patterns that indicate metadata/front matter
    metadata_patterns = [
        re.compile(r'^\s*table of contents\s*$', re.IGNORECASE),
        re.compile(r'^\s*contents\s*$', re.IGNORECASE),
        re.compile(r'^\s*copyright\s', re.IGNORECASE),
        re.compile(r'^\s*all rights reserved', re.IGNORECASE),
        re.compile(r'^\s*published by\s', re.IGNORECASE),
        re.compile(r'^\s*dedication\s*$', re.IGNORECASE),
        re.compile(r'^\s*preface\s*$', re.IGNORECASE),
        re.compile(r'^\s*foreword\s*$', re.IGNORECASE),
        re.compile(r'^\s*ISBN\s', re.IGNORECASE),
        re.compile(r'^\s*(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\s*$'),  # Roman numeral TOC entries
    ]

    # Strategy: find the first substantial paragraph that looks like narrative prose.
    # Skip: title, author, TOC, dedication, epigraph, roman numerals
    content_start = 0
    found = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # Skip known metadata patterns
        if any(p.match(stripped) for p in metadata_patterns):
            continue

        # Skip short lines in first 80 lines (title, author, dedication, epigraph)
        if i < 80 and len(stripped) < 40:
            continue

        # Skip lines that look like poetry/epigraph (indented, short)
        if i < 80 and line.startswith('  ') and len(stripped) < 60:
            continue

        # Found first substantial narrative line
        if len(stripped) > 50:
            content_start = i
            found = True
            break

    # Also try to detect "Chapter 1" or standalone "I" as start marker
    if not found:
        for i, line in enumerate(lines[:100]):
            stripped = line.strip()
            if re.match(r'^(chapter\s+\d+|chapter\s+[ivxlc]+)\s*$', stripped, re.IGNORECASE):
                for j in range(i + 1, min(i + 5, len(lines))):
                    if lines[j].strip() and len(lines[j].strip()) > 30:
                        content_start = i
                        found = True
                        break
                if found:
                    break

    result = "\n".join(lines[content_start:])
    logger.info("Stripped metadata: kept from line %d/%d", content_start, len(lines))
    return result


def _safe_json_parse(s: Any, default: Any = None) -> Any:
    """Parse JSON string, or return as-is if already a dict/list."""
    if s is None:
        return default
    if isinstance(s, (dict, list)):
        return s
    if not isinstance(s, str) or not s.strip():
        return default
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return default


def execute_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool by name with the given arguments.

    Returns a dict with either {"result": ...} or {"error": ...}.
    """
    # Normalize args: Gemini may pass dicts/lists where we expect JSON strings
    normalized = {}
    for key, value in args.items():
        if key.endswith("_json") and isinstance(value, (dict, list)):
            normalized[key] = json.dumps(value, default=str)
        else:
            normalized[key] = value

    try:
        result = _dispatch(name, normalized)
        return {"result": result}
    except Exception as e:
        logger.exception("Tool %s failed", name)
        return {"error": f"{type(e).__name__}: {e}"}


def _dispatch(name: str, args: dict[str, Any]) -> Any:
    if name == "extract_text":
        return _tool_extract_text(args)
    elif name == "analyze_book":
        return _tool_analyze_book(args)
    elif name == "select_scenes":
        return _tool_select_scenes(args)
    elif name == "simplify_text":
        return _tool_simplify_text(args)
    elif name == "generate_illustration_prompts":
        return _tool_generate_illustration_prompts(args)
    elif name == "generate_character_sheets":
        return _tool_generate_character_sheets(args)
    elif name == "generate_images":
        return _tool_generate_images(args)
    elif name == "check_quality":
        return _tool_check_quality(args)
    elif name == "render_book":
        return _tool_render_book(args)
    elif name == "save_book_to_db":
        return _tool_save_book_to_db(args)
    elif name == "get_book_from_db":
        return _tool_get_book_from_db(args)
    elif name == "list_books_from_db":
        return _tool_list_books_from_db(args)
    else:
        raise ValueError(f"Unknown tool: {name}")


# ============================================================================
# Tool implementations — each tool reads from state store (previous step
# outputs) and saves its output back. Gemini only needs to say "call X",
# not shuttle large JSON blobs.
# ============================================================================

def _get_book_id(args: dict) -> str:
    return args.get("book_id", "default")


# --- Layer 1: Extraction ---
def _tool_extract_text(args: dict) -> dict:
    from src.extraction import extract_text
    from src.state_store import save

    source = args["source"]
    result = extract_text(source)
    book_id = args.get("book_id", "default")

    # Strip metadata (TOC, dedications, epigraphs, copyright)
    full_text = result.get("full_text", "")
    full_text = _strip_book_metadata(full_text)

    # Try to extract a better title from the text
    title = result.get("title", "Untitled")
    if (title == "Untitled" or title == "untitled") and full_text:
        # Find first non-empty line
        for line in full_text.split("\n"):
            line = line.strip()
            if line and 3 < len(line) < 120:
                title = line
                break

    # Sanitize title into a folder-safe book_id
    import re
    sanitized = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', title)
    sanitized = re.sub(r'\s+', '_', sanitized.strip())[:60]
    new_book_id = sanitized or book_id

    # Clean old folder, create new one (overwrite previous runs)
    if book_id != new_book_id:
        old_dir = GENERATED_DIR / book_id
        new_dir = GENERATED_DIR / new_book_id
        if new_dir.exists():
            import shutil
            shutil.rmtree(new_dir)
        if old_dir.exists():
            old_dir.rename(new_dir)
        from src.state_store import _stores
        if book_id in _stores:
            _stores[new_book_id] = _stores.pop(book_id)

    # Save to state store for downstream tools
    save(new_book_id, "full_text", full_text)
    save(new_book_id, "chapters", result.get("chapters", []))
    save(new_book_id, "title", title)
    save(new_book_id, "book_id", new_book_id)

    return {
        "title": title,
        "book_id": new_book_id,
        "num_chapters": len(result.get("chapters", [])),
        "full_text_length": len(result.get("full_text", "")),
        "full_text_preview": result.get("full_text", "")[:500],
    }


# --- Layer 2: Analysis ---
def _tool_analyze_book(args: dict) -> dict:
    from src.analysis import analyze_text
    from src.state_store import save, load

    book_id = args.get("book_id", "default")

    # ALWAYS prefer state store (full text), agent args may be truncated previews
    text = load(book_id, "full_text", "") or args.get("text", "")
    chapters = load(book_id, "chapters", None)
    if not chapters:
        chapters_raw = args.get("chapters", "")
        chapters = _safe_json_parse(chapters_raw, [])

    # Filter chapters if selected_chapters is specified
    selected_chapters_raw = args.get("selected_chapters")
    if selected_chapters_raw:
        if isinstance(selected_chapters_raw, str):
            selected_indices = [int(x.strip()) for x in selected_chapters_raw.split(",") if x.strip()]
        elif isinstance(selected_chapters_raw, list):
            selected_indices = [int(x) for x in selected_chapters_raw]
        else:
            selected_indices = None
        if selected_indices and chapters:
            chapters = [ch for i, ch in enumerate(chapters) if i in selected_indices]
            # Also filter text to only include selected chapter text
            text = "\n\n".join(ch.get("text", "") for ch in chapters)
            logger.info("Filtered to %d chapters (indices: %s)", len(chapters), selected_indices)

    result = analyze_text(text, chapters if chapters else None)

    # Save full analysis to state store
    save(book_id, "analysis", result)
    save(book_id, "character_profiles", result.get("character_profiles", []))

    return {
        "num_segments": len(result.get("segments", [])),
        "characters": [
            {"name": c["name"], "role": c["role"], "mentions": c["mention_count"]}
            for c in result.get("characters", [])[:10]
        ],
        "sentiment_arc": result.get("sentiment", {}).get("overall_arc", "unknown"),
        "sentiment_peaks": result.get("sentiment", {}).get("peaks", []),
        "complexity_grade": result.get("complexity", {}).get("flesch_kincaid_grade", 0),
        "num_key_events": len(result.get("key_events", [])),
        "top_events": [
            {"summary": e["summary"], "score": e["importance_score"]}
            for e in result.get("key_events", [])[:5]
        ],
        "num_character_profiles": len(result.get("character_profiles", [])),
    }


# --- Layer 3: Scene Selection ---
def _tool_select_scenes(args: dict) -> dict:
    from src.agent.story_arc_selector import select_story_arc
    from src.state_store import save, load

    book_id = args.get("book_id", "default")

    # Always read full analysis from state store (Gemini only passes summary)
    analysis = load(book_id, "analysis", None)
    if not analysis:
        analysis_raw = args.get("analysis_json", "")
        analysis = _safe_json_parse(analysis_raw, {})

    num_pages = int(args.get("num_pages", 10))
    age_group = args.get("age_group", "4-6")
    template = args.get("template", "classic")
    title = load(book_id, "title", "Untitled")

    # Use LLM-powered story arc selection for narrative coherence
    scenes = select_story_arc(analysis, num_pages, age_group, template, title)

    # Save to state store
    save(book_id, "scenes", scenes)

    return {
        "num_scenes": len(scenes),
        "method": "LLM story arc selection (narrative coherence)",
        "main_storyline": scenes[0].get("main_storyline", "") if scenes else "",
        "scenes_preview": [
            {
                "page": s.get("page_number"),
                "beat": s.get("template_beat", ""),
                "role": s.get("narrative_role", ""),
                "tone": s.get("emotional_tone", ""),
                "summary": s.get("scene_summary", "")[:100],
            }
            for s in scenes
        ],
    }


# --- Layer 3: Text Simplification ---
def _tool_simplify_text(args: dict) -> dict:
    from src.agent.text_simplifier import simplify_text
    from src.state_store import save, load

    book_id = args.get("book_id", "default")

    # Always read scenes from state store (select_scenes output), not from Gemini args
    scenes = load(book_id, "scenes", None)
    if not scenes:
        scenes_raw = args.get("scenes_json", "")
        scenes = _safe_json_parse(scenes_raw, [])
    original_text = args.get("original_text", "") or load(book_id, "full_text", "")
    age_group = args.get("age_group", "4-6")

    language = args.get("language", "en")

    # Pass character profiles for richer dialogue
    character_profiles = load(book_id, "character_profiles", [])
    logger.info("simplify_text: %d scenes, age_group=%s, language=%s, %d characters",
                len(scenes), age_group, language, len(character_profiles))

    result = simplify_text(scenes, age_group, original_text, language=language,
                          characters=character_profiles)

    # Save simplified scenes
    save(book_id, "simplified_scenes", result)

    return {
        "num_pages": len(result),
        "method": "rule-based NLP simplification (no LLM)",
        "pages_preview": [
            {
                "page": s.get("page_number"),
                "text": s.get("page_text", "")[:100],
                "words": s.get("word_count", 0),
                "level": s.get("reading_level", ""),
            }
            for s in result
        ],
    }


# --- Layer 3: Illustration Prompts ---
def _tool_generate_illustration_prompts(args: dict) -> dict:
    from src.agent.illustration_prompter import generate_illustration_prompts
    from src.state_store import save, load

    book_id = args.get("book_id", "default")

    # Read simplified scenes from state store
    scenes = load(book_id, "simplified_scenes", None)
    if not scenes:
        scenes_raw = args.get("scenes_json", "")
        scenes = _safe_json_parse(scenes_raw, [])
    # Use character sheets if available (they have richer visual descriptions)
    character_sheets = load(book_id, "character_sheets", [])
    if character_sheets:
        # Convert sheets to profile format with visual descriptions
        profiles = [
            {
                "name": s["character_name"],
                "appearance": s.get("appearance", []),
                "personality_traits": s.get("traits", []),
                "visual_description": s.get("description", ""),
            }
            for s in character_sheets
        ]
    else:
        profiles_raw = args.get("character_profiles_json", "")
        profiles = _safe_json_parse(profiles_raw, None) or load(book_id, "character_profiles", [])

    style = args.get("style")

    logger.info("illustration_prompts: %d scenes, %d profiles (from %s)",
                len(scenes), len(profiles),
                "character_sheets" if character_sheets else "analysis")

    result = generate_illustration_prompts(scenes, profiles, style)

    # Save to state store
    save(book_id, "illustration_prompts", result)

    return {
        "num_character_sheets": len(result.get("character_sheet_prompts", [])),
        "num_page_prompts": len(result.get("page_prompts", [])),
    }


# --- Character Sheet Generation ---
def _tool_generate_character_sheets(args: dict) -> dict:
    from src.generation.character_sheet import generate_character_sheets
    from src.state_store import save, load

    book_id = args.get("book_id", "default")

    # Read character profiles from state store (from analyze_book)
    profiles = load(book_id, "character_profiles", [])

    if not profiles:
        return {"error": "No character profiles found. Run analyze_book first."}

    logger.info("generate_character_sheets: %d profiles", len(profiles))

    sheets = generate_character_sheets(profiles, book_id)

    # Save to state store for downstream tools
    save(book_id, "character_sheets", sheets)

    return {
        "num_sheets": len(sheets),
        "characters": [
            {
                "name": s["character_name"],
                "has_image": bool(s["sheet_path"]),
                "appearance": s.get("appearance", []),
                "traits": s.get("traits", []),
            }
            for s in sheets
        ],
    }


# --- Layer 4: Image Generation ---
def _tool_generate_images(args: dict) -> dict:
    from src.generation.illustration import generate_illustrations
    from src.generation.consistency_check import check_consistency
    from src.state_store import save, load

    book_id = args.get("book_id", "default")

    # Read prompts from state store first (most reliable), then args as fallback
    prompts = load(book_id, "illustration_prompts", None)
    if not prompts:
        prompts_raw = args.get("prompts_json", "")
        prompts = _safe_json_parse(prompts_raw, {})
    if not prompts:
        prompts = {}

    # Normalize key names
    if isinstance(prompts, dict):
        if "page_prompts" in prompts and "pages" not in prompts:
            prompts["pages"] = prompts.pop("page_prompts")

    page_prompts = prompts.get("pages", [])

    # Use pre-generated character sheets from state store
    character_sheets = load(book_id, "character_sheets", [])

    # Merge illustration prompts with simplified scene data (text, scene_direction, key_characters)
    simplified = load(book_id, "simplified_scenes", [])
    if simplified and page_prompts:
        scene_map = {s.get("page_number", i + 1): s for i, s in enumerate(simplified)}
        for pp in page_prompts:
            pn = pp.get("page_number", 0)
            scene = scene_map.get(pn, {})
            if "text" not in pp:
                pp["text"] = scene.get("page_text", scene.get("text", ""))
            if "scene_direction" not in pp:
                pp["scene_direction"] = scene.get("scene_direction", "")
            if "key_characters" not in pp:
                pp["key_characters"] = scene.get("key_characters", [])

    logger.info("generate_images: %d pre-generated character sheets, %d page prompts",
                len(character_sheets), len(page_prompts))

    # Generate page illustrations directly (character sheets already done)
    illustrations = generate_illustrations(page_prompts, character_sheets, book_id)

    # Consistency check (best-effort)
    try:
        consistency = check_consistency(illustrations, character_sheets)
    except Exception as e:
        logger.warning("Consistency check failed: %s", e)
        consistency = {"overall_score": -1.0, "per_page_scores": [], "flagged_pages": []}

    result = {
        "character_sheets": character_sheets,
        "illustrations": illustrations,
        "consistency": consistency,
    }

    # Save to state store
    save(book_id, "image_result", result)

    return {
        "num_character_sheets": len(character_sheets),
        "num_illustrations": len(illustrations),
        "consistency_score": consistency.get("overall_score", -1),
        "flagged_pages": consistency.get("flagged_pages", []),
    }


# --- Layer 5: QA ---
def _tool_check_quality(args: dict) -> dict:
    from src.qa import run_qa_pipeline
    from src.state_store import load

    book_id = args.get("book_id", "default")

    # Build pages from state store
    simplified = load(book_id, "simplified_scenes", [])
    image_result = load(book_id, "image_result", {})
    illustrations = image_result.get("illustrations", [])

    pages = []
    for idx, scene in enumerate(simplified):
        ill = illustrations[idx] if idx < len(illustrations) else {}
        pages.append({
            "text": scene.get("page_text", scene.get("text", "")),
            "image_path": ill.get("image_path", ""),
        })

    # Fallback to args
    if not pages:
        pages = _safe_json_parse(args.get("pages_json", ""), [])

    analysis = _safe_json_parse(args.get("analysis_json", ""), None) or load(book_id, "analysis", {})
    original_text = args.get("original_text", "") or load(book_id, "full_text", "")
    age_group = args.get("age_group", "4-6")

    return run_qa_pipeline(pages, analysis, original_text, age_group)


# --- Layer 6: Render ---
def _tool_render_book(args: dict) -> dict:
    from src.renderer import generate_book_html, export_pdf
    from src.state_store import load

    book_id = args.get("book_id", "default")
    book_title = args.get("book_title", "") or load(book_id, "title", "Untitled")

    # Build pages from state store
    simplified = load(book_id, "simplified_scenes", [])
    image_result = load(book_id, "image_result", {})
    illustrations = image_result.get("illustrations", [])
    character_sheets = load(book_id, "character_sheets", [])

    pages = []
    for idx, scene in enumerate(simplified):
        ill = illustrations[idx] if idx < len(illustrations) else {}
        pages.append({
            "text": scene.get("page_text", scene.get("text", "")),
            "image_path": ill.get("image_path", ""),
            "template_beat": scene.get("template_beat", ""),
        })

    # Fallback to args
    if not pages:
        pages = _safe_json_parse(args.get("pages_json", ""), [])

    output_dir = GENERATED_DIR / book_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get cover image (first character sheet or first illustration as cover background)
    cover_image = ""
    if character_sheets:
        cover_image = character_sheets[0].get("sheet_path", "")

    html_content = generate_book_html(pages, book_title, book_id)
    html_path = output_dir / "book.html"
    html_path.write_text(html_content, encoding="utf-8")

    pdf_path = str(output_dir / "book.pdf")
    try:
        export_pdf(pages, book_title, pdf_path, cover_image=cover_image)
    except Exception as e:
        logger.warning("PDF export failed: %s", e)
        pdf_path = ""

    return {
        "html_path": str(html_path),
        "pdf_path": pdf_path,
        "num_pages": len(pages),
    }


# --- MongoDB tools ---
def _tool_save_book_to_db(args: dict) -> dict:
    from src.state_store import load

    book_id = args.get("book_id", "default")

    # Build book document from state store
    simplified = load(book_id, "simplified_scenes", [])
    image_result = load(book_id, "image_result", {})
    illustrations = image_result.get("illustrations", [])

    pages = []
    for idx, scene in enumerate(simplified):
        ill = illustrations[idx] if idx < len(illustrations) else {}
        pages.append({
            "page_number": idx + 1,
            "text": scene.get("page_text", scene.get("text", "")),
            "image_path": ill.get("image_path", ""),
        })

    book_doc = _safe_json_parse(args.get("book_json", ""), {})
    book_doc.update({
        "book_id": book_id,
        "title": load(book_id, "title", "Untitled"),
        "pages": pages,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    # Save to file (always works)
    output_dir = GENERATED_DIR / book_id
    output_dir.mkdir(parents=True, exist_ok=True)
    book_path = output_dir / "book.json"
    book_path.write_text(json.dumps(book_doc, indent=2, default=str, ensure_ascii=False), encoding="utf-8")

    # Save to MongoDB (best effort)
    try:
        import pymongo
        client = pymongo.MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3000)
        db = client[MONGODB_DB]
        db.books.update_one({"book_id": book_id}, {"$set": book_doc}, upsert=True)
        client.close()
        return {"saved": True, "book_id": book_id, "storage": "mongodb+file"}
    except Exception as e:
        logger.debug("MongoDB save skipped: %s", e)
        return {"saved": True, "book_id": book_id, "storage": "file_only", "file": str(book_path)}


def _tool_get_book_from_db(args: dict) -> dict:
    book_id = args["book_id"]
    # Try file first
    book_path = GENERATED_DIR / book_id / "book.json"
    if book_path.exists():
        return json.loads(book_path.read_text(encoding="utf-8"))
    # Try MongoDB
    try:
        import pymongo
        client = pymongo.MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3000)
        db = client[MONGODB_DB]
        book = db.books.find_one({"book_id": book_id}, {"_id": 0})
        client.close()
        if book:
            return book
    except Exception:
        pass
    return {"error": f"Book {book_id} not found"}


def _tool_list_books_from_db(args: dict) -> dict:
    books = []
    # Scan generated directory
    for d in sorted(GENERATED_DIR.iterdir()):
        book_json = d / "book.json"
        if book_json.exists():
            try:
                doc = json.loads(book_json.read_text(encoding="utf-8"))
                books.append({
                    "book_id": doc.get("book_id", d.name),
                    "title": doc.get("title", "Untitled"),
                    "created_at": doc.get("created_at", ""),
                })
            except Exception:
                continue
    return {"books": books, "count": len(books)}
