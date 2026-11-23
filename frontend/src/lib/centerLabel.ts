import { toTitle } from "./titleCase";

export type CenterState = "active" | "paused" | "archived";

interface CenterLike {
  name?: string | null;
  status?: CenterState | string | null;
  is_active?: boolean;
  is_deleted?: boolean;
}

export function centerState(c: CenterLike | null | undefined): CenterState {
  if (!c) return "active";
  if (c.status === "archived" || c.is_deleted) return "archived";
  if (c.status === "paused" || c.is_active === false) return "paused";
  return "active";
}

/**
 * "Indore East (Paused)" / "Indore East (Archived)" — for filters and lists
 * where HISTORY is read (bookings, finance, salary, audit …): a paused or
 * archived center keeps all its records and still appears there. Pickers for
 * NEW work ask the API for `active_only` centers instead, so they never show it.
 */
export function centerLabel(c: CenterLike | null | undefined): string {
  const name = toTitle(c?.name || "") || "Unknown Center";
  const state = centerState(c);
  return state === "active" ? name : `${name} (${state === "paused" ? "Paused" : "Archived"})`;
}
