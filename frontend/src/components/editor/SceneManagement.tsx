"use client";

import { useState, useEffect } from "react";
import { MapPin, RefreshCw } from "lucide-react";
import { getLocations, regenerateSceneSheet } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface SceneManagementProps {
  bookId: string;
}

export default function SceneManagement({ bookId }: SceneManagementProps) {
  const [locations, setLocations] = useState<any[]>([]);
  const [sceneSheets, setSceneSheets] = useState<Record<string, string>>({});
  const [selectedLoc, setSelectedLoc] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState<string | null>(null);

  useEffect(() => {
    getLocations(bookId)
      .then(data => {
        setLocations(data.locations || []);
        setSceneSheets(data.scene_sheets || {});
        if (data.locations?.length > 0) setSelectedLoc(data.locations[0].name);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [bookId]);

  const selected = locations.find(l => l.name === selectedLoc);
  const majorLocs = locations.filter(l => l.importance === "major");
  const minorLocs = locations.filter(l => l.importance !== "major");

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

  const handleGenerateAll = async () => {
    for (const loc of locations) {
      if (sceneSheets[loc.name]) continue;
      setGenerating(loc.name);
      setSelectedLoc(loc.name);
      try {
        await regenerateSceneSheet(bookId, loc.name);
        // Wait for it to complete
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
            onClick={() => setSelectedLoc(loc.name)}
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
                onClick={() => setSelectedLoc(loc.name)}
              />
            ))}
          </>
        )}
      </div>

      {/* Middle: Scene Reference Image */}
      {selected && (
        <div className="flex-1 overflow-y-auto p-6 border-r border-peach/20 flex flex-col">
          <h2 className="font-display text-lg font-bold text-gray-800 mb-3 shrink-0">{selected.name}</h2>
          <div className="flex-1 flex items-center justify-center min-h-0">
            {sceneSheets[selected.name] ? (
              <div className="flex flex-col items-center gap-3">
                <img
                  src={`${API_BASE}${sceneSheets[selected.name]}?t=${Date.now()}`}
                  alt={selected.name}
                  className="max-h-[calc(100vh-220px)] max-w-full rounded-xl shadow-md object-contain"
                />
                <button
                  onClick={async () => {
                    setGenerating(selected.name);
                    try {
                      await regenerateSceneSheet(bookId, selected.name);
                      // Poll for completion
                      const poll = setInterval(async () => {
                        const data = await getLocations(bookId);
                        if (data.scene_sheets?.[selected.name] !== sceneSheets[selected.name]) {
                          setSceneSheets(data.scene_sheets || {});
                          setGenerating(null);
                          clearInterval(poll);
                        }
                      }, 5000);
                      setTimeout(() => { clearInterval(poll); setGenerating(null); }, 120000);
                    } catch { setGenerating(null); }
                  }}
                  disabled={generating !== null}
                  className="btn-secondary text-xs !px-3 !py-1.5 flex items-center gap-1"
                >
                  <RefreshCw size={12} className={generating === selected.name ? "animate-spin" : ""} />
                  {generating === selected.name ? "Regenerating..." : "Regenerate"}
                </button>
              </div>
            ) : (
              <div className="w-full max-w-md aspect-video bg-peach/20 rounded-xl flex flex-col items-center justify-center text-gray-400 gap-3">
                <MapPin size={32} />
                <p className="text-xs">No scene reference yet</p>
                <button
                  onClick={async () => {
                    setGenerating(selected.name);
                    try {
                      await regenerateSceneSheet(bookId, selected.name);
                      // Poll for completion
                      const poll = setInterval(async () => {
                        const data = await getLocations(bookId);
                        if (data.scene_sheets?.[selected.name]) {
                          setSceneSheets(data.scene_sheets || {});
                          setGenerating(null);
                          clearInterval(poll);
                        }
                      }, 5000);
                      setTimeout(() => { clearInterval(poll); setGenerating(null); }, 120000);
                    } catch { setGenerating(null); }
                  }}
                  disabled={generating !== null}
                  className="btn-primary text-xs !px-4 !py-2 flex items-center gap-1.5"
                >
                  <RefreshCw size={14} className={generating === selected.name ? "animate-spin" : ""} />
                  {generating === selected.name ? "Generating..." : "Generate Scene"}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Right: Location Details */}
      {selected && (
        <div className="w-[320px] shrink-0 overflow-y-auto p-5 space-y-3">
          <div>
            <label className="text-xs text-gray-500 font-semibold mb-1 block">Description</label>
            <p className="text-sm text-gray-700 bg-cream/50 rounded-lg p-3">{selected.description || "No description"}</p>
          </div>

          {selected.visual_details && (
            <div className="border border-peach/30 rounded-lg p-3 space-y-2">
              <p className="text-[10px] text-gray-400 font-semibold uppercase tracking-wider">Visual Details</p>
              {[
                { key: "setting", label: "Setting" },
                { key: "time_period", label: "Period" },
                { key: "architecture", label: "Architecture" },
                { key: "lighting", label: "Lighting" },
                { key: "atmosphere", label: "Atmosphere" },
                { key: "key_objects", label: "Key Objects" },
                { key: "colors", label: "Colors" },
              ].map(({ key, label }) => {
                const val = selected.visual_details[key];
                if (!val) return null;
                return (
                  <div key={key} className="flex items-start gap-2">
                    <label className="text-[10px] text-gray-500 w-20 shrink-0 text-right pt-0.5">{label}</label>
                    <p className="text-xs text-gray-700 flex-1">{val}</p>
                  </div>
                );
              })}
            </div>
          )}

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
