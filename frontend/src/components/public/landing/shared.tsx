import type { ReactNode } from "react";
import type { VehicleTypeOption } from "../../../types";

/* ------------------------------------------------------------------ */
/* Landing-page primitives — monochrome, no accent colour.            */
/* ------------------------------------------------------------------ */

export const INR = (n: number) => `₹${Math.round(n).toLocaleString("en-IN")}`;

export function SectionShell({
  id,
  className = "",
  children,
}: {
  id?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className={`py-12 sm:py-16 lg:py-20 ${className}`}>
      <div className="container-page">{children}</div>
    </section>
  );
}

export function SectionHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="max-w-xl">
        <h2 className="font-display text-[28px] font-bold leading-[1.1] tracking-tight text-black sm:text-[36px]">
          {title}
        </h2>
        {subtitle && (
          <p className="mt-2 text-[15px] leading-relaxed text-neutral-600 sm:text-[16px]">
            {subtitle}
          </p>
        )}
      </div>
      {action}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Pricing                                                            */
/* ------------------------------------------------------------------ */

type Priced = {
  price: number;
  discounted_price?: number | null;
  vehicle_type_prices?: Record<string, number>;
  vehicle_type_discounted_prices?: Record<string, number>;
  original_price?: number | null;
  vehicle_type_original_prices?: Record<string, number>;
  vehicle_types?: string[];
};

export interface PriceView {
  /** Lowest amount a visitor can pay across the vehicle types offered. */
  final: number;
  /** Struck-through price: the regular price behind a first-time offer, else the MRP. */
  original: number | null;
  /** Prices differ by vehicle type — prefix the amount with "from". */
  varies: boolean;
  /** Set only when the first-time-customer price is what's headlined. */
  offerLabel: string | null;
}

/**
 * Resolves the same per-vehicle-type rules the booking wizard uses
 * (`vehicle_type_prices[vt] ?? price`, `vehicle_type_discounted_prices[vt]
 * ?? discounted_price`, `vehicle_type_original_prices[vt] ?? original_price`)
 * and picks the cheapest combination to headline.
 */
export function priceView(p: Priced): PriceView {
  const ids = p.vehicle_types?.length ? p.vehicle_types : ["__base__"];
  const entries = ids.map((id) => {
    const regular = p.vehicle_type_prices?.[id] ?? p.price;
    const raw = p.vehicle_type_discounted_prices?.[id] ?? p.discounted_price ?? null;
    const offer = raw != null && raw > 0 && raw < regular ? raw : null;
    const mrpRaw = p.vehicle_type_original_prices?.[id] ?? p.original_price ?? null;
    const mrp = mrpRaw != null && mrpRaw > regular ? mrpRaw : null;
    return { regular, offer, mrp, final: offer ?? regular };
  });
  const best = entries.reduce((a, b) => (b.final < a.final ? b : a));
  return {
    final: best.final,
    original: best.offer != null ? best.regular : best.mrp,
    varies: new Set(entries.map((e) => e.final)).size > 1,
    offerLabel: best.offer != null ? "First booking offer" : null,
  };
}

/** Price + struck-through original for one specific vehicle type (wizard cards). */
export function priceForType(p: Priced, vehicleTypeId: string): { price: number; original: number | null } {
  const price = p.vehicle_type_prices?.[vehicleTypeId] ?? p.price;
  const mrp = p.vehicle_type_original_prices?.[vehicleTypeId] ?? p.original_price ?? null;
  return { price, original: mrp != null && mrp > price ? mrp : null };
}

/** One sellable product on the website: a service plus its sibling variants (if any). */
export interface ServiceGroup<T extends GroupableService = GroupableService> {
  /** Cheapest variant — what the card headlines and links to. */
  primary: T;
  /** All variants incl. primary, in catalogue order (length 1 for plain services). */
  variants: T[];
}

type GroupableService = Priced & { id: string; is_addon?: boolean; variant_group?: string | null };

/** Collapses variant siblings into one group each and drops add-ons. Keeps catalogue order. */
export function groupServices<T extends GroupableService>(services: T[]): ServiceGroup<T>[] {
  const groups: ServiceGroup<T>[] = [];
  const byGroup = new Map<string, ServiceGroup<T>>();
  for (const s of services) {
    if (s.is_addon) continue;
    const key = s.variant_group?.trim();
    if (!key) {
      groups.push({ primary: s, variants: [s] });
      continue;
    }
    const existing = byGroup.get(key);
    if (existing) {
      existing.variants.push(s);
      if (priceView(s).final < priceView(existing.primary).final) existing.primary = s;
    } else {
      const g = { primary: s, variants: [s] };
      byGroup.set(key, g);
      groups.push(g);
    }
  }
  return groups;
}

