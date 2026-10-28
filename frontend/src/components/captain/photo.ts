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
  /** The device's own time for this fix (ISO) — the server compares it with its clock. */
  fix_at?: string;
  speed_mps?: number;
  heading_deg?: number;
  altitude_m?: number;
  /** Extra evidence only (never the control): HTTPS page? permission state? */
  secure_context?: boolean;
  permission_state?: string;
}

/** The fields every geo-stamped job step sends, whichever step it is. */
export function fixFields(p: Position): Position {
  return {
    latitude: p.latitude,
    longitude: p.longitude,
    accuracy_m: p.accuracy_m,
    fix_at: p.fix_at,
    speed_mps: p.speed_mps,
    heading_deg: p.heading_deg,
    altitude_m: p.altitude_m,
    secure_context: p.secure_context,
    permission_state: p.permission_state,
  };
}

const finite = (n: number | null | undefined): number | undefined => (typeof n === "number" && Number.isFinite(n) ? n : undefined);

async function permissionState(): Promise<string | undefined> {
  try {
    const status = await navigator.permissions?.query({ name: "geolocation" as PermissionName });
    return status?.state;
  } catch {
    return undefined;
  }
}

/** Reads a browser position into the shape the server checks. */
export function toPosition(pos: GeolocationPosition, permission?: string): Position {
  const c = pos.coords;
  return {
    latitude: c.latitude,
    longitude: c.longitude,
    accuracy_m: finite(c.accuracy),
    fix_at: new Date(pos.timestamp).toISOString(),
    speed_mps: finite(c.speed),
    heading_deg: finite(c.heading),
    altitude_m: finite(c.altitude),
    secure_context: typeof window !== "undefined" ? window.isSecureContext : undefined,
    permission_state: permission,
  };
}

/** Why a fix couldn't be taken, in words a captain can act on. */
export function geoErrorMessage(err: GeolocationPositionError | undefined, fallback: string): string {
  if (!err) return fallback;
  if (err.code === 1) return "Location Is Blocked — Allow Location For This App In Your Browser Settings, Then Try Again";
  if (err.code === 2) return "Your Location Isn't Available — Move To An Open Area And Try Again";
  if (err.code === 3) return "Your Location Took Too Long — Try Again";
  return fallback;
}

/** A blocking, FRESH GPS fix (maximumAge 0 — never a cached one): every job
 * step is geo-stamped and the server refuses an old or missing position. */
export async function getPosition(failMessage: string): Promise<Position> {
  if (!navigator.geolocation) throw new Error(failMessage);
  if (typeof window !== "undefined" && window.isSecureContext === false) {
    throw new Error("Open The App Over A Secure (HTTPS) Link To Share Your Location");
  }
  const permission = await permissionState();
  if (permission === "denied") throw new Error(geoErrorMessage({ code: 1 } as GeolocationPositionError, failMessage));
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve(toPosition(pos, permission)),
      (err) => reject(new Error(geoErrorMessage(err, failMessage))),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  });
}

/** Best effort — attendance is still recorded without a fix (GPS denied
 * must not stop a work day); it just stores no location. */
export function tryPosition(): Promise<GeoPayload> {
  return getPosition("").then(
    (p) => ({ latitude: p.latitude, longitude: p.longitude, accuracy_m: p.accuracy_m }),
    () => ({}),
  );
}
