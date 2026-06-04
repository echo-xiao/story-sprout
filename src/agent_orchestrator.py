"""Gemini Agent Orchestrator: uses function calling to drive the picture book pipeline.

Instead of a hardcoded sequential pipeline, the Gemini Agent decides which
tools to call, in what order, and how to handle failures (e.g., re-generate
if QA fails). This is the core "agent" that the hackathon judges evaluate.
"""

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

from google import genai

from src.config import GEMINI_API_KEY, GEMINI_MODEL
from src.mcp_server import TOOL_DEFINITIONS, execute_tool
from src.models import GenerationConfig, GenerationStatus, PageData, PictureBook, StatusEnum

logger = logging.getLogger(__name__)

# Max turns to prevent infinite loops
MAX_AGENT_TURNS = 30

# Status callback type
StatusCallback = Optional[Callable[[GenerationStatus], Coroutine[Any, Any, None]]]

SYSTEM_INSTRUCTION = """\
You are a Picture Book Generator Agent. Your job is to transform adult literature \
into beautiful children's picture books by orchestrating a series of specialized tools.

You have access to these tools:
1. extract_text — Extract text from a book file or raw text
2. analyze_book — Run NLP analysis (characters, sentiment, key events, complexity)
3. select_scenes — Choose the best scenes for the picture book
4. simplify_text — Rewrite text for the target age group
5. generate_illustration_prompts — Create detailed image prompts
6. generate_images — Generate character sheets and page illustrations
7. check_quality — Run QA checks (safety, readability, coverage, hallucination)
8. render_book — Generate the final HTML viewer and PDF
9. save_book_to_db — Save the completed book to MongoDB
10. get_book_from_db — Retrieve a book from MongoDB
11. list_books_from_db — List all saved books

## Your workflow — you MUST complete ALL steps in this exact order:
1. extract_text — Extract the text from the source
2. analyze_book — Analyze structure, characters, sentiment, key events
3. select_scenes — Select the best scenes for the picture book
4. simplify_text — Rewrite text for the target age group
5. generate_character_sheets — Generate character reference sheets (front/side/expressions) for EACH main character. This MUST happen BEFORE page illustrations.
6. generate_illustration_prompts — Create detailed image prompts for each page
7. generate_images — Generate page illustrations (referencing the character sheets)
8. check_quality — Run QA checks (safety, readability, coverage, hallucination)
9. render_book — Generate the final HTML viewer and PDF
10. save_book_to_db — Save the completed book to MongoDB

## CRITICAL rules:
- You MUST call ALL 9 steps above. Do NOT skip check_quality or render_book.
- Pass data between tools as JSON strings.
- If a tool fails, retry ONCE. If it fails again, move on to the next step.
- If check_quality reports issues, you may retry simplify_text once, then continue.
- Do NOT call the same tool more than 3 times total.
- After save_book_to_db, output a text summary of what was created.

## Current task:
Generate a picture book with the following configuration:
{config_description}

The source material is provided. Begin by extracting and analyzing it.
"""


def _build_gemini_tools() -> list[dict]:
    """Convert our tool definitions to Gemini function declaration format."""
    declarations = []
    for tool in TOOL_DEFINITIONS:
        declarations.append({
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["parameters"],
        })
    return declarations


def _make_config_description(config: GenerationConfig) -> str:
    lang_map = {"zh": "Chinese (简体中文)", "en": "English", "ja": "Japanese", "ko": "Korean"}
    lang_name = lang_map.get(config.language, config.language)
    parts = [
        f"- Age group: {config.age_group}",
        f"- Number of pages: {config.num_pages}",
        f"- Story template: {config.template}",
        f"- Output language: {lang_name}",
    ]
    if config.language != "en":
        parts.append(f"  IMPORTANT: ALL story text in the picture book MUST be written in {lang_name}. "
                     f"Scene summaries, page text, and character names should all be in {lang_name}.")
    if config.style:
        parts.append(f"- Illustration style: {config.style}")
    if config.education_goal:
        parts.append(f"- Education goal: {config.education_goal}")
    if config.selected_chapters:
        parts.append(f"- Selected chapters: {config.selected_chapters}")
    return "\n".join(parts)


