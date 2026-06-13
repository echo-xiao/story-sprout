import { useEffect, useRef, useState } from "react";
import { Image as ImageIcon, RefreshCw, Save, Users, MapPin } from "lucide-react";
import type { CharacterInfo, Segment } from "@/types";
import {
  getSpecialPageHistory,
  restoreSpecialPageVersion,
  checkSpecialPageQuality,
  updateSpecialPage,
  type SpecialPageData,
} from "@/lib/api";
import AutoTextarea from "@/components/editor/AutoTextarea";
import VersionsCarousel from "@/components/editor/VersionsCarousel";
import QualityCheckPanel from "@/components/editor/QualityCheckPanel";
import CharacterSheetsPanel from "@/components/editor/CharacterSheetsPanel";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export type SpecialPage = SpecialPageData;

interface Props {
  special: SpecialPage;
  meta: { title?: string };
  bookId: string;
  characters: CharacterInfo[];
  sheets: Record<string, string>;
  portraits?: Record<string, string>;
  locations: any[];
  sceneSheets: Record<string, string>;
  segments: Segment[];
  specialCacheBust: number;
  sheetCacheBust?: number;
  regenSpecial: boolean;
  onRegenerate: () => void;
  onRestored?: () => void;
  onNavigateToCharacter?: (charName: string) => void;
  onNavigateToScene?: () => void;
  canGenerate?: boolean;
}

/** Same editing surface as story pages — editable record (title/subtitle/
 * background/summary/characters), versions carousel, quality check, and the
 * character & scene reference panel — backed by the special-page record API. */
