/**
 * Root cause C: character-sheet regen polling must be claim-first.
 *
 * The polls used "a sheet with version === 'current' exists" as the success
 * signal. But a FAILED regen of a character that already had a sheet restores
 * the previous sheet (current always exists again), so that check reported
 * success and swallowed the real error (e.g. a free-tier key with zero image
 * quota). Success/failure now comes ONLY from the claim channel
 * (getRegenActive → {active, error}), matching page.tsx and SceneManagement.
 *
 * Strict guard: the deleted history-"current" success heuristic must not
 * reappear in the regen poll loops.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// vitest runs from the repo root (Next.js co-located with the Python api).
const src = readFileSync(
  resolve(process.cwd(), "components/editor/CharacterManagement.tsx"),
  "utf-8",
);

describe("character regen polling is claim-first", () => {
  it("no poll gates success on a 'current' sheet existing", () => {
    // The exact removed pattern: a version==='current' check that resolves the
    // poll as success. Rendering uses version==='current' too, but never to
    // resolve a regen poll — so this success-gate shape must be gone.
    const gate = /images\?\.some\(\s*img\s*=>\s*img\.version\s*===\s*["']current["']\s*\)\s*\)\s*\{\s*clearInterval/;
    expect(gate.test(src)).toBe(false);
  });

  it("regen poll loops query getRegenActive as the source of truth", () => {
    // Both poll loops must consult the claim channel.
    const claimChecks = src.match(/getRegenActive\(bookId, "character"/g) || [];
    expect(claimChecks.length).toBeGreaterThanOrEqual(2);
  });
});
