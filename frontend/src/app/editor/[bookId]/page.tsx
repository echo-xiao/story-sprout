"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useParams } from "next/navigation";

import { RefreshCw, Save, Users, MapPin, Smile, BookOpen, Image, Activity } from "lucide-react";
import {
  getChapters,
  getCharacters,
  getChapterSegments,
  updateSegment,
  regenerateSegment,
  restoreSegmentVersion,
  regenerateCharacterSheet,
  generateChapter,
  getChapterProgress,
  generateSimplifiedText,
  generateSceneBackground,
  generateSummary,
  getSegmentHistory,
  checkSegmentQuality,
  getRegenActive,
  getRegenStatus,
  getLocations,
  getSpecialPages,
  regenerateSpecialPage,
  getStalePages,
  getConfig,
  getPreprocessProgress,
  type SpecialPageData,
} from "@/lib/api";
import type { Segment, ChapterInfo, CharacterInfo } from "@/types";
import { setActionField, addAction, removeAction } from "@/lib/segment";
import { isSegmentPageStep } from "@/lib/progress";

import IllustrationPanel from "@/components/editor/IllustrationPanel";
import QualityCheckPanel from "@/components/editor/QualityCheckPanel";
import CharacterSheetsPanel from "@/components/editor/CharacterSheetsPanel";
import AIChatPanel from "@/components/editor/AIChatPanel";
import VersionsCarousel from "@/components/editor/VersionsCarousel";
import CharacterManagement from "@/components/editor/CharacterManagement";
import SceneManagement from "@/components/editor/SceneManagement";
import AutoTextarea from "@/components/editor/AutoTextarea";
import AgentActivityPanel from "@/components/editor/AgentActivityPanel";
import SpecialPageView from "@/components/editor/SpecialPageView";
import PreprocessLoadingScreen from "@/components/editor/PreprocessLoadingScreen";
import { AGENT_META } from "@/lib/agents";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const SENTIMENTS = ["positive", "negative", "neutral", "tense", "emotional"];

