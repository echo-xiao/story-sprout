"""The production book-generation pipeline, expressed with Google's Agent
Development Kit (ADK / Agent Builder).

The four real agents — Analyzer, Writer, Artist, QA — are wrapped as ADK
``BaseAgent`` stages and run in fixed order by an ADK ``SequentialAgent``,
in-process (the Artist stage generates real illustrations on Gemini, so it stays
on Cloud Run rather than a lightweight managed runtime). The agents do the same
heavy work as before; only the orchestration now runs through ADK.

Data flows between stages via a shared ``PipelineContext`` object (ADK session
state is not used for the heavy Python objects).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent, SequentialAgent
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from src.config import GENERATED_DIR

logger = logging.getLogger(__name__)


def _update_progress(book_id: str, chapter_idx: int, **kwargs) -> None:
    """Write progress.json for frontend polling."""
    progress_file = GENERATED_DIR / book_id / "chapters" / f"ch{chapter_idx:02d}" / "progress.json"
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if progress_file.exists():
        try:
            existing = json.loads(progress_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    existing.update(kwargs)
    progress_file.write_text(json.dumps(existing))


class PipelineContext:
    """Mutable state shared across the ADK pipeline stages."""

    def __init__(self, book_id, data, chapter_idx, page_filter, age_group, self_correct):
        self.book_id = book_id
        self.data = data
        self.chapter_idx = chapter_idx
        self.page_filter = page_filter
        self.age_group = age_group
        self.self_correct = self_correct

        analysis = data.get("analysis", {})
        self.characters = analysis.get("characters", [])
        self.profiles = analysis.get("character_profiles", [])
        self.title = data.get("meta", {}).get("title", "Untitled")
        self.chapter_dir = GENERATED_DIR / book_id / "chapters" / f"ch{chapter_idx:02d}"

        # Lazily-instantiated agents (created in the Analyzer stage).
        self.analyzer = None
        self.artist = None
        # Intermediate results.
        self.segments: list = []
        self.ch_title: str = ""
        self.scenes: list = []
        self.chapter_profiles: list = []
        self.character_sheets: list = []
        self.simplified: list = []
        self.page_prompts: list = []
        self.illustrations: list = []
        self.chapter_data: dict | None = None
        self.aborted = False  # set when there's nothing to generate


class _Stage(BaseAgent):
    """Base for a pipeline stage holding the shared PipelineContext."""

    ctx: PipelineContext
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, name: str, ctx: PipelineContext):
        super().__init__(name=name, ctx=ctx)


class AnalyzerStage(_Stage):
    """Load chapter segments, build scenes, and find the chapter's characters."""

    async def _run_async_impl(self, _) -> AsyncGenerator[Event, None]:
        from src.agents.analyzer import AnalyzerAgent
        from src.agents.artist import ArtistAgent
        from src.agents.agent_log import log_event, clear_log

        c = self.ctx
        clear_log(c.book_id, c.chapter_idx)

        _update_progress(c.book_id, c.chapter_idx, status="generating", agent="analyzer",
                         current_step="Analyzing chapter structure...", progress=5)
        log_event(c.book_id, c.chapter_idx, "analyzer", "load_chapter", f"Loading chapter {c.chapter_idx} data")
        c.analyzer = AnalyzerAgent(c.book_id)
        c.artist = ArtistAgent(c.book_id)
        c.segments, c.ch_title = c.analyzer.get_chapter_segments(c.data, c.chapter_idx)
        log_event(c.book_id, c.chapter_idx, "analyzer", "load_chapter", f"Chapter: {c.ch_title}",
                  result=f"{len(c.segments)} segments found", status="done")
        print(f"\n=== Generating Chapter {c.chapter_idx}: {c.ch_title} ===")
        print(f"  Segments: {len(c.segments)}")
        c.chapter_dir.mkdir(parents=True, exist_ok=True)

        _update_progress(c.book_id, c.chapter_idx, agent="analyzer", current_step="Building scenes...", progress=15)
        log_event(c.book_id, c.chapter_idx, "analyzer", "build_scenes", "Converting segments to scenes")
        c.scenes = c.analyzer.build_scenes(c.segments, c.characters)
        log_event(c.book_id, c.chapter_idx, "analyzer", "build_scenes", f"{len(c.scenes)} pages to generate", status="done")
        print(f"  Pages to generate: {len(c.scenes)}")

        if c.page_filter:
            c.scenes = [s for s in c.scenes if s["page_number"] in c.page_filter]
            print(f"  Filtered to pages: {c.page_filter}")

        if not c.scenes:
            log_event(c.book_id, c.chapter_idx, "analyzer", "build_scenes", "No pages to generate", status="warn")
            print("  No pages to generate.")
            c.aborted = True
            yield Event(author=self.name)
            return

        _, c.chapter_profiles = c.analyzer.get_chapter_characters(c.data, c.segments)
        char_names = [p.get("name", "?") for p in c.chapter_profiles]
        log_event(c.book_id, c.chapter_idx, "analyzer", "find_characters",
                  f"Found {len(c.chapter_profiles)} characters", result=", ".join(char_names[:8]), status="done")
        print(f"\n[Analyzer Agent] {len(c.chapter_profiles)} characters in this chapter")
        yield Event(author=self.name)


