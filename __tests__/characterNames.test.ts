/**
 * Scene character names must resolve to the canonical record even when the
 * scene uses a short form the canonical name stores WITH a leading article.
 *
 * "rocket 去哪里了": the cover's characters_in_scene listed "Remarkable Rocket",
 * but the record's canonical_name is "the Remarkable Rocket" and its aliases
 * (["the Rocket","Rocket"]) don't include the short form — so the old
 * exact/case-insensitive-only match fell back to a "?/?" stub with no sheet.
 * This resolver mirrors the backend so panel + generation agree.
 */
import { describe, it, expect } from "vitest";
import { resolveSceneCharacter, normalizeCharacterName } from "@/lib/characterNames";
import type { CharacterInfo } from "@/types";

const mk = (canonical_name: string, aliases: string[] = []): CharacterInfo => ({
  canonical_name, aliases, gender: "male", role: "main", description: "", appearance: "",
});

const CHARS: CharacterInfo[] = [
  mk("the Remarkable Rocket", ["the Rocket", "Rocket"]),
  mk("Swallow"),
  mk("Hugh the Miller"),
  mk("the Happy Prince", ["Happy Prince"]),
];

describe("resolveSceneCharacter", () => {
  it("resolves the-stripped short form to the canonical record (the bug case)", () => {
    const c = resolveSceneCharacter("Remarkable Rocket", CHARS);
    expect(c.canonical_name).toBe("the Remarkable Rocket");
    expect(c.role).toBe("main"); // real record, not the "?/?" stub
  });

  it("resolves by exact alias", () => {
    expect(resolveSceneCharacter("Rocket", CHARS).canonical_name).toBe("the Remarkable Rocket");
    expect(resolveSceneCharacter("Happy Prince", CHARS).canonical_name).toBe("the Happy Prince");
  });

  it("passes exact / case-insensitive canonical through", () => {
    expect(resolveSceneCharacter("Swallow", CHARS).canonical_name).toBe("Swallow");
    expect(resolveSceneCharacter("swallow", CHARS).canonical_name).toBe("Swallow");
    expect(resolveSceneCharacter("Hugh the Miller", CHARS).canonical_name).toBe("Hugh the Miller");
  });

  it("falls back to a ?/? stub for an unknown name", () => {
    const c = resolveSceneCharacter("Nobody", CHARS);
    expect(c.canonical_name).toBe("Nobody");
    expect(c.gender).toBe("?");
    expect(c.role).toBe("?");
  });

  it("prefers a real canonical over another character's alias", () => {
    const chars = [mk("the Happy Prince", ["Happy Prince"]), mk("Happy Prince")];
    expect(resolveSceneCharacter("Happy Prince", chars).canonical_name).toBe("Happy Prince");
  });

  it("normalizes leading article + whitespace", () => {
    expect(normalizeCharacterName("The  Remarkable   Rocket")).toBe("remarkable rocket");
    expect(normalizeCharacterName("A Swallow")).toBe("swallow");
  });
});
