/**
 * Polling lifecycle of AgentActivityPanel.
 *
 * Convention (mirrors the backend pytest suite): tests locking in current
 * correct behavior are plain `it(...)`; tests documenting a KNOWN BUG from
 * CODE_REVIEW_2026-06-11.md are `it.fails(...)` — they fail today by design,
 * and once the bug is fixed vitest reports them as "expected to fail but
 * passed", forcing the fixer to drop the `.fails` marker, which turns the
 * test into a permanent regression test.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, act } from "@testing-library/react";
import React from "react";

import AgentActivityPanel from "@/components/editor/AgentActivityPanel";
import { getAgentLog } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  getAgentLog: vi.fn(),
}));

const mockGetAgentLog = vi.mocked(getAgentLog);

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

function renderPanel(overrides: Partial<React.ComponentProps<typeof AgentActivityPanel>> = {}) {
  return render(
    <AgentActivityPanel
      bookId="test-book"
      chapterIdx={0}
      isGenerating={true}
      currentAgent="artist"
      onClose={() => {}}
      {...overrides}
    />
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  mockGetAgentLog.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("AgentActivityPanel polling", () => {
  it("polls immediately and re-polls every 3s while generating", async () => {
    mockGetAgentLog.mockResolvedValue([]);
    renderPanel();

    expect(mockGetAgentLog).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(mockGetAgentLog).toHaveBeenCalledTimes(2);
  });

  it("does not re-poll when not generating", async () => {
    mockGetAgentLog.mockResolvedValue([]);
    renderPanel({ isGenerating: false });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(mockGetAgentLog).toHaveBeenCalledTimes(1);
  });

  it("does not poll without a chapter", () => {
    renderPanel({ chapterIdx: null });
    expect(mockGetAgentLog).not.toHaveBeenCalled();
  });

  it("stops a settled polling chain on unmount", async () => {
    mockGetAgentLog.mockResolvedValue([]);
    const { unmount } = renderPanel();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0); // let the first poll settle + arm the timer
    });

    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(mockGetAgentLog).toHaveBeenCalledTimes(1);
  });

  it("stops an in-flight polling chain on unmount", async () => {
    const first = deferred<never[]>();
    mockGetAgentLog.mockReturnValueOnce(first.promise as any);
    mockGetAgentLog.mockResolvedValue([]);

    const { unmount } = renderPanel();
    expect(mockGetAgentLog).toHaveBeenCalledTimes(1);

    unmount(); // request still pending
    await act(async () => {
      first.resolve([]); // request lands after unmount
      await vi.advanceTimersByTimeAsync(10000);
    });

    // A correct implementation bails out after unmount; the orphan chain
    // keeps polling instead.
    expect(mockGetAgentLog).toHaveBeenCalledTimes(1);
  });

  it("dep change while a request is in flight must not fork the chain", async () => {
    const first = deferred<never[]>();
    mockGetAgentLog.mockReturnValueOnce(first.promise as any);
    mockGetAgentLog.mockResolvedValue([]);

    const { rerender } = renderPanel({ chapterIdx: 0 });
    expect(mockGetAgentLog).toHaveBeenCalledTimes(1);

    // Chapter switches while chapter-0 request is pending: effect re-runs
    // (one new immediate poll), and the old chain must die.
    rerender(
      <AgentActivityPanel
        bookId="test-book"
        chapterIdx={1}
        isGenerating={true}
        currentAgent="artist"
        onClose={() => {}}
      />
    );
    expect(mockGetAgentLog).toHaveBeenCalledTimes(2);

    await act(async () => {
      first.resolve([]);
      await vi.advanceTimersByTimeAsync(3000);
    });

    // Expected: exactly one re-poll (the chapter-1 chain). The orphaned
    // chapter-0 chain adds an extra call.
    expect(mockGetAgentLog).toHaveBeenCalledTimes(3);
  });
});