def _infer_status(tool_name: str) -> tuple[StatusEnum, int, str]:
    """Infer the pipeline status from the tool being called."""
    mapping = {
        "extract_text": (StatusEnum.ANALYZING, 10, "Extracting text from source"),
        "analyze_book": (StatusEnum.ANALYZING, 20, "Analyzing story structure and characters"),
        "select_scenes": (StatusEnum.GENERATING_TEXT, 35, "Selecting best scenes for picture book"),
        "simplify_text": (StatusEnum.GENERATING_TEXT, 45, "Simplifying text for children"),
        "generate_character_sheets": (StatusEnum.GENERATING_IMAGES, 50, "Generating character reference sheets"),
        "generate_illustration_prompts": (StatusEnum.GENERATING_TEXT, 55, "Creating illustration prompts"),
        "generate_images": (StatusEnum.GENERATING_IMAGES, 65, "Generating page illustrations"),
        "check_quality": (StatusEnum.QA_CHECK, 80, "Running quality checks"),
        "render_book": (StatusEnum.COMPLETE, 90, "Rendering final book"),
        "save_book_to_db": (StatusEnum.COMPLETE, 95, "Saving to database"),
    }
    return mapping.get(tool_name, (StatusEnum.ANALYZING, 50, f"Running {tool_name}"))


