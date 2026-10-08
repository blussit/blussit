/**
 * Captain proof photos + GPS. Moved here from shared/PhotoCapture (only the
 * captain flow ever used it) — the resize is unchanged: ~1600 px JPEG.
 */
import type { GeoPayload } from "../../api/staffOps";

const MAX_EDGE_PX = 1600;
const JPEG_QUALITY = 0.8;

async function decodeImage(file: File): Promise<{ source: CanvasImageSource; width: number; height: number; close: () => void }> {
  if (typeof createImageBitmap === "function") {
    try {
      // Applies the EXIF rotation, so a portrait phone shot isn't drawn sideways.
      const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
      return { source: bitmap, width: bitmap.width, height: bitmap.height, close: () => bitmap.close() };
    } catch {
      // Older Safari rejects the options bag — the <img> path below also
      // honours EXIF orientation in every browser that lacks it.
    }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    return { source: img, width: img.naturalWidth, height: img.naturalHeight, close: () => {} };
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Camera originals run to 8 MB; ~1600 px JPEG is plenty as proof of work
 * and uploads in a fraction of the time on a field connection. Falls back
 * to the original file if the browser can't decode it (e.g. HEIC). */
export async function shrinkPhoto(file: File): Promise<File> {
  try {
    const img = await decodeImage(file);
    try {
      const scale = Math.min(1, MAX_EDGE_PX / Math.max(img.width, img.height));
      const width = Math.round(img.width * scale);
      const height = Math.round(img.height * scale);
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      if (!ctx) return file;
      ctx.drawImage(img.source, 0, 0, width, height);
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
      if (!blob || (blob.size >= file.size && file.type === "image/jpeg")) return file;
      return new File([blob], file.name.replace(/\.[^.]*$/, "") + ".jpg", { type: "image/jpeg" });
    } finally {
      img.close();
    }
  } catch {
    return file;
  }
}

export interface Position {
  latitude: number;
  longitude: number;
  accuracy_m?: number;
}

/** A blocking GPS fix — every job step is geo-stamped, so no fix, no step. */
export function getPosition(failMessage: string): Promise<Position> {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error(failMessage));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ latitude: pos.coords.latitude, longitude: pos.coords.longitude, accuracy_m: pos.coords.accuracy ?? undefined }),
      (err) => reject(new Error(err.message || failMessage)),
      { enableHighAccuracy: true, timeout: 15000 },
    );
  });
}

/** Best effort — attendance is still recorded without a fix (GPS denied
 * must not stop a work day); it just stores no location. */
export function tryPosition(): Promise<GeoPayload> {
  return getPosition("").then(
    (p) => p,
    () => ({}),
  );
}
