/**
 * One proof photo: camera only (capture="environment", no gallery), shrunk
 * to ~1600 px JPEG, GPS fix taken at the same moment. The upload happens
 * when the captain submits the step, and the uploaded URL is kept so a
 * failed submit (spotty connection) retries without re-shooting.
 */
import { useEffect, useRef, useState } from "react";
import { Camera, Loader2, MapPin, RefreshCw } from "lucide-react";
import { uploadApi } from "../../api/upload";
import { fixFields, getJobPosition, hasFix, shrinkPhoto, type Position } from "./photo";

export interface Shot {
  ready: boolean;
  /** A position was captured with the photo (the "Location Saved" tick). */
  hasLocation: boolean;
  busy: boolean;
  error: string | null;
  /** Uploads (once) and returns what the photo endpoints take. */
  submit: () => Promise<{ image_url: string } & Position>;
  input: {
    preview: string | null;
    status: "idle" | "locating" | "ready";
    onFile: (file: File) => void;
    reset: () => void;
  };
}

/** LOCATION NEVER BLOCKS THE CAPTAIN: the photo is ready the moment it is shot.
 * The position is looked up beside it (and again, fresh, when the step is
 * submitted); a phone that can't give one still submits — the server flags it
 * to the manager. */
export function usePhotoShot(): Shot {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [geo, setGeo] = useState<Position | null>(null);
  const [status, setStatus] = useState<"idle" | "locating" | "ready">("idle");
  const [error, setError] = useState<string | null>(null);
  const uploaded = useRef<string | null>(null);
  // A late position from a photo that was since retaken must not land on the new one.
  const shotNo = useRef(0);

  useEffect(() => {
    if (!preview) return;
    return () => URL.revokeObjectURL(preview);
  }, [preview]);

  const onFile = async (selected: File) => {
    setError(null);
    setStatus("locating");
    const mine = ++shotNo.current;
    try {
      const photo = await shrinkPhoto(selected);
      uploaded.current = null;
      setFile(photo);
      setGeo(null);
      setPreview(URL.createObjectURL(photo));
      setStatus("ready");
      void getJobPosition().then((pos) => {
        if (mine === shotNo.current && hasFix(pos)) setGeo(pos);
      });
    } catch (e) {
      setStatus(preview ? "ready" : "idle");
      setError(e instanceof Error ? e.message : null);
    }
  };

  const reset = () => {
    shotNo.current++;
    uploaded.current = null;
    setFile(null);
    setGeo(null);
    setPreview(null);
    setStatus("idle");
    setError(null);
  };

  const submit = async () => {
    if (!file) throw new Error("Take the photo first.");
    if (!uploaded.current) uploaded.current = await uploadApi.photo(file);
    // The position sent is taken NOW (the photo may have been shot minutes
    // ago); the photo-time one is only the fallback, and a phone with no
    // position at all submits without — never blocked.
    const fresh = await getJobPosition();
    return { image_url: uploaded.current, ...fixFields(hasFix(fresh) ? fresh : (geo ?? fresh)) };
  };

  return {
    ready: status === "ready" && !!file,
    hasLocation: hasFix(geo),
    busy: status === "locating",
    error,
    submit,
    input: { preview, status, onFile: (f) => void onFile(f), reset },
  };
}

export function PhotoTile({
  shot,
  label,
  tapText,
  locatingText,
  retakeText,
  gpsText,
  noGpsText,
}: {
  shot: Shot;
  label: string;
  tapText: string;
  locatingText: string;
  retakeText: string;
  gpsText: string;
  /** Shown instead of the location tick while no position is attached (neutral — never a warning). */
  noGpsText?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const { preview, status, onFile, reset } = shot.input;
  const open = () => ref.current?.click();
  return (
    <div>
      <input
        ref={ref}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        data-testid="captain-photo-input"
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) onFile(f);
        }}
      />
      {preview ? (
        <div className="overflow-hidden rounded-[18px] border border-[#E4E9F1]">
          <img src={preview} alt={label} className="aspect-[4/3] w-full object-cover" />
          <div className="flex items-center justify-between gap-2 px-3 py-2">
            <span className="flex items-center gap-1.5 text-xs font-semibold text-[#15803D]">
              <MapPin className="h-3.5 w-3.5" /> {shot.hasLocation || !noGpsText ? gpsText : noGpsText}
            </span>
            <button
              type="button"
              onClick={() => {
                reset();
                open();
              }}
              className="flex min-h-[44px] items-center gap-1.5 rounded-xl px-3 text-sm font-bold text-[#0A66F0] active:bg-[#E8F0FE]"
            >
              <RefreshCw className="h-4 w-4" /> {retakeText}
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={open}
          disabled={status === "locating"}
          className="flex aspect-[4/3] w-full flex-col items-center justify-center gap-3 rounded-[18px] border-2 border-dashed border-[#B9CCEB] bg-[#EEF3FA] text-[#0A66F0] active:bg-[#E8F0FE]"
        >
          {status === "locating" ? (
            <>
              <Loader2 className="h-9 w-9 animate-spin" />
              <span className="text-sm font-semibold text-[#5F6878]">{locatingText}</span>
            </>
          ) : (
            <>
              <span className="flex h-16 w-16 items-center justify-center rounded-full bg-white shadow-sm">
                <Camera className="h-8 w-8" />
              </span>
              <span className="text-[15px] font-bold">{tapText}</span>
              <span className="text-xs font-semibold text-[#5F6878]">{label}</span>
            </>
          )}
        </button>
      )}
      {shot.error && <p className="mt-2 text-sm font-medium text-[#B91C1C]">{shot.error}</p>}
    </div>
  );
}
