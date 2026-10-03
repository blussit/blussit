import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useNavigationType } from "react-router-dom";
import { Armchair, ArrowRight, ArrowUp, Bike, Car, Check, ChevronLeft, ChevronRight, Clock, Droplets, Leaf, Sparkles, type LucideIcon } from "lucide-react";
import { scrollToSection } from "../../../lib/sections";
import { useAuth } from "../../../context/AuthContext";
import { catalogApi } from "../../../api/catalog";
import type { Service } from "../../../types";
import { INR, groupServices, parseIncludes, priceView, serviceImage, titleCase, type ServiceGroup } from "./shared";

const NAVY = "#0E1A33";
const BLUE = "#0A66F0";
const MUTED = "#5F6878";
const LAST_SERVICE_KEY = "blussit:lastServiceCard";
// Landing order (founder's call); anything else follows in admin order.
const CARD_ORDER = [/deep clean/i, /waterless/i, /star/i, /jet/i, /bike|scooty|scooter/i];
const cardRank = (name: string) => {
  const i = CARD_ORDER.findIndex((re) => re.test(name));
  return i === -1 ? CARD_ORDER.length : i;
};

/** `row`: one swipeable row — 4 cards per view on desktop, two-and-a-bit on
 * a phone, with dots — and "View All Services" opens every service as a grid
 * right here (there is no separate services page). Without `row`: the grid. */
