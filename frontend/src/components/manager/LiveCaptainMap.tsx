import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useLiveChannel } from "../../lib/socket";
import { staffDirectoryApi } from "../../api/admin";
import { ensureGoogleMaps, getLib } from "../../lib/googleMaps";

// Same fix as MapPicker.tsx — Leaflet's default marker icon paths don't
// resolve through the bundler, so this points at the CDN copies instead.
// Purely cosmetic.
const markerIcon = L.icon({
  iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41],
  iconAnchor: [12, 41],
});

function secondsAgoLabel(iso: string | null): string {
  if (!iso) return "no position yet";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  return `${minutes} min${minutes === 1 ? "" : "s"} ago`;
}

/**
 * Read-only live-updating marker for one captain — subscribes to
 * "captain-location:{captainId}" and moves the pin as pings arrive, no
 * polling involved (the channel pushes the actual lat/lng directly, see
 * ws_manager.py's module docstring for why that one channel is the
 * exception to the "ping only, then refetch" rule). Seeded with
 * `initialLatitude`/`initialLongitude` (the caller's last-fetched
 * eligible-captains data) so the map isn't empty while waiting for the
 * first live push.
 */
export interface TrailPoint {
  latitude: number;
  longitude: number;
}

// The live push ("captain-location:{id}") only reaches sockets on the API
// instance that received the ping — with several Cloud Run instances most
// pings never arrive here. This poll re-reads the captain's newest
// breadcrumb (the same /locations trail the manager map loads) as a
// fallback, so the pin keeps moving either way.
const POSITION_POLL_MS = 25_000;
const todayStartIST = () => `${new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" })}T00:00:00+05:30`;

function usePositionPoll(captainId: string, capturedAt: string | null, apply: (lat: number, lng: number, at: string) => void) {
  const latest = useRef(capturedAt);
  latest.current = capturedAt;
  const applyRef = useRef(apply);
  applyRef.current = apply;
  const { data } = useQuery({
    queryKey: ["captain-live-position", captainId],
    // Only what's newer than the pin already shows — a tiny payload.
    queryFn: () => staffDirectoryApi.locationTrail(captainId, latest.current || todayStartIST()),
    refetchInterval: POSITION_POLL_MS,
    staleTime: 0,
  });
  useEffect(() => {
    if (!data?.length) return;
    const at = (p: { at: string }) => new Date(p.at).getTime();
    const newest = data.reduce((a, b) => (at(b) > at(a) ? b : a));
    if (latest.current && at(newest) <= new Date(latest.current).getTime()) return;
    applyRef.current(newest.latitude, newest.longitude, newest.at);
  }, [data]);
}

