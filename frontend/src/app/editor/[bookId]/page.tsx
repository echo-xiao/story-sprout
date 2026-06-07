"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useParams } from "next/navigation";
import { RefreshCw, Save, Users, MapPin, Smile, BookOpen } from "lucide-react";
import {
  getChapters,
  getCharacters,
  getChapterSegments,
  updateSegment,
  regenerateSegment,
  regenerateCharacterSheet,
  generateChapter,
  getChapterProgress,
  generateSimplifiedText,
  generateSceneBackground,
  generateSummary,
  getSegmentHistory,
  checkSegmentQuality,
  chatWithAI,
  getRegenStatus,
} from "@/lib/api";
import type { Segment, ChapterInfo, CharacterInfo } from "@/types";

import IllustrationPanel from "@/components/editor/IllustrationPanel";
import QualityCheckPanel from "@/components/editor/QualityCheckPanel";
import CharacterSheetsPanel from "@/components/editor/CharacterSheetsPanel";
import AIChatPanel from "@/components/editor/AIChatPanel";
import VersionsCarousel from "@/components/editor/VersionsCarousel";
import CharacterManagement from "@/components/editor/CharacterManagement";

const SENTIMENTS = ["positive", "negative", "neutral", "tense", "emotional"];

export default function EditorPage() {
  const params = useParams();
  const bookId = params.bookId as string;
  const initApplied = useRef(false);

  // Read initial chapter/segment from URL (sync, no useSearchParams)
  const [initialChapter] = useState<number | null>(() => {
    if (typeof window === "undefined") return null;
    const sp = new URLSearchParams(window.location.search);
    return sp.get("ch") ? +sp.get("ch")! : null;
  });
  const [initialSegment] = useState<number | null>(() => {
    if (typeof window === "undefined") return null;
    const sp = new URLSearchParams(window.location.search);
    return sp.get("seg") ? +sp.get("seg")! : null;
  });

  const [activeTab, setActiveTab] = useState<"editor" | "characters">("editor");
  const [navigateToChar, setNavigateToChar] = useState<string | null>(null);
  const [chapters, setChapters] = useState<Record<string, ChapterInfo>>({});
  const [meta, setMeta] = useState<{ title?: string }>({});
  const [characters, setCharacters] = useState<CharacterInfo[]>([]);
  const [sheets, setSheets] = useState<Record<string, string>>({});
  const [portraits, setPortraits] = useState<Record<string, string>>({});
  const [aliasMap, setAliasMap] = useState<Record<string, string>>({});
  const [selectedChapter, setSelectedChapter] = useState<number | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [selectedSegment, setSelectedSegment] = useState<Segment | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [showCharPanel, setShowCharPanel] = useState(true);
  const [historyImages, setHistoryImages] = useState<Array<{ url: string; version: string; timestamp: number; quality?: any }>>([]);
  const [generatingChapter, setGeneratingChapter] = useState<number | null>(null);
  const [chapterProgress, setChapterProgress] = useState<{ progress: number; current_step: string } | null>(null);
  const [qualityResult, setQualityResult] = useState<{
    overall_score: number;
    segment_id: number;
    page: number;
    character_consistency: { score: number; characters: Array<{ name: string; score: number; issues: string[] }> };
    spelling: { score: number; errors: string[] };
    duplicate_characters: { score: number; duplicates: string[] };
    name_face_mismatch: { score: number; mismatches: string[] };
    character_count: { score: number; expected: number; found: number; missing: string[]; extra: string[] };
    regeneration_feedback: string;
  } | null>(null);
  const [checkingQuality, setCheckingQuality] = useState(false);

  // AI Chat state
  const [chatMessages, setChatMessages] = useState<Array<{ role: string; content: string }>>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);

  const selectedSegId = selectedSegment?.id ?? -1;

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
            // Auto quality check for current segment
            const currentSeg = data.segments?.find((s: Segment) => s.id === selectedSegId);
            if (currentSeg?.illustration_url) {
              setCheckingQuality(true);
              checkSegmentQuality(bookId, currentSeg.id)
                .then((result) => setQualityResult(result))
                .catch((e) => console.error("Auto quality check failed:", e))
                .finally(() => setCheckingQuality(false));
            }
          }
          // Reload character sheets
          const charData = await getCharacters(bookId);
          setSheets(charData.sheets || {});
          setPortraits(charData.portraits || {});
        }
      } catch (e) {
        console.error("Progress poll failed:", e);
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [generatingChapter, bookId, selectedChapter]);

  // Load chapters + characters on mount (retry until preprocess is done)
  useEffect(() => {
    let retryTimer: NodeJS.Timeout;

    async function load() {
      try {
        const chapData = await getChapters(bookId);
        const chapKeys = Object.keys(chapData.chapters || {});

        if (chapKeys.length > 0) {
          // Preprocess is done — load everything
          const charData = await getCharacters(bookId);
          setChapters(chapData.chapters || {});
          setMeta(chapData.meta || {});
          setCharacters(charData.characters || []);
          setSheets(charData.sheets || {});
          setPortraits(charData.portraits || {});
          setAliasMap(charData.alias_map || {});

          const sortedKeys = chapKeys.sort((a, b) => +a - +b);
          const startCh = initialChapter !== null && chapKeys.includes(String(initialChapter))
            ? initialChapter
            : +sortedKeys[0];
          setSelectedChapter(startCh);
          setLoading(false);
          return;
        }
      } catch {
        // API error or 404 — preprocess not ready
      }
      // Still processing — retry in 5 seconds
      retryTimer = setTimeout(load, 5000);
    }

    load();
    return () => clearTimeout(retryTimer);
  }, [bookId]);

  // Load segments + cached consistency when chapter changes
  useEffect(() => {
    setQualityResult(null);
    if (selectedChapter === null) return;
    async function loadSegments() {
      try {
        const data = await getChapterSegments(bookId, selectedChapter!);
        setSegments(data.segments || []);
        if (data.segments?.length > 0) {
          // On first load, restore segment from URL
          if (!initApplied.current && initialSegment !== null) {
            const match = data.segments.find((s: Segment) => s.id === initialSegment);
            setSelectedSegment(match || data.segments[0]);
            initApplied.current = true;
          } else {
            setSelectedSegment(data.segments[0]);
          }
        }
      } catch (e) {
        console.error("Failed to load segments:", e);
      }
    }
    loadSegments();
  }, [bookId, selectedChapter]);

  // Update URL when chapter/segment changes
  useEffect(() => {
    if (selectedChapter === null) return;
    const segId = selectedSegment?.id;
    const url = `/editor/${bookId}?ch=${selectedChapter}${segId != null ? `&seg=${segId}` : ""}`;
    window.history.replaceState(null, "", url);
  }, [bookId, selectedChapter, selectedSegment?.id]);

  // Clear chat when segment changes
  useEffect(() => {
    setChatMessages([]);
    setChatInput("");
  }, [selectedSegId]);

  // Auto-generate simplified text if empty
  useEffect(() => {
    if (selectedSegId < 0 || !selectedSegment || selectedSegment.simplified_text) return;
    generateSimplifiedText(bookId, selectedSegId)
      .then((res) => {
        if (res.simplified_text) {
          const updated = { ...selectedSegment, simplified_text: res.simplified_text };
          setSelectedSegment(updated);
          setSegments((prev) => prev.map((s) => (s.id === selectedSegId ? updated : s)));
        }
      })
      .catch(() => {});
  }, [selectedSegId]);

  // Load history when segment changes
  useEffect(() => {
    if (selectedSegId < 0) return;
    getSegmentHistory(bookId, selectedSegId)
      .then((data) => {
        const images = data.images || [];
        setHistoryImages(images);
        // Auto-load quality for current version
        const current = images.find((img: any) => img.version === "current");
        setQualityResult(current?.quality || null);
      })
      .catch(() => { setHistoryImages([]); setQualityResult(null); });
  }, [bookId, selectedSegId, regenerating]);

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
  const handleRegenerate = async () => {
    if (!selectedSegment) return;
    setRegenerating(true);
    try {
      // Save first
      await updateSegment(bookId, selectedSegment.id, {
        simplified_text: selectedSegment.simplified_text,
        characters_in_scene: selectedSegment.characters_in_scene,
        character_actions: selectedSegment.character_actions,
        scene_background: selectedSegment.scene_background,
        scene_summary: selectedSegment.scene_summary,
        sentiment: selectedSegment.sentiment,
      });
      // Trigger regeneration
      await regenerateSegment(bookId, selectedSegment.id);
      // Poll regen-status every 5s until complete
      const pollInterval = setInterval(async () => {
        try {
          const status = await getRegenStatus(bookId, selectedSegment.id);
          if (status.status === "complete") {
            clearInterval(pollInterval);
            // Reload segments to get new illustration URL
            if (selectedChapter !== null) {
              const data = await getChapterSegments(bookId, selectedChapter);
              const updated = data.segments?.find((s: Segment) => s.id === selectedSegment.id);
              if (updated) {
                setSegments(data.segments || []);
                setSelectedSegment(updated);
              }
            }
            setRegenerating(false);

            // Auto quality check
            setCheckingQuality(true);
            try {
              const result = await checkSegmentQuality(bookId, selectedSegment.id);
              setQualityResult(result);
              // Auto-fix if score < 70%
              if (result.overall_score < 70 && result.regeneration_feedback) {
                const fixMsg = `Quality check found issues (score: ${result.overall_score}%). Please fix the prompts based on this feedback:\n${result.regeneration_feedback}`;
                setChatOpen(true);
                setChatMessages([{ role: "user", content: fixMsg }]);
                setChatLoading(true);
                try {
                  const res = await chatWithAI(bookId, selectedSegment.id, fixMsg, []);
                  setChatMessages(prev => [...prev, { role: "assistant", content: res.reply }]);
                  if (res.updates && Object.keys(res.updates).length > 0) {
                    const seg = selectedSegment;
                    const fix = { ...seg, ...res.updates } as any;
                    if (res.updates.character_actions) {
                      fix.characters_in_scene = (res.updates.character_actions as any[]).map((a: any) => a.name).filter(Boolean);
                    }
                    setSelectedSegment(fix);
                    setSegments(prev => prev.map(s => s.id === seg.id ? fix : s));
                  }
                } catch {} finally { setChatLoading(false); }
              }
            } catch {} finally { setCheckingQuality(false); }
          }
        } catch {}
      }, 5000);
      // Timeout after 3 minutes
      setTimeout(() => { clearInterval(pollInterval); setRegenerating(false); }, 180000);
    } catch (e: any) {
      console.error("Regenerate failed:", e);
      alert(`Regenerate failed: ${e?.message || e}`);
      setRegenerating(false);
    }
  };

  // Update selected segment field
  const updateField = (field: string, value: unknown) => {
    if (!selectedSegment) return;
    setSelectedSegment({ ...selectedSegment, [field]: value });
    setSegments((prev) =>
      prev.map((s) => (s.id === selectedSegment.id ? { ...s, [field]: value } : s))
    );
  };

  // Update character action — must update both fields in one setState call
  const updateAction = (idx: number, field: "name" | "action", value: string) => {
    if (!selectedSegment) return;
    const actions = [...(selectedSegment.character_actions || [])];
    actions[idx] = { ...actions[idx], [field]: value };
    const updated = {
      ...selectedSegment,
      character_actions: actions,
      characters_in_scene: actions.map((a) => a.name).filter(Boolean),
    };
    setSelectedSegment(updated);
    setSegments((prev) => prev.map((s) => (s.id === selectedSegment.id ? updated : s)));
  };

  const addCharacterAction = () => {
    if (!selectedSegment) return;
    const actions = [...(selectedSegment.character_actions || []), { name: "", action: "" }];
    const updated = { ...selectedSegment, character_actions: actions };
    setSelectedSegment(updated);
    setSegments((prev) => prev.map((s) => (s.id === selectedSegment.id ? updated : s)));
  };

  const removeCharacterAction = (idx: number) => {
    if (!selectedSegment) return;
    const actions = (selectedSegment.character_actions || []).filter((_, i) => i !== idx);
    const updated = {
      ...selectedSegment,
      character_actions: actions,
      characters_in_scene: actions.map((a) => a.name).filter(Boolean),
    };
    setSelectedSegment(updated);
    setSegments((prev) => prev.map((s) => (s.id === selectedSegment.id ? updated : s)));
  };

  // Send chat message to AI
  const handleChatSend = async () => {
    if (!chatInput.trim() || !selectedSegment || chatLoading) return;
    const userMsg = chatInput.trim();
    setChatInput("");
    const newMessages = [...chatMessages, { role: "user", content: userMsg }];
    setChatMessages(newMessages);
    setChatLoading(true);
    try {
      const res = await chatWithAI(bookId, selectedSegment.id, userMsg, chatMessages);
      setChatMessages([...newMessages, { role: "assistant", content: res.reply }]);
      // Apply updates to the segment fields
      if (res.updates && Object.keys(res.updates).length > 0) {
        let updated = { ...selectedSegment };
        if (res.updates.simplified_text !== undefined) updated.simplified_text = res.updates.simplified_text as string;
        if (res.updates.scene_background !== undefined) updated.scene_background = res.updates.scene_background as string;
        if (res.updates.scene_summary !== undefined) updated.scene_summary = res.updates.scene_summary as string;
        if (res.updates.sentiment !== undefined) updated.sentiment = res.updates.sentiment as string;
        if (res.updates.character_actions !== undefined) {
          updated.character_actions = res.updates.character_actions as any;
          updated.characters_in_scene = (res.updates.character_actions as any[]).map((a: any) => a.name).filter(Boolean);
        }
        setSelectedSegment(updated);
        setSegments((prev) => prev.map((s) => (s.id === selectedSegment.id ? updated : s)));
      }
    } catch (e) {
      setChatMessages([...newMessages, { role: "assistant", content: "Error: Failed to get AI response." }]);
      console.error("Chat error:", e);
    } finally {
      setChatLoading(false);
    }
  };

  // Handle quality check
  const handleRunQualityCheck = async () => {
    if (!selectedSegment) return;
    setCheckingQuality(true);
    try {
      const result = await checkSegmentQuality(bookId, selectedSegment.id);
      setQualityResult(result);
    } catch (e) {
      console.error("Quality check failed:", e);
    } finally {
      setCheckingQuality(false);
    }
  };

  // Handle regenerate character sheet
  const handleRegenerateSheet = async (canonicalName: string) => {
    await regenerateCharacterSheet(bookId, canonicalName);
    // Refresh after delay
    setTimeout(async () => {
      const data = await getCharacters(bookId);
      setSheets(data.sheets || {});
    }, 15000);
  };

  // Handle version selection from carousel
  const handleSelectVersion = (url: string, quality: any) => {
    if (selectedSegment) {
      setSelectedSegment({ ...selectedSegment, illustration_url: url });
      setQualityResult(quality);
    }
  };

  const [loadingStatus, setLoadingStatus] = useState("Loading book data...");
  const [preprocessProgress, setPreprocessProgress] = useState<{
    progress: number; step: string; steps_done: string[];
    annotated_chapters?: number; total_chapters?: number;
  } | null>(null);

  // Poll preprocess progress while loading
  useEffect(() => {
    if (!loading) return;
    const interval = setInterval(async () => {
      try {
        const prog = await fetch(`/api/book/${bookId}/preprocess/progress`).then(r => r.json());
        setPreprocessProgress(prog);
        setLoadingStatus(prog.step || "Processing...");
      } catch {}
    }, 3000);
    return () => clearInterval(interval);
  }, [loading, bookId]);

  const PREPROCESS_STEPS = [
    { key: "extract_text", label: "Extracting text and chapters" },
    { key: "identify_characters", label: "Identifying characters with AI" },
    { key: "build_aliases", label: "Building alias map" },
    { key: "replace_aliases", label: "Replacing name aliases" },
    { key: "segment_text", label: "Segmenting into scenes" },
    { key: "annotate_complete", label: "Annotating characters, actions, sentiment" },
  ];

  if (loading) {
    const progress = preprocessProgress?.progress || 0;
    const stepsDone = new Set(preprocessProgress?.steps_done || []);

    return (
      <div className="min-h-screen flex items-center justify-center bg-cream">
        <div className="text-center max-w-md w-full px-4">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-coral mx-auto mb-4" />
          <p className="text-gray-700 font-semibold mb-2">
            Preprocessing Book...
          </p>
          <p className="text-gray-500 text-sm mb-2">{loadingStatus}</p>
          {preprocessProgress && (preprocessProgress.annotated_chapters ?? 0) > 0 && (
            <p className="text-coral font-semibold text-sm mb-2">
              {preprocessProgress.annotated_chapters} / {preprocessProgress.total_chapters || "?"} chapters annotated
            </p>
          )}

          {/* Progress bar */}
          <div className="w-full h-3 bg-gray-200 rounded-full overflow-hidden mb-2">
            <div
              className="h-full bg-gradient-to-r from-coral to-sunshine rounded-full transition-all duration-700"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className="text-sm text-gray-400 mb-4">{progress}%</p>

          {/* Steps with color */}
          <div className="bg-white rounded-xl p-4 text-left text-xs space-y-2">
            {PREPROCESS_STEPS.map((s, idx) => {
              const done = stepsDone.has(s.key);
              const current = !done && idx === PREPROCESS_STEPS.findIndex(st => !stepsDone.has(st.key));
              return (
                <div key={s.key} className={`flex items-center gap-2 ${
                  done ? "text-gray-400" : current ? "text-coral font-semibold" : "text-gray-300"
                }`}>
                  <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] ${
                    done ? "bg-sage text-white" : current ? "bg-coral text-white animate-pulse" : "bg-gray-200"
                  }`}>
                    {done ? "\u2713" : idx + 1}
                  </span>
                  {s.label}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-cream" style={{ lineHeight: 1.26 }}>
      {/* Header */}
      <header className="bg-white border-b border-peach/30 px-4 py-2 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <a href="/" className="text-2xl">📖</a>
          <div>
            <h1 className="font-display text-lg font-bold text-gray-800">
              {meta.title || bookId}
            </h1>
            <p className="text-xs text-gray-500">
              {Object.keys(chapters).length} chapters, {characters.length} characters
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {/* Tab Buttons */}
          <div className="flex bg-cream rounded-lg p-0.5">
            <button
              onClick={() => setActiveTab("characters")}
              className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors flex items-center gap-1 ${
                activeTab === "characters" ? "bg-white shadow-sm text-coral" : "text-gray-500 hover:text-gray-700"
              }`}
            >
              <Users size={12} /> Characters
            </button>
            <button
              onClick={() => setActiveTab("editor")}
              className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors flex items-center gap-1 ${
                activeTab === "editor" ? "bg-white shadow-sm text-coral" : "text-gray-500 hover:text-gray-700"
              }`}
            >
              <BookOpen size={12} /> Editor
            </button>
          </div>
          <a
            href={`/book/${bookId}`}
            className="px-3 py-1.5 text-xs rounded-lg bg-coral text-white hover:bg-coral/80 transition-colors flex items-center gap-1 font-semibold"
          >
            View Book
          </a>
        </div>
      </header>

      {/* Character Management Tab */}
      {activeTab === "characters" && (
        <CharacterManagement
          bookId={bookId}
          characters={characters}
          sheets={sheets}
          aliasMap={aliasMap}
          navigateToChar={navigateToChar}
          onCharactersUpdate={(chars, newSheets) => {
            setCharacters(chars);
            setSheets(newSheets);
            setNavigateToChar(null);
          }}
        />
      )}

      {/* Editor Tab */}
      {activeTab === "editor" && (
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
                    onClick={() => setSelectedChapter(selectedChapter === +chIdx ? null : +chIdx)}
                    className={`flex-1 text-left px-3 py-2 text-xs font-semibold flex items-center gap-1 min-w-0 ${
                      selectedChapter === +chIdx ? "text-coral" : "text-gray-700"
                    }`}
                  >
                    <span className="text-[10px] text-gray-400 w-4 shrink-0">{+chIdx + 1}</span>
                    <span className="truncate flex-1">{info.chapter_title}</span>
                    <span className="text-[10px] text-gray-400 shrink-0 ml-1">{info.num_segments}</span>
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
                      className="w-8 h-6 mr-1 text-[9px] bg-coral/80 text-white rounded hover:bg-coral transition-colors disabled:opacity-50 shrink-0"
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

        {/* Main content: 4 columns */}
        <div className="flex-1 flex overflow-hidden">
          {selectedSegment ? (
            <>
              {/* Col 1: Illustration + Original Text */}
              <IllustrationPanel
                selectedSegment={selectedSegment}
                regenerating={regenerating}
              />

              {/* Col 2: Prompt Editing */}
              <div className="w-[36%] shrink-0 overflow-y-auto p-3 border-r border-peach/20 space-y-3">
                {/* Versions Carousel */}
                <VersionsCarousel
                  historyImages={historyImages}
                  selectedSegment={selectedSegment}
                  onSelectVersion={handleSelectVersion}
                />

                {/* Simplified Text */}
                <div className="card !p-3">
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="font-display font-bold text-gray-700 text-xs">Simplified Text</h3>
                    <button
                      onClick={async () => {
                        try {
                          const res = await generateSimplifiedText(bookId, selectedSegment.id);
                          updateField("simplified_text", res.simplified_text);
                        } catch (e) { console.error(e); }
                      }}
                      className="text-[10px] bg-sky/50 hover:bg-sky text-gray-700 px-2 py-0.5 rounded font-semibold"
                    >
                      Generate
                    </button>
                  </div>
                  <textarea
                    value={selectedSegment.simplified_text || ""}
                    onChange={(e) => updateField("simplified_text", e.target.value)}
                    rows={Math.max(3, (selectedSegment.simplified_text || "").split("\n").length + 1)}
                    className="w-full rounded-lg border border-peach/50 p-3 text-xs focus:ring-2 focus:ring-coral/30 focus:border-coral outline-none resize-none !leading-[1.26]"
                    placeholder="Click 'Generate' or type your own..."
                  />
                </div>

                {/* Scene Background */}
                <div className="card !p-3">
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="font-display font-bold text-gray-700 text-xs flex items-center gap-1">
                      <MapPin size={12} /> Scene Background
                    </h3>
                    <button
                      onClick={async () => {
                        try {
                          const res = await generateSceneBackground(bookId, selectedSegment.id);
                          updateField("scene_background", res.scene_background);
                        } catch (e) { console.error(e); }
                      }}
                      className="text-[10px] bg-sky/50 hover:bg-sky text-gray-700 px-2 py-0.5 rounded font-semibold"
                    >
                      Generate
                    </button>
                  </div>
                  <textarea
                    value={selectedSegment.scene_background || ""}
                    onChange={(e) => updateField("scene_background", e.target.value)}
                    rows={Math.max(2, (selectedSegment.scene_background || "").split("\n").length + 1)}
                    className="w-full rounded-lg border border-peach/50 p-3 text-xs focus:ring-2 focus:ring-coral/30 focus:border-coral outline-none resize-none !leading-[1.26]"
                    placeholder="Click 'Generate' or describe the setting..."
                  />
                </div>

                {/* Characters + Actions */}
                <div className="card !p-3">
                  <h3 className="font-display font-bold text-gray-700 mb-2 text-xs flex items-center gap-1">
                    <Users size={12} /> Characters & Actions
                  </h3>
                  <div className="space-y-1.5">
                    {(selectedSegment.character_actions || []).map((ca, idx) => (
                      <div key={idx} className="flex gap-1.5 items-center">
                        <input
                          value={ca.name}
                          onChange={(e) => updateAction(idx, "name", e.target.value)}
                          className="w-1/3 rounded-md border border-peach/50 px-2 py-1.5 text-xs focus:ring-2 focus:ring-coral/30 outline-none !leading-[1.26]"
                          placeholder="Name"
                        />
                        <input
                          value={ca.action}
                          onChange={(e) => updateAction(idx, "action", e.target.value)}
                          className="flex-1 rounded-md border border-peach/50 px-2 py-1.5 text-xs focus:ring-2 focus:ring-coral/30 outline-none !leading-[1.26]"
                          placeholder="What are they doing?"
                        />
                        <button
                          onClick={() => removeCharacterAction(idx)}
                          className="text-red-400 hover:text-red-600 text-sm w-5 shrink-0"
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
                <div className="card !p-3">
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="font-display font-bold text-gray-700 text-xs">Summary & Sentiment</h3>
                    <button
                      onClick={async () => {
                        try {
                          const res = await generateSummary(bookId, selectedSegment.id);
                          const updated = {
                            ...selectedSegment,
                            scene_summary: res.scene_summary,
                            sentiment: res.sentiment,
                          };
                          setSelectedSegment(updated);
                          setSegments((prev) => prev.map((s) => (s.id === selectedSegment.id ? updated : s)));
                        } catch (e) { console.error(e); }
                      }}
                      className="text-[10px] bg-sky/50 hover:bg-sky text-gray-700 px-2 py-0.5 rounded font-semibold"
                    >
                      Generate
                    </button>
                  </div>
                  <textarea
                    value={selectedSegment.scene_summary || ""}
                    onChange={(e) => updateField("scene_summary", e.target.value)}
                    rows={Math.max(2, (selectedSegment.scene_summary || "").split("\n").length + 1)}
                    className="w-full rounded-lg border border-peach/50 p-3 text-xs focus:ring-2 focus:ring-coral/30 focus:border-coral outline-none resize-none !leading-[1.26] mb-2"
                    placeholder="Scene summary..."
                  />
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-gray-500"><Smile size={12} className="inline" /> Sentiment:</span>
                    <select
                      value={selectedSegment.sentiment || "neutral"}
                      onChange={(e) => updateField("sentiment", e.target.value)}
                      className="rounded-md border border-peach/50 px-2 py-1 text-xs focus:ring-2 focus:ring-coral/30 outline-none bg-white"
                    >
                      {SENTIMENTS.map((s) => (
                        <option key={s} value={s}>{s}</option>
                      ))}
                    </select>
                  </div>
                </div>

                {/* AI Chat Panel */}
                <AIChatPanel
                  chatOpen={chatOpen}
                  chatMessages={chatMessages}
                  chatInput={chatInput}
                  chatLoading={chatLoading}
                  onToggle={() => setChatOpen(!chatOpen)}
                  onInputChange={setChatInput}
                  onSend={handleChatSend}
                />

                {/* Action Buttons */}
                <div className="flex gap-2">
                  <button
                    onClick={handleSave}
                    disabled={saving || regenerating}
                    className="btn-secondary text-xs !px-3 !py-1.5 flex items-center gap-1"
                  >
                    <Save size={12} />
                    {saving ? "Saving..." : "Save"}
                  </button>
                  <button
                    onClick={handleRegenerate}
                    disabled={regenerating || saving}
                    className="btn-primary text-xs !px-3 !py-1.5 flex items-center gap-1"
                  >
                    <RefreshCw size={12} className={regenerating ? "animate-spin" : ""} />
                    {regenerating ? "Generating..." : "Save & Regen"}
                  </button>
                </div>
              </div>

              {/* Col 3: Quality Check + Character Sheets side by side */}
              <div className="flex-1 flex overflow-hidden">
                <QualityCheckPanel
                  qualityResult={qualityResult}
                  checkingQuality={checkingQuality}
                  hasIllustration={!!selectedSegment?.illustration_url}
                  onRunCheck={handleRunQualityCheck}
                />

                <CharacterSheetsPanel
                  selectedSegment={selectedSegment}
                  characters={characters}
                  sheets={sheets}
                  portraits={portraits}
                  bookId={bookId}
                  onRegenerateSheet={handleRegenerateSheet}
                  onNavigateToCharacter={(charName) => {
                    setNavigateToChar(charName);
                    setActiveTab("characters");
                  }}
                />
              </div>
            </>
          ) : (
            <div className="flex items-center justify-center flex-1 text-gray-400">
              Select a segment from the left panel
            </div>
          )}
        </div>
      </div>
      )}
    </div>
  );
}