export function ServicesShowcase({ row, id, title, subtitle }: { row?: boolean; id?: string; title?: string; subtitle?: string }) {
  const [expanded, setExpanded] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["public-services"],
    queryFn: () => catalogApi.services({ page_size: 100 }),
  });
  const groups = groupServices((data?.data ?? []).filter((s) => s.is_active !== false))
    .map((g, i) => ({ g, i }))
    .sort((a, b) => cardRank(a.g.primary.name) - cardRank(b.g.primary.name) || a.i - b.i)
    .map(({ g }) => g);
  const shown = groups;
  const asRow = !!row && !expanded;

  if (isLoading) return <SkeletonSection id={id} asRow={!!row} />;
  if (shown.length === 0) return null;

  return (
    <section id={id || "services"} className="bg-white pb-10 pt-6 md:pb-14 md:pt-8 lg:pt-14">
      <div className="container-page">
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <p className="text-[14px] font-extrabold uppercase tracking-[0.1em] md:text-[15px]" style={{ color: BLUE }}>
              Our services
            </p>
            {title && (
              <h2
                className="mt-2 font-display text-[23px] font-extrabold leading-[1.15] tracking-[-0.02em] sm:text-[30px] lg:text-[38px]"
                style={{ color: NAVY }}
              >
                {title}
              </h2>
            )}
            {subtitle && (
              <p className="mt-2 max-w-[640px] text-[14px] leading-[1.55] md:text-[15px]" style={{ color: MUTED }}>
                {subtitle}
              </p>
            )}
          </div>
          {row && (
            <button
              type="button"
              onClick={() => {
                setExpanded((v) => !v);
                if (expanded) scrollToSection(id || "services");
              }}
              aria-expanded={expanded}
              className="inline-flex shrink-0 items-center gap-1 text-[13px] font-semibold hover:underline md:text-[15px]"
              style={{ color: BLUE }}
            >
              {expanded ? (
                <>
                  Show Less
                  <ArrowUp className="h-4 w-4" strokeWidth={2.4} />
                </>
              ) : (
                <>
                  <span className="hidden sm:inline">View All Services</span>
                  <span className="sm:hidden">View All</span>
                  <ArrowRight className="h-4 w-4" strokeWidth={2.4} />
                </>
              )}
            </button>
          )}
        </div>

        {asRow ? (
          <ServiceRow groups={shown} />
        ) : (
          <div className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-5 lg:grid-cols-4">
            {shown.map((g, i) => (
              <ServiceCard key={g.primary.id} group={g} index={i} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function ServiceRow({ groups }: { groups: ServiceGroup<Service>[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState(0);
  const [pages, setPages] = useState(1);
  const [canScroll, setCanScroll] = useState(false);
  const navType = useNavigationType();

  // Pages = how many "screens" of cards there are; recomputed on resize.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const measure = () => {
      const n = Math.max(1, Math.ceil(el.scrollWidth / el.clientWidth - 0.05));
      setPages(n);
      setCanScroll(el.scrollWidth > el.clientWidth + 4);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [groups.length]);

  // Coming back from a booking page: put the card they opened back in view.
  useEffect(() => {
    if (navType !== "POP") return;
    let lastId: string | null = null;
    try {
      lastId = sessionStorage.getItem(LAST_SERVICE_KEY);
    } catch {
      /* storage blocked */
    }
    if (lastId) document.getElementById(`service-card-${lastId}`)?.scrollIntoView({ behavior: "auto", inline: "start", block: "nearest" });
  }, [navType]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const max = el.scrollWidth - el.clientWidth;
    setPage(max <= 0 ? 0 : Math.round((el.scrollLeft / max) * (pages - 1)));
  };

  const go = (dir: 1 | -1) => scrollRef.current?.scrollBy({ left: dir * scrollRef.current.clientWidth * 0.9, behavior: "smooth" });

  return (
    <div className="relative mt-4 md:mt-5">
      {canScroll && (
        <>
          <ArrowButton side="left" disabled={page === 0} onClick={() => go(-1)} />
          <ArrowButton side="right" disabled={page >= pages - 1} onClick={() => go(1)} />
        </>
      )}
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="hide-scrollbar -mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 pb-4 pt-1 sm:mx-0 sm:gap-5 sm:px-0"
        style={{ scrollPaddingInline: "16px" }}
      >
        {groups.map((g, i) => (
          <div key={g.primary.id} className="w-[57%] shrink-0 snap-start sm:w-[calc((100%-40px)/3)] lg:w-[calc((100%-60px)/4)]">
            <ServiceCard group={g} index={i} />
          </div>
        ))}
      </div>
      {pages > 1 && (
        <div className="mt-2 flex justify-center gap-1.5 lg:hidden" aria-hidden="true">
          {Array.from({ length: pages }, (_, i) => (
            <span
              key={i}
              className="h-1.5 rounded-full transition-all"
              style={{ width: i === page ? 18 : 6, backgroundColor: i === page ? BLUE : "#D5DDEA" }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ArrowButton({ side, disabled, onClick }: { side: "left" | "right"; disabled: boolean; onClick: () => void }) {
  const Icon = side === "left" ? ChevronLeft : ChevronRight;
  return (
    <button
      type="button"
      aria-label={side === "left" ? "Previous services" : "Next services"}
      onClick={onClick}
      disabled={disabled}
      className={`absolute top-[38%] z-10 hidden h-11 w-11 -translate-y-1/2 items-center justify-center rounded-full border border-[#E4E9F1] bg-white shadow-[0_6px_18px_rgba(15,30,60,0.10)] transition-opacity disabled:pointer-events-none disabled:opacity-0 lg:flex ${
        side === "left" ? "-left-5" : "-right-5"
      }`}
      style={{ color: BLUE }}
    >
      <Icon className="h-5 w-5" strokeWidth={2.6} />
    </button>
  );
}

/** One line of "what you get", from the admin description. */
/** What the service includes, from the admin description: its list items
 * (shown as ticks), or a single sentence when that's how it's written. */
function included(description?: string | null): { items: string[]; summary: string | null } {
  const { summary, items } = parseIncludes(description);
  if (items.length) return { items: items.slice(0, 3), summary: null };
  return { items: [], summary: summary || "Professional doorstep care for your vehicle." };
}

function serviceIcon(name: string): { Icon: LucideIcon; color: string } {
  const n = name.toLowerCase();
  if (/waterless|eco/.test(n)) return { Icon: Leaf, color: "#12A150" };
  if (/bike|scooter/.test(n)) return { Icon: Bike, color: BLUE };
  if (/interior|seat|vacuum/.test(n)) return { Icon: Armchair, color: BLUE };
  if (/deep|detail|polish|wax/.test(n)) return { Icon: Sparkles, color: BLUE };
  if (/jet|pressure/.test(n)) return { Icon: Droplets, color: BLUE };
  return { Icon: Car, color: BLUE };
}

function ServiceCard({ group, index }: { group: ServiceGroup<Service>; index: number }) {
  const s = group.primary;
  const pv = priceView(s);
  const { user } = useAuth();
  const navigate = useNavigate();
  const { Icon, color } = serviceIcon(s.name);
  const includes = included(s.description);
  const off = pv.original && pv.original > pv.final ? Math.round(((pv.original - pv.final) / pv.original) * 100) : null;
  const tag = s.offer_tag?.trim();

  const book = () => {
    try {
      sessionStorage.setItem(LAST_SERVICE_KEY, s.id);
    } catch {
      /* storage blocked */
    }
    navigate(`${user?.role === "customer" ? "/app/book" : "/book"}?service=${encodeURIComponent(s.slug)}`);
  };

  return (
    <article
      id={`service-card-${s.id}`}
      onClick={book}
      className="group flex h-full cursor-pointer flex-col overflow-hidden rounded-[16px] border border-[#EDF0F5] bg-white shadow-[0_6px_20px_rgba(15,30,60,0.06)] transition-all duration-300 hover:-translate-y-1 hover:shadow-[0_12px_28px_rgba(15,30,60,0.10)] md:rounded-[18px]"
    >
      <div className="relative">
        <div className="aspect-[16/10] w-full overflow-hidden bg-[#F3F6FA]">
          <img
            src={serviceImage(s, index)}
            alt={titleCase(s.name)}
            loading="lazy"
            decoding="async"
            className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-[1.04]"
          />
        </div>
        {(tag || off != null) && (
          <div className="absolute left-2.5 right-2.5 top-2.5 flex items-start justify-between gap-1 sm:left-2 sm:right-2 sm:top-2 md:left-3 md:right-3 md:top-3">
            {tag ? (
              <span className="truncate rounded-full px-2 py-0.5 text-[10px] font-extrabold uppercase tracking-wide sm:px-2 sm:py-0.5 sm:text-[9.5px] md:px-2.5 md:py-1 md:text-[10.5px]" style={{ backgroundColor: "#FFD21F", color: NAVY }}>
                {tag}
              </span>
            ) : (
              <span />
            )}
            {off != null && (
              <span className="shrink-0 rounded-full bg-[#E11D48] px-2 py-0.5 text-[10px] font-extrabold text-white sm:px-2 sm:py-0.5 sm:text-[9.5px] md:px-2.5 md:py-1 md:text-[10.5px]">
                {off}% OFF
              </span>
            )}
          </div>
        )}
        <span className="absolute -bottom-[18px] left-3.5 flex h-9 w-9 items-center justify-center rounded-full bg-white shadow-[0_4px_14px_rgba(15,30,60,0.14)] sm:-bottom-4 sm:left-3 sm:h-8 sm:w-8 md:-bottom-5 md:left-4 md:h-10 md:w-10">
          <Icon className="h-[18px] w-[18px] sm:h-4 sm:w-4 md:h-5 md:w-5" style={{ color }} strokeWidth={2.2} />
        </span>
      </div>

      <div className="flex flex-1 flex-col px-3.5 pb-3.5 pt-6 sm:px-3 sm:pb-3 sm:pt-6 md:px-4 md:pb-4 md:pt-7">
        <h3 className="text-[16px] font-bold leading-snug sm:text-[15px] md:text-[17px]" style={{ color: NAVY }}>
          {titleCase(s.name)}
        </h3>
        {includes.items.length ? (
          <ul className="mt-2 space-y-1 sm:mt-2 sm:space-y-1 md:mt-2.5 md:space-y-1.5">
            {includes.items.map((item) => (
              <li key={item} className="flex items-start gap-1.5 text-[13px] leading-[1.35] sm:gap-1.5 sm:text-[12px] md:gap-2 md:text-[13.5px]" style={{ color: "#3A4456" }}>
                <Check className="mt-[1px] h-3.5 w-3.5 shrink-0 sm:h-3.5 sm:w-3.5 md:h-4 md:w-4" style={{ color: BLUE }} strokeWidth={3} />
                <span className="line-clamp-2 md:line-clamp-1">{titleCase(item)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1.5 line-clamp-2 text-[13px] leading-[1.45] sm:text-[12px] md:text-[13.5px]" style={{ color: MUTED }}>
            {includes.summary}
          </p>
        )}
        <p className="mt-2.5 flex items-center gap-1.5 text-[12px] sm:mt-2 sm:text-[11.5px] md:mt-3 md:text-[13px]" style={{ color: MUTED }}>
          <Clock className="h-3.5 w-3.5 shrink-0 sm:h-3.5 sm:w-3.5 md:h-4 md:w-4" strokeWidth={2} />
          {s.duration_minutes || 45} mins
        </p>
        <div className="mt-auto flex items-end justify-between gap-2 pt-3 sm:pt-3 md:pt-4">
          {/* Original price struck out on top, the price you pay big and bold. */}
          <div className="min-w-0 whitespace-nowrap leading-none">
            {pv.original != null && pv.original > pv.final && (
              <p className="mb-1 text-[13px] font-medium text-[#94A3B8] line-through sm:text-[12px] md:text-[14px]">{INR(pv.original)}</p>
            )}
            <p>
              {pv.varies && (
                <span className="mr-1 text-[12px] sm:text-[11px] md:text-[13px]" style={{ color: MUTED }}>
                  From
                </span>
              )}
              <span className="text-[22px] font-extrabold tracking-[-0.01em] sm:text-[20px] md:text-[26px]" style={{ color: NAVY }}>
                {INR(pv.final)}
              </span>
            </p>
          </div>
          <span
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-white transition-transform group-hover:translate-x-0.5 sm:h-8 sm:w-8 md:h-10 md:w-10"
            style={{ backgroundColor: BLUE }}
            aria-hidden="true"
          >
            <ArrowRight className="h-4 w-4 sm:h-4 sm:w-4 md:h-[18px] md:w-[18px]" strokeWidth={2.5} />
          </span>
        </div>
      </div>
    </article>
  );
}

function SkeletonSection({ id, asRow }: { id?: string; asRow: boolean }) {
  return (
    <section id={id || "services"} className="bg-white py-10 md:py-14">
      <div className="container-page animate-pulse">
        <div className="h-3 w-24 rounded bg-[#EEF1F5]" />
        <div className="mt-3 h-8 w-72 max-w-full rounded bg-[#EEF1F5]" />
        <div className={`mt-8 gap-3 sm:gap-5 ${asRow ? "flex overflow-hidden" : "grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4"}`}>
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className={`overflow-hidden rounded-[18px] border border-[#EDF0F5] ${asRow ? "w-[57%] shrink-0 sm:w-[calc((100%-40px)/3)] lg:w-[calc((100%-60px)/4)]" : ""}`}
            >
              <div className="aspect-[16/10] bg-[#F3F6FA]" />
              <div className="space-y-2 p-4">
                <div className="h-4 w-3/4 rounded bg-[#EEF1F5]" />
                <div className="h-3 w-full rounded bg-[#EEF1F5]" />
                <div className="h-3 w-1/2 rounded bg-[#EEF1F5]" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
