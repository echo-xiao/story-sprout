"use client";

import { useEffect, useRef, useState } from "react";
import { getStyleReference, uploadStyleReference, deleteStyleReference } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

/**
 * Compact book-wide "style reference" control for the editor top bar.
 * Shows the current reference image (uploaded, or the cover by default) as a
 * small thumbnail; click to upload/replace; "revert" drops back to the cover.
 * Every scene/character/page is generated to match this image.
 */
export default function StyleReferenceWidget({
  bookId,
  canEdit,
}: {
  bookId: string;
  canEdit: boolean;
}) {
  const [ref, setRef] = useState<{ url: string | null; custom: boolean }>({
    url: null,
    custom: false,
  });
  const [busy, setBusy] = useState(false);
  const [bust, setBust] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = () =>
    getStyleReference(bookId)
      .then((r) => {
        setRef(r);
        setBust(Date.now());  // force the <img> to reload even on a same-name re-upload
      })
      .catch(() => {});

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookId]);

  const onPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setBusy(true);
    try {
      await uploadStyleReference(bookId, f);
      await load();
    } catch (err: any) {
      alert(`Upload failed: ${err?.response?.data?.detail || err?.message || err}`);
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const onRevert = async () => {
    setBusy(true);
    try {
      await deleteStyleReference(bookId);
      await load();
    } catch (err: any) {
      alert(`Revert failed: ${err?.response?.data?.detail || err?.message || err}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="flex items-center gap-1.5"
      title="Style reference — every scene, character and page is generated to match this image. Click to upload your own; default is the cover."
    >
      <span className="text-[11px] text-gray-500 font-semibold">Style</span>
      <button
        type="button"
        disabled={!canEdit || busy}
        onClick={() => fileRef.current?.click()}
        className="relative h-9 w-9 rounded-lg overflow-hidden border border-peach/50 hover:border-coral transition-colors disabled:opacity-50 disabled:cursor-not-allowed bg-peach/10"
      >
        {ref.url ? (
          <img
            src={`${ref.url.startsWith("http") ? "" : API_BASE}${ref.url}${ref.url.includes("?") ? "&" : "?"}t=${bust}`}
            alt="style reference"
            className="h-full w-full object-cover"
          />
        ) : (
          <span className="text-[9px] text-gray-400">none</span>
        )}
        {busy && (
          <span className="absolute inset-0 flex items-center justify-center bg-white/60 text-[10px]">
            …
          </span>
        )}
      </button>
      {ref.custom && canEdit && (
        <button
          type="button"
          onClick={onRevert}
          disabled={busy}
          className="text-[10px] text-gray-400 hover:text-coral"
        >
          revert
        </button>
      )}
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={onPick}
      />
    </div>
  );
}
