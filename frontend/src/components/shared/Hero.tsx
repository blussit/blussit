import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { Clock3, Droplets, MapPin, Megaphone, Phone, QrCode, ShieldCheck, Sparkles, UserCheck } from "lucide-react";
import { catalogApi, homepageConfigApi } from "../../api/catalog";
import { Button, Card } from "../ui";

const featureIcons = [
  { icon: Sparkles, label: "Doorstep Service" },
  { icon: Droplets, label: "Water Efficient" },
  { icon: UserCheck, label: "Trained Experts" },
  { icon: ShieldCheck, label: "100% Satisfaction" },
];

export function Hero() {
  const navigate = useNavigate();
  const { data: config } = useQuery({ queryKey: ["homepage-config"], queryFn: homepageConfigApi.get });

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
  const firstWashPrice = headline?.discounted_price ?? 99;
  const regularPrice = headline?.price ?? 299;

  return (
    <section className="relative overflow-hidden bg-white">
      {config?.banner_active && config.banner_text && (
        <div className="bg-[var(--color-secondary)] px-4 py-2 text-center text-sm font-medium text-white">
          <span className="inline-flex items-center gap-1.5">
            <Megaphone className="h-3.5 w-3.5" /> {config.banner_text}
          </span>
        </div>
      )}
      <div className="container-page grid grid-cols-1 items-center gap-12 pb-10 pt-14 md:pt-20 lg:grid-cols-2">
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }}>
          <span className="mb-5 inline-flex items-center gap-2 rounded-full bg-[var(--color-secondary-light)] px-3.5 py-1.5 text-xs font-bold uppercase tracking-wide text-[var(--color-secondary)]">
            {config?.hero_badge_text || "Professional care"}
          </span>
          <h1 className="font-display text-4xl font-extrabold uppercase leading-[1.05] text-[var(--color-primary)] md:text-5xl lg:text-6xl">
            {config?.hero_headline || "Car wash at your doorstep"}
          </h1>
          <p className="mt-6 max-w-md text-base text-[var(--color-text-secondary)] md:text-lg">
            {config?.hero_subtext || "We come to you. You relax. We make your car shine like new."}
          </p>

          <div className="mt-7 flex flex-wrap gap-3">
            <div className="rounded-2xl bg-[var(--color-primary)] px-5 py-4 text-white shadow-[var(--shadow-soft)]">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-white/70">First wash only</p>
              <p className="font-mono-num text-3xl font-bold text-[var(--color-secondary)]">₹{firstWashPrice}</p>
              <p className="text-[11px] text-white/60">First-time customers only</p>
            </div>
            <div className="rounded-2xl border border-gray-200 px-5 py-4">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Regular price</p>
              <p className="font-mono-num text-3xl font-bold text-[var(--color-text-primary)]">₹{regularPrice}</p>
              <p className="text-[11px] text-[var(--color-text-secondary)]">Per wash</p>
            </div>
          </div>

          <div className="mt-7 flex flex-wrap gap-x-6 gap-y-3">
            {featureIcons.map((f) => (
              <div key={f.label} className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
                <f.icon className="h-4 w-4 text-[var(--color-secondary)]" /> {f.label}
              </div>
            ))}
          </div>

          <div className="mt-7 flex flex-wrap gap-3">
            <Button size="lg" onClick={() => navigate("/app/book")}>
              Book Your Wash Now
            </Button>
            <Button size="lg" variant="outline" onClick={() => document.getElementById("services")?.scrollIntoView({ behavior: "smooth" })}>
              View Services
            </Button>
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.6, delay: 0.1 }}
          className="relative flex justify-center"
        >
          <div className="relative aspect-square w-full max-w-md overflow-hidden rounded-full border-8 border-white shadow-[var(--shadow-lifted)]">
            <img
              src="https://images.unsplash.com/photo-1607860108855-64acf2078ed9?q=80&w=1200&auto=format&fit=crop"
              alt="Professional cleaning a premium car at a customer's home"
              className="h-full w-full object-cover"
            />
          </div>
          <Card className="absolute -bottom-2 -right-2 flex items-center gap-3 p-4 md:right-6">
            <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-[var(--color-primary-light)] text-[var(--color-primary)]">
              <QrCode className="h-6 w-6" />
            </span>
            <div>
              <p className="text-sm font-semibold text-[var(--color-text-primary)]">Scan QR to</p>
              <p className="text-xs text-[var(--color-text-secondary)]">Visit Our Website and Book Now</p>
            </div>
          </Card>
        </motion.div>
      </div>

      <div className="border-t border-gray-100 bg-[var(--color-primary)]">
        <div className="container-page flex flex-wrap items-center justify-between gap-4 py-4 text-sm text-white/85">
          <div className="flex items-center gap-2">
            <Phone className="h-4 w-4 text-[var(--color-secondary)]" /> Call Us +91 12345 67890
          </div>
          <div className="flex items-center gap-2">
            <Clock3 className="h-4 w-4 text-[var(--color-secondary)]" /> Working Hours 7:00 AM - 9:00 PM
          </div>
          <div className="flex items-center gap-2">
            <MapPin className="h-4 w-4 text-[var(--color-secondary)]" /> Indore, Madhya Pradesh
          </div>
        </div>
      </div>
    </section>
  );
}