class ArtistSetupStage(_Stage):
    """Generate special pages (cover etc.) and per-character reference sheets."""

    async def _run_async_impl(self, _) -> AsyncGenerator[Event, None]:
        from src.agents.agent_log import log_event

        c = self.ctx
        if c.aborted:
            yield Event(author=self.name)
            return

        _update_progress(c.book_id, c.chapter_idx, agent="artist", current_step="Generating special pages...", progress=10)
        log_event(c.book_id, c.chapter_idx, "artist", "special_pages", "Generating cover & special pages")
        if not c.page_filter:
            c.artist.ensure_special_pages(c.data, c.chapter_idx, c.segments)
        log_event(c.book_id, c.chapter_idx, "artist", "special_pages", "Special pages ready", status="done")

        _update_progress(c.book_id, c.chapter_idx, agent="artist", current_step="Generating character sheets...", progress=20)
        log_event(c.book_id, c.chapter_idx, "artist", "character_sheets",
                  f"Generating sheets for {len(c.chapter_profiles)} characters")
        c.character_sheets = c.artist.generate_character_sheets(c.chapter_profiles)
        cached = len([s for s in c.character_sheets if s.get("_cached")])
        log_event(c.book_id, c.chapter_idx, "artist", "character_sheets",
                  f"{len(c.character_sheets)} sheets ready ({cached} cached)", status="done")
        yield Event(author=self.name)


class WriterStage(_Stage):
    """Simplify each scene into child-friendly text and build illustration prompts."""

    async def _run_async_impl(self, _) -> AsyncGenerator[Event, None]:
        from src.agents.writer import WriterAgent
        from src.agents.agent_log import log_event

        c = self.ctx
        if c.aborted:
            yield Event(author=self.name)
            return

        _update_progress(c.book_id, c.chapter_idx, agent="writer", current_step="Simplifying text for kids...", progress=30)
        log_event(c.book_id, c.chapter_idx, "writer", "simplify_text",
                  f"Simplifying {len(c.scenes)} scenes for age {c.age_group}")
        writer = WriterAgent(age_group=c.age_group)
        chapter_char_names = {s["character_name"] for s in c.character_sheets}
        chapter_chars = [p for p in c.profiles if p.get("name") in chapter_char_names]
        c.simplified = writer.simplify(c.scenes, characters=chapter_chars, character_sheets=c.character_sheets)
        log_event(c.book_id, c.chapter_idx, "writer", "simplify_text", f"Simplified {len(c.simplified)} pages", status="done")

        _update_progress(c.book_id, c.chapter_idx, agent="writer", current_step="Building illustration prompts...", progress=35)
        log_event(c.book_id, c.chapter_idx, "writer", "build_prompts", f"Building {len(c.simplified)} illustration prompts")
        c.page_prompts = writer.build_prompts(c.simplified)
        log_event(c.book_id, c.chapter_idx, "writer", "build_prompts", f"{len(c.page_prompts)} prompts ready", status="done")
        yield Event(author=self.name)


