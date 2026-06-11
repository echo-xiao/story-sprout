"use client";

import { useState } from "react";
import { UploadForm } from "@/components/UploadForm";
import { GenerationProgress } from "@/components/GenerationProgress";
import { BookReader } from "@/components/BookReader";
import { BookLibrary } from "@/components/BookLibrary";
import type { PictureBook, GenerationConfig } from "@/types";

type View = "home" | "generating" | "reading" | "library";

export default function Home() {
  const [view, setView] = useState<View>("home");
  const [bookId, setBookId] = useState<string>("");
  const [book, setBook] = useState<PictureBook | null>(null);

  const handleStartGeneration = (id: string) => {
    setBookId(id);
    setView("generating");
  };

  const handleGenerationComplete = (completedBook: PictureBook) => {
    setBook(completedBook);
    setView("reading");
  };

  const handleSelectBook = (selectedBook: PictureBook) => {
    setBook(selectedBook);
    setView("reading");
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
              className="px-4 py-2 rounded-xl text-sm font-semibold text-gray-600 hover:bg-peach/50 transition-all"
            >
              Editor
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
          <GenerationProgress
            bookId={bookId}
            onComplete={handleGenerationComplete}
            onBack={() => setView("home")}
          />
        )}
        {view === "reading" && book && (
          <BookReader book={book} onBack={() => setView("home")} />
        )}
        {view === "library" && (
          <BookLibrary onSelectBook={handleSelectBook} />
        )}
      </div>
    </main>
  );
}
