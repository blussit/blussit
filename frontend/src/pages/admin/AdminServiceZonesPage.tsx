/**
 * Service zones — draw each center's real coverage area as a polygon.
 * Once at least one zone is active, a booking's PIN decides coverage
 * (pincode becomes display-only), killing the wrong-pincode problem.
 *
 * Draw: pick a center, name the zone, hit "Draw zone", click corners on
 * the map, double-click to finish. Click an existing zone to select it,
 * drag its corners to adjust, then "Save shape".
 */
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MapPinned, Pencil, Trash2 } from "lucide-react";
import { adminServiceCenterApi } from "../../api/admin";
import { zonesApi, type ServiceZone } from "../../api/admin";
import { Badge, Button, Card, CardBody, Input, Select, Spinner } from "../../components/ui";
import { ensureGoogleMaps, getLib } from "../../lib/googleMaps";
import { getErrorMessage } from "../../lib/api-client";

const INDORE = { lat: 22.7196, lng: 75.8577 };

export default function AdminServiceZonesPage() {
  const qc = useQueryClient();
  const mapRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const objects = useRef<{ map?: any; drawing?: any; polygons: Map<string, any>; draft?: any; draftPoints: any[] }>({ polygons: new Map(), draftPoints: [] });
  const [mapsUp, setMapsUp] = useState<boolean | null>(null);
  const [name, setName] = useState("");
  const [centerId, setCenterId] = useState("");
  const [drawing, setDrawing] = useState(false);
  const [cornerCount, setCornerCount] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");

  const { data: centersPage } = useQuery({ queryKey: ["centers-admin"], queryFn: () => adminServiceCenterApi.list({ page: 1, page_size: 100 }) });
  const centers = centersPage?.data;
  const { data: zones, isLoading } = useQuery({ queryKey: ["service-zones"], queryFn: zonesApi.list });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["service-zones"] });
  const createZone = useMutation({
    mutationFn: (ring: number[][]) => zonesApi.create({ name: name.trim() || "Zone", service_center_id: centerId, ring }),
    onSuccess: () => {
      setName("");
      setError("");
      invalidate();
    },
    onError: (e) => setError(getErrorMessage(e)),
  });
  const updateZone = useMutation({
    mutationFn: ({ id, ...payload }: { id: string; name?: string; ring?: number[][]; is_active?: boolean }) => zonesApi.update(id, payload),
    onSuccess: () => {
      setDirty(false);
      setError("");
      invalidate();
    },
    onError: (e) => setError(getErrorMessage(e)),
  });
  const removeZone = useMutation({ mutationFn: (id: string) => zonesApi.remove(id), onSuccess: invalidate });

  // Map boot.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const ok = await ensureGoogleMaps();
      if (cancelled) return;
      if (!ok || !mapRef.current) {
        setMapsUp(false);
        return;
      }
      const mapsLib = await getLib("maps");
      if (!mapsLib || !mapRef.current) {
        setMapsUp(false);
        return;
      }
      const bootMap = new mapsLib.Map(mapRef.current, {
        center: INDORE,
        zoom: 12,
        mapId: "blussit-zones",
        clickableIcons: false,
        mapTypeControl: false,
        streetViewControl: false,
      });
      objects.current.map = bootMap;
      if (import.meta.env.DEV) (window as unknown as { __zonesMap?: unknown }).__zonesMap = bootMap;
      setMapsUp(true);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Render zone polygons whenever data or map changes.
  useEffect(() => {
    (async () => {
      const map = objects.current.map;
      const mapsLib = await getLib("maps");
      if (!map || !mapsLib || !zones) return;
      // clear old
      objects.current.polygons.forEach((poly) => poly.setMap(null));
      objects.current.polygons.clear();
      for (const zone of zones) {
        const path = zone.polygon.coordinates[0].map(([lng, lat]) => ({ lat, lng }));
        const poly = new mapsLib.Polygon({
          paths: path,
          map,
          strokeColor: zone.is_active ? "#188038" : "#9AA0A6",
          strokeWeight: 2,
          fillColor: zone.is_active ? "#34A853" : "#9AA0A6",
          fillOpacity: 0.15,
        });
        poly.addListener("click", () => {
          setSelectedId(zone.id);
          setDirty(false);
          objects.current.polygons.forEach((p, id) => p.setEditable(id === zone.id));
          const editable = objects.current.polygons.get(zone.id);
          if (editable) {
            const markDirty = () => setDirty(true);
            editable.getPath().addListener("set_at", markDirty);
            editable.getPath().addListener("insert_at", markDirty);
            editable.getPath().addListener("remove_at", markDirty);
          }
        });
        objects.current.polygons.set(zone.id, poly);
      }
    })();
  }, [zones, mapsUp]);

  // Hand-rolled drafting (Google's DrawingManager gives no feedback and
  // confuses people): every map click drops a visible corner into an
  // editable amber polygon; "Finish zone" saves it. No double-click magic.
  const startDrawing = async () => {
    const map = objects.current.map;
    const mapsLib = await getLib("maps");
    if (!map || !mapsLib) {
      setError("Map not ready yet — give it a second and try again.");
      return;
    }
    setError("");
    setCornerCount(0);
    setDrawing(true);
    const draft = new mapsLib.Polygon({
      map,
      paths: [],
      // clickable/editable stay OFF while capturing — an interactive
      // overlay would swallow the very map clicks that add corners.
      clickable: false,
      editable: false,
      strokeColor: "#F0A500",
      strokeWeight: 2,
      fillColor: "#F0A500",
      fillOpacity: 0.18,
    });
    objects.current.draft = draft;
    objects.current.draftPoints = [];
    objects.current.drawing = map.addListener("click", (e: { latLng: { lat: () => number; lng: () => number } }) => {
      // A Polygon born with empty paths has NO path object to push into
      // (getPath() is undefined) — keep our own points array and setPath.
      objects.current.draftPoints.push(e.latLng);
      draft.setPath(objects.current.draftPoints);
      setCornerCount(objects.current.draftPoints.length);
    });
  };

  const stopDraftListeners = () => {
    objects.current.drawing?.remove?.();
    objects.current.drawing = undefined;
  };

  const cancelDrawing = () => {
    stopDraftListeners();
    objects.current.draft?.setMap(null);
    objects.current.draft = undefined;
    objects.current.draftPoints = [];
    setCornerCount(0);
    setDrawing(false);
  };

  const finishDrawing = () => {
    const draft = objects.current.draft;
    if (!draft) return;
    const ring = objects.current.draftPoints.map((p: { lat: () => number; lng: () => number }) => [p.lng(), p.lat()]);
    if (ring.length < 3) {
      setError("Add at least 3 corners before finishing.");
      return;
    }
    stopDraftListeners();
    draft.setMap(null);
    objects.current.draft = undefined;
    setCornerCount(0);
    setDrawing(false);
    createZone.mutate(ring);
  };

  const saveShape = () => {
    if (!selectedId) return;
    const poly = objects.current.polygons.get(selectedId);
    if (!poly) return;
    const ring = poly.getPath().getArray().map((p: { lat: () => number; lng: () => number }) => [p.lng(), p.lat()]);
    updateZone.mutate({ id: selectedId, ring });
  };

  const centerName = (id: string) => centers?.find((c: { id: string; name: string }) => c.id === id)?.name || id.slice(-6);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Service zones</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Draw where you actually serve. Once a zone is active, a customer's pinned location decides coverage — mistyped pincodes stop
          mattering. No zones drawn = the old pincode rules stay in force.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[340px_1fr]">
        <div className="space-y-4">
          <Card>
            <CardBody className="space-y-3 p-4">
              <p className="text-sm font-semibold text-[var(--color-text-primary)]">New zone</p>
              <Select label="Service center" value={centerId} onChange={(e) => setCenterId(e.target.value)}>
                <option value="">Select…</option>
                {(centers || []).map((c: { id: string; name: string }) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
              <Input label="Zone name" placeholder="e.g. Vijay Nagar belt" value={name} onChange={(e) => setName(e.target.value)} />
              {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
              {drawing ? (
                <div className="space-y-2">
                  <div className="rounded-xl bg-[var(--color-secondary-light,#FDF3D7)] px-3 py-2.5 text-sm text-[var(--color-text-primary)]">
                    <span className="font-semibold">{cornerCount === 0 ? "Now click the map" : `${cornerCount} corner${cornerCount === 1 ? "" : "s"} added`}</span>
                    <span className="block text-xs text-[var(--color-text-secondary)]">
                      Each click on the map adds a corner of your zone. Trace the area you serve (3+ corners), then press Finish.
                    </span>
                  </div>
                  <Button className="w-full" disabled={cornerCount < 3} isLoading={createZone.isPending} onClick={finishDrawing}>
                    ✓ Finish zone ({cornerCount} corners)
                  </Button>
                  <Button variant="secondary" className="w-full" onClick={cancelDrawing}>
                    Cancel
                  </Button>
                </div>
              ) : (
                <Button className="w-full" disabled={!centerId || mapsUp !== true} isLoading={createZone.isPending} onClick={startDrawing}>
                  <Pencil className="h-4 w-4" /> Draw zone on map
                </Button>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardBody className="p-4">
              <p className="mb-2 text-sm font-semibold text-[var(--color-text-primary)]">Zones</p>
              {isLoading && <Spinner />}
              {!isLoading && !(zones || []).length && <p className="text-sm text-[var(--color-text-secondary)]">Nothing drawn yet.</p>}
              <ul className="space-y-2">
                {(zones || []).map((zone: ServiceZone) => (
                  <li
                    key={zone.id}
                    className={`rounded-xl border p-3 ${selectedId === zone.id ? "border-[var(--color-primary)]" : "border-gray-100"}`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <button
                        type="button"
                        className="min-w-0 flex-1 text-left"
                        onClick={() => {
                          setSelectedId(zone.id);
                          const poly = objects.current.polygons.get(zone.id);
                          if (poly && objects.current.map) {
                            objects.current.polygons.forEach((p, id) => p.setEditable(id === zone.id));
                            const bounds = poly.getPath().getArray();
                            if (bounds.length) objects.current.map.panTo({ lat: bounds[0].lat(), lng: bounds[0].lng() });
                          }
                        }}
                      >
                        <p className="truncate text-sm font-semibold text-[var(--color-text-primary)]">{zone.name}</p>
                        <p className="text-xs text-[var(--color-text-secondary)]">{centerName(zone.service_center_id)}</p>
                      </button>
                      <Badge tone={zone.is_active ? "success" : "neutral"}>{zone.is_active ? "Active" : "Off"}</Badge>
                    </div>
                    <div className="mt-2 flex items-center gap-3 text-xs">
                      <button
                        type="button"
                        className="font-medium text-[var(--color-text-secondary)] underline"
                        onClick={() => updateZone.mutate({ id: zone.id, is_active: !zone.is_active })}
                      >
                        {zone.is_active ? "Deactivate" : "Activate"}
                      </button>
                      {selectedId === zone.id && dirty && (
                        <button type="button" className="font-semibold text-[var(--color-primary)] underline" onClick={saveShape}>
                          Save shape
                        </button>
                      )}
                      <button
                        type="button"
                        className="ml-auto text-[var(--color-error)] opacity-70 hover:opacity-100"
                        onClick={() => removeZone.mutate(zone.id)}
                        title="Delete zone"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </CardBody>
          </Card>
        </div>

        <Card>
          <CardBody className="p-2">
            {mapsUp === false ? (
              <div className="flex h-[560px] items-center justify-center text-sm text-[var(--color-text-secondary)]">
                <MapPinned className="mr-2 h-4 w-4" /> Google Maps unavailable — zones need the map to draw.
              </div>
            ) : (
              <div ref={mapRef} className="h-[560px] w-full overflow-hidden rounded-xl" />
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
