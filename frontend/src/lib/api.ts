import axios from "axios";
import type { GenerationConfig, GenerationStatus, PictureBook } from "@/types";

const api = axios.create({
  baseURL: "/api",
  timeout: 300000,
});

export async function startGeneration(
  text: string,
  config: GenerationConfig
): Promise<{ book_id: string }> {
  const { data } = await api.post("/generate", { source_text: text, config });
  return data;
}

export async function uploadAndGenerate(
  file: File,
  config: GenerationConfig
): Promise<{ book_id: string }> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("config", JSON.stringify(config));
  const { data } = await api.post("/generate/upload", formData);
  return data;
}

export async function getStatus(bookId: string): Promise<GenerationStatus> {
  const { data } = await api.get(`/status/${bookId}`);
  return data;
}

export async function getBook(bookId: string): Promise<PictureBook> {
  const { data } = await api.get(`/book/${bookId}`);
  return data;
}

export async function getBookHtml(bookId: string): Promise<string> {
  const { data } = await api.get(`/book/${bookId}/html`);
  return data;
}

export async function listBooks(): Promise<PictureBook[]> {
  const { data } = await api.get("/books");
  return data;
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

export async function deleteBook(bookId: string): Promise<void> {
  await api.delete(`/book/${bookId}`);
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

export async function getChapterSegments(bookId: string, chapterIdx: number) {
  const { data } = await api.get(`/book/${bookId}/preprocess/chapter/${chapterIdx}/segments`);
  return data;
}

export async function updateSegment(bookId: string, segId: number, updates: Record<string, unknown>) {
  const { data } = await api.put(`/book/${bookId}/segment/${segId}`, updates);
  return data;
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

export async function getSceneSheetHistory(bookId: string, sceneName: string) {
  const { data } = await api.get(`/book/${bookId}/preprocess/scenes/${encodeURIComponent(sceneName)}/history`);
  return data as { images: Array<{ url: string; version: string; timestamp: number }> };
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

export async function checkSegmentQuality(bookId: string, segId: number) {
  const { data } = await api.post(`/book/${bookId}/segment/${segId}/quality`);
  return data;
}

export async function getChapterConsistency(bookId: string, chapterIdx: number) {
  const { data } = await api.get(`/book/${bookId}/chapter/${chapterIdx}/consistency`);
  return data;
}

export async function checkChapterConsistency(bookId: string, chapterIdx: number) {
  const { data } = await api.post(`/book/${bookId}/chapter/${chapterIdx}/consistency`);
  return data;
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
