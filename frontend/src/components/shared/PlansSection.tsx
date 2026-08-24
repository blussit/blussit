import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Check } from "lucide-react";
import { subscriptionApi } from "../../api/engagement";
import { Section, SectionHeading } from "./Section";
import { Badge, Button, Card, PageLoader } from "../ui";

export function PlansSection() {
  const navigate = useNavigate();
  const { data, isLoading } = useQuery({ queryKey: ["public-plans"], queryFn: () => subscriptionApi.plans(true) });

  if (!isLoading && !data?.length) return null;

  return (
    <Section id="subscriptions" className="bg-[var(--color-surface)]">
      <SectionHeading
        eyebrow="Subscribe & save"
        title="Never worry about booking again"
        description="Bundle your regular washes into a monthly plan and save on every visit."
      />
      <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
        {isLoading ? (
          <PageLoader />
        ) : (
          (data || []).map((plan) => (
            <Card key={plan.id} className={`relative p-6 ${plan.is_popular ? "border-2 border-[var(--color-primary)]" : ""}`}>
              {plan.is_popular && (
                <Badge tone="primary" className="absolute -top-3 left-1/2 -translate-x-1/2">
                  Most popular
                </Badge>
              )}
              <h3 className="text-lg font-semibold text-[var(--color-text-primary)]">{plan.name}</h3>
              <p className="mt-1 text-sm capitalize text-[var(--color-text-secondary)]">{plan.billing_cycle} billing</p>
              <div className="mt-5 flex items-baseline gap-1">
                <span className="font-mono-num text-3xl font-bold text-[var(--color-text-primary)]">₹{plan.discounted_price ?? plan.price}</span>
                <span className="text-sm text-[var(--color-text-secondary)]">/{plan.billing_cycle}</span>
              </div>
              <ul className="mt-6 space-y-2.5 text-sm text-[var(--color-text-secondary)]">
                <li className="flex items-center gap-2">
                  <Check className="h-4 w-4 text-[var(--color-secondary)]" /> {plan.total_service_count} services included
                </li>
                <li className="flex items-center gap-2">
                  <Check className="h-4 w-4 text-[var(--color-secondary)]" /> Priority scheduling
                </li>
                <li className="flex items-center gap-2">
                  <Check className="h-4 w-4 text-[var(--color-secondary)]" /> Cancel anytime
                </li>
              </ul>
              <Button className="mt-6 w-full" variant={plan.is_popular ? "primary" : "outline"} onClick={() => navigate("/register")}>
                Choose plan
              </Button>
            </Card>
          ))
        )}
      </div>
    </Section>
  );
}
