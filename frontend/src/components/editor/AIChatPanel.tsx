import { ChevronRight, FileText } from "lucide-react";
import { useMemo } from "react";
import type { Segment } from "@/types";

interface PromptPreviewPanelProps {
  chatOpen: boolean;
  onToggle: () => void;
  selectedSegment: Segment | null;
}

export default function AIChatPanel({
  chatOpen,
  onToggle,
  selectedSegment,
}: PromptPreviewPanelProps) {
  const prompt = useMemo(() => {
    if (!selectedSegment) return "";
    const seg = selectedSegment;
    const text = seg.simplified_text || seg.text?.slice(0, 200) || "";
    const background = seg.scene_background || seg.scene_direction || "";
    const summary = seg.scene_summary || "";
    const actions = seg.character_actions || [];
    const charBlock = actions.length > 0
      ? actions.map(ca => `- ${ca.name}: ${ca.action || "(no action)"}`).join("\n")
      : (seg.characters_in_scene || []).map(n => `- ${n}`).join("\n") || "no specific characters";

    return `Children's picture book illustration.

SCENE:
${summary || "(no summary)"}

BACKGROUND/SETTING:
${background || "(no background)"}

CHARACTERS AND ACTIONS:
${charBlock}
- ONLY draw these characters. No one else.
- EACH CHARACTER APPEARS EXACTLY ONCE.

STORY TEXT:
"${text}"
Embed naturally: speech bubbles for dialogue, scrolls/banners for narration.

+ Style reference (book cover image)
+ Character sheet images for each character above`;
  }, [selectedSegment]);

  return (
    <div className="card !p-3">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between"
      >
        <h3 className="font-display font-bold text-gray-700 text-xs flex items-center gap-1">
          <FileText size={12} /> LLM Prompt Preview
        </h3>
        <ChevronRight size={14} className={`text-gray-400 transition-transform ${chatOpen ? "rotate-90" : ""}`} />
      </button>

      {chatOpen && (
        <div className="mt-2">
          <pre className="bg-cream/50 rounded-lg p-3 text-[10px] text-gray-600 whitespace-pre-wrap break-words max-h-64 overflow-y-auto font-sans !leading-[1.4] border border-peach/20">
            {prompt || "Select a segment to preview the prompt."}
          </pre>
          <p className="text-[9px] text-gray-400 mt-1">
            This is the prompt sent to generate the illustration. Edit the fields above to change it.
          </p>
        </div>
      )}
    </div>
  );
}
