import { Outlet } from "react-router-dom";
import { AlertTriangle, Building, CalendarDays, CalendarPlus, CalendarX2, CheckCircle2, CreditCard, Gift, Layers, LayoutDashboard, ListChecks, Package, Star, User, Users } from "lucide-react";
import { DashboardShell, type NavItem } from "../../components/layout/DashboardShell";
import { CustomerLookup } from "../../components/shared/CustomerLookup";

const navItems: NavItem[] = [
  { label: "Dashboard", to: "/manager", icon: LayoutDashboard, end: true },
  { label: "New Booking", to: "/manager/new-booking", icon: CalendarPlus },
  { label: "Log A Done Job", to: "/manager/log-job", icon: CheckCircle2 },
  { label: "Booking Queue", to: "/manager/bookings", icon: ListChecks },
  { label: "Cancellation Charges", to: "/manager/charges", icon: CalendarX2 },
  { label: "Captains", to: "/manager/captains", icon: Users },
  { label: "Subscriptions", to: "/manager/subscribers", icon: Gift },
  { label: "Sell A Plan", to: "/manager/sell-plan", icon: CreditCard },
  { label: "Custom Plans", to: "/manager/custom-plans", icon: Layers },
  { label: "Societies", to: "/manager/societies", icon: Building },
  { label: "Society Planner", to: "/manager/society-planner", icon: CalendarDays },
  { label: "Inventory", to: "/manager/inventory", icon: Package },
  { label: "Complaints", to: "/manager/complaints", icon: AlertTriangle },
  { label: "Reviews", to: "/manager/reviews", icon: Star },
  { label: "Profile", to: "/manager/profile", icon: User },
];

export default function ManagerLayout() {
  return (
    <DashboardShell navItems={navItems} portalLabel="Manager" headerRight={<CustomerLookup />}>
      <Outlet />
    </DashboardShell>
  );
}