export default function EditorPage() {
  const params = useParams();
  const bookId = params.bookId as string;
  const initApplied = useRef(false);

  // Read initial state from URL (sync, no useSearchParams)
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
  const [initialTab] = useState<"pages" | "characters" | "scenes">(() => {
    if (typeof window === "undefined") return "pages";
    const sp = new URLSearchParams(window.location.search);
    const t = sp.get("tab");
    return t === "characters" || t === "scenes" ? t : "pages";
  });
  const [initialChar] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return new URLSearchParams(window.location.search).get("char");
  });
  const [initialScene] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return new URLSearchParams(window.location.search).get("scene");
  });

  const [activeTab, setActiveTab] = useState<"pages" | "characters" | "scenes">(initialTab);
  const [navigateToChar, setNavigateToChar] = useState<string | null>(null);
  // Scene to land on when jumping to the Scenes tab from the Pages tab (the
  // panel remounts on tab switch and reads it via initialScene at mount).
  const [sceneNav, setSceneNav] = useState<string | null>(null);
  const [chapters, setChapters] = useState<Record<string, ChapterInfo>>({});
  const [meta, setMeta] = useState<{ title?: string }>({});
  const [characters, setCharacters] = useState<CharacterInfo[]>([]);
  const [sheets, setSheets] = useState<Record<string, string>>({});
  const [portraits, setPortraits] = useState<Record<string, string>>({});
  const [aliasMap, setAliasMap] = useState<Record<string, string>>({});
  const [locations, setLocations] = useState<any[]>([]);
  const [sceneSheets, setSceneSheets] = useState<Record<string, string>>({});
  const [selectedChapter, setSelectedChapter] = useState<number | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const segmentsRef = useRef<Segment[]>([]);
  const [selectedSegment, setSelectedSegment] = useState<Segment | null>(null);
  // Segment ids with local edits not yet PUT to the backend — handleSave saves
  // them all, so edits made on a previously-selected segment aren't lost.
  const dirtySegIds = useRef<Set<number>>(new Set());
  const [staleSegIds, setStaleSegIds] = useState<Set<number>>(new Set());
  const [staleReasons, setStaleReasons] = useState<Record<number, string>>({});
  const [specialCacheBust, setSpecialCacheBust] = useState(0);
  // Bumped only after a character/scene sheet is regenerated, so CharacterSheetsPanel
  // reloads images then — NOT on every keystroke (which Date.now() in render caused).
  const [sheetCacheBust, setSheetCacheBust] = useState(0);
  const [specialPages, setSpecialPages] = useState<SpecialPageData[]>([]);
  const [selectedSpecial, setSelectedSpecial] = useState<SpecialPageData | null>(null);
  const [regenSpecial, setRegenSpecial] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [showCharPanel, setShowCharPanel] = useState(true);
  const [historyImages, setHistoryImages] = useState<Array<{ url: string; version: string; timestamp: number; quality?: any }>>([]);
  const [generatingChapter, setGeneratingChapter] = useState<number | null>(null);
  const [chapterProgress, setChapterProgress] = useState<{ progress: number; current_step: string } | null>(null);
  const [genAllChapters, setGenAllChapters] = useState(false);
  const genAllChaptersRef = useRef(false);
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

  // Agent Activity Panel (open by default so the live agent log is always visible)
  const [agentPanelOpen, setAgentPanelOpen] = useState(true);

  // BYOK: the editor is read-only unless the visitor supplied their own Gemini
  // key (generation endpoints also enforce this server-side with a 403).
  const [hasKey] = useState(() => typeof window !== "undefined" && !!localStorage.getItem("pbg_api_key"));
  const [keyInput, setKeyInput] = useState("");
  // The BYOK gate is only enforced when the backend says so (REQUIRE_USER_KEY).
  // Default off → editor is fully usable without a key (project/Vertex billing).
  const [requireKey, setRequireKey] = useState(false);
  useEffect(() => {
    getConfig().then(c => setRequireKey(!!c.require_user_key)).catch(() => {});
  }, []);
  const canEdit = hasKey || !requireKey;

  // Expand/collapse state for the read-only LLM Prompt Preview (AIChatPanel).
  const [chatOpen, setChatOpen] = useState(false);

  const selectedSegId = selectedSegment?.id ?? -1;

  // Latest selection for async handlers — a quality result landing after the
  // user switched segments must not display under the new segment.
  const selectedSegIdRef = useRef(selectedSegId);
  useEffect(() => {
    selectedSegIdRef.current = selectedSegId;
  }, [selectedSegId]);

  // Keep a ref of the latest segments to avoid stale closures in async handlers
  useEffect(() => {
    segmentsRef.current = segments;
  }, [segments]);

  // Keep a ref of the selected chapter too — the long Gen-All loop captures it
  // once and would otherwise refresh/compare against the chapter that was
  // selected when the button was clicked, not the one the user is now viewing.
  const selectedChapterRef = useRef<number | null>(null);
  useEffect(() => {
    selectedChapterRef.current = selectedChapter;
  }, [selectedChapter]);

  // Set on unmount so handler-started polls (Gen All / regen / sheet / special)
  // stop instead of polling — and in Gen All's case launching new chapter
  // generations — for minutes after the user navigates away.
  const unmountedRef = useRef(false);
  useEffect(() => {
    // Reset on mount: React StrictMode (dev) runs setup→cleanup→setup and the
    // ref survives that remount — without this reset the cleanup below left
    // unmountedRef permanently true, so every poll guard bailed on its first
    // tick and Gen All launched every chapter's generation back-to-back.
    unmountedRef.current = false;
    return () => {
      unmountedRef.current = true;
      // Stop the Gen All LOOP too, not just its polls — otherwise after the
      // user navigates away each poll resolves early and the loop fires
      // generateChapter for every remaining chapter in quick succession.
      genAllChaptersRef.current = false;
      // A pending scene-background debounce would otherwise still fire its
      // updateSegment + paid LLM call after the user has left the page.
      if (sceneRegenTimer.current) clearTimeout(sceneRegenTimer.current);
    };
  }, []);

  // Warn before leaving the page (View Book is a full navigation) while
  // segment edits are unsaved — the in-app chapter switch already confirms,
  // but a navigation away silently dropped them.
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (dirtySegIds.current.size > 0) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, []);

  // Merge server segments into local state while PRESERVING dirty (unsaved)
  // local copies — async completion handlers (chapter gen, regen, restore,
  // rename) otherwise clobber edits the user typed while they ran.
  const applyServerSegments = (server: Segment[]) => {
    setSegments(prev => server.map(s => dirtySegIds.current.has(s.id) ? (prev.find(p => p.id === s.id) ?? s) : s));
    setSelectedSegment(prev => {
      if (!prev) return prev;
      if (dirtySegIds.current.has(prev.id)) return prev; // keep local dirty copy
      return server.find(s => s.id === prev.id) || prev;
    });
  };

  // Refresh the set of stale pages (deps regenerated after the page image) for a chapter
  const refreshStale = async (chIdx: number | null) => {
    if (chIdx === null) return;
    try {
      const data = await getStalePages(bookId, chIdx);
      // The user may have switched chapters during the await — don't overwrite
      // the now-selected chapter's stale flags with another chapter's.
      if (selectedChapterRef.current !== chIdx) return;
      const ids = new Set<number>();
      const reasons: Record<number, string> = {};
      (data.stale || []).forEach((s) => {
        ids.add(s.segment_id);
        reasons[s.segment_id] = (s.reasons || []).map((r) => `${r.name} updated`).join(", ");
      });
      setStaleSegIds(ids);
      setStaleReasons(reasons);
    } catch {
      if (selectedChapterRef.current !== chIdx) return;
      setStaleSegIds(new Set());
      setStaleReasons({});
    }
  };

  // Poll progress when generating
  useEffect(() => {
    if (generatingChapter === null) return;
    // In Gen-All mode the loop runs its OWN poll and transitions; a second poll
    // here just doubles the requests and makes the progress bar flicker between
    // the two responses. Let the loop own it.
    if (genAllChapters) return;
    const interval = setInterval(async () => {
      try {
        const prog = await getChapterProgress(bookId, generatingChapter).catch(() => null);
        if (!prog) return;
        setChapterProgress(prog);
        if (prog.status === "failed") {
          setGeneratingChapter(null);
          setChapterProgress(null);
          alert(`Chapter generation failed: ${(prog as any).error || prog.current_step || "unknown error"}`);
          return;
        }
        if (prog.status === "complete" && !genAllChaptersRef.current) {
          // Only auto-clear when NOT in Gen All mode (Gen All handles its own transitions)
          setGeneratingChapter(null);
          setChapterProgress(null);
          if (selectedChapter === generatingChapter) {
            const data = await getChapterSegments(bookId, generatingChapter);
            // Re-check after the await — the user may have switched chapters
            // meanwhile, and these would overwrite the new chapter's segments.
            if (selectedChapterRef.current === generatingChapter) {
              applyServerSegments(data.segments || []);
            }
          }
          // Chapter generation also creates covers server-side — refresh the
          // special pages list (fetched only once on mount otherwise).
          getSpecialPages(bookId).then(d => setSpecialPages(d.pages || [])).catch(() => {});
          refreshStale(generatingChapter);  // pages regenerated → clear stale red dots
        }
      } catch {}
    }, 5000);
    return () => clearInterval(interval);
  }, [generatingChapter, bookId, selectedChapter, genAllChapters]);

  // Auto-open the agent activity panel the moment a chapter generation starts,
  // so the user can watch the agent interactions live.
  useEffect(() => {
    if (generatingChapter !== null) setAgentPanelOpen(true);
  }, [generatingChapter]);

  // Gen All Chapters: generate all chapters sequentially
  const handleGenAllChapters = async () => {
    const chapterIndices = Object.keys(chapters).map(Number).sort((a, b) => a - b);
    setGenAllChapters(true);
    genAllChaptersRef.current = true;
    setAgentPanelOpen(true);

    for (const chIdx of chapterIndices) {
      if (!genAllChaptersRef.current) break; // allow cancel

      // Skip chapters that are already fully generated
      try {
        const prog = await getChapterProgress(bookId, chIdx).catch(() => null);
        if (prog && prog.status === "complete") {
          continue;
        }
      } catch {}

      setGeneratingChapter(chIdx);
      try {
        await generateChapter(bookId, chIdx);
        // Wait for completion by polling progress
        await new Promise<void>((resolve) => {
          const poll = setInterval(async () => {
            if (unmountedRef.current) { clearInterval(poll); resolve(); return; }
            try {
              const prog = await getChapterProgress(bookId, chIdx).catch(() => null);
              if (!prog) return;
              setChapterProgress(prog);
              if (prog.status === "complete" || prog.status === "failed") {
                // On success refresh segments (green dots); on failure just move
                // on instead of waiting out the 16-min per-chapter timeout.
                if (prog.status === "complete" && selectedChapterRef.current === chIdx) {
                  getChapterSegments(bookId, chIdx)
                    .then(data => {
                      // Re-check after the fetch — the user may have switched
                      // chapters while it was in flight.
                      if (selectedChapterRef.current !== chIdx) return;
                      applyServerSegments(data.segments || []);
                    })
                    .catch(() => {});
                }
                clearInterval(poll);
                resolve();
              }
            } catch {}
          }, 5000);
          // Must exceed the backend subprocess timeout (900s) — if the frontend
          // gave up first it would start the next chapter while the previous
          // subprocess was still running, double-hitting Gemini and racing on
          // progress.json / agent_log.json.
          setTimeout(() => { clearInterval(poll); resolve(); }, 960000); // 16 min > backend 15 min
        });
      } catch (e) {
        console.error(`Gen chapter ${chIdx} failed:`, e);
      }
    }

    setGeneratingChapter(null);
    setChapterProgress(null);
    setGenAllChapters(false);
    genAllChaptersRef.current = false;

    // Refresh character sheets (may have been generated)
    try {
      const charData = await getCharacters(bookId);
      setSheets(charData.sheets || {});
    } catch {}

    // Chapter generation also creates covers server-side — refresh the
    // special pages list (fetched only once on mount otherwise).
    getSpecialPages(bookId).then(d => setSpecialPages(d.pages || [])).catch(() => {});

    // Pages were regenerated — refresh stale indicators
    refreshStale(selectedChapterRef.current);
  };

  // Load chapters + characters on mount (retry until preprocess is done)
  useEffect(() => {
    let retryTimer: NodeJS.Timeout;
    let cancelled = false;

    async function load() {
      try {
        const chapData = await getChapters(bookId);
        if (cancelled) return;
        const chapKeys = Object.keys(chapData.chapters || {});

        if (chapKeys.length > 0) {
          // Preprocess is done — load everything
          const charData = await getCharacters(bookId);
          if (cancelled) return;
          setChapters(chapData.chapters || {});
          setMeta(chapData.meta || {});
          setCharacters(charData.characters || []);
          setSheets(charData.sheets || {});
          setPortraits(charData.portraits || {});
          // Load locations + special pages (best-effort)
          getLocations(bookId).then(d => {
            if (cancelled) return;
            setLocations(d.locations || []);
            setSceneSheets(d.scene_sheets || {});
          }).catch(() => {});
          getSpecialPages(bookId).then(d => {
            if (cancelled) return;
            setSpecialPages(d.pages || []);
          }).catch(() => {});
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
      if (cancelled) return;
      // If preprocess failed fatally, stop retrying and surface the error
      try {
        const prog = await getPreprocessProgress(bookId);
        if (cancelled) return;
        if (prog?.status === "error") {
          setPreprocessError(prog.error || prog.step || "Preprocess failed");
          return;
        }
      } catch {}
      if (cancelled) return;
      // Still processing — retry in 5 seconds
      retryTimer = setTimeout(load, 5000);
    }

    load();
    return () => { cancelled = true; clearTimeout(retryTimer); };
  }, [bookId]);

  // Load segments + cached consistency when chapter changes
  useEffect(() => {
    setQualityResult(null);
    // Clear immediately so the previous chapter's segments don't render under
    // the newly-selected chapter for the moment before the fetch returns.
    setSegments([]);
    setSelectedSegment(null);
    if (selectedChapter === null) return;
    let cancelled = false;
    async function loadSegments() {
      try {
        const data = await getChapterSegments(bookId, selectedChapter!);
        if (cancelled) return;
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
        } else {
          setSelectedSegment(null);
        }
      } catch {
        // 404 means analysis.json not ready yet — clear segments silently
        if (!cancelled) {
          setSegments([]);
          setSelectedSegment(null);
        }
      }
    }
    loadSegments();
    refreshStale(selectedChapter);
    return () => { cancelled = true; };
  }, [bookId, selectedChapter]);

  // Update URL when tab/selection changes
  const [selectedCharName, setSelectedCharName] = useState<string | null>(initialChar);
  const [selectedSceneName, setSelectedSceneName] = useState<string | null>(initialScene);

  useEffect(() => {
    // Don't rewrite the URL while the initial-load screen is still up — it
    // would replaceState away the ?ch=&seg=&char= deep-link params before the
    // data they're restored from has loaded.
    if (loading) return;
    const params = new URLSearchParams();
    params.set("tab", activeTab);
    if (activeTab === "pages") {
      if (selectedChapter !== null) params.set("ch", String(selectedChapter));
      if (selectedSegment?.id != null) params.set("seg", String(selectedSegment.id));
    } else if (activeTab === "characters" && selectedCharName) {
      params.set("char", selectedCharName);
    } else if (activeTab === "scenes" && selectedSceneName) {
      params.set("scene", selectedSceneName);
    }
    window.history.replaceState(null, "", `/editor/${bookId}?${params.toString()}`);
  }, [bookId, loading, activeTab, selectedChapter, selectedSegment?.id, selectedCharName, selectedSceneName]);

  // Auto-generate simplified text if empty — only when editing is enabled, so a
  // view-only / no-key visitor doesn't silently trigger paid LLM calls.
  // In-flight segment ids: switching A→B→A must not fire a second (paid) call
  // for A while the first is still running.
  const simplifyInFlight = useRef<Set<number>>(new Set());
  useEffect(() => {
    if (selectedSegId < 0 || !selectedSegment || selectedSegment.simplified_text || !canEdit) return;
    const segId = selectedSegId;
    if (simplifyInFlight.current.has(segId)) return;
    simplifyInFlight.current.add(segId);
    generateSimplifiedText(bookId, segId)
      .then((res) => {
        if (res.simplified_text) {
          // Only apply if STILL empty — don't clobber text the user typed while
          // the request was in flight.
          setSelectedSegment(prev => prev?.id === segId && !prev.simplified_text ? { ...prev, simplified_text: res.simplified_text } : prev);
          setSegments(prev => prev.map(s => s.id === segId && !s.simplified_text ? { ...s, simplified_text: res.simplified_text } : s));
        }
      })
      .catch(() => {})
      .finally(() => { simplifyInFlight.current.delete(segId); });
  }, [selectedSegId, canEdit]);

  // Load history when segment changes
  useEffect(() => {
    if (selectedSegId < 0) return;
    // Clear synchronously so the new segment doesn't render under the OLD
    // segment's quality score / carousel for one round-trip.
    setQualityResult(null);
    setHistoryImages([]);
    const segId = selectedSegId;
    getSegmentHistory(bookId, segId)
      .then((data) => {
        // Out-of-order guard: ignore a response that lands after the user
        // switched segments (mirrors handleRunQualityCheck).
        if (selectedSegIdRef.current !== segId) return;
        const images = data.images || [];
        setHistoryImages(images);
        // Auto-load quality for current version
        const current = images.find((img: any) => img.version === "current");
        setQualityResult(current?.quality || null);
      })
      .catch(() => {
        if (selectedSegIdRef.current !== segId) return;
        setHistoryImages([]);
        setQualityResult(null);
      });
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
      // Also persist any other segments edited but never saved (the user can
      // edit a segment, switch to another, then hit Save).
      for (const segId of Array.from(dirtySegIds.current)) {
        if (segId === selectedSegment.id) continue;
        const seg = segmentsRef.current.find((s) => s.id === segId);
        if (!seg) continue;
        await updateSegment(bookId, segId, {
          text: seg.text,
          simplified_text: seg.simplified_text,
          characters_in_scene: seg.characters_in_scene,
          character_actions: seg.character_actions,
          scene_background: seg.scene_background,
          scene_summary: seg.scene_summary,
          sentiment: seg.sentiment,
        });
      }
      dirtySegIds.current = new Set();
    } catch (e: any) {
      console.error("Save failed:", e);
      alert(`Save failed: ${e?.response?.data?.detail || e?.message || e}`);
    } finally {
      setSaving(false);
    }
  }, [bookId, selectedSegment]);

  // Regenerate illustration
  const handleRegenerate = async () => {
    if (!selectedSegment || selectedChapter === null) return;
    // Capture IDs at call time to avoid stale closures
    const segId = selectedSegment.id;
    const chIdx = selectedChapter;
    setRegenerating(true);
    try {
      // Save first
      await updateSegment(bookId, segId, {
        simplified_text: selectedSegment.simplified_text,
        characters_in_scene: selectedSegment.characters_in_scene,
        character_actions: selectedSegment.character_actions,
        scene_background: selectedSegment.scene_background,
        scene_summary: selectedSegment.scene_summary,
        sentiment: selectedSegment.sentiment,
      });
      // The PUT above just persisted this segment — it's no longer dirty
      // (otherwise a false "unsaved changes" confirm appears later).
      dirtySegIds.current.delete(segId);
      // Trigger regeneration. The BACKEND runs the QA check + bounded
      // self-correction (shared page service, same policy as the pipeline);
      // the frontend only triggers and waits — no client-side retry loop.
      await regenerateSegment(bookId, segId);
      await new Promise<void>((resolve) => {
        let done = false;
        const poll = setInterval(async () => {
          if (done) return;
          if (unmountedRef.current) { done = true; clearInterval(poll); resolve(); return; }
          try {
            const status = await getRegenStatus(bookId, segId);
            if (status.status === "complete" || status.status === "error") {
              done = true;
              clearInterval(poll);
              if (status.status === "error") {
                alert(`Regenerate failed: ${(status as any).error || "unknown error"}`);
              } else if (selectedChapterRef.current === chIdx) {
                // Only apply if the user is still viewing the chapter that was
                // selected at click time — otherwise we'd overwrite the
                // currently-selected chapter's segments with another chapter's.
                const data = await getChapterSegments(bookId, chIdx);
                // Re-check after the await: the user may have switched
                // chapters while the fetch was in flight.
                if (selectedChapterRef.current === chIdx) {
                  applyServerSegments(data.segments || []);
                }
              }
              resolve();
            }
          } catch {}
        }, 5000);
        // The backend moves the old image to history before regenerating, so
        // bailing out silently leaves a broken <img>. Match the backend's
        // 600s request ceiling and tell the user if it's still running.
        setTimeout(() => {
          if (!done) {
            done = true;
            clearInterval(poll);
            if (!unmountedRef.current) {
              alert("Still generating in the background — reload the page in a minute to see the result.");
            }
            resolve();
          }
        }, 600000);
      });
      // Page regenerated — refresh stale flags. The history/quality effect
      // (keyed on `regenerating`) reloads the backend's QA result for display.
      if (selectedChapterRef.current === chIdx) refreshStale(chIdx);
    } catch (e: any) {
      console.error("Regenerate failed:", e);
      // Surface the backend's detail (e.g. "already regenerating" 409) over
      // axios's generic status-code message.
      alert(`Regenerate failed: ${e?.response?.data?.detail || e?.message || e}`);
    } finally {
      setRegenerating(false);
    }
  };

  // Update selected segment field
  const updateField = (field: string, value: unknown) => {
    if (!selectedSegment) return;
    const segId = selectedSegment.id;
    dirtySegIds.current.add(segId);
    // The user is hand-editing the scene background → cancel any pending
    // auto-regen, otherwise the debounce (armed when they changed a character)
    // would fire 2s later and silently overwrite what they just typed.
    if (field === "scene_background" && sceneRegenTimer.current) {
      clearTimeout(sceneRegenTimer.current);
      sceneRegenTimer.current = null;
    }
    // Functional updates: two field edits in the same tick must not clobber
    // each other via a stale `selectedSegment` closure.
    setSelectedSegment((prev) => (prev && prev.id === segId ? { ...prev, [field]: value } : prev));
    setSegments((prev) => prev.map((s) => (s.id === segId ? { ...s, [field]: value } : s)));
  };

  // Debounce timer for auto-regenerating scene_background after character changes
  const sceneRegenTimer = useRef<NodeJS.Timeout | null>(null);
  const [regenningBg, setRegenningBg] = useState(false);

  const triggerSceneBackgroundRegen = useCallback((segId: number) => {
    if (sceneRegenTimer.current) clearTimeout(sceneRegenTimer.current);
    sceneRegenTimer.current = setTimeout(async () => {
      if (unmountedRef.current) return;
      try {
        setRegenningBg(true);
        // Read the LATEST segments via the ref — the closed-over `segments`
        // is from the render that scheduled this timer and is missing the edit
        // the user just typed, which would persist a stale character list.
        const seg = segmentsRef.current.find(s => s.id === segId);
        if (!seg) {
          // Chapter switched before the debounce fired: the edit was never
          // saved, so generating a background from it would use stale data.
          return;
        }
        await updateSegment(bookId, segId, {
          characters_in_scene: seg.characters_in_scene,
          character_actions: seg.character_actions,
        });
        const res = await generateSceneBackground(bookId, segId);
        // Update local state with new background
        const newBg = res.scene_background;
        setSelectedSegment(prev => prev && prev.id === segId ? { ...prev, scene_background: newBg } : prev);
        setSegments(prev => prev.map(s => s.id === segId ? { ...s, scene_background: newBg } : s));
      } catch (e) {
        console.error("Auto scene_background regen failed:", e);
      } finally {
        setRegenningBg(false);
      }
    }, 2000);
  }, [bookId]);

  // Apply a pure transform to the selected segment in BOTH state copies using
  // functional updates, so two edits in one tick can't clobber each other via a
  // stale `selectedSegment` closure (quick add/remove of character rows).
  const mutateSegment = (segId: number, transform: (seg: Segment) => Segment) => {
    dirtySegIds.current.add(segId);
    setSelectedSegment((prev) => (prev && prev.id === segId ? transform(prev) : prev));
    setSegments((prev) => prev.map((s) => (s.id === segId ? transform(s) : s)));
  };

  // Update character action — must update both fields in one setState call
  const updateAction = (idx: number, field: "name" | "action", value: string) => {
    if (!selectedSegment) return;
    const segId = selectedSegment.id;
    mutateSegment(segId, (seg) => setActionField(seg, idx, field, value));
    // Auto-regenerate scene_background after character changes
    if (field === "name" && value.trim()) {
      triggerSceneBackgroundRegen(segId);
    }
  };

  const addCharacterAction = () => {
    if (!selectedSegment) return;
    mutateSegment(selectedSegment.id, (seg) => addAction(seg));
  };

  const removeCharacterAction = (idx: number) => {
    if (!selectedSegment) return;
    const segId = selectedSegment.id;
    mutateSegment(segId, (seg) => removeAction(seg, idx));
    // Auto-regenerate scene_background after removing character
    triggerSceneBackgroundRegen(segId);
  };

  // Handle quality check
  const handleRunQualityCheck = async () => {
    if (!selectedSegment) return;
    const segId = selectedSegment.id;
    setCheckingQuality(true);
    try {
      const result = await checkSegmentQuality(bookId, segId);
      // Only show it if the user is still on this segment.
      if (selectedSegIdRef.current === segId) setQualityResult(result);
    } catch (e) {
      console.error("Quality check failed:", e);
    } finally {
      setCheckingQuality(false);
    }
  };

  // Handle regenerate character sheet (called from CharacterSheetsPanel in Pages tab)
  const handleRegenerateSheet = async (canonicalName: string) => {
    try {
      await regenerateCharacterSheet(bookId, canonicalName);
    } catch (err: any) {
      alert(`Sheet regeneration failed: ${err?.response?.data?.detail || err?.message || "unknown error"}`);
      return;
    }
    // Poll the regen CLAIM, not the file: on failure the backend restores the
    // OLD sheet, so "file exists" can't distinguish success from failure —
    // a failed regen on an existing sheet used to silently report success.
    const poll = setInterval(async () => {
      if (unmountedRef.current) { clearInterval(poll); return; }
      try {
        const st = await getRegenActive(bookId, "character", canonicalName).catch(() => null);
        if (!st || st.active !== false) return;  // still running (or transient fetch error)
        clearInterval(poll);
        if (st.error) {
          alert(`Regeneration failed: ${st.error}`);
          return;
        }
        const data = await getCharacters(bookId);
        setSheets(data.sheets || {});
        setPortraits(data.portraits || {});
        setSheetCacheBust(v => v + 1);  // sheet file reused its name — force reload
        // Character sheet changed — pages depending on it are now stale.
        // Via the ref: this poll runs up to 120s, the user may have
        // switched chapters since it started.
        refreshStale(selectedChapterRef.current);
      } catch {}
    }, 5000);
    // 240s: sheet regen may now self-correct (2x generate + 2x QA worst case)
    setTimeout(() => clearInterval(poll), 240000);
  };

  // Regenerate a special page (book/chapter cover, back cover) + poll until ready
  const handleRegenSpecial = async () => {
    if (!selectedSpecial) return;
    const spType = selectedSpecial.type;
    const spChapter = selectedSpecial.chapter ?? 0;
    setRegenSpecial(true);
    try {
      await regenerateSpecialPage(bookId, spType, spChapter);
      await new Promise<void>((resolve) => {
        // Track the timeout handle so EVERY poll exit cancels it — a leftover
        // timer would clear regenSpecial in the middle of a LATER run.
        let timeout: ReturnType<typeof setTimeout> | undefined;
        const stop = (poll: ReturnType<typeof setInterval>) => {
          clearInterval(poll);
          if (timeout) clearTimeout(timeout);
        };
        const poll = setInterval(async () => {
          if (unmountedRef.current) { stop(poll); resolve(); return; }
          try {
            // Claim lifecycle is the source of truth: on failure the backend
            // restores the OLD image, so "url exists" can't distinguish
            // success from failure for a page that already had an image.
            const st = await getRegenActive(bookId, "special", `${spType}:${spChapter}`).catch(() => null);
            if (!st || st.active !== false) return;  // still running (or transient fetch error)
            stop(poll);
            setRegenSpecial(false);
            if (st.error) {
              alert(`Regeneration failed: ${st.error}`);
            } else {
              const data = await getSpecialPages(bookId).catch(() => null);
              if (data) {
                const found = data.pages.find(p => p.type === spType && (p.chapter ?? 0) === spChapter);
                setSpecialPages(data.pages || []);
                if (found) setSelectedSpecial(found);
                setSpecialCacheBust(Date.now());
              }
            }
            resolve();
          } catch {}
        }, 5000);
        timeout = setTimeout(() => { clearInterval(poll); setRegenSpecial(false); resolve(); }, 120000);
      });
    } catch (e: any) {
      setRegenSpecial(false);
      alert(`Regenerate failed: ${e?.response?.data?.detail || e?.message || "Regenerate failed"}`);
    }
  };

  // Restore a historical version from the carousel. This persists on the
  // backend — the old purely-local swap reverted on reload and never reached
  // the PDF/viewer.
  const handleSelectVersion = async (img: { url: string; version: string; quality?: any }) => {
    if (!selectedSegment || regenerating) return;
    setQualityResult(img.quality || null);
    if (img.version === "current") return;
    const segId = selectedSegment.id;
    const chIdx = selectedChapter;
    setRegenerating(true); // also makes IllustrationPanel bust its image cache
    try {
      await restoreSegmentVersion(bookId, segId, img.version);
      if (chIdx !== null && selectedChapterRef.current === chIdx) {
        const data = await getChapterSegments(bookId, chIdx);
        // Re-check after the await: the user may have switched chapters
        // while the fetch was in flight.
        if (selectedChapterRef.current === chIdx) {
          applyServerSegments(data.segments || []);
        }
      }
    } catch (e: any) {
      alert(`Restore failed: ${e?.response?.data?.detail || e?.message || e}`);
    } finally {
      setRegenerating(false); // history effect reloads the carousel + quality
    }
  };

  const [loadingStatus, setLoadingStatus] = useState("Loading book data...");
  const [preprocessError, setPreprocessError] = useState<string | null>(null);
  const [preprocessProgress, setPreprocessProgress] = useState<{
    progress: number; step: string; steps_done: string[];
    annotated_chapters?: number; total_chapters?: number;
  } | null>(null);

  // Poll preprocess progress while loading
  useEffect(() => {
    if (!loading || preprocessError) return;
    const interval = setInterval(async () => {
      try {
        const prog = await getPreprocessProgress(bookId);
        if (prog.status === "error") {
          setPreprocessError(prog.error || prog.step || "Preprocess failed");
          clearInterval(interval);
          return;
        }
        setPreprocessProgress(prog);
        setLoadingStatus(prog.step || "Processing...");
      } catch {}
    }, 3000);
    return () => clearInterval(interval);
  }, [loading, bookId, preprocessError]);

  if (loading) {
    return <PreprocessLoadingScreen loadingStatus={loadingStatus} preprocessProgress={preprocessProgress} error={preprocessError} />;
  }

  return (
    <div className="h-screen flex flex-col bg-cream" style={{ lineHeight: 1.26 }}>
      {/* Header */}
      <header className="bg-white border-b border-peach/30 px-4 py-2 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <a href="/" className="text-2xl hover:opacity-70 transition-opacity" title="Back to Home">📖</a>
          <div>
            <h1 className="font-display text-lg font-bold text-gray-800">
              {meta.title || bookId}
            </h1>
            <p className="text-xs text-gray-500">
              {Object.keys(chapters).length} chapters, {characters.length} characters
            </p>
          </div>
        </div>
        {/* Main Navigation */}
        <div className="flex items-center gap-2">
          <a href="/" className="px-3 py-1.5 text-xs font-semibold rounded-md text-gray-500 hover:bg-peach/50 transition-colors">
            Create
          </a>
          <span className="px-3 py-1.5 text-xs font-semibold rounded-md bg-coral/10 text-coral">
            Editor
          </span>
          <a href="/?view=library" className="px-3 py-1.5 text-xs font-semibold rounded-md text-gray-500 hover:bg-peach/50 transition-colors">
            Library
          </a>
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
              onClick={() => setActiveTab("scenes")}
              className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors flex items-center gap-1 ${
                activeTab === "scenes" ? "bg-white shadow-sm text-coral" : "text-gray-500 hover:text-gray-700"
              }`}
            >
              <MapPin size={12} /> Scenes
            </button>
            <button
              onClick={() => setActiveTab("pages")}
              className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors flex items-center gap-1 ${
                activeTab === "pages" ? "bg-white shadow-sm text-coral" : "text-gray-500 hover:text-gray-700"
              }`}
            >
              <BookOpen size={12} /> Pages
            </button>
          </div>
          {/* Agent Activity Indicator */}
          <button
            onClick={() => setAgentPanelOpen(!agentPanelOpen)}
            className={`px-3 py-1.5 text-xs rounded-lg transition-colors flex items-center gap-1.5 font-semibold border ${
              generatingChapter !== null
                ? "bg-gradient-to-r from-blue-50 to-purple-50 border-purple-200 text-purple-700"
                : agentPanelOpen
                ? "bg-gray-100 border-gray-300 text-gray-700"
                : "border-gray-200 text-gray-500 hover:bg-gray-50"
            }`}
          >
            {generatingChapter !== null ? (
              <>
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-purple-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-purple-500"></span>
                </span>
                {(() => {
                  const agent = (chapterProgress as any)?.agent;
                  const info = agent ? AGENT_META[agent] : null;
                  return info ? `${info.icon} ${info.name}` : "Agents";
                })()}
              </>
            ) : (
              <>
                <Activity size={12} />
                Agents
              </>
            )}
          </button>
          <a
            href={`/book/${bookId}`}
            className="px-3 py-1.5 text-xs rounded-lg bg-coral text-white hover:bg-coral/80 transition-colors flex items-center gap-1 font-semibold"
          >
            View Book
          </a>
        </div>
      </header>

      {requireKey && !hasKey && (
        <div className="bg-amber-50 border-b border-amber-200 px-4 py-2 text-xs text-amber-800 flex items-center justify-center gap-2 shrink-0 flex-wrap">
          <span>👁 View-only — paste your Gemini API key to edit:</span>
          <input
            type="password"
            name="gemini_key"
            aria-label="Gemini API key"
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            placeholder="AIza..."
            className="px-2 py-1 rounded border border-amber-300 text-gray-700 w-52 bg-white focus:outline-none focus:ring-2 focus:ring-amber-300"
          />
          <button
            onClick={() => {
              const k = keyInput.trim();
              if (k) { localStorage.setItem("pbg_api_key", k); window.location.reload(); }
            }}
            disabled={!keyInput.trim()}
            className="px-2.5 py-1 rounded bg-amber-500 text-white font-semibold hover:bg-amber-600 disabled:opacity-50"
          >
            Save &amp; enable
          </button>
          <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener noreferrer" className="underline">create a key</a>
          <span className="text-amber-700">(must have billing enabled — free-tier keys can&apos;t generate images)</span>
        </div>
      )}

      {/* Body: tab content + persistent live Agent Activity column side by side */}
      <div className="flex flex-1 overflow-hidden">
      {/* Tab content area */}
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
      {/* Character Management Tab */}
      {activeTab === "characters" && (
        <CharacterManagement
          bookId={bookId}
          canGenerate={canEdit}
          characters={characters}
          sheets={sheets}
          aliasMap={aliasMap}
          navigateToChar={navigateToChar || initialChar}
          onCharactersUpdate={(chars, newSheets, renamedFrom?: string, renamedTo?: string) => {
            setCharacters(chars);
            setSheets(newSheets);
            setNavigateToChar(null);
            setSheetCacheBust(v => v + 1);  // a sheet may have been (re)generated — reload images
            // On rename the BACKEND cascades the new name across all segments,
            // the alias map, the gender map and the sheet files. Refetch the
            // affected client state instead of mutating it locally — the old
            // local loop raced segmentsRef and silently dropped every change
            // after the first, and never touched other chapters or the aliases.
            if (renamedFrom && renamedTo && renamedFrom !== renamedTo) {
              getCharacters(bookId).then(d => {
                setAliasMap(d.alias_map || {});
                setPortraits(d.portraits || {});
                setSheets(d.sheets || {});
              }).catch(() => {});
              if (selectedChapter !== null) {
                const chIdx = selectedChapter;
                getChapterSegments(bookId, chIdx).then(d => {
                  // Re-check after the fetch — the user may have switched
                  // chapters while it was in flight.
                  if (selectedChapterRef.current !== chIdx) return;
                  applyServerSegments(d.segments || []);
                }).catch(() => {});
              }
              setSheetCacheBust(v => v + 1);
            }
            // A character may have been regenerated — refresh stale pages
            refreshStale(selectedChapter);
          }}
          onSelectChar={setSelectedCharName}
        />
      )}

      {/* Scenes Tab */}
      {activeTab === "scenes" && (
        <SceneManagement
          bookId={bookId}
          canGenerate={canEdit}
          initialScene={sceneNav || initialScene}
          onSelectScene={(name) => {
            setSelectedSceneName(name);
            // One-shot navigation target consumed — clearing is safe because
            // SceneManagement only reads initialScene in its mount effect
            // (keyed on bookId), which won't re-run when the prop reverts to
            // `initialScene` (no `navigateToChar || initialChar` yank-back).
            setSceneNav(null);
          }}
          onSceneRegen={() => {
            refreshStale(selectedChapter);
            // SceneManagement keeps its own copy of locations/sceneSheets; sync the
            // parent's copy (used by the Pages-tab CharacterSheetsPanel) and reload images.
            getLocations(bookId).then(d => {
              setLocations(d.locations || []);
              setSceneSheets(d.scene_sheets || {});
            }).catch(() => {});
            setSheetCacheBust(v => v + 1);
          }}
        />
      )}

      {/* Pages Tab */}
      {activeTab === "pages" && (
      <div className="flex flex-1 overflow-hidden">
        {/* Left Panel: Chapters + Segments + Special Pages */}
        <div className="w-64 bg-white border-r border-peach/30 overflow-y-auto shrink-0">
          {/* Gen All Chapters */}
          <div className="px-3 py-2 text-[10px] font-bold text-gray-400 uppercase tracking-wider bg-cream/50 flex items-center justify-between">
            <span>Chapters ({Object.keys(chapters).length})</span>
            <div className="flex items-center gap-1">
              {genAllChapters && (
                <span className="text-[9px] text-amber-600 animate-pulse">
                  Ch {(generatingChapter ?? 0) + 1}/{Object.keys(chapters).length}
                </span>
              )}
              <button
                onClick={handleGenAllChapters}
                disabled={genAllChapters || generatingChapter !== null || !canEdit}
                className="text-[9px] bg-coral/80 text-white px-2 py-0.5 rounded hover:bg-coral transition-colors disabled:opacity-50"
              >
                {genAllChapters ? "Running..." : "Gen All"}
              </button>
            </div>
          </div>
          {/* Book Cover */}
          {(() => {
            const bc = specialPages.find(p => p.type === "book_cover");
            return bc ? (
              <button
                onClick={() => { setSelectedSegment(null); setSelectedSpecial(bc); }}
                className={`w-full text-left px-3 py-2 text-xs font-semibold border-b border-gray-100 flex items-center gap-2 transition-colors ${
                  selectedSpecial?.type === "book_cover" ? "bg-sky/20 text-gray-800" : "hover:bg-peach/20 text-gray-700"
                }`}
              >
                <span className={`w-2 h-2 rounded-full shrink-0 ${bc.url ? "bg-green-400" : "bg-gray-300"}`} />
                Book Cover
              </button>
            ) : null;
          })()}

          {Object.entries(chapters)
            .sort(([a], [b]) => +a - +b)
            .map(([chIdx, info]) => (
              <div key={chIdx}>
                <div
                  className={`flex items-center border-b border-gray-100 transition-colors ${
                    generatingChapter === +chIdx
                      ? "bg-amber-50 border-l-2 border-l-amber-400"
                      : selectedChapter === +chIdx
                      ? "bg-coral/10"
                      : "hover:bg-peach/30"
                  }`}
                >
                  <button
                    onClick={() => {
                      const next = selectedChapter === +chIdx ? null : +chIdx;
                      if (next !== selectedChapter && dirtySegIds.current.size > 0) {
                        if (!window.confirm("You have unsaved segment edits. Discard them?")) return;
                        dirtySegIds.current = new Set();
                      }
                      setSelectedChapter(next);
                      setSelectedSpecial(null);
                    }}
                    className={`flex-1 text-left px-3 py-2 text-xs font-semibold flex items-center gap-1 min-w-0 ${
                      selectedChapter === +chIdx ? "text-coral" : "text-gray-700"
                    }`}
                  >
                    <span className="text-[10px] text-gray-400 w-4 shrink-0">{+chIdx + 1}</span>
                    <span className="truncate flex-1">{info.chapter_title}</span>
                    <span className="text-[10px] text-gray-400 shrink-0 ml-1">{info.num_segments}</span>
                  </button>
                  {generatingChapter === +chIdx ? (
                    <div className="mr-2 w-28">
                      <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-amber-400 rounded-full transition-all duration-500"
                          style={{ width: `${chapterProgress?.progress ?? 0}%` }}
                        />
                      </div>
                      <p className="text-[8px] text-amber-600 text-center mt-0.5 animate-pulse">
                        {(() => {
                          const agent = (chapterProgress as any)?.agent;
                          const agentInfo = agent ? AGENT_META[agent] : null;
                          const step = chapterProgress?.current_step || "Starting...";
                          return agentInfo ? `${agentInfo.icon} ${step}` : step;
                        })()}
                      </p>
                    </div>
                  ) : (
                    <button
                      onClick={async (e) => {
                        e.stopPropagation();
                        setGeneratingChapter(+chIdx);
                        setAgentPanelOpen(true);
                        try {
                          await generateChapter(bookId, +chIdx);
                        } catch (err: any) {
                          console.error(err);
                          // Reset so the Gen buttons don't stay disabled forever
                          setGeneratingChapter(null);
                          alert(`Chapter generation failed: ${err?.response?.data?.detail || err?.message || err}`);
                        }
                      }}
                      disabled={generatingChapter !== null || !canEdit}
                      className="w-8 h-6 mr-1 text-[9px] bg-coral/80 text-white rounded hover:bg-coral transition-colors disabled:opacity-50 shrink-0"
                      title="Generate illustrations for this chapter"
                    >
                      Gen
                    </button>
                  )}
                </div>

                {selectedChapter === +chIdx && (<>
                  {/* Chapter Cover */}
                  {(() => {
                    const cc = specialPages.find(p => p.type === "chapter_cover" && p.chapter === +chIdx);
                    return cc ? (
                      <button
                        onClick={() => { setSelectedSegment(null); setSelectedSpecial(cc); }}
                        className={`w-full text-left px-6 py-2 text-xs border-b border-gray-50 transition-colors flex items-center gap-1.5 ${
                          selectedSpecial?.type === "chapter_cover" && selectedSpecial?.chapter === +chIdx
                            ? "bg-sky/20 text-gray-800" : "hover:bg-gray-50 text-gray-500"
                        }`}
                      >
                        <span className={`w-2 h-2 rounded-full shrink-0 ${cc.url ? "bg-green-400" : "bg-gray-300"}`} />
                        <span>Chapter Cover</span>
                      </button>
                    ) : null;
                  })()}

                  {segments.map((seg, idx) => {
                    const isGenerating = generatingChapter === +chIdx && isSegmentPageStep(chapterProgress?.current_step, idx);
                    const hasIllustration = !!seg.illustration_url;
                    return (
                    <button
                      key={seg.id}
                      onClick={() => { setSelectedSegment(seg); setSelectedSpecial(null); }}
                      className={`w-full text-left px-6 py-2 text-xs border-b border-gray-50 transition-colors ${
                        isGenerating
                          ? "bg-amber-50"
                          : selectedSegment?.id === seg.id && !selectedSpecial
                          ? "bg-sky/20 text-gray-800"
                          : "hover:bg-gray-50 text-gray-500"
                      }`}
                    >
                      <div className="flex items-center gap-1.5">
                        <span
                          className={`w-2 h-2 rounded-full shrink-0 ${
                            isGenerating ? "bg-amber-400 animate-pulse" : staleSegIds.has(seg.id) ? "bg-red-500" : hasIllustration ? "bg-green-400" : "bg-gray-300"
                          }`}
                          title={staleSegIds.has(seg.id) ? `Stale — ${staleReasons[seg.id] || "a character/scene changed"}; regenerate` : undefined}
                        />
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
                  );
                  })}

                </>)}
              </div>
            ))}

          {/* Back Cover */}
          {(() => {
            const bc = specialPages.find(p => p.type === "back_cover");
            return bc ? (
              <button
                onClick={() => { setSelectedSegment(null); setSelectedSpecial(bc); }}
                className={`w-full text-left px-3 py-2 text-xs font-semibold border-b border-gray-100 flex items-center gap-2 transition-colors ${
                  selectedSpecial?.type === "back_cover" ? "bg-sky/20 text-gray-800" : "hover:bg-peach/20 text-gray-700"
                }`}
              >
                <span className={`w-2 h-2 rounded-full shrink-0 ${bc.url ? "bg-green-400" : "bg-gray-300"}`} />
                Back Cover
              </button>
            ) : null;
          })()}
        </div>

        {/* Main content */}
        <div className="flex-1 flex overflow-hidden">
          {selectedSpecial ? (
            <SpecialPageView
              canGenerate={canEdit}
              special={selectedSpecial}
              meta={meta}
              bookId={bookId}
              characters={characters}
              sheets={sheets}
              portraits={portraits}
              locations={locations}
              sceneSheets={sceneSheets}
              segments={segments}
              specialCacheBust={specialCacheBust}
              sheetCacheBust={sheetCacheBust}
              regenSpecial={regenSpecial}
              onRegenerate={handleRegenSpecial}
              onRestored={() => {
                // A restore swapped the current image on disk under the same
                // filename — refetch the list (urls/extensions may change) and
                // bust the image cache so the restored version actually shows.
                getSpecialPages(bookId).then(d => {
                  setSpecialPages(d.pages || []);
                  setSelectedSpecial(prev => prev
                    ? (d.pages || []).find(p => p.type === prev.type && (p.chapter ?? 0) === (prev.chapter ?? 0)) || prev
                    : prev);
                }).catch(() => {});
                setSpecialCacheBust(Date.now());
              }}
              onNavigateToCharacter={(charName) => {
                setNavigateToChar(charName);
                setActiveTab("characters");
              }}
              onNavigateToScene={() => setActiveTab("scenes")}
            />
          ) : selectedSegment ? (
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
                  <AutoTextarea
                    value={selectedSegment.simplified_text || ""}
                    onChange={(e) => updateField("simplified_text", e.target.value)}
                    className="w-full rounded-lg border border-peach/50 p-3 text-xs focus:ring-2 focus:ring-coral/30 focus:border-coral outline-none resize-none !leading-[1.26] min-h-[3rem]"
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
                  <AutoTextarea
                    value={selectedSegment.scene_background || ""}
                    onChange={(e) => updateField("scene_background", e.target.value)}
                    className="w-full rounded-lg border border-peach/50 p-3 text-xs focus:ring-2 focus:ring-coral/30 focus:border-coral outline-none resize-none !leading-[1.26] min-h-[2.5rem]"
                    placeholder="Click 'Generate' or describe the setting..."
                  />
                </div>

                {/* Characters + Actions */}
                <div className="card !p-3">
                  <h3 className="font-display font-bold text-gray-700 mb-2 text-xs flex items-center gap-1">
                    <Users size={12} /> Characters & Actions
                    {regenningBg && <span className="text-[9px] text-gray-400 ml-1 animate-pulse">updating scene...</span>}
                  </h3>
                  <div className="space-y-1.5">
                    {(selectedSegment.character_actions || []).map((ca, idx) => (
                      <div key={idx} className="flex gap-1.5 items-center">
                        <div className="w-1/3 relative">
                          <input
                            list={`char-list-${idx}`}
                            value={ca.name}
                            onChange={(e) => updateAction(idx, "name", e.target.value)}
                            className="w-full rounded-md border border-peach/50 px-2 py-1.5 text-xs focus:ring-2 focus:ring-coral/30 outline-none !leading-[1.26]"
                            placeholder="Name"
                          />
                          <datalist id={`char-list-${idx}`}>
                            {characters.map(c => (
                              <option key={c.canonical_name} value={c.canonical_name}>
                                {c.role}
                              </option>
                            ))}
                          </datalist>
                        </div>
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
                    {/* Add character: dropdown to pick from list, or type custom */}
                    <div className="flex gap-1.5 items-center">
                      <select
                        value=""
                        onChange={(e) => {
                          if (!e.target.value || !selectedSegment) return;
                          const name = e.target.value;
                          const segId = selectedSegment.id;
                          mutateSegment(segId, (seg) => addAction(seg, name));
                          triggerSceneBackgroundRegen(segId);
                        }}
                        className="text-xs text-coral font-semibold bg-transparent border border-peach/30 rounded-md px-1 py-0.5 outline-none cursor-pointer"
                      >
                        <option value="">+ Pick from list</option>
                        {characters
                          .filter(c => !(selectedSegment.character_actions || []).some(ca => ca.name === c.canonical_name))
                          .map(c => (
                            <option key={c.canonical_name} value={c.canonical_name}>
                              {c.canonical_name} ({c.role})
                            </option>
                          ))}
                      </select>
                      <button
                        onClick={addCharacterAction}
                        className="text-xs text-gray-500 hover:text-coral font-semibold"
                      >
                        + Custom
                      </button>
                    </div>
                  </div>
                </div>

                {/* Summary + Sentiment */}
                <div className="card !p-3">
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="font-display font-bold text-gray-700 text-xs">Summary & Sentiment</h3>
                    <button
                      onClick={async () => {
                        const segId = selectedSegment.id;
                        try {
                          const res = await generateSummary(bookId, segId);
                          // Merge into the LATEST state (not the click-time snapshot),
                          // and only touch the open segment if the user hasn't
                          // switched away while the LLM call was in flight.
                          setSegments((prev) => prev.map((s) =>
                            s.id === segId ? { ...s, scene_summary: res.scene_summary, sentiment: res.sentiment } : s));
                          if (selectedSegIdRef.current === segId) {
                            setSelectedSegment((prev) =>
                              prev && prev.id === segId
                                ? { ...prev, scene_summary: res.scene_summary, sentiment: res.sentiment }
                                : prev);
                          }
                        } catch (e) { console.error(e); }
                      }}
                      className="text-[10px] bg-sky/50 hover:bg-sky text-gray-700 px-2 py-0.5 rounded font-semibold"
                    >
                      Generate
                    </button>
                  </div>
                  <AutoTextarea
                    value={selectedSegment.scene_summary || ""}
                    onChange={(e) => updateField("scene_summary", e.target.value)}
                    className="w-full rounded-lg border border-peach/50 p-3 text-xs focus:ring-2 focus:ring-coral/30 focus:border-coral outline-none resize-none !leading-[1.26] mb-2 min-h-[2.5rem]"
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

                {/* LLM Prompt Preview */}
                <AIChatPanel
                  chatOpen={chatOpen}
                  onToggle={() => setChatOpen(!chatOpen)}
                  selectedSegment={selectedSegment}
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
                    disabled={regenerating || saving || !canEdit}
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
                  locations={locations}
                  sceneSheets={sceneSheets}
                  cacheBust={sheetCacheBust}
                  bookId={bookId}
                  onRegenerateSheet={handleRegenerateSheet}
                  onNavigateToCharacter={(charName) => {
                    setNavigateToChar(charName);
                    setActiveTab("characters");
                  }}
                  onNavigateToScene={(locName) => {
                    setSceneNav(locName);
                    setActiveTab("scenes");
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

      {/* Persistent live Agent Activity column (sits alongside tab content) */}
      {agentPanelOpen && (
        <AgentActivityPanel
          bookId={bookId}
          chapterIdx={generatingChapter ?? selectedChapter}
          isGenerating={generatingChapter !== null}
          currentAgent={(chapterProgress as any)?.agent || null}
          onClose={() => setAgentPanelOpen(false)}
        />
      )}
      </div>
    </div>
  );
}
