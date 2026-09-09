import { Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CalendarClock, LayoutDashboard, User, Wallet } from "lucide-react";
import { DashboardShell, type NavItem } from "../../components/layout/DashboardShell";
import { bookingPolicyApi } from "../../api/catalog";

const BASE_NAV_ITEMS: NavItem[] = [
  { label: "Jobs", to: "/captain", icon: LayoutDashboard, end: true },
  { label: "Attendance", to: "/captain/attendance", icon: CalendarClock },
  { label: "Profile", to: "/captain/profile", icon: User },
];

export default function CaptainLayout() {
  // Wallet balance gating is off by default (no real payment gateway to top
  // up against yet — see AdminPricingPage's toggle), so there's nothing
  // useful for a captain to do on an earnings/wallet page while it's off.
  // Hide the nav entry until an admin turns it on; the route itself also
  // redirects away, see CaptainEarningsPage.
  const { data: policy } = useQuery({ queryKey: ["booking-policy"], queryFn: bookingPolicyApi.get });
  const navItems = policy?.wallet_gating_enabled
    ? [...BASE_NAV_ITEMS.slice(0, 2), { label: "Earnings", to: "/captain/earnings", icon: Wallet }, ...BASE_NAV_ITEMS.slice(2)]
    : BASE_NAV_ITEMS;

  // Captains are the most mobile-first users in the whole system — same
  // brand shell + bottom tab bar treatment as the customer portal (no
  // raised center button; every tab is a plain destination).
  return (
    <DashboardShell navItems={navItems} portalLabel="Captain" brand bottomNav={navItems}>
      <Outlet />
    </DashboardShell>
  );
}
