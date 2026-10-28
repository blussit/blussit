import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";
import { vehicleTypeApi } from "../api/catalog";
import { toTitle } from "./titleCase";

/** What a screen prints for a vehicle type nobody can name. Never an id. */
export const REMOVED_CAR_TYPE = "Removed Car Type";

const HEX24 = /^[0-9a-f]{24}$/i;

/** A display name that is never a raw id: blank / id-shaped -> "Removed Car Type". */
export const safeTypeName = (name?: string | null): string => {
  const n = (name || "").trim();
  return !n || HEX24.test(n) ? REMOVED_CAR_TYPE : n;
};

/**
 * Names for EVERY vehicle type, deleted ones included (GET /vehicle-types/lookup).
 * `name(id)` is the original name of a removed type; `tagged(id)` adds the
 * "· Removed" tag, for admin/staff screens only (customers never see the tag).
 */
export function useVehicleTypeNames() {
  const { data } = useQuery({ queryKey: ["vehicle-types", "lookup"], queryFn: () => vehicleTypeApi.lookup(), staleTime: 5 * 60_000 });
  const byId = useMemo(() => new Map((data || []).map((t) => [t.id, t])), [data]);
  const name = useCallback((id?: string | null) => (id && byId.get(id) ? toTitle(safeTypeName(byId.get(id)!.name)) : data ? REMOVED_CAR_TYPE : ""), [byId, data]);
  const isRemoved = useCallback((id?: string | null) => !!id && !!data && (byId.get(id)?.removed ?? true), [byId, data]);
  const tagged = useCallback((id?: string | null) => (id ? `${name(id)}${isRemoved(id) && data ? " · Removed" : ""}` : ""), [name, isRemoved, data]);
  return { name, isRemoved, tagged, ready: !!data };
}
