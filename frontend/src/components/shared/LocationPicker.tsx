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

/** Central Indore — where the map opens, and the point whose serving
 *  center shows its slots before a customer has given an address. */
export const INDORE_CENTER = { lat: 22.7196, lng: 75.8577 };
const INDORE = INDORE_CENTER;

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

  // Fires the INSTANT this step is shown — deliberately not nested inside
  // the Google Maps loading effect below, so the browser's location
  // permission prompt (and, once granted, the fix itself) is already in
  // flight in the background while the map SDK is still downloading,
  // rather than only starting after the map finishes loading. Whichever
  // finishes second (map or fix) is the one that actually drops the pin.
  const gpsFixRef = useRef<{ lat: number; lng: number } | null>(null);
  useEffect(() => {
    if (value) return; // an existing pin (edit/repeat booking) is never overridden
    acquireBestFix(
      (lat, lng) => {
        gpsFixRef.current = { lat, lng };
        if (objects.current.map) settle(lat, lng, true); // map was ready first
      },
      () => setGpsError("Location access is off — search your area below or tap the map."),
    );
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
      // The background fix above may already have landed by the time the
      // map finishes loading — open directly on it instead of panning
      // away from a generic city view a moment later.
      const gotFixFirst = !value && !!gpsFixRef.current;
      const start = value ? { lat: value.latitude, lng: value.longitude } : gpsFixRef.current ?? INDORE;
      const map = new mapsLib.Map(mapRef.current, {
        center: start,
        zoom: value || gotFixFirst ? 17 : 13,
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
      // Resolve the actual address (reverse-geocode + onChange) for a fix
      // that beat the map here — settle() needs the marker above to exist.
      if (gotFixFirst && gpsFixRef.current) settle(gpsFixRef.current.lat, gpsFixRef.current.lng);
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
    <div className="space-y-2.5">
      <div className="flex gap-2">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[#9AA3B2]" />
          <input
            ref={searchRef}
            placeholder="Search area, colony or landmark…"
            className="h-[48px] w-full rounded-[12px] border border-[#E4E9F1] bg-white pl-10 pr-3 text-[15px] text-[#0E1A33] outline-none transition placeholder:text-[#9AA3B2] focus:border-[#0A66F0] focus:ring-2 focus:ring-[#0A66F0]/15"
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
        <button
          type="button"
          onClick={useMyLocation}
          aria-label="Use my current location"
          className="flex h-[48px] shrink-0 items-center gap-1.5 rounded-[12px] border border-[#CFE0FD] bg-[#F3F7FF] px-3 text-[13px] font-semibold text-[#0A66F0] transition hover:bg-[#E8F0FE]"
        >
          {locating ? <Crosshair className="h-4 w-4 animate-spin" /> : <LocateFixed className="h-4 w-4" />}
          <span className="hidden min-[400px]:inline">Use My Location</span>
        </button>
      </div>
      <div className="relative overflow-hidden rounded-[14px] border border-[#E4E9F1] bg-[#F6F8FC]" style={{ height }}>
        <div ref={mapRef} className="h-full w-full" />
        {status === "loading" && <div className="absolute inset-0 flex items-center justify-center text-sm text-[#5F6878]">Loading map…</div>}
      </div>
      <p className="flex items-start gap-1.5 text-[12px] text-[#5F6878]">
        <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0A66F0]" />
        {resolving
          ? "Finding this address…"
          : value
            ? "Drag the pin onto your exact gate — our captain navigates to this point."
            : "Drop the pin on your exact location — our captain navigates to this point."}
      </p>
      {gpsError && <p className="text-[12px] text-amber-700">{gpsError}</p>}
    </div>
  );
}
