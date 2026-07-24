# StorySprout — Unify Page/Special Carousel onto the Version Store

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Page and special-page history carousels build from the durable version store (`list_asset_versions`) — the SAME system characters/scenes use — so every carousel entry carries its own per-version QA, and restore selects a version instead of renaming history files. Achieves true editor-wide version coherence.

**Architecture:** `get_segment_illustration_history` / `get_special_page_history` currently build entries from a `history/`-prefix file glob + the current image, and attach QA only to the "current" entry (historical entries have none). Restore renames `history/` files. This plan rebuilds both carousels from `list_asset_versions` (backfilled), mapping each version → the SAME `{url, version, timestamp, quality}` shape the frontend already consumes (the selected version → `version:"current"`), and switches restore to `set_selected_version` + `_promote_selected`. The frontend is UNCHANGED — it treats `img.version` as an opaque string, reads `img.quality`, and calls `restoreSegmentVersion(img.version)`.

**Tech Stack:** FastAPI; `src/routes/editor.py` (history + restore endpoints), `src/core/store.py` (`list_asset_versions`/`set_selected_version`), `src/routes/editor.py` `_backfill_versions`/`_promote_selected`. Tests: pytest + fake store bucket.

## Global Constraints
- Backend suite green: baseline **286 passed**; never fewer + new tests.
- Both modes (prod GCS / fake store). Additive/guarded; a page/special with no versions falls back gracefully (empty carousel, not a crash).
- Frontend contract unchanged: `{images: [{url, version, timestamp, quality?}]}`; selected → `version:"current"`; restore accepts the `version` string (now a version-id).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Deploy backend only (`vercel --prod --yes` → `storysprout`). Frontend needs NO redeploy.
- asset_key: page = `f"ch{ch_idx:02d}:p{page_num:03d}"`; special = `f"{page_type}:{chapter}"`.

---

## Task 1: Segment (page) history + restore onto the version store

**Files:** Modify `src/routes/editor.py` (`get_segment_illustration_history`, `restore_segment_version`). Test `tests/test_page_carousel_versions.py` (create).

- [ ] **Step 1: Failing test** — assert (a) `get_segment_illustration_history` returns one entry per recorded version with that version's own `quality`, selected → `version:"current"`; (b) `restore_segment_version(seg, version_id)` calls `set_selected_version(book,"page",key,version_id)`. Seed via the store (2 page versions, each `set_version_quality`, one selected). Drive the endpoints (mirror `tests/test_stale_versioned.py` stubbing of `_load_json` for analysis/segment→page_num).

- [ ] **Step 2:** Run → FAIL.

- [ ] **Step 3: Rewrite `get_segment_illustration_history`** — after deriving `ch_idx`/`page_num`/`asset_key = f"ch{ch_idx:02d}:p{page_num:03d}"`: `_backfill_versions(book_id, "page", asset_key)`, then `rec = list_asset_versions(book_id, "page", asset_key)`. Build `images` newest-first: for each version `v`, `{"url": v["url"], "version": "current" if v["id"]==rec["selected_version_id"] else v["id"], "timestamp": _epoch(v.get("created_at")), "quality": v.get("quality")}` (omit `quality` key when None). Keep the `_load_quality` legacy fallback ONLY for the selected/current entry when it has no stored quality. If `rec["versions"]` is empty, fall back to the existing current-image path so nothing regresses for never-recorded pages.

- [ ] **Step 4: Rewrite `restore_segment_version`** — derive `ch_idx`/`page_num`/`asset_key`; `ok = set_selected_version(book_id, "page", asset_key, version)`; if not ok → 404; `_promote_selected(book_id, "page", asset_key)` (updates the live page image + chapter_data). Drop the `history/`-file rename dance.

- [ ] **Step 5:** Run → PASS. Full suite ≥288.

- [ ] **Step 6: Commit** `fix(pages): build segment carousel from the version store (per-version QA) + version-based restore`.

---

## Task 2: Special-page history + restore onto the version store

**Files:** Modify `src/routes/editor.py` (`get_special_page_history`, `restore_special_page_version`). Test `tests/test_special_carousel_versions.py` (create).

- [ ] **Step 1: Failing test** — mirror Task 1 for special: `asset_key = f"{page_type}:{chapter}"`, asset_type `"special"`. Two versions + per-version quality + selected → current; restore selects the version.

- [ ] **Step 2:** Run → FAIL.

- [ ] **Step 3: Rewrite `get_special_page_history`** — same pattern: `_backfill_versions` + `list_asset_versions(book_id, "special", f"{page_type}:{chapter}")` → map to `{url, version, timestamp, quality}` (selected → "current"); fall back to current-image path when no versions.

- [ ] **Step 4: Rewrite `restore_special_page_version`** — `set_selected_version(book_id, "special", f"{page_type}:{chapter}", version)` + `_promote_selected`; 404 if not found. Drop history-file rename.

- [ ] **Step 5:** Run → PASS. Full suite ≥290.

- [ ] **Step 6: Commit** `fix(special): build special carousel from the version store + version-based restore`.

---

## Task 3: Deploy + live-verify

- [ ] **Step 1:** Deploy backend only (`vercel --prod --yes` → `storysprout`). No frontend redeploy.
- [ ] **Step 2:** On `storysprout-web.vercel.app`: regenerate a page twice → the page version carousel shows BOTH versions, EACH with its own QA text; click an older version → it restores (becomes current); the character/scene/page carousels now all behave identically.

## Self-Review
Coverage: both page + special carousels + restore moved onto `list_asset_versions`/`set_selected_version` → per-version QA for all entries + unified system. Frontend contract preserved (opaque `version` string, `quality`, `restore(version)`). Fallback: no-version pages keep the current-image path. Restore via `_promote_selected` also updates the live image + chapter_data (page). Risk: restore behavior changes from file-rename to version-select — Task 1/2 tests assert the select path; live-verify (Task 3) confirms the promote updates the visible image.
