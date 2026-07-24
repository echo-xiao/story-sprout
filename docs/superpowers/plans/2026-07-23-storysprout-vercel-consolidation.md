# StorySprout — Merge Frontend + Backend into ONE Vercel Project

> Infra/deploy restructure. "Verification" = live deploys, not unit tests. NOTHING destructive (no project/alias deletion, no touching the live `storysprout-web`) until the merged build is verified on a throwaway target. Executed in an isolated git worktree.

**Goal:** A single Vercel project (kept as `storysprout-web`, URL `storysprout-web.vercel.app`) that builds the Next.js frontend AND serves the FastAPI backend as a Python serverless function under `/api` — then delete the separate backend project `storysprout`.

**Why it's delicate:** (a) frontend code lives in `frontend/src/` which collides with the Python `src/` at repo root; (b) one `vercel.json` must run BOTH Next.js and the Python `api/index.py` function, and `/api` routing between Next and the Python function is the fiddly risk point; (c) the `storysprout-web` project's "Root Directory" is `frontend/` and must become `.` — a Vercel DASHBOARD setting the CLI can't reliably change (a manual owner step).

**Target root layout (single project, root = repo root):**
```
/  package.json next.config.js tsconfig.json postcss.config.js tailwind.config.js vitest.config.ts next-env.d.ts   (moved from frontend/)
   public/ (from frontend/public)  app/ components/ lib/ types/ __tests__/ (from frontend/src/*  — NO src/ dir, dodges the Python src/)
   api/index.py  src/ (Python)  requirements.txt  tests/ (Python)   (UNCHANGED, stay at root)
   vercel.json (merged: Next.js + functions:{api/index.py} + rewrite /api → /api/index)
```

## Global Constraints
- The live app (`storysprout-web` + `storysprout`) must keep working untouched until the final cutover. All dev happens in a worktree; verification happens on a THROWAWAY Vercel project.
- Python `src/`, `api/`, `tests/`, `requirements.txt` DO NOT MOVE (keeps `from src.app import app` + the 300 pytest suite valid).
- `.env.local` / any secret file must NOT be committed (memory: .env never in git). Confirm `.gitignore` covers it after the move.
- Backend pytest stays green (`python -m pytest -q` → 300) — the Python side is unchanged, but re-run to prove the move didn't disturb imports.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Rollback at any pre-cutover step = discard the worktree; live app untouched.

---

## Task 1: Isolated worktree + restructure (no deploy yet)

- [ ] **1.1** Create a worktree off `main`: `git worktree add ../pbg-merge -b merge/vercel-consolidation` (use superpowers:using-git-worktrees). ALL following steps run in `../pbg-merge`.
- [ ] **1.2** Move frontend code to root, dodging the `src/` collision (git mv so history follows):
  - `git mv frontend/src/app app`; `git mv frontend/src/components components`; `git mv frontend/src/lib lib`; `git mv frontend/src/types types`; `git mv frontend/src/__tests__ __tests__`.
  - `git mv frontend/public public` (if root has no `public/`; else merge).
  - `git mv frontend/next.config.js .`; `frontend/next-env.d.ts`; `frontend/postcss.config.js`; `frontend/tailwind.config.js`; `frontend/vitest.config.ts`; `frontend/package.json`; `frontend/package-lock.json`; `frontend/tsconfig.json` → root.
- [ ] **1.3** Resolve config collisions:
  - `tsconfig.json`: change `"paths": { "@/*": ["./src/*"] }` → `"@/*": ["./*"]` (frontend imports now resolve from root).
  - `.gitignore`: MERGE frontend's ignores (`.next/`, `node_modules/`, `.env.local`, `next-env.d.ts`, `tsconfig.tsbuildinfo`) into the root `.gitignore`. Confirm `.env.local` is ignored.
  - `vitest.config.ts`: update any `src/` path references to the new root locations.
- [ ] **1.4** Merge `vercel.json` (delete `frontend/vercel.json`; write the root one):
  ```json
  {
    "$schema": "https://openapi.vercel.sh/vercel.json",
    "functions": { "api/index.py": { "maxDuration": 60 } },
    "rewrites": [ { "source": "/api/(.*)", "destination": "/api/index" } ]
  }
  ```
