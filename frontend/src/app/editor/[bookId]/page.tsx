"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import { ChevronRight, RefreshCw, Save, Image, Users, MapPin, Smile, BookOpen } from "lucide-react";
import {
  getChapters,
  getCharacters,
  getChapterSegments,
  updateSegment,
  regenerateSegment,
  regenerateCharacterSheet,
  generateChapter,
  getChapterProgress,
} from "@/lib/api";
import type { Segment, CharacterAction, ChapterInfo, CharacterInfo } from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const SENTIMENTS = ["positive", "negative", "neutral", "tense", "emotional"];

export default function EditorPage() {
  const params = useParams();
  const bookId = params.bookId as string;

  const [chapters, setChapters] = useState<Record<string, ChapterInfo>>({});
  const [meta, setMeta] = useState<{ title?: string }>({});
  const [characters, setCharacters] = useState<CharacterInfo[]>([]);
  const [sheets, setSheets] = useState<Record<string, string>>({});
  const [selectedChapter, setSelectedChapter] = useState<number | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [selectedSegment, setSelectedSegment] = useState<Segment | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [showCharPanel, setShowCharPanel] = useState(true);
  const [generatingChapter, setGeneratingChapter] = useState<number | null>(null);
  const [chapterProgress, setChapterProgress] = useState<{ progress: number; current_step: string } | null>(null);

  // Poll progress when generating
  useEffect(() => {
    if (generatingChapter === null) return;
    const interval = setInterval(async () => {
      try {
        const prog = await getChapterProgress(bookId, generatingChapter);
        setChapterProgress(prog);
        if (prog.status === "complete") {
          setGeneratingChapter(null);
          setChapterProgress(null);
          // Reload segments
          if (selectedChapter === generatingChapter) {
            const data = await getChapterSegments(bookId, generatingChapter);
            setSegments(data.segments || []);
          }
          // Reload character sheets
          const charData = await getCharacters(bookId);
          setSheets(charData.sheets || {});
        }
      } catch (e) {
        console.error("Progress poll failed:", e);
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [generatingChapter, bookId, selectedChapter]);

  // Load chapters + characters on mount
  useEffect(() => {
    async function load() {
      try {
        const [chapData, charData] = await Promise.all([
          getChapters(bookId),
          getCharacters(bookId),
        ]);
        setChapters(chapData.chapters || {});
        setMeta(chapData.meta || {});
        setCharacters(charData.characters || []);
        setSheets(charData.sheets || {});

        // Auto-select first chapter
        const firstCh = Object.keys(chapData.chapters || {}).sort((a, b) => +a - +b)[0];
        if (firstCh !== undefined) {
          setSelectedChapter(+firstCh);
        }
      } catch (e) {
        console.error("Failed to load:", e);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [bookId]);

  // Load segments when chapter changes
  useEffect(() => {
    if (selectedChapter === null) return;
    async function loadSegments() {
      try {
        const data = await getChapterSegments(bookId, selectedChapter!);
        setSegments(data.segments || []);
        if (data.segments?.length > 0) {
          setSelectedSegment(data.segments[0]);
        }
      } catch (e) {
        console.error("Failed to load segments:", e);
      }
    }
    loadSegments();
  }, [bookId, selectedChapter]);

  // Save segment changes
  const handleSave = useCallback(async () => {
    if (!selectedSegment) return;
    setSaving(true);
    try {
      await updateSegment(bookId, selectedSegment.id, {
        text: selectedSegment.text,
        simplified_text: selectedSegment.simplified_text,
        characters_in_scene: selectedSegment.characters_in_scene,
        character_actions: selectedSegment.character_actions,
        scene_background: selectedSegment.scene_background,
        scene_summary: selectedSegment.scene_summary,
        sentiment: selectedSegment.sentiment,
      });
    } catch (e) {
      console.error("Save failed:", e);
    } finally {
      setSaving(false);
    }
  }, [bookId, selectedSegment]);

  // Regenerate illustration
  const handleRegenerate = useCallback(async () => {
    if (!selectedSegment) return;
    setRegenerating(true);
    try {
      await handleSave(); // Save first
      await regenerateSegment(bookId, selectedSegment.id);
      // Poll for completion (simple timeout)
      setTimeout(async () => {
        if (selectedChapter !== null) {
          const data = await getChapterSegments(bookId, selectedChapter);
          setSegments(data.segments || []);
          const updated = data.segments?.find((s: Segment) => s.id === selectedSegment.id);
          if (updated) setSelectedSegment(updated);
        }
        setRegenerating(false);
      }, 30000);
    } catch (e) {
      console.error("Regenerate failed:", e);
      setRegenerating(false);
    }
  }, [bookId, selectedSegment, selectedChapter, handleSave]);

  // Update selected segment field
  const updateField = (field: string, value: unknown) => {
    if (!selectedSegment) return;
    setSelectedSegment({ ...selectedSegment, [field]: value });
    // Also update in segments list
    setSegments((prev) =>
      prev.map((s) => (s.id === selectedSegment.id ? { ...s, [field]: value } : s))
    );
  };

  // Update character action
  const updateAction = (idx: number, field: "name" | "action", value: string) => {
    if (!selectedSegment) return;
    const actions = [...(selectedSegment.character_actions || [])];
    actions[idx] = { ...actions[idx], [field]: value };
    updateField("character_actions", actions);
    // Also sync characters_in_scene
    updateField("characters_in_scene", actions.map((a) => a.name));
  };

  const addCharacterAction = () => {
    if (!selectedSegment) return;
    const actions = [...(selectedSegment.character_actions || []), { name: "", action: "" }];
    updateField("character_actions", actions);
  };

  const removeCharacterAction = (idx: number) => {
    if (!selectedSegment) return;
    const actions = (selectedSegment.character_actions || []).filter((_, i) => i !== idx);
    updateField("character_actions", actions);
    updateField("characters_in_scene", actions.map((a) => a.name));
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-cream">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-coral mx-auto mb-4" />
          <p className="text-gray-600">Loading book data...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-cream">
      {/* Header */}
      <header className="bg-white border-b border-peach/30 px-4 py-3 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <a href="/" className="text-2xl">📖</a>
          <div>
            <h1 className="font-display text-lg font-bold text-gray-800">
              {meta.title || bookId}
            </h1>
            <p className="text-xs text-gray-500">
              {Object.keys(chapters).length} chapters, {segments.length} segments
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowCharPanel(!showCharPanel)}
            className="px-3 py-1.5 text-sm rounded-lg bg-lavender/50 hover:bg-lavender transition-colors flex items-center gap-1"
          >
            <Users size={14} /> Characters
          </button>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Left Panel: Chapters + Segments */}
        <div className="w-64 bg-white border-r border-peach/30 overflow-y-auto shrink-0">
          {Object.entries(chapters)
            .sort(([a], [b]) => +a - +b)
            .map(([chIdx, info]) => (
              <div key={chIdx}>
                <div
                  className={`flex items-center border-b border-gray-100 transition-colors ${
                    selectedChapter === +chIdx
                      ? "bg-coral/10"
                      : "hover:bg-peach/30"
                  }`}
                >
                  <button
                    onClick={() => setSelectedChapter(+chIdx)}
                    className={`flex-1 text-left px-3 py-2 text-xs font-semibold flex items-center gap-1.5 ${
                      selectedChapter === +chIdx ? "text-coral" : "text-gray-700"
                    }`}
                  >
                    <span className="text-[10px] text-gray-400 w-5 shrink-0">{+chIdx + 1}</span>
                    <span className="truncate">{info.chapter_title}</span>
                    <span className="ml-auto text-[10px] text-gray-400 shrink-0">{info.num_segments}</span>
                  </button>
                  {generatingChapter === +chIdx && chapterProgress ? (
                    <div className="mr-2 w-20">
                      <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-coral rounded-full transition-all duration-500"
                          style={{ width: `${chapterProgress.progress}%` }}
                        />
                      </div>
                      <p className="text-[8px] text-gray-400 text-center mt-0.5">
                        {chapterProgress.current_step}
                      </p>
                    </div>
                  ) : (
                    <button
                      onClick={async (e) => {
                        e.stopPropagation();
                        setGeneratingChapter(+chIdx);
                        try {
                          await generateChapter(bookId, +chIdx);
                        } catch (err) {
                          console.error(err);
                        }
                      }}
                      disabled={generatingChapter !== null}
                      className="px-2 py-1 mr-2 text-[10px] bg-coral/80 text-white rounded hover:bg-coral transition-colors disabled:opacity-50"
                      title="Generate illustrations for this chapter"
                    >
                      Gen
                    </button>
                  )}
                </div>

                {selectedChapter === +chIdx &&
                  segments.map((seg, idx) => (
                    <button
                      key={seg.id}
                      onClick={() => setSelectedSegment(seg)}
                      className={`w-full text-left px-6 py-2 text-xs border-b border-gray-50 transition-colors ${
                        selectedSegment?.id === seg.id
                          ? "bg-sky/20 text-gray-800"
                          : "hover:bg-gray-50 text-gray-500"
                      }`}
                    >
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-[10px] text-gray-400">
                          {idx + 1}
                        </span>
                        <span className="truncate">
                          {seg.scene_summary || seg.text?.slice(0, 40) + "..."}
                        </span>
                      </div>
                      {seg.characters_in_scene?.length > 0 && (
                        <div className="mt-0.5 flex gap-1 flex-wrap">
                          {seg.characters_in_scene.slice(0, 3).map((c) => (
                            <span
                              key={c}
                              className="px-1 py-0.5 bg-sage/30 text-[9px] rounded"
                            >
                              {c.split(" ").pop()}
                            </span>
                          ))}
                        </div>
                      )}
                    </button>
                  ))}
              </div>
            ))}
        </div>

        {/* Center: Segment Editor */}
        <div className="flex-1 overflow-y-auto p-6">
          {selectedSegment ? (
            <div className="flex gap-6 h-full">
              {/* Left: Illustration (sticky) */}
              <div className="w-1/2 shrink-0">
                <div className="sticky top-0 pt-0">
                  <div className="card">
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="font-display font-bold text-gray-700 flex items-center gap-2">
                        <Image size={16} /> Illustration
                      </h3>
                      <button
                        onClick={handleRegenerate}
                        disabled={regenerating}
                        className="btn-primary text-xs !px-3 !py-1.5 flex items-center gap-1.5"
                      >
                        <RefreshCw size={12} className={regenerating ? "animate-spin" : ""} />
                        {regenerating ? "Generating..." : "Regenerate"}
                      </button>
                    </div>
                    {selectedSegment.illustration_url ? (
                      <img
                        src={`${API_BASE}${selectedSegment.illustration_url}`}
                        alt="Page illustration"
                        className="w-full rounded-2xl shadow-md"
                      />
                    ) : (
                      <div className="w-full aspect-square bg-peach/20 rounded-2xl flex items-center justify-center text-gray-400">
                        No illustration yet
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* Right: Edit fields */}
              <div className="w-1/2 space-y-4 overflow-y-auto pb-6">
                {/* Original Text */}
                <div className="card !p-4">
                  <h3 className="font-display font-bold text-gray-700 mb-2 text-sm flex items-center gap-2">
                    <BookOpen size={14} /> Original Text
                  </h3>
                  <div className="text-xs text-gray-600 bg-cream/50 rounded-lg p-3 max-h-32 overflow-y-auto leading-relaxed">
                    {selectedSegment.text}
                  </div>
                </div>

                {/* Simplified Text */}
                <div className="card !p-4">
                  <h3 className="font-display font-bold text-gray-700 mb-2 text-sm">
                    Simplified Text
                  </h3>
                  <textarea
                    value={selectedSegment.simplified_text || ""}
                    onChange={(e) => updateField("simplified_text", e.target.value)}
                    className="w-full rounded-lg border border-peach/50 p-3 text-xs focus:ring-2 focus:ring-coral/30 focus:border-coral outline-none resize-y min-h-[60px]"
                    placeholder="Simplified text for children"
                  />
                </div>

                {/* Scene Background */}
                <div className="card !p-4">
                  <h3 className="font-display font-bold text-gray-700 mb-2 text-sm flex items-center gap-2">
                    <MapPin size={14} /> Scene Background
                  </h3>
                  <textarea
                    value={selectedSegment.scene_background || ""}
                    onChange={(e) => updateField("scene_background", e.target.value)}
                    className="w-full rounded-lg border border-peach/50 p-3 text-xs focus:ring-2 focus:ring-coral/30 focus:border-coral outline-none resize-y min-h-[50px]"
                    placeholder="Describe the physical setting..."
                  />
                </div>

                {/* Characters + Actions */}
                <div className="card !p-4">
                  <h3 className="font-display font-bold text-gray-700 mb-2 text-sm flex items-center gap-2">
                    <Users size={14} /> Characters & Actions
                  </h3>
                  <div className="space-y-2">
                    {(selectedSegment.character_actions || []).map((ca, idx) => (
                      <div key={idx} className="flex gap-1.5 items-start">
                        <input
                          value={ca.name}
                          onChange={(e) => updateAction(idx, "name", e.target.value)}
                          className="w-1/3 rounded-md border border-peach/50 px-2 py-1.5 text-xs focus:ring-2 focus:ring-coral/30 outline-none"
                          placeholder="Name"
                        />
                        <input
                          value={ca.action}
                          onChange={(e) => updateAction(idx, "action", e.target.value)}
                          className="flex-1 rounded-md border border-peach/50 px-2 py-1.5 text-xs focus:ring-2 focus:ring-coral/30 outline-none"
                          placeholder="Action"
                        />
                        <button
                          onClick={() => removeCharacterAction(idx)}
                          className="text-red-400 hover:text-red-600 px-1 py-1 text-xs"
                        >
                          &times;
                        </button>
                      </div>
                    ))}
                    <button
                      onClick={addCharacterAction}
                      className="text-xs text-coral hover:text-coral/80 font-semibold"
                    >
                      + Add character
                    </button>
                  </div>
                </div>

                {/* Summary + Sentiment */}
                <div className="card !p-4">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <h3 className="font-display font-bold text-gray-700 mb-1.5 text-sm">Summary</h3>
                      <input
                        value={selectedSegment.scene_summary || ""}
                        onChange={(e) => updateField("scene_summary", e.target.value)}
                        className="w-full rounded-md border border-peach/50 px-2 py-1.5 text-xs focus:ring-2 focus:ring-coral/30 outline-none"
                      />
                    </div>
                    <div>
                      <h3 className="font-display font-bold text-gray-700 mb-1.5 text-sm flex items-center gap-1">
                        <Smile size={12} /> Sentiment
                      </h3>
                      <select
                        value={selectedSegment.sentiment || "neutral"}
                        onChange={(e) => updateField("sentiment", e.target.value)}
                        className="w-full rounded-md border border-peach/50 px-2 py-1.5 text-xs focus:ring-2 focus:ring-coral/30 outline-none bg-white"
                      >
                        {SENTIMENTS.map((s) => (
                          <option key={s} value={s}>{s}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                </div>

                {/* Save Buttons */}
                <div className="flex gap-2">
                  <button
                    onClick={handleSave}
                    disabled={saving}
                    className="btn-primary text-sm !px-4 !py-2 flex items-center gap-1.5"
                  >
                    <Save size={14} />
                    {saving ? "Saving..." : "Save"}
                  </button>
                  <button
                    onClick={handleRegenerate}
                    disabled={regenerating}
                    className="btn-secondary text-sm !px-4 !py-2 flex items-center gap-1.5"
                  >
                    <RefreshCw size={14} className={regenerating ? "animate-spin" : ""} />
                    {regenerating ? "Generating..." : "Save & Regenerate"}
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center h-full text-gray-400">
              Select a segment from the left panel
            </div>
          )}
        </div>

        {/* Right Panel: Character Sheets */}
        {showCharPanel && (
          <div className="w-72 bg-white border-l border-peach/30 overflow-y-auto shrink-0 p-4">
            <h3 className="font-display font-bold text-gray-700 mb-3">Character Sheets</h3>
            <div className="space-y-4">
              {characters
                .filter((c) =>
                  sheets[c.canonical_name] &&
                  selectedSegment?.characters_in_scene?.includes(c.canonical_name)
                )
                .map((char) => {
                  const sheetUrl = sheets[char.canonical_name];

                  return (
                    <div key={char.canonical_name} className="border border-peach/30 rounded-xl p-3">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-sm font-semibold text-gray-700">
                          {char.canonical_name}
                        </span>
                        <span className="text-[10px] px-1.5 py-0.5 bg-sage/30 rounded text-gray-600">
                          {char.gender} / {char.role}
                        </span>
                      </div>
                      {sheetUrl ? (
                        <img
                          src={`${API_BASE}${sheetUrl}`}
                          alt={char.canonical_name}
                          className="w-full rounded-lg mb-2"
                        />
                      ) : (
                        <div className="w-full h-24 bg-peach/20 rounded-lg flex items-center justify-center text-xs text-gray-400 mb-2">
                          No sheet
                        </div>
                      )}
                      <p className="text-[10px] text-gray-500 mb-2">{char.description}</p>
                      <button
                        onClick={async () => {
                          await regenerateCharacterSheet(bookId, char.canonical_name);
                          // Refresh after delay
                          setTimeout(async () => {
                            const data = await getCharacters(bookId);
                            setSheets(data.sheets || {});
                          }, 15000);
                        }}
                        className="text-xs text-coral hover:text-coral/80 font-semibold"
                      >
                        Regenerate Sheet
                      </button>
                    </div>
                  );
                })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
