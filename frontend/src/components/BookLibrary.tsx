"use client";

import { useState, useEffect } from "react";
import { listBooks, deleteBook } from "@/lib/api";
import type { PictureBook } from "@/types";

interface Props {
  onSelectBook: (book: PictureBook) => void;
}

export function BookLibrary({ onSelectBook }: Props) {
  const [books, setBooks] = useState<PictureBook[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadBooks();
  }, []);

  const loadBooks = async () => {
    try {
      const data = await listBooks();
      setBooks(data);
    } catch {
      // API might not be available
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (bookId: string) => {
    if (!confirm("Delete this picture book?")) return;
    try {
      await deleteBook(bookId);
      setBooks((prev) => prev.filter((b) => b.book_id !== bookId));
    } catch {
      // ignore
    }
  };

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
          Create your first picture book to see it here!
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
          <div
            key={book.book_id}
            className="card group hover:shadow-xl transition-all cursor-pointer"
            onClick={() => onSelectBook(book)}
          >
            {/* Cover preview */}
            <div className="aspect-square rounded-2xl overflow-hidden mb-4 bg-gradient-to-br from-peach/50 to-lavender/50">
              {book.pages[0]?.illustration_url ? (
                <img
                  src={book.pages[0].illustration_url}
                  alt={book.title}
                  className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center">
                  <span className="text-5xl">📖</span>
                </div>
              )}
            </div>

            <h3 className="font-display font-bold text-gray-800 truncate">
              {book.title}
            </h3>
            <div className="flex items-center justify-between mt-2">
              <span className="text-xs text-gray-400">
                {book.pages.length} pages | Ages {book.config.age_group}
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  handleDelete(book.book_id);
                }}
                className="text-xs text-red-400 hover:text-red-600 opacity-0 group-hover:opacity-100 transition-opacity"
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