/** Add-ons that can be booked with `service` (vehicle types overlap, or either side is unrestricted). */
export function addonsFor<T extends GroupableService & { vehicle_types?: string[] }>(service: T, all: T[]): T[] {
  return all.filter((a) => {
    if (!a.is_addon) return false;
    if (!a.vehicle_types?.length || !service.vehicle_types?.length) return true;
    return a.vehicle_types.some((vt) => service.vehicle_types!.includes(vt));
  });
}

/* ------------------------------------------------------------------ */
/* Service helpers                                                    */
/* ------------------------------------------------------------------ */

const stripBullet = (s: string) => s.replace(/^[\s\-–—•*✓✔·]+/, "").trim();

/**
 * Turns the admin-entered description into a "what's included" list.
 * One item per line (or "a, b, c") → checklist; a single sentence → summary.
 */
export function parseIncludes(description?: string | null): { summary: string | null; items: string[] } {
  if (!description) return { summary: null, items: [] };
  const lines = description.split(/\r?\n|[•|;]/).map(stripBullet).filter(Boolean);
  if (lines.length >= 2) return { summary: null, items: lines };
  const single = lines[0] ?? "";
  // "a, b, c" (no closing period) reads as a list; "Deep clean of x, y and z." is a sentence.
  const parts = single.split(",").map(stripBullet).filter(Boolean);
  if (parts.length >= 2 && !/[.!?]$/.test(single) && parts.every((x) => x.length <= 40)) {
    return { summary: null, items: parts };
  }
  return { summary: single || null, items: [] };
}

const LOCAL_IMAGES = {
  // Real Blussit shoot photos (from the newimage batch, converted to WebP)
  jet: "/service-jet.webp", // captain pressure-washing an SUV
  foam: "/service-star.webp", // captain + full kit beside foamed SUV, branded
  interior: "/service-deepclean.webp", // interior vacuum, branded
  polish: "/service-4.webp", // wax/polish on bonnet
  dashboard: "/service-5.webp",
  bike: "/service-bike.webp", // captain washing a bike, branded
  waterless: "/service-waterless.webp", // waterless wash spray + microfibre
};

/** Admin-uploaded image first; otherwise a bundled photo matched by service name. */
export function serviceImage(s: { name: string; image?: string | null }, index: number): string {
  if (s.image) return s.image;
  const n = s.name.toLowerCase();
  if (/waterless/.test(n)) return LOCAL_IMAGES.waterless;
  if (/bike|scooter|two.?wheeler|chain/.test(n)) return LOCAL_IMAGES.bike;
  if (/dashboard/.test(n)) return LOCAL_IMAGES.dashboard;
  if (/wax|polish|ceramic|coat|paint/.test(n)) return LOCAL_IMAGES.polish;
  if (/deep|detail|interior|vacuum|seat|cabin|sanit/.test(n)) return LOCAL_IMAGES.interior;
  if (/star|foam/.test(n)) return LOCAL_IMAGES.foam;
  if (/jet|exterior|wash|clean/.test(n)) return LOCAL_IMAGES.jet;
  const all = Object.values(LOCAL_IMAGES);
  return all[index % all.length];
}

const isBikeType = (t: VehicleTypeOption) => /bike|scooter|two/i.test(`${t.slug} ${t.name}`);

/** "Car", "Bike" or "Car & Bike" for the vehicle types an item is offered on. */
export function vehicleLabel(ids: string[] | undefined, types: VehicleTypeOption[] | undefined): string | null {
  if (!types?.length) return null;
  const mine = ids?.length ? types.filter((t) => ids.includes(t.id)) : types;
  if (!mine.length) return null;
  const bikes = mine.filter(isBikeType).length;
  const cars = mine.length - bikes;
  return cars && bikes ? "Car & Bike" : bikes ? "Bike" : "Car";
}

/** "Hatchback, Sedan, SUV +2 more" */
export function vehicleNames(ids: string[] | undefined, types: VehicleTypeOption[] | undefined, max = 3): string | null {
  if (!types?.length) return null;
  const mine = ids?.length ? types.filter((t) => ids.includes(t.id)) : types;
  if (!mine.length) return null;
  const names = mine.map((t) => t.name);
  if (names.length <= max) return names.join(", ");
  return `${names.slice(0, max).join(", ")} +${names.length - max} more`;
}
