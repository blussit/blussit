import { useQuery } from "@tanstack/react-query";
import { Star } from "lucide-react";
import { contentApi } from "../../../api/catalog";
import { SectionHeader, SectionShell } from "./shared";
import { AutoRail } from "./AutoRail";

interface ReviewItem {
  id: string;
  customer_name: string;
  rating: number;
  comment: string;
}

export function ReviewsShowcase({ id = "reviews" }: { id?: string }) {
  const { data } = useQuery({ queryKey: ["public-testimonials"], queryFn: contentApi.testimonials });
  const reviews: ReviewItem[] = data || [];

  // Honesty over decoration: this section used to show three HARDCODED
  // fake reviews forever. Until real testimonials are seeded, it simply
  // doesn't render. No review/rating structured data either: these are
  // testimonials the business picks, and star markup built from a curated
  // list on our own business is "self-serving" to Google — not eligible,
  // and misleading as an average of all customers.
  if (!reviews.length) return null;

  return (
    <SectionShell id={id} className="border-t border-[#EDF0F5] bg-white">
      <SectionHeader title="What Customers Say" subtitle="Real doorstep washes, in their own words." />

      {/* Auto-advancing swipe row on phones, grid from tablet up */}
      <AutoRail className="mt-6 lg:mt-8" gridClassName="sm:grid-cols-2 lg:grid-cols-3 lg:gap-5">
        {reviews.map((r) => (
          <figure
            key={r.id}
            className="flex w-[80vw] max-w-[340px] shrink-0 snap-center sm:w-auto sm:max-w-none flex-col rounded-2xl border border-[#E4E9F1] bg-white p-6 shadow-[0_6px_22px_rgba(14,26,51,0.05)]"
          >
            <div className="flex gap-0.5" aria-label={`${r.rating} out of 5 stars`}>
              {Array.from({ length: 5 }).map((_, i) => (
                <Star
                  key={i}
                  className={`h-4 w-4 ${i < r.rating ? "fill-[#FFD21F] text-[#FFD21F]" : "fill-[#E4E9F1] text-[#E4E9F1]"}`}
                />
              ))}
            </div>
            <blockquote className="mt-4 flex-1 text-[15px] leading-relaxed text-[#0E1A33]">“{r.comment}”</blockquote>
            <figcaption className="mt-5 flex items-center gap-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-full bg-[#E8F0FE] text-[13px] font-bold text-[#0A66F0]">
                {initials(r.customer_name)}
              </span>
              <span className="text-[14px] font-semibold text-[#0E1A33]">{r.customer_name}</span>
            </figcaption>
          </figure>
        ))}
      </AutoRail>
    </SectionShell>
  );
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}
