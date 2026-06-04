#!/usr/bin/env python3
"""CLI script to run the picture-book generation pipeline.

Usage
-----
    python scripts/run_pipeline.py --input path/to/book.txt --pages 10 --age "4-6"
    python scripts/run_pipeline.py --text "Once upon a time..." --pages 8 --template journey
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure the project root is on sys.path so ``src`` is importable.
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from src.models import GenerationConfig, GenerationStatus  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a picture book from a text source.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", type=str, help="Path to a .txt / .pdf / .epub file.")
    group.add_argument("--text", type=str, help="Raw text string to adapt.")

    parser.add_argument("--pages", type=int, default=10, help="Number of pages (default: 10).")
    parser.add_argument("--age", type=str, default="4-6", help="Target age group (default: '4-6').")
    parser.add_argument("--template", type=str, default="classic", help="Story template (classic/journey/simple).")
    parser.add_argument("--style", type=str, default=None, help="Optional illustration style override.")
    parser.add_argument("--chapters", type=str, default=None, help="Comma-separated chapter indices to include.")
    parser.add_argument("--education-goal", type=str, default=None, help="Educational objective.")
    return parser.parse_args()


async def _status_printer(status: GenerationStatus) -> None:
    """Print status updates to stdout."""
    bar_len = 30
    filled = int(bar_len * status.progress / 100)
    bar = "#" * filled + "-" * (bar_len - filled)
    print(f"  [{bar}] {status.progress:3d}%  {status.current_step}", flush=True)

    if status.error:
        print(f"\n  ERROR: {status.error}", file=sys.stderr)


async def main() -> None:
    args = _parse_args()

    source: str
    if args.input:
        resolved = Path(args.input).resolve()
        if not resolved.exists():
            print(f"Error: file not found: {resolved}", file=sys.stderr)
            sys.exit(1)
        # Read the file content directly for the agent
        source = resolved.read_text(encoding="utf-8", errors="replace")
    else:
        source = args.text

    chapters_list: list[int] | None = None
    if args.chapters:
        chapters_list = [int(c.strip()) for c in args.chapters.split(",")]

    config = GenerationConfig(
        age_group=args.age,
        num_pages=args.pages,
        template=args.template,
        style=args.style,
        selected_chapters=chapters_list,
        education_goal=args.education_goal,
    )

    print(f"Picture Book Generator (Agent Mode)")
    print(f"====================================")
    print(f"  Source : {str(source)[:80]}{'...' if len(source) > 80 else ''}")
    print(f"  Pages  : {config.num_pages}")
    print(f"  Age    : {config.age_group}")
    print(f"  Template: {config.template}")
    print()

    from src.agent_orchestrator import run_agent  # noqa: E402

    try:
        book = await run_agent(source, config, status_callback=_status_printer)
    except Exception as exc:
        print(f"\nAgent failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print()
    print(f"Done!  book_id = {book.book_id}")
    print(f"  Title : {book.title}")
    print(f"  Pages : {len(book.pages)}")

    from src.config import GENERATED_DIR  # noqa: E402

    output_dir = GENERATED_DIR / book.book_id
    print(f"  Output: {output_dir}")

    if book.qa_results:
        passes = book.qa_results.get("passes", "unknown")
        summary = book.qa_results.get("summary", "")
        print(f"  QA    : {'PASSED' if passes else 'ISSUES FOUND'} - {summary}")


if __name__ == "__main__":
    asyncio.run(main())
