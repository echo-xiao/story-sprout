# StorySprout Plan 4 — Vercel Deploy + /tmp Materialization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the app deployable on Vercel (Next.js frontend + Python serverless functions, GCS as the only data layer), replacing the Cloud Run / Docker two-process model.

**Architecture:** The FastAPI app is wrapped as a single Vercel Python serverless function (ASGI); `vercel.json` routes `/api/*` to it and everything else to the native Next.js build. Serverless has no persistent disk and no ambient GCP identity, so: (1) GCS auth uses a service-account JSON from env in BOTH the JSON store and the image layer; (2) `GENERATED_DIR` moves to `/tmp` (scratch only); (3) generation localizes its dependency images (character/scene sheets, style ref) from GCS to `/tmp` before reading them; (4) image URLs served to the browser are GCS public URLs, not local `/static` paths.

**Tech Stack:** Vercel (Next.js runtime + `@vercel/python`), FastAPI/uvicorn ASGI, google-cloud-storage, google-genai (Gemini images/QA), DeepSeek (text), Pillow, reportlab.

## Global Constraints

- Branch: `refactor/deepseek-gcs-vercel` (continue committing here; do NOT create a new branch).
- Backend tests must stay green at every task: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest -q` from repo root → **237 passed, 0 failed, 0 errors**. (The test suite uses local-fallback storage, so `/tmp`/GCS changes must not break it.)
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **GCS is the single source of truth.** `GENERATED_DIR` is per-invocation scratch — anything written there must also be persisted to GCS (existing `mirror_to_gcs` / `record_image_version` already do this) and must be re-`localize`-able on a cold start.
- **The exact model id stays `GEMINI_IMAGE_MODEL = "gemini-3-pro-image"`** (spec §2) — do not change it.
- **This plan cannot be fully verified in this session.** Tasks 1 and 8 (Gemini-auth probe, actual deploy) require the user's Vercel account, a GCS service account with bucket-only access, and a Gemini key/credential. Those tasks END in a handoff to the user, who runs the command and reports back. Do NOT claim deploy success without the user's live-run evidence.
- DRY, YAGNI, TDD where a unit is testable, frequent commits.

### Locked decisions / scope
1. **FastAPI on Vercel = one ASGI serverless function** (`api/index.py` exposing `src.app:app`), not a split-per-route design. Each request is already short.
2. **config env cleanup is minimal:** delete only `MONGODB_URI` / `MONGODB_DB` (Mongo fully removed). KEEP `REQUIRE_USER_KEY` / `ADMIN_TOKEN` for now — they are still read by inert BYOK helpers; full BYOK removal is a separate cleanup, out of scope here.
3. **Gemini image auth is an open unknown resolved in Task 1** before the rest depends on it (whether `gemini-3-pro-image` is reachable via the AI Studio `api_key` endpoint or is Vertex-only). The chosen auth path (env-only vs a code change to `make_genai_client`) is decided by Task 1's finding.
4. **Covers, per-page QA, PDF** are unchanged from Plan 3 — this plan only changes where files live and how images are authed/served.

---

## File Structure

- **Create** `api/index.py` — Vercel Python serverless entry exposing the ASGI `app`.
- **Create** `vercel.json` — build + routing (`/api/*` → Python function; rest → Next.js).
- **Modify** `src/config.py` — `GENERATED_DIR`/`DATA_DIR` env-configurable + import-time mkdir guarded; delete `MONGODB_URI`/`MONGODB_DB`.
- **Modify** `src/core/storage.py` — `_bucket()` uses `GCS_SA_JSON` (mirror `store.py`).
- **Modify** `src/app.py` — guard the import-time `GENERATED_DIR.mkdir`; skip/adjust the frontend `StaticFiles` mount on serverless (Next.js is served by Vercel, not FastAPI).
- **Modify** the illustration/regenerate path (`src/routes/generation.py` + `src/generation/illustration.py` and/or `character_sheet.py`) — localize dependency sheets/style-ref from GCS before reading local paths.
- **Modify** `src/routes/editor.py` (+ any `versioned_static_url`/`_special_image_url` helpers) — emit GCS public URLs instead of `/static/...` when `GCS_BUCKET` is set.
- **Modify** `src/gemini_backend.py` — ONLY IF Task 1 finds `gemini-3-pro-image` is Vertex-only: wire `GCS_SA_JSON` into the `vertexai=True` client.
- **Delete** `Dockerfile`, `cloudbuild.yaml`, `.dockerignore`, `start.sh`.
- **Modify** `requirements.txt` — remove `pymongo`, `pymongo[srv]`, `dnspython`, `motor`, `mcp`; fix the stale ADK header comment.
- **Modify** `docs/superpowers/HANDOFF.md` — Plan 4 status.

---

## Task 1: De-risk Gemini image auth on Vercel (investigation + decision)

Vercel has no ambient GCP identity. `src/gemini_backend.py:make_genai_client()` uses `genai.Client(vertexai=True, project, location)` for `GEMINI_BACKEND=vertex` (needs ADC — absent on Vercel) or `genai.Client(api_key=GEMINI_API_KEY)` for `api_key`. The open question: **is `gemini-3-pro-image` (Nano Banana Pro) reachable via the AI Studio `api_key` endpoint, or Vertex-only?** The answer picks the deploy auth path.

**Files:** none changed in this task (investigation + a report + a decision recorded in the plan/HANDOFF).

- [ ] **Step 1: Confirm the two client paths** in `src/gemini_backend.py:make_genai_client()` (read it) — note that `vertexai=True` takes no credentials arg today (so SA-JSON-on-Vertex is NOT currently supported).

- [ ] **Step 2: Probe model availability (needs the user's Gemini key/credential).**

Hand the user this probe to run locally with their AI Studio key:
```bash
# AI Studio api_key path — does gemini-3-pro-image answer an image request?
GEMINI_BACKEND=api_key GEMINI_API_KEY=<their key> \
/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python - <<'PY'
from google import genai
c = genai.Client(api_key=__import__("os").environ["GEMINI_API_KEY"])
r = c.models.generate_content(model="gemini-3-pro-image",
    contents="a simple red circle on white, children's book style")
print("OK:", type(r), bool(getattr(r, "candidates", None)))
PY
```
Expected outcomes:
- **Works** → auth path = env-only: set `GEMINI_BACKEND=api_key` + `GEMINI_API_KEY` in Vercel. No code change. Skip Task 1b.
- **404 / model-not-found / permission** → the model is Vertex-only → do Task 1b (wire SA JSON into the Vertex client).

- [ ] **Step 3: Record the decision** in `docs/superpowers/HANDOFF.md` (Plan 4 notes): which auth path, and whether Task 1b is needed. This gates the env list in Task 8.

### Task 1b (CONDITIONAL — only if Step 2 shows Vertex-only): wire GCS_SA_JSON into the Vertex client

**Files:** Modify `src/gemini_backend.py`.

- [ ] **Step 1:** In `make_genai_client()`, when `GEMINI_BACKEND == "vertex"`, build credentials from `GCS_SA_JSON` (reuse the same SA if it has Vertex AI User role, or a separate `GEMINI_SA_JSON`). Pattern (mirrors `store.py`):
```python
if GEMINI_BACKEND == "vertex":
    if not GCP_PROJECT:
        raise ValueError("GEMINI_BACKEND=vertex but no GCP project is set ...")
    from src.config import GCS_SA_JSON
    if GCS_SA_JSON:
        import json
        from google.oauth2 import service_account
        info = json.loads(GCS_SA_JSON)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        return genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION, credentials=creds)
    return genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
```
- [ ] **Step 2:** `pytest -q` → 237 passed (the QA tests stub the client, so this is import-safe).
- [ ] **Step 3:** Commit `feat(gemini): support GCS_SA_JSON credentials on the Vertex path (Vercel)`.

---

## Task 2: `config.py` — env-configurable GENERATED_DIR + guarded mkdir + drop Mongo env

Serverless: the repo dir is read-only; only `/tmp` is writable. The import-time `GENERATED_DIR.mkdir()` crashes cold start, and the fixed path is unwritable.

**Files:** Modify `src/config.py`.

- [ ] **Step 1: Make the scratch dir env-driven and guard the mkdir.** Replace:
```python
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
GENERATED_DIR = DATA_DIR / "generated"

GENERATED_DIR.mkdir(parents=True, exist_ok=True)
```
with:
```python
BASE_DIR = Path(__file__).parent.parent
# Per-invocation scratch. On serverless (Vercel) the repo dir is read-only —
# only /tmp is writable — so both are env-overridable (set GENERATED_DIR=/tmp/pbg
# on Vercel). GCS is the durable source of truth; this dir is just where files
# get localized/generated for the duration of one request.
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
GENERATED_DIR = Path(os.getenv("GENERATED_DIR", str(DATA_DIR / "generated")))
try:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    # Read-only FS at import (serverless cold start) — writers create it lazily.
    pass
```

- [ ] **Step 2: Delete the dead Mongo config** (Mongo fully removed in Plan 2a). First confirm nothing references them: `grep -rn "MONGODB_URI\|MONGODB_DB" src/` → expect only `config.py`. Then delete:
```python
# MongoDB
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "picture_book_generator")
```
(Keep `REQUIRE_USER_KEY` and `ADMIN_TOKEN` — still referenced by inert BYOK helpers.)

- [ ] **Step 3:** `pytest -q` → 237 passed.
- [ ] **Step 4:** Commit `refactor(config): env-configurable GENERATED_DIR (/tmp on Vercel) + drop Mongo env`.

---

## Task 3: `storage.py` — GCS auth via GCS_SA_JSON (image layer)

`store.py` (JSON layer) already authenticates with the SA JSON; `storage.py` (image bytes) does not — it uses ambient `storage.Client()` (line 41), which fails on Vercel. Mirror the pattern.

**Files:** Modify `src/core/storage.py`.

- [ ] **Step 1:** Replace `_bucket()`:
```python
def _bucket():
    """Return the GCS bucket handle, or None when GCS is not configured."""
    global _client
    if not GCS_BUCKET:
        return None
    with _client_lock:
        if _client is None:
            from google.cloud import storage
            from src.config import GCS_SA_JSON
            if GCS_SA_JSON:
                import json
                from google.oauth2 import service_account
                info = json.loads(GCS_SA_JSON)
                creds = service_account.Credentials.from_service_account_info(info)
                _client = storage.Client(project=info.get("project_id"), credentials=creds)
            else:
                _client = storage.Client()
    return _client.bucket(GCS_BUCKET)
```

- [ ] **Step 2:** `pytest -q` → 237 passed (tests run with `GCS_BUCKET` effectively unset/local-fallback, so `_bucket()` returns None and this branch isn't exercised — confirm the suite still green).
- [ ] **Step 3:** Commit `fix(storage): authenticate GCS image layer with GCS_SA_JSON (Vercel)`.

---

## Task 4: Localize dependency images before generation (the risky one)

On a cold serverless invocation `/tmp` is empty, but generation reads character/scene sheets and the style ref as LOCAL paths under `GENERATED_DIR`. `storage.localize(key)` already pulls a GCS object to the local path; `special_pages.get_style_ref` is the reference user. This task adds `localize` to the per-page/illustration path so sheets exist before they're read.

**Files:** Modify `src/routes/generation.py` (the `regenerate_segment_illustration` `_regen` closure that resolves `chars_dir / f"{safe}_sheet{ext}"`) and/or `src/generation/illustration.py` / `src/generation/character_sheet.py` where sheet paths are resolved.

- [ ] **Step 1: Read the current sheet-resolution code** in `src/routes/generation.py` (`_regen`: the loop over `target["characters_in_scene"]` that checks `sheet_path.exists()` under `chars_dir`) and in `src/generation/illustration.py:generate_illustrations` (how `character_sheets[].sheet_path` is consumed). Identify every place a dependency image is read as a local path: character sheets (`{book_id}/characters/{safe}_sheet.{ext}`), scene sheets (`{book_id}/scenes/...`), and the style ref.

- [ ] **Step 2: Before each `sheet_path.exists()` check, localize from GCS.** Apply this transform at each dependency-read site (worked example for the character sheet in `_regen`):
```python
for name in target.get("characters_in_scene", []):
    safe = _safe_filename(name)
    found = False
    for ext in (".png", ".jpg"):
        key = f"{book_id}/characters/{safe}_sheet{ext}"
        storage.localize(key)  # pull from GCS to GENERATED_DIR if not already local
        sheet_path = chars_dir / f"{safe}_sheet{ext}"
        if sheet_path.exists():
            character_sheets.append({"character_name": name, "sheet_path": str(sheet_path)})
            found = True
            break
    ...
```
(Import `from src.core import storage` if not already imported. `localize` returns None on a miss and is a no-op if the file is already local, so it's safe on local dev too.) Apply the same localize-before-read to scene sheets and, if the illustration path reads the style ref directly, reuse `special_pages.get_style_ref` (which already localizes).

- [ ] **Step 3: Verify locally** that nothing regressed: `pytest -q` → 237 passed. (Full serverless behavior is verified on deploy, Task 8.)

- [ ] **Step 4: Add a focused regression guard** if a unit seam exists: a test that `localize` is invoked for a missing local sheet given a stubbed `get_image`. If the code is too entangled to unit-test without heavy mocking, note that in the report and rely on the deploy smoke test — do not fabricate a shallow test.

- [ ] **Step 5:** Commit `feat(generation): localize character/scene sheets from GCS before drawing (serverless /tmp)`.

---

## Task 5: Serve GCS public URLs instead of `/static/…`

On Vercel the FastAPI `/static` mount (local disk) is gone; images must resolve to GCS public URLs. `storage.image_url(key)` already returns the GCS URL when `GCS_BUCKET` is set — but many sites in `editor.py` still hardcode `/static/{key}`.

**Files:** Modify `src/routes/editor.py` (and the `versioned_static_url` / `_special_image_url` helpers).

- [ ] **Step 1: Enumerate every `/static/` URL emission and the `versioned_static_url` helper.** Run:
```bash
grep -n "/static/\|versioned_static_url\|def _special_image_url" src/routes/editor.py
```
(Known sites at plan time: ~752, 774, 981, 1019, 1142, 1165, plus `versioned_static_url` at ~1049 and `_special_image_url` at ~646.)

- [ ] **Step 2: Replace each hardcoded `f"/static/{key}"` with `storage.image_url(key)`** (which returns `/static/{key}` in local-fallback mode and the GCS URL when `GCS_BUCKET` is set — so local dev is unaffected). Worked example:
```python
# before
"url": f"/static/{k}",
# after
"url": storage.image_url(k),
```
For `versioned_static_url` (which appends a cache-buster like `?v=<ts>`): keep the cache-buster, but build the base from `storage.image_url(key)` instead of `/static/{key}`, e.g. `f"{storage.image_url(key)}?v={ts}"`. Do the same inside `_special_image_url`.

- [ ] **Step 3: Adjust `src/app.py`'s frontend static mount.** The `app.mount("/", StaticFiles(...))` serving the Next.js build (app.py ~128-130) is Cloud-Run-only — on Vercel the frontend is served natively. Guard it so it only mounts when the local build dir exists AND `GCS_BUCKET`/serverless isn't the target (e.g. skip when `os.getenv("VERCEL")`), and guard the app.py:94 `GENERATED_DIR.mkdir` the same way as Task 2. Read app.py:85-135 first and make the mount conditional; keep the local-dev behavior intact.

- [ ] **Step 4: Confirm the frontend consumes absolute GCS URLs correctly.** `IllustrationPanel.tsx` builds `${API_BASE}${illustration_url}`. When `illustration_url` is already an absolute `https://storage.googleapis.com/...`, prefixing `API_BASE` (empty in prod) is fine, but verify a leading `https://` isn't double-prefixed anywhere. Grep the frontend for `${API_BASE}${` image usages and confirm they tolerate absolute URLs; note any that need a guard.

- [ ] **Step 5:** `pytest -q` → 237 passed (local-fallback keeps `/static` URLs, so existing URL-shape assertions, if any, still hold — if a test asserts a `/static/` prefix, update it to accept `storage.image_url` output and note it).

- [ ] **Step 6:** Commit `refactor(editor): emit GCS public image URLs (browser-direct) instead of /static`.

---

## Task 6: Vercel entry — `api/index.py` + `vercel.json`

**Files:** Create `api/index.py`, create `vercel.json`.

- [ ] **Step 1: Create `api/index.py`** (Vercel `@vercel/python` picks up an ASGI `app`):
```python
# Vercel Python serverless entry — exposes the FastAPI ASGI app. Vercel routes
# /api/* here (see vercel.json); each request is short (per Plan 3's per-page model).
from src.app import app  # noqa: F401  (Vercel's ASGI adapter imports `app`)
```

- [ ] **Step 2: Create `vercel.json`** routing `/api/*` to the function and the rest to the Next.js build:
```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "buildCommand": "cd frontend && npm install && npm run build",
  "outputDirectory": "frontend/.next",
  "functions": {
    "api/index.py": { "runtime": "@vercel/python", "maxDuration": 60 }
  },
  "rewrites": [
    { "source": "/api/(.*)", "destination": "/api/index" }
  ]
}
```
(Adjust `maxDuration` to 300 if on Vercel Pro and a page occasionally needs it — spec §8 risk 3. Confirm the exact `builds`/`functions` shape against current Vercel docs at deploy time; the Next.js part may auto-detect without `buildCommand`/`outputDirectory` when the project root is set to `frontend/` in the dashboard — Task 8 resolves the final wiring with the user.)

- [ ] **Step 3: Verify the ASGI import is clean** outside Vercel: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -c "import api.index"` → no error (proves the entry imports). Then `pytest -q` → 237.

- [ ] **Step 4:** Commit `feat(vercel): ASGI serverless entry + vercel.json routing`.

---

## Task 7: Delete Docker infra + clean requirements.txt

**Files:** Delete `Dockerfile`, `cloudbuild.yaml`, `.dockerignore`, `start.sh`. Modify `requirements.txt`.

- [ ] **Step 1: Delete the Cloud Run / Docker files:**
```bash
git rm Dockerfile cloudbuild.yaml .dockerignore start.sh
```

- [ ] **Step 2: Clean `requirements.txt`** — remove the Mongo/MCP deps and fix the stale header. Delete these lines:
```
pymongo>=4.6.0
pymongo[srv]>=4.6.0
dnspython>=2.6.0
motor>=3.3.0
```
and
```
# MCP integration — client for MongoDB's official MCP server (partner integration)
mcp>=1.0.0
```
and change the top comment `# Gemini Agent — pipeline orchestrated with Google ADK (Agent Builder)` to `# StorySprout — DeepSeek (text) + Gemini (images/QA) + GCS`. Keep: `google-genai`, `google-cloud-aiplatform` (Vertex client), `google-cloud-storage`, `Pillow`, `reportlab`, `fastapi`, `uvicorn`, `python-multipart`, `pydantic`, `python-dotenv`, `httpx`, `tqdm`.

- [ ] **Step 3: Confirm no residual imports** of the removed deps: `grep -rn "import pymongo\|import motor\|from motor\|import mcp\|import dns" src/` → expect nothing. `pytest -q` → 237 passed (the removed deps are already uninstalled per the environment, so a green run proves nothing imports them).

- [ ] **Step 4:** Commit `chore: remove Docker/Cloud Run infra + Mongo/MCP deps from requirements`.

---

## Task 8: Deploy handoff (user-run; live verification)

This task cannot run in-session — it needs the user's Vercel account, a GCS service account, and the Gemini credential from Task 1. Produce the checklist; the user runs it and reports back.

- [ ] **Step 1: GCS service account.** In GCP: create a service account with `Storage Object Admin` on the `picture-book-gen-assets` bucket only (least privilege); make the bucket public-read (objects served browser-direct); download its JSON key.

- [ ] **Step 2: Vercel env vars** (Project → Settings → Environment Variables):
  - `GCS_BUCKET=picture-book-gen-assets`
  - `GCS_SA_JSON=<the full SA JSON string>`
  - `GENERATED_DIR=/tmp/pbg`
  - `ACCESS_CODE=<your passcode>` (default `Caput Draconis`)
  - `DEEPSEEK_API_KEY=<key>`
  - Gemini auth per Task 1: EITHER `GEMINI_BACKEND=api_key` + `GEMINI_API_KEY=<key>` (if the probe worked) OR `GEMINI_BACKEND=vertex` + `GCP_PROJECT` + `GCS_SA_JSON` with Vertex AI User role (if Task 1b was needed).
  - `NEXT_PUBLIC_API_URL=` (empty — same origin).

- [ ] **Step 3: Deploy.** `vercel` (preview) then `vercel --prod`. If the frontend build wiring in `vercel.json` fights the auto-detection, set the Vercel project root to the repo root and let it detect Next.js under `frontend/` (or move `vercel.json` settings accordingly) — iterate with the preview URL.

- [ ] **Step 4: Live smoke test** (the verification this whole plan defers to):
  1. Open the deployed URL → enter the passcode.
  2. Paste a Gutenberg URL → preprocess completes (no cold-start crash from `GENERATED_DIR`).
  3. Editor → "Gen" a chapter → pages generate (proves: GCS image auth, `/tmp` writes, localize-before-draw, per-page endpoint under the function time limit).
  4. Images display from `storage.googleapis.com/...` (browser-direct, not `/static`).
  5. Mark a page stale (change a character) → "Gen All" regenerates it (Plan 3 fix).
  6. Library → "Download PDF" works.
  7. Open the URL without the passcode header → generation returns 403.
  - Watch Vercel function logs for: 250MB unzipped size limit (spec §8 risk 2), cold-start latency, auth errors.

- [ ] **Step 5: Update `docs/superpowers/HANDOFF.md`** with the deployed URL, the final env list, the Gemini-auth decision, and any risk that bit (function size / duration / model availability). Commit.

---

## Self-Review

**Spec coverage (spec §3–§8, HANDOFF Plan 4):**
- §3/§4 GCS-only, browser-direct images → Task 3 (auth) + Task 5 (GCS URLs). ✅
- §5 per-page short requests on serverless → Task 6 (`maxDuration`) + Plan 3 already did the per-page model. ✅
- §7 delete Docker/Cloud Run + Mongo/MCP deps + Mongo env → Task 7 + Task 2. ✅
- §8 risk 1 (GCS auth on Vercel) → Task 3 + Task 1b. ✅
- §8 risk 2 (250MB deps) → Task 8 Step 4 watch item (can't verify pre-deploy). ✅ flagged
- §8 risk 3 (single-page time limit) → Task 6 `maxDuration`. ✅
- §8 risk 4 (Vision QA is Gemini) → unchanged; Task 1 confirms Gemini auth covers it. ✅
- §8 risk 5 (`GENERATED_DIR` → `/tmp` + localize deps) → Task 2 + Task 4 (the biggest, riskiest block, exactly as the spec warns). ✅
- HANDOFF "core 4 步保留" → generation logic unchanged; only file location/auth/URLs move. ✅

**Corrections to the HANDOFF's Plan 4 assumptions (found during Plan 3/4 research), baked into this plan:**
- "代码已支持 from_service_account_info" was only true for `store.py`; `storage.py` (image layer) needed Task 3. ✅
- `GENERATED_DIR.mkdir()` at import (config.py + app.py) crashes on read-only serverless FS → Task 2 + Task 5 Step 3. ✅
- Image URLs are `/static/…` in many `editor.py` sites, not GCS → Task 5. ✅
- Gemini image auth on Vercel is a real unknown (Vertex needs identity; api_key model availability unconfirmed) → Task 1 de-risks it first. ✅

**Placeholder scan:** config/auth/infra tasks (2, 3, 6, 7) carry complete verbatim code. The two pipeline tasks (4, 5) give the exact target sites + the transform + a worked example + a read-first step, because the surrounding code is best matched fresh (giving stale verbatim code across 6+ scattered sites would risk mismatch) — each has a concrete verification grep/command. Tasks 1 and 8 are investigation/handoff by nature and say so.

**Risk acknowledgment:** Tasks 4, 5, and 8 have behavior that only manifests on a real Vercel deploy with live GCS/Gemini credentials. `pytest` (local-fallback storage) proves no regression but not serverless correctness. This is inherent to the spec's "落地时验证" framing and is called out per-task rather than hidden.
