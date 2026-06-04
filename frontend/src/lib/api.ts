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

export async function deleteBook(bookId: string): Promise<void> {
  await api.delete(`/book/${bookId}`);
}
