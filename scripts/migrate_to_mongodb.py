#!/usr/bin/env python3
"""Migrate local preprocess JSON files to MongoDB.

Usage:
    python scripts/migrate_to_mongodb.py [--book BOOK_ID]

Without --book, migrates all books found in data/generated/.
"""

import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import GENERATED_DIR
from src.core.db import save_preprocess_file, is_available, _get_db


def migrate_book(book_id: str) -> int:
    """Migrate all preprocess JSON files for a book. Returns count of files migrated."""
    preprocess_dir = GENERATED_DIR / book_id / "preprocess"
    if not preprocess_dir.exists():
        print(f"  No preprocess dir for {book_id}, skipping")
        return 0

    count = 0
    for json_file in sorted(preprocess_dir.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            if save_preprocess_file(book_id, json_file.name, data):
                count += 1
                print(f"  Uploaded {json_file.name}")
            else:
                print(f"  FAILED {json_file.name} (MongoDB unavailable)")
        except (json.JSONDecodeError, OSError) as e:
            print(f"  SKIPPED {json_file.name}: {e}")

    return count


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Migrate preprocess data to MongoDB")
    parser.add_argument("--book", help="Specific book_id to migrate")
    args = parser.parse_args()

    if not is_available():
        print("ERROR: MongoDB is not available. Check MONGODB_URI in .env")
        sys.exit(1)

    db = _get_db()
    print(f"Connected to MongoDB: {db.name}")

    if args.book:
        book_ids = [args.book]
    else:
        book_ids = []
        if GENERATED_DIR.exists():
            for d in sorted(GENERATED_DIR.iterdir()):
                if d.is_dir() and (d / "preprocess").exists():
                    book_ids.append(d.name)

    if not book_ids:
        print("No books found to migrate.")
        return

    total = 0
    for book_id in book_ids:
        print(f"\nMigrating: {book_id}")
        count = migrate_book(book_id)
        total += count
        print(f"  -> {count} files uploaded")

    print(f"\nDone! {total} files migrated across {len(book_ids)} books.")

    # Verify
    count = db.preprocess_files.count_documents({})
    print(f"Total documents in preprocess_files collection: {count}")


if __name__ == "__main__":
    main()
