import { MapPin } from "lucide-react";
import type { Segment, CharacterInfo } from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface CharacterSheetsPanelProps {
  selectedSegment: Segment;
  characters: CharacterInfo[];
  sheets: Record<string, string>;
  portraits: Record<string, string>;
  locations: any[];
  sceneSheets: Record<string, string>;
  bookId: string;
  onRegenerateSheet: (canonicalName: string) => void;
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
  // Match characters to scene (show all that are in characters_in_scene, even without sheets)
  const sceneChars = selectedSegment?.characters_in_scene || [];
  const filteredCharacters = sceneChars.length === 0 ? [] : characters.filter((c) => {
    const cName = c.canonical_name.toLowerCase();
    const cParts = cName.split(/\s+/).filter((p) => p.length > 3);
    return sceneChars.some((sc) => {
      const scLower = sc.toLowerCase();
      if (cName === scLower) return true;
      const scParts = scLower.split(/\s+/).filter((p) => p.length > 3);
      return cParts.some((p) => scLower.includes(p)) || scParts.some((p) => cName.includes(p));
    });
  });

  // Match location to scene_background
  const bg = (selectedSegment?.scene_background || "").toLowerCase();
  const matchedLocations = locations.filter((loc) => {
    const locName = loc.name.toLowerCase();
    const locParts = locName.split(/\s+/).filter((p: string) => p.length > 3);
    const aliases = (loc.aliases || []).map((a: string) => a.toLowerCase());
    // Check if any location name/alias word appears in scene_background
    return locParts.some((p: string) => bg.includes(p))
      || aliases.some((a: string) => bg.includes(a))
      || bg.includes(locName);
  });

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
                    src={`${API_BASE}${sheetUrl}?t=${Date.now()}`}
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
