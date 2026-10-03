/**
 * Offers inside the app: every service the admin has tagged with an offer
 * (the same list the landing's offer bar shows), each one tap from booking,
 * plus the monthly plans.
 */
import { Link } from "react-router-dom";
import { ArrowRight, Gift, Tag } from "lucide-react";
import { useActiveOffers } from "../../components/public/LaunchOfferPopup";
import { serviceImage, titleCase } from "../../components/public/landing/shared";
import { btn, card, PageHeader } from "../../components/customer/ui";

export default function OffersPage() {
  const offers = useActiveOffers();

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <PageHeader back="/app/profile" title="Offers" />

      {offers.length ? (
        <div className="grid gap-4 sm:grid-cols-2">
          {offers.map((o, i) => (
            <div key={o.service.id} className={`${card} overflow-hidden`}>
              <div className="relative h-36 bg-[#EEF3FA]">
                <img src={serviceImage(o.service, i)} alt="" loading="lazy" className="h-full w-full object-cover" />
                <span className="absolute left-3 top-3 inline-flex items-center gap-1 rounded-full bg-[#FFD21F] px-2.5 py-1 text-xs font-bold text-[#0E1A33]">
                  <Tag className="h-3.5 w-3.5" /> {o.tag}
                </span>
              </div>
              <div className="flex items-end justify-between gap-3 p-4">
                <div className="min-w-0">
                  <p className="truncate font-display text-[17px] font-bold text-[#0E1A33]">{titleCase(o.service.name)}</p>
                  <p className="mt-0.5 text-sm text-[#5F6878]">
                    {o.samePriceForAllCars ? "" : "From "}
                    <span className="tabular-nums text-lg font-bold text-[#0E1A33]">₹{Math.round(o.price)}</span>
                    {o.original != null && <span className="ml-1.5 tabular-nums text-[#8A94A6] line-through">₹{Math.round(o.original)}</span>}
                  </p>
                </div>
                <Link to={`/app/book?service=${encodeURIComponent(o.service.slug || o.service.id)}`} className={btn("primary", "sm", "rounded-full px-4")}>
                  Book <ArrowRight className="h-4 w-4" />
                </Link>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed border-[#CFDCF0] bg-[#F7FAFF] px-6 py-8 text-center">
          <p className="font-display text-base font-bold text-[#0E1A33]">No Offers Right Now</p>
          <p className="mt-1 text-sm text-[#5F6878]">New ones show up here first.</p>
        </div>
      )}

      <Link to="/app/subscriptions" className={`${card} flex items-center gap-3.5 p-4 transition-shadow hover:shadow-[0_12px_32px_-16px_rgba(14,26,51,0.28)]`}>
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px] bg-[#E8F0FE] text-[#0A66F0]">
          <Gift className="h-5 w-5" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] font-semibold text-[#0E1A33]">Monthly Care Plans</span>
          <span className="block text-xs text-[#5F6878]">Save on regular washes.</span>
        </span>
        <ArrowRight className="h-4 w-4 shrink-0 text-[#0A66F0]" />
      </Link>
    </div>
  );
}
