"use client";

import { useState } from "react";
import { startGeneration, fetchBookFromUrl } from "@/lib/api";

interface Props {
  onStartGeneration: (bookId: string) => void;
}

export function UploadForm({ onStartGeneration }: Props) {
  const [url, setUrl] = useState("");
  const [email, setEmail] = useState(() => typeof window !== "undefined" ? localStorage.getItem("pbg_email") || "" : "");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async () => {
    setError("");

    if (!url.trim()) {
      setError("Please provide a URL.");
      return;
    }

    // Persist for convenience (only what was provided)
    if (email.trim()) localStorage.setItem("pbg_email", email.trim());

    setLoading(true);

    try {
      const finalConfig = {
        email: email.trim(),
      };

      // Fetch text from URL via backend
      const fetched = await fetchBookFromUrl(url.trim());
      const result = await startGeneration(fetched.text, finalConfig);
      onStartGeneration(result.book_id);
    } catch (err: any) {
      // Surface the backend's detail (e.g. why a URL was rejected) instead of
      // axios's generic "Request failed with status code 400".
      const message =
        err?.response?.data?.detail ||
        (err instanceof Error ? err.message : "Failed to start generation");
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="animate-fade-in space-y-8">
      {/* Hero */}
      <div className="text-center py-8">
        <h2 className="font-display text-4xl md:text-5xl font-bold text-gray-800 mb-4">
          Turn Any Story Into a<br />
          <span className="text-coral">Children&apos;s Picture Book</span>
        </h2>
        <p className="text-gray-600 text-lg max-w-2xl mx-auto">
          Paste a link to any classic book (e.g. from Project Gutenberg).
          Our AI will analyze the story, create beautiful illustrations,
          and produce a complete picture book for your little ones.
        </p>
      </div>

      <div className="max-w-3xl mx-auto">
        {/* User Info */}
        <div className="card mb-6">
          <h3 className="font-display font-bold text-gray-700 mb-3">Your Info</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-semibold text-gray-600 mb-1 block">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full p-3 border border-peach/50 rounded-xl
                           focus:outline-none focus:ring-2 focus:ring-coral/30 focus:border-coral
                           font-body text-gray-700 bg-cream/50"
              />
            </div>
          </div>
        </div>

        {/* Input Panel */}
        <div className="card">
          <div className="flex gap-2 mb-4">
            <span className="px-4 py-2 rounded-xl text-sm font-semibold bg-sage text-gray-800 shadow-sm">
              From URL
            </span>
          </div>

          <div>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.gutenberg.org/files/1342/1342-0.txt"
              className="w-full p-4 border border-peach/50 rounded-2xl
                         focus:outline-none focus:ring-2 focus:ring-coral/30 focus:border-coral
                         font-body text-gray-700 bg-cream/50"
            />
            <p className="text-sm text-gray-400 mt-2">
              Paste a URL to a plain text file (e.g. from Project Gutenberg)
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button onClick={() => setUrl("https://www.gutenberg.org/cache/epub/1342/pg1342.txt")} className="text-xs bg-peach/30 px-3 py-1.5 rounded-lg hover:bg-peach/50 text-gray-700">Pride & Prejudice</button>
              <button onClick={() => setUrl("https://www.gutenberg.org/cache/epub/64317/pg64317.txt")} className="text-xs bg-peach/30 px-3 py-1.5 rounded-lg hover:bg-peach/50 text-gray-700">The Great Gatsby</button>
              <button onClick={() => setUrl("https://www.gutenberg.org/cache/epub/84/pg84.txt")} className="text-xs bg-peach/30 px-3 py-1.5 rounded-lg hover:bg-peach/50 text-gray-700">Frankenstein</button>
              <button onClick={() => setUrl("https://www.gutenberg.org/cache/epub/1661/pg1661.txt")} className="text-xs bg-peach/30 px-3 py-1.5 rounded-lg hover:bg-peach/50 text-gray-700">Sherlock Holmes</button>
              <button onClick={() => setUrl("https://www.gutenberg.org/cache/epub/11/pg11.txt")} className="text-xs bg-peach/30 px-3 py-1.5 rounded-lg hover:bg-peach/50 text-gray-700">Alice in Wonderland</button>
            </div>
          </div>
          {/* Submit */}
          <button
            onClick={handleSubmit}
            disabled={loading}
            className={`w-full btn-primary mt-4 ${
              loading ? "opacity-50 cursor-not-allowed" : ""
            }`}
          >
            {loading ? "Preprocessing... (this takes a few minutes)" : "Generate Picture Book"}
          </button>

          {error && (
            <p className="text-red-500 text-sm text-center mt-2">{error}</p>
          )}
        </div>
      </div>
    </div>
  );
}
