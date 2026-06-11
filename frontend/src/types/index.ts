export interface GenerationConfig {
  style?: string;
  selected_chapters?: number[];
  education_goal?: string;
  email?: string;
  gemini_api_key?: string;
}

// Editor types
export interface CharacterAction {
  name: string;
  action: string;
}

export interface Segment {
  id: number;
  chapter_idx: number;
  text: string;
  simplified_text?: string;
  scene_direction?: string;
  characters_in_scene: string[];
  character_actions: CharacterAction[];
  scene_background: string;
  scene_summary: string;
  sentiment: string;
  is_key_event: boolean;
  event_description?: string;
  illustration_url?: string;
}

export interface ChapterInfo {
  chapter_title: string;
  num_segments: number;
  segment_ids: number[];
}

export interface CharacterInfo {
  canonical_name: string;
  aliases: string[];
  gender: string;
  role: string;
  description: string;
  appearance: string;
  visual_details?: Record<string, string>;
  sheet_url?: string;
}
