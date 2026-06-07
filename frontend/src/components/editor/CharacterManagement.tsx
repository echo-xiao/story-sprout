"use client";

import { useState, useEffect } from "react";
import { Users, RefreshCw, Save } from "lucide-react";
import { updateCharacter, regenerateCharacterSheet, getCharacters, getCharacterSheetHistory } from "@/lib/api";
import type { CharacterInfo } from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface CharacterManagementProps {
  bookId: string;
  characters: CharacterInfo[];
  sheets: Record<string, string>;
  aliasMap: Record<string, string>;
  onCharactersUpdate: (characters: CharacterInfo[], sheets: Record<string, string>) => void;
}

export default function CharacterManagement({
  bookId,
  characters,
  sheets,
  aliasMap,
  onCharactersUpdate,
}: CharacterManagementProps) {
  const [selectedChar, setSelectedChar] = useState<string | null>(characters[0]?.canonical_name || null);
  const [editing, setEditing] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);
  const [regenning, setRegenning] = useState(false);
  const [sheetHistory, setSheetHistory] = useState<Array<{ url: string; version: string; timestamp: number }>>([]);
  const [activeSheetUrl, setActiveSheetUrl] = useState<string | null>(null);

  const selected = characters.find(c => c.canonical_name === selectedChar);

  const selectChar = (char: CharacterInfo) => {
    setSelectedChar(char.canonical_name);
    setActiveSheetUrl(null);
    setEditing({
      gender: char.gender || "unknown",
      role: char.role || "supporting",
      appearance: char.appearance || "",
      description: char.description || "",
    });
  };

  // Load sheet history when character changes
  useEffect(() => {
    if (!selectedChar) return;
    getCharacterSheetHistory(bookId, selectedChar)
      .then(data => {
        setSheetHistory(data.images || []);
        const current = data.images?.find(i => i.version === "current");
        setActiveSheetUrl(current?.url || null);
      })
      .catch(() => { setSheetHistory([]); setActiveSheetUrl(null); });
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

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Left: Character List */}
      <div className="w-64 bg-white border-r border-peach/30 overflow-y-auto shrink-0">
        {/* Main Characters */}
        <div className="px-3 py-2 text-[10px] font-bold text-gray-400 uppercase tracking-wider bg-cream/50">
          Main ({mainChars.length})
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
                {(activeSheetUrl || sheets[selected.canonical_name]) ? (
                  <img
                    src={`${API_BASE}${activeSheetUrl || sheets[selected.canonical_name]}?t=${Date.now()}`}
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
            {sheetHistory.length > 1 && (
              <div className="w-20 shrink-0 overflow-y-auto p-2 space-y-2 border-l border-peach/10">
                <p className="text-[9px] text-gray-400 font-semibold text-center">History</p>
                {sheetHistory.map((img, idx) => (
                  <div key={idx}>
                    <img
                      src={`${API_BASE}${img.url}?t=${img.timestamp}`}
                      alt={img.version === "current" ? "Current" : `v${sheetHistory.length - idx}`}
                      onClick={() => setActiveSheetUrl(img.url)}
                      className={`w-full aspect-square object-cover rounded-lg cursor-pointer border-2 transition-colors ${
                        (activeSheetUrl || sheets[selected.canonical_name]) === img.url
                          ? "border-coral"
                          : "border-transparent hover:border-coral/50"
                      }`}
                    />
                    <p className="text-[8px] text-gray-400 text-center mt-0.5">
                      {img.version === "current" ? "Now" : `v${sheetHistory.length - idx}`}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Right: Portrait + Edit Fields */}
          <div className="w-[320px] shrink-0 overflow-y-auto p-5 space-y-3">
            {/* Portrait (cropped from sheet - FRONT view, square, full width) */}
            {sheets[selected.canonical_name] && (
              <div className="w-full aspect-square rounded-xl overflow-hidden shadow-md mb-1">
                <img
                  src={`${API_BASE}${sheets[selected.canonical_name]}?t=${Date.now()}`}
                  alt={`${selected.canonical_name} portrait`}
                  className="w-[300%] h-[300%] object-cover object-[15%_5%]"
                />
              </div>
            )}

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
              <label className="text-xs text-gray-500 font-semibold mb-1 block">Appearance</label>
              <textarea
                value={editing.appearance || ""}
                onChange={e => setEditing(prev => ({ ...prev, appearance: e.target.value }))}
                rows={Math.max(2, Math.ceil((editing.appearance || "").length / 35) + 1)}
                className="w-full rounded-lg border border-peach/50 px-3 py-2 text-sm resize-y"
                placeholder="Physical description: hair color, face shape, clothing, accessories..."
              />
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
  onClick,
}: {
  char: CharacterInfo;
  selected: boolean;
  hasSheet: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 border-b border-gray-50 transition-colors ${
        selected ? "bg-coral/10 border-l-2 border-l-coral" : "hover:bg-peach/20"
      }`}
    >
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full shrink-0 ${hasSheet ? "bg-green-400" : "bg-gray-300"}`} />
        <span className={`text-sm truncate ${selected ? "font-bold text-gray-800" : "text-gray-700"}`}>
          {char.canonical_name}
        </span>
      </div>
      <p className="text-[10px] text-gray-400 ml-4 truncate">
        {char.gender || "?"} / {char.role || "?"}{char.description ? ` — ${char.description.slice(0, 40)}...` : ""}
      </p>
    </button>
  );
}
