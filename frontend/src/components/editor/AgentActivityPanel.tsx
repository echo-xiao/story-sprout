"use client";

import { useState, useEffect, useRef } from "react";
import { X } from "lucide-react";
import { getAgentLog } from "@/lib/api";

interface AgentLogEntry {
  ts: number;
  agent: string;
  action: string;
  detail: string;
  result: string;
  status: string; // running | done | warn | error
}

interface Props {
  bookId: string;
  chapterIdx: number | null;
  isGenerating: boolean;
  currentAgent: string | null;
  onClose: () => void;
}

const AGENTS = [
  { key: "analyzer", icon: "\uD83D\uDD0D", name: "Analyzer", color: "#3B82F6", desc: "Text & NLP analysis" },
  { key: "writer", icon: "\u270D\uFE0F", name: "Writer", color: "#8B5CF6", desc: "Simplify for kids" },
  { key: "artist", icon: "\uD83C\uDFA8", name: "Artist", color: "#EC4899", desc: "Illustrations" },
  { key: "qa", icon: "\u2705", name: "QA", color: "#10B981", desc: "Quality checks" },
] as const;

// Which agents have been active based on log
function getAgentStatuses(logs: AgentLogEntry[], currentAgent: string | null) {
  const seen = new Set<string>();
  const doneAgents = new Set<string>();
  for (const entry of logs) {
    seen.add(entry.agent);
    if (entry.status === "done") doneAgents.add(entry.agent);
  }
  return AGENTS.map((a) => {
    if (a.key === currentAgent) return { ...a, status: "active" as const };
    // An agent is "done" only if it was seen AND its last entry is done AND it's not the current agent
    const agentLogs = logs.filter((l) => l.agent === a.key);
    if (agentLogs.length > 0) {
      const last = agentLogs[agentLogs.length - 1];
      if (last.status === "done") return { ...a, status: "done" as const };
      if (last.status === "error") return { ...a, status: "error" as const };
      if (last.status === "warn") return { ...a, status: "warn" as const };
      return { ...a, status: "active" as const };
    }
    return { ...a, status: "pending" as const };
  });
}

