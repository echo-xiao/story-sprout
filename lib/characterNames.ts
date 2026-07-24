import type { CharacterInfo } from "@/types";

/**
 * Loose key for matching a scene/segment character name to a canonical record:
 * lowercased, a leading article ("the "/"a "/"an ") dropped, whitespace
 * collapsed. Lets "Remarkable Rocket" match canonical "the Remarkable Rocket"
 * without hardcoding either — the mirror of the backend's
 * `_normalize_character_name` so the editor panel and generation resolve a
 * scene name to the SAME character.
 */
export function normalizeCharacterName(name: string): string {
  return (name || "")
    .trim()
    .toLowerCase()
    .replace(/^(the|a|an)\s+/, "")
    .replace(/\s+/g, " ");
}

/**
 * Resolve a scene character name to its full record via
 * canonical → alias → normalized(canonical) → normalized(alias), matching the
 * backend's priority (a real canonical always beats another char's alias).
 * Falls back to a stub record ("?/?", no sheet) when nothing matches, so an
 * unknown name renders instead of crashing.
 */
export function resolveSceneCharacter(
  name: string,
  characters: CharacterInfo[],
): CharacterInfo {
  const low = (name || "").trim().toLowerCase();
  const nrm = normalizeCharacterName(name);
  return (
    characters.find((c) => c.canonical_name === name) ||
    characters.find((c) => c.canonical_name.toLowerCase() === low) ||
    characters.find((c) => (c.aliases || []).some((a) => a.toLowerCase() === low)) ||
    characters.find((c) => normalizeCharacterName(c.canonical_name) === nrm) ||
    characters.find((c) => (c.aliases || []).some((a) => normalizeCharacterName(a) === nrm)) ||
    ({ canonical_name: name, gender: "?", role: "?", aliases: [], description: "", appearance: "" } as CharacterInfo)
  );
}
