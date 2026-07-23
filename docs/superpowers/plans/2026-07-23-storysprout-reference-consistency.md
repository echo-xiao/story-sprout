# StorySprout — Page Reference Consistency (character + scene) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every generated page references the SAME, user-selected, immutable character-sheet and scene-sheet image, so characters and scenes stay consistent across all pages.

**Architecture:** Today page generation resolves each character/scene reference by localizing a MUTABLE "current" key (`{safe}_sheet` / `{safe}_scene`); `storage.localize` returns a stale `/tmp` cache without refreshing, warm serverless instances therefore serve different bytes, and a missing local sheet triggers an on-the-fly regenerate — three defects that make references drift page-to-page. The fix: resolve references from the SELECTED version's IMMUTABLE, content-addressed image (`{name}_{hash}.jpg`), which is cache-safe (same key ⇒ same bytes on every instance) and stable; and make a manual selection sticky so a later regen never hijacks it. Fallback to the old current-file path only when no version exists.

**Tech Stack:** FastAPI (Python 3.12), `src/core/store.py` (GCS-JSON asset versions), `src/core/storage.py` (image bytes + `localize`/`record_image_version`), `src/routes/generation.py` (page/character/scene generation), `src/generation/illustration.py` (page image + scene-sheet lookup). Tests: pytest with the in-memory fake GCS bucket (`_fake_store_bucket` in `tests/conftest.py`).

## Global Constraints

- Backend suite stays green: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest -q` from repo root (current baseline **267 passed**; never fewer, plus this plan's new tests).
- Tests run with `storage.GCS_BUCKET=""` (autouse `_no_real_gcs`) and `store._bucket` = in-memory fake. Every change must work in BOTH prod (real GCS) and test (local fallback / fake) modes.
- Every change is ADDITIVE with a guarded FALLBACK to today's current-file behavior — a missing/blank selected version must never break generation, only fall back.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Deploy target is the EXISTING backend project only: `vercel --prod --yes` from repo root → `storysprout` (alias `storysprout-nine.vercel.app`). Do NOT create new Vercel projects. Frontend is untouched by this plan.
- The asset_key used by `record_image_version` is the canonical name: `character` → the character's canonical name; `scene` → the location `name`. Reference resolution MUST use the same key.

---

## Task 1: Make a manual selection sticky (store)

**Files:**
- Modify: `src/core/store.py` — `set_selected_version` (~line 213) and `add_asset_version` (~line 185).
- Test: `tests/test_selection_sticky.py` (create).

**Interfaces:**
- Produces: after `set_selected_version(book_id, asset_type, asset_key, vid)`, the asset record carries `user_selected: True`; subsequent `add_asset_version(...)` for that asset does NOT change `selected_version_id`. Before any manual select, `add_asset_version` still auto-selects the newest (unchanged default).

- [ ] **Step 1: Write the failing test** — `tests/test_selection_sticky.py`:

```python
"""A manual version selection must survive later regens (add_asset_version).

Requirement: page generation must keep using the version the user picked, so a
regen (which appends a new version) must NOT auto-hijack the selection."""
import src.core.store as store


def test_add_version_autoselects_until_user_picks():
    b = "stickyb"
    v1 = store.add_asset_version(b, "character", "X", "u1", image_hash="h1", storage_key="k1")
    assert store.get_selected_version(b, "character", "X")["id"] == v1  # default: newest
    v2 = store.add_asset_version(b, "character", "X", "u2", image_hash="h2", storage_key="k2")
    assert store.get_selected_version(b, "character", "X")["id"] == v2  # still auto-newest


def test_manual_select_sticks_across_later_add():
    b = "stickyb2"
    v1 = store.add_asset_version(b, "character", "X", "u1", image_hash="h1", storage_key="k1")
    v2 = store.add_asset_version(b, "character", "X", "u2", image_hash="h2", storage_key="k2")
    assert store.set_selected_version(b, "character", "X", v1) is True   # user picks the OLD one
    store.add_asset_version(b, "character", "X", "u3", image_hash="h3", storage_key="k3")  # a later regen
    assert store.get_selected_version(b, "character", "X")["id"] == v1, "manual pick must stick"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest tests/test_selection_sticky.py -q`
Expected: `test_manual_select_sticks_across_later_add` FAILS (selection becomes v3, not v1).

- [ ] **Step 3: Implement — set the flag in `set_selected_version`.** Inside its `_mut(assets)` mutator, after `rec["selected_version_id"] = version_id`, add `rec["user_selected"] = True`.

- [ ] **Step 4: Implement — respect the flag in `add_asset_version`.** In its `_mut(assets)` mutator, guard BOTH places that assign `rec["selected_version_id"]` (the dedup-hit branch and the new-version branch) so they only set it when the user has NOT manually selected:

```python
# new-version branch:
if not rec.get("user_selected"):
    rec["selected_version_id"] = vid
