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
  // fake reviews forever (no admin surface exists yet to add real ones).
  // Until real testimonials are seeded, the section simply doesn't render.
  if (!reviews.length) return null;

  return (
    <SectionShell id={id} className="border-t border-cream-line-soft bg-cream-deep">
      <SectionHeader title="What customers say" subtitle="Real doorstep washes, in their own words." />

      {/* Auto-advancing swipe row on phones, grid from tablet up */}
      <AutoRail className="mt-8 sm:mt-10" gridClassName="sm:grid-cols-2 lg:grid-cols-3 lg:gap-5">
        {reviews.map((r) => (
          <figure
            key={r.id}
            className="flex w-[80vw] max-w-[340px] shrink-0 snap-center sm:w-auto sm:max-w-none flex-col rounded-2xl border border-cream-line bg-white p-6"
          >
            <div className="flex gap-0.5" aria-label={`${r.rating} out of 5 stars`}>
              {Array.from({ length: 5 }).map((_, i) => (
                <Star
                  key={i}
                  className={`h-4 w-4 ${i < r.rating ? "fill-black text-black" : "fill-neutral-200 text-neutral-200"}`}
                />
              ))}
            </div>
            <blockquote className="mt-4 flex-1 text-[15px] leading-relaxed text-neutral-800">“{r.comment}”</blockquote>
            <figcaption className="mt-5 flex items-center gap-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-full bg-black text-[13px] font-bold text-white">
                {initials(r.customer_name)}
              </span>
              <span className="text-[14px] font-semibold text-black">{r.customer_name}</span>
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
