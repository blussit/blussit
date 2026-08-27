import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useLiveChannel } from "../../lib/socket";

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
export function LiveCaptainMap({
  captainId,
  initialLatitude,
  initialLongitude,
  initialCapturedAt,
}: {
  captainId: string;
  initialLatitude?: number | null;
  initialLongitude?: number | null;
  initialCapturedAt?: string | null;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markerRef = useRef<L.Marker | null>(null);
  const [capturedAt, setCapturedAt] = useState<string | null>(initialCapturedAt ?? null);
  const [, forceTick] = useState(0);

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

  useLiveChannel(`captain-location:${captainId}`, (message) => {
    const lat = message.latitude as number | undefined;
    const lng = message.longitude as number | undefined;
    if (lat == null || lng == null || !mapRef.current) return;
    if (markerRef.current) {
      markerRef.current.setLatLng([lat, lng]);
    } else {
      markerRef.current = L.marker([lat, lng], { icon: markerIcon }).addTo(mapRef.current);
    }
    mapRef.current.setView([lat, lng], Math.max(mapRef.current.getZoom(), 14));
    setCapturedAt((message.captured_at as string | undefined) ?? new Date().toISOString());
  });

  // Keeps the "Xs ago" label ticking even between pings.
  useEffect(() => {
    const timer = setInterval(() => forceTick((n) => n + 1), 5000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="space-y-1.5">
      <div ref={containerRef} className="h-56 w-full overflow-hidden rounded-xl border border-gray-200" />
      <p className="text-xs text-[var(--color-text-secondary)]">Last position: {secondsAgoLabel(capturedAt)}</p>
    </div>
  );
}
