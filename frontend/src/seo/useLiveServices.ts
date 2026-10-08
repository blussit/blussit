import { useQuery } from "@tanstack/react-query";
import { catalogApi } from "../api/catalog";
import { parseIncludes, priceView, titleCase } from "../components/public/landing/shared";
import type { Service } from "../types";
import type { ServiceContent } from "./content";

/** The public catalogue — the same query (and cache entry) the landing page
 *  uses, so moving between the two never refetches. Pre-filled at build time
 *  when the page is pre-rendered (src/prerender/entry.tsx). */
export function usePublicServices(): Service[] {
  const { data } = useQuery({ queryKey: ["public-services"], queryFn: () => catalogApi.services({ page_size: 100 }) });
  return (data?.data ?? []).filter((s) => s.is_active !== false);
}

export interface LiveService {
  price: number;
  original: number | null;
  /** Price differs by car size — show "From". */
  varies: boolean;
  minutes: number;
  includes: string[];
  offerTag: string | null;
  /** Slug the booking page understands (?service=). */
  bookSlug: string;
}

/** A page's service as the live catalogue prices it, or its fallback copy. */
export function liveFor(content: ServiceContent, all: Service[]): LiveService {
  const s = all.find((x) => x.slug === content.apiSlug);
  if (!s) {
    return {
      price: content.priceFallback,
      original: null,
      varies: content.vehicle === "car",
      minutes: content.minutesFallback,
      includes: content.includesFallback,
      offerTag: null,
      bookSlug: content.apiSlug,
    };
  }
  const pv = priceView(s);
  const items = parseIncludes(s.description).items;
  return {
    price: pv.final,
    original: pv.original,
    varies: pv.varies,
    minutes: s.duration_minutes || content.minutesFallback,
    includes: items.length ? items.map(titleCase) : content.includesFallback,
    offerTag: s.offer_tag?.trim() || null,
    bookSlug: s.slug,
  };
}
