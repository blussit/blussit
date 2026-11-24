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
  /** Absent when the phone could not give a position (denied / timed out /
   *  unavailable): the step is still sent and the server flags it to the manager. */
  latitude?: number;
  longitude?: number;
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

/** A position that does carry coordinates (what the periodic ping needs). */
export type FixedPosition = Position & { latitude: number; longitude: number };

/** True when the position carries real coordinates. */
export const hasFix = (p: Position | null | undefined): p is FixedPosition =>
  !!p && typeof p.latitude === "number" && typeof p.longitude === "number";

/** The fields every geo-stamped job step sends, whichever step it is. */
export function fixFields(p: FixedPosition): FixedPosition;
export function fixFields(p: Position): Position;
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
export function toPosition(pos: GeolocationPosition, permission?: string): FixedPosition {
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

// How a job step gets its position. LOCATION NEVER BLOCKS THE CAPTAIN: every
// path below resolves — with coordinates when the phone gave some, without
// them when it didn't (the server then flags the step to the manager).
const FIRST_FIX_TIMEOUT_MS = 15_000; // one fresh high-accuracy fix
const POOR_FIX_M = 100; // worse than this: keep listening a few seconds for a better one
const WATCH_MS = 8_000; // …and take the best fix seen in that time
const MAX_FIX_AGE_MS = 0; // always a fresh fix (the server rejects reused ones as suspicious)

function readOnce(): Promise<GeolocationPosition | null> {
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve(pos),
      () => resolve(null),
      { enableHighAccuracy: true, timeout: FIRST_FIX_TIMEOUT_MS, maximumAge: MAX_FIX_AGE_MS },
    );
  });
}

/** Listens for `ms` and resolves with the most accurate fix seen (or null). */
function watchBest(ms: number, seed: GeolocationPosition | null): Promise<GeolocationPosition | null> {
  return new Promise((resolve) => {
    let best = seed;
    const finish = () => {
      navigator.geolocation.clearWatch(id);
      clearTimeout(timer);
      resolve(best);
    };
    const id = navigator.geolocation.watchPosition(
      (pos) => {
        if (!best || pos.coords.accuracy < best.coords.accuracy) best = pos;
        if (best.coords.accuracy <= POOR_FIX_M) finish(); // good enough — stop waiting
      },
      () => {}, // an error while listening just means "keep what we have"
      { enableHighAccuracy: true, timeout: ms, maximumAge: MAX_FIX_AGE_MS },
    );
    const timer = setTimeout(finish, ms);
  });
}

/** The position for a job step (heading, arrived, before / after photo): never
 * throws and never waits more than ~25 s. A good first fix is used as is; a
 * weak or missing one is retried by listening for a few seconds and taking the
 * most accurate fix. No usable fix at all (permission denied, timeout, no
 * GPS, plain HTTP) -> a Position WITHOUT coordinates that still says why
 * (permission_state / secure_context), and the step is submitted anyway. */
export async function getJobPosition(): Promise<Position> {
  const base: Position = { secure_context: typeof window !== "undefined" ? window.isSecureContext : undefined };
  if (typeof navigator === "undefined" || !navigator.geolocation) return base;
  if (base.secure_context === false) return base;
  const permission = await permissionState();
  base.permission_state = permission;
  if (permission === "denied") return base;
  try {
    let best = await readOnce();
    if (!best || best.coords.accuracy > POOR_FIX_M) best = await watchBest(WATCH_MS, best);
    return best ? toPosition(best, permission) : base;
  } catch {
    return base;
  }
}

/** Best effort — attendance is still recorded without a fix (GPS denied
 * must not stop a work day); it just stores no location. */
export function tryPosition(): Promise<GeoPayload> {
  return getJobPosition().then((p) => (hasFix(p) ? { latitude: p.latitude, longitude: p.longitude, accuracy_m: p.accuracy_m } : {}));
}