# dedup-hit branch:
if not rec.get("user_selected"):
    rec["selected_version_id"] = v["id"]
```
(Leave `out["id"] = vid`/`v["id"]` as-is — the return value is unaffected.)

- [ ] **Step 5: Run tests to confirm they pass**

Run: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest tests/test_selection_sticky.py -q`
Expected: PASS (both tests).

- [ ] **Step 6: Full suite + commit**

Run: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest -q` → ≥269 passed.
```bash
git add src/core/store.py tests/test_selection_sticky.py
git commit -m "fix(store): make a manual version selection sticky (regen no longer hijacks it)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `selected_version_image` resolver (storage)

**Files:**
- Modify: `src/core/storage.py` — add a function near `localize` / `record_image_version`.
- Test: `tests/test_selected_version_image.py` (create).

**Interfaces:**
- Produces: `storage.selected_version_image(book_id: str, asset_type: str, asset_key: str) -> str | None` — the LOCALIZED filesystem path of the selected version's immutable image (`storage.localize(selected["storage_key"])`), or `None` when no version/storage_key exists. Consumed by Tasks 3 and 4.

- [ ] **Step 1: Write the failing test** — `tests/test_selected_version_image.py`:

```python
"""selected_version_image returns the localized bytes of the SELECTED version's
immutable (content-addressed) image — the anchor that keeps every page's
reference identical."""
import src.core.store as store
import src.core.storage as storage


def test_returns_localized_selected_version(monkeypatch, tmp_path):
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    b = "selimg"
    # Seed a version whose immutable image lives locally (fake-GCS falls back local).
    key = f"{b}/characters/X_deadbeef1234.png"
    (tmp_path / key).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / key).write_bytes(b"\x89PNG\r\n\x1a\nSEL")
    store.add_asset_version(b, "character", "X", "url", image_hash="deadbeef1234", storage_key=key)

    path = storage.selected_version_image(b, "character", "X")
    assert path is not None
    assert path.endswith("X_deadbeef1234.png")
    from pathlib import Path
    assert Path(path).read_bytes() == b"\x89PNG\r\n\x1a\nSEL"


def test_returns_none_when_no_version():
    assert storage.selected_version_image("nobook", "character", "Nobody") is None
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest tests/test_selected_version_image.py -q`
Expected: FAIL with `AttributeError: module 'src.core.storage' has no attribute 'selected_version_image'`.

- [ ] **Step 3: Implement** — add to `src/core/storage.py`:

```python
def selected_version_image(book_id: str, asset_type: str, asset_key: str) -> str | None:
    """Localized path of the SELECTED version's immutable, content-addressed
    image, or None if no version is recorded. The hash key never changes
    content, so localize's cache is safe and every serverless instance resolves
    the identical bytes — the anchor for cross-page character/scene consistency."""
    from src.core.store import get_selected_version
    sel = get_selected_version(book_id, asset_type, asset_key)
    if not sel or not sel.get("storage_key"):
        return None
    return localize(sel["storage_key"])
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest tests/test_selected_version_image.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest -q` → ≥271 passed.
```bash
git add src/core/storage.py tests/test_selected_version_image.py
git commit -m "feat(storage): selected_version_image — resolve the selected version's immutable image

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Character reference reads the selected version (both page paths)

**Files:**
- Modify: `src/routes/generation.py` — `_sheets_for` (~line 29-45) AND the inline story-page loop (~line 241-269).
- Test: `tests/test_sheets_for_selected.py` (create).

**Interfaces:**
- Consumes: `storage.selected_version_image(book_id, "character", name)` (Task 2).
- Produces: both character-reference resolvers return the selected version's immutable image path when a version exists; fall back to the current `{safe}_sheet` file otherwise; the inline loop only on-the-fly-generates when NEITHER a selected version NOR a current file exists.

- [ ] **Step 1: Write the failing test** — `tests/test_sheets_for_selected.py`:

```python
"""_sheets_for must return the SELECTED version's immutable image (not the
mutable current {safe}_sheet file) so special/cover + page references are stable."""
import src.core.store as store
import src.core.storage as storage
import src.routes.generation as gen