class IllustrateQAStage(_Stage):
    """Generate illustrations with per-page QA, summarize quality, and save the chapter."""

    async def _run_async_impl(self, _) -> AsyncGenerator[Event, None]:
        from src.agents.qa import QAAgent
        from src.agents.agent_log import log_event

        c = self.ctx
        if c.aborted:
            yield Event(author=self.name)
            return

        total_pages = len(c.page_prompts)
        _update_progress(c.book_id, c.chapter_idx, agent="artist", current_step=f"Illustrating page 1/{total_pages}...",
                         progress=40, total_pages=total_pages, completed_pages=0)
        log_event(c.book_id, c.chapter_idx, "artist", "illustrate", f"Starting illustration of {total_pages} pages")

        def _progress_with_log(completed: int, step: str) -> None:
            agent = "artist" if "Illustrat" in step else "qa"
            _update_progress(c.book_id, c.chapter_idx, agent=agent, current_step=step,
                             progress=40 + int(completed / max(total_pages, 1) * 50),
                             completed_pages=completed, total_pages=total_pages)
            log_event(c.book_id, c.chapter_idx, agent,
                      "illustrate" if agent == "artist" else "check_page", step)

        qa = QAAgent(c.book_id)
        c.illustrations = c.artist.generate_illustrations(
            c.page_prompts, c.simplified, c.character_sheets, c.chapter_dir, qa_agent=qa,
            progress_callback=_progress_with_log, self_correct=c.self_correct,
        )
        log_event(c.book_id, c.chapter_idx, "artist", "illustrate", f"All {total_pages} pages illustrated", status="done")

        _update_progress(c.book_id, c.chapter_idx, agent="qa", current_step="Running quality checks...", progress=92)
        log_event(c.book_id, c.chapter_idx, "qa", "summarize", "Computing chapter quality summary")
        qa.summarize(c.illustrations, c.chapter_dir)
        log_event(c.book_id, c.chapter_idx, "qa", "summarize", "Quality summary complete", status="done")

        c.chapter_data = self._save_chapter_data(c)

        if not c.page_filter:
            c.artist.ensure_ending_pages(c.data, c.chapter_idx, c.segments)

        self._save_to_mongo(c)
        print(f"  Chapter {c.chapter_idx} done: {len(c.chapter_data['pages'])} pages")
        yield Event(author=self.name)

    @staticmethod
    def _save_chapter_data(c: PipelineContext) -> dict:
        chapter_data_path = c.chapter_dir / "chapter_data.json"
        chapter_data = None
        if c.page_filter and chapter_data_path.exists():
            try:
                chapter_data = json.loads(chapter_data_path.read_text(encoding="utf-8"))
                pages = chapter_data.get("pages", [])
                for idx, scene in enumerate(c.simplified):
                    ill = c.illustrations[idx] if idx < len(c.illustrations) else {}
                    pn = scene.get("page_number", 0)
                    if 1 <= pn <= len(pages):
                        pages[pn - 1] = {
                            "text": scene.get("page_text", scene.get("text", "")),
                            "image_path": ill.get("image_path", pages[pn - 1].get("image_path", "")),
                        }
            except (json.JSONDecodeError, OSError):
                chapter_data = None
        if chapter_data is None:
            chapter_data = {"chapter_idx": c.chapter_idx, "chapter_title": c.ch_title, "pages": []}
            for idx, scene in enumerate(c.simplified):
                ill = c.illustrations[idx] if idx < len(c.illustrations) else {}
                chapter_data["pages"].append({
                    "text": scene.get("page_text", scene.get("text", "")),
                    "image_path": ill.get("image_path", ""),
                })
        chapter_data_path.write_text(
            json.dumps(chapter_data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
        return chapter_data

    @staticmethod
    def _save_to_mongo(c: PipelineContext) -> None:
        try:
            import pymongo
            from src.config import MONGODB_URI, MONGODB_DB
            client = pymongo.MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
            db = client[MONGODB_DB]
            db.books.update_one(
                {"book_id": c.book_id, "chapter": c.chapter_idx},
                {"$set": {
                    "book_id": c.book_id, "title": c.title,
                    "chapter": c.chapter_idx, "chapter_title": c.ch_title,
                    "num_pages": len(c.chapter_data["pages"]),
                    "pages": c.chapter_data["pages"],
                }},
                upsert=True,
            )
            client.close()
            print("  MongoDB: saved")
        except Exception:
            pass


def build_pipeline(ctx: PipelineContext) -> SequentialAgent:
    """Compose the four ADK agent stages into the book-generation SequentialAgent."""
    return SequentialAgent(
        name="storysprout_pipeline",
        description="Analyzer -> Artist(setup) -> Writer -> Artist+QA. The production "
                    "picture-book pipeline, orchestrated with Google ADK.",
        sub_agents=[
            AnalyzerStage("analyzer", ctx),
            ArtistSetupStage("artist_setup", ctx),
            WriterStage("writer", ctx),
            IllustrateQAStage("illustrate_qa", ctx),
        ],
    )


async def _run_async(ctx: PipelineContext) -> None:
    pipeline = build_pipeline(ctx)
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="storysprout", user_id="pipeline")
    runner = Runner(agent=pipeline, app_name="storysprout", session_service=session_service)
    message = types.Content(role="user", parts=[types.Part(text=f"Generate chapter {ctx.chapter_idx} of {ctx.book_id}")])
    async for _ in runner.run_async(user_id="pipeline", session_id=session.id, new_message=message):
        pass


def run_adk_pipeline(book_id: str, data: dict, chapter_idx: int, page_filter: list[int] | None = None,
                     age_group: str = "4-6", self_correct: bool = False) -> dict | None:
    """Run the book-generation pipeline via the ADK SequentialAgent. Returns chapter_data."""
    ctx = PipelineContext(book_id, data, chapter_idx, page_filter, age_group, self_correct)
    asyncio.run(_run_async(ctx))
    return ctx.chapter_data
