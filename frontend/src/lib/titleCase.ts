/**
 * Staff-console Title Case for names that come from the API (services,
 * plans, car types, statuses) — "star wash" -> "Star Wash".
 *
 * Only ever RAISES the first letter of each word; the rest is left as
 * typed, so "SUV", "XUV 5-Seater", "BK0041" and "WhatsApp" survive intact
 * (the landing page's titleCase lowercases the tail, which would turn
 * "XUV 5-Seater" into "Xuv 5-Seater"). Underscores read as spaces, so a raw
 * enum like "captain_on_the_way" becomes "Captain On The Way".
 */
export function toTitle(value: string | null | undefined): string {
  if (!value) return "";
  return value.replace(/_/g, " ").replace(/(^|[\s(/-])(\p{Ll})/gu, (_m, lead: string, ch: string) => lead + ch.toUpperCase());
}

/** "Hatchback · Star Wash" from whichever of the two is known (empty when neither). */
export function carAndService(vehicleType: string | null | undefined, service: string | null | undefined): string {
  return [toTitle(vehicleType), toTitle(service)].filter(Boolean).join(" · ");
}

/** A booking row's "Sedan · Star Wash" — the car TYPE (never "Brand Model")
 *  and its wash(es) or combo, title-cased, for staff lists. */
export function bookingCarAndService(b: {
  vehicle_type_name?: string | null;
  vehicle_label?: string | null;
  combo_name?: string | null;
  service_names?: string[] | null;
}): string {
  return carAndService(b.vehicle_type_name || b.vehicle_label, b.combo_name || (b.service_names || []).join(", "));
}
