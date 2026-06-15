import type { Segment } from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

interface HistoryImage {
  url: string;
  version: string;
  timestamp: number;
  quality?: any;
}

interface VersionsCarouselProps {
  historyImages: HistoryImage[];
  selectedSegment: Segment;
  onSelectVersion: (img: HistoryImage) => void;
}

export default function VersionsCarousel({
  historyImages,
  selectedSegment,
  onSelectVersion,
}: VersionsCarouselProps) {
  if (historyImages.length === 0) return null;

  return (
    <div className="card !p-3">
      <h3 className="font-display font-bold text-gray-700 text-xs mb-2 !leading-[1.26]">
        Versions ({historyImages.length})
      </h3>
      <div className="flex gap-2 overflow-x-auto pb-2">
        {historyImages.map((img, idx) => {
          // Newest-first ordering → bigger number = newer, matching the
          // character sheet panel's version labels.
          const label = img.version === "current" ? "Current" : `v${historyImages.length - idx}`;
          return (
            <div key={idx} className="shrink-0 w-20">
              <img
                src={`${API_BASE}${img.url}?t=${img.timestamp}`}
                alt={label}
                title={img.version === "current" ? "Current version" : "Click to restore this version"}
                onClick={() => onSelectVersion(img)}
                className={`w-20 h-20 object-contain rounded-lg cursor-pointer border-2 transition-colors bg-gray-50 ${
                  // Compare paths only — illustration_url now carries a ?v=<mtime>
                  // cache-buster the history url doesn't, so a raw === never matched.
                  selectedSegment?.illustration_url?.split("?")[0] === img.url.split("?")[0]
                    ? "border-coral"
                    : "border-transparent hover:border-coral/50"
                }`}
              />
              <p className="text-[9px] text-gray-400 text-center mt-0.5">{label}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
