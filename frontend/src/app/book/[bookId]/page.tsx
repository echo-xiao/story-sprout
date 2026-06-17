"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";

import { ChevronLeft, ChevronRight, Edit3, Download } from "lucide-react";
import { getChapters, getChapterSegments } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

interface PageInfo {
  page_number: number;
  chapter_idx: number;
  segment_id: number;
  text: string;
  image_url: string;
}

export default function BookViewerPage() {
  const params = useParams();
  const router = useRouter();
  const bookId = params.bookId as string;

  const [title, setTitle] = useState("");
  const [pages, setPages] = useState<PageInfo[]>([]);
  const [currentPage, setCurrentPage] = useState(0);
  // "flip" = page-by-page image viewer; "pdf" = whole-book PDF embedded in an
  // iframe (browser-native viewer, continuous scroll) — the "show as PDF" mode.
  const [viewMode, setViewMode] = useState<"flip" | "pdf">("flip");
  const [loading, setLoading] = useState(true);
  // Bust the image cache once per page-load so edits made in the editor show up
  // here instead of a stale browser-cached image (filenames are reused).
  const [cacheBust] = useState(() => Date.now());

  useEffect(() => {
    async function load() {
      try {
        // Load chapters
        const chapData = await getChapters(bookId);
        setTitle(chapData.meta?.title || bookId);
        const chapterMap = chapData.chapters || {};

        // Fetch every chapter's segments in parallel (sequential fetches made
        // opening a 9-chapter book take 9 round-trips), then assemble in
        // chapter order.
        const chapterIndices = Object.keys(chapterMap).map(Number).sort((a, b) => a - b);
        const results = await Promise.all(
          chapterIndices.map(async (chIdx) => {
            try {
              const segData = await getChapterSegments(bookId, chIdx);
              return { chIdx, segments: segData.segments || [] };
            } catch {
              return { chIdx, segments: [] };
            }
          })
        );

        const allPages: PageInfo[] = [];
        for (const { chIdx, segments } of results) {
          segments.forEach((seg: any) => {
            if (seg.illustration_url) {
              allPages.push({
                page_number: allPages.length + 1,
                chapter_idx: chIdx,
                segment_id: seg.id,
                text: seg.simplified_text || seg.scene_summary || "",
                image_url: seg.illustration_url,
              });
            }
          });
        }
        setPages(allPages);
      } catch (e) {
        console.error("Failed to load book:", e);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [bookId]);

  // Keyboard navigation
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight" || e.key === " ") {
        e.preventDefault(); // Space would otherwise also scroll the page
        setCurrentPage((p) => Math.min(p + 1, pages.length - 1));
      } else if (e.key === "ArrowLeft") {
        setCurrentPage((p) => Math.max(p - 1, 0));
      } else if (e.key === "Escape") {
        router.push("/");
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [pages.length, router]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-900">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-white mx-auto mb-4" />
          <p className="text-white/70">Loading book...</p>
        </div>
      </div>
    );
  }

  if (pages.length === 0) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-900">
        <div className="text-center">
          <p className="text-6xl mb-4">📖</p>
          <h2 className="text-white text-xl font-bold mb-2">No illustrations yet</h2>
          <p className="text-white/50 mb-6">Generate chapters in the editor first.</p>
          <a
            href={`/editor/${bookId}`}
            className="px-6 py-3 bg-coral text-white rounded-xl font-semibold hover:bg-coral/80 transition-colors"
          >
            Open Editor
          </a>
        </div>
      </div>
    );
  }

  const page = pages[currentPage];

  return (
    <div className="min-h-screen bg-gray-900 flex flex-col">
      {/* Top bar */}
      <header className="bg-black/30 px-4 py-2 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <a href="/?view=library" className="text-white/70 hover:text-white text-sm">&larr; Library</a>
          <h1 className="text-white font-bold text-sm truncate max-w-xs">{title}</h1>
        </div>
        <div className="flex items-center gap-3">
          {viewMode === "flip" && (
            <span className="text-white/50 text-xs">
              {currentPage + 1} / {pages.length}
            </span>
          )}
          {/* View switch: page-by-page flip vs whole-book PDF */}
          <div className="flex rounded-lg overflow-hidden border border-white/10">
            <button
              onClick={() => setViewMode("flip")}
              className={`text-xs px-3 py-1.5 transition-colors ${viewMode === "flip" ? "bg-white/20 text-white" : "bg-white/5 text-white/50 hover:text-white"}`}
            >
              翻页
            </button>
            <button
              onClick={() => setViewMode("pdf")}
              className={`text-xs px-3 py-1.5 transition-colors ${viewMode === "pdf" ? "bg-white/20 text-white" : "bg-white/5 text-white/50 hover:text-white"}`}
            >
              整本 PDF
            </button>
          </div>
          {/* Built on demand from chapter_data — always current. */}
          <a
            href={`${API_BASE}/api/book/${bookId}/pdf`}
            className="text-xs bg-white/10 hover:bg-white/20 text-white px-3 py-1.5 rounded-lg transition-colors flex items-center gap-1"
          >
            <Download size={12} /> PDF
          </a>
          {/* Full navigation: the editor reads window.location.search in useState
              initializers, which a soft router.push would leave stale. */}
          <button
            onClick={() => window.location.assign(`/editor/${bookId}?ch=${page.chapter_idx}&seg=${page.segment_id}&tab=pages`)}
            className="text-xs bg-white/10 hover:bg-white/20 text-white px-3 py-1.5 rounded-lg transition-colors flex items-center gap-1"
          >
            <Edit3 size={12} /> Edit
          </button>
        </div>
      </header>

      {/* Whole-book PDF view: browser-native viewer, continuous scroll. Only
          mounted in pdf mode so flip mode doesn't prefetch the (heavy) PDF. */}
      {viewMode === "pdf" && (
        <iframe
          src={`${API_BASE}/api/book/${bookId}/pdf?inline=1`}
          className="flex-1 w-full border-0 bg-white"
          title={`${title} — full book PDF`}
        />
      )}

      {/* Page viewer */}
      <div className={`flex-1 flex items-center justify-center relative px-4 py-4 ${viewMode === "pdf" ? "hidden" : ""}`}>
        {/* Previous button */}
        <button
          onClick={() => setCurrentPage((p) => Math.max(p - 1, 0))}
          disabled={currentPage === 0}
          className="absolute left-4 z-10 w-12 h-12 rounded-full bg-white/10 hover:bg-white/20 flex items-center justify-center text-white disabled:opacity-20 transition-all"
        >
          <ChevronLeft size={24} />
        </button>

        {/* Page content */}
        <div
          className="max-w-2xl w-full cursor-pointer group"
          onClick={() => {
            // Click to go to editor for this specific segment (full navigation —
            // the editor reads window.location.search in useState initializers)
            window.location.assign(`/editor/${bookId}?ch=${page.chapter_idx}&seg=${page.segment_id}&tab=pages`);
          }}
          title="Click to edit this page"
        >
          <div className="relative">
            <img
              src={`${API_BASE}${page.image_url}${page.image_url.includes("?") ? "&" : "?"}v=${cacheBust}`}
              alt={`Page ${currentPage + 1}`}
              className="w-full rounded-2xl shadow-2xl"
            />
            {/* Edit overlay on hover */}
            <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 rounded-2xl transition-all flex items-center justify-center">
              <span className="opacity-0 group-hover:opacity-100 bg-white/90 text-gray-800 px-4 py-2 rounded-xl text-sm font-semibold transition-opacity flex items-center gap-1.5">
                <Edit3 size={14} /> Edit in Editor
              </span>
            </div>
          </div>
          {/* Text below image */}
          {page.text && (
            <p className="text-white/70 text-center text-sm mt-4 max-w-lg mx-auto leading-relaxed">
              {page.text}
            </p>
          )}
        </div>

        {/* Next button */}
        <button
          onClick={() => setCurrentPage((p) => Math.min(p + 1, pages.length - 1))}
          disabled={currentPage >= pages.length - 1}
          className="absolute right-4 z-10 w-12 h-12 rounded-full bg-white/10 hover:bg-white/20 flex items-center justify-center text-white disabled:opacity-20 transition-all"
        >
          <ChevronRight size={24} />
        </button>
      </div>

      {/* Page thumbnails */}
      <div className={`bg-black/30 px-4 py-3 shrink-0 ${viewMode === "pdf" ? "hidden" : ""}`}>
        <div className="flex gap-2 overflow-x-auto justify-center max-w-4xl mx-auto">
          {pages.map((p, idx) => (
            <button
              key={idx}
              onClick={() => setCurrentPage(idx)}
              className={`shrink-0 w-12 h-12 rounded-lg overflow-hidden border-2 transition-all ${
                idx === currentPage ? "border-coral scale-110" : "border-transparent opacity-60 hover:opacity-100"
              }`}
            >
              <img
                src={`${API_BASE}${p.image_url}${p.image_url.includes("?") ? "&" : "?"}v=${cacheBust}`}
                alt={`Page ${idx + 1}`}
                className="w-full h-full object-cover"
              />
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