export default function AgentActivityPanel({
  bookId,
  chapterIdx,
  isGenerating,
  currentAgent,
  onClose,
}: Props) {
  const [logs, setLogs] = useState<AgentLogEntry[]>([]);
  const logEndRef = useRef<HTMLDivElement>(null);
  const logContainerRef = useRef<HTMLDivElement>(null);

  // Poll agent logs
  useEffect(() => {
    if (chapterIdx === null) return;
    // Drop the previous chapter's logs immediately — otherwise its activity
    // (agent statuses, negotiation count) flashes under the newly-selected
    // chapter until the first request for the new one returns.
    setLogs([]);
    let timer: NodeJS.Timeout;
    // clearTimeout alone is not enough: if cleanup runs while a getAgentLog
    // request is in flight, the awaited callback would re-arm setTimeout
    // afterwards — an orphan polling chain that never stops.
    let cancelled = false;
    async function poll() {
      try {
        const data = await getAgentLog(bookId, chapterIdx!);
        if (cancelled) return;
        setLogs(data || []);
      } catch {}
      if (cancelled) return;
      if (isGenerating) timer = setTimeout(poll, 3000);
    }
    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [bookId, chapterIdx, isGenerating]);

  // Auto-scroll the activity log to the newest entry as logs arrive — but only
  // if the user is already near the bottom, so the 3s poll doesn't keep yanking
  // them back down while they're reading earlier entries.
  useEffect(() => {
    const el = logContainerRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 160;
    if (nearBottom) logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const agentStatuses = getAgentStatuses(logs, currentAgent);

  // Find QA->Artist negotiation rounds
  const negotiations: Array<{ qaEntry: AgentLogEntry; artistRetry?: AgentLogEntry }> = [];
  for (let i = 0; i < logs.length; i++) {
    const entry = logs[i];
    if (entry.agent === "qa" && entry.action === "check_page" && (entry.status === "warn" || entry.status === "error")) {
      // Look for a following artist retry
      const retry = logs.slice(i + 1).find(
        (l) => l.agent === "artist" && l.action === "illustrate"
      );
      negotiations.push({ qaEntry: entry, artistRetry: retry });
    }
  }

  return (
    <div className="w-[340px] bg-white border-l border-gray-200 flex flex-col h-full shrink-0">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between bg-gradient-to-r from-blue-50 to-purple-50">
        <div>
          <h3 className="font-bold text-sm text-gray-800">Agent Pipeline</h3>
          <p className="text-[10px] text-gray-500">
            {isGenerating ? "Generation in progress..." : logs.length > 0 ? "Generation complete" : "No activity yet"}
          </p>
        </div>
        <button onClick={onClose} className="p-1 hover:bg-gray-200 rounded">
          <X size={16} />
        </button>
      </div>

      {/* Pipeline DAG */}
      <div className="px-4 py-3 border-b border-gray-100 bg-gray-50/50">
        <div className="flex items-center justify-between">
          {agentStatuses.map((agent, idx) => (
            <div key={agent.key} className="flex items-center">
              {/* Node */}
              <div className="flex flex-col items-center">
                <div
                  className={`w-10 h-10 rounded-full flex items-center justify-center text-lg border-2 transition-all duration-300 ${
                    agent.status === "active"
                      ? "border-current animate-pulse shadow-md"
                      : agent.status === "done"
                      ? "border-green-400 bg-green-50"
                      : agent.status === "error"
                      ? "border-red-400 bg-red-50"
                      : agent.status === "warn"
                      ? "border-amber-400 bg-amber-50"
                      : "border-gray-200 bg-white opacity-40"
                  }`}
                  style={agent.status === "active" ? { borderColor: agent.color, color: agent.color } : undefined}
                >
                  {agent.icon}
                </div>
                <span className={`text-[9px] mt-1 font-semibold ${
                  agent.status === "active" ? "text-gray-800" : agent.status === "done" ? "text-green-600" : "text-gray-400"
                }`}>
                  {agent.name}
                </span>
                <span className="text-[8px] text-gray-400">{agent.desc}</span>
              </div>
              {/* Arrow */}
              {idx < agentStatuses.length - 1 && (
                <div className="mx-1.5 flex flex-col items-center">
                  {/* Special bidirectional arrow between Artist and QA */}
                  {idx === 2 ? (
                    <div className="flex flex-col items-center">
                      <svg width="24" height="16" viewBox="0 0 24 16" className="text-gray-300">
                        <path d="M2 5 L18 5 L14 1" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                        <path d="M22 11 L6 11 L10 15" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                      {negotiations.length > 0 && (
                        <span className="text-[8px] text-amber-600 font-semibold">
                          {negotiations.length}x
                        </span>
                      )}
                    </div>
                  ) : (
                    <svg width="20" height="10" viewBox="0 0 20 10" className="text-gray-300">
                      <path d="M2 5 L15 5 L11 1 M15 5 L11 9" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Negotiation Summary (if any) */}
      {negotiations.length > 0 && (
        <div className="px-4 py-2 border-b border-gray-100 bg-amber-50/50">
          <p className="text-[10px] font-bold text-amber-700 mb-1">
            Agent Negotiation ({negotiations.length} round{negotiations.length > 1 ? "s" : ""})
          </p>
          {negotiations.slice(-3).map((n, i) => (
            <div key={i} className="flex items-start gap-1.5 text-[10px] text-amber-800 mb-0.5">
              <span className="shrink-0">{i + 1}.</span>
              <div>
                <span className="font-semibold">QA</span> found issue: {n.qaEntry.detail}
                {n.artistRetry && (
                  <span className="text-gray-600"> &rarr; <span className="font-semibold">Artist</span> re-illustrating</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Activity Log Stream */}
      <div ref={logContainerRef} className="flex-1 overflow-y-auto px-3 py-2">
        <p className="text-[9px] text-gray-400 font-bold uppercase tracking-wider mb-2">Activity Log</p>
        {logs.length === 0 ? (
          <p className="text-xs text-gray-400 text-center py-8">
            {isGenerating ? "Waiting for first event..." : "No logs yet. Start a chapter generation."}
          </p>
        ) : (
          <div className="space-y-0.5">
            {logs.map((entry, idx) => {
              const agent = AGENTS.find((a) => a.key === entry.agent);
              const statusIcon = entry.status === "done" ? "\u2713" : entry.status === "warn" ? "\u26A0" : entry.status === "error" ? "\u2717" : "\u2022";
              const statusColor = entry.status === "done" ? "text-green-500" : entry.status === "warn" ? "text-amber-500" : entry.status === "error" ? "text-red-500" : "text-blue-400";
              return (
                <div
                  key={idx}
                  className={`flex items-start gap-1.5 py-1 px-2 rounded text-[11px] ${
                    entry.status === "warn" ? "bg-amber-50" : entry.status === "error" ? "bg-red-50" : ""
                  }`}
                >
                  <span className={`shrink-0 mt-0.5 ${statusColor}`}>{statusIcon}</span>
                  <span className="shrink-0 text-xs">{agent?.icon || "\uD83E\uDD16"}</span>
                  <div className="min-w-0">
                    <span className="font-semibold" style={{ color: agent?.color }}>
                      {agent?.name || entry.agent}
                    </span>
                    <span className="text-gray-500"> {entry.detail}</span>
                    {entry.result && (
                      <span className="text-gray-400 block text-[10px]">&rarr; {entry.result}</span>
                    )}
                  </div>
                </div>
              );
            })}
            <div ref={logEndRef} />
          </div>
        )}
      </div>
    </div>
  );
}
