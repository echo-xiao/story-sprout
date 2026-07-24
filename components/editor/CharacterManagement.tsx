"use client";

import { useState, useEffect, useRef } from "react";
import { Users, RefreshCw, Shield } from "lucide-react";
import { updateCharacter, regenerateCharacterSheet, getCharacters, getAssetVersions, selectVersion, autofillCharacterDetails, checkCharacterSheetQuality, getRegenActive } from "@/lib/api";
import AutoTextarea from "./AutoTextarea";
import type { CharacterInfo } from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

interface CharacterManagementProps {
  bookId: string;
  characters: CharacterInfo[];
  sheets: Record<string, string>;
  aliasMap: Record<string, string>;
  onCharactersUpdate: (characters: CharacterInfo[], sheets: Record<string, string>, renamedFrom?: string, renamedTo?: string) => void;
  navigateToChar?: string | null;
  onSelectChar?: (name: string) => void;
  canGenerate?: boolean;
}

export default function CharacterManagement({
  bookId,
  characters,
  sheets,
  aliasMap,
  onCharactersUpdate,
  navigateToChar,
  onSelectChar,
  canGenerate = true,
}: CharacterManagementProps) {
  const [selectedChar, setSelectedChar] = useState<string | null>(characters[0]?.canonical_name || null);
  const [editing, setEditing] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);
  const [regenning, setRegenning] = useState(false);
  const [versions, setVersions] = useState<Array<{ id: string; url: string; created_at: string }>>([]);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [activeSheetUrl, setActiveSheetUrl] = useState<string | null>(null);
  // Bumped after each regeneration to force the browser to reload the new sheet
  // (the current sheet keeps the same filename, so the URL alone won't change).
  const [sheetCacheBust, setSheetCacheBust] = useState(0);
  const [checkingQuality, setCheckingQuality] = useState(false);
  const [qualityResult, setQualityResult] = useState<{
    overall_score: number | null;
    character_name: string;
    is_group: boolean;
    appearance_match: { score: number; issues: string[] };
    internal_consistency: { score: number; issues: string[] };
    multi_angle: { score: number; has_front: boolean; has_side: boolean; has_back: boolean; has_expressions: boolean; issues: string[] };
    style_quality: { score: number; issues: string[] };
    text_labels: { score: number; issues: string[] };
    regeneration_feedback: string;
  } | null>(null);

  const selected = characters.find(c => c.canonical_name === selectedChar);

  // Stop handler-started polls after the component unmounts.
  const unmountedRef = useRef(false);
  // Reset on mount — StrictMode (dev) remounts reuse the ref, and without the
  // reset the cleanup left it permanently true, killing every poll's first tick.
  useEffect(() => {
    unmountedRef.current = false;
    return () => { unmountedRef.current = true; };
  }, []);

  // Snapshot of `editing` as of the last select/save — switching characters
  // with edits that differ from it asks for confirmation instead of silently
  // dropping them (segments already behave this way on chapter switch).
  const editingBaselineRef = useRef<string>("");
  const editingIsDirty = () =>
    Object.keys(editing).length > 0 &&
    editingBaselineRef.current !== "" &&
    JSON.stringify(editing) !== editingBaselineRef.current;

  // Report selected char to parent via effect (avoids setState-during-render)
  useEffect(() => {
    if (selectedChar) onSelectChar?.(selectedChar);
  }, [selectedChar]);

  // Latest selection for async handlers: autofill/quality responses landing
  // after the user switched characters must NOT be applied to the now-selected
  // one (autofill merged char A's appearance into char B's form, and Save then
  // persisted it onto B).
  const selectedCharRef = useRef<string | null>(selectedChar);
  useEffect(() => {
    selectedCharRef.current = selectedChar;
  }, [selectedChar]);

  const selectChar = (char: CharacterInfo) => {
    if (selectedChar !== char.canonical_name) {
      if (editingIsDirty() && !window.confirm("You have unsaved character edits. Discard them?")) {
        return;
      }
      setActiveSheetUrl(null);
      setQualityResult(null);
      setVersions([]);
      setSelectedVersionId(null);
    }
    setSelectedChar(char.canonical_name);
    const snapshot = {
      canonical_name: char.canonical_name,
      gender: char.gender || "unknown",
      role: char.role || "supporting",
      appearance: char.appearance || "",
      description: char.description || "",
      visual_details: char.visual_details || {},
    };
    setEditing(snapshot);
    editingBaselineRef.current = JSON.stringify(snapshot);
  };

  // Navigate to specific character when triggered from editor
  useEffect(() => {
    if (!navigateToChar) return;
    const char = characters.find(c => c.canonical_name === navigateToChar);
    if (char) {
      selectChar(char);
    }
  }, [navigateToChar]);

  // Load selectable versions when the character changes / after a regen.
  useEffect(() => {
    if (!selectedChar) return;
    getAssetVersions(bookId, "character", selectedChar)
      .then(data => {
        setVersions(data.versions || []);
        setSelectedVersionId(data.selected_version_id || null);
      })
      .catch(() => { setVersions([]); setSelectedVersionId(null); });
  }, [bookId, selectedChar, regenning]);

  // Pick a version → backend promotes it to the live sheet, so the editor, the
  // Pages "Characters in Scene" panel and page generation all use it. Selecting
  // does NOT generate a new version.
  const pickVersion = async (versionId: string) => {
    if (!selectedChar || !canGenerate) return;
    try {
      await selectVersion(bookId, "character", selectedChar, versionId);
      setSelectedVersionId(versionId);
      const data = await getCharacters(bookId);
      onCharactersUpdate(data.characters || [], data.sheets || {});
      setSheetCacheBust(Date.now());
    } catch (e: any) {
      alert(`Select failed: ${e?.response?.data?.detail || e?.message || e}`);
    }
  };

  // Auto-select first character once data is available. In an effect, not the
  // render body — calling setState (selectChar) during render warns under
  // StrictMode and can loop.
  useEffect(() => {
    // When a navigation target exists, let the navigateToChar effect own the
    // selection — on mount both effects see render-1 state and this one runs
    // LAST, so it used to override deep links back to the first character.
    if (navigateToChar) return;
    if (selected && Object.keys(editing).length === 0) {
      selectChar(selected);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, navigateToChar]);

  // Regenerate a character's sheet BY NAME (not the possibly-stale selectedChar),
  // poll until it lands, bust the preview cache, then auto quality-check.
  // Does NOT refresh the parent — the caller does that with the right rename args.
  const regenAndCheck = async (charName: string) => {
    setRegenning(true);
    setQualityResult(null);
    let failed = false;
    try {
      await regenerateCharacterSheet(bookId, charName);
      // Poll until new sheet appears instead of blindly waiting 30s
      await new Promise<void>((resolve) => {
        const poll = setInterval(async () => {
          if (unmountedRef.current) { clearInterval(poll); resolve(); return; }
          try {
            // Claim is the SINGLE source of truth. The old "a 'current' sheet
            // exists" check reported success even when a failed regen had just
            // RESTORED the previous sheet (current always exists for a
            // character that already had one), swallowing the real error.
            const st = await getRegenActive(bookId, "character", charName).catch(() => null);
            if (!st || st.active !== false) return;  // still running (or transient fetch error)
            clearInterval(poll);
            if (st.error) {
              failed = true;
              alert(`Regeneration failed: ${st.error}`);
            }
            resolve();
          } catch {}
        }, 5000);
        // 240s: sheet regen may now self-correct (2x generate + 2x QA worst case)
        setTimeout(() => { clearInterval(poll); if (!unmountedRef.current) alert("Still generating in the background — reload in a minute to see it."); resolve(); }, 240000);
      });
      setSheetCacheBust(Date.now());  // force big preview + Current thumbnail to reload
    } finally {
      setRegenning(false);
    }
    if (failed) return; // nothing new to quality-check
    // Auto quality check after generation completes
    setCheckingQuality(true);
    try {
      const result = await checkCharacterSheetQuality(bookId, charName);
      // Only show it if the user is still on this character.
      if (selectedCharRef.current === charName) setQualityResult(result);
    } catch {} finally {
      setCheckingQuality(false);
    }
  };

  const mainChars = characters.filter(c => c.role === "main");
  const otherChars = characters.filter(c => c.role !== "main");
  const [genAllRunning, setGenAllRunning] = useState(false);
  const [genAllProgress, setGenAllProgress] = useState("");
  const [genAllCurrentChar, setGenAllCurrentChar] = useState<string | null>(null);
  const [autoFilling, setAutoFilling] = useState(false);

  const handleGenerateAll = async () => {
    const toGenerate = characters.filter(c => !sheets[c.canonical_name]);
    if (toGenerate.length === 0) {
      alert("All characters already have sheets!");
      return;
    }
    setGenAllRunning(true);
    try {
      let failed = false;
      for (let i = 0; i < toGenerate.length; i++) {
        const char = toGenerate[i];
        setGenAllProgress(`${i + 1}/${toGenerate.length}`);
        setGenAllCurrentChar(char.canonical_name);
        try {
          await regenerateCharacterSheet(bookId, char.canonical_name);
          // Wait for sheet to appear
          await new Promise<void>((resolve) => {
            const poll = setInterval(async () => {
              if (unmountedRef.current) { clearInterval(poll); resolve(); return; }
              try {
                // Claim-first (same as the single-char path): a failed regen
                // restores the old sheet, so "current exists" can't tell
                // success from failure. Stop the whole run on failure — the
                // next ones (same key/quota) would fail identically.
                const st = await getRegenActive(bookId, "character", char.canonical_name).catch(() => null);
                if (!st || st.active !== false) return;  // still running (or transient fetch error)
                clearInterval(poll);
                if (st.error) {
                  failed = true;
                  alert(`Regeneration failed: ${st.error}`);
                }
                resolve();
              } catch {}
            }, 10000);
            setTimeout(() => { clearInterval(poll); if (!unmountedRef.current) alert("Still generating in the background — reload in a minute to see it."); resolve(); }, 240000);
          });
        } catch {}
        if (failed) break;
      }
      // Final refresh
      const finalData = await getCharacters(bookId);
      onCharactersUpdate(finalData.characters || [], finalData.sheets || {});
    } catch (e) {
      console.error("Gen All refresh failed:", e);
    } finally {
      // Without this, a thrown refresh left genAllRunning true and the
      // button disabled until a full page reload.
      setGenAllProgress("");
      setGenAllCurrentChar(null);
      setGenAllRunning(false);
    }
  };

  const handleAutoFill = async () => {
    if (!selectedChar) return;
    const charName = selectedChar;
    setAutoFilling(true);
    try {
      const result = await autofillCharacterDetails(bookId, charName);
      if (selectedCharRef.current !== charName) {
        // User switched characters while the LLM ran — the backend saved the
        // autofill on the right character; merging it into the form now would
        // write charName's appearance onto the newly-selected one.
        return;
      }
      setEditing(prev => ({
        ...prev,
        appearance: result.appearance || prev.appearance,
        visual_details: result.visual_details || prev.visual_details,
      }));
      // The backend persisted the autofill, so fold it into the dirty-check
      // baseline too — otherwise switching away after a plain autofill would
      // ask to discard edits that are already saved.
      try {
        const base = JSON.parse(editingBaselineRef.current || "{}");
        editingBaselineRef.current = JSON.stringify({
          ...base,
          appearance: result.appearance || base.appearance,
          visual_details: result.visual_details || base.visual_details,
        });
      } catch {}
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
            onClick={handleGenerateAll}
            disabled={genAllRunning || regenning}
            className="text-[9px] bg-coral/80 text-white px-2 py-0.5 rounded hover:bg-coral transition-colors disabled:opacity-50"
          >
            {genAllRunning ? genAllProgress || "Generating..." : "Gen All"}
          </button>
        </div>
        {mainChars.map(char => (
          <CharListItem
            key={char.canonical_name}
            char={char}
            selected={selectedChar === char.canonical_name}
            hasSheet={!!sheets[char.canonical_name]}
            generating={genAllCurrentChar === char.canonical_name}
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
            generating={genAllCurrentChar === char.canonical_name}
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
                    src={`${API_BASE}${activeSheetUrl || sheets[selected.canonical_name]}${activeSheetUrl || !sheetCacheBust ? "" : `${(sheets[selected.canonical_name] || "").includes("?") ? "&" : "?"}v=${sheetCacheBust}`}`}
                    alt={selected.canonical_name}
                    decoding="async"
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

            {/* Version thumbnails — click to pick the one used in the book/PDF */}
            {versions.length > 1 && (
              <div className="w-[320px] shrink-0 overflow-y-auto p-4 space-y-3 border-l border-peach/20">
                <p className="text-xs text-gray-500 font-semibold">Versions ({versions.length})</p>
                {versions.slice().reverse().map((v, idx) => (
                  <div key={v.id}>
                    <img
                      src={`${v.url.startsWith("http") ? "" : API_BASE}${v.url}`}
                      alt={v.id === selectedVersionId ? "Selected" : `v${versions.length - idx}`}
                      loading="lazy"
                      decoding="async"
                      onClick={() => pickVersion(v.id)}
                      className={`w-full rounded-xl cursor-pointer border-2 transition-colors ${
                        v.id === selectedVersionId
                          ? "border-coral shadow-md"
                          : "border-transparent hover:border-coral/50"
                      }`}
                    />
                    <p className="text-[10px] text-gray-400 text-center mt-1">
                      {v.id === selectedVersionId ? "Selected" : `Version ${versions.length - idx}`}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Quality Check column (between thumbnails and edit fields) */}
          {!!(selectedChar && sheets[selectedChar]) && (
            <div className="w-[240px] shrink-0 overflow-y-auto p-3 border-r border-peach/20">
              <div className="card !p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <h3 className="font-display font-bold text-gray-700 text-xs flex items-center gap-1">
                    <Shield size={12} /> Quality Check
                  </h3>
                  <button
                    onClick={async () => {
                      if (!selectedChar) return;
                      const charName = selectedChar;
                      setCheckingQuality(true);
                      try {
                        const result = await checkCharacterSheetQuality(bookId, charName);
                        // Don't show char A's score under char B after a switch.
                        if (selectedCharRef.current === charName) setQualityResult(result);
                      } catch (e) {
                        console.error("Quality check failed:", e);
                      } finally {
                        setCheckingQuality(false);
                      }
                    }}
                    disabled={checkingQuality}
                    className="text-[10px] font-semibold bg-sky/50 hover:bg-sky text-gray-700 px-2 py-0.5 rounded transition-colors disabled:opacity-50 flex items-center gap-1"
                  >
                    <Shield size={10} className={checkingQuality ? "animate-spin" : ""} />
                    {checkingQuality ? "..." : "Run"}
                  </button>
                </div>

                {qualityResult ? (
                  <>
                    <div className="flex items-center gap-2">
                      {qualityResult.overall_score === null ? (
                        <span className="text-sm font-semibold text-gray-400">QA unavailable — try again</span>
                      ) : (
                      <span className={`text-xl font-bold ${
                        qualityResult.overall_score >= 80 ? "text-green-600" :
                        qualityResult.overall_score >= 60 ? "text-yellow-600" : "text-red-600"
                      }`}>{qualityResult.overall_score}%</span>
                      )}
                      {qualityResult.is_group && (
                        <span className="text-[9px] bg-lavender/30 px-1.5 py-0.5 rounded text-gray-600">Group</span>
                      )}
                    </div>

                    <div className="space-y-1.5">
                      {[
                        { key: "appearance_match", label: "Appearance", data: qualityResult.appearance_match },
                        { key: "internal_consistency", label: "Consistency", data: qualityResult.internal_consistency },
                        { key: "multi_angle", label: "Multi-Angle", data: qualityResult.multi_angle },
                        { key: "style_quality", label: "Style", data: qualityResult.style_quality },
                        { key: "text_labels", label: "Text & Labels", data: qualityResult.text_labels },
                      ].map(({ key, label, data }) => {
                        const score = data?.score ?? 100;
                        return (
                          <div key={key}>
                            <div className="flex items-center gap-1 text-xs">
                              <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                                score >= 80 ? "bg-green-500" : score >= 60 ? "bg-yellow-500" : "bg-red-500"
                              }`} />
                              <span className="text-gray-600 flex-1">{label}</span>
                              <span className={`font-bold ${
                                score >= 80 ? "text-green-600" : score >= 60 ? "text-yellow-600" : "text-red-600"
                              }`}>{score}%</span>
                            </div>
                            {(data?.issues?.length ?? 0) > 0 && (
                              <ul className="text-[10px] text-gray-500 pl-4 mt-0.5 space-y-0.5">
                                {data.issues.slice(0, 5).map((issue: string, i: number) => (
                                  <li key={i} className="list-disc">{issue}</li>
                                ))}
                              </ul>
                            )}
                          </div>
                        );
                      })}
                    </div>

                    {qualityResult.multi_angle && (
                      <div className="flex gap-1 flex-wrap">
                        {(["front", "side", "back", "expressions"] as const).map(view => {
                          const has = qualityResult.multi_angle[`has_${view}` as keyof typeof qualityResult.multi_angle];
                          return (
                            <span key={view} className={`text-[9px] px-1.5 py-0.5 rounded ${
                              has ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"
                            }`}>
                              {has ? "\u2713" : "\u2717"} {view}
                            </span>
                          );
                        })}
                      </div>
                    )}

                    {qualityResult.regeneration_feedback && (
                      <div className="bg-amber-50 rounded-lg p-2 text-[10px] text-amber-800">
                        <p className="font-semibold mb-0.5">Suggested fix:</p>
                        <p>{qualityResult.regeneration_feedback}</p>
                      </div>
                    )}
                  </>
                ) : (
                  <p className="text-xs text-gray-400">
                    {checkingQuality ? "Analyzing sheet with AI..." : "Click Run to check quality."}
                  </p>
                )}
              </div>
            </div>
          )}

          {/* Right: Edit Fields */}
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
              <AutoTextarea
                value={editing.appearance || ""}
                onChange={e => setEditing(prev => ({ ...prev, appearance: e.target.value }))}
                className="w-full rounded-lg border border-peach/50 px-3 py-2 text-sm min-h-[3rem]"
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
                  <AutoTextarea
                    value={(editing.visual_details || {})[key] || ""}
                    onChange={e => setEditing(prev => ({
                      ...prev,
                      visual_details: { ...(prev.visual_details || {}), [key]: e.target.value }
                    }))}
                    className="flex-1 rounded-md border border-peach/40 px-2 py-1 text-xs min-h-[1.75rem]"
                    placeholder={placeholder}
                  />
                </div>
              ))}
            </div>

            <div>
              <label className="text-xs text-gray-500 font-semibold mb-1 block">Description</label>
              <AutoTextarea
                value={editing.description || ""}
                onChange={e => setEditing(prev => ({ ...prev, description: e.target.value }))}
                className="w-full rounded-lg border border-peach/50 px-3 py-2 text-sm min-h-[3rem]"
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
                  const oldName = selectedChar;
                  const newName = (editing.canonical_name || oldName).trim() || oldName;
                  const renamed = oldName !== newName;
                  setSaving(true);
                  try {
                    await updateCharacter(bookId, oldName, editing);
                    // Saved — this editing state is the new clean baseline.
                    editingBaselineRef.current = JSON.stringify(editing);
                    // Refresh parent + switch selection to the new name UP FRONT,
                    // so the panel keeps the character selected (and regen targets
                    // the right sheet) while the slow regeneration runs.
                    const pre = await getCharacters(bookId);
                    onCharactersUpdate(
                      pre.characters || [],
                      pre.sheets || {},
                      renamed ? oldName : undefined,
                      renamed ? newName : undefined,
                    );
                    if (renamed) setSelectedChar(newName);
                    await regenAndCheck(newName);
                    const post = await getCharacters(bookId);
                    onCharactersUpdate(post.characters || [], post.sheets || {});
                  } catch (e: any) {
                    console.error("Save & regen failed:", e);
                    const msg = e?.response?.data?.detail || e?.message || String(e);
                    alert(`Generate failed: ${msg}`);
                  } finally {
                    setSaving(false);
                  }
                }}
                disabled={saving || regenning || !canGenerate}
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
