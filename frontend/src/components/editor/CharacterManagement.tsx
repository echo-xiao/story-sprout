"use client";

import { useState } from "react";
import { Users, RefreshCw, Save } from "lucide-react";
import { updateCharacter, regenerateCharacterSheet, getCharacters } from "@/lib/api";
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

  const selected = characters.find(c => c.canonical_name === selectedChar);

  const selectChar = (char: CharacterInfo) => {
    setSelectedChar(char.canonical_name);
    setEditing({
      gender: char.gender || "unknown",
      role: char.role || "supporting",
      appearance: char.appearance || "",
      description: char.description || "",
    });
  };

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

      {/* Right: Detail + Sheet */}
      {selected ? (
        <div className="flex-1 flex overflow-hidden">
          {/* Middle: Edit Fields */}
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            <h2 className="font-display text-lg font-bold text-gray-800">{selected.canonical_name}</h2>

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
                rows={4}
                className="w-full rounded-lg border border-peach/50 px-3 py-2 text-sm resize-none"
                placeholder="Physical description: hair color, face shape, clothing, accessories..."
              />
            </div>

            <div>
              <label className="text-xs text-gray-500 font-semibold mb-1 block">Description</label>
              <textarea
                value={editing.description || ""}
                onChange={e => setEditing(prev => ({ ...prev, description: e.target.value }))}
                rows={3}
                className="w-full rounded-lg border border-peach/50 px-3 py-2 text-sm resize-none"
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
              <button onClick={handleSave} disabled={saving} className="btn-primary text-sm !px-4 !py-2 flex items-center gap-1.5">
                <Save size={14} />
                {saving ? "Saving..." : "Save Changes"}
              </button>
              <button onClick={handleRegenSheet} disabled={regenning} className="btn-secondary text-sm !px-4 !py-2 flex items-center gap-1.5">
                <RefreshCw size={14} className={regenning ? "animate-spin" : ""} />
                {regenning ? "Generating..." : "Regenerate Sheet"}
              </button>
            </div>
          </div>

          {/* Right: Character Sheet Image */}
          <div className="w-80 shrink-0 overflow-y-auto p-6 border-l border-peach/20 bg-cream/30">
            <label className="text-xs text-gray-500 font-semibold mb-2 block">Character Sheet</label>
            {sheets[selected.canonical_name] ? (
              <img
                src={`${API_BASE}${sheets[selected.canonical_name]}`}
                alt={selected.canonical_name}
                className="w-full rounded-xl shadow-md"
              />
            ) : regenning ? (
              <div className="w-full aspect-square bg-peach/10 rounded-xl flex flex-col items-center justify-center gap-3">
                <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-coral" />
                <p className="text-sm text-gray-500">Generating sheet...</p>
                <p className="text-xs text-gray-400">~30 seconds</p>
              </div>
            ) : (
              <div className="w-full aspect-square bg-peach/20 rounded-xl flex flex-col items-center justify-center text-gray-400 gap-2">
                <Users size={32} />
                <p className="text-xs">No sheet yet</p>
                <p className="text-[10px]">Click "Regenerate Sheet" to create</p>
              </div>
            )}
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
