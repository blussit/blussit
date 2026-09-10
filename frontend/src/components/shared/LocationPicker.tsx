/**
 * The one location block every address form uses — the Swiggy/Rapido
 * pattern, in this order:
 *
 *   [ Google map + Use-my-location ]   ← the PIN is the truth
 *   [ Area / locality search        ]  ← another way to move the pin
 *
 * Two-way sync: moving the pin re-labels the search box (area, city,
 * pincode refresh from the pin); picking a suggestion moves the pin.
 * City / state / pincode are captured silently — never asked again.
 *
 * Edge cases handled here so parents don't have to:
 *  - GPS denied/slow → search or tap the map still works, clear message;
 *  - typing in search without picking → label restores to the pin on blur;
 *  - drag-spam → reverse geocode debounced;
 *  - missing pincode on the first geocode result → gap-filled from others;
 *  - Maps unreachable → onUnavailable() lets the parent show manual fields.
 *
 * The pin is MANDATORY by design (and enforced server-side): the captain
 * navigates to exactly this point.
 */
import { useEffect, useRef, useState } from "react";
import { Crosshair, LocateFixed, MapPin, Search } from "lucide-react";
import { MAPS_AUTH_FAILURE_EVENT, ensureGoogleMaps, getLib, isMapsAuthFailed, reverseGeocode } from "../../lib/googleMaps";

export type LocationValue = {
  latitude: number;
  longitude: number;
  area: string;
  city: string;
  state: string;
  pincode: string;
  formatted: string;
};

const INDORE = { lat: 22.7196, lng: 75.8577 };

function shortLabel(v: LocationValue): string {
  return [v.area, v.city, v.pincode].filter(Boolean).join(", ") || v.formatted;
}