async def run_agent(
    source: str,
    config: GenerationConfig,
    book_id: str | None = None,
    status_callback: StatusCallback = None,
) -> PictureBook:
    """Run the Gemini Agent to generate a picture book.

    The agent uses function calling to orchestrate the pipeline tools,
    making its own decisions about order, retries, and error handling.

    Args:
        source: Raw text or file path to the source material.
        config: Generation configuration.
        book_id: Optional pre-assigned book ID.
        status_callback: Optional async callback for status updates.

    Returns:
        The completed PictureBook.
    """
    if book_id is None:
        # Use a sanitized version of the source text as folder name
        # Will be replaced with actual book title after extract_text
        book_id = "latest_book"

    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not set")

    client = genai.Client(api_key=GEMINI_API_KEY)

    # Build system instruction with config
    config_desc = _make_config_description(config)
    system_inst = SYSTEM_INSTRUCTION.format(config_description=config_desc)

    # Build tools
    gemini_tools = _build_gemini_tools()

    # Initial message: keep it concise, tell agent to use tools
    source_preview = source[:500] + "..." if len(source) > 500 else source
    initial_message = (
        f"Generate a {config.num_pages}-page picture book for ages {config.age_group}.\n\n"
        f"Book ID: {book_id}\n"
        f"Source text preview (first 500 chars):\n{source_preview}\n\n"
        f"Total source length: {len(source)} characters.\n\n"
        f"IMPORTANT: You MUST call extract_text now to begin processing. "
        f"Pass the source text to it. Then follow the full pipeline: "
        f"analyze_book -> select_scenes -> simplify_text -> "
        f"generate_character_sheets -> generate_illustration_prompts -> "
        f"generate_images -> check_quality -> render_book -> save_book_to_db."
    )

    # Track state for building the final PictureBook
    agent_state: dict[str, Any] = {
        "book_id": book_id,
        "title": "Untitled",
        "pages": [],
        "qa_results": {},
        "config": config,
    }

    # Status tracking
    status = GenerationStatus(
        book_id=book_id,
        status=StatusEnum.QUEUED,
        progress=0,
        current_step="Starting agent",
    )

    # Conversation history
    contents: list[dict] = [{"role": "user", "parts": [{"text": initial_message}]}]

    # Track consecutive failures to prevent infinite retry loops
    _last_tool: str = ""
    _consecutive_same: int = 0

    for turn in range(MAX_AGENT_TURNS):
        logger.info("Agent turn %d/%d", turn + 1, MAX_AGENT_TURNS)

        try:
            # Force function calling until key steps are done,
            # then switch to AUTO so the agent can finish with a summary.
            done_enough = (
                agent_state.get("_rendered", False)
                or agent_state.get("_save_attempted", False)
                or turn >= MAX_AGENT_TURNS - 3
            )
            fc_mode = "AUTO" if done_enough else "ANY"

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_inst,
                    tools=[{"function_declarations": gemini_tools}],
                    tool_config={"function_calling_config": {"mode": fc_mode}},
                ),
            )
        except Exception as e:
            logger.error("Gemini API error on turn %d: %s", turn + 1, e)
            raise

        if not response.candidates:
            logger.warning("No candidates in response on turn %d", turn + 1)
            break

        candidate = response.candidates[0]
        response_parts = (candidate.content.parts if candidate.content and candidate.content.parts else []) or []

        # Check for function calls
        function_calls = [p for p in response_parts if p.function_call]
        text_parts = [p for p in response_parts if p.text]

        if not function_calls:
            # Agent is done — it returned a text response
            final_text = " ".join(p.text for p in text_parts)
            logger.info("Agent finished: %s", final_text[:200])
            break

        # Add the model's response to history
        contents.append({"role": "model", "parts": response_parts})

        # Execute each function call
        function_responses = []
        for fc in function_calls:
            tool_name = fc.function_call.name
            tool_args = dict(fc.function_call.args) if fc.function_call.args else {}

            # Always inject book_id, language, and source text
            tool_args["book_id"] = book_id
            tool_args["language"] = config.language
            if tool_name == "extract_text":
                tool_args["source"] = source
            if tool_name == "analyze_book" and config.selected_chapters:
                tool_args["selected_chapters"] = config.selected_chapters

            # Detect infinite retry loops
            if tool_name == _last_tool:
                _consecutive_same += 1
            else:
                _consecutive_same = 0
                _last_tool = tool_name

            if _consecutive_same >= 3:
                logger.warning("Tool %s called %d times consecutively, skipping to next step", tool_name, _consecutive_same)
                agent_state["_rendered"] = True  # Force exit from ANY mode
                function_responses.append(
                    genai.types.Part.from_function_response(
                        name=tool_name,
                        response={"result": json.dumps({"skipped": True, "reason": "Max retries exceeded. Move on to the next step."})},
                    )
                )
                continue

            logger.info("Calling tool: %s (args keys: %s)", tool_name, list(tool_args.keys()))

            # Update status
            new_status, progress, step_desc = _infer_status(tool_name)
            status.status = new_status
            status.progress = progress
            status.current_step = step_desc
            if status_callback:
                await status_callback(status)

            # Execute and log
            t0 = time.time()
            result = execute_tool(tool_name, tool_args)
            duration_ms = int((time.time() - t0) * 1000)

            # Persist step to files + MongoDB
            from src.step_logger import log_step
            log_step(
                book_id=book_id,
                tool_name=tool_name,
                tool_input=tool_args,
                tool_output=result,
                duration_ms=duration_ms,
            )

            # Track state from results
            _update_agent_state(agent_state, tool_name, tool_args, result)

            # Update book_id if it changed (e.g., after extract_text)
            if agent_state.get("book_id") and agent_state["book_id"] != book_id:
                book_id = agent_state["book_id"]
                logger.info("Book ID updated to: %s", book_id)

            # Serialize result for Gemini (truncate if too large)
            result_str = json.dumps(result, default=str)
            if len(result_str) > 30000:
                # Truncate but keep structure
                result_summary = _summarize_result(result)
                result_str = json.dumps(result_summary, default=str)

            function_responses.append(
                genai.types.Part.from_function_response(
                    name=tool_name,
                    response={"result": result_str},
                )
            )

        # Add function responses to history
        contents.append({"role": "user", "parts": function_responses})

    # Build final PictureBook from state store
    from src.state_store import load as _load
    simplified = _load(book_id, "simplified_scenes", [])
    image_result = _load(book_id, "image_result", {})
    illustrations = image_result.get("illustrations", [])

    final_pages = []
    for idx, scene in enumerate(simplified):
        ill = illustrations[idx] if idx < len(illustrations) else {}
        final_pages.append(PageData(
            page_number=idx + 1,
            text=scene.get("page_text", scene.get("text", "")),
            illustration_path=ill.get("image_path"),
            illustration_prompt=ill.get("prompt_used", ""),
            layout=scene.get("layout", "full"),
        ))

    title = _load(book_id, "title", agent_state.get("title", "Untitled"))

    book = PictureBook(
        book_id=book_id,
        title=title,
        pages=final_pages,
        created_at=datetime.now(timezone.utc),
        config=config,
        qa_results=agent_state.get("qa_results", {}),
    )

    # Final status
    status.status = StatusEnum.COMPLETE
    status.progress = 100
    status.current_step = "Complete"
    if status_callback:
        await status_callback(status)

    logger.info(
        "Agent completed: book_id=%s, title=%s, pages=%d",
        book.book_id, book.title, len(book.pages),
    )

    return book


