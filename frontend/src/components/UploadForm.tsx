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
  const [inputMode, setInputMode] = useState<"text" | "file">("text");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
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
      if (inputMode === "file" && file) {
        result = await uploadAndGenerate(file, finalConfig);
      } else if (inputMode === "text" && text.trim()) {
        result = await startGeneration(text, finalConfig);
      } else {
        setError("Please provide text or upload a file.");
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

      <div className="grid md:grid-cols-3 gap-6">
        {/* Input Panel */}
        <div className="md:col-span-2 card">
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
          </div>

          {inputMode === "text" ? (
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
        </div>

        {/* Config Panel */}
        <div className="card space-y-5">
          <h3 className="font-display text-lg font-bold text-gray-800">
            Settings
          </h3>

          {/* Age Group */}
          <div>
            <label className="block text-sm font-semibold text-gray-600 mb-2">
              Age Group
            </label>
            <div className="grid grid-cols-3 gap-2">
              {(["2-4", "4-6", "6-8"] as const).map((age) => (
                <button
                  key={age}
                  onClick={() => setConfig({ ...config, age_group: age })}
                  className={`py-2 rounded-xl text-sm font-bold transition-all ${
                    config.age_group === age
                      ? "bg-sky text-gray-800 shadow-sm"
                      : "bg-gray-100 text-gray-500 hover:bg-gray-200"
                  }`}
                >
                  {age}
                </button>
              ))}
            </div>
          </div>

          {/* Pages */}
          <div>
            <label className="block text-sm font-semibold text-gray-600 mb-2">
              Pages: {config.num_pages}
            </label>
            <input
              type="range"
              min={6}
              max={20}
              value={config.num_pages}
              onChange={(e) =>
                setConfig({ ...config, num_pages: parseInt(e.target.value) })
              }
              className="w-full accent-coral"
            />
            <div className="flex justify-between text-xs text-gray-400">
              <span>6</span>
              <span>20</span>
            </div>
          </div>

          {/* Template */}
          <div>
            <label className="block text-sm font-semibold text-gray-600 mb-2">
              Story Structure
            </label>
            <select
              value={config.template}
              onChange={(e) =>
                setConfig({
                  ...config,
                  template: e.target.value as GenerationConfig["template"],
                })
              }
              className="w-full p-2 border border-peach/50 rounded-xl bg-white
                         focus:outline-none focus:ring-2 focus:ring-coral/30 text-sm"
            >
              <option value="classic">Classic (Problem-Solution)</option>
              <option value="journey">Journey (Adventure)</option>
              <option value="simple">Simple (Sequential)</option>
            </select>
          </div>

          {/* Education Goal */}
          <div>
            <label className="block text-sm font-semibold text-gray-600 mb-2">
              Education Goal (optional)
            </label>
            <input
              type="text"
              value={educationGoal}
              onChange={(e) => setEducationGoal(e.target.value)}
              placeholder="e.g., learning to share"
              className="w-full p-2 border border-peach/50 rounded-xl bg-white
                         focus:outline-none focus:ring-2 focus:ring-coral/30 text-sm"
            />
          </div>

          {/* Submit */}
          <button
            onClick={handleSubmit}
            disabled={loading}
            className={`w-full btn-primary ${
              loading ? "opacity-50 cursor-not-allowed" : ""
            }`}
          >
            {loading ? "Starting..." : "Generate Picture Book"}
          </button>

          {error && (
            <p className="text-red-500 text-sm text-center">{error}</p>
          )}
        </div>
      </div>

      {/* Features */}
      <div className="grid md:grid-cols-3 gap-6 pt-8">
        {[
          {
            icon: "🔍",
            title: "Smart Analysis",
            desc: "NLP-powered character detection, sentiment analysis, and story structure identification",
          },
          {
            icon: "🎨",
            title: "AI Illustrations",
            desc: "Beautiful watercolor-style illustrations with consistent characters across all pages",
          },
          {
            icon: "📚",
            title: "Age-Appropriate",
            desc: "Language complexity automatically adjusted for your child's age group",
          },
        ].map((feature) => (
          <div key={feature.title} className="card text-center">
            <span className="text-3xl">{feature.icon}</span>
            <h3 className="font-display text-lg font-bold mt-2">
              {feature.title}
            </h3>
            <p className="text-sm text-gray-500 mt-1">{feature.desc}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