export function LocationPicker({
  value,
  onChange,
  onUnavailable,
  height = "15rem",
}: {
  value: LocationValue | null;
  onChange: (v: LocationValue) => void;
  onUnavailable?: () => void;
  height?: string;
}) {
  const mapRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const objects = useRef<{ map?: any; marker?: any }>({});
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const valueRef = useRef<LocationValue | null>(value);
  valueRef.current = value;

  const [status, setStatus] = useState<"loading" | "ready" | "unavailable">("loading");
  const [locating, setLocating] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [gpsError, setGpsError] = useState("");
  const watchRef = useRef<number | null>(null);

  const settle = (lat: number, lng: number, zoomIn = false) => {
    const { map, marker } = objects.current;
    if (marker) marker.position = { lat, lng };
    if (map) {
      map.panTo({ lat, lng });
      if (zoomIn && map.getZoom() < 17) map.setZoom(17);
    }
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      setResolving(true);
      const picked = await reverseGeocode(lat, lng);
      setResolving(false);
      const next: LocationValue = picked
        ? { latitude: lat, longitude: lng, area: picked.area || picked.road, city: picked.city, state: picked.state, pincode: picked.pincode, formatted: picked.formatted }
        : { latitude: lat, longitude: lng, area: "", city: "", state: "", pincode: "", formatted: `${lat.toFixed(5)}, ${lng.toFixed(5)}` };
      onChange(next);
      if (searchRef.current) searchRef.current.value = shortLabel(next);
    }, 350);
  };

  /**
   * Get the BEST device fix, not the first one.
   *
   * getCurrentPosition({enableHighAccuracy:true}) answers as soon as the
   * browser has *any* position — on a laptop (no GPS radio) and often on
   * the first seconds of a phone request that's a WiFi/IP estimate whose
   * accuracy is hundreds of metres to several kilometres. Dropping the
   * pin there looks confident and is simply wrong, which is exactly the
   * "we're nowhere near that spot" complaint.
   *
   * So: watch the position, keep the most accurate reading seen, and stop
   * as soon as it's genuinely precise (<= GOOD_FIX_M) or the window
   * closes. The customer can always drag the pin afterwards.
   */
  const acquireBestFix = (onFix: (lat: number, lng: number) => void, onFail: () => void) => {
    const GOOD_FIX_M = 50;
    const WINDOW_MS = 12000;
    if (!navigator.geolocation) {
      onFail();
      return;
    }
    let best: GeolocationPosition | null = null;
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      if (watchRef.current != null) navigator.geolocation.clearWatch(watchRef.current);
      watchRef.current = null;
      clearTimeout(windowTimer);
      if (best) onFix(best.coords.latitude, best.coords.longitude);
      else onFail();
    };
    const windowTimer = setTimeout(finish, WINDOW_MS);
    watchRef.current = navigator.geolocation.watchPosition(
      (pos) => {
        if (!best || pos.coords.accuracy < best.coords.accuracy) best = pos;
        if (pos.coords.accuracy <= GOOD_FIX_M) finish();
      },
      () => finish(),
      // maximumAge:0 — never reuse a cached fix from another part of town.
      { enableHighAccuracy: true, timeout: WINDOW_MS, maximumAge: 0 },
    );
  };

  useEffect(
    () => () => {
      if (watchRef.current != null) navigator.geolocation?.clearWatch(watchRef.current);
    },
    [],
  );

  // Key rejected by Google (wrong referrer, billing, API off): Google would
  // otherwise paint its "Oops! Something went wrong" box inside our map.
  // Drop to the parent's manual-address fields instead so booking continues.
  useEffect(() => {
    const fail = () => {
      setStatus("unavailable");
      onUnavailable?.();
    };
    if (isMapsAuthFailed()) {
      fail();
      return;
    }
    window.addEventListener(MAPS_AUTH_FAILURE_EVENT, fail);
    return () => window.removeEventListener(MAPS_AUTH_FAILURE_EVENT, fail);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const ok = await ensureGoogleMaps();
      const mapsLib = ok ? await getLib("maps") : null;
      const markerLib = ok ? await getLib("marker") : null;
      if (cancelled) return;
      if (!mapsLib || !markerLib || !mapRef.current) {
        setStatus("unavailable");
        onUnavailable?.();
        return;
      }
      const start = value ? { lat: value.latitude, lng: value.longitude } : INDORE;
      const map = new mapsLib.Map(mapRef.current, {
        center: start,
        zoom: value ? 17 : 13,
        mapId: "blussit-location",
        disableDefaultUI: true,
        zoomControl: true,
        clickableIcons: false,
      });
      const marker = new markerLib.AdvancedMarkerElement({ map, position: start, gmpDraggable: true });
      objects.current = { map, marker };

      marker.addListener("dragend", () => {
        const p = marker.position as unknown as { lat: number | (() => number); lng: number | (() => number) };
        settle(typeof p.lat === "function" ? p.lat() : p.lat, typeof p.lng === "function" ? p.lng() : p.lng);
      });
      map.addListener("click", (e: { latLng: { lat: () => number; lng: () => number } }) => settle(e.latLng.lat(), e.latLng.lng()));

      const placesLib = await getLib("places");
      if (searchRef.current && placesLib?.Autocomplete) {
        const auto = new placesLib.Autocomplete(searchRef.current, {
          componentRestrictions: { country: "in" },
          fields: ["geometry"],
        });
        auto.addListener("place_changed", () => {
          const loc = auto.getPlace()?.geometry?.location;
          if (loc) settle(loc.lat(), loc.lng(), true);
        });
      }
      setStatus("ready");
      if (searchRef.current && value) searchRef.current.value = shortLabel(value);

      // First open with no value: try GPS right away (best-effort — a
      // denial just leaves the search/tap paths).
      if (!value) {
        acquireBestFix(
          (lat, lng) => settle(lat, lng, true),
          () => setGpsError("Location access is off — search your area below or tap the map."),
        );
      }
    })();
    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const useMyLocation = () => {
    if (!navigator.geolocation) {
      setGpsError("This device doesn't support location — search your area or tap the map.");
      return;
    }
    setGpsError("");
    setLocating(true);
    acquireBestFix(
      (lat, lng) => {
        setLocating(false);
        settle(lat, lng, true);
      },
      () => {
        setLocating(false);
        setGpsError("Couldn't get your location — allow location access, or search / tap the map.");
      },
    );
  };

  if (status === "unavailable") return null;

  return (
    <div className="space-y-2">
      <div className="relative overflow-hidden rounded-xl border border-gray-200" style={{ height }}>
        <div ref={mapRef} className="h-full w-full" />
        {status === "loading" && (
          <div className="absolute inset-0 flex items-center justify-center bg-gray-50 text-sm text-[var(--color-text-secondary)]">Loading map…</div>
        )}
        <button
          type="button"
          onClick={useMyLocation}
          className="absolute right-3 top-3 flex items-center gap-1.5 rounded-full bg-white px-3 py-2 text-xs font-semibold text-[var(--color-text-primary)] shadow-lg hover:bg-gray-50"
        >
          {locating ? <Crosshair className="h-4 w-4 animate-spin text-[var(--color-primary)]" /> : <LocateFixed className="h-4 w-4 text-[var(--color-primary)]" />}
          Use my location
        </button>
      </div>
      <p className="flex items-start gap-1.5 text-xs text-[var(--color-text-secondary)]">
        <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-primary)]" />
        {resolving
          ? "Finding this address…"
          : value
            ? "Confirm the pin is on your exact doorstep — our captain navigates to this point."
            : "Drop the pin on your exact location — our captain navigates to this point."}
      </p>

      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
        <input
          ref={searchRef}
          placeholder="Search area, colony or landmark…"
          className="w-full rounded-xl border border-gray-300 py-2.5 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
          onFocus={(e) => e.target.select()}
          onBlur={(e) => {
            // Typed-but-not-picked text is meaningless — restore the pin's label.
            const current = valueRef.current;
            setTimeout(() => {
              if (current && e.target.value !== shortLabel(current)) e.target.value = shortLabel(current);
            }, 200);
          }}
        />
      </div>
      {gpsError && <p className="text-xs text-amber-700">{gpsError}</p>}
    </div>
  );
}
