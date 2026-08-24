import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Car, Check, Layers, Sparkles, Wand2 } from "lucide-react";
import { catalogApi, comboOfferApi, homepageConfigApi } from "../../api/catalog";
import { Button, Card, PageLoader } from "../ui";

const serviceIcons = [Car, Sparkles, Layers, Wand2];

const howItWorksSteps = [
  { title: "Book your slot", description: "Choose date & time that suits you" },
  { title: "We arrive", description: "Our expert reaches your location" },
  { title: "We clean", description: "We clean your car with care" },
  { title: "You relax", description: "Sit back & relax while we do the job" },
];

export function FeatureStrip() {
  const navigate = useNavigate();
  const { data: config } = useQuery({ queryKey: ["homepage-config"], queryFn: homepageConfigApi.get });

  const { data: servicesData, isLoading: servicesLoading } = useQuery({
    queryKey: ["public-services"],
    queryFn: () => catalogApi.services({ page_size: 4 }),
  });

  const { data: featuredService } = useQuery({
    queryKey: ["hero-featured-service", config?.featured_service_id],
    queryFn: () => catalogApi.service(config!.featured_service_id!),
    enabled: !!config?.featured_service_id,
  });
  const { data: fallbackServices } = useQuery({
    queryKey: ["hero-fallback-service"],
    queryFn: () => catalogApi.services({ page_size: 1 }),
    enabled: !!config && !config.featured_service_id,
  });
  const headline = featuredService ?? fallbackServices?.data?.[0];
  const firstWash = headline?.discounted_price ?? 99;
  const regular = headline?.price ?? 299;
  const services = servicesData?.data || [];

  const { data: allCombos } = useQuery({ queryKey: ["combos-for-homepage"], queryFn: () => comboOfferApi.list(true) });
  const featuredCombos = (allCombos || []).filter((c) => config?.featured_combo_ids?.includes(c.id));

  return (
    <section id="services" className="py-16 md:py-24">
      <div className="container-page grid grid-cols-1 gap-10 lg:grid-cols-3">
        {/* Our services */}
        <div id="about">
          <p className="mb-1 text-xs font-bold uppercase tracking-widest text-[var(--color-secondary)]">Our services</p>
          <h2 className="font-display text-2xl font-bold text-[var(--color-text-primary)]">High quality care for your car</h2>
          {servicesLoading ? (
            <PageLoader />
          ) : (
            <div className="mt-6 space-y-5">
              {(services.length
                ? services
                : [
                    { id: "1", name: "Exterior Wash", description: "Deep exterior wash & shine" },
                    { id: "2", name: "Interior Cleaning", description: "Complete interior vacuum & cleaning" },
                    { id: "3", name: "Combo Wash", description: "Exterior + interior complete care" },
                    { id: "4", name: "Premium Detailing", description: "Advanced detailing for a showroom finish" },
                  ]
              ).map((s, i) => {
                const Icon = serviceIcons[i % serviceIcons.length];
                return (
                  <div key={s.id} className="flex items-start gap-3">
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--color-primary-light)] text-[var(--color-primary)]">
                      <Icon className="h-4 w-4" />
                    </span>
                    <div>
                      <p className="text-sm font-semibold text-[var(--color-text-primary)]">{s.name}</p>
                      <p className="text-xs text-[var(--color-text-secondary)]">{s.description || "Doorstep care, done right."}</p>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          <button
            onClick={() => navigate("/app/book")}
            className="mt-6 inline-flex items-center gap-1.5 text-sm font-semibold text-[var(--color-primary)] hover:underline"
          >
            View all services <ArrowRight className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* Pricing */}
        <div id="plans">
          <p className="mb-1 text-xs font-bold uppercase tracking-widest text-[var(--color-secondary)]">Pricing plans</p>
          <h2 className="font-display text-2xl font-bold text-[var(--color-text-primary)]">Simple pricing. No hidden charges.</h2>
          <Card className="mt-6 overflow-hidden">
            <div className="bg-[var(--color-primary)] px-6 py-5 text-white">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-white/70">First wash only</p>
              <p className="font-mono-num text-3xl font-bold text-[var(--color-secondary)]">₹{firstWash}</p>
              <p className="text-[11px] text-white/60">First-time customers only</p>
            </div>
            <div className="px-6 py-5">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Regular wash</p>
              <p className="font-mono-num text-3xl font-bold text-[var(--color-text-primary)]">
                ₹{regular} <span className="text-sm font-normal text-[var(--color-text-secondary)]">/ wash</span>
              </p>
              <ul className="mt-4 space-y-2 text-sm text-[var(--color-text-secondary)]">
                {["Exterior & Interior Cleaning", "Trained Professionals", "Water Efficient", "100% Satisfaction"].map((item) => (
                  <li key={item} className="flex items-center gap-2">
                    <Check className="h-4 w-4 text-[var(--color-secondary)]" /> {item}
                  </li>
                ))}
              </ul>
              <Button className="mt-5 w-full" onClick={() => navigate("/app/book")}>
                Book Now
              </Button>
            </div>
          </Card>
        </div>

        {/* How it works */}
        <div id="how-it-works">
          <p className="mb-1 text-xs font-bold uppercase tracking-widest text-[var(--color-secondary)]">How it works</p>
          <h2 className="font-display text-2xl font-bold text-[var(--color-text-primary)]">Passion for cars. Commitment to you.</h2>
          <div className="mt-6 space-y-5">
            {howItWorksSteps.map((step, i) => (
              <div key={step.title} className="flex items-start gap-3">
                <span className="font-mono-num flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--color-secondary-light)] text-sm font-bold text-[var(--color-secondary)]">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div>
                  <p className="text-sm font-semibold text-[var(--color-text-primary)]">{step.title}</p>
                  <p className="text-xs text-[var(--color-text-secondary)]">{step.description}</p>
                </div>
              </div>
            ))}
          </div>
          <div className="mt-6 overflow-hidden rounded-2xl">
            <img
              src="https://images.unsplash.com/photo-1601362840469-51e4d8d58785?q=80&w=800&auto=format&fit=crop"
              alt="Captain cleaning a car doorstep"
              className="h-40 w-full object-cover"
            />
          </div>
          <Button variant="outline" className="mt-4 w-full" onClick={() => navigate("/app/book")}>
            Book Your Wash Now <ArrowRight className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {featuredCombos.length > 0 && (
        <div className="container-page mt-16">
          <p className="mb-1 text-xs font-bold uppercase tracking-widest text-[var(--color-secondary)]">Combo offers</p>
          <h2 className="mb-6 font-display text-2xl font-bold text-[var(--color-text-primary)]">Bundle up and save</h2>
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {featuredCombos.map((c) => (
              <Card key={c.id} className="p-5">
                <p className="font-semibold text-[var(--color-text-primary)]">{c.name}</p>
                {c.description && <p className="mt-1 text-sm text-[var(--color-text-secondary)]">{c.description}</p>}
                <p className="font-mono-num mt-3 text-2xl font-bold text-[var(--color-secondary)]">₹{c.discounted_price ?? c.price}</p>
                <Button className="mt-4 w-full" onClick={() => navigate("/app/book")}>
                  Book this combo
                </Button>
              </Card>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
