"""One-time migration: copy every JSON object from the GCS bucket into Firestore.

The JSON data layer is moving from GCS objects to Firestore (strong read-your-writes
consistency). Image BYTES stay in GCS — only `*.json` keys are migrated. Reads use
`store._gcs_*` (source), writes use `store._fs_*` (dest), so both backends are active
in one process. Idempotent: re-running overwrites. Does NOT delete anything from GCS
(GCS remains the rollback copy).

Usage:
    FIRESTORE_DATABASE=default python scripts/migrate_gcs_json_to_firestore.py --dry-run
    FIRESTORE_DATABASE=default python scripts/migrate_gcs_json_to_firestore.py
Credentials come from GCS_SA_JSON / GCS_BUCKET in the environment (same as the app).
"""
import os
import sys
import json
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DRY = "--dry-run" in sys.argv
_ONE_MIB = 1024 * 1024


def main():
    from src.core import store

    print(f"Firestore database: {os.environ.get('FIRESTORE_DATABASE', '(default)')}")
    print("Listing JSON objects in GCS...")
    keys = [k for k in store._gcs_list_keys("") if k.endswith(".json")]
    by_book = defaultdict(int)
    oversized = []
    for k in keys:
        by_book[k.split("/", 1)[0]] += 1

    print(f"\nFound {len(keys)} JSON objects across {len(by_book)} book(s):")
    for book in sorted(by_book):
        print(f"  {book}: {by_book[book]} files")

    migrated = 0
    for k in keys:
        data = store._gcs_get_json(k)
        if data is None:
            print(f"  ! skip (unreadable): {k}")
            continue
        size = len(json.dumps(data, ensure_ascii=False).encode())
        if size >= _ONE_MIB:
            oversized.append((k, size))
            print(f"  !! OVERSIZE {size} bytes >= 1MiB (Firestore doc limit): {k}")
        if not DRY:
            store._fs_put_json(k, data)
            migrated += 1

    if oversized:
        print(f"\n⚠️  {len(oversized)} doc(s) exceed Firestore's 1 MiB limit — these "
              f"need splitting before they can be stored. Listed above.")
    if DRY:
        print(f"\n[DRY RUN] would migrate {len(keys)} objects. No writes performed.")
    else:
        print(f"\n✅ Migrated {migrated} JSON objects to Firestore.")
        # Verify count parity.
        fs_keys = [k for k in store._fs_list_keys("") if k.endswith(".json")]
        print(f"Firestore now holds {len(fs_keys)} JSON docs (GCS had {len(keys)}).")


if __name__ == "__main__":
    main()
