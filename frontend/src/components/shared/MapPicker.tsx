import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { Loader2, LocateFixed, MapPin, Search } from "lucide-react";
import { ensureGoogleMaps, getLib, reverseGeocode as googleReverseGeocode } from "../../lib/googleMaps";

// Leaflet's default marker icon references image paths that don't resolve
// correctly through bundlers — pointing at the CDN copies sidesteps needing
// any local asset config. This is a purely cosmetic fallback; it doesn't
// affect map functionality.
const markerIcon = L.icon({
  iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41],
  iconAnchor: [12, 41],
});

const INDIA_CENTER: [number, number] = [22.9734, 78.6569]; // rough geographic center of India — sane default before a pin is placed

interface NominatimResult {
  display_name: string;
  lat: string;
  lon: string;
}

export interface ResolvedAddress {
  line1: string;
  city: string;
  state: string;
  pincode: string;
}

// Mirrors the existing forward-search call below — same host, same
// best-effort/non-blocking contract. Never throws; a failed/unreachable
// lookup just means no "Detected: ..." suggestion appears, the pin itself
// is never blocked by this.
async function reverseGeocode(lat: number, lng: number): Promise<ResolvedAddress | null> {
  try {
    const res = await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${lat}&lon=${lng}`);
    const data = await res.json();
    const addr = data?.address;
    if (!addr) return null;
    return {
      line1: [addr.house_number, addr.road, addr.suburb].filter(Boolean).join(", ") || data.display_name || "",
      city: addr.city || addr.town || addr.village || addr.county || "",
      state: addr.state || "",
      pincode: addr.postcode || "",
    };
  } catch {
    return null;
  }
}

function LeafletMapPicker({
  latitude,
  longitude,
  onChange,
  onAddressResolved,
  showUseMyLocation = false,
}: {
  latitude: number | null | undefined;
  longitude: number | null | undefined;
  onChange: (lat: number, lng: number) => void;
  /** Best-effort reverse-geocode guess whenever the pin moves — the caller
   * decides whether/how to surface it (e.g. a dismissible "Detected: ..."
   * suggestion). Never called with a silent overwrite in mind — the parent
   * form's fields are the source of truth. */
  onAddressResolved?: (address: ResolvedAddress) => void;
  /** Renders a "Use my current location" button that gets the browser's GPS
   * position, moves the pin there, and (if onAddressResolved is provided)
   * reverse-geocodes it. */
  showUseMyLocation?: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markerRef = useRef<L.Marker | null>(null);
  const reverseGeocodeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<NominatimResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [locating, setLocating] = useState(false);
  const [locateError, setLocateError] = useState("");

  // Debounced so dragging the pin around doesn't fire a reverse-geocode
  // request on every intermediate position — same courtesy as the forward
  // search below, re: Nominatim's shared-usage rate limit.
  const scheduleReverseGeocode = (lat: number, lng: number) => {
    if (!onAddressResolved) return;
    if (reverseGeocodeTimer.current) clearTimeout(reverseGeocodeTimer.current);
    reverseGeocodeTimer.current = setTimeout(async () => {
      const resolved = await reverseGeocode(lat, lng);
      if (resolved) onAddressResolved(resolved);
    }, 500);
  };

  // Initialize the map once.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const start: [number, number] = latitude != null && longitude != null ? [latitude, longitude] : INDIA_CENTER;
    const map = L.map(containerRef.current).setView(start, latitude != null ? 15 : 5);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(map);

    const marker = L.marker(start, { icon: markerIcon, draggable: true }).addTo(map);
    marker.on("dragend", () => {
      const pos = marker.getLatLng();
      onChange(pos.lat, pos.lng);
      scheduleReverseGeocode(pos.lat, pos.lng);
    });
    map.on("click", (e: L.LeafletMouseEvent) => {
      marker.setLatLng(e.latlng);
      onChange(e.latlng.lat, e.latlng.lng);
      scheduleReverseGeocode(e.latlng.lat, e.latlng.lng);
    });

    mapRef.current = map;
    markerRef.current = marker;

    return () => {
      map.remove();
      mapRef.current = null;
      markerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep the marker in sync if the parent's lat/lng changes externally (e.g. "use my location").
  useEffect(() => {
    if (latitude == null || longitude == null || !mapRef.current || !markerRef.current) return;
    const current = markerRef.current.getLatLng();
    if (Math.abs(current.lat - latitude) > 1e-6 || Math.abs(current.lng - longitude) > 1e-6) {
      markerRef.current.setLatLng([latitude, longitude]);
      mapRef.current.setView([latitude, longitude], Math.max(mapRef.current.getZoom(), 15));
    }
  }, [latitude, longitude]);

  // Debounced address search via Nominatim — free, no API key, but rate-limited
  // to reasonable personal/low-volume use per OSM's usage policy. For production
  // traffic at scale, swap this for a paid geocoder or a self-hosted Nominatim.
  useEffect(() => {
    if (query.trim().length < 3) {
      setResults([]);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await fetch(
          `https://nominatim.openstreetmap.org/search?format=json&countrycodes=in&limit=5&q=${encodeURIComponent(query)}`,
          { signal: controller.signal }
        );
        const data: NominatimResult[] = await res.json();
        setResults(data);
      } catch {
        // Aborted or network error — silently drop, the user can keep typing or place the pin manually.
      } finally {
        setSearching(false);
      }
    }, 500);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query]);

  const useMyLocation = () => {
    if (!navigator.geolocation) {
      setLocateError("Geolocation isn't supported on this device.");
      return;
    }
    setLocateError("");
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false);
        const { latitude: lat, longitude: lng } = pos.coords;
        onChange(lat, lng);
        scheduleReverseGeocode(lat, lng);
        if (mapRef.current && markerRef.current) {
          markerRef.current.setLatLng([lat, lng]);
          mapRef.current.setView([lat, lng], 16);
        }
      },
      (err) => {
        setLocating(false);
        setLocateError(err.message || "Couldn't get your location. Enable location access and try again.");
      },
      { enableHighAccuracy: true, timeout: 15000 }
    );
  };

  const selectResult = (r: NominatimResult) => {
    const lat = Number(r.lat);
    const lng = Number(r.lon);
    onChange(lat, lng);
    setQuery(r.display_name);
    setResults([]);
    if (mapRef.current && markerRef.current) {
      markerRef.current.setLatLng([lat, lng]);
      mapRef.current.setView([lat, lng], 16);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            className="w-full rounded-xl border border-gray-300 py-2.5 pl-9 pr-9 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
            placeholder="Search for an address…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {searching && <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-gray-400" />}
          {results.length > 0 && (
            <div className="absolute z-[1000] mt-1 w-full rounded-xl border border-gray-200 bg-white shadow-lg">
              {results.map((r, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => selectResult(r)}
                  className="flex w-full items-start gap-2 border-b border-gray-100 px-3 py-2 text-left text-xs last:border-0 hover:bg-gray-50"
                >
                  <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-primary)]" />
                  {r.display_name}
                </button>
              ))}
            </div>
          )}
        </div>
        {showUseMyLocation && (
          <button
            type="button"
            onClick={useMyLocation}
            disabled={locating}
            className="flex shrink-0 items-center gap-1.5 rounded-xl border border-gray-300 px-3.5 py-2.5 text-sm font-medium text-[var(--color-text-primary)] hover:bg-gray-50 disabled:opacity-60"
          >
            {locating ? <Loader2 className="h-4 w-4 animate-spin" /> : <LocateFixed className="h-4 w-4 text-[var(--color-primary)]" />}
            <span className="hidden sm:inline">Use my location</span>
          </button>
        )}
      </div>
      {locateError && <p className="text-xs text-[var(--color-error)]">{locateError}</p>}
      <div ref={containerRef} className="h-64 w-full overflow-hidden rounded-xl border border-gray-200" />
      <p className="text-xs text-[var(--color-text-secondary)]">
        {latitude != null && longitude != null
          ? `Pinned at ${latitude.toFixed(5)}, ${longitude.toFixed(5)} — drag the pin or click the map to adjust.`
          : "Search an address or click the map to drop a pin."}
      </p>
    </div>
  );
}


