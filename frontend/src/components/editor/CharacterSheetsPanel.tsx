import { MapPin } from "lucide-react";
import type { Segment, CharacterInfo } from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

interface CharacterSheetsPanelProps {
  selectedSegment: Segment;
  characters: CharacterInfo[];
  sheets: Record<string, string>;
  portraits?: Record<string, string>;
  locations: any[];
  sceneSheets: Record<string, string>;
  bookId?: string;
  onRegenerateSheet?: (canonicalName: string) => void;
  onNavigateToCharacter?: (charName: string) => void;
  onNavigateToScene?: (locName: string) => void;
}

export default function CharacterSheetsPanel({
  selectedSegment,
  characters,
  sheets,
  locations,
  sceneSheets,
  onNavigateToCharacter,
  onNavigateToScene,
}: CharacterSheetsPanelProps) {
  // Show exactly the characters listed in characters_in_scene (from Characters & Actions editor)
  const sceneChars = selectedSegment?.characters_in_scene || [];
  const filteredCharacters = sceneChars.map((name) => {
    // Find matching character info (exact match first, then case-insensitive)
    return characters.find((c) => c.canonical_name === name)
      || characters.find((c) => c.canonical_name.toLowerCase() === name.toLowerCase())
      || { canonical_name: name, gender: "?", role: "?", aliases: [], description: "", appearance: "" } as CharacterInfo;
  });

  // Match location to scene_background — score each location and pick the best
  const bg = (selectedSegment?.scene_background || "").toLowerCase();
  const scoredLocations = locations.map((loc) => {
    const locName = loc.name.toLowerCase();
    const aliases = (loc.aliases || []).map((a: string) => a.toLowerCase());
    let score = 0;
    // Full name match is strongest signal
    if (bg.includes(locName)) score += 10;
    // Full alias match
    for (const a of aliases) {
      if (bg.includes(a)) score += 8;
    }
    // Partial word match (only significant words >4 chars, skip generic like "house", "room")
    const genericWords = new Set(["house", "room", "place", "street", "building", "area", "town", "city"]);
    const locParts = locName.split(/\s+/).filter((p: string) => p.length > 4 && !genericWords.has(p));
    for (const p of locParts) {
      if (bg.includes(p)) score += 3;
    }
    return { loc, score };
  });
  const matchedLocations = scoredLocations
    .filter((s) => s.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 2)
    .map((s) => s.loc);

  return (
    <div className="w-1/2 overflow-y-auto p-3 space-y-3">
      {/* Characters in Scene */}
      <div className="card !p-3">
        <h3 className="font-display font-bold text-gray-700 text-xs mb-3">Characters in Scene</h3>
        <div className="space-y-4">
          {filteredCharacters.map((char) => {
            const sheetUrl = sheets[char.canonical_name];
            return (
              <div
                key={char.canonical_name}
                className="cursor-pointer hover:opacity-80 transition-opacity"
                onClick={() => onNavigateToCharacter?.(char.canonical_name)}
              >
                {sheetUrl ? (
                  <img
                    src={`${API_BASE}${sheetUrl}`}
                    alt={char.canonical_name}
                    className="w-full rounded-xl mb-2"
                  />
                ) : (
                  <div className="w-full aspect-square bg-peach/20 rounded-xl flex items-center justify-center mb-2 text-gray-300 text-xs">
                    No sheet yet
                  </div>
                )}
                <p className="text-xs font-bold text-gray-800">{char.canonical_name}</p>
                <p className="text-[10px] text-gray-500">{char.gender} / {char.role}</p>
              </div>
            );
          })}
          {filteredCharacters.length === 0 && (
            <p className="text-[10px] text-gray-400">No matching characters.</p>
          )}
        </div>
      </div>

      {/* Scene / Location */}
      <div className="card !p-3">
        <h3 className="font-display font-bold text-gray-700 text-xs mb-3 flex items-center gap-1">
          <MapPin size={12} /> Scene Location
        </h3>
        {matchedLocations.length > 0 ? (
          <div className="space-y-3">
            {matchedLocations.map((loc) => (
              <div
                key={loc.name}
                className="cursor-pointer hover:opacity-80 transition-opacity"
                onClick={() => onNavigateToScene?.(loc.name)}
              >
                {sceneSheets[loc.name] ? (
                  <img
                    src={`${API_BASE}${sceneSheets[loc.name]}`}
                    alt={loc.name}
                    className="w-full rounded-xl mb-2"
                  />
                ) : (
                  <div className="w-full aspect-video bg-peach/20 rounded-xl flex items-center justify-center mb-2">
                    <MapPin size={20} className="text-gray-300" />
                  </div>
                )}
                <p className="text-xs font-bold text-gray-800">{loc.name}</p>
                {loc.description && (
                  <p className="text-[10px] text-gray-500">{loc.description}</p>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[10px] text-gray-400">No matching location for this scene.</p>
        )}
      </div>
    </div>
  );
}
