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
