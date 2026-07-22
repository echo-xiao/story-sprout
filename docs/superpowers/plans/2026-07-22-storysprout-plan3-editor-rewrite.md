# StorySprout Plan 3 (前段) — Editor Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the picture-book editor so whole-book / whole-chapter generation drives the live single-page endpoint in a sequential loop, and delete the dead chapter-subprocess / AgentActivityPanel / BYOK machinery it replaced.

**Architecture:** The deleted backend chapter subprocess (`chapter/generate` + progress polling + `agent-log`) is replaced by looping the already-live single-page path `POST /segment/{id}/regenerate` → poll `GET /segment/{id}/regen-status`. That per-page path already runs QA + bounded self-correction + version-append server-side; the frontend only triggers and waits. The loop logic is extracted into a unit-tested `lib/pageGen.ts` orchestrator; the editor wires it into the per-chapter "Gen" and "Gen All" buttons. BYOK (bring-your-own-key) gating is removed so the editor is always editable.

**Tech Stack:** Next.js 15 / React 19, TypeScript, axios (`lib/api.ts`), Vitest + Testing Library (jsdom), Tailwind. Backend is FastAPI (Python 3.12) — untouched by this plan except confirming tests stay green.

## Global Constraints

- Branch: `refactor/deepseek-gcs-vercel` (do **not** create a new branch; continue committing here). Do not push or deploy — that is Plan 4.
- Backend test command (must stay **237 passed, 0 failed, 0 errors**): `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest -q` run from repo root `/Users/echoooooo/Desktop/code/picture_book_generator`.
- Frontend typecheck (must be clean — no output): `cd frontend && npx tsc --noEmit`.
- Frontend tests: `cd frontend && npm test` (runs `vitest run`).
- Commit messages end with the repo's existing trailer (188 commits use it): `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. (This is a private project — the no-attribution rule is sktime-only and does NOT apply here.)
- Vitest picks up any `**/*.test.ts(x)` under `frontend/src`; alias `@` → `frontend/src`.
- The access gate only guards `POST` paths ending in generation suffixes; `GET /api/book/{id}/pdf` is a read and passes through un-gated (so a plain `<a download>` works).
- DRY, YAGNI, TDD, frequent commits.

### Design decisions locked for this plan (confirmed with the user)

1. **Scene-background field:** keep the user-initiated "Generate" button (and its `generateSceneBackground` api fn + `POST …/background` route). Delete ONLY the silent 2s debounce auto-regen (`triggerSceneBackgroundRegen` + timer + `regenningBg`).
2. **Batch generation scope:** "Gen chapter" / "Gen All" only generate pages that have **no illustration OR are flagged stale** — already-good pages are skipped (saves money; mirrors the old "skip complete chapters" behavior at page granularity).
3. **characters_in_scene edits:** changing characters just marks the segment dirty; the user regenerates by clicking "Save & Regen" (manual, per spec §11.2 default — no auto-anything).
4. **`lib/agents.ts` is KEPT** (correction to HANDOFF): `AGENT_META` + `PREPROCESS_STEPS` still power the live preprocess UI (`GenerationProgress.tsx`, `PreprocessLoadingScreen.tsx`). Only the *editor's* usage of `AGENT_META` is removed.
5. **`getConfig` deletion + Create-page (`UploadForm.tsx`) BYOK cleanup are DEFERRED** to a later Create-page task (`getConfig` is still used by `UploadForm`, so it is not dead yet). This plan removes only the three truly-dead api fns: `generateChapter`, `getChapterProgress`, `getAgentLog`.
6. **Covers are out of scope for the batch loop:** the per-page endpoint does not create book/chapter/back covers. Covers remain generated via their existing per-item regenerate flow (`handleRegenSpecial`, unchanged). Batch "Gen All" generates pages only. (Documented, not a silent gap.)

---

## File Structure

- **Create** `frontend/src/lib/pageGen.ts` — sequential per-page generation orchestrator (pure, testable).
- **Create** `frontend/src/__tests__/pageGen.test.ts` — unit tests for the orchestrator.
- **Modify** `frontend/src/app/editor/[bookId]/page.tsx` — remove BYOK; replace chapter-subprocess generation with the per-page loop; remove AgentActivityPanel wiring; remove the scene-background debounce.
- **Modify** `frontend/src/lib/api.ts` — delete `generateChapter`, `getChapterProgress`, `getAgentLog`.
- **Modify** `frontend/src/components/BookLibrary.tsx` — add a "Download PDF" link per generated book.
- **Delete** `frontend/src/components/editor/AgentActivityPanel.tsx`, `frontend/src/__tests__/AgentActivityPanel.test.tsx`, `frontend/src/lib/progress.ts`, `frontend/src/__tests__/progress.test.ts`.
- **Modify** `docs/superpowers/HANDOFF.md` — mark Plan 3 前段 done + record the corrections.

---

## Task 1: Sequential page-generation orchestrator (`lib/pageGen.ts`)

**Files:**
- Create: `frontend/src/lib/pageGen.ts`
- Test: `frontend/src/__tests__/pageGen.test.ts`

**Interfaces:**
- Consumes: nothing (pure module; all IO injected).
- Produces (the editor in Task 3 relies on exactly these names/types):
  - `interface PageGenIO { regenerate(segId:number):Promise<unknown>; pollStatus(segId:number):Promise<{status:string; error?:string|null}>; sleep(ms:number):Promise<void>; isCancelled():boolean }`
  - `interface PageGenResult { status: "complete"|"error"|"cancelled"|"timeout"; error?: string }`
  - `interface BatchProgress { done:number; total:number; segId:number }`
  - `interface BatchHooks { onProgress?(p:BatchProgress):void; onPageDone?(segId:number, result:PageGenResult):void|Promise<void> }`
  - `interface BatchResult { completed:number; failed:Array<{segId:number; error:string}>; cancelled:boolean }`
  - `generateOnePage(segId:number, io:PageGenIO, opts?:{pollMs?:number; timeoutMs?:number}): Promise<PageGenResult>`
  - `generatePagesSequential(segIds:number[], io:PageGenIO, hooks?:BatchHooks, opts?:{pollMs?:number; timeoutMs?:number}): Promise<BatchResult>`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/__tests__/pageGen.test.ts`:

```ts
import { describe, it, expect, vi } from "vitest";
import { generateOnePage, generatePagesSequential, type PageGenIO } from "@/lib/pageGen";

