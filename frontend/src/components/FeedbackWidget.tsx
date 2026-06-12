"use client";

import { useState } from "react";
import { MessageCircle, X, Send } from "lucide-react";
import { submitFeedback } from "@/lib/api";

/**
 * Floating feedback button (bottom-right, every page). Opens a small card with
 * a message box + optional email. Submits to POST /api/feedback. Captures the
 * current path as context so feedback can be tied to where the user was.
 */
export default function FeedbackWidget() {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");

  const send = async () => {
    const msg = message.trim();
    if (!msg) return;
    setState("sending");
    try {
      const context = typeof window !== "undefined" ? window.location.pathname + window.location.search : "";
      await submitFeedback(msg, email.trim() || undefined, context);
      setState("sent");
      setMessage("");
      setEmail("");
      // Auto-close shortly after the thank-you so the panel doesn't linger.
      setTimeout(() => { setOpen(false); setState("idle"); }, 1800);
    } catch {
      setState("error");
    }
  };

  return (
    <div className="fixed bottom-5 right-5 z-[60]">
      {open ? (
        <div className="w-80 max-w-[calc(100vw-2.5rem)] bg-white rounded-2xl shadow-2xl border border-peach/40 overflow-hidden animate-fade-in">
          <div className="flex items-center justify-between px-4 py-3 bg-gradient-to-r from-coral/10 to-peach/20 border-b border-peach/30">
            <h3 className="font-display font-bold text-gray-800 text-sm flex items-center gap-1.5">
              <MessageCircle size={15} className="text-coral" /> Share feedback
            </h3>
            <button onClick={() => setOpen(false)} className="p-1 hover:bg-white/60 rounded-lg text-gray-500" aria-label="Close">
              <X size={16} />
            </button>
          </div>

          {state === "sent" ? (
            <div className="p-6 text-center">
              <p className="text-2xl mb-2">🙌</p>
              <p className="text-sm font-semibold text-gray-700">Thanks for the feedback!</p>
            </div>
          ) : (
            <div className="p-4 space-y-2.5">
              <textarea
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="What's working, what's confusing, what you'd love to see…"
                rows={4}
                className="w-full rounded-xl border border-peach/50 p-3 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-coral/30 focus:border-coral bg-cream/40 text-gray-700"
              />
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Email (optional, if you'd like a reply)"
                className="w-full rounded-xl border border-peach/50 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-coral/30 focus:border-coral bg-cream/40 text-gray-700"
              />
              {state === "error" && (
                <p className="text-xs text-red-500">Couldn&apos;t send — please try again.</p>
              )}
              <button
                onClick={send}
                disabled={!message.trim() || state === "sending"}
                className="w-full btn-primary text-sm !py-2 flex items-center justify-center gap-1.5 disabled:opacity-50"
              >
                <Send size={14} />
                {state === "sending" ? "Sending…" : "Send feedback"}
              </button>
            </div>
          )}
        </div>
      ) : (
        <button
          onClick={() => setOpen(true)}
          className="flex items-center gap-2 bg-coral text-white pl-3.5 pr-4 py-2.5 rounded-full shadow-lg hover:bg-coral/90 transition-colors font-semibold text-sm"
          title="Share feedback"
        >
          <MessageCircle size={18} /> Feedback
        </button>
      )}
    </div>
  );
}
