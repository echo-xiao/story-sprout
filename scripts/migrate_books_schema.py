"""One-time (idempotent) migration: split the mixed `books` collection.

Before: `books` held both book-level docs ({book_id, title, num_chapters, ...})
and per-chapter docs ({book_id, chapter, pages, ...}), plus case-variant
book_id duplicates. Readers papered over it with $exists guards and dedup.

After:
  books          — exactly one doc per book (unique index on book_id)
  book_chapters  — one doc per generated chapter (unique on book_id+chapter)

Safe to re-run: every step is an upsert/merge. A JSON backup of the whole
books collection is written next to the data before anything is touched.

Usage: python scripts/migrate_books_schema.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymongo  # noqa: E402

from src.config import GENERATED_DIR, MONGODB_DB, MONGODB_URI  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing.")
    args = parser.parse_args()

    client = pymongo.MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
    client.admin.command("ping")
    db = client[MONGODB_DB]

    docs = list(db.books.find({}))
    print(f"books collection: {len(docs)} docs")

    # ── Backup ──────────────────────────────────────────────────────────
    backup_dir = GENERATED_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"books_backup_{int(time.time())}.json"
    if not args.dry_run:
        backup_path.write_text(
            json.dumps(docs, default=str, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"backup written: {backup_path}")

    # ── 1. Move per-chapter docs → book_chapters ────────────────────────
    # Case-variant book_ids exist among chapter docs too (old runs wrote
    # uppercase ids). Normalize to the on-disk directory name when one
    # exists, else lowercase, and keep the most complete doc per chapter.
    chapter_docs = [d for d in docs if "chapter" in d]
    book_docs = [d for d in docs if "chapter" not in d]
    print(f"  chapter-shaped docs: {len(chapter_docs)}  book-shaped docs: {len(book_docs)}")

    def canonical_id(book_id: str) -> str:
        for variant in (book_id, book_id.lower()):
            if (GENERATED_DIR / variant).exists():
                return variant
        return book_id.lower()

    by_chapter: dict[tuple, list[dict]] = {}
    for d in chapter_docs:
        by_chapter.setdefault(((d.get("book_id") or "").lower(), d.get("chapter")), []).append(d)

    for (key, ch), group in sorted(by_chapter.items()):
        group.sort(key=lambda d: len(d.get("pages") or []), reverse=True)
        keeper = dict(group[0])
        keeper.pop("_id", None)
        keeper["book_id"] = canonical_id(keeper["book_id"])
        dropped = len(group) - 1
        note = f" (merged {dropped} case-variant dup)" if dropped else ""
        if args.dry_run:
            print(f"  [dry] → book_chapters: {keeper['book_id']} ch{ch}{note}")
            continue
        db.book_chapters.update_one(
            {"book_id": keeper["book_id"], "chapter": ch},
            {"$set": keeper},
            upsert=True,
        )
        db.books.delete_many({"_id": {"$in": [d["_id"] for d in group]}})
        print(f"  → book_chapters: {keeper['book_id']} ch{ch}{note}")

    # ── 2. Merge case-variant duplicates of book-level docs ────────────
    by_key: dict[str, list[dict]] = {}
    for d in book_docs:
        by_key.setdefault((d.get("book_id") or "").lower(), []).append(d)

    for key, group in by_key.items():
        if len(group) <= 1:
            continue
        # Keep the variant whose directory actually exists on disk; ties (or
        # neither) broken by newest updated_at.
        def rank(d: dict) -> tuple:
            return (
                (GENERATED_DIR / (d.get("book_id") or "")).exists(),
                str(d.get("updated_at") or ""),
            )
        group.sort(key=rank, reverse=True)
        keeper, rest = group[0], group[1:]
        merged = {}
        for d in [keeper, *rest]:  # keeper's values win; others fill gaps
            for k, v in d.items():
                if k != "_id" and k not in merged and v not in (None, "", [], {}):
                    merged[k] = v
        print(f"  merge {len(group)} variants of '{key}' → keep '{keeper.get('book_id')}'")
        if args.dry_run:
            continue
        db.books.update_one({"_id": keeper["_id"]}, {"$set": merged})
        db.books.delete_many({"_id": {"$in": [d["_id"] for d in rest]}})

    # ── 3. Unique indexes so the mess can't come back ───────────────────
    if not args.dry_run:
        db.books.create_index("book_id", unique=True)
        db.book_chapters.create_index([("book_id", 1), ("chapter", 1)], unique=True)
        print("indexes ensured: books.book_id (unique), book_chapters.book_id+chapter (unique)")

    print(f"done. books: {db.books.count_documents({})} docs, "
          f"book_chapters: {db.book_chapters.count_documents({})} docs")
    client.close()


if __name__ == "__main__":
    main()