def test_sheets_for_uses_selected_version(monkeypatch, tmp_path):
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    b = "shb"
    chars = tmp_path / b / "characters"
    chars.mkdir(parents=True)
    # A stale mutable "current" file (what the OLD code would return):
    (chars / f"{gen._safe_filename('Swallow')}_sheet.png").write_bytes(b"OLD-CURRENT")
    # The selected immutable version:
    vkey = f"{b}/characters/Swallow_abc123abc123.png"
    (tmp_path / vkey).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / vkey).write_bytes(b"SELECTED")
    store.add_asset_version(b, "character", "Swallow", "url", image_hash="abc123abc123", storage_key=vkey)

    out = gen._sheets_for(b, ["Swallow"])
    assert len(out) == 1
    from pathlib import Path
    assert Path(out[0]["sheet_path"]).read_bytes() == b"SELECTED", "must use the selected version, not the current file"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest tests/test_sheets_for_selected.py -q`
Expected: FAIL (returns `OLD-CURRENT`).

- [ ] **Step 3: Implement — `_sheets_for`.** Replace its body's per-name resolution so it prefers the selected version:

```python
def _sheets_for(book_id: str, names: list[str]) -> list[dict]:
    """Reference-sheet entries for the named characters. Prefer the SELECTED,
    immutable version (stable across pages/instances); fall back to the current
    {safe}_sheet file for characters with no recorded version yet."""
    chars_dir = GENERATED_DIR / book_id / "characters"
    out: list[dict] = []
    for name in names:
        sel = storage.selected_version_image(book_id, "character", name)
        if sel:
            out.append({"character_name": name, "sheet_path": sel})
            continue
        safe = _safe_filename(name)
        for ext in (".png", ".jpg"):
            storage.localize(f"{book_id}/characters/{safe}_sheet{ext}")
            p = chars_dir / f"{safe}_sheet{ext}"
            if p.exists():
                out.append({"character_name": name, "sheet_path": str(p)})
                break
    return out
```

- [ ] **Step 4: Implement — the inline story-page loop (generation.py ~241-269).** Prefer the selected version before the current-file localize, and only on-the-fly-generate when neither exists:

```python
        for name in target.get("characters_in_scene", []):
            sel = storage.selected_version_image(book_id, "character", name)
            if sel:
                character_sheets.append({"character_name": name, "sheet_path": sel})
                continue
            safe = _safe_filename(name)
            found = False
            for ext in (".png", ".jpg"):
                storage.localize(f"{book_id}/characters/{safe}_sheet{ext}")
                sheet_path = chars_dir / f"{safe}_sheet{ext}"
                if sheet_path.exists():
                    character_sheets.append({"character_name": name, "sheet_path": str(sheet_path)})
                    found = True
                    break
            if not found:
                c = by_canonical.get(name)
                if c:
                    chars_to_generate.append({
                        "name": name,
                        "role": c.get("role", "supporting"),
                        "gender": c.get("gender", "unknown"),
                        "appearance_description": [c.get("appearance", ""), c.get("description", "")],
                        "visual_details": c.get("visual_details", {}),
                    })
```
(The `chars_to_generate` / `generate_character_sheets` block right after stays as-is — it now runs only for characters with no version AND no current file.)

- [ ] **Step 5: Run tests to confirm they pass**

Run: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest tests/test_sheets_for_selected.py -q`
Expected: PASS.

- [ ] **Step 6: Full suite + commit**

Run: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest -q` → ≥272 passed. If any existing character-regen/page test seeded only a current file and asserted its use, add a recorded version to it OR confirm it has no selection (fallback path still works).
```bash
git add src/routes/generation.py tests/test_sheets_for_selected.py
git commit -m "fix(pages): character reference reads the selected immutable version, not the mutable current file

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Scene reference reads the selected version

**Files:**
- Modify: `src/generation/illustration.py` — `_find_scene_sheet` (~line 253-291).
- Test: `tests/test_find_scene_sheet_selected.py` (create).

**Interfaces:**
- Consumes: `storage.selected_version_image(book_id, "scene", name)` (Task 2), where `name` is the matched location name.
- Produces: `_find_scene_sheet` returns the selected scene version's immutable image when one exists; falls back to the current `{safe}_scene` file otherwise.

- [ ] **Step 1: Write the failing test** — `tests/test_find_scene_sheet_selected.py`:

```python
"""_find_scene_sheet must return the SELECTED scene version's immutable image so
every page's background reference is identical."""
import src.core.store as store
import src.core.storage as storage
import src.generation.illustration as illus


def test_find_scene_sheet_uses_selected_version(monkeypatch, tmp_path):
    monkeypatch.setattr("src.generation.illustration.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    b = "scb"
    # Location comes from the durable preprocess store.
    monkeypatch.setattr("src.core.store.load_preprocess_file",
                        lambda book_id, fn: {"locations": [{"name": "The Garden", "aliases": []}]})
    vkey = f"{b}/scenes/The_Garden_9f9f9f9f9f9f.png"
    (tmp_path / vkey).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / vkey).write_bytes(b"SELECTED-SCENE")
    store.add_asset_version(b, "scene", "The Garden", "url", image_hash="9f9f9f9f9f9f", storage_key=vkey)

    path = illus._find_scene_sheet(b, "a page set in The Garden at dusk")
    from pathlib import Path
    assert path is not None
    assert Path(path).read_bytes() == b"SELECTED-SCENE"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest tests/test_find_scene_sheet_selected.py -q`
Expected: FAIL (returns None or the current-file path).

- [ ] **Step 3: Implement** — in `_find_scene_sheet`, once a location `name` is matched, prefer the selected version before the current-file localize:

```python
        for n in all_names:
            if n.lower() in bg_lower:
                sel = storage.selected_version_image(book_id, "scene", name)
                if sel:
                    return sel
                safe = re.sub(r'[^\w\s一-鿿-]', '', name)
                safe = re.sub(r'\s+', '_', safe.strip()).lower()[:50]
                for ext in (".png", ".jpg"):
                    storage.localize(f"{book_id}/scenes/{safe}_scene{ext}")
                    path = scenes_dir / f"{safe}_scene{ext}"
                    if path.exists():
                        return str(path)
                break
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest tests/test_find_scene_sheet_selected.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest -q` → ≥273 passed.
```bash
git add src/generation/illustration.py tests/test_find_scene_sheet_selected.py
git commit -m "fix(pages): scene reference reads the selected immutable version, not the mutable current file

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Integration (联动) — selection + resolver + reference sites end-to-end

**Files:**
- Test: `tests/test_reference_consistency_integration.py` (create).

**Interfaces:**
- Consumes: `store.add_asset_version` / `set_selected_version` (Task 1), `storage.selected_version_image` (Task 2), `generation._sheets_for` (Task 3), `illustration._find_scene_sheet` (Task 4). This task adds NO production code — it proves the pieces work TOGETHER (the linkage), which per-unit tests do not.

- [ ] **Step 1: Write the integration tests** — `tests/test_reference_consistency_integration.py`:

```python
"""联动/integration: sticky selection + resolver + reference resolution together
guarantee every page's character & scene reference is the user's pick AND
identical across pages — even after a later regen appends a new version."""
from pathlib import Path

import src.core.store as store
import src.routes.generation as gen
import src.generation.illustration as illus


def _seed(tmp_path, key, data):
    p = tmp_path / key
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_character_ref_is_selected_and_stable_across_regen(monkeypatch, tmp_path):
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    b = "intb"
    _seed(tmp_path, f"{b}/characters/Swallow_1111.png", b"V1")
    _seed(tmp_path, f"{b}/characters/Swallow_2222.png", b"V2")
    v1 = store.add_asset_version(b, "character", "Swallow", "u", image_hash="1111",
                                 storage_key=f"{b}/characters/Swallow_1111.png")
    store.add_asset_version(b, "character", "Swallow", "u", image_hash="2222",
                            storage_key=f"{b}/characters/Swallow_2222.png")
    assert store.set_selected_version(b, "character", "Swallow", v1)  # user picks the FIRST

    # a later regen appends a THIRD version — must NOT change what pages use
    _seed(tmp_path, f"{b}/characters/Swallow_3333.png", b"V3")
    store.add_asset_version(b, "character", "Swallow", "u", image_hash="3333",
                            storage_key=f"{b}/characters/Swallow_3333.png")

    # two independent page-reference resolutions (simulating two pages) => same V1
    a = gen._sheets_for(b, ["Swallow"])
    c = gen._sheets_for(b, ["Swallow"])
    assert Path(a[0]["sheet_path"]).read_bytes() == b"V1"
    assert a[0]["sheet_path"] == c[0]["sheet_path"], "every page must resolve to the IDENTICAL reference"