- [ ] **1.5** `next.config.js`: REMOVE the prod-conflicting `/api` and `/static` rewrites to `API_URL` (they proxied to the separate backend). Keep them ONLY behind a dev guard (`if (process.env.NODE_ENV === "development")`) so local `npm run dev` can still hit a locally-run `python -m uvicorn ...`; in prod, `vercel.json`'s rewrite sends `/api/*` to the Python function. (This /api routing is the #1 risk — Task 3 verifies it live.)
- [ ] **1.6** `git rm -r frontend` (now empty of moved files; ensure `frontend/.vercel` and `frontend/.env.local` are handled — do NOT commit `.env.local`).
- [ ] **1.7** Verify LOCALLY in the worktree: `npx tsc --noEmit` clean; `npx vitest run` → frontend tests pass; `python -m pytest -q` → 300 (Python unchanged); `npx next build` completes (catches most Next/import breakage before any deploy). Commit: `refactor(deploy): co-locate Next.js + Python api at repo root for a single Vercel project`.

---

## Task 2: Deploy to a THROWAWAY verify project (live app untouched)

- [ ] **2.1** From the worktree root, `vercel link` to a NEW project named `storysprout-merged-verify` (do NOT link to storysprout-web/storysprout). Set its Root Directory = `.` (default for a fresh link).
- [ ] **2.2** Copy the required runtime env vars to the verify project (GCS_SA_JSON, GCS_BUCKET, DEEPSEEK/GEMINI keys, ACCESS_CODE if any) via `vercel env add` or the dashboard — the same values the `storysprout` backend uses. (Owner may need to supply these.)
- [ ] **2.3** `vercel --prod --yes` → get the verify URL.

---

## Task 3: Live-verify the merged build on the throwaway URL

- [ ] **3.1** Frontend loads: `GET <verify-url>/` returns the app (Library landing).
- [ ] **3.2** `/api` reaches the Python function: `GET <verify-url>/api/health` → `{"status":"ok"}`; `GET <verify-url>/api/books/preprocessed` → the book list (proves the full Next→Python→GCS chain in ONE project).
- [ ] **3.3** A real generation path works end-to-end on the merged deploy (e.g. open a book, regen a page) — proves the Python function has GCS + model access and `/api` POST routing works.
- [ ] **If any of 3.1–3.3 fails:** the merge isn't ready — fix in the worktree and redeploy the verify project. The live `storysprout-web` is STILL the two-project setup and STILL works. Do not proceed.

---

## Task 4: Cutover (only after Task 3 fully passes)

- [ ] **4.1** Merge the worktree branch into `main` (`git checkout main && git merge merge/vercel-consolidation`); push.
- [ ] **4.2** OWNER MANUAL STEP (Vercel dashboard): change the `storysprout-web` project's **Root Directory** from `frontend/` to `.` (repo root). (CLI cannot reliably change this.)
- [ ] **4.3** Deploy `storysprout-web` from repo root (`vercel --prod` linked to storysprout-web) → it now serves Next.js + `/api` from one project.
- [ ] **4.4** Verify on `storysprout-web.vercel.app`: `/api/health` 200, library loads, a generation works. (Frontend `/api` no longer needs the cross-project rewrite — it's same-project now.)

---

## Task 5: Cleanup (last — only after Task 4 verified)

- [ ] **5.1** Delete the throwaway `storysprout-merged-verify` project.
- [ ] **5.2** Delete the backend project `storysprout`.
- [ ] **5.3** `git worktree remove ../pbg-merge`; delete the merge branch.
- [ ] **Result:** ONE Vercel project (`storysprout-web`) serving both; ONE domain; the backend project gone.

## Self-Review / Risk
- The `/api` routing (Next vs the Python function) is the primary risk — Task 3.2/3.3 verify it on the throwaway project before any cutover, so a routing failure never touches the live app.
- The Python side is unchanged (src/api/tests stay at root) → the 300-test suite and `from src.app import app` remain valid; Task 1.7 re-proves it.
- The only step outside CLI control is the Root Directory setting (Task 4.2) — an explicit owner action, called out.
- Every pre-cutover step is reversible by discarding the worktree; `storysprout-web`/`storysprout` are untouched until Task 4.3, and the backend isn't deleted until Task 5.2.