function makeIO(overrides: Partial<PageGenIO> = {}): PageGenIO {
  return {
    regenerate: vi.fn().mockResolvedValue({ status: "regenerating" }),
    pollStatus: vi.fn().mockResolvedValue({ status: "complete" }),
    sleep: vi.fn().mockResolvedValue(undefined),
    isCancelled: () => false,
    ...overrides,
  };
}

describe("generateOnePage", () => {
  it("returns complete once the marker reports complete", async () => {
    const pollStatus = vi.fn()
      .mockResolvedValueOnce({ status: "generating" })
      .mockResolvedValueOnce({ status: "complete" });
    const io = makeIO({ pollStatus });
    const r = await generateOnePage(1, io, { pollMs: 10, timeoutMs: 1000 });
    expect(r.status).toBe("complete");
    expect(io.regenerate).toHaveBeenCalledWith(1);
    expect(pollStatus).toHaveBeenCalledTimes(2);
  });

  it("surfaces a backend error marker", async () => {
    const io = makeIO({ pollStatus: vi.fn().mockResolvedValue({ status: "error", error: "no quota" }) });
    const r = await generateOnePage(1, io, { pollMs: 10, timeoutMs: 1000 });
    expect(r).toEqual({ status: "error", error: "no quota" });
  });

  it("returns error when the regenerate request throws", async () => {
    const io = makeIO({ regenerate: vi.fn().mockRejectedValue({ response: { data: { detail: "already regenerating" } } }) });
    const r = await generateOnePage(1, io);
    expect(r.status).toBe("error");
    expect(r.error).toBe("already regenerating");
  });

  it("stops as cancelled when isCancelled flips true", async () => {
    let cancelled = false;
    const io = makeIO({
      pollStatus: vi.fn().mockResolvedValue({ status: "generating" }),
      isCancelled: () => cancelled,
      sleep: vi.fn().mockImplementation(async () => { cancelled = true; }),
    });
    const r = await generateOnePage(1, io, { pollMs: 10, timeoutMs: 1000 });
    expect(r.status).toBe("cancelled");
  });

  it("times out when the marker never completes", async () => {
    const io = makeIO({ pollStatus: vi.fn().mockResolvedValue({ status: "generating" }) });
    const r = await generateOnePage(1, io, { pollMs: 100, timeoutMs: 300 });
    expect(r.status).toBe("timeout");
  });
});

