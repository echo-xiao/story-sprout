"use client";

import { useState, useEffect } from "react";
import { MapPin, RefreshCw } from "lucide-react";
import { getLocations, regenerateSceneSheet, getSceneSheetHistory } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface SceneManagementProps {
  bookId: string;
  initialScene?: string | null;
  onSelectScene?: (name: string) => void;
}

export default function SceneManagement({ bookId, initialScene, onSelectScene }: SceneManagementProps) {
  const [locations, setLocations] = useState<any[]>([]);
  const [sceneSheets, setSceneSheets] = useState<Record<string, string>>({});
  const [selectedLoc, setSelectedLoc] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState<string | null>(null);
  const [editing, setEditing] = useState<Record<string, any>>({});
  const [sheetHistory, setSheetHistory] = useState<Array<{ url: string; version: string; timestamp: number }>>([]);
  const [activeSheetUrl, setActiveSheetUrl] = useState<string | null>(null);

  useEffect(() => {
    getLocations(bookId)
      .then(data => {
        setLocations(data.locations || []);
        setSceneSheets(data.scene_sheets || {});
        if (data.locations?.length > 0) {
          const initial = initialScene
            ? data.locations.find((l: any) => l.name === initialScene)
            : null;
          const first = initial || data.locations[0];
          setSelectedLoc(first.name);
          setEditing(first.visual_details || {});
          onSelectScene?.(first.name);
        }
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [bookId]);

  const selected = locations.find(l => l.name === selectedLoc);
  const majorLocs = locations.filter(l => l.importance === "major");
  const minorLocs = locations.filter(l => l.importance !== "major");

  const selectLoc = (loc: any) => {
    setSelectedLoc(loc.name);
    setEditing(loc.visual_details || {});
    setActiveSheetUrl(null);
    onSelectScene?.(loc.name);
  };

  // Load sheet history when location changes
  useEffect(() => {
    if (!selectedLoc) return;
    getSceneSheetHistory(bookId, selectedLoc)
      .then(data => {
        setSheetHistory(data.images || []);
        const current = data.images?.find(i => i.version === "current");
        setActiveSheetUrl(current?.url || null);
      })
      .catch(() => { setSheetHistory([]); setActiveSheetUrl(null); });
  }, [bookId, selectedLoc, generating]);

  const handleRegenerate = async () => {
    if (!selected) return;
    setGenerating(selected.name);
    try {
      await regenerateSceneSheet(bookId, selected.name);
      setTimeout(async () => {
        const data = await getLocations(bookId);
        setSceneSheets(data.scene_sheets || {});
        setGenerating(null);
      }, 30000);
    } catch {
      setGenerating(null);
    }
  };

  const handleGenerateAll = async () => {
    for (const loc of locations) {
      if (sceneSheets[loc.name]) continue;
      setGenerating(loc.name);
      setSelectedLoc(loc.name);
      try {
        await regenerateSceneSheet(bookId, loc.name);
        await new Promise<void>((resolve) => {
          const poll = setInterval(async () => {
            const data = await getLocations(bookId);
            if (data.scene_sheets?.[loc.name]) {
              setSceneSheets(data.scene_sheets || {});
              clearInterval(poll);
              resolve();
            }
          }, 5000);
          setTimeout(() => { clearInterval(poll); resolve(); }, 120000);
        });
      } catch {}
    }
    setGenerating(null);
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-400">
        Loading locations...
      </div>
    );
  }

  if (locations.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-400">
        <div className="text-center">
          <MapPin size={32} className="mx-auto mb-2" />
          <p className="text-sm">No locations identified yet.</p>
          <p className="text-xs">Run preprocess to identify key locations.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Left: Location List */}
      <div className="w-64 bg-white border-r border-peach/30 overflow-y-auto shrink-0">
        <div className="px-3 py-2 text-[10px] font-bold text-gray-400 uppercase tracking-wider bg-cream/50 flex items-center justify-between">
          <span>Major Locations ({majorLocs.length})</span>
          <button
            onClick={handleGenerateAll}
            disabled={generating !== null}
            className="text-[9px] bg-coral/80 text-white px-2 py-0.5 rounded hover:bg-coral transition-colors disabled:opacity-50"
          >
            {generating ? "Generating..." : "Gen All"}
          </button>
        </div>
        {majorLocs.map(loc => (
          <LocListItem
            key={loc.name}
            loc={loc}
            selected={selectedLoc === loc.name}
            hasSheet={!!sceneSheets[loc.name]}
            onClick={() => selectLoc(loc)}
          />
        ))}
        {minorLocs.length > 0 && (
          <>
            <div className="px-3 py-2 text-[10px] font-bold text-gray-400 uppercase tracking-wider bg-cream/50">
              Minor Locations ({minorLocs.length})
            </div>
            {minorLocs.map(loc => (
              <LocListItem
                key={loc.name}
                loc={loc}
                selected={selectedLoc === loc.name}
                hasSheet={!!sceneSheets[loc.name]}
                onClick={() => selectLoc(loc)}
              />
            ))}
          </>
        )}
      </div>

      {/* Middle + Right */}
      {selected ? (
        <div className="flex-1 flex overflow-hidden">
          {/* Middle: Scene Sheet + History thumbnails */}
          <div className="flex-1 flex overflow-hidden border-r border-peach/20">
            {/* Main sheet image */}
            <div className="flex-1 overflow-y-auto p-6 flex flex-col">
              <h2 className="font-display text-lg font-bold text-gray-800 mb-3 shrink-0">{selected.name}</h2>
              <div className="flex-1 flex items-center justify-center min-h-0">
                {(activeSheetUrl || sceneSheets[selected.name]) ? (
                  <img
                    src={`${API_BASE}${activeSheetUrl || sceneSheets[selected.name]}?t=${Date.now()}`}
                    alt={selected.name}
                    className="max-h-[calc(100vh-180px)] max-w-full rounded-xl shadow-md object-contain"
                  />
                ) : generating === selected.name ? (
                  <div className="w-full max-w-md aspect-square bg-peach/10 rounded-xl flex flex-col items-center justify-center gap-3">
                    <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-coral" />
                    <p className="text-sm text-gray-500">Generating scene...</p>
                    <p className="text-xs text-gray-400">~30 seconds</p>
                  </div>
                ) : (
                  <div className="w-full max-w-md aspect-square bg-peach/20 rounded-xl flex flex-col items-center justify-center text-gray-400 gap-2">
                    <MapPin size={32} />
                    <p className="text-xs">No scene reference yet</p>
                    <p className="text-[10px]">Click Save & Regenerate to create</p>
                  </div>
                )}
              </div>
            </div>

            {/* History thumbnails (vertical, right side of sheet) */}
            {sheetHistory.length > 1 && (
              <div className="w-[320px] shrink-0 overflow-y-auto p-4 space-y-3 border-l border-peach/20">
                <p className="text-xs text-gray-500 font-semibold">Versions ({sheetHistory.length})</p>
                {sheetHistory.map((img, idx) => (
                  <div key={idx}>
                    <img
                      src={`${API_BASE}${img.url}?t=${img.timestamp}`}
                      alt={img.version === "current" ? "Current" : `v${sheetHistory.length - idx}`}
                      onClick={() => setActiveSheetUrl(img.url)}
                      className={`w-full rounded-xl cursor-pointer border-2 transition-colors ${
                        (activeSheetUrl || sceneSheets[selected.name]) === img.url
                          ? "border-coral shadow-md"
                          : "border-transparent hover:border-coral/50"
                      }`}
                    />
                    <p className="text-[10px] text-gray-400 text-center mt-1">
                      {img.version === "current" ? "Current" : `Version ${sheetHistory.length - idx}`}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Right: Details + Save & Regenerate */}
          <div className="w-[320px] shrink-0 overflow-y-auto p-5 space-y-3">
            <div>
              <label className="text-xs text-gray-500 font-semibold mb-1 block">Description</label>
              <p className="text-sm text-gray-700 bg-cream/50 rounded-lg p-3">{selected.description || "No description"}</p>
            </div>

            {/* Editable Visual Details */}
            <div className="border border-peach/30 rounded-lg p-3 space-y-2">
              <p className="text-[10px] text-gray-400 font-semibold uppercase tracking-wider">Visual Details</p>
              {[
                { key: "setting", label: "Setting", placeholder: "e.g. indoor, outdoor" },
                { key: "time_period", label: "Period", placeholder: "e.g. 1780s France" },
                { key: "architecture", label: "Architecture", placeholder: "e.g. narrow stone staircase" },
                { key: "lighting", label: "Lighting", placeholder: "e.g. dim candlelight" },
                { key: "atmosphere", label: "Atmosphere", placeholder: "e.g. tense, brooding" },
                { key: "key_objects", label: "Key Objects", placeholder: "e.g. wine barrels, candles" },
                { key: "colors", label: "Colors", placeholder: "e.g. dark brown, red, grey" },
              ].map(({ key, label, placeholder }) => (
                <div key={key} className="flex items-start gap-2">
                  <label className="text-[10px] text-gray-500 w-20 shrink-0 text-right pt-1">{label}</label>
                  <textarea
                    value={editing[key] || ""}
                    onChange={e => setEditing(prev => ({ ...prev, [key]: e.target.value }))}
                    rows={Math.max(1, Math.ceil((editing[key] || "").length / 25))}
                    className="flex-1 rounded-md border border-peach/40 px-2 py-1 text-xs resize-none"
                    placeholder={placeholder}
                  />
                </div>
              ))}
            </div>

            {selected.chapters_appeared && selected.chapters_appeared.length > 0 && (
              <div>
                <label className="text-xs text-gray-500 font-semibold mb-1 block">Appears in chapters</label>
                <div className="flex flex-wrap gap-1.5">
                  {selected.chapters_appeared.map((ch: number) => (
                    <span key={ch} className="px-2 py-0.5 bg-sky/30 text-xs rounded-full text-gray-700">
                      Ch {ch + 1}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {selected.aliases && selected.aliases.length > 0 && (
              <div>
                <label className="text-xs text-gray-500 font-semibold mb-1 block">Also called</label>
                <div className="flex flex-wrap gap-1.5">
                  {selected.aliases.map((alias: string) => (
                    <span key={alias} className="px-2 py-0.5 bg-lavender/30 text-xs rounded-full text-gray-700">
                      {alias}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div className="flex gap-2 pt-2">
              <button
                onClick={handleRegenerate}
                disabled={generating !== null}
                className="btn-primary text-sm !px-4 !py-2 flex items-center gap-1.5"
              >
                <RefreshCw size={14} className={generating === selected.name ? "animate-spin" : ""} />
                {generating === selected.name ? "Generating..." : "Save & Regenerate"}
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center text-gray-400">
          Select a location from the left
        </div>
      )}
    </div>
  );
}

function LocListItem({
  loc,
  selected,
  hasSheet,
  onClick,
}: {
  loc: any;
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
          {loc.name}
        </span>
      </div>
      {loc.description && (
        <p className="text-[10px] text-gray-400 ml-4 truncate">{loc.description}</p>
      )}
    </button>
  );
}
