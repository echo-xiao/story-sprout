import axios from "axios";
import type { GenerationConfig } from "@/types";

const api = axios.create({
  baseURL: "/api",
  timeout: 300000,
});

// BYOK: attach the visitor's own Gemini key (+ email) to every request from
// localStorage. Generation endpoints require it server-side (403 otherwise) and
// bill it to the user's quota; read-only endpoints simply ignore it.
api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const key = localStorage.getItem("pbg_api_key");
    const email = localStorage.getItem("pbg_email");
    if (key) config.headers["X-Gemini-Key"] = key;
    if (email) config.headers["X-User-Email"] = email;
  }
  return config;
});

export async function fetchBookFromUrl(url: string): Promise<{ text: string; title: string }> {
  const { data } = await api.post("/fetch-url", { url });
  return data;
}

export async function startGeneration(
  text: string,
  config: GenerationConfig
): Promise<{ book_id: string }> {
  const { data } = await api.post("/generate", { source_text: text, config });
  return data;
}

export async function submitFeedback(message: string, email?: string, context?: string) {
  const { data } = await api.post("/feedback", { message, email, context });
  return data as { status: string };
}

export async function getConfig() {
  const { data } = await api.get("/config");
  return data as { require_user_key: boolean };
}

export async function listPreprocessedBooks() {
  const { data } = await api.get("/books/preprocessed");
  return data;
}

export async function getPreprocessProgress(bookId: string) {
  const { data } = await api.get(`/book/${bookId}/preprocess/progress`);
  return data;
}

export async function generateSimplifiedText(bookId: string, segId: number) {
  const { data } = await api.post(`/book/${bookId}/segment/${segId}/simplify`);
  return data;
}

export async function generateSceneBackground(bookId: string, segId: number) {
  const { data } = await api.post(`/book/${bookId}/segment/${segId}/background`);
  return data;
}

export async function generateSummary(bookId: string, segId: number) {
  const { data } = await api.post(`/book/${bookId}/segment/${segId}/summarize`);
  return data;
}

export async function getSegmentHistory(bookId: string, segId: number) {
  const { data } = await api.get(`/book/${bookId}/segment/${segId}/history`);
  return data;
}

// ── Editor APIs ──

export async function getChapters(bookId: string) {
  const { data } = await api.get(`/book/${bookId}/preprocess/chapters`);
  return data;
}

export async function getCharacters(bookId: string) {
  const { data } = await api.get(`/book/${bookId}/preprocess/characters`);
  return data;
}

export async function getLocations(bookId: string) {
  const { data } = await api.get(`/book/${bookId}/preprocess/locations`);
  return data as { locations: any[]; scene_sheets: Record<string, string> };
}

export async function getCharacterSheetHistory(bookId: string, charName: string) {
  const { data } = await api.get(`/book/${bookId}/preprocess/characters/${encodeURIComponent(charName)}/history`);
  return data as { images: Array<{ url: string; version: string; timestamp: number }> };
}

export async function updateCharacter(
  bookId: string,
  charName: string,
  updates: Record<string, unknown>
) {
  const { data } = await api.put(
    `/book/${bookId}/preprocess/characters/${encodeURIComponent(charName)}`,
    updates
  );
  return data;
}

export async function updateScene(
  bookId: string,
  sceneName: string,
  updates: Record<string, unknown>
) {
  const { data } = await api.put(
    `/book/${bookId}/preprocess/scenes/${encodeURIComponent(sceneName)}`,
    updates
  );
  return data;
}

export async function getChapterSegments(bookId: string, chapterIdx: number) {
  const { data } = await api.get(`/book/${bookId}/preprocess/chapter/${chapterIdx}/segments`);
  return data;
}

export async function updateSegment(bookId: string, segId: number, updates: Record<string, unknown>) {
  const { data } = await api.put(`/book/${bookId}/segment/${segId}`, updates);
  return data;
}

export async function restoreSegmentVersion(bookId: string, segId: number, version: string) {
  const { data } = await api.post(
    `/book/${bookId}/segment/${segId}/restore-version?version=${encodeURIComponent(version)}`
  );
  return data as { status: string; segment_id: number; illustration_url: string };
}

export async function regenerateSegment(bookId: string, segId: number) {
  const { data } = await api.post(`/book/${bookId}/segment/${segId}/regenerate`);
  return data;
}