describe("generatePagesSequential", () => {
  it("generates every page and reports progress + completion per page", async () => {
    const io = makeIO();
    const onProgress = vi.fn();
    const onPageDone = vi.fn();
    const r = await generatePagesSequential([1, 2, 3], io, { onProgress, onPageDone }, { pollMs: 1, timeoutMs: 100 });
    expect(r).toEqual({ completed: 3, failed: [], cancelled: false });
    expect(onProgress).toHaveBeenCalledTimes(3);
    expect(onPageDone).toHaveBeenCalledTimes(3);
  });

  it("continues past a failed page and collects the error", async () => {
    const pollStatus = vi.fn()
      .mockResolvedValueOnce({ status: "complete" })
      .mockResolvedValueOnce({ status: "error", error: "boom" })
      .mockResolvedValueOnce({ status: "complete" });
    const io = makeIO({ pollStatus });
    const r = await generatePagesSequential([1, 2, 3], io, {}, { pollMs: 1, timeoutMs: 100 });
    expect(r.completed).toBe(2);
    expect(r.failed).toEqual([{ segId: 2, error: "boom" }]);
    expect(r.cancelled).toBe(false);
  });

  it("stops early when cancelled", async () => {
    let calls = 0;
    const io = makeIO({ isCancelled: () => calls++ >= 1 });
    const r = await generatePagesSequential([1, 2, 3], io, {}, { pollMs: 1, timeoutMs: 100 });
    expect(r.cancelled).toBe(true);
    expect(r.completed).toBeLessThan(3);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/__tests__/pageGen.test.ts`
Expected: FAIL — `Failed to resolve import "@/lib/pageGen"` (module doesn't exist yet).

- [ ] **Step 3: Write the orchestrator**

Create `frontend/src/lib/pageGen.ts`:

```ts
// Sequential per-page generation orchestrator. The editor drives whole-book /
// whole-chapter generation by looping the single-page regenerate endpoint
// (POST /segment/{id}/regenerate → poll /regen-status), replacing the deleted
// chapter subprocess + progress polling. Extracted here so the loop logic is
// unit-testable in isolation from the React editor.

export interface PageGenIO {
  /** Trigger backend regeneration for one segment (POST …/regenerate). */
  regenerate: (segId: number) => Promise<unknown>;
  /** Read the regen marker (GET …/regen-status). */
  pollStatus: (segId: number) => Promise<{ status: string; error?: string | null }>;
  /** Await ms — injected so tests run without real timers. */
  sleep: (ms: number) => Promise<void>;
  /** True when the caller wants to abort (unmount / user "Stop"). */
  isCancelled: () => boolean;
}

export interface PageGenResult {
  status: "complete" | "error" | "cancelled" | "timeout";
  error?: string;
}

export interface BatchProgress {
  /** 0-based index of the page currently being generated. */
  done: number;
  total: number;
  segId: number;
}

export interface BatchHooks {
  onProgress?: (p: BatchProgress) => void;
  onPageDone?: (segId: number, result: PageGenResult) => void | Promise<void>;
}

export interface BatchResult {
  completed: number;
  failed: Array<{ segId: number; error: string }>;
  cancelled: boolean;
}

const DEFAULT_POLL_MS = 5000;
const DEFAULT_TIMEOUT_MS = 600000; // matches the backend's 600s request ceiling

/**
 * Generate a single page: fire the regenerate request, then poll the status
 * marker until it reports complete/error, the caller cancels, or we hit the
 * timeout. The backend runs QA + bounded self-correction itself — this only
 * triggers and waits.
 */
export async function generateOnePage(
  segId: number,
  io: PageGenIO,
  opts: { pollMs?: number; timeoutMs?: number } = {},
): Promise<PageGenResult> {
  const pollMs = opts.pollMs ?? DEFAULT_POLL_MS;
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  if (io.isCancelled()) return { status: "cancelled" };

  try {
    await io.regenerate(segId);
  } catch (e: any) {
    return { status: "error", error: e?.response?.data?.detail || e?.message || String(e) };
  }

  let waited = 0;
  while (waited < timeoutMs) {
    if (io.isCancelled()) return { status: "cancelled" };
    await io.sleep(pollMs);
    waited += pollMs;
    let st: { status: string; error?: string | null };
    try {
      st = await io.pollStatus(segId);
    } catch {
      continue; // transient fetch error — keep polling
    }
    if (st.status === "complete") return { status: "complete" };
    if (st.status === "error") return { status: "error", error: st.error || "generation failed" };
  }
  return { status: "timeout" };
}

/**
 * Generate many pages one at a time, in order. Continues past a failed page
 * (collecting the error) but stops immediately when the caller cancels.
 */
export async function generatePagesSequential(
  segIds: number[],
  io: PageGenIO,
  hooks: BatchHooks = {},
  opts: { pollMs?: number; timeoutMs?: number } = {},
): Promise<BatchResult> {
  const failed: BatchResult["failed"] = [];
  let completed = 0;

  for (let i = 0; i < segIds.length; i++) {
    if (io.isCancelled()) return { completed, failed, cancelled: true };
    const segId = segIds[i];
    hooks.onProgress?.({ done: i, total: segIds.length, segId });

    const result = await generateOnePage(segId, io, opts);
    if (result.status === "cancelled") return { completed, failed, cancelled: true };
    if (result.status === "complete") completed++;
    else failed.push({ segId, error: result.error || result.status });

    await hooks.onPageDone?.(segId, result);
  }
  return { completed, failed, cancelled: false };
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/__tests__/pageGen.test.ts`
Expected: PASS (8 tests).

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
cd /Users/echoooooo/Desktop/code/picture_book_generator
git add frontend/src/lib/pageGen.ts frontend/src/__tests__/pageGen.test.ts
git commit -m "$(cat <<'EOF'
feat(frontend): sequential per-page generation orchestrator

Extract the single-page regenerate → poll loop into a testable lib so the
editor can drive whole-chapter / whole-book generation by looping it (replacing
the deleted chapter subprocess).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Remove BYOK gating from the editor

Removes the "view-only unless you paste a Gemini key" machinery so the editor is always editable (`canEdit` was already effectively true — `REQUIRE_USER_KEY` defaults off). Self-contained: touches only the editor file. Leaves the chapter-generation refs (`generatingChapter`, `genAllChapters`) intact — those go in Task 3.

**Files:**
- Modify: `frontend/src/app/editor/[bookId]/page.tsx`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing later tasks depend on (pure deletion). After this task `canEdit` no longer exists in the file.

- [ ] **Step 1: Remove the `getConfig` import**

In the api import block (around line 28), delete the line `  getConfig,`. Leave every other imported name.

- [ ] **Step 2: Remove the BYOK state block**

Delete the entire block (currently lines ~135–148):

```tsx
  // Agent Activity Panel (open by default so the live agent log is always visible)
  const [agentPanelOpen, setAgentPanelOpen] = useState(false);

  // BYOK: the editor is read-only unless the visitor supplied their own Gemini
  // key (generation endpoints also enforce this server-side with a 403).
  const [hasKey] = useState(() => typeof window !== "undefined" && !!localStorage.getItem("pbg_api_key"));
  const [keyInput, setKeyInput] = useState("");
  // The BYOK gate is only enforced when the backend says so (REQUIRE_USER_KEY).
  // Default off → editor is fully usable without a key (project/Vertex billing).
  const [requireKey, setRequireKey] = useState(false);
  useEffect(() => {
    getConfig().then(c => setRequireKey(!!c.require_user_key)).catch(() => {});
  }, []);
  const canEdit = hasKey || !requireKey;
```

Replace it with just the agent-panel state (which Task 3 removes — keep it here for now so the file stays compiling between tasks):

```tsx
  // Agent Activity Panel (open by default so the live agent log is always visible)
  const [agentPanelOpen, setAgentPanelOpen] = useState(false);
```

- [ ] **Step 3: Drop the `!canEdit` guard in the auto-simplify effect**

Find (around line 518):

```tsx
    if (selectedSegId < 0 || !selectedSegment || selectedSegment.simplified_text || !canEdit) return;
```
Replace with:
```tsx
    if (selectedSegId < 0 || !selectedSegment || selectedSegment.simplified_text) return;
```
And its dependency array (around line 534):
```tsx
  }, [selectedSegId, canEdit]);
```
Replace with:
```tsx
  }, [selectedSegId]);
```

- [ ] **Step 4: Remove the BYOK banner**

Delete the entire banner block (currently lines ~1017–1042), the JSX starting at `{requireKey && !hasKey && (` and ending at its closing `)}`:

```tsx
      {requireKey && !hasKey && (
        <div className="bg-amber-50 border-b border-amber-200 ...">
          ...
        </div>
      )}
```

(Remove the whole block. The next sibling is the `{/* Body: tab content + persistent live Agent Activity column ... */}` div.)

- [ ] **Step 5: Replace the remaining `canEdit` references**

There are five `canEdit`-prop / `!canEdit` references left. Change each:

- `<StyleReferenceWidget bookId={bookId} canEdit={canEdit} />` → `canEdit={true}`
- `<CharacterManagement ... canGenerate={canEdit} ...>` → `canGenerate={true}`
- `<SceneManagement ... canGenerate={canEdit} ...>` → `canGenerate={true}`
- `<SpecialPageView canGenerate={canEdit} ...>` → `canGenerate={true}`
- The "Gen All" button `disabled={genAllChapters || generatingChapter !== null || !canEdit}` → `disabled={genAllChapters || generatingChapter !== null}`
- The per-chapter "Gen" button `disabled={generatingChapter !== null || !canEdit}` → `disabled={generatingChapter !== null}`
- The "Save & Regen" button `disabled={regenerating || saving || !canEdit}` → `disabled={regenerating || saving}`

Verify none remain: `cd frontend && grep -n "canEdit" src/app/editor/\[bookId\]/page.tsx` should return **nothing**.

- [ ] **Step 6: Typecheck + tests**

Run: `cd frontend && npx tsc --noEmit && npm test`
Expected: tsc clean; all existing tests pass (still includes AgentActivityPanel + progress tests — removed in Task 3).

- [ ] **Step 7: Commit**

```bash
cd /Users/echoooooo/Desktop/code/picture_book_generator
git add frontend/src/app/editor/\[bookId\]/page.tsx
git commit -m "$(cat <<'EOF'
refactor(frontend): remove BYOK gating from the editor — always editable

Delete the view-only-unless-you-paste-a-key banner + hasKey/keyInput/requireKey/
canEdit state (REQUIRE_USER_KEY is gone; the single shared passcode is the only
gate now).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Replace chapter-subprocess generation with the per-page loop

The core of the rewrite. Removes every reference to the deleted chapter subprocess (`generateChapter`, `getChapterProgress`), the AgentActivityPanel, and the now-dead `lib/progress.ts`; wires the Task 1 orchestrator into the per-chapter "Gen" button and "Gen All". Each removed page is generated by looping `POST /segment/{id}/regenerate` (QA + self-correction happen server-side per page).

**Files:**
- Modify: `frontend/src/app/editor/[bookId]/page.tsx`
- Modify: `frontend/src/lib/api.ts`
- Delete: `frontend/src/components/editor/AgentActivityPanel.tsx`
- Delete: `frontend/src/__tests__/AgentActivityPanel.test.tsx`
- Delete: `frontend/src/lib/progress.ts`
- Delete: `frontend/src/__tests__/progress.test.ts`

**Interfaces:**
- Consumes from Task 1: `generateOnePage`, `generatePagesSequential`, `type PageGenIO`.
- Consumes existing api fns (already imported): `regenerateSegment(bookId, segId)`, `getRegenStatus(bookId, segId)`, `getChapterSegments`, `getStalePages`, `getCharacters`, `getSpecialPages`.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Delete the four dead files**

```bash
cd /Users/echoooooo/Desktop/code/picture_book_generator/frontend
git rm src/components/editor/AgentActivityPanel.tsx \
       src/__tests__/AgentActivityPanel.test.tsx \
       src/lib/progress.ts \
       src/__tests__/progress.test.ts
```

- [ ] **Step 2: Delete the three dead api fns**

In `frontend/src/lib/api.ts`, delete these three exported functions in full:

```ts
export async function generateChapter(bookId: string, chapterIdx: number) {
  const { data } = await api.post(`/book/${bookId}/chapter/${chapterIdx}/generate`);
  return data;
}

export async function getChapterProgress(bookId: string, chapterIdx: number) {
  const { data } = await api.get(`/book/${bookId}/chapter/${chapterIdx}/progress`);
  return data;
}

export async function getAgentLog(bookId: string, chapterIdx: number) {
  const { data } = await api.get(`/book/${bookId}/chapter/${chapterIdx}/agent-log`);
  return data as Array<{
    ts: number;
    agent: string;
    action: string;
    detail: string;
    result: string;
    status: string;
  }>;
}
```

(Keep `getConfig`, `generateSceneBackground`, and everything else.)

- [ ] **Step 3: Fix the editor imports**

In `frontend/src/app/editor/[bookId]/page.tsx`:

Line 6 — remove `Activity` from the lucide import:
```tsx
import { RefreshCw, Save, Users, MapPin, Smile, BookOpen, Image, Activity } from "lucide-react";
```
→
```tsx
import { RefreshCw, Save, Users, MapPin, Smile, BookOpen, Image } from "lucide-react";
```

In the api import block, remove these two lines:
```tsx
  generateChapter,
  getChapterProgress,
```
(Keep `regenerateSegment`, `getRegenStatus`, `getRegenActive`, `generateSceneBackground`, etc.)

Remove these three component/lib imports:
```tsx
import AgentActivityPanel from "@/components/editor/AgentActivityPanel";
```
```tsx
import { AGENT_META } from "@/lib/agents";
```
```tsx
import { isSegmentPageStep } from "@/lib/progress";
```

Add the orchestrator import next to the other `@/lib` imports:
```tsx
import { generateOnePage, generatePagesSequential, type PageGenIO } from "@/lib/pageGen";
```

- [ ] **Step 4: Replace the chapter-generation state with batch-generation state**

Remove these state/ref declarations wherever they appear:
```tsx
  const [generatingChapter, setGeneratingChapter] = useState<number | null>(null);
  const [chapterProgress, setChapterProgress] = useState<{ progress: number; current_step: string } | null>(null);
  const [genAllChapters, setGenAllChapters] = useState(false);
  const genAllChaptersRef = useRef(false);
```
```tsx
  // Agent Activity Panel (open by default so the live agent log is always visible)
  const [agentPanelOpen, setAgentPanelOpen] = useState(false);
```
```tsx
  // How many pages the chapter generation has finished so far ...
  const lastCompletedPagesRef = useRef(0);
```

Add, in their place (near the other generation state), the new batch state:
```tsx
  // Batch (whole-chapter / whole-book) generation drives the single-page
  // regenerate endpoint in a sequential loop (replaces the chapter subprocess).
  const [genRunning, setGenRunning] = useState(false);
  const [genProgress, setGenProgress] = useState<{ done: number; total: number; segId: number; chIdx: number } | null>(null);
  const genCancelRef = useRef(false);
```

- [ ] **Step 5: Fix the unmount cleanup effect**

In the unmount effect, replace the Gen-All-loop stop line:
```tsx
      // Stop the Gen All LOOP too, not just its polls — otherwise after the
      // user navigates away each poll resolves early and the loop fires
      // generateChapter for every remaining chapter in quick succession.
      genAllChaptersRef.current = false;
```
with:
```tsx
      // Stop any running batch generation loop on unmount.
      genCancelRef.current = true;
```
(Leave the `sceneRegenTimer` clearTimeout line here for now — Task 4 removes it.)

- [ ] **Step 6: Delete the chapter-progress polling effect and the agent-panel auto-open effect**

Delete the entire `useEffect` that polls `getChapterProgress` (the block beginning `// Poll progress when generating` and its `}, [generatingChapter, bookId, selectedChapter, genAllChapters]);`).

Delete the entire `useEffect` beginning `// Auto-open the agent activity panel ...` ending `}, [generatingChapter]);`.

- [ ] **Step 7: Replace `handleGenAllChapters` with the new batch handlers**

Delete the whole `handleGenAllChapters` function (from `// Gen All Chapters: generate all chapters sequentially` through its closing `};`). Replace it with:

```tsx
  // Build the injected IO for the page-generation orchestrator. isCancelled
  // covers both the "Stop" button and unmount.
  const makeIO = (): PageGenIO => ({
    regenerate: (segId) => regenerateSegment(bookId, segId),
    pollStatus: (segId) => getRegenStatus(bookId, segId),
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
    isCancelled: () => genCancelRef.current || unmountedRef.current,
  });

  // Generate every missing/stale page in one chapter, sequentially, streaming
  // each finished image in as it completes. Shared by the per-chapter "Gen"
  // button and "Gen All".
  const runChapterGeneration = async (chIdx: number): Promise<void> => {
    const data = await getChapterSegments(bookId, chIdx).catch(() => null);
    const segs: Segment[] = data?.segments || [];
    // Only fill pages with no illustration + ones flagged stale — re-running
    // never re-burns money on pages that are already good.
    const targets = segs
      .filter((s) => !s.illustration_url || staleSegIds.has(s.id))
      .map((s) => s.id);
    if (targets.length === 0) return;
    await generatePagesSequential(
      targets,
      makeIO(),
      {
        onProgress: (p) => setGenProgress({ done: p.done, total: p.total, segId: p.segId, chIdx }),
        onPageDone: async () => {
          if (selectedChapterRef.current !== chIdx) return;
          const fresh = await getChapterSegments(bookId, chIdx).catch(() => null);
          if (fresh && selectedChapterRef.current === chIdx) applyServerSegments(fresh.segments || []);
        },
      },
    );
    if (selectedChapterRef.current === chIdx) refreshStale(chIdx);
  };

  // Per-chapter "Gen" button.
  const handleGenChapter = async (chIdx: number) => {
    if (genRunning) return;
    genCancelRef.current = false;
    setGenRunning(true);
    setGenProgress({ done: 0, total: 0, segId: -1, chIdx });
    try {
      await runChapterGeneration(chIdx);
    } catch (e: any) {
      if (!unmountedRef.current) alert(`Generation failed: ${e?.response?.data?.detail || e?.message || e}`);
    } finally {
      setGenRunning(false);
      setGenProgress(null);
      // Character sheets may have been generated on demand — refresh them.
      getCharacters(bookId).then((d) => setSheets(d.sheets || {})).catch(() => {});
    }
  };

  // "Gen All" button — every chapter, in order.
  const handleGenAll = async () => {
    if (genRunning) return;
    genCancelRef.current = false;
    setGenRunning(true);
    const chapterIndices = Object.keys(chapters).map(Number).sort((a, b) => a - b);
    try {
      for (const chIdx of chapterIndices) {
        if (genCancelRef.current || unmountedRef.current) break;
        await runChapterGeneration(chIdx);
      }
    } catch (e: any) {
      if (!unmountedRef.current) alert(`Generation failed: ${e?.response?.data?.detail || e?.message || e}`);
    } finally {
      setGenRunning(false);
      setGenProgress(null);
      getCharacters(bookId).then((d) => setSheets(d.sheets || {})).catch(() => {});
      getSpecialPages(bookId).then((d) => setSpecialPages(d.pages || [])).catch(() => {});
      refreshStale(selectedChapterRef.current);
    }
  };

  // Stop button — cooperative cancel; the loop checks genCancelRef between pages.
  const handleStopGen = () => { genCancelRef.current = true; };
```

- [ ] **Step 8: Refactor `handleRegenerate` (single page) to use the orchestrator**

Replace the whole `handleRegenerate` function body (the single-page "Save & Regen" handler that currently has an inline poll loop) with this DRY version that reuses `generateOnePage`:

```tsx
  // Regenerate the currently-selected illustration (Save & Regen). Same
  // single-page path the batch loop uses; the backend runs QA + self-correction.
  const handleRegenerate = async () => {
    if (!selectedSegment || selectedChapter === null) return;
    const segId = selectedSegment.id;
    const chIdx = selectedChapter;
    setRegenerating(true);
    try {
      // Persist edits first so the illustration embeds them.
      await updateSegment(bookId, segId, {
        simplified_text: selectedSegment.simplified_text,
        characters_in_scene: selectedSegment.characters_in_scene,
        character_actions: selectedSegment.character_actions,
        scene_background: selectedSegment.scene_background,
        scene_summary: selectedSegment.scene_summary,
        sentiment: selectedSegment.sentiment,
      });
      dirtySegIds.current.delete(segId);

      const result = await generateOnePage(segId, makeIO());
      if (result.status === "error") {
        alert(`Regenerate failed: ${result.error || "unknown error"}`);
      } else if (result.status === "timeout") {
        if (!unmountedRef.current) alert("Still generating in the background — reload the page in a minute to see the result.");
      } else if (result.status === "complete" && selectedChapterRef.current === chIdx) {
        const data = await getChapterSegments(bookId, chIdx);
        if (selectedChapterRef.current === chIdx) applyServerSegments(data.segments || []);
      }
      if (selectedChapterRef.current === chIdx) refreshStale(chIdx);
    } catch (e: any) {
      console.error("Regenerate failed:", e);
      alert(`Regenerate failed: ${e?.response?.data?.detail || e?.message || e}`);
    } finally {
      setRegenerating(false);
    }
  };
```

(Note: `makeIO` must be declared above `handleRegenerate` — it is, per Step 7 ordering. If your file has `handleRegenerate` above the Step-7 block, move `makeIO` up so it precedes both. `const` function expressions are not hoisted.)

- [ ] **Step 9: Remove the header "Agents" button**

In the header, delete the entire `<button onClick={() => setAgentPanelOpen(!agentPanelOpen)} ...>` block (the "Agent Activity Indicator" — it references `generatingChapter`, `AGENT_META`, `chapterProgress`, `Activity`). Leave the `StyleReferenceWidget` before it and the `View Book` `<a>` after it.

- [ ] **Step 10: Rewrite the Chapters-panel header (Gen All + progress + Stop)**

Replace the "Gen All Chapters" header block:

```tsx
          {/* Gen All Chapters */}
          <div className="px-3 py-2 text-[10px] font-bold text-gray-400 uppercase tracking-wider bg-cream/50 flex items-center justify-between">
            <span>Chapters ({Object.keys(chapters).length})</span>
            <div className="flex items-center gap-1">
              {genAllChapters && (
                <span className="text-[9px] text-amber-600 animate-pulse">
                  Ch {(generatingChapter ?? 0) + 1}/{Object.keys(chapters).length}
                </span>
              )}
              <button
                onClick={handleGenAllChapters}
                disabled={genAllChapters || generatingChapter !== null}
                className="text-[9px] bg-coral/80 text-white px-2 py-0.5 rounded hover:bg-coral transition-colors disabled:opacity-50"
              >
                {genAllChapters ? "Running..." : "Gen All"}
              </button>
            </div>
          </div>
```

with:

```tsx
          {/* Gen All Chapters */}
          <div className="px-3 py-2 text-[10px] font-bold text-gray-400 uppercase tracking-wider bg-cream/50 flex items-center justify-between">
            <span>Chapters ({Object.keys(chapters).length})</span>
            <div className="flex items-center gap-1">
              {genRunning && genProgress && (
                <span className="text-[9px] text-amber-600 animate-pulse">
                  {genProgress.done + 1}/{genProgress.total || "?"}
                </span>
              )}
              {genRunning ? (
                <button
                  onClick={handleStopGen}
                  className="text-[9px] bg-gray-400 text-white px-2 py-0.5 rounded hover:bg-gray-500 transition-colors"
                >
                  Stop
                </button>
              ) : (
                <button
                  onClick={handleGenAll}
                  disabled={regenerating}
                  className="text-[9px] bg-coral/80 text-white px-2 py-0.5 rounded hover:bg-coral transition-colors disabled:opacity-50"
                >
                  Gen All
                </button>
              )}
            </div>
          </div>
```

- [ ] **Step 11: Rewrite the per-chapter row (Gen button + inline progress)**

In the chapter-row `<div>`, replace the highlight class that references `generatingChapter`:
```tsx
                  className={`flex items-center border-b border-gray-100 transition-colors ${
                    generatingChapter === +chIdx
                      ? "bg-amber-50 border-l-2 border-l-amber-400"
                      : selectedChapter === +chIdx
                      ? "bg-coral/10"
                      : "hover:bg-peach/30"
                  }`}
```
with:
```tsx
                  className={`flex items-center border-b border-gray-100 transition-colors ${
                    genRunning && genProgress?.chIdx === +chIdx
                      ? "bg-amber-50 border-l-2 border-l-amber-400"
                      : selectedChapter === +chIdx
                      ? "bg-coral/10"
                      : "hover:bg-peach/30"
                  }`}
```

Then replace the whole `{generatingChapter === +chIdx ? (…progress bar…) : (…Gen button…)}` ternary with:

```tsx
                  {genRunning && genProgress?.chIdx === +chIdx ? (
                    <div className="mr-2 w-28">
                      <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-amber-400 rounded-full transition-all duration-500"
                          style={{ width: `${genProgress.total ? ((genProgress.done + 1) / genProgress.total) * 100 : 0}%` }}
                        />
                      </div>
                      <p className="text-[8px] text-amber-600 text-center mt-0.5 animate-pulse">
                        Page {genProgress.done + 1}/{genProgress.total}
                      </p>
                    </div>
                  ) : (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleGenChapter(+chIdx);
                      }}
                      disabled={genRunning || regenerating}
                      className="w-8 h-6 mr-1 text-[9px] bg-coral/80 text-white rounded hover:bg-coral transition-colors disabled:opacity-50 shrink-0"
                      title="Generate illustrations for this chapter"
                    >
                      Gen
                    </button>
                  )}
```

- [ ] **Step 12: Rewrite the segment dot (remove `isSegmentPageStep`)**

Replace the segment-button `isGenerating` computation:
```tsx
                    const isGenerating = generatingChapter === +chIdx && isSegmentPageStep(chapterProgress?.current_step, idx);
                    const hasIllustration = !!seg.illustration_url;
```
with:
```tsx
                    const isGenerating = genRunning && genProgress?.chIdx === +chIdx && genProgress?.segId === seg.id;
                    const hasIllustration = !!seg.illustration_url;
```

Then replace the dot's className + title (which referenced `completed_pages`):
```tsx
                        <span
                          className={`w-2 h-2 rounded-full shrink-0 ${
                            isGenerating
                              ? (idx < ((chapterProgress as any)?.completed_pages ?? 0) ? "bg-green-400" : "bg-amber-400 animate-pulse")
                              : staleSegIds.has(seg.id) ? "bg-red-500" : hasIllustration ? "bg-green-400" : "bg-gray-300"
                          }`}
                          title={isGenerating
                            ? (idx < ((chapterProgress as any)?.completed_pages ?? 0) ? "Done" : "Generating…")
                            : staleSegIds.has(seg.id) ? `Stale — ${staleReasons[seg.id] || "a character/scene changed"}; regenerate` : undefined}
                        />
```
with:
```tsx
                        <span
                          className={`w-2 h-2 rounded-full shrink-0 ${
                            isGenerating
                              ? "bg-amber-400 animate-pulse"
                              : staleSegIds.has(seg.id) ? "bg-red-500" : hasIllustration ? "bg-green-400" : "bg-gray-300"
                          }`}
                          title={isGenerating
                            ? "Generating…"
                            : staleSegIds.has(seg.id) ? `Stale — ${staleReasons[seg.id] || "a character/scene changed"}; regenerate` : undefined}
                        />
```

- [ ] **Step 13: Remove the AgentActivityPanel render**

At the bottom of the JSX, delete the entire block:
```tsx
      {/* Persistent live Agent Activity column (sits alongside tab content) */}
      {agentPanelOpen && (
        <AgentActivityPanel
          bookId={bookId}
          chapterIdx={generatingChapter ?? selectedChapter}
          isGenerating={generatingChapter !== null}
          currentAgent={(chapterProgress as any)?.agent || null}
          currentStep={chapterProgress?.current_step}
          progress={chapterProgress?.progress}
          completedPages={(chapterProgress as any)?.completed_pages}
          totalPages={(chapterProgress as any)?.total_pages}
          onClose={() => setAgentPanelOpen(false)}
        />
      )}
```

- [ ] **Step 14: Verify no dangling references remain**

Run:
```bash
cd /Users/echoooooo/Desktop/code/picture_book_generator/frontend
grep -n "generatingChapter\|chapterProgress\|genAllChapters\|agentPanelOpen\|AGENT_META\|AgentActivityPanel\|isSegmentPageStep\|generateChapter\|getChapterProgress\|getAgentLog\|lastCompletedPagesRef\|Activity" src/app/editor/\[bookId\]/page.tsx
```
Expected: **no output** (all references gone). If `Activity` still matches, ensure it was only the lucide import (removed in Step 3).

- [ ] **Step 15: Typecheck + full frontend test run**

Run: `cd frontend && npx tsc --noEmit && npm test`
Expected: tsc clean; tests pass (AgentActivityPanel + progress tests are gone; pageGen + segment + CharacterManagement tests pass).

- [ ] **Step 16: Backend tests still green**

Run: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest -q`
Expected: **237 passed** (no backend change, but confirm nothing depended on the deleted frontend files via a shared fixture — it shouldn't).

- [ ] **Step 17: Commit**

```bash
cd /Users/echoooooo/Desktop/code/picture_book_generator
git add -A
git commit -m "$(cat <<'EOF'
refactor(frontend): drive chapter/all generation via per-page loop

Replace the deleted chapter subprocess (chapter/generate + progress poll +
agent-log) with a sequential loop over POST /segment/{id}/regenerate. Gen chapter
/ Gen All now fill only missing + stale pages, streaming each in live, with a
cooperative Stop. Delete AgentActivityPanel, lib/progress.ts, and the three dead
api fns.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Remove the scene-background auto-regen debounce

Deletes the silent 2s debounce that auto-regenerated `scene_background` after a character change (spec §11.2). Per the user's decision, the manual "Generate" button for the scene-background text field stays (with its `generateSceneBackground` api fn + `POST …/background` route).

**Files:**
- Modify: `frontend/src/app/editor/[bookId]/page.tsx`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing.

- [ ] **Step 1: Remove the debounce timer + state + function**

Delete:
```tsx
  // Debounce timer for auto-regenerating scene_background after character changes
  const sceneRegenTimer = useRef<NodeJS.Timeout | null>(null);
  const [regenningBg, setRegenningBg] = useState(false);

  const triggerSceneBackgroundRegen = useCallback((segId: number) => {
    ...
  }, [bookId]);
```
(the whole `triggerSceneBackgroundRegen` `useCallback` block).

- [ ] **Step 2: Remove the debounce cleanup in the unmount effect**

Delete these lines from the unmount effect:
```tsx
      // A pending scene-background debounce would otherwise still fire its
      // updateSegment + paid LLM call after the user has left the page.
      if (sceneRegenTimer.current) clearTimeout(sceneRegenTimer.current);
```

- [ ] **Step 3: Remove the scene-background special case in `updateField`**

Delete:
```tsx
    // The user is hand-editing the scene background → cancel any pending
    // auto-regen, otherwise the debounce (armed when they changed a character)
    // would fire 2s later and silently overwrite what they just typed.
    if (field === "scene_background" && sceneRegenTimer.current) {
      clearTimeout(sceneRegenTimer.current);
      sceneRegenTimer.current = null;
    }
```

- [ ] **Step 4: Remove the auto-regen triggers on character changes**

In `updateAction`, delete:
```tsx
    // Auto-regenerate scene_background after character changes
    if (field === "name" && value.trim()) {
      triggerSceneBackgroundRegen(segId);
    }
```

In `removeCharacterAction`, delete:
```tsx
    // Auto-regenerate scene_background after removing character
    triggerSceneBackgroundRegen(segId);
```

In the add-character `<select>` `onChange`, delete the `triggerSceneBackgroundRegen(segId);` line (keep the `mutateSegment(...)` call):
```tsx
          const segId = selectedSegment.id;
          mutateSegment(segId, (seg) => addAction(seg, name));
          triggerSceneBackgroundRegen(segId);   // ← delete this line only
```

- [ ] **Step 5: Remove the "updating scene..." indicator**

In the "Characters & Actions" header, delete:
```tsx
                    {regenningBg && <span className="text-[9px] text-gray-400 ml-1 animate-pulse">updating scene...</span>}
```

- [ ] **Step 6: Verify no dangling references**

Run:
```bash
cd /Users/echoooooo/Desktop/code/picture_book_generator/frontend
grep -n "triggerSceneBackgroundRegen\|sceneRegenTimer\|regenningBg" src/app/editor/\[bookId\]/page.tsx
```
Expected: **no output**. Also confirm the manual button survives:
```bash
grep -n "generateSceneBackground" src/app/editor/\[bookId\]/page.tsx
```
Expected: two matches (the import + the manual "Generate" button's onClick).

- [ ] **Step 7: Typecheck + tests + commit**

Run: `cd frontend && npx tsc --noEmit && npm test`
Expected: clean + green.

```bash
cd /Users/echoooooo/Desktop/code/picture_book_generator
git add frontend/src/app/editor/\[bookId\]/page.tsx
git commit -m "$(cat <<'EOF'
refactor(frontend): drop silent scene_background auto-regen debounce

Changing characters_in_scene no longer fires a 2s-debounced paid regen. The
page regenerates only on explicit Save & Regen; the manual "Generate" button for
the scene-background text field stays.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Add "Download PDF" to the Library

Each generated book gets a direct PDF download link. `GET /api/book/{id}/pdf` is un-gated (a read), so a plain `<a download>` works without the access-code header.

**Files:**
- Modify: `frontend/src/components/BookLibrary.tsx`

**Interfaces:**
- Consumes: existing `BookEntry` (`book_id`, `title`, `generated_chapters`).
- Produces: nothing.

- [ ] **Step 1: Add the PDF link beside "View Book"**

In the card's action row, replace:
```tsx
            <div className="mt-3 flex gap-2">
              <span className="text-xs bg-sage/30 text-gray-600 px-2 py-1 rounded-lg">
                Editor
              </span>
              {book.generated_chapters > 0 && (
                <a
                  href={`/book/${book.book_id}`}
                  className="relative z-10 text-xs bg-coral text-white px-2 py-1 rounded-lg hover:bg-coral/80 transition-colors"
                >
                  View Book
                </a>
              )}
            </div>
```
with:
```tsx
            <div className="mt-3 flex flex-wrap gap-2">
              <span className="text-xs bg-sage/30 text-gray-600 px-2 py-1 rounded-lg">
                Editor
              </span>
              {book.generated_chapters > 0 && (
                <a
                  href={`/book/${book.book_id}`}
                  className="relative z-10 text-xs bg-coral text-white px-2 py-1 rounded-lg hover:bg-coral/80 transition-colors"
                >
                  View Book
                </a>
              )}
              {book.generated_chapters > 0 && (
                <a
                  href={`/api/book/${book.book_id}/pdf`}
                  download
                  className="relative z-10 text-xs bg-sky/70 text-gray-800 px-2 py-1 rounded-lg hover:bg-sky transition-colors"
                >
                  Download PDF
                </a>
              )}
            </div>
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd frontend && npx tsc --noEmit`
Expected: no output.

```bash
cd /Users/echoooooo/Desktop/code/picture_book_generator
git add frontend/src/components/BookLibrary.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): Download PDF link per generated book in the Library

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Full verification + update HANDOFF

**Files:**
- Modify: `docs/superpowers/HANDOFF.md`

- [ ] **Step 1: Run the whole suite**

```bash
cd /Users/echoooooo/Desktop/code/picture_book_generator
/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest -q
cd frontend && npx tsc --noEmit && npm test
```
Expected: backend **237 passed**; tsc clean; frontend tests green (pageGen, segment, CharacterManagement.race, CharacterManagement.regenClaim).

- [ ] **Step 2: Confirm the dead endpoints are truly gone from the frontend**

```bash
cd /Users/echoooooo/Desktop/code/picture_book_generator/frontend
grep -rn "generateChapter\|getChapterProgress\|getAgentLog\|AgentActivityPanel\|isSegmentPageStep\|triggerSceneBackgroundRegen" src/
```
Expected: **no output**.

- [ ] **Step 3: Manual smoke test (record result in the commit / notes)**

The orchestrator is unit-tested but the wiring needs one manual pass. With the backend running and a preprocessed book:
1. Open the editor → click a chapter's "Gen" → pages fill one-by-one (amber dot → green), progress bar advances, images stream in.
2. Click "Gen All" → chapters process in order; click "Stop" mid-run → the loop halts before the next page.
3. Re-click "Gen All" → already-illustrated non-stale pages are skipped (only missing/stale regenerate).
4. Edit a character on a page → "Save & Regen" → that page regenerates (no silent auto-regen on the character edit itself).
5. Library → "Download PDF" downloads the book PDF.

(If the app can't be run in this session, note that Task 6 Step 3 is deferred to manual QA and say so explicitly — do not claim it passed.)

- [ ] **Step 4: Update `docs/superpowers/HANDOFF.md`**

Under "⬜ 剩余工作 → Plan 3", mark the editor rewrite, AgentActivityPanel removal, BYOK-banner removal, api.ts dead-fn cleanup, Library PDF, and scene-background debounce removal as ✅ done. Record the two corrections: (a) `lib/agents.ts` is KEPT (preprocess UI still uses it); (b) `getConfig` deletion + Create-page (`UploadForm.tsx`) BYOK cleanup are deferred to a Create-page task. Note that batch generation covers pages only (covers via existing manual regenerate). Leave Plan 4 (deploy) and the small BYOK-inert cleanup as remaining.

- [ ] **Step 5: Commit**

```bash
cd /Users/echoooooo/Desktop/code/picture_book_generator
git add docs/superpowers/HANDOFF.md
git commit -m "$(cat <<'EOF'
docs: mark Plan 3 (editor rewrite) done + record corrections

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage (HANDOFF Plan 3 + spec §11):**
- HANDOFF 3.1 "Gen 整章/Gen All → 逐页循环 via regenerateSegment" → Task 1 + Task 3. ✅
- HANDOFF 3.1 "删 AgentActivityPanel + 组件 + 测试 + lib/agents.ts(AGENT_META)" → Task 3 deletes the component/test/usage; **agents.ts kept** (documented correction — it's still used by preprocess UI). ✅ (with justified deviation)
- HANDOFF 3.1 "删 BYOK 横幅 + requireKey/canEdit/hasKey/keyInput → canEdit=true" → Task 2. ✅
- HANDOFF 3.2 "api.ts 删 generateChapter/getChapterProgress/getAgentLog" → Task 3 Step 2. ✅ "getConfig 也可删" → **deferred** (still used by UploadForm) — documented. ✅
- HANDOFF 3.3 "Library 每本书加下载 PDF 按钮 → /api/book/{id}/pdf" → Task 5. ✅
- HANDOFF 3.4 "依赖联动 stale 标红 + 重画=换图+版本+自动QA (后端已具备；前端保留)" → preserved: `refreshStale` still called after every regen (single + batch); the per-page endpoint does version+QA server-side. ✅
- spec §11.2 rule 1 (stale red dots) → preserved (unchanged `refreshStale` + `staleSegIds` rendering). ✅
- spec §11.2 rule 2 (any regen = swap+version+QA, one atomic path) → all regen now flows through the single-page endpoint. ✅
- spec §11.2 "删掉防抖 2s 自动重画 scene_background" → Task 4 (manual button kept per user decision). ✅

**Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N" — every code step shows full code. Grep-verify steps included per task.

**Type consistency:** `PageGenIO`/`PageGenResult`/`BatchProgress`/`BatchHooks`/`BatchResult` and `generateOnePage`/`generatePagesSequential` names/signatures in Task 1 match their usages in Task 3 (`makeIO(): PageGenIO`, `generateOnePage(segId, makeIO())`, `generatePagesSequential(targets, makeIO(), {onProgress, onPageDone})`). `genProgress` shape `{done,total,segId,chIdx}` is consistent across state decl (Step 4) and every UI reference (Steps 10–12). `Segment` type (with `illustration_url?`, `id`) matches `frontend/src/types/index.ts`.

**Ordering safety:** Task 2 leaves `generatingChapter`/`genAllChapters` refs intact (only removes `canEdit`), so it compiles alone. Task 3 removes those refs and everything referencing them in one atomic task. `makeIO` (const, not hoisted) is declared before both `handleRegenerate` and the batch handlers. Tasks 3 and 4 edit largely disjoint regions of the same file; run them in order.
