import { Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Banknote, Building, CalendarDays, CalendarPlus, CalendarX2, CheckCircle2, CreditCard, Gift, Layers, LayoutDashboard, ListChecks, Package, SlidersHorizontal, Star, User, Users } from "lucide-react";
import { DashboardShell, type NavItem } from "../../components/layout/DashboardShell";
import { CustomerLookup } from "../../components/shared/CustomerLookup";
import { useFranchisePaysCaptains } from "../../components/payroll/useFranchisePaysCaptains";
import { bookingApi } from "../../api/booking";
import { useAuth } from "../../context/AuthContext";

const navItems: NavItem[] = [
  { label: "Dashboard", to: "/manager", icon: LayoutDashboard, end: true },
  { label: "New Booking", to: "/manager/new-booking", icon: CalendarPlus },
  { label: "Log A Done Job", to: "/manager/log-job", icon: CheckCircle2 },
  { label: "Booking Queue", to: "/manager/bookings", icon: ListChecks },
  { label: "Cancellation Charges", to: "/manager/charges", icon: CalendarX2 },
  { label: "Captains", to: "/manager/captains", icon: Users },
  { label: "Salary", to: "/manager/salary", icon: Banknote },
  { label: "Subscriptions", to: "/manager/subscribers", icon: Gift },
  { label: "Sell A Plan", to: "/manager/sell-plan", icon: CreditCard },
  { label: "Custom Plans", to: "/manager/custom-plans", icon: Layers },
  { label: "Societies", to: "/manager/societies", icon: Building },
  { label: "Society Planner", to: "/manager/society-planner", icon: CalendarDays },
  { label: "Society Schedule Settings", to: "/manager/society-schedule-settings", icon: SlidersHorizontal },
  { label: "Inventory", to: "/manager/inventory", icon: Package },
  { label: "Complaints", to: "/manager/complaints", icon: AlertTriangle },
  { label: "Reviews", to: "/manager/reviews", icon: Star },
  { label: "Profile", to: "/manager/profile", icon: User },
];

export default function ManagerLayout() {
  // A center whose captains the franchise partner pays has no Salary page.
  const franchisePays = useFranchisePaysCaptains();
  // A red count on Booking Queue while captains have finished steps from
  // outside the boundary / without GPS that nobody has looked at yet. The key
  // starts with "center-bookings", so every live booking ping refreshes it.
  const { user } = useAuth();
  const centerId = user?.service_center_id || "";
  const warned = useQuery({
    queryKey: ["center-bookings", centerId, "location-warnings-count"],
    queryFn: () => bookingApi.forCenter(centerId, { scope: "location_warnings", page: 1, page_size: 1 }),
    enabled: !!centerId,
    select: (page) => page.meta.total,
    refetchInterval: 120_000,
  });
  const items = navItems.map((i) => (i.to === "/manager/bookings" ? { ...i, badge: warned.data ?? 0 } : i));
  return (
    <DashboardShell navItems={franchisePays ? items.filter((i) => i.to !== "/manager/salary") : items} portalLabel="Manager" headerRight={<CustomerLookup />}>
      <Outlet />
    </DashboardShell>
  );
}
