import { AGENT_META, PREPROCESS_STEPS } from "@/lib/agents";

interface Props {
  loadingStatus: string;
  preprocessProgress: {
    progress?: number;
    steps_done?: string[];
    annotated_chapters?: number;
    total_chapters?: number;
    agent?: string;
  } | null;
  error?: string | null;
}

export default function PreprocessLoadingScreen({ loadingStatus, preprocessProgress, error }: Props) {
  const progress = preprocessProgress?.progress || 0;
  const stepsDone = new Set(preprocessProgress?.steps_done || []);
  const currentAgent = preprocessProgress?.agent ? AGENT_META[preprocessProgress.agent] : null;

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-cream">
        <div className="text-center max-w-md w-full px-4">
          <p className="text-5xl mb-4">⚠️</p>
          <p className="text-gray-700 font-semibold mb-2">Preprocessing Failed</p>
          <p className="text-red-500 text-sm mb-4">{error}</p>
          <a href="/" className="text-sm text-gray-400 hover:text-gray-600 underline">
            Back to Home
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-cream">
      <div className="text-center max-w-md w-full px-4">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-coral mx-auto mb-4" />
        <p className="text-gray-700 font-semibold mb-2">
          Preprocessing Book...
        </p>
        {currentAgent && (
          <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-white shadow-sm border border-gray-100 mb-2 text-blue-600">
            <span className="text-sm">{currentAgent.icon}</span>
            <span className="text-xs font-semibold">{currentAgent.name} Agent</span>
          </div>
        )}
        <p className="text-gray-500 text-sm mb-2">{loadingStatus}</p>
        {preprocessProgress && (preprocessProgress.annotated_chapters ?? 0) > 0 && (
          <p className="text-coral font-semibold text-sm mb-2">
            {preprocessProgress.annotated_chapters} / {preprocessProgress.total_chapters || "?"} chapters annotated
          </p>
        )}

        {/* Progress bar */}
        <div className="w-full h-3 bg-gray-200 rounded-full overflow-hidden mb-2">
          <div
            className="h-full bg-gradient-to-r from-coral to-sunshine rounded-full transition-all duration-700"
            style={{ width: `${progress}%` }}
          />
        </div>
        <p className="text-sm text-gray-400 mb-4">{progress}%</p>

        {/* Steps with agent labels */}
        <div className="bg-white rounded-xl p-4 text-left text-xs space-y-2">
          {PREPROCESS_STEPS.map((s, idx) => {
            const done = stepsDone.has(s.key);
            const current = !done && idx === PREPROCESS_STEPS.findIndex(st => !stepsDone.has(st.key));
            const agentInfo = AGENT_META[s.agent];
            return (
              <div key={s.key} className={`flex items-center gap-2 ${
                done ? "text-gray-400" : current ? "text-coral font-semibold" : "text-gray-300"
              }`}>
                <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] ${
                  done ? "bg-sage text-white" : current ? "bg-coral text-white animate-pulse" : "bg-gray-200"
                }`}>
                  {done ? "✓" : idx + 1}
                </span>
                <span className="text-sm">{agentInfo?.icon}</span>
                {s.label}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
