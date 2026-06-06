"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useSearchParams, useRouter } from "next/navigation";
import { ChevronRight, RefreshCw, Save, Image, Users, MapPin, Smile, BookOpen, Shield, Paintbrush, MessageCircle, Send } from "lucide-react";
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
  checkChapterConsistency,
  getChapterConsistency,
  checkSegmentQuality,
  chatWithAI,
} from "@/lib/api";
import type { Segment, CharacterAction, ChapterInfo, CharacterInfo } from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const SENTIMENTS = ["positive", "negative", "neutral", "tense", "emotional"];

export default function EditorPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();
  const bookId = params.bookId as string;
  const initialChapter = searchParams.get("ch") ? +searchParams.get("ch")! : null;
  const initialSegment = searchParams.get("seg") ? +searchParams.get("seg")! : null;
  const initApplied = useRef(false);

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
      // Quality result cleared on chapter change — checked per-segment on demand
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

  // Load history when segment changes
  const selectedSegId = selectedSegment?.id ?? -1;

  // Clear chat when segment changes
  useEffect(() => {
    setChatMessages([]);
    setChatInput("");
  }, [selectedSegId]);

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
      // Poll every 5s until image appears
      const pollInterval = setInterval(async () => {
        if (selectedChapter !== null) {
          try {
            const data = await getChapterSegments(bookId, selectedChapter);
            const updated = data.segments?.find((s: Segment) => s.id === selectedSegment.id);
            if (updated?.illustration_url) {
              setSegments(data.segments || []);
              setSelectedSegment(updated);
              setRegenerating(false);
              clearInterval(pollInterval);
              // Auto quality check
              setCheckingQuality(true);
              checkSegmentQuality(bookId, selectedSegment.id)
                .then((result) => setQualityResult(result))
                .catch((e) => console.error("Auto quality check failed:", e))
                .finally(() => setCheckingQuality(false));
            }
          } catch {}
        }
      }, 5000);
      // Timeout after 2 minutes
      setTimeout(() => {
        clearInterval(pollInterval);
        setRegenerating(false);
      }, 120000);
    } catch (e) {
      console.error("Regenerate failed:", e);
      setRegenerating(false);
    }
  };

  // Update selected segment field
  const updateField = (field: string, value: unknown) => {
    if (!selectedSegment) return;
    setSelectedSegment({ ...selectedSegment, [field]: value });
    // Also update in segments list
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
                    {done ? "✓" : idx + 1}
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
          <a
            href={`/book/${bookId}`}
            className="px-3 py-1.5 text-sm rounded-lg bg-coral text-white hover:bg-coral/80 transition-colors flex items-center gap-1 font-semibold"
          >
            <BookOpen size={14} /> View Book
          </a>
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
              {/* Col 1: Illustration + Quality + Original Text + History */}
              <div className="w-[40%] shrink-0 overflow-y-auto p-3 border-r border-peach/20">
                <div className="card !p-3 mb-3">
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="font-display font-bold text-gray-700 text-sm flex items-center gap-1">
                      <Image size={14} /> Illustration
                    </h3>
                    <button
                      onClick={handleRegenerate}
                      disabled={regenerating}
                      className="btn-primary text-[10px] !px-2 !py-1 flex items-center gap-1"
                    >
                      <RefreshCw size={10} className={regenerating ? "animate-spin" : ""} />
                      {regenerating ? "..." : "Regenerate"}
                    </button>
                  </div>
                  {selectedSegment.illustration_url ? (
                    <img
                      key={`${selectedSegment.id}-${regenerating}`}
                      src={`${API_BASE}${selectedSegment.illustration_url}?t=${Date.now()}`}
                      alt="Page illustration"
                      className="w-full rounded-xl shadow-md"
                    />
                  ) : (
                    <div className="w-full aspect-square bg-peach/20 rounded-xl flex items-center justify-center text-gray-400 text-xs">
                      Click "Regenerate" to create
                    </div>
                  )}
                </div>

                {/* Original Text (under illustration, full content) */}
                <div className="card !p-3 mb-3">
                  <h3 className="font-display font-bold text-gray-700 mb-2 text-xs flex items-center gap-1">
                    <BookOpen size={12} /> Original Text
                  </h3>
                  <div className="text-xs text-gray-600 bg-cream/50 rounded-lg p-3 !leading-[1.26]">
                    {selectedSegment.text.split(/\n\n+/).map((para, i) => (
                      <p key={i} className={i > 0 ? "mt-2" : ""}>{para.replace(/\n/g, " ").trim()}</p>
                    ))}
                  </div>
                </div>

              </div>

              {/* Col 2: Prompt Editing */}
              <div className="w-[36%] shrink-0 overflow-y-auto p-3 border-r border-peach/20 space-y-3">
                {/* History Carousel */}
                {historyImages.filter(img => img.version !== "current").length > 0 && (
                  <div className="card !p-3">
                    <h3 className="font-display font-bold text-gray-700 text-xs mb-2 !leading-[1.26]">
                      Previous versions ({historyImages.filter(img => img.version !== "current").length})
                    </h3>
                    <div className="flex gap-2 overflow-x-auto pb-2">
                      {historyImages
                        .filter(img => img.version !== "current")
                        .map((img, idx) => (
                          <div key={idx} className="shrink-0 w-20">
                            <img
                              src={`${API_BASE}${img.url}?t=${img.timestamp}`}
                              alt={`Version ${idx + 1}`}
                              onClick={() => {
                                if (selectedSegment) {
                                  setSelectedSegment({
                                    ...selectedSegment,
                                    illustration_url: img.url,
                                  });
                                  setQualityResult(img.quality || null);
                                }
                              }}
                              className="w-20 h-20 object-contain rounded-lg cursor-pointer border-2 border-transparent hover:border-coral transition-colors bg-gray-50"
                            />
                          </div>
                      ))}
                    </div>
                  </div>
                )}

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
                <div className="card !p-3">
                  <button
                    onClick={() => setChatOpen(!chatOpen)}
                    className="w-full flex items-center justify-between"
                  >
                    <h3 className="font-display font-bold text-gray-700 text-xs flex items-center gap-1">
                      <MessageCircle size={12} /> AI Assistant
                    </h3>
                    <ChevronRight size={14} className={`text-gray-400 transition-transform ${chatOpen ? "rotate-90" : ""}`} />
                  </button>

                  {chatOpen && (
                    <div className="mt-2">
                      {/* Chat Messages */}
                      <div className="bg-cream/50 rounded-lg p-2 mb-2 max-h-48 overflow-y-auto space-y-2">
                        {chatMessages.length === 0 && (
                          <p className="text-[10px] text-gray-400 text-center py-2">
                            Describe the illustration you want, or ask to adjust fields.
                          </p>
                        )}
                        {chatMessages.map((msg, idx) => (
                          <div key={idx} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                            <div className={`max-w-[85%] rounded-lg px-2.5 py-1.5 text-[11px] !leading-[1.26] ${
                              msg.role === "user"
                                ? "bg-coral/20 text-gray-800"
                                : "bg-white text-gray-700 border border-peach/30"
                            }`}>
                              {msg.content}
                            </div>
                          </div>
                        ))}
                        {chatLoading && (
                          <div className="flex justify-start">
                            <div className="bg-white border border-peach/30 rounded-lg px-2.5 py-1.5 text-[11px] text-gray-400">
                              Thinking...
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Input */}
                      <div className="flex gap-1.5">
                        <input
                          value={chatInput}
                          onChange={(e) => setChatInput(e.target.value)}
                          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleChatSend(); } }}
                          className="flex-1 rounded-lg border border-peach/50 px-2.5 py-1.5 text-xs focus:ring-2 focus:ring-coral/30 focus:border-coral outline-none"
                          placeholder="e.g. Make the scene a rainy night..."
                          disabled={chatLoading}
                        />
                        <button
                          onClick={handleChatSend}
                          disabled={chatLoading || !chatInput.trim()}
                          className="bg-coral text-white px-2.5 py-1.5 rounded-lg hover:bg-coral/80 transition-colors disabled:opacity-50"
                        >
                          <Send size={12} />
                        </button>
                      </div>
                    </div>
                  )}
                </div>

                {/* Save Buttons */}
                <div className="flex gap-2">
                  <button
                    onClick={handleSave}
                    disabled={saving}
                    className="btn-primary text-xs !px-3 !py-1.5 flex items-center gap-1"
                  >
                    <Save size={12} />
                    {saving ? "..." : "Save"}
                  </button>
                  <button
                    onClick={handleRegenerate}
                    disabled={regenerating}
                    className="btn-secondary text-xs !px-3 !py-1.5 flex items-center gap-1"
                  >
                    <RefreshCw size={12} className={regenerating ? "animate-spin" : ""} />
                    {regenerating ? "..." : "Save & Regen"}
                  </button>
                </div>
              </div>

              {/* Col 3: Quality Check + Character Sheets side by side */}
              <div className="flex-1 flex overflow-hidden">
                {/* Quality Check */}
                <div className="w-1/2 overflow-y-auto p-3">
                <div className="card !p-3">
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="font-display font-bold text-gray-700 text-xs flex items-center gap-1">
                      <Shield size={12} /> Quality Check
                    </h3>
                    <button
                      onClick={async () => {
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
                      }}
                      disabled={checkingQuality || !selectedSegment?.illustration_url}
                      className="text-[10px] font-semibold bg-sky/50 hover:bg-sky text-gray-700 px-2 py-0.5 rounded transition-colors disabled:opacity-50 flex items-center gap-1"
                    >
                      <Shield size={10} className={checkingQuality ? "animate-spin" : ""} />
                      {checkingQuality ? "..." : "Run"}
                    </button>
                  </div>

                  {qualityResult ? (
                    <>
                      <div className="flex items-center gap-2 mb-2">
                        <span className={`text-xl font-bold ${
                          qualityResult.overall_score >= 80 ? "text-green-600" :
                          qualityResult.overall_score >= 60 ? "text-yellow-600" : "text-red-600"
                        }`}>{qualityResult.overall_score}%</span>
                      </div>
                      <div className="space-y-1.5 mb-2">
                        {[
                          { key: "character_consistency", label: "Character Match", data: qualityResult.character_consistency },
                          { key: "spelling", label: "Spelling", data: qualityResult.spelling },
                          { key: "duplicate_characters", label: "No Duplicates", data: qualityResult.duplicate_characters },
                          { key: "name_face_mismatch", label: "Name-Face Match", data: qualityResult.name_face_mismatch },
                          { key: "character_count", label: "Char Count", data: qualityResult.character_count },
                        ].map(({ key, label, data }) => {
                          const score = data?.score ?? 100;
                          return (
                            <div key={key} className="flex items-center gap-1 text-xs !leading-[1.26]">
                              <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                                score >= 80 ? "bg-green-500" : score >= 60 ? "bg-yellow-500" : "bg-red-500"
                              }`} />
                              <span className="text-gray-600 flex-1">{label}</span>
                              <span className={`font-bold ${
                                score >= 80 ? "text-green-600" : score >= 60 ? "text-yellow-600" : "text-red-600"
                              }`}>{score}%</span>
                            </div>
                          );
                        })}
                      </div>
                      {(qualityResult.character_consistency?.characters?.length ?? 0) > 0 && (
                        <div className="mb-2">
                          <p className="text-xs font-semibold text-gray-500 mb-1 !leading-[1.26]">Per character:</p>
                          <div className="space-y-1">
                            {qualityResult.character_consistency.characters.map((c) => (
                              <div key={c.name} className="flex items-center gap-1 text-xs !leading-[1.26]">
                                <span className={`w-3.5 h-3.5 rounded-full flex items-center justify-center text-[8px] text-white shrink-0 ${
                                  c.score >= 80 ? "bg-green-500" : c.score >= 60 ? "bg-yellow-500" : "bg-red-500"
                                }`}>{c.score >= 80 ? "✓" : "!"}</span>
                                <span className="text-gray-700 truncate flex-1">{c.name}</span>
                                <span className={`font-bold ${
                                  c.score >= 80 ? "text-green-600" : c.score >= 60 ? "text-yellow-600" : "text-red-600"
                                }`}>{c.score}%</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      {(() => {
                        const groups: Array<{ label: string; color: string; items: string[] }> = [];
                        const spellErrs = qualityResult.spelling?.errors || [];
                        if (spellErrs.length > 0) groups.push({ label: "Spelling", color: "text-red-700", items: spellErrs });
                        const dups = qualityResult.duplicate_characters?.duplicates || [];
                        if (dups.length > 0) groups.push({ label: "Duplicates", color: "text-orange-700", items: dups });
                        const mm = qualityResult.name_face_mismatch?.mismatches || [];
                        if (mm.length > 0) groups.push({ label: "Name Mismatch", color: "text-amber-700", items: mm });
                        const miss = qualityResult.character_count?.missing || [];
                        if (miss.length > 0) groups.push({ label: "Missing", color: "text-purple-700", items: miss });
                        const charIss = (qualityResult.character_consistency?.characters || []).flatMap(c =>
                          (c.issues || []).map(iss => `${c.name}: ${iss}`)
                        );
                        if (charIss.length > 0) groups.push({ label: "Appearance", color: "text-blue-700", items: charIss });
                        if (groups.length === 0) return null;
                        return (
                          <div className="space-y-2">
                            {groups.map((g, gi) => (
                              <div key={gi}>
                                <p className={`text-xs font-bold ${g.color} mb-0.5 !leading-[1.26]`}>{g.label}</p>
                                <ul className="text-xs text-gray-700 space-y-0.5 pl-3">
                                  {g.items.slice(0, 5).map((item, ii) => (
                                    <li key={ii} className="list-disc !leading-[1.26]">{item}</li>
                                  ))}
                                </ul>
                              </div>
                            ))}
                          </div>
                        );
                      })()}
                    </>
                  ) : (
                    <p className="text-xs text-gray-400 !leading-[1.26]">
                      {checkingQuality ? "Checking quality..." : selectedSegment?.illustration_url ? "Auto-checking after generation..." : "Generate an illustration first."}
                    </p>
                  )}
                </div>
                </div>

                {/* Character Sheets */}
                <div className="w-1/2 overflow-y-auto p-3">
                  <div className="card !p-3">
                  <h3 className="font-display font-bold text-gray-700 text-xs mb-3">Character Sheets</h3>
                  <div className="space-y-4">
              {characters
                .filter((c) => {
                  if (!sheets[c.canonical_name]) return false;
                  const sceneChars = selectedSegment?.characters_in_scene || [];
                  if (sceneChars.length === 0) return false;
                  const cName = c.canonical_name.toLowerCase();
                  const cParts = cName.split(/\s+/).filter(p => p.length > 2);
                  return sceneChars.some((sc) => {
                    const scLower = sc.toLowerCase();
                    if (cName === scLower) return true;
                    const scParts = scLower.split(/\s+/).filter(p => p.length > 2);
                    return cParts.some(p => scLower.includes(p)) || scParts.some(p => cName.includes(p));
                  });
                })
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
                </div>
              </div>
            </>
          ) : (
            <div className="flex items-center justify-center flex-1 text-gray-400">
              Select a segment from the left panel
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
