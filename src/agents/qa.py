"""QA Agent: quality checks for generated picture book pages.

Responsible for:
- Per-page quality check (spelling, character consistency, duplicates, name-face match)
- Style coherence across all pages
- Chapter-level quality summary
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from src.config import GENERATED_DIR

logger = logging.getLogger(__name__)


class QAAgent:
    """Checks and reports quality issues in generated illustrations."""

    def __init__(self, book_id: str):
        self.book_id = book_id
        self.per_page_results: list[dict] = []
        self.per_character_scores: dict[str, list[int]] = {}

    def check_page(
        self,
        illustration_path: str,
        character_sheets: list[dict],
        scene: dict,
        page_num: int,
        chapter_dir: Path,
    ) -> dict | None:
        """Run quality check on a single page illustration.

        Checks: character consistency, spelling, duplicate characters,
        name-face mismatch, character count.

        Returns the quality result dict, or None if check unavailable.
        """
        try:
            from src.generation.gemini_consistency_check import check_page_quality
        except Exception:
            return None

        scene_chars = scene.get("key_characters", [])
        page_text = scene.get("page_text", scene.get("text", ""))
        relevant_sheets = [
            s for s in character_sheets
            if s["character_name"] in scene_chars
        ]

        t0 = time.time()
        result = check_page_quality(
            illustration_path, relevant_sheets, page_text, scene_chars, page_num,
        )
        result["page"] = page_num
        self.per_page_results.append(result)

        # Save per-page quality file
        quality_dir = chapter_dir / "quality"
        quality_dir.mkdir(parents=True, exist_ok=True)
        quality_file = quality_dir / f"page_{page_num:03d}_quality.json"
        quality_file.write_text(
            json.dumps(result, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )

        # Track per-character scores
        for c in result.get("character_consistency", {}).get("characters", []):
            self.per_character_scores.setdefault(c["name"], []).append(c.get("score", 100))

        # Print summary
        score = result.get("overall_score", 100)
        issues = []
        if result.get("spelling", {}).get("errors"):
            issues.append(f"spell:{len(result['spelling']['errors'])}")
        if result.get("duplicate_characters", {}).get("duplicates"):
            issues.append(f"dup:{len(result['duplicate_characters']['duplicates'])}")
        if result.get("name_face_mismatch", {}).get("mismatches"):
            issues.append(f"name:{len(result['name_face_mismatch']['mismatches'])}")
        if result.get("character_count", {}).get("missing"):
            issues.append(f"miss:{result['character_count']['missing']}")
        status = "OK" if score >= 80 else "WARN" if score >= 60 else "BAD"
        issues_str = f" ({', '.join(issues)})" if issues else ""
        dt = time.time() - t0
        print(f"  [QA Agent] Page {page_num}: {score}% [{status}]{issues_str} ({dt:.1f}s)")

        # Log to agent activity log
        try:
            from src.agents.agent_log import log_event
            log_status = "done" if score >= 80 else "warn" if score >= 60 else "error"
            log_event(
                self.book_id, chapter_dir.name.replace("ch", "").lstrip("0") or "0",
                "qa", "check_page",
                f"Page {page_num}: {score}%{issues_str}",
                result=f"Score {score}% - {status}",
                status=log_status,
            )
        except Exception:
            pass

        return result

    def check_style_coherence(
        self, illustrations: list[dict], chapter_dir: Path,
    ) -> dict:
        """Check visual style consistency across all pages.

        Compares each page against the book cover as style reference.
        """
        try:
            from src.generation.gemini_consistency_check import check_style_consistency
        except Exception:
            return {"score": 100, "per_page": [], "issues": []}

        ill_paths = [
            ill.get("image_path", "")
            for ill in illustrations if ill.get("image_path")
        ]
        if len(ill_paths) < 2:
            return {"score": 100, "per_page": [], "issues": []}

        # Find book cover as reference
        cover_path = None
        special_dir = GENERATED_DIR / self.book_id / "special"
        for ext in (".png", ".jpg"):
            candidate = special_dir / f"book_cover{ext}"
            if candidate.exists():
                cover_path = str(candidate)
                break

        print(f"[QA Agent] Checking style coherence across {len(ill_paths)} pages...")
        return check_style_consistency(ill_paths, reference_path=cover_path)

    def summarize(
        self, illustrations: list[dict], chapter_dir: Path,
    ) -> dict:
        """Compute and save chapter-level quality summary.

        Aggregates per-page results into dimension scores.
        """
        try:
            # Per-character averages
            per_character_avg = []
            for name, scores in self.per_character_scores.items():
                avg = round(sum(scores) / len(scores)) if scores else 100
                per_character_avg.append({"name": name, "score": avg})
            char_overall = (
                round(sum(c["score"] for c in per_character_avg) / len(per_character_avg))
                if per_character_avg else 100
            )

            # Style coherence
            style_result = self.check_style_coherence(illustrations, chapter_dir)

            # Dimension scores
            n = max(len(self.per_page_results), 1)
            dim_scores = {
                "character_consistency": round(sum(
                    r.get("character_consistency", {}).get("score", 100)
                    for r in self.per_page_results
                ) / n),
                "spelling": round(sum(
                    r.get("spelling", {}).get("score", 100)
                    for r in self.per_page_results
                ) / n),
                "duplicate_characters": round(sum(
                    r.get("duplicate_characters", {}).get("score", 100)
                    for r in self.per_page_results
                ) / n),
                "name_face_mismatch": round(sum(
                    r.get("name_face_mismatch", {}).get("score", 100)
                    for r in self.per_page_results
                ) / n),
                "character_count": round(sum(
                    r.get("character_count", {}).get("score", 100)
                    for r in self.per_page_results
                ) / n),
                "style_coherence": style_result.get("score", 100),
            }

            result = {
                "overall_score": round(sum(dim_scores.values()) / len(dim_scores)),
                "dimensions": dim_scores,
                "character_match": {"score": char_overall, "per_character": per_character_avg},
                "style_coherence": style_result,
                "per_page": self.per_page_results,
            }

            print(f"\n  === [QA Agent] Chapter Quality Summary ===")
            print(f"  Overall: {result['overall_score']}%")
            for dim, sc in dim_scores.items():
                st = "OK" if sc >= 80 else "WARN" if sc >= 60 else "BAD"
                print(f"    {dim}: {sc}% [{st}]")

            consistency_path = chapter_dir / "consistency.json"
            consistency_path.write_text(
                json.dumps(result, indent=2, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
            return result

        except Exception as e:
            logger.warning("[QA Agent] Summary failed: %s", e)
            return {"overall_score": -1}
