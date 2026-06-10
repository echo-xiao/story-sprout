"""Artist Agent: character sheet and illustration generation.

Responsible for:
- Generating character reference sheets (portrait + multi-angle)
- Generating page illustrations with character/scene consistency
- Managing visual style coherence across all pages
- Special pages: book cover, chapter covers, endings, back cover
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from src.config import GENERATED_DIR

logger = logging.getLogger(__name__)


class ArtistAgent:
    """Generates all visual assets for the picture book."""

    def __init__(self, book_id: str):
        self.book_id = book_id
        self.characters_dir = GENERATED_DIR / book_id / "characters"
        self.special_dir = GENERATED_DIR / book_id / "special"
        self.characters_dir.mkdir(parents=True, exist_ok=True)
        self.special_dir.mkdir(parents=True, exist_ok=True)

    def generate_character_sheets(
        self,
        profiles: list[dict],
    ) -> list[dict]:
        """Generate (or reuse cached) character reference sheets.

        Two-step process per character:
        1. Portrait (simple front-facing) — used as avatar
        2. Full sheet (multi-angle + expressions) — used as reference

        Returns list of sheet dicts with character_name, sheet_path, visual_identity.
        """
        from src.generation.character_sheet import (
            generate_character_sheets, _assign_visual_identities, _safe_filename,
        )

        profiles = _assign_visual_identities(profiles)
        sheets: list[dict] = []
        to_generate: list[dict] = []

        for p in profiles:
            safe = _safe_filename(p.get("name", ""))
            existing = None
            for ext in (".png", ".jpg"):
                sheet_path = self.characters_dir / f"{safe}_sheet{ext}"
                if sheet_path.exists():
                    existing = str(sheet_path)
                    break
            if existing:
                sheets.append({
                    "character_name": p["name"],
                    "sheet_path": existing,
                    "visual_identity": p.get("visual_identity", ""),
                    "background": p.get("background", ""),
                })
            else:
                to_generate.append(p)

        if to_generate:
            print(f"[Artist Agent] Generating {len(to_generate)} character sheets "
                  f"(reusing {len(sheets)} cached)...")
            t0 = time.time()
            new_sheets = generate_character_sheets(to_generate, self.book_id)
            sheets.extend(new_sheets)
            print(f"  Generated in {time.time() - t0:.1f}s")
        else:
            print(f"[Artist Agent] All {len(sheets)} character sheets cached")

        return sheets

    def generate_illustrations(
        self,
        page_prompts: list[dict],
        simplified: list[dict],
        character_sheets: list[dict],
        chapter_dir: Path,
        qa_agent=None,
        progress_callback=None,
        self_correct: bool = False,
        self_correct_threshold: int = 50,
    ) -> list[dict]:
        """Generate page illustrations with optional per-page QA.

        Uses character sheets as visual references for consistency.
        If qa_agent is provided, runs quality check after each page.
        If self_correct is enabled, pages scoring below the threshold are
        regenerated once with the QA feedback injected into the prompt
        (bounded: max 1 retry per page, the better-scoring image is kept).

        Args:
            page_prompts: Prompt data for each page.
            simplified: Simplified scene data (for QA context).
            character_sheets: Character sheet dicts.
            chapter_dir: Output directory for this chapter.
            qa_agent: Optional QAAgent for per-page quality checks.
            progress_callback: Optional callback(completed: int, step: str) for progress.

        Returns:
            List of illustration dicts (page_number, image_path, prompt_used).
        """
        from src.generation.illustration import _get_client, _generate_single_page

        pages_dir = chapter_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        valid_sheets = [
            s for s in character_sheets
            if s.get("sheet_path") and Path(s["sheet_path"]).exists()
        ]
        img_client = _get_client()

        print(f"\n[Artist Agent] Generating {len(page_prompts)} illustrations...")

        # Progress file for frontend
        progress_file = chapter_dir / "progress.json"
        total = len(page_prompts)

        illustrations: list[dict] = []

        for idx, page_prompt in enumerate(page_prompts):
            page_num = page_prompt.get("page_number", idx + 1)
            save_path = pages_dir / f"page_{page_num:03d}"
            scene = simplified[idx] if idx < len(simplified) else {}

            # Checkpoint
            existing = None
            for ext in (".png", ".jpg"):
                candidate = save_path.with_suffix(ext)
                if candidate.exists():
                    existing = str(candidate)
                    break

            # Update progress
            progress = int(idx / total * 100) if total > 0 else 0
            progress_file.write_text(json.dumps({
                "status": "generating", "progress": progress,
                "current_step": f"Illustrating page {page_num}/{total}...",
                "total_pages": total, "completed_pages": idx,
            }))
            if progress_callback:
                progress_callback(idx, f"Illustrating page {page_num}/{total}...")

            if existing:
                print(f"  Page {page_num}: cached")
                ill_path = existing
            else:
                t0 = time.time()
                success, ill_path, prompt = _generate_single_page(
                    img_client, page_prompt, valid_sheets, save_path,
                )
                dt = time.time() - t0
                if not success:
                    print(f"  Page {page_num}: FAILED ({dt:.1f}s)")
                    illustrations.append({"page_number": page_num, "image_path": "", "prompt_used": prompt})
                    continue
                for ext in (".png", ".jpg"):
                    candidate = save_path.with_suffix(ext)
                    if candidate.exists():
                        ill_path = str(candidate)
                        break
                print(f"  Page {page_num}: generated ({dt:.1f}s)")

            illustrations.append({"page_number": page_num, "image_path": ill_path, "prompt_used": ""})

            # Per-page QA
            if qa_agent and ill_path:
                if progress_callback:
                    progress_callback(idx + 1, f"QA checking page {page_num}/{total}...")
                result = None
                if self_correct and existing:
                    # Reuse the saved report for cached pages instead of
                    # burning another vision call
                    quality_file = chapter_dir / "quality" / f"page_{page_num:03d}_quality.json"
                    if quality_file.exists():
                        try:
                            result = json.loads(quality_file.read_text(encoding="utf-8"))
                            qa_agent.record_cached(result)
                            print(f"  [QA Agent] Page {page_num}: {result.get('overall_score', '?')}% (cached report)")
                        except (json.JSONDecodeError, OSError):
                            result = None
                if result is None:
                    result = qa_agent.check_page(
                        ill_path, character_sheets, scene, page_num, chapter_dir,
                    )

                if (
                    self_correct
                    and result
                    and result.get("overall_score", 100) < self_correct_threshold
                    and not result.get("self_correct_attempted")
                    and result.get("regeneration_feedback")
                ):
                    if progress_callback:
                        progress_callback(idx + 1, f"Self-correcting page {page_num}/{total}...")
                    ill_path = self._self_correct_page(
                        img_client, page_prompt, valid_sheets, save_path, ill_path,
                        result, qa_agent, character_sheets, scene, page_num, chapter_dir,
                    )
                    illustrations[-1]["image_path"] = ill_path

        return illustrations

    def _self_correct_page(
        self,
        img_client,
        page_prompt: dict,
        valid_sheets: list[dict],
        save_path: Path,
        old_path: str,
        old_result: dict,
        qa_agent,
        character_sheets: list[dict],
        scene: dict,
        page_num: int,
        chapter_dir: Path,
    ) -> str:
        """Regenerate one low-scoring page using QA feedback (max 1 retry).

        Keeps whichever image scores higher; the losing image goes to history.
        Marks the quality report with self_correct_attempted so the page is
        never retried again on later runs.
        """
        from src.generation.illustration import _generate_single_page

        old_score = old_result.get("overall_score", 0)
        feedback = old_result.get("regeneration_feedback", "")
        print(f"  [QA Agent → Artist] Page {page_num}: {old_score}% below threshold, "
              f"regenerating with QA feedback...")

        history_dir = chapter_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        old_file = Path(old_path)
        backup = history_dir / f"{old_file.stem}_selfcorrect_prev{old_file.suffix}"
        shutil.copy2(old_file, backup)

        quality_file = chapter_dir / "quality" / f"page_{page_num:03d}_quality.json"
        quality_file.parent.mkdir(parents=True, exist_ok=True)

        success, new_path, _ = _generate_single_page(
            img_client, page_prompt, valid_sheets, save_path,
            correction_feedback=feedback,
        )
        if not success:
            print(f"  [Self-Correct] Page {page_num}: regeneration failed, keeping original")
            if not old_file.exists():
                shutil.copy2(backup, old_file)
            old_result["self_correct_attempted"] = True
            quality_file.write_text(
                json.dumps(old_result, indent=2, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
            return old_path

        new_result = qa_agent.check_page(
            new_path, character_sheets, scene, page_num, chapter_dir,
        ) or {}
        new_score = new_result.get("overall_score", 0)
        kept_new = new_score >= old_score
        final = new_result if kept_new else old_result

        if not kept_new:
            # Retry scored worse — restore the original image
            Path(new_path).unlink(missing_ok=True)
            shutil.copy2(backup, old_file)
            if qa_agent.per_page_results and qa_agent.per_page_results[-1] is new_result:
                qa_agent.per_page_results[-1] = old_result

        final["self_correct_attempted"] = True
        final["self_correct"] = {
            "old_score": old_score, "new_score": new_score,
            "kept": "new" if kept_new else "old",
        }
        quality_file.write_text(
            json.dumps(final, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  [Self-Correct] Page {page_num}: {old_score}% -> {new_score}%, "
              f"kept {'new' if kept_new else 'original'}")

        try:
            from src.agents.agent_log import log_event
            log_event(
                self.book_id,
                chapter_dir.name.replace("ch", "").lstrip("0") or "0",
                "qa", "self_correct",
                f"Page {page_num}: {old_score}% < threshold, regenerated with QA feedback",
                result=f"{old_score}% -> {new_score}%, kept {'new' if kept_new else 'original'}",
                status="done" if kept_new else "warn",
            )
        except Exception:
            pass

        return new_path if kept_new else str(old_file)

    def generate_book_cover(self, title: str, profiles: list[dict]) -> str:
        """Generate book cover illustration."""
        from src.generation.special_pages import generate_book_cover
        from src.generation.character_sheet import _assign_visual_identities

        profiles = _assign_visual_identities(profiles)
        print(f"[Artist Agent] Generating book cover...")
        t0 = time.time()
        path = generate_book_cover(title, profiles, self.book_id)
        print(f"  Done in {time.time() - t0:.1f}s")
        return path

    def generate_chapter_cover(
        self, ch_title: str, ch_num: int, summary: str, profiles: list[dict]
    ) -> str:
        """Generate chapter title page."""
        from src.generation.special_pages import generate_chapter_cover
        from src.generation.character_sheet import _assign_visual_identities

        profiles = _assign_visual_identities(profiles)
        print(f"[Artist Agent] Generating chapter {ch_num} cover...")
        t0 = time.time()
        path = generate_chapter_cover(ch_title, ch_num, summary, profiles, self.book_id)
        print(f"  Done in {time.time() - t0:.1f}s")
        return path

    def generate_chapter_ending(
        self, ch_title: str, ch_num: int, ending_text: str, profiles: list[dict]
    ) -> str:
        """Generate chapter ending page."""
        from src.generation.special_pages import generate_chapter_ending
        from src.generation.character_sheet import _assign_visual_identities

        profiles = _assign_visual_identities(profiles)
        print(f"[Artist Agent] Generating chapter {ch_num} ending...")
        t0 = time.time()
        path = generate_chapter_ending(ch_title, ch_num, ending_text, profiles, self.book_id)
        print(f"  Done in {time.time() - t0:.1f}s")
        return path

    def generate_back_cover(self, title: str) -> str:
        """Generate back cover."""
        from src.generation.special_pages import generate_back_cover

        print(f"[Artist Agent] Generating back cover...")
        t0 = time.time()
        path = generate_back_cover(title, self.book_id)
        print(f"  Done in {time.time() - t0:.1f}s")
        return path

    def ensure_special_pages(
        self, data: dict, chapter_idx: int, segments: list[dict]
    ):
        """Generate special pages if not already cached."""
        meta = data.get("meta", {})
        title = meta.get("title", "Untitled")
        profiles = data.get("analysis", {}).get("character_profiles", [])
        main = [p for p in profiles if p.get("role") in ("main", "supporting")][:5]
        if not main:
            main = profiles[:5]

        # Book cover
        cover_exists = any(
            (self.special_dir / f"book_cover{ext}").exists() for ext in (".png", ".jpg")
        )
        if not cover_exists:
            self.generate_book_cover(title, main)

        # Chapter cover
        ch_num = chapter_idx + 1
        ch_cover_exists = any(
            (self.special_dir / f"chapter_{ch_num:02d}_cover{ext}").exists()
            for ext in (".png", ".jpg")
        )
        if not ch_cover_exists:
            from src.agents.analyzer import AnalyzerAgent
            analyzer = AnalyzerAgent(self.book_id)
            _, ch_title = analyzer.get_chapter_segments(data, chapter_idx)
            summary = segments[0].get("text", "")[:200] if segments else ""
            self.generate_chapter_cover(ch_title, ch_num, summary, main)

    def ensure_ending_pages(
        self, data: dict, chapter_idx: int, segments: list[dict]
    ):
        """Generate ending pages if not already cached."""
        meta = data.get("meta", {})
        title = meta.get("title", "Untitled")
        profiles = data.get("analysis", {}).get("character_profiles", [])
        main = [p for p in profiles if p.get("role") in ("main", "supporting")][:5]
        if not main:
            main = profiles[:5]

        ch_num = chapter_idx + 1

        # Chapter ending
        ch_ending_exists = any(
            (self.special_dir / f"chapter_{ch_num:02d}_ending{ext}").exists()
            for ext in (".png", ".jpg")
        )
        if not ch_ending_exists:
            from src.agents.analyzer import AnalyzerAgent
            analyzer = AnalyzerAgent(self.book_id)
            _, ch_title = analyzer.get_chapter_segments(data, chapter_idx)
            ending_text = segments[-1].get("text", "")[:200] if segments else ""
            self.generate_chapter_ending(ch_title, ch_num, ending_text, main)

        # Back cover
        back_exists = any(
            (self.special_dir / f"back_cover{ext}").exists() for ext in (".png", ".jpg")
        )
        if not back_exists:
            self.generate_back_cover(title)