export async function getRegenStatus(bookId: string, segId: number) {
  const { data } = await api.get(`/book/${bookId}/segment/${segId}/regen-status`);
  return data as { status: string; segment_id?: number; page_number?: number; timestamp?: number };
}

export async function regenerateCharacterSheet(bookId: string, charName: string) {
  const { data } = await api.post(`/book/${bookId}/characters/${encodeURIComponent(charName)}/regenerate`);
  return data;
}

export async function autofillCharacterDetails(bookId: string, charName: string) {
  const { data } = await api.post(`/book/${bookId}/preprocess/characters/${encodeURIComponent(charName)}/autofill`);
  return data as { appearance: string; visual_details: Record<string, string> };
}

export async function getSceneSheetHistory(bookId: string, sceneName: string) {
  const { data } = await api.get(`/book/${bookId}/preprocess/scenes/${encodeURIComponent(sceneName)}/history`);
  return data as { images: Array<{ url: string; version: string; timestamp: number }> };
}

export async function getSpecialPages(bookId: string) {
  const { data } = await api.get(`/book/${bookId}/special-pages`);
  return data as { pages: Array<{ type: string; label: string; url: string | null; chapter?: number; chapter_title?: string; chapter_summary?: string }> };
}

export async function regenerateSpecialPage(bookId: string, pageType: string, chapter: number = 0) {
  const { data } = await api.post(`/book/${bookId}/special/${pageType}/regenerate?chapter=${chapter}`);
  return data;
}

export async function regenerateSceneSheet(bookId: string, sceneName: string) {
  const { data } = await api.post(`/book/${bookId}/scenes/${encodeURIComponent(sceneName)}/regenerate`);
  return data;
}

export async function generateChapter(bookId: string, chapterIdx: number) {
  const { data } = await api.post(`/book/${bookId}/chapter/${chapterIdx}/generate`);
  return data;
}

export async function getChapterProgress(bookId: string, chapterIdx: number) {
  const { data } = await api.get(`/book/${bookId}/chapter/${chapterIdx}/progress`);
  return data;
}

export async function getAgentLog(bookId: string, chapterIdx: number) {
  const { data } = await api.get(`/book/${bookId}/chapter/${chapterIdx}/agent-log`);
  return data as Array<{
    ts: number;
    agent: string;
    action: string;
    detail: string;
    result: string;
    status: string;
  }>;
}

export async function checkSegmentQuality(bookId: string, segId: number) {
  const { data } = await api.post(`/book/${bookId}/segment/${segId}/quality`);
  return data;
}

export async function getChapterConsistency(bookId: string, chapterIdx: number) {
  const { data } = await api.get(`/book/${bookId}/chapter/${chapterIdx}/consistency`);
  return data;
}

export async function getStalePages(bookId: string, chIdx: number) {
  const { data } = await api.get(`/book/${bookId}/chapter/${chIdx}/stale-pages`);
  return data as { stale: Array<{ page: number; segment_id: number; reasons: Array<{ type: "character" | "scene"; name: string }> }> };
}

export async function checkChapterConsistency(bookId: string, chapterIdx: number) {
  const { data } = await api.post(`/book/${bookId}/chapter/${chapterIdx}/consistency`);
  return data;
}

export async function checkCharacterSheetQuality(bookId: string, charName: string) {
  const { data } = await api.post(`/book/${bookId}/characters/${encodeURIComponent(charName)}/quality`);
  return data as {
    overall_score: number;
    character_name: string;
    is_group: boolean;
    appearance_match: { score: number; issues: string[] };
    internal_consistency: { score: number; issues: string[] };
    multi_angle: { score: number; has_front: boolean; has_side: boolean; has_back: boolean; has_expressions: boolean; issues: string[] };
    style_quality: { score: number; issues: string[] };
    text_labels: { score: number; issues: string[] };
    regeneration_feedback: string;
  };
}

export async function chatWithAI(
  bookId: string,
  segId: number,
  message: string,
  history: Array<{ role: string; content: string }>
) {
  const { data } = await api.post(`/book/${bookId}/segment/${segId}/chat`, {
    message,
    history,
  });
  return data as { reply: string; updates: Record<string, unknown> };
}
