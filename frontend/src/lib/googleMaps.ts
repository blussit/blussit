/**
 * Google Maps JS loader — key comes from the backend's public maps-config
 * (browser keys are public by design; Google Cloud key restrictions are
 * the real protection). Loads once; resolves false when not configured so
 * callers keep the manual-address fallback.
 */
import { mapsApi } from "../api/auth";

// Loose typing on purpose — avoids a @types/google.maps dependency for
// the handful of APIs we touch.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type GMaps = any;

declare global {
  interface Window {
    google?: { maps?: GMaps; accounts?: GMaps };
  }
}

let loader: Promise<boolean> | null = null;

// Google calls window.gm_authFailure() when the key is rejected AFTER the
// script loaded (RefererNotAllowedMapError, billing, API not enabled…). The
// script itself loads fine in that case, so ensureGoogleMaps() alone can't
// tell — we remember the failure and broadcast it so pickers can drop to
// their manual-address fallback instead of showing Google's "Oops" box.
export const MAPS_AUTH_FAILURE_EVENT = "gmaps-auth-failure";
let authFailed = false;
export const isMapsAuthFailed = () => authFailed;
if (typeof window !== "undefined") {
  (window as unknown as { gm_authFailure?: () => void }).gm_authFailure = () => {
    authFailed = true;
    console.warn("Google Maps rejected the browser key for this origin — falling back to manual address entry.");
    window.dispatchEvent(new Event(MAPS_AUTH_FAILURE_EVENT));
  };
}
// Modern Maps JS keeps classes inside the imported library objects (they
// are no longer reliably attached to the google.maps.* namespace).
export const mapsLibs: { maps?: GMaps; marker?: GMaps; places?: GMaps; geocoding?: GMaps; drawing?: GMaps } = {};

/** On-demand library access — resilient to any load-order surprises. */
export async function getLib(name: "maps" | "marker" | "places" | "geocoding" | "drawing"): Promise<GMaps | null> {
  if (mapsLibs[name]) return mapsLibs[name];
  const g = window.google?.maps;
  if (!g?.importLibrary) return null;
  try {
    mapsLibs[name] = await g.importLibrary(name);
    return mapsLibs[name];
  } catch {
    return null;
  }
}

export function ensureGoogleMaps(): Promise<boolean> {
  if (!loader) {
    loader = (async () => {
      try {
        if (window.google?.maps) return true;
        const cfg = await mapsApi.config();
        if (!cfg.enabled || !cfg.browser_key) return false;
        await new Promise<void>((resolve, reject) => {
          const script = document.createElement("script");
          script.src = `https://maps.googleapis.com/maps/api/js?key=${cfg.browser_key}&libraries=places,marker&loading=async&region=IN`;
          script.async = true;
          script.onload = () => resolve();
          script.onerror = () => reject(new Error("maps script failed"));
          document.head.appendChild(script);
        });
        // google.maps appears a beat BEFORE importLibrary attaches —
        // wait for the function itself, or every constructor is missing.
        for (let i = 0; i < 50 && !window.google?.maps?.importLibrary; i++) await new Promise((r) => setTimeout(r, 100));
        const g = window.google?.maps;
        if (!g?.importLibrary) return false;
        // loading=async defers every constructor until its library is
        // imported — pull in the full set we use up front.
        const [maps, marker, places, geocoding] = await Promise.all([
          g.importLibrary("maps"),
          g.importLibrary("marker"),
          g.importLibrary("places"),
          g.importLibrary("geocoding"),
        ]);
        Object.assign(mapsLibs, { maps, marker, places, geocoding });
        return true;
      } catch {
        return false;
      }
    })();
  }
  return loader;
}

export type PickedAddress = {
  latitude: number;
  longitude: number;
  road: string;
  area: string;
  city: string;
  state: string;
  pincode: string;
  formatted: string;
};

/** Reverse-geocodes a point into the fields our address form uses. */
export async function reverseGeocode(lat: number, lng: number): Promise<PickedAddress | null> {
  const geocoding = await getLib("geocoding");
  if (!geocoding) return null;
  const geocoder = new geocoding.Geocoder();
  try {
    const { results } = await geocoder.geocode({ location: { lat, lng } });
    const best = results?.[0];
    if (!best) return null;
    // Some pins resolve without a pincode/locality on the FIRST result —
    // scan the whole result set so those fields still fill (edge case:
    // new colonies, plus-code-only results).
    const get = (type: string) => {
      for (const r of results) {
        const hit = r.address_components?.find((c: { types: string[]; long_name: string }) => c.types.includes(type));
        if (hit) return hit.long_name;
      }
      return "";
    };
    return {
      latitude: lat,
      longitude: lng,
      road: [get("street_number"), get("route")].filter(Boolean).join(", "),
      area: get("sublocality_level_1") || get("sublocality") || get("neighborhood"),
      city: get("locality") || get("administrative_area_level_2"),
      state: get("administrative_area_level_1"),
      pincode: get("postal_code"),
      formatted: best.formatted_address,
    };
  } catch {
    return null;
  }
}
