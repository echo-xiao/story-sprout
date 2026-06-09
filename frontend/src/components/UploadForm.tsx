"use client";

import { useState, useRef } from "react";
import { startGeneration, uploadAndGenerate } from "@/lib/api";
import type { GenerationConfig } from "@/types";

interface Props {
  onStartGeneration: (bookId: string) => void;
}

const SAMPLE_TEXT = `Once upon a time, in a small house at the edge of a great forest, there lived a little rabbit named Rosie. Rosie had soft brown fur, big curious eyes, and the longest ears in the whole meadow.

Every morning, Rosie would hop to the garden to pick carrots for breakfast. But one day, she noticed something strange — all the carrots had disappeared! "Oh no!" cried Rosie. "Who took my carrots?"

She asked her friend Oliver the owl, who was sleeping in the old oak tree. "Whooo would take your carrots?" Oliver yawned. "Maybe follow the tracks and see."

Rosie looked down and saw tiny footprints leading into the forest. She was a little scared, but she was also very brave. She hopped along the trail, deeper and deeper into the woods.

The footprints led to a small burrow under a bush. Rosie peeked inside and found a tiny hedgehog, surrounded by all her carrots! The hedgehog looked up with big teary eyes. "I'm sorry," she sniffled. "I was so hungry and I couldn't find any food."

Rosie's heart melted. "Don't cry! My name is Rosie. What's yours?" "I'm Hazel," said the little hedgehog. "I just moved here and I don't know where to find food."

Rosie smiled her warmest smile. "Well, Hazel, you don't need to steal! I'll share my garden with you. That's what friends do!" And from that day on, Rosie and Hazel tended the garden together, and they always had plenty to eat.

The End.`;

export function UploadForm({ onStartGeneration }: Props) {
  const [inputMode, setInputMode] = useState<"text" | "file" | "url">("text");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [config, setConfig] = useState<GenerationConfig>({
    age_group: "4-6",
    num_pages: 10,
    template: "classic",
  });
  const [educationGoal, setEducationGoal] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const handleSubmit = async () => {
    setError("");
    setLoading(true);

    try {
      const finalConfig = {
        ...config,
        ...(educationGoal ? { education_goal: educationGoal } : {}),
      };

      let result;
      if (inputMode === "url" && url.trim()) {
        // Fetch text from URL via backend
        const { fetchBookFromUrl } = await import("@/lib/api");
        const fetched = await fetchBookFromUrl(url.trim());
        result = await startGeneration(fetched.text, finalConfig);
      } else if (inputMode === "file" && file) {
        result = await uploadAndGenerate(file, finalConfig);
      } else if (inputMode === "text" && text.trim()) {
        result = await startGeneration(text, finalConfig);
      } else {
        setError("Please provide text, a file, or a URL.");
        setLoading(false);
        return;
      }

      onStartGeneration(result.book_id);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to start generation";
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
          Paste a story, upload a book, or use our sample text.
          Our AI will analyze the story, create beautiful illustrations,
          and produce a complete picture book for your little ones.
        </p>
      </div>

      <div className="max-w-3xl mx-auto">
        {/* Input Panel */}
        <div className="card">
          <div className="flex gap-2 mb-4">
            <button
              onClick={() => setInputMode("text")}
              className={`px-4 py-2 rounded-xl text-sm font-semibold transition-all ${
                inputMode === "text"
                  ? "bg-sage text-gray-800 shadow-sm"
                  : "text-gray-500 hover:bg-gray-100"
              }`}
            >
              Paste Text
            </button>
            <button
              onClick={() => setInputMode("file")}
              className={`px-4 py-2 rounded-xl text-sm font-semibold transition-all ${
                inputMode === "file"
                  ? "bg-sage text-gray-800 shadow-sm"
                  : "text-gray-500 hover:bg-gray-100"
              }`}
            >
              Upload File
            </button>
            <button
              onClick={() => setInputMode("url")}
              className={`px-4 py-2 rounded-xl text-sm font-semibold transition-all ${
                inputMode === "url"
                  ? "bg-sage text-gray-800 shadow-sm"
                  : "text-gray-500 hover:bg-gray-100"
              }`}
            >
              From URL
            </button>
          </div>

          {inputMode === "url" ? (
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
          ) : inputMode === "text" ? (
            <div>
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Paste your story text here..."
                className="w-full h-64 p-4 border border-peach/50 rounded-2xl resize-none
                           focus:outline-none focus:ring-2 focus:ring-coral/30 focus:border-coral
                           font-body text-gray-700 bg-cream/50"
              />
              <div className="flex justify-between items-center mt-2">
                <span className="text-sm text-gray-400">
                  {text.length > 0 ? `${text.split(/\s+/).length} words` : ""}
                </span>
                <button
                  onClick={() => setText(SAMPLE_TEXT)}
                  className="text-sm text-coral hover:text-coral/80 font-semibold"
                >
                  Use sample story
                </button>
              </div>
            </div>
          ) : (
            <div
              className="h-64 border-2 border-dashed border-peach rounded-2xl flex flex-col
                         items-center justify-center cursor-pointer hover:bg-peach/10 transition-colors"
              onClick={() => fileRef.current?.click()}
            >
              <input
                ref={fileRef}
                type="file"
                accept=".txt,.pdf,.epub"
                className="hidden"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
              <span className="text-4xl mb-2">📄</span>
              {file ? (
                <p className="text-gray-700 font-semibold">{file.name}</p>
              ) : (
                <>
                  <p className="text-gray-600 font-semibold">
                    Drop a file here or click to browse
                  </p>
                  <p className="text-sm text-gray-400 mt-1">
                    Supports .txt, .pdf, .epub
                  </p>
                </>
              )}
            </div>
          )}
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
