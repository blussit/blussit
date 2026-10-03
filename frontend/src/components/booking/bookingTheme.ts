/**
 * Booking v2 look (approved mockup `figmav2bookingpage.jpeg`) — the same
 * palette the v2 landing hero uses. Kept as plain values so Tailwind
 * arbitrary classes and inline styles agree.
 */
import { titleCase } from "../public/landing/shared";

export const NAVY = "#0E1A33";
export const BLUE = "#0A66F0";
export const MUTED = "#5F6878";
export const YELLOW = "#FFD21F";
export const TINT = "#E8F0FE";
export const ICON_BG = "#EEF3FA";
export const LINE = "#E4E9F1";

/** One soft card surface for every block on the booking page. */
export const CARD = "rounded-[18px] border border-[#E4E9F1] bg-white shadow-[0_6px_24px_-12px_rgba(14,26,51,0.14)]";

/** The yellow call to action (navy text), full width. */
export const CTA =
  "flex h-[52px] w-full items-center justify-center gap-2 rounded-[14px] bg-[#FFD21F] px-5 font-display text-[16px] font-bold text-[#0E1A33] shadow-[0_8px_20px_-10px_rgba(232,169,0,0.7)] transition hover:bg-[#FFC800] active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-60";

/** Text inputs on the confirm step. */
export const FIELD =
  "h-[48px] w-full rounded-[12px] border border-[#E4E9F1] bg-white px-3.5 text-[15px] text-[#0E1A33] placeholder:text-[#9AA3B2] outline-none transition focus:border-[#0A66F0] focus:ring-2 focus:ring-[#0A66F0]/15";

export interface VehicleMeta {
  image: string;
  /** "i10, Swift etc." — what a customer recognises the class by. */
  examples: string;
  bike: boolean;
}

/** Picture + everyday examples for a vehicle type, matched by its name/slug. */
export function vehicleMeta(t: { name: string; slug?: string } | null | undefined): VehicleMeta {
  const key = `${t?.slug || ""} ${t?.name || ""}`.toLowerCase();
  if (/bike|scoot|two.?wheel/.test(key)) return { image: "/img/vt-bike-560.webp", examples: "Scooter or motorbike", bike: true };
  if (/7.?seat|muv/.test(key)) return { image: "/img/vt-suv-560.webp", examples: "Innova, XUV700 etc.", bike: false };
  if (/xuv|5.?seat/.test(key)) return { image: "/img/vt-suv-560.webp", examples: "Harrier, Hector etc.", bike: false };
  if (/suv|luxury/.test(key)) return { image: "/img/vt-suv-560.webp", examples: "Creta, Brezza etc.", bike: false };
  if (/sedan/.test(key)) return { image: "/img/vt-sedan-560.webp", examples: "Dzire, City etc.", bike: false };
  if (/hatch/.test(key)) return { image: "/img/vt-hatchback-560.webp", examples: "i10, Swift etc.", bike: false };
  return { image: "/img/vt-hatchback-560.webp", examples: "", bike: false };
}

/** "Hatchback (i10, Swift etc.)" */
export function vehicleLabel(t: { name: string; slug?: string } | null | undefined): string {
  if (!t) return "";
  const { examples } = vehicleMeta(t);
  const name = titleCase(t.name);
  return examples ? `${name} (${examples})` : name;
}

/** "Today" / "Sat" + "13 Sep" for a YYYY-MM-DD day (IST). */
export function dayParts(iso: string, today: string): { top: string; bottom: string } {
  const d = new Date(`${iso}T12:00:00+05:30`);
  const bottom = d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });
  const top = iso === today ? "Today" : d.toLocaleDateString("en-GB", { weekday: "short", timeZone: "Asia/Kolkata" });
  return { top, bottom };
}

/** The bookable days: today + the policy window (capped at two weeks). */
export function bookingDays(today: string, maxAdvanceDays: number | undefined): string[] {
  const horizon = Math.max(1, Math.min(maxAdvanceDays || 7, 14));
  const start = new Date(`${today}T12:00:00+05:30`);
  const out: string[] = [];
  for (let i = 0; i < horizon; i++) {
    const d = new Date(start.getTime() + i * 24 * 60 * 60 * 1000);
    out.push(new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(d));
  }
  return out;
}
