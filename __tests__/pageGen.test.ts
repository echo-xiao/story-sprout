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
