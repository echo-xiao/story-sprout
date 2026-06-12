"use client";

import { useState } from "react";
import { UploadForm } from "@/components/UploadForm";
import { GenerationProgress } from "@/components/GenerationProgress";
import { BookLibrary } from "@/components/BookLibrary";

type View = "home" | "generating" | "library";

export default function Home() {
  // Initialize from the URL so a full navigation into "/?view=library" (e.g.
  // the editor's Library link) lands on the Library tab, not always Create.
  const [view, setViewState] = useState<View>(() => {
    if (typeof window === "undefined") return "home";
    return new URLSearchParams(window.location.search).get("view") === "library" ? "library" : "home";
  });
  const [bookId, setBookId] = useState<string>("");

  // Keep the URL in sync when switching tabs (generating is transient, no URL).
  const setView = (v: View) => {
    setViewState(v);
    if (typeof window !== "undefined" && v !== "generating") {
      window.history.replaceState(null, "", v === "library" ? "/?view=library" : "/");
    }
  };

  const handleStartGeneration = (id: string) => {
    setBookId(id);
    setView("generating");
  };

  return (
    <main className="min-h-screen">
      {/* Header */}
      <header className="bg-white/80 backdrop-blur-sm border-b border-peach/30 sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between">
          <button
            onClick={() => setView("home")}
            className="flex items-center gap-3 hover:opacity-80 transition-opacity"
          >
            <img src="/logo.png" alt="StorySprout" className="w-11 h-11 rounded-full object-cover shadow-sm" />
            <div>
              <h1 className="font-display text-xl font-bold text-gray-800">
                StorySprout
              </h1>
              <p className="text-xs text-gray-500">
                Transform any book into a children&apos;s picture book
              </p>
            </div>
          </button>

          <nav className="flex gap-2">
            <button
              onClick={() => setView("home")}
              className={`px-4 py-2 rounded-xl text-sm font-semibold transition-all ${
                view === "home"
                  ? "bg-coral text-white shadow-md"
                  : "text-gray-600 hover:bg-peach/50"
              }`}
            >
              Create
            </button>
            <button
              onClick={() => setView("library")}
              className={`px-4 py-2 rounded-xl text-sm font-semibold transition-all ${
                view === "library"
                  ? "bg-coral text-white shadow-md"
                  : "text-gray-600 hover:bg-peach/50"
              }`}
            >
              Library
            </button>
          </nav>
        </div>
      </header>

      {/* Content */}
      <div className="page-container">
        {view === "home" && (
          <div className="relative">
            <div
              aria-hidden
              className="pointer-events-none absolute inset-0 -z-10 bg-cover bg-bottom bg-no-repeat opacity-35"
              style={{ backgroundImage: "url('/create-bg.png')" }}
            />
            <UploadForm onStartGeneration={handleStartGeneration} />
          </div>
        )}
        {view === "generating" && (
          <GenerationProgress bookId={bookId} onBack={() => setView("home")} />
        )}
        {view === "library" && <BookLibrary />}
      </div>
    </main>
  );
}
