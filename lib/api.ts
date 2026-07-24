import axios from "axios";
import type { GenerationConfig } from "@/types";

const api = axios.create({
  baseURL: "/api",
  timeout: 300000,
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

export async function listPreprocessedBooks() {
  // Public product: everyone sees every book (no per-owner isolation).
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

// Version selection — pick which generated version of an image is used in the
// book/PDF. Pure pointer write (no generation). assetType ∈ page|scene|character.
export type AssetType = "page" | "scene" | "character" | "special";

export async function selectVersion(
  bookId: string, assetType: AssetType, assetKey: string, versionId: string
) {
  const { data } = await api.post(
    `/book/${bookId}/asset/${assetType}/${encodeURIComponent(assetKey)}/select`,
    { version_id: versionId }
  );
  return data as { status: string; version_id: string };
}

// Book-wide style reference — one image every scene/character/page is generated
// to match. Default is the cover; uploading overrides; deleting reverts.
export async function getStyleReference(bookId: string) {
  const { data } = await api.get(`/book/${bookId}/style-reference`);
  return data as { url: string | null; custom: boolean };
}

export async function uploadStyleReference(bookId: string, file: File) {
  const fd = new FormData();
  fd.append("file", file);
  // Do NOT set Content-Type manually — axios/browser must add the multipart
  // boundary itself, or the server can't parse the upload.
  const { data } = await api.post(`/book/${bookId}/style-reference`, fd);
  return data as { status: string; url: string; custom: boolean };
}

export async function deleteStyleReference(bookId: string) {
  const { data } = await api.delete(`/book/${bookId}/style-reference`);
  return data as { status: string };
}

export async function getAssetVersions(
  bookId: string, assetType: AssetType, assetKey: string
) {
  const { data } = await api.get(
    `/book/${bookId}/asset/${assetType}/${encodeURIComponent(assetKey)}/versions`
  );
  return data as {
    versions: Array<{ id: string; url: string; hash: string | null; created_at: string }>;
    selected_version_id: string | null;
  };
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

// Whether the backend still holds an active regen claim for kind/key (kind:
// "character" | "scene" | "special"; key: char name / scene name /
// `${type}:${chapter}`). Used by polls to detect silent failures — on failure
// the backend restores the old image, so the file watch alone can't tell.
export async function getRegenActive(bookId: string, kind: string, key: string) {
  const { data } = await api.get(`/book/${bookId}/regen-active`, { params: { kind, key } });
  return data as { active: boolean; error?: string | null };
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

export type SpecialPageData = {
  type: string; label: string; key?: string; url: string | null;
  chapter?: number; chapter_title?: string; chapter_summary?: string;
  title_text?: string; subtitle_text?: string;
  scene_background?: string; scene_summary?: string;
  characters_in_scene?: string[];
};

export async function getSpecialPages(bookId: string) {
  const { data } = await api.get(`/book/${bookId}/special-pages`);
  return data as { pages: SpecialPageData[] };
}

export async function regenerateSpecialPage(bookId: string, pageType: string, chapter: number = 0) {
  const { data } = await api.post(`/book/${bookId}/special/${pageType}/regenerate?chapter=${chapter}`);
  return data;
}

export async function updateSpecialPage(
  bookId: string, pageType: string, chapter: number, updates: Record<string, unknown>,
) {
  const { data } = await api.put(`/book/${bookId}/special/${pageType}?chapter=${chapter}`, updates);
  return data;
}

export async function getSpecialPageHistory(bookId: string, pageType: string, chapter: number = 0) {
  const { data } = await api.get(`/book/${bookId}/special/${pageType}/history?chapter=${chapter}`);
  return data as { images: Array<{ url: string; version: string; timestamp: number; quality?: any }> };
}

export async function restoreSpecialPageVersion(
  bookId: string, pageType: string, chapter: number, version: string,
) {
  const { data } = await api.post(
    `/book/${bookId}/special/${pageType}/restore-version?chapter=${chapter}&version=${version}`,
  );
  return data as { status: string; url: string };
}

export async function checkSpecialPageQuality(bookId: string, pageType: string, chapter: number = 0) {
  const { data } = await api.post(`/book/${bookId}/special/${pageType}/quality?chapter=${chapter}`);
  return data;
}

export async function regenerateSceneSheet(bookId: string, sceneName: string) {
  const { data } = await api.post(`/book/${bookId}/scenes/${encodeURIComponent(sceneName)}/regenerate`);
  return data;
}

export async function checkSegmentQuality(bookId: string, segId: number) {
  const { data } = await api.post(`/book/${bookId}/segment/${segId}/quality`);
  return data;
}

export async function getStalePages(bookId: string, chIdx: number) {
  const { data } = await api.get(`/book/${bookId}/chapter/${chIdx}/stale-pages`);
  return data as { stale: Array<{ page: number; segment_id: number; reasons: Array<{ type: "character" | "scene"; name: string }> }> };
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
