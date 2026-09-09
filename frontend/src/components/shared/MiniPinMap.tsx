/**
 * Tiny read-only Google map with one pin — the job card's "where exactly
 * is this customer" glance. Renders nothing when Maps isn't available
 * (the Navigate deep link still works regardless).
 */
import { useEffect, useRef, useState } from "react";
import { ensureGoogleMaps, getLib } from "../../lib/googleMaps";

export function MiniPinMap({ latitude, longitude, className = "" }: { latitude: number; longitude: number; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const ok = await ensureGoogleMaps();
      const mapsLib = ok ? await getLib("maps") : null;
      const markerLib = ok ? await getLib("marker") : null;
      if (cancelled) return;
      if (!mapsLib || !markerLib || !ref.current) {
        setFailed(true);
        return;
      }
      const map = new mapsLib.Map(ref.current, {
        center: { lat: latitude, lng: longitude },
        zoom: 15,
        mapId: "blussit-mini",
        disableDefaultUI: true,
        gestureHandling: "cooperative",
        clickableIcons: false,
      });
      new markerLib.AdvancedMarkerElement({ map, position: { lat: latitude, lng: longitude } });
    })();
    return () => {
      cancelled = true;
    };
  }, [latitude, longitude]);

  if (failed) return null;
  return <div ref={ref} className={`h-36 w-full overflow-hidden rounded-xl border border-gray-200 ${className}`} />;
}
