import { useQuery } from "@tanstack/react-query";
import { Star } from "lucide-react";
import { contentApi } from "../../api/catalog";
import { Section, SectionHeading } from "./Section";
import { Card, PageLoader } from "../ui";

export function TestimonialsSection() {
  const { data, isLoading } = useQuery({ queryKey: ["public-testimonials"], queryFn: contentApi.testimonials });
  const testimonials = data?.length
    ? data
    : [
        { id: "1", customer_name: "Ananya Rao", rating: 5, comment: "Booked a foam wash before work — the captain arrived on time and my SUV looked brand new." },
        { id: "2", customer_name: "Rohit Malviya", rating: 5, comment: "Loved the transparency in pricing. No surprises, just a great clean." },
        { id: "3", customer_name: "Priya Sethi", rating: 4, comment: "Convenient and professional. The monthly plan has been totally worth it." },
      ];

  return (
    <Section>
      <SectionHeading eyebrow="Testimonials" title="What our customers say" />
      {isLoading ? (
        <PageLoader />
      ) : (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
          {testimonials.map((t) => (
            <Card key={t.id} className="p-6">
              <div className="flex gap-0.5">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Star key={i} className={`h-4 w-4 ${i < t.rating ? "fill-amber-400 text-amber-400" : "text-gray-200"}`} />
                ))}
              </div>
              <p className="mt-4 text-sm text-[var(--color-text-secondary)]">"{t.comment}"</p>
              <p className="mt-4 text-sm font-semibold text-[var(--color-text-primary)]">{t.customer_name}</p>
            </Card>
          ))}
        </div>
      )}
    </Section>
  );
}

export function StatsSection() {
  const { data } = useQuery({ queryKey: ["public-stats"], queryFn: contentApi.publicStats });
  const stats = [
    { label: "Vehicles serviced", value: data?.vehicles_serviced ?? 12500 },
    { label: "Happy customers", value: data?.happy_customers ?? 8200 },
    { label: "Service centers", value: data?.service_centers ?? 14 },
    { label: "Average rating", value: data?.average_rating ?? 4.8 },
  ];

  return (
    <Section className="bg-[var(--color-primary)]">
      <div className="grid grid-cols-2 gap-8 text-center text-white md:grid-cols-4">
        {stats.map((stat) => (
          <div key={stat.label}>
            <p className="font-mono-num text-3xl font-bold md:text-4xl">{stat.value.toLocaleString()}+</p>
            <p className="mt-2 text-sm text-white/70">{stat.label}</p>
          </div>
        ))}
      </div>
    </Section>
  );
}
