import type { Segment } from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface HistoryImage {
  url: string;
  version: string;
  timestamp: number;
  quality?: any;
}

interface VersionsCarouselProps {
  historyImages: HistoryImage[];
  selectedSegment: Segment;
  onSelectVersion: (url: string, quality: any) => void;
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
        {historyImages.map((img, idx) => (
          <div key={idx} className="shrink-0 w-20">
            <img
              src={`${API_BASE}${img.url}?t=${img.timestamp}`}
              alt={img.version === "current" ? "Current" : `Version ${idx}`}
              onClick={() => onSelectVersion(img.url, img.quality || null)}
              className={`w-20 h-20 object-contain rounded-lg cursor-pointer border-2 transition-colors bg-gray-50 ${
                selectedSegment?.illustration_url === img.url
                  ? "border-coral"
                  : "border-transparent hover:border-coral/50"
              }`}
            />
            <p className="text-[9px] text-gray-400 text-center mt-0.5">{img.version === "current" ? "Current" : `v${idx}`}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