export default function SpecialPageView({
  special,
  meta,
  bookId,
  characters,
  sheets,
  portraits,
  locations,
  sceneSheets,
  specialCacheBust,
  sheetCacheBust = 0,
  regenSpecial,
  onRegenerate,
  onRestored,
  onNavigateToCharacter,
  onNavigateToScene,
  canGenerate = true,
}: Props) {
  const spChapter = special.chapter ?? 0;
  const recordKey = special.key || `${special.type}:${spChapter}`;

  // Local editable copy of the record. Reset when the user switches pages.
  const [record, setRecord] = useState<SpecialPageData>(special);
  const dirty = useRef(false);
  const [saving, setSaving] = useState(false);
  const [historyImages, setHistoryImages] = useState<Array<{ url: string; version: string; timestamp: number; quality?: any }>>([]);
  const [qualityResult, setQualityResult] = useState<any>(null);
  const [checkingQuality, setCheckingQuality] = useState(false);
  const [restoring, setRestoring] = useState(false);

  useEffect(() => {
    setRecord(special);
    dirty.current = false;
  }, [recordKey, special]);

  // Versions + cached quality, reloaded after each regen/restore completes.
  useEffect(() => {
    let cancelled = false;
    getSpecialPageHistory(bookId, special.type, spChapter)
      .then((data) => {
        if (cancelled) return;
        const images = data.images || [];
        setHistoryImages(images);
        setQualityResult(images.find((i) => i.version === "current")?.quality || null);
      })
      .catch(() => {
        if (!cancelled) { setHistoryImages([]); setQualityResult(null); }
      });
    return () => { cancelled = true; };
  }, [bookId, special.type, spChapter, regenSpecial, restoring, specialCacheBust]);

  const updateField = (field: keyof SpecialPageData, value: unknown) => {
    dirty.current = true;
    setRecord((prev) => ({ ...prev, [field]: value }));
  };

  const saveRecord = async (): Promise<boolean> => {
    setSaving(true);
    try {
      await updateSpecialPage(bookId, special.type, spChapter, {
        title_text: record.title_text,
        subtitle_text: record.subtitle_text,
        scene_background: record.scene_background,
        scene_summary: record.scene_summary,
        characters_in_scene: record.characters_in_scene,
      });
      dirty.current = false;
      return true;
    } catch (e: any) {
      alert(`Save failed: ${e?.response?.data?.detail || e?.message || e}`);
      return false;
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAndRegen = async () => {
    if (await saveRecord()) onRegenerate();
  };

  const handleSelectVersion = async (img: { url: string; version: string; quality?: any }) => {
    if (regenSpecial || restoring) return;
    setQualityResult(img.quality || null);
    if (img.version === "current") return;
    setRestoring(true);
    try {
      await restoreSpecialPageVersion(bookId, special.type, spChapter, img.version);
      onRestored?.();
    } catch (e: any) {
      alert(`Restore failed: ${e?.response?.data?.detail || e?.message || e}`);
    } finally {
      setRestoring(false);
    }
  };

  const handleRunQualityCheck = async () => {
    setCheckingQuality(true);
    try {
      const result = await checkSpecialPageQuality(bookId, special.type, spChapter);
      setQualityResult(result);
    } catch (e: any) {
      alert(`Quality check failed: ${e?.response?.data?.detail || e?.message || e}`);
    } finally {
      setCheckingQuality(false);
    }
  };

  // Pseudo-segment so the shared reference panel + carousel work unchanged.
  const pseudoSegment = {
    id: -1,
    characters_in_scene: record.characters_in_scene || [],
    scene_background: record.scene_background || "",
    illustration_url: special.url || "",
  } as unknown as Segment;

  const sceneChars = record.characters_in_scene || [];

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Col 1: Image */}
      <div className="flex-1 overflow-y-auto p-6 flex flex-col items-center justify-center border-r border-peach/20">
        <h2 className="font-display text-lg font-bold text-gray-800 mb-4">{special.label}</h2>
        {special.url ? (
          <img
            src={`${API_BASE}${special.url}${
              specialCacheBust ? `${special.url.includes("?") ? "&" : "?"}v=${specialCacheBust}` : ""
            }`}
            alt={special.label}
            className="max-h-[calc(100vh-200px)] max-w-full rounded-xl shadow-md object-contain"
          />
        ) : regenSpecial ? (
          <div className="w-full max-w-md aspect-square bg-peach/10 rounded-xl flex flex-col items-center justify-center gap-3">
            <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-coral" />
            <p className="text-sm text-gray-500">Generating...</p>
            <p className="text-xs text-gray-400">~30 seconds</p>
          </div>
        ) : (
          <div className="w-full max-w-md aspect-square bg-peach/20 rounded-xl flex flex-col items-center justify-center text-gray-400 gap-2">
            <ImageIcon size={32} />
            <p className="text-xs">Not generated yet</p>
          </div>
        )}
      </div>

      {/* Col 2: Editable record (same layout as the segment prompt column) */}
      <div className="w-[32%] shrink-0 overflow-y-auto p-3 space-y-3">
        <VersionsCarousel
          historyImages={historyImages}
          selectedSegment={pseudoSegment}
          onSelectVersion={handleSelectVersion}
        />

        {/* Title / Subtitle */}
        <div className="card !p-3">
          <h3 className="font-display font-bold text-gray-700 text-xs mb-2">
            {special.type === "back_cover" ? "Closing Text" : "Title Text"}
          </h3>
          <AutoTextarea
            value={record.title_text || ""}
            onChange={(e) => updateField("title_text", e.target.value)}
            className="w-full rounded-lg border border-peach/50 p-3 text-xs focus:ring-2 focus:ring-coral/30 focus:border-coral outline-none resize-none !leading-[1.26] min-h-[2.5rem] mb-2"
            placeholder={special.type === "chapter_cover" ? "Chapter title..." : "Title shown on the page..."}
          />
          <h3 className="font-display font-bold text-gray-700 text-xs mb-2">Subtitle</h3>
          <AutoTextarea
            value={record.subtitle_text || ""}
            onChange={(e) => updateField("subtitle_text", e.target.value)}
            className="w-full rounded-lg border border-peach/50 p-3 text-xs focus:ring-2 focus:ring-coral/30 focus:border-coral outline-none resize-none !leading-[1.26] min-h-[2rem]"
            placeholder="Subtitle / secondary text..."
          />
        </div>

        {/* Scene Background */}
        <div className="card !p-3">
          <h3 className="font-display font-bold text-gray-700 text-xs flex items-center gap-1 mb-2">
            <MapPin size={12} /> Scene Background
          </h3>
          <AutoTextarea
            value={record.scene_background || ""}
            onChange={(e) => updateField("scene_background", e.target.value)}
            className="w-full rounded-lg border border-peach/50 p-3 text-xs focus:ring-2 focus:ring-coral/30 focus:border-coral outline-none resize-none !leading-[1.26] min-h-[2.5rem]"
            placeholder="Setting for this page — matched against scene reference sheets..."
          />
        </div>

        {/* Characters */}
        <div className="card !p-3">
          <h3 className="font-display font-bold text-gray-700 text-xs flex items-center gap-1 mb-2">
            <Users size={12} /> Characters
          </h3>
          <div className="space-y-1.5">
            {sceneChars.map((name, idx) => (
              <div key={`${name}-${idx}`} className="flex gap-1.5 items-center">
                <span className="flex-1 rounded-md border border-peach/50 px-2 py-1.5 text-xs bg-cream/40">{name}</span>
                <button
                  onClick={() => updateField("characters_in_scene", sceneChars.filter((_, i) => i !== idx))}
                  className="text-red-400 hover:text-red-600 text-sm w-5 shrink-0"
                >
                  &times;
                </button>
              </div>
            ))}
            <select
              value=""
              onChange={(e) => {
                if (!e.target.value) return;
                updateField("characters_in_scene", [...sceneChars, e.target.value]);
              }}
              className="text-xs text-coral font-semibold bg-transparent border border-peach/30 rounded-md px-1 py-0.5 outline-none cursor-pointer"
            >
              <option value="">+ Add character</option>
              {characters
                .filter((c) => !sceneChars.includes(c.canonical_name))
                .map((c) => (
                  <option key={c.canonical_name} value={c.canonical_name}>
                    {c.canonical_name} ({c.role})
                  </option>
                ))}
            </select>
          </div>
        </div>

        {/* Summary */}
        <div className="card !p-3">
          <h3 className="font-display font-bold text-gray-700 text-xs mb-2">Scene Summary</h3>
          <AutoTextarea
            value={record.scene_summary || ""}
            onChange={(e) => updateField("scene_summary", e.target.value)}
            className="w-full rounded-lg border border-peach/50 p-3 text-xs focus:ring-2 focus:ring-coral/30 focus:border-coral outline-none resize-none !leading-[1.26] min-h-[2.5rem]"
            placeholder="What this page should convey..."
          />
        </div>

        {/* Actions */}
        <div className="flex gap-2">
          <button
            onClick={saveRecord}
            disabled={saving || regenSpecial}
            className="btn-secondary text-xs !px-3 !py-1.5 flex items-center gap-1"
          >
            <Save size={12} />
            {saving ? "Saving..." : "Save"}
          </button>
          <button
            onClick={handleSaveAndRegen}
            disabled={regenSpecial || saving || !canGenerate}
            className="btn-primary text-xs !px-3 !py-1.5 flex items-center gap-1"
          >
            <RefreshCw size={12} className={regenSpecial ? "animate-spin" : ""} />
            {regenSpecial ? "Generating..." : special.url ? "Save & Regen" : "Save & Generate"}
          </button>
        </div>
        <p className="text-[10px] text-gray-400">
          Book: {meta.title || bookId}
        </p>
      </div>

      {/* Col 3: Quality + character/scene references (shared panels) */}
      <div className="flex-1 flex overflow-hidden">
        <QualityCheckPanel
          qualityResult={qualityResult}
          checkingQuality={checkingQuality}
          hasIllustration={!!special.url}
          onRunCheck={handleRunQualityCheck}
        />
        <CharacterSheetsPanel
          selectedSegment={pseudoSegment}
          characters={characters}
          sheets={sheets}
          portraits={portraits}
          locations={locations}
          sceneSheets={sceneSheets}
          cacheBust={sheetCacheBust}
          bookId={bookId}
          onNavigateToCharacter={onNavigateToCharacter}
          onNavigateToScene={onNavigateToScene}
        />
      </div>
    </div>
  );
}
