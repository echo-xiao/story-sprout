#!/usr/bin/env bash
# Admin chapter (re)generation — runs on the PROJECT Vertex backend, no BYOK key,
# WITHOUT flipping the global REQUIRE_USER_KEY switch (so generation never opens
# to the public, and there is no env change → no Cloud Run drain that would kill
# an in-flight run). Auth is per-request via X-Admin-Token (read from .env).
#
#   ./scripts/admin_gen.sh the_great_gatsby 0
#   BASE=http://localhost:8000 ./scripts/admin_gen.sh the_great_gatsby 0
set -euo pipefail

BASE="${BASE:-https://picture-book-gen-e3mtc46uua-uc.a.run.app}"
BOOK="${1:?usage: admin_gen.sh <book_id> <chapter_idx>}"
CH="${2:?usage: admin_gen.sh <book_id> <chapter_idx>}"

TOKEN="$(grep -E '^ADMIN_TOKEN=' .env | cut -d= -f2- | tr -d ' "')"
[ -z "$TOKEN" ] && { echo "ERROR: ADMIN_TOKEN not set in .env"; exit 1; }

echo "Admin → (re)generating ch${CH} of ${BOOK} on Vertex (no key, no flag flip)…"
curl -s -H "X-Admin-Token: ${TOKEN}" -X POST "${BASE}/api/book/${BOOK}/chapter/${CH}/generate"; echo

# Poll until the run reaches a terminal state.
while :; do
  s="$(curl -s "${BASE}/api/book/${BOOK}/chapter/${CH}/progress")"
  st="$(printf '%s' "$s" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("status",""))' 2>/dev/null || echo '')"
  cp="$(printf '%s' "$s" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("completed_pages",0))' 2>/dev/null || echo '?')"
  step="$(printf '%s' "$s" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("current_step",""))' 2>/dev/null || echo '')"
  echo "  status=${st} pages=${cp} — ${step}"
  case "$st" in complete|failed) break;; esac
  sleep 50
done

echo "=== Done: ${st} ==="
curl -s "${BASE}/api/book/${BOOK}/chapter/${CH}/stale-pages" \
  | python3 -c 'import sys,json;s=json.load(sys.stdin).get("stale",[]);print("stale pages:", [p["page"] for p in s] or "none")'
