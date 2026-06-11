"use client";

import { useState, useEffect } from "react";
import { listPreprocessedBooks } from "@/lib/api";

interface BookEntry {
  book_id: string;
  title: string;
  num_chapters: number;
  num_characters: number;
  generated_chapters: number;
  total_pages: number;
}

export function BookLibrary() {
  const [books, setBooks] = useState<BookEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const data = await listPreprocessedBooks();
        setBooks(data);
      } catch {
        // API might not be available
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) {
    return (
      <div className="text-center py-20">
        <span className="text-4xl animate-float inline-block">📚</span>
        <p className="text-gray-500 mt-4">Loading library...</p>
      </div>
    );
  }

  if (books.length === 0) {
    return (
      <div className="text-center py-20 animate-fade-in">
        <span className="text-6xl">📚</span>
        <h2 className="font-display text-2xl font-bold mt-4 text-gray-800">
          No books yet
        </h2>
        <p className="text-gray-500 mt-2">
          Upload a book to get started!
        </p>
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      <h2 className="font-display text-2xl font-bold text-gray-800 mb-6">
        Your Library
      </h2>

      <div className="grid sm:grid-cols-2 md:grid-cols-3 gap-6">
        {books.map((book) => (
          // A <button>/<a> can't legally nest inside an <a>, so the card is a
          // div with a stretched primary link and the View Book link on top.
          <div
            key={book.book_id}
            className="card group hover:shadow-xl transition-all relative"
          >
            <a
              href={`/editor/${book.book_id}`}
              className="absolute inset-0 rounded-2xl"
              aria-label={`Open ${book.title} in editor`}
            />
            {/* Cover */}
            <div className="aspect-[4/3] rounded-2xl overflow-hidden mb-4 bg-gradient-to-br from-peach/50 to-lavender/50 flex items-center justify-center">
              <span className="text-5xl group-hover:scale-110 transition-transform">📖</span>
            </div>

            <h3 className="font-display font-bold text-gray-800 truncate">
              {book.title}
            </h3>

            <div className="mt-2 space-y-1">
              <div className="flex items-center justify-between text-xs text-gray-500">
                <span>{book.num_chapters} chapters</span>
                <span>{book.num_characters} characters</span>
              </div>
              {book.generated_chapters > 0 && (
                <div className="flex items-center justify-between text-xs">
                  <span className="text-coral font-semibold">
                    {book.generated_chapters} chapters illustrated
                  </span>
                  <span className="text-gray-400">
                    {book.total_pages} pages
                  </span>
                </div>
              )}
            </div>

            <div className="mt-3 flex gap-2">
              <span className="text-xs bg-sage/30 text-gray-600 px-2 py-1 rounded-lg">
                Editor
              </span>
              {book.generated_chapters > 0 && (
                <a
                  href={`/book/${book.book_id}`}
                  className="relative z-10 text-xs bg-coral text-white px-2 py-1 rounded-lg hover:bg-coral/80 transition-colors"
                >
                  View Book
                </a>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
