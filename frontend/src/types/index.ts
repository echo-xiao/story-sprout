export interface GenerationConfig {
  age_group: "2-4" | "4-6" | "6-8";
  num_pages: number;
  template: "classic" | "journey" | "simple";
  style?: string;
  selected_chapters?: number[];
  education_goal?: string;
}

export interface PageData {
  page_number: number;
  text: string;
  illustration_url: string;
  illustration_prompt: string;
  layout: "full_image_text_bottom" | "left_image_right_text" | "full_spread" | "text_over_image";
}

export interface QAResults {
  passes: boolean;
  safety: { is_safe: boolean; flagged_pages: Array<{ page: number; reason: string }>; overall_score: number };
  readability: { passes: boolean; per_page: Array<{ page: number; grade_level: number; word_count: number; issues: string[] }> };
  coverage: { coverage_score: number; covered_events: string[]; missed_events: string[] };
  hallucination: { hallucination_score: number; new_entities: string[]; is_acceptable: boolean };
  summary: string;
}

export interface PictureBook {
  book_id: string;
  title: string;
  pages: PageData[];
  created_at: string;
  config: GenerationConfig;
  qa_results?: QAResults;
}

export interface GenerationStatus {
  book_id: string;
  status: "queued" | "analyzing" | "generating_text" | "generating_images" | "qa_check" | "complete" | "failed";
  progress: number;
  current_step: string;
  error?: string;
}

export interface BookAnalysis {
  segments: Array<{ id: number; text: string; title?: string }>;
  characters: Array<{ name: string; role: string; mention_count: number }>;
  sentiment: { scores: number[]; peaks: number[]; valleys: number[]; overall_arc: string };
  complexity: { flesch_kincaid_grade: number; avg_sentence_length: number };
  key_events: Array<{ segment_id: number; summary: string; importance_score: number }>;
}
