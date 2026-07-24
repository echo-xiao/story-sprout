// Shared agent metadata + preprocess step list. Previously duplicated verbatim
// in GenerationProgress.tsx and the editor page.

export interface AgentMeta {
  icon: string;
  name: string;       // short label, e.g. "Analyzer"
  fullName: string;   // e.g. "Analyzer Agent"
  color: string;      // tailwind text color class
}

export const AGENT_META: Record<string, AgentMeta> = {
  analyzer: { icon: "🔍", name: "Analyzer", fullName: "Analyzer Agent", color: "text-blue-600" },
  writer:   { icon: "✍️", name: "Writer",   fullName: "Writer Agent",   color: "text-purple-600" },
  artist:   { icon: "🎨", name: "Artist",   fullName: "Artist Agent",   color: "text-pink-600" },
  qa:       { icon: "✅", name: "QA",       fullName: "QA Agent",       color: "text-green-600" },
};

export const PREPROCESS_STEPS = [
  { key: "extract_text", label: "Extracting text and chapters", agent: "analyzer" },
  { key: "identify_characters", label: "Identifying characters with AI", agent: "analyzer" },
  { key: "build_aliases", label: "Building alias map", agent: "analyzer" },
  { key: "replace_aliases", label: "Replacing name aliases", agent: "analyzer" },
  { key: "segment_text", label: "Segmenting into scenes", agent: "analyzer" },
  { key: "annotate_complete", label: "Annotating characters, actions, sentiment", agent: "analyzer" },
];
