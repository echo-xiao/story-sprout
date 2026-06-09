"use client";

import { useState, useEffect, useRef } from "react";
import { Users, RefreshCw, Save } from "lucide-react";
import { updateCharacter, regenerateCharacterSheet, getCharacters, getCharacterSheetHistory, autofillCharacterDetails } from "@/lib/api";
import type { CharacterInfo } from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface CharacterManagementProps {
  bookId: string;
  characters: CharacterInfo[];
  sheets: Record<string, string>;
  aliasMap: Record<string, string>;
  onCharactersUpdate: (characters: CharacterInfo[], sheets: Record<string, string>) => void;
  navigateToChar?: string | null;
  onSelectChar?: (name: string) => void;
}

export default function CharacterManagement({
  bookId,
  characters,
  sheets,
  aliasMap,
  onCharactersUpdate,
  navigateToChar,
  onSelectChar,
}: CharacterManagementProps) {
  const [selectedChar, setSelectedChar] = useState<string | null>(characters[0]?.canonical_name || null);
  const [editing, setEditing] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);
  const [regenning, setRegenning] = useState(false);
  const [sheetHistory, setSheetHistory] = useState<Array<{ url: string; version: string; timestamp: number }>>([]);
  const [activeSheetUrl, setActiveSheetUrl] = useState<string | null>(null);

  const selected = characters.find(c => c.canonical_name === selectedChar);

  // Report selected char to parent via effect (avoids setState-during-render)
  useEffect(() => {
    if (selectedChar) onSelectChar?.(selectedChar);
  }, [selectedChar]);

  const selectChar = (char: CharacterInfo) => {
    if (selectedChar !== char.canonical_name) {
      setActiveSheetUrl(null);
      // Show current sheet immediately as placeholder while history loads
      const sheetUrl = sheets[char.canonical_name];
      setSheetHistory(sheetUrl ? [{ url: sheetUrl, version: "current", timestamp: Date.now() }] : []);
    }
    setSelectedChar(char.canonical_name);
    setEditing({
      canonical_name: char.canonical_name,
      gender: char.gender || "unknown",
      role: char.role || "supporting",
      appearance: char.appearance || "",
      description: char.description || "",
      visual_details: char.visual_details || {},
    });
  };

  // Navigate to specific character when triggered from editor
  useEffect(() => {
    if (!navigateToChar) return;
    const char = characters.find(c => c.canonical_name === navigateToChar);
    if (char) {
      selectChar(char);
    }
  }, [navigateToChar]);

  // Load sheet history when character changes
  useEffect(() => {
    if (!selectedChar) return;
    getCharacterSheetHistory(bookId, selectedChar)
      .then(data => {
        setSheetHistory(data.images || []);
        // Don't override activeSheetUrl — let sheets[name] be the default
      })
      .catch(() => { setSheetHistory([]); });
  }, [bookId, selectedChar, regenning]);

  // Auto-select first character on mount
  if (selected && Object.keys(editing).length === 0) {
    selectChar(selected);
  }

  const handleSave = async () => {
    if (!selectedChar) return;
    setSaving(true);
    try {
      await updateCharacter(bookId, selectedChar, editing);
      const data = await getCharacters(bookId);
      onCharactersUpdate(data.characters || [], data.sheets || {});
    } catch (e) {
      console.error("Save failed:", e);
    } finally {
      setSaving(false);
    }
  };

  const handleRegenSheet = async () => {
    if (!selectedChar) return;
    setRegenning(true);
    try {
      await regenerateCharacterSheet(bookId, selectedChar);
      setTimeout(async () => {
        const data = await getCharacters(bookId);
        onCharactersUpdate(data.characters || [], data.sheets || {});
        setRegenning(false);
      }, 30000);
    } catch (e) {
      console.error("Regen failed:", e);
      setRegenning(false);
    }
  };

  const mainChars = characters.filter(c => c.role === "main");
  const otherChars = characters.filter(c => c.role !== "main");
  const [genAllRunning, setGenAllRunning] = useState(false);
  const genAllProgressRef = useRef("");
  const genAllCurrentCharRef = useRef<string | null>(null);
  const genAllBtnRef = useRef<HTMLButtonElement>(null);
  const [autoFilling, setAutoFilling] = useState(false);

  const handleGenerateAll = async () => {
    const toGenerate = characters.filter(c => !sheets[c.canonical_name]);
    if (toGenerate.length === 0) {
      alert("All characters already have sheets!");
      return;
    }
    setGenAllRunning(true);
    for (let i = 0; i < toGenerate.length; i++) {
      const char = toGenerate[i];
      genAllProgressRef.current = `${i + 1}/${toGenerate.length}: ${char.canonical_name}`;
      genAllCurrentCharRef.current = char.canonical_name;
      // Update button text directly (no re-render)
      if (genAllBtnRef.current) genAllBtnRef.current.textContent = genAllProgressRef.current;
      try {
        await regenerateCharacterSheet(bookId, char.canonical_name);
        // Wait for sheet to appear (lightweight, no state updates)
        await new Promise<void>((resolve) => {
          const poll = setInterval(async () => {
            try {
              const hist = await getCharacterSheetHistory(bookId, char.canonical_name);
              if (hist.images?.some(img => img.version === "current")) {
                clearInterval(poll);
                resolve();
              }
            } catch {}
          }, 10000);
          setTimeout(() => { clearInterval(poll); resolve(); }, 120000);
        });
      } catch {}
    }
    // Final refresh
    const finalData = await getCharacters(bookId);
    onCharactersUpdate(finalData.characters || [], finalData.sheets || {});
    genAllProgressRef.current = "";
    genAllCurrentCharRef.current = null;
    setGenAllRunning(false);
  };

  const handleAutoFill = async () => {
    if (!selectedChar) return;
    setAutoFilling(true);
    try {
      const result = await autofillCharacterDetails(bookId, selectedChar);
      setEditing(prev => ({
        ...prev,
        appearance: result.appearance || prev.appearance,
        visual_details: result.visual_details || prev.visual_details,
      }));
      // Refresh character data
      const data = await getCharacters(bookId);
      onCharactersUpdate(data.characters || [], data.sheets || {});
    } catch (e) {
      console.error("Auto fill failed:", e);
    } finally {
      setAutoFilling(false);
    }
  };

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Left: Character List */}
      <div className="w-64 bg-white border-r border-peach/30 overflow-y-auto shrink-0">
        {/* Main Characters */}
        <div className="px-3 py-2 text-[10px] font-bold text-gray-400 uppercase tracking-wider bg-cream/50 flex items-center justify-between">
          <span>Main ({mainChars.length})</span>
          <button
            ref={genAllBtnRef}
            onClick={handleGenerateAll}
            disabled={genAllRunning || regenning}
            className="text-[9px] bg-coral/80 text-white px-2 py-0.5 rounded hover:bg-coral transition-colors disabled:opacity-50"
          >
            {genAllRunning ? "Generating..." : "Gen All"}
          </button>
        </div>
        {mainChars.map(char => (
          <CharListItem
            key={char.canonical_name}
            char={char}
            selected={selectedChar === char.canonical_name}
            hasSheet={!!sheets[char.canonical_name]}
            onClick={() => selectChar(char)}
          />
        ))}

        {/* Other Characters */}
        <div className="px-3 py-2 text-[10px] font-bold text-gray-400 uppercase tracking-wider bg-cream/50">
          Supporting & Minor ({otherChars.length})
        </div>
        {otherChars.map(char => (
          <CharListItem
            key={char.canonical_name}
            char={char}
            selected={selectedChar === char.canonical_name}
            hasSheet={!!sheets[char.canonical_name]}
            onClick={() => selectChar(char)}
          />
        ))}
      </div>

      {/* Middle + Right */}
      {selected ? (
        <div className="flex-1 flex overflow-hidden">
          {/* Middle: Character Sheet + History thumbnails */}
          <div className="flex-1 flex overflow-hidden border-r border-peach/20">
            {/* Main sheet image */}
            <div className="flex-1 overflow-y-auto p-6 flex flex-col">
              <h2 className="font-display text-lg font-bold text-gray-800 mb-3 shrink-0">{selected.canonical_name}</h2>
              <div className="flex-1 flex items-center justify-center min-h-0">
                {(sheets[selected.canonical_name] || activeSheetUrl) ? (
                  <img
                    src={`${API_BASE}${activeSheetUrl || sheets[selected.canonical_name]}`}
                    alt={selected.canonical_name}
                    className="max-h-[calc(100vh-180px)] max-w-full rounded-xl shadow-md object-contain"
                  />
                ) : regenning ? (
                  <div className="w-full max-w-md aspect-square bg-peach/10 rounded-xl flex flex-col items-center justify-center gap-3">
                    <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-coral" />
                    <p className="text-sm text-gray-500">Generating sheet...</p>
                    <p className="text-xs text-gray-400">~30 seconds</p>
                  </div>
                ) : (
                  <div className="w-full max-w-md aspect-square bg-peach/20 rounded-xl flex flex-col items-center justify-center text-gray-400 gap-2">
                    <Users size={32} />
                    <p className="text-xs">No sheet yet</p>
                    <p className="text-[10px]">Click Save & Regenerate to create</p>
                  </div>
                )}
              </div>
            </div>

            {/* History thumbnails (vertical, right side of sheet) */}
            {(() => {
              // Build versions list: current from sheets + historical from sheetHistory
              const currentUrl = sheets[selected.canonical_name];
              const historical = sheetHistory.filter(img => img.version !== "current");
              const allVersions = [
                ...(currentUrl ? [{ url: currentUrl, version: "current", timestamp: Date.now() }] : []),
                ...historical,
              ];
              if (allVersions.length === 0) return null;
              return (
                <div className="w-[320px] shrink-0 overflow-y-auto p-4 space-y-3 border-l border-peach/20">
                  <p className="text-xs text-gray-500 font-semibold">Versions ({allVersions.length})</p>
                  {allVersions.map((img, idx) => (
                    <div key={`${selected.canonical_name}-${img.version}-${idx}`}>
                      <img
                        src={`${API_BASE}${img.url}`}
                        alt={img.version === "current" ? "Current" : `v${allVersions.length - idx}`}
                        onClick={() => setActiveSheetUrl(img.url)}
                        className={`w-full rounded-xl cursor-pointer border-2 transition-colors ${
                          (activeSheetUrl || currentUrl) === img.url
                            ? "border-coral shadow-md"
                            : "border-transparent hover:border-coral/50"
                        }`}
                      />
                      <p className="text-[10px] text-gray-400 text-center mt-1">
                        {img.version === "current" ? "Current" : `Version ${allVersions.length - idx}`}
                      </p>
                    </div>
                  ))}
                </div>
              );
            })()}
          </div>

          {/* Right: Portrait + Edit Fields */}
          <div className="w-[320px] shrink-0 overflow-y-auto p-5 space-y-3">
            {/* Editable Name */}
            <div>
              <label className="text-xs text-gray-500 font-semibold mb-1 block">Name</label>
              <input
                value={editing.canonical_name ?? selected.canonical_name}
                onChange={e => setEditing(prev => ({ ...prev, canonical_name: e.target.value }))}
                className="w-full rounded-lg border border-peach/50 px-3 py-2 text-sm font-bold"
              />
            </div>
            <div className="flex gap-4">
              <div className="flex-1">
                <label className="text-xs text-gray-500 font-semibold mb-1 block">Gender</label>
                <select
                  value={editing.gender || "unknown"}
                  onChange={e => setEditing(prev => ({ ...prev, gender: e.target.value }))}
                  className="w-full rounded-lg border border-peach/50 px-3 py-2 text-sm bg-white"
                >
                  <option value="male">Male</option>
                  <option value="female">Female</option>
                  <option value="unknown">Unknown</option>
                </select>
              </div>
              <div className="flex-1">
                <label className="text-xs text-gray-500 font-semibold mb-1 block">Role</label>
                <select
                  value={editing.role || "supporting"}
                  onChange={e => setEditing(prev => ({ ...prev, role: e.target.value }))}
                  className="w-full rounded-lg border border-peach/50 px-3 py-2 text-sm bg-white"
                >
                  <option value="main">Main</option>
                  <option value="supporting">Supporting</option>
                  <option value="minor">Minor</option>
                </select>
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-gray-500 font-semibold">Appearance (from book)</label>
                <button
                  onClick={handleAutoFill}
                  disabled={autoFilling}
                  className="text-[10px] bg-sky/50 hover:bg-sky text-gray-700 px-2 py-0.5 rounded font-semibold disabled:opacity-50"
                >
                  {autoFilling ? "Filling..." : "Auto Fill"}
                </button>
              </div>
              <textarea
                value={editing.appearance || ""}
                onChange={e => setEditing(prev => ({ ...prev, appearance: e.target.value }))}
                rows={Math.max(2, Math.ceil((editing.appearance || "").length / 35) + 1)}
                className="w-full rounded-lg border border-peach/50 px-3 py-2 text-sm resize-y"
                placeholder="Physical description from the book..."
              />
            </div>

            {/* Structured appearance fields */}
            <div className="border border-peach/30 rounded-lg p-3 space-y-2">
              <p className="text-[10px] text-gray-400 font-semibold uppercase tracking-wider">Visual Details</p>
              {[
                { key: "age", label: "Age", placeholder: "e.g. 60, elderly, young" },
                { key: "ethnicity", label: "Ethnicity", placeholder: "e.g. European, French" },
                { key: "skin_tone", label: "Skin Tone", placeholder: "e.g. fair, rosy cheeks" },
                { key: "hair", label: "Hair", placeholder: "e.g. flaxen wig, curly dark hair" },
                { key: "eyes", label: "Eyes", placeholder: "e.g. bright moist eyes, brown" },
                { key: "build", label: "Build", placeholder: "e.g. stout, tall and thin" },
                { key: "clothing", label: "Clothing", placeholder: "e.g. brown suit, large square cuffs" },
                { key: "accessories", label: "Accessories", placeholder: "e.g. spectacles, cane, bonnet" },
                { key: "distinctive", label: "Distinctive Feature", placeholder: "e.g. big red nose, scar" },
              ].map(({ key, label, placeholder }) => (
                <div key={key} className="flex items-start gap-2">
                  <label className="text-[10px] text-gray-500 w-20 shrink-0 text-right pt-1">{label}</label>
                  <textarea
                    value={(editing.visual_details || {})[key] || ""}
                    onChange={e => setEditing(prev => ({
                      ...prev,
                      visual_details: { ...(prev.visual_details || {}), [key]: e.target.value }
                    }))}
                    rows={Math.max(1, Math.ceil(((editing.visual_details || {})[key] || "").length / 25))}
                    className="flex-1 rounded-md border border-peach/40 px-2 py-1 text-xs resize-none"
                    placeholder={placeholder}
                  />
                </div>
              ))}
            </div>

            <div>
              <label className="text-xs text-gray-500 font-semibold mb-1 block">Description</label>
              <textarea
                value={editing.description || ""}
                onChange={e => setEditing(prev => ({ ...prev, description: e.target.value }))}
                rows={Math.max(2, Math.ceil((editing.description || "").length / 35) + 1)}
                className="w-full rounded-lg border border-peach/50 px-3 py-2 text-sm resize-y"
                placeholder="Character background, personality, role in the story..."
              />
            </div>

            {/* Aliases */}
            {Object.entries(aliasMap).filter(([, v]) => v === selectedChar).length > 0 && (
              <div>
                <label className="text-xs text-gray-500 font-semibold mb-1 block">Aliases</label>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(aliasMap)
                    .filter(([, v]) => v === selectedChar)
                    .map(([alias]) => (
                      <span key={alias} className="px-2 py-0.5 bg-lavender/30 text-xs rounded-full text-gray-700">
                        {alias}
                      </span>
                    ))}
                </div>
              </div>
            )}

            <div className="flex gap-2 pt-2">
              <button
                onClick={async () => {
                  if (!selectedChar) return;
                  setSaving(true);
                  try {
                    await updateCharacter(bookId, selectedChar, editing);
                    await handleRegenSheet();
                    const data = await getCharacters(bookId);
                    onCharactersUpdate(data.characters || [], data.sheets || {});
                  } catch (e) {
                    console.error("Save & regen failed:", e);
                  } finally {
                    setSaving(false);
                  }
                }}
                disabled={saving || regenning}
                className="btn-primary text-sm !px-4 !py-2 flex items-center gap-1.5"
              >
                <RefreshCw size={14} className={saving || regenning ? "animate-spin" : ""} />
                {saving ? "Saving..." : regenning ? "Generating..." : "Save & Regenerate"}
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center text-gray-400">
          Select a character from the left
        </div>
      )}
    </div>
  );
}

function CharListItem({
  char,
  selected,
  hasSheet,
  generating,
  onClick,
}: {
  char: CharacterInfo;
  selected: boolean;
  hasSheet: boolean;
  generating?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      data-char={char.canonical_name}
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 border-b border-gray-50 transition-colors ${
        generating ? "bg-amber-50 border-l-2 border-l-amber-400" :
        selected ? "bg-coral/10 border-l-2 border-l-coral" : "hover:bg-peach/20"
      }`}
    >
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full shrink-0 ${generating ? "bg-amber-400 animate-pulse" : hasSheet ? "bg-green-400" : "bg-gray-300"}`} />
        <span className={`text-sm truncate ${selected ? "font-bold text-gray-800" : "text-gray-700"}`}>
          {char.canonical_name}
        </span>
        {generating && <span className="text-[9px] text-amber-600 animate-pulse ml-auto shrink-0">generating...</span>}
      </div>
      <p className="text-[10px] text-gray-400 ml-4 truncate">
        {char.gender || "?"} / {char.role || "?"}{char.description ? ` — ${char.description.slice(0, 40)}...` : ""}
      </p>
    </button>
  );
}
