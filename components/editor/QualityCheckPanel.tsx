import { Shield } from "lucide-react";

interface QualityResult {
  overall_score: number | null;  // null = the QA call failed (unknown, not 100)
  segment_id: number;
  page: number;
  character_consistency: { score: number; characters: Array<{ name: string; score: number; issues: string[] }> };
  spelling: { score: number; errors: string[] };
  duplicate_characters: { score: number; duplicates: string[] };
  name_face_mismatch: { score: number; mismatches: string[] };
  character_count: { score: number; expected: number; found: number; missing: string[]; extra: string[] };
  regeneration_feedback: string;
}

interface QualityCheckPanelProps {
  qualityResult: QualityResult | null;
  checkingQuality: boolean;
  hasIllustration: boolean;
  onRunCheck: () => void;
}

export default function QualityCheckPanel({
  qualityResult,
  checkingQuality,
  hasIllustration,
  onRunCheck,
}: QualityCheckPanelProps) {
  return (
    <div className="w-1/2 overflow-y-auto p-3">
      <div className="card !p-3">
        <div className="flex items-center justify-between mb-2">
          <h3 className="font-display font-bold text-gray-700 text-xs flex items-center gap-1">
            <Shield size={12} /> Quality Check
          </h3>
          <button
            onClick={onRunCheck}
            disabled={checkingQuality || !hasIllustration}
            className="text-[10px] font-semibold bg-sky/50 hover:bg-sky text-gray-700 px-2 py-0.5 rounded transition-colors disabled:opacity-50 flex items-center gap-1"
          >
            <Shield size={10} className={checkingQuality ? "animate-spin" : ""} />
            {checkingQuality ? "..." : "Run"}
          </button>
        </div>

        {qualityResult ? (
          <>
            <div className="flex items-center gap-2 mb-2">
              {qualityResult.overall_score === null ? (
                <span className="text-sm font-semibold text-gray-400">QA unavailable — try again</span>
              ) : (
                <span className={`text-xl font-bold ${
                  qualityResult.overall_score >= 80 ? "text-green-600" :
                  qualityResult.overall_score >= 60 ? "text-yellow-600" : "text-red-600"
                }`}>{qualityResult.overall_score}%</span>
              )}
            </div>
            <div className="space-y-1.5 mb-2">
              {[
                { key: "character_consistency", label: "Character Match", data: qualityResult.character_consistency },
                { key: "spelling", label: "Spelling", data: qualityResult.spelling },
                { key: "duplicate_characters", label: "No Duplicates", data: qualityResult.duplicate_characters },
                { key: "name_face_mismatch", label: "Name-Face Match", data: qualityResult.name_face_mismatch },
                { key: "character_count", label: "Char Count", data: qualityResult.character_count },
              ].map(({ key, label, data }) => {
                // Coerce defensively: a stale/blank score (e.g. "") must not
                // render as an empty "%". Fall back to 100 when non-numeric.
                const n = Number(data?.score);
                const score = Number.isFinite(n) ? n : 100;
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
                      }`}>{c.score >= 80 ? "\u2713" : "!"}</span>
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
            {checkingQuality ? "Checking quality..." : hasIllustration ? "Auto-checking after generation..." : "Generate an illustration first."}
          </p>
        )}
      </div>
    </div>
  );
}