function LeafletLiveCaptainMap({
  captainId,
  initialLatitude,
  initialLongitude,
  initialCapturedAt,
  trail,
}: {
  captainId: string;
  initialLatitude?: number | null;
  initialLongitude?: number | null;
  initialCapturedAt?: string | null;
  trail?: TrailPoint[];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markerRef = useRef<L.Marker | null>(null);
  const trailRef = useRef<L.Polyline | null>(null);
  const [capturedAt, setCapturedAt] = useState<string | null>(initialCapturedAt ?? null);
  const [, forceTick] = useState(0);

  // Today's breadcrumb trail (workflow captures + pings) as a dotted line —
  // the audit view behind the live pin.
  useEffect(() => {
    if (!mapRef.current) return;
    trailRef.current?.remove();
    trailRef.current = null;
    if (trail && trail.length > 1) {
      trailRef.current = L.polyline(
        trail.map((p) => [p.latitude, p.longitude] as [number, number]),
        { color: "#0A66F0", weight: 3, opacity: 0.8, dashArray: "6 6" }
      ).addTo(mapRef.current);
    }
  }, [trail]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const hasStart = initialLatitude != null && initialLongitude != null;
    const start: [number, number] = hasStart ? [initialLatitude as number, initialLongitude as number] : [22.9734, 78.6569];
    const map = L.map(containerRef.current, { zoomControl: true }).setView(start, hasStart ? 14 : 5);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(map);
    markerRef.current = hasStart ? L.marker(start, { icon: markerIcon }).addTo(map) : null;
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      markerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const moveTo = (lat: number, lng: number, at: string) => {
    if (!mapRef.current) return;
    if (markerRef.current) {
      markerRef.current.setLatLng([lat, lng]);
    } else {
      markerRef.current = L.marker([lat, lng], { icon: markerIcon }).addTo(mapRef.current);
    }
    mapRef.current.setView([lat, lng], Math.max(mapRef.current.getZoom(), 14));
    setCapturedAt(at);
  };
  useLiveChannel(`captain-location:${captainId}`, (message) => {
    const lat = message.latitude as number | undefined;
    const lng = message.longitude as number | undefined;
    if (lat == null || lng == null) return;
    moveTo(lat, lng, (message.captured_at as string | undefined) ?? new Date().toISOString());
  });
  usePositionPoll(captainId, capturedAt, moveTo);

  // Keeps the "Xs ago" label ticking even between pings.
  useEffect(() => {
    const timer = setInterval(() => forceTick((n) => n + 1), 5000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="space-y-1.5">
      <div ref={containerRef} className="h-56 w-full overflow-hidden rounded-xl border border-gray-200" />
      <p className="text-xs text-[var(--color-text-secondary)]">Last Position: {secondsAgoLabel(capturedAt)}</p>
    </div>
  );
}


/** Google-tiles version of the live captain marker — same contract. */
function GoogleLiveCaptainMap({
  captainId,
  initialLatitude,
  initialLongitude,
  initialCapturedAt,
  trail,
}: {
  captainId: string;
  initialLatitude?: number | null;
  initialLongitude?: number | null;
  initialCapturedAt?: string | null;
  trail?: TrailPoint[];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const objectsRef = useRef<{
    map?: { panTo: (p: unknown) => void; setZoom: (z: number) => void; getZoom: () => number };
    marker?: { position: unknown };
    polyline?: { setMap: (m: unknown) => void };
  }>({});
  const [capturedAt, setCapturedAt] = useState<string | null>(initialCapturedAt ?? null);
  const [, forceTick] = useState(0);

  useEffect(() => {
    const { map } = objectsRef.current;
    if (!map) return;
    objectsRef.current.polyline?.setMap(null);
    objectsRef.current.polyline = undefined;
    if (trail && trail.length > 1) {
      const gmaps = (window as unknown as { google?: { maps?: { Polyline?: new (opts: unknown) => { setMap: (m: unknown) => void } } } }).google?.maps;
      if (gmaps?.Polyline) {
        objectsRef.current.polyline = new gmaps.Polyline({
          path: trail.map((p) => ({ lat: p.latitude, lng: p.longitude })),
          map,
          strokeColor: "#0A66F0",
          strokeOpacity: 0.8,
          strokeWeight: 3,
        });
      }
    }
  }, [trail]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const mapsLib = await getLib("maps");
      const markerLib = await getLib("marker");
      if (cancelled || !containerRef.current || !mapsLib || !markerLib) return;
      const hasStart = initialLatitude != null && initialLongitude != null;
      const start = hasStart ? { lat: initialLatitude as number, lng: initialLongitude as number } : { lat: 22.7196, lng: 75.8577 };
      const map = new mapsLib.Map(containerRef.current, {
        center: start,
        zoom: hasStart ? 14 : 11,
        mapId: "blussit-live",
        disableDefaultUI: true,
        zoomControl: true,
        clickableIcons: false,
      });
      const marker = hasStart ? new markerLib.AdvancedMarkerElement({ map, position: start }) : new markerLib.AdvancedMarkerElement({ map });
      objectsRef.current = { map, marker };
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const moveTo = (lat: number, lng: number, at: string) => {
    const { map, marker } = objectsRef.current;
    if (!map || !marker) return;
    marker.position = { lat, lng };
    map.panTo({ lat, lng });
    if (map.getZoom() < 14) map.setZoom(14);
    setCapturedAt(at);
  };
  useLiveChannel(`captain-location:${captainId}`, (message) => {
    const lat = message.latitude as number | undefined;
    const lng = message.longitude as number | undefined;
    if (lat == null || lng == null) return;
    moveTo(lat, lng, (message.captured_at as string | undefined) ?? new Date().toISOString());
  });
  usePositionPoll(captainId, capturedAt, moveTo);

  useEffect(() => {
    const timer = setInterval(() => forceTick((n) => n + 1), 5000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="space-y-1.5">
      <div ref={containerRef} className="h-56 w-full overflow-hidden rounded-xl border border-gray-200" />
      <p className="text-xs text-[var(--color-text-secondary)]">Last Position: {secondsAgoLabel(capturedAt)}</p>
    </div>
  );
}

/** Google when configured, the original Leaflet/OSM view as fallback. */
export function LiveCaptainMap(props: {
  captainId: string;
  initialLatitude?: number | null;
  initialLongitude?: number | null;
  initialCapturedAt?: string | null;
  trail?: TrailPoint[];
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
    return <div className="flex h-56 items-center justify-center rounded-xl border border-gray-200 bg-gray-50 text-sm text-[var(--color-text-secondary)]">Loading map…</div>;
  }
  return googleOk ? <GoogleLiveCaptainMap {...props} /> : <LeafletLiveCaptainMap {...props} />;
}
