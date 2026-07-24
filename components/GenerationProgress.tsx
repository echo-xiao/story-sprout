"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { getChapters, getPreprocessProgress } from "@/lib/api";
import { AGENT_META, PREPROCESS_STEPS as STEPS } from "@/lib/agents";

interface Props {
  bookId: string;
  onBack: () => void;
}

export function GenerationProgress({ bookId, onBack }: Props) {
  const router = useRouter();
  const [progress, setProgress] = useState(0);
  const [loadingStatus, setLoadingStatus] = useState("Starting...");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preprocessProgress, setPreprocessProgress] = useState<any>(null);

  // Poll for preprocess progress + completion
  useEffect(() => {
    let timer: NodeJS.Timeout;
    let redirectTimer: NodeJS.Timeout;
    let cancelled = false;

    async function poll() {
      // Check progress endpoint
      try {
        const prog = await getPreprocessProgress(bookId);
        if (cancelled) return;
        if (prog.status === "error") {
          // Fatal preprocess error — stop polling, don't redirect
          setError(prog.error || prog.step || "Preprocess failed");
          return;
        }
        setPreprocessProgress(prog);
        setProgress(prog.progress || 0);
        setLoadingStatus(prog.step || "Processing...");
      } catch {}
      if (cancelled) return;

      // Check completion
      try {
        const data = await getChapters(bookId);
        if (cancelled) return;
        if (data.chapters && Object.keys(data.chapters).length > 0) {
          setDone(true);
          setProgress(100);
          redirectTimer = setTimeout(() => {
            router.push(`/editor/${bookId}`);
          }, 1500);
          return;
        }
      } catch {}
      if (cancelled) return;

      timer = setTimeout(poll, 3000);
    }

    timer = setTimeout(poll, 3000);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      clearTimeout(redirectTimer);
    };
  }, [bookId, router]);

  const stepsDone = new Set(preprocessProgress?.steps_done || []);
  const currentAgent = preprocessProgress?.agent ? AGENT_META[preprocessProgress.agent] : null;

  if (error) {
    return (
      <div className="min-h-[60vh] flex items-center justify-center">
        <div className="text-center max-w-md w-full px-4">
          <p className="text-5xl mb-4">⚠️</p>
          <p className="text-gray-700 font-semibold mb-2">Preprocessing Failed</p>
          <p className="text-red-500 text-sm mb-4">{error}</p>
          <button onClick={onBack} className="text-sm text-gray-400 hover:text-gray-600 mt-4">
            Back
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-[60vh] flex items-center justify-center">
      <div className="text-center max-w-md w-full px-4">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-coral mx-auto mb-4" />
        <p className="text-gray-700 font-semibold mb-2">
          {done ? "Preprocessing Complete!" : "Preprocessing Book..."}
        </p>
        {/* Active Agent Badge */}
        {!done && currentAgent && (
          <div className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-white shadow-sm border border-gray-100 mb-2 ${currentAgent.color}`}>
            <span className="text-sm">{currentAgent.icon}</span>
            <span className="text-xs font-semibold">{currentAgent.fullName}</span>
          </div>
        )}
        <p className="text-gray-500 text-sm mb-2">
          {done ? "Redirecting to editor..." : loadingStatus}
        </p>

        {/* Progress bar */}
        <div className="w-full h-3 bg-gray-200 rounded-full overflow-hidden mb-2">
          <div
            className="h-full bg-gradient-to-r from-coral to-sunshine rounded-full transition-all duration-700"
            style={{ width: `${progress}%` }}
          />
        </div>
        <p className="text-sm text-gray-400 mb-4">{Math.round(progress)}%</p>

        {/* Steps with agent labels */}
        <div className="bg-white rounded-xl p-4 text-left text-xs space-y-2">
          {STEPS.map((s, idx) => {
            const isDone = stepsDone.has(s.key);
            const isCurrent = !isDone && idx === STEPS.findIndex(st => !stepsDone.has(st.key));
            const agentInfo = AGENT_META[s.agent];
            return (
              <div key={s.key} className={`flex items-center gap-2 ${
                isDone ? "text-gray-400" : isCurrent ? "text-coral font-semibold" : "text-gray-300"
              }`}>
                <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] ${
                  isDone ? "bg-sage text-white" : isCurrent ? "bg-coral text-white animate-pulse" : "bg-gray-200"
                }`}>
                  {isDone ? "\u2713" : idx + 1}
                </span>
                <span className="text-sm">{agentInfo?.icon}</span>
                {s.label}
              </div>
            );
          })}
        </div>

        <button onClick={onBack} className="text-sm text-gray-400 hover:text-gray-600 mt-4">
          Cancel
        </button>
      </div>
    </div>
  );
}
