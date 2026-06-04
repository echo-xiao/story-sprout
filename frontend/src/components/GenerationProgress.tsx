"use client";

import { useState, useEffect, useCallback } from "react";
import { getStatus, getBook } from "@/lib/api";
import type { PictureBook, GenerationStatus } from "@/types";

interface Props {
  bookId: string;
  onComplete: (book: PictureBook) => void;
  onBack: () => void;
}

const STEP_INFO: Record<string, { label: string; icon: string; detail: string }> = {
  queued: {
    label: "Queued",
    icon: "🕐",
    detail: "Your book is in the queue...",
  },
  analyzing: {
    label: "Analyzing Story",
    icon: "🔍",
    detail: "Reading the story, identifying characters, mapping emotional arcs, and extracting key events...",
  },
  generating_text: {
    label: "Writing Picture Book",
    icon: "✍️",
    detail: "Selecting the best scenes, simplifying language for children, and crafting each page...",
  },
  generating_images: {
    label: "Creating Illustrations",
    icon: "🎨",
    detail: "Designing characters, painting illustrations, and ensuring visual consistency...",
  },
  qa_check: {
    label: "Quality Check",
    icon: "✅",
    detail: "Checking content safety, readability, story coverage, and accuracy...",
  },
  complete: {
    label: "Complete!",
    icon: "🎉",
    detail: "Your picture book is ready!",
  },
  failed: {
    label: "Failed",
    icon: "❌",
    detail: "Something went wrong.",
  },
};

const STEPS_ORDER = [
  "analyzing",
  "generating_text",
  "generating_images",
  "qa_check",
  "complete",
];

export function GenerationProgress({ bookId, onComplete, onBack }: Props) {
  const [status, setStatus] = useState<GenerationStatus | null>(null);
  const [error, setError] = useState("");

  const pollStatus = useCallback(async () => {
    try {
      const s = await getStatus(bookId);
      setStatus(s);

      if (s.status === "complete") {
        const book = await getBook(bookId);
        onComplete(book);
      } else if (s.status === "failed") {
        setError(s.error || "Generation failed");
      }
    } catch {
      setError("Failed to check status");
    }
  }, [bookId, onComplete]);

  useEffect(() => {
    pollStatus();
    const interval = setInterval(pollStatus, 2000);
    return () => clearInterval(interval);
  }, [pollStatus]);

  const currentStep = status?.status || "queued";
  const info = STEP_INFO[currentStep] || STEP_INFO.queued;
  const progress = status?.progress || 0;
  const currentStepIdx = STEPS_ORDER.indexOf(currentStep);

  return (
    <div className="animate-fade-in max-w-2xl mx-auto py-12">
      <div className="card text-center space-y-8">
        {/* Main icon */}
        <div className="animate-float">
          <span className="text-6xl">{info.icon}</span>
        </div>

        {/* Status */}
        <div>
          <h2 className="font-display text-2xl font-bold text-gray-800">
            {info.label}
          </h2>
          <p className="text-gray-500 mt-2">{info.detail}</p>
          {status?.current_step && (
            <p className="text-sm text-coral mt-1 font-semibold">
              {status.current_step}
            </p>
          )}
        </div>

        {/* Progress bar */}
        <div className="w-full">
          <div className="w-full h-4 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="h-full progress-bar rounded-full transition-all duration-700 ease-out"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className="text-sm text-gray-400 mt-2">{Math.round(progress)}%</p>
        </div>

        {/* Step indicators */}
        <div className="flex justify-between px-4">
          {STEPS_ORDER.slice(0, -1).map((step, idx) => {
            const stepInfo = STEP_INFO[step];
            const isActive = idx === currentStepIdx;
            const isDone = idx < currentStepIdx;

            return (
              <div
                key={step}
                className={`flex flex-col items-center gap-1 transition-all ${
                  isActive
                    ? "scale-110"
                    : isDone
                    ? "opacity-60"
                    : "opacity-30"
                }`}
              >
                <div
                  className={`w-8 h-8 rounded-full flex items-center justify-center text-sm ${
                    isDone
                      ? "bg-sage text-gray-800"
                      : isActive
                      ? "bg-coral text-white"
                      : "bg-gray-200 text-gray-400"
                  }`}
                >
                  {isDone ? "✓" : idx + 1}
                </div>
                <span className="text-xs text-gray-500 hidden sm:block">
                  {stepInfo.label}
                </span>
              </div>
            );
          })}
        </div>

        {/* Error */}
        {error && (
          <div className="bg-red-50 text-red-600 p-4 rounded-xl">
            <p className="font-semibold">Error</p>
            <p className="text-sm mt-1">{error}</p>
            <button onClick={onBack} className="btn-secondary mt-3 text-sm">
              Go Back
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