/**
 * Google-powered picker (same contract as the Leaflet fallback below it):
 * real Google map tiles, Places Autocomplete search, and Google-grade
 * reverse geocoding for the "Detected: ..." suggestion — the accuracy the
 * OSM/Nominatim fallback can't reach in India.
 */
function GoogleMapPicker({
  latitude,
  longitude,
  onChange,
  onAddressResolved,
  showUseMyLocation = false,
}: {
  latitude: number | null | undefined;
  longitude: number | null | undefined;
  onChange: (lat: number, lng: number) => void;
  onAddressResolved?: (address: ResolvedAddress) => void;
  showUseMyLocation?: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const objectsRef = useRef<{ map?: { setZoom: (z: number) => void; panTo: (p: unknown) => void; getZoom: () => number }; marker?: { position: unknown } }>({});
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [locating, setLocating] = useState(false);
  const [locateError, setLocateError] = useState("");
  const [resolving, setResolving] = useState(false);

  const scheduleResolve = (lat: number, lng: number) => {
    if (!onAddressResolved) return;
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(async () => {
      setResolving(true);
      const picked = await googleReverseGeocode(lat, lng);
      setResolving(false);
      if (picked) {
        onAddressResolved({
          line1: [picked.road, picked.area].filter(Boolean).join(", ") || picked.formatted.split(",").slice(0, 2).join(","),
          city: picked.city,
          state: picked.state,
          pincode: picked.pincode,
        });
      }
    }, 400);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const mapsLib = await getLib("maps");
      const markerLib = await getLib("marker");
      if (cancelled || !containerRef.current || !mapsLib || !markerLib) return;
      const start = latitude != null && longitude != null ? { lat: latitude, lng: longitude } : { lat: 22.7196, lng: 75.8577 };
      const map = new mapsLib.Map(containerRef.current, {
        center: start,
        zoom: latitude != null ? 16 : 12,
        mapId: "blussit-picker",
        disableDefaultUI: true,
        zoomControl: true,
        clickableIcons: false,
      });
      const marker = new markerLib.AdvancedMarkerElement({ map, position: start, gmpDraggable: true });
      objectsRef.current = { map, marker };

      const settle = (lat: number, lng: number) => {
        marker.position = { lat, lng };
        onChange(lat, lng);
        scheduleResolve(lat, lng);
      };
      marker.addListener("dragend", () => {
        const p = marker.position as unknown as { lat: number | (() => number); lng: number | (() => number) };
        settle(typeof p.lat === "function" ? p.lat() : p.lat, typeof p.lng === "function" ? p.lng() : p.lng);
      });
      map.addListener("click", (e: { latLng: { lat: () => number; lng: () => number } }) => settle(e.latLng.lat(), e.latLng.lng()));

      const placesLib = await getLib("places");
      if (searchRef.current && placesLib?.Autocomplete) {
        const auto = new placesLib.Autocomplete(searchRef.current, { componentRestrictions: { country: "in" }, fields: ["geometry"] });
        auto.addListener("place_changed", () => {
          const loc = auto.getPlace()?.geometry?.location;
          if (loc) {
            map.setZoom(17);
            map.panTo({ lat: loc.lat(), lng: loc.lng() });
            settle(loc.lat(), loc.lng());
          }
        });
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // External lat/lng changes (parent state) keep the pin in sync.
  useEffect(() => {
    const { map, marker } = objectsRef.current;
    if (latitude == null || longitude == null || !map || !marker) return;
    marker.position = { lat: latitude, lng: longitude };
    map.panTo({ lat: latitude, lng: longitude });
  }, [latitude, longitude]);

  const useMyLocation = () => {
    if (!navigator.geolocation) {
      setLocateError("Geolocation isn't supported on this device.");
      return;
    }
    setLocateError("");
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false);
        const { map, marker } = objectsRef.current;
        if (map && marker) {
          map.setZoom(17);
          marker.position = { lat: pos.coords.latitude, lng: pos.coords.longitude };
          map.panTo({ lat: pos.coords.latitude, lng: pos.coords.longitude });
        }
        onChange(pos.coords.latitude, pos.coords.longitude);
        scheduleResolve(pos.coords.latitude, pos.coords.longitude);
      },
      (err) => {
        setLocating(false);
        setLocateError(err.message || "Couldn't get your location. Enable location access and try again.");
      },
      { enableHighAccuracy: true, timeout: 15000 },
    );
  };

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            ref={searchRef}
            className="w-full rounded-xl border border-gray-300 py-2.5 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
            placeholder="Search area, society or landmark…"
          />
        </div>
        {showUseMyLocation && (
          <button
            type="button"
            onClick={useMyLocation}
            disabled={locating}
            className="flex shrink-0 items-center gap-1.5 rounded-xl border border-gray-300 px-3.5 py-2.5 text-sm font-medium text-[var(--color-text-primary)] hover:bg-gray-50 disabled:opacity-60"
          >
            {locating ? <Loader2 className="h-4 w-4 animate-spin" /> : <LocateFixed className="h-4 w-4 text-[var(--color-primary)]" />}
            <span className="hidden sm:inline">Use my location</span>
          </button>
        )}
      </div>
      {locateError && <p className="text-xs text-[var(--color-error)]">{locateError}</p>}
      <div ref={containerRef} className="h-64 w-full overflow-hidden rounded-xl border border-gray-200" />
      <p className="text-xs text-[var(--color-text-secondary)]">
        {resolving
          ? "Finding this address…"
          : latitude != null && longitude != null
            ? `Pinned at ${latitude.toFixed(5)}, ${longitude.toFixed(5)} — drag the pin or click the map to adjust.`
            : "Search, use your location, or click the map to drop a pin."}
      </p>
    </div>
  );
}

/**
 * Public picker: Google Maps when configured (accurate Indian addresses),
 * the original Leaflet/OpenStreetMap picker as automatic fallback.
 */
export function MapPicker(props: {
  latitude: number | null | undefined;
  longitude: number | null | undefined;
  onChange: (lat: number, lng: number) => void;
  onAddressResolved?: (address: ResolvedAddress) => void;
  showUseMyLocation?: boolean;
}) {
  const [googleOk, setGoogleOk] = useState<boolean | null>(null);
  useEffect(() => {
    let cancelled = false;
    ensureGoogleMaps().then((ok) => !cancelled && setGoogleOk(ok));
    return () => {
      cancelled = true;
    };
  }, []);
  if (googleOk === null) {
    return <div className="flex h-64 items-center justify-center rounded-xl border border-gray-200 bg-gray-50 text-sm text-[var(--color-text-secondary)]">Loading map…</div>;
  }
  return googleOk ? <GoogleMapPicker {...props} /> : <LeafletMapPicker {...props} />;
}