def test_scene_ref_is_selected_not_newest(monkeypatch, tmp_path):
    monkeypatch.setattr("src.generation.illustration.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.store.load_preprocess_file",
                        lambda book_id, fn: {"locations": [{"name": "The Garden", "aliases": []}]})
    b = "intsc"
    _seed(tmp_path, f"{b}/scenes/The_Garden_aaaa.png", b"SCENE-SEL")
    _seed(tmp_path, f"{b}/scenes/The_Garden_bbbb.png", b"SCENE-NEW")
    v1 = store.add_asset_version(b, "scene", "The Garden", "u", image_hash="aaaa",
                                 storage_key=f"{b}/scenes/The_Garden_aaaa.png")
    store.set_selected_version(b, "scene", "The Garden", v1)
    store.add_asset_version(b, "scene", "The Garden", "u", image_hash="bbbb",
                            storage_key=f"{b}/scenes/The_Garden_bbbb.png")  # later regen

    p = illus._find_scene_sheet(b, "a page set in The Garden at dusk")
    assert Path(p).read_bytes() == b"SCENE-SEL", "scene ref must be the SELECTED version, not the newest"


def test_character_without_version_falls_back_to_current_file(monkeypatch, tmp_path):
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    b = "intfb"
    chars = tmp_path / b / "characters"
    chars.mkdir(parents=True)
    (chars / f"{gen._safe_filename('Nobody')}_sheet.png").write_bytes(b"CURRENT-FALLBACK")
    out = gen._sheets_for(b, ["Nobody"])  # no recorded version => old current-file path
    assert Path(out[0]["sheet_path"]).read_bytes() == b"CURRENT-FALLBACK"
```

- [ ] **Step 2: Run the integration tests to confirm they pass** (Tasks 1-4 already merged)

Run: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest tests/test_reference_consistency_integration.py -q`
Expected: PASS (3 tests). If `test_character_ref_is_selected_and_stable_across_regen` fails on stability, sticky selection (Task 1) or the resolver wiring (Task 3) is wrong — fix before proceeding.

- [ ] **Step 3: Full suite + commit**

Run: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest -q` → ≥276 passed.
```bash
git add tests/test_reference_consistency_integration.py
git commit -m "test(pages): integration — selection+resolver keep character/scene refs stable across pages & regens

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Deploy + live-verify cross-page consistency

**Files:** none (deploy + verification).

- [ ] **Step 1: Deploy backend only.** From repo root: `vercel --prod --yes` (project `storysprout` → `storysprout-nine.vercel.app`). Do NOT deploy or create any other project.

- [ ] **Step 2: Pin a character + scene.** On `storysprout-web.vercel.app`, for one book: select (click) the character version you want for each character, and the scene version for each location. This sets `user_selected` + promotes bytes.

- [ ] **Step 3: Regenerate two pages that share a character and a scene.** Confirm via the backend that both pages' generation referenced the SAME immutable image: check the deployment logs for the resolved `sheet_path` / `scene` (they should be the content-addressed `{name}_{hash}.ext`, identical across both pages), and eyeball the two pages — the character and background match.

- [ ] **Step 4: Regression guard.** Regenerate the SAME character once more (adds a new version), then regenerate a page WITHOUT re-selecting. Confirm the page still uses the previously-selected version (sticky selection held) — the character did not change.

---

## Self-Review

**Spec coverage:** Root-cause defects ① `localize` stale cache, ② mutable reference key, ③ on-the-fly regen — all three are closed by resolving to the selected immutable version (Tasks 2-4); sticky selection (Task 1) makes "the selected version" mean the user's pick. Character path (`_sheets_for` + inline loop) and scene path (`_find_scene_sheet`) both covered.

**Placeholder scan:** every step has concrete file paths, real code, exact pytest commands, and expected pass/fail. No TBDs.

**Type consistency:** `selected_version_image(book_id, asset_type, asset_key) -> str | None` defined in Task 2 is consumed with those exact args in Tasks 3-4. asset_key = canonical character name / location name, matching `record_image_version`'s keys.

**Fallback safety:** every reference site falls back to the current-file path when no selected version exists, so never-generated characters/scenes and existing tests keep working; changes are additive.

**Integration coverage:** Task 5 proves the LINKAGE (联动) — sticky selection + resolver + both reference sites working together: a page's character/scene reference stays locked to the user's pick across a later regen, two page resolutions return the identical reference, and a versionless character still falls back. Per-unit tests (Tasks 1-4) plus this integration task cover both the parts and their interaction.

**Risk:** serverless multi-instance behavior is only fully verifiable live (Task 6) — pytest proves the resolver + sticky selection + linkage, not real cross-instance caching; but because the resolved key is now immutable/content-addressed, the serverless stale-cache path is eliminated by construction.
