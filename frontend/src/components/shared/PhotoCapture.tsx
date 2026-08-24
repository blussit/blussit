import { useEffect, useRef, useState } from "react";
import { Camera, Loader2, MapPin, RefreshCw, TriangleAlert } from "lucide-react";
import { Button } from "../ui";
import { uploadApi } from "../../api/upload";
import { getErrorMessage } from "../../lib/api-client";

export interface CapturedPhoto {
  image_url: string;
  latitude: number;
  longitude: number;
}

/**
 * Camera-only photo capture with a mandatory GPS fix, used for the captain's
 * before/after service proof. `capture="environment"` forces the device
 * camera on mobile instead of the photo library, matching the "no gallery
 * uploads" requirement in the PRD's transparency trail.
 *
 * The captured file is uploaded to the backend (see api/upload.ts) and only
 * the resulting short URL is ever handed to `onCapture` — never the raw
 * photo bytes. Sending the photo itself as a base64 string used to be what
 * happened here, and it's what made every booking-list query in the app
 * slow to the point of hanging (see backend/app/core/storage.py).
 */
export function PhotoCapture({
  label,
  onCapture,
  disabled,
  submitError,
}: {
  label: string;
  onCapture: (photo: CapturedPhoto) => void;
  disabled?: boolean;
  // Set this from the parent's mutation error (e.g. beforePhotoMutation.error)
  // — the photo upload itself can succeed while the SEPARATE call that
  // actually attaches it to the booking fails (spotty field connection).
  // Without this, that failure left the component stuck showing
  // "Uploading…" forever with both buttons disabled, and retrying meant
  // losing the already-uploaded photo and re-acquiring GPS from scratch.
  submitError?: string;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [location, setLocation] = useState<{ latitude: number; longitude: number } | null>(null);
  const [status, setStatus] = useState<"idle" | "locating" | "ready" | "uploading" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  // The already-uploaded photo's URL, kept around so a retry after a
  // submitError can re-attach it directly instead of re-uploading.
  const [uploadedUrl, setUploadedUrl] = useState<string | null>(null);

  useEffect(() => {
    if (submitError) setStatus("ready");
  }, [submitError]);

  const locate = () =>
    new Promise<{ latitude: number; longitude: number }>((resolve, reject) => {
      if (!navigator.geolocation) {
        reject(new Error("Geolocation is not supported on this device"));
        return;
      }
      navigator.geolocation.getCurrentPosition(
        (pos) => resolve({ latitude: pos.coords.latitude, longitude: pos.coords.longitude }),
        (err) => reject(new Error(err.message || "Couldn't get your location. Enable location access and try again.")),
        { enableHighAccuracy: true, timeout: 15000 }
      );
    });

  const handleFile = async (selected: File) => {
    setError(null);
    setStatus("locating");
    try {
      // The data-URL here is only ever used for the on-screen preview —
      // it's never sent anywhere. The actual upload (on confirm) sends the
      // File itself as multipart/form-data.
      const [dataUrl, geo] = await Promise.all([
        new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result as string);
          reader.onerror = () => reject(new Error("Couldn't read the photo"));
          reader.readAsDataURL(selected);
        }),
        locate(),
      ]);
      setFile(selected);
      setPreview(dataUrl);
      setLocation(geo);
      setUploadedUrl(null);
      setStatus("ready");
    } catch (e) {
      setStatus("error");
      setError(e instanceof Error ? e.message : "Capture failed");
    }
  };

  const retake = () => {
    setFile(null);
    setPreview(null);
    setLocation(null);
    setUploadedUrl(null);
    setStatus("idle");
    setError(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  const confirm = async () => {
    if (!location) return;
    setError(null);
    // Already uploaded once (this is a retry after the SUBMIT step failed,
    // not the upload step) — re-attach the same photo, don't re-upload it.
    if (uploadedUrl) {
      onCapture({ image_url: uploadedUrl, latitude: location.latitude, longitude: location.longitude });
      return;
    }
    if (!file) return;
    setStatus("uploading");
    try {
      const url = await uploadApi.photo(file);
      setUploadedUrl(url);
      onCapture({ image_url: url, latitude: location.latitude, longitude: location.longitude });
    } catch (e) {
      setStatus("ready");
      setError(getErrorMessage(e));
    }
  };

  return (
    <div className="rounded-xl border border-dashed border-gray-300 p-4">
      <p className="mb-3 text-sm font-medium text-[var(--color-text-primary)]">{label}</p>

      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void handleFile(file);
        }}
      />

      {!preview ? (
        <button
          type="button"
          disabled={disabled || status === "locating"}
          onClick={() => inputRef.current?.click()}
          className="flex w-full flex-col items-center justify-center gap-2 rounded-lg bg-gray-50 py-8 text-sm text-gray-500 transition-colors hover:bg-gray-100 disabled:opacity-50"
        >
          {status === "locating" ? (
            <>
              <Loader2 className="h-6 w-6 animate-spin text-[var(--color-primary)]" />
              Getting your location…
            </>
          ) : (
            <>
              <Camera className="h-6 w-6" />
              Tap to open camera
            </>
          )}
        </button>
      ) : (
        <div className="space-y-3">
          <img src={preview} alt={label} className="h-48 w-full rounded-lg object-cover" />
          {location && (
            <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
              <MapPin className="h-3.5 w-3.5 text-[var(--color-accent)]" />
              GPS tagged · {location.latitude.toFixed(5)}, {location.longitude.toFixed(5)}
            </div>
          )}
          <div className="flex gap-2">
            <Button type="button" variant="outline" size="sm" onClick={retake} disabled={status === "uploading"} className="flex-1">
              <RefreshCw className="h-3.5 w-3.5" /> Retake
            </Button>
            <Button type="button" size="sm" isLoading={status === "uploading"} onClick={() => void confirm()} className="flex-1">
              {status === "uploading" ? "Uploading…" : submitError ? "Retry" : "Use this photo"}
            </Button>
          </div>
        </div>
      )}

      {submitError && (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-[var(--color-error)]">
          <TriangleAlert className="h-3.5 w-3.5" /> Couldn't save this to the booking — {submitError}. Tap Retry, no need to recapture.
        </p>
      )}
      {error && (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-[var(--color-error)]">
          <TriangleAlert className="h-3.5 w-3.5" /> {error}
        </p>
      )}
    </div>
  );
}
