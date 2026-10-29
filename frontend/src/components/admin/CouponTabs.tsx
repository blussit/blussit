import { NavLink } from "react-router-dom";
import { cn } from "../../lib/cn";

/** Coupons ↔ Coupon Bundles, shown at the top of both admin pages. */
export function CouponTabs() {
  const tab = (to: string, label: string, end: boolean) => (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cn(
          "rounded-[9px] px-3 py-1.5 text-sm font-medium transition",
          isActive ? "bg-[#FFF4CD] text-[#0E1A33] ring-1 ring-[#0E1A33]" : "text-[#5F6878] hover:text-[#0E1A33]"
        )
      }
    >
      {label}
    </NavLink>
  );
  return (
    <div className="inline-flex rounded-[12px] border border-[var(--color-card-border)] bg-white p-1" role="tablist" aria-label="Coupon Sections">
      {tab("/admin/coupons", "Coupons", true)}
      {tab("/admin/coupons/bundles", "Coupon Bundles", false)}
    </div>
  );
}