def _update_agent_state(
    state: dict, tool_name: str, args: dict, result: dict
) -> None:
    """Track important outputs from tool calls to build the final book."""
    data = result.get("result", result)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return

    if tool_name == "extract_text":
        title = data.get("title", "Untitled")
        if title and title != "Untitled":
            state["title"] = title
        # Update book_id if extract_text returned a new one (based on title)
        new_id = data.get("book_id")
        if new_id:
            state["book_id"] = new_id
        state["_extracted"] = True

    elif tool_name == "analyze_book":
        state["_analyzed"] = True

    elif tool_name == "simplify_text":
        scenes = data.get("scenes", [])
        if scenes:
            state["_scenes"] = scenes
        state["_simplified"] = True

    elif tool_name == "generate_character_sheets":
        state["_sheets_done"] = True

    elif tool_name == "generate_illustration_prompts":
        state["_prompts_done"] = True

    elif tool_name == "generate_images":
        illustrations = data.get("illustrations", [])
        if illustrations:
            state["_illustrations"] = illustrations
        state["_images_done"] = True

    elif tool_name == "check_quality":
        state["qa_results"] = data

    elif tool_name == "render_book":
        state["_rendered"] = True
        # Build pages from accumulated state
        scenes = state.get("_scenes", [])
        illustrations = state.get("_illustrations", [])
        pages = []
        for idx, scene in enumerate(scenes):
            ill = illustrations[idx] if idx < len(illustrations) else {}
            pages.append({
                "page_number": idx + 1,
                "text": scene.get("page_text", scene.get("text", "")),
                "illustration_path": ill.get("image_path"),
                "illustration_prompt": ill.get("prompt_used", ""),
                "layout": scene.get("layout", "full"),
            })
        state["pages"] = pages

    elif tool_name == "save_book_to_db":
        state["_save_attempted"] = True


def _summarize_result(result: dict) -> dict:
    """Create a condensed version of a large result for the conversation."""
    summary = {}
    for key, value in result.items():
        if key.startswith("_"):
            continue
        if isinstance(value, str) and len(value) > 2000:
            summary[key] = value[:2000] + "... (truncated)"
        elif isinstance(value, list) and len(value) > 10:
            summary[key] = value[:10]
            summary[f"{key}_count"] = len(value)
        elif isinstance(value, dict) and len(json.dumps(value, default=str)) > 5000:
            summary[key] = {k: v for k, v in list(value.items())[:10]}
            summary[f"{key}_note"] = "truncated"
        else:
            summary[key] = value
    return summary
