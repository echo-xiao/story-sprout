/**
 * Cross-character race guards in CharacterManagement.
 *
 * Review finding (medium): Auto Fill / quality responses landing AFTER the
 * user switched characters were applied to the now-selected character —
 * autofill merged char A's appearance into char B's form, and Save then
 * persisted A's looks onto B (real data corruption for the consistency
 * pipeline). Quality scores likewise displayed under the wrong character.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, act, fireEvent } from "@testing-library/react";
import React from "react";

import CharacterManagement from "@/components/editor/CharacterManagement";
import {
  autofillCharacterDetails,
  checkCharacterSheetQuality,
  getCharacters,
} from "@/lib/api";

vi.mock("@/lib/api", () => ({
  updateCharacter: vi.fn(),
  regenerateCharacterSheet: vi.fn(),
  getCharacters: vi.fn(),
  autofillCharacterDetails: vi.fn(),
  checkCharacterSheetQuality: vi.fn(),
  getRegenActive: vi.fn(),
  // The component loads selectable versions on character change — without these
  // in the mock the render throws ("No export defined") and every test errors.
  getAssetVersions: vi.fn(() => Promise.resolve({ versions: [], selected_version_id: null })),
  selectVersion: vi.fn(() => Promise.resolve({})),
}));

const mockAutofill = vi.mocked(autofillCharacterDetails);
const mockQuality = vi.mocked(checkCharacterSheetQuality);
const mockGetCharacters = vi.mocked(getCharacters);

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

const CHARS = [
  { canonical_name: "Alpha", aliases: [], gender: "male", role: "main", description: "", appearance: "alpha appearance" },
  { canonical_name: "Beta", aliases: [], gender: "female", role: "main", description: "", appearance: "beta appearance" },
];
const SHEETS = { Alpha: "/static/b/characters/alpha_sheet.png", Beta: "/static/b/characters/beta_sheet.png" };

async function renderPanel() {
  const utils = render(
    <CharacterManagement
      bookId="test-book"
      characters={CHARS as any}
      sheets={SHEETS}
      aliasMap={{}}
      onCharactersUpdate={() => {}}
    />
  );
  // Let the mount effects settle (auto-select Alpha + its history load).
  await act(async () => {});
  return utils;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetCharacters.mockResolvedValue({ characters: CHARS, sheets: SHEETS });
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("Auto Fill race", () => {
  it("applies the result when the user stayed on the character (control)", async () => {
    const slow = deferred<{ appearance: string; visual_details: Record<string, string> }>();
    mockAutofill.mockReturnValueOnce(slow.promise as any);

    const { getByText, getByPlaceholderText } = await renderPanel();
    fireEvent.click(getByText("Auto Fill"));
    await act(async () => {
      slow.resolve({ appearance: "GATSBY_PINK_SUIT", visual_details: {} });
    });

    expect(
      (getByPlaceholderText("Physical description from the book...") as HTMLTextAreaElement).value
    ).toBe("GATSBY_PINK_SUIT");
  });

  it("discards a result that lands after switching characters", async () => {
    const slow = deferred<{ appearance: string; visual_details: Record<string, string> }>();
    mockAutofill.mockReturnValueOnce(slow.promise as any);

    const { getByText, getByPlaceholderText } = await renderPanel();
    fireEvent.click(getByText("Auto Fill")); // autofill for Alpha...
    fireEvent.click(getByText("Beta"));      // ...user switches to Beta
    await act(async () => {
      slow.resolve({ appearance: "GATSBY_PINK_SUIT", visual_details: {} });
    });

    // Alpha's autofill must NOT be merged into Beta's form — Save would have
    // persisted Alpha's looks onto Beta.
    expect(
      (getByPlaceholderText("Physical description from the book...") as HTMLTextAreaElement).value
    ).toBe("beta appearance");
  });
});

describe("Quality check race", () => {
  it("shows the score when the user stayed on the character (control)", async () => {
    const slow = deferred<any>();
    mockQuality.mockReturnValueOnce(slow.promise as any);

    const { getByText, queryByText } = await renderPanel();
    fireEvent.click(getByText("Run"));
    await act(async () => {
      slow.resolve({ overall_score: 92 });
    });

    expect(queryByText("92%")).not.toBeNull();
  });

  it("does not show a score that lands after switching characters", async () => {
    const slow = deferred<any>();
    mockQuality.mockReturnValueOnce(slow.promise as any);

    const { getByText, queryByText } = await renderPanel();
    fireEvent.click(getByText("Run"));  // quality for Alpha...
    fireEvent.click(getByText("Beta")); // ...user switches to Beta
    await act(async () => {
      slow.resolve({ overall_score: 92 });
    });

    // Alpha's 92% must not display under Beta.
    expect(queryByText("92%")).toBeNull();
  });
});
