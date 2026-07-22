"use client";

import { useEffect, useState } from "react";

/**
 * Single shared-passcode gate — the app's only front-door.
 *
 * Collects the access code once, stores it locally, and lets the app through;
 * api.ts attaches it as X-Access-Code on every request. The REAL enforcement is
 * server-side (AccessCodeMiddleware 403s generation without the right code), so
 * this is UX only — a wrong code lets you browse but generation will 403.
 */
export default function AccessGate({ children }: { children: React.ReactNode }) {
  const [unlocked, setUnlocked] = useState(false);
  const [ready, setReady] = useState(false);
  const [input, setInput] = useState("");

  useEffect(() => {
    setUnlocked(!!localStorage.getItem("pbg_access_code"));
    setReady(true);
  }, []);

  if (!ready) return null; // avoid a first-paint flash of the gate

  if (unlocked) return <>{children}</>;

  const submit = () => {
    const code = input.trim();
    if (!code) return;
    localStorage.setItem("pbg_access_code", code);
    setUnlocked(true);
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-cream px-4">
      <div className="bg-white rounded-2xl shadow-lg p-8 w-full max-w-sm text-center">
        <div className="text-5xl mb-3">🌱</div>
        <h1 className="font-display text-xl font-bold text-gray-800 mb-1">StorySprout</h1>
        <p className="text-sm text-gray-500 mb-5">Enter the passcode to continue.</p>
        <input
          type="password"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="口令 / passcode"
          autoFocus
          className="w-full px-3 py-2 rounded-lg border border-peach/50 text-gray-700 focus:outline-none focus:ring-2 focus:ring-coral/40 mb-3"
        />
        <button
          onClick={submit}
          disabled={!input.trim()}
          className="w-full py-2 rounded-lg bg-coral text-white font-semibold hover:bg-coral/80 transition-colors disabled:opacity-50"
        >
          Enter
        </button>
      </div>
    </div>
  );
}
