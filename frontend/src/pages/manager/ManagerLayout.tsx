import { Outlet } from "react-router-dom";
import { AlertTriangle, BarChart3, CalendarPlus, Gift, LayoutDashboard, ListChecks, Package, Star, User, Users } from "lucide-react";
import { DashboardShell, type NavItem } from "../../components/layout/DashboardShell";

const navItems: NavItem[] = [
  { label: "Dashboard", to: "/manager", icon: LayoutDashboard, end: true },
  { label: "KPIs", to: "/manager/kpi", icon: BarChart3 },
  { label: "New booking", to: "/manager/new-booking", icon: CalendarPlus },
  { label: "Booking queue", to: "/manager/bookings", icon: ListChecks },
  { label: "Captains", to: "/manager/captains", icon: Users },
  { label: "Subscribers", to: "/manager/subscribers", icon: Gift },
  { label: "Inventory", to: "/manager/inventory", icon: Package },
  { label: "Complaints", to: "/manager/complaints", icon: AlertTriangle },
  { label: "Reviews", to: "/manager/reviews", icon: Star },
  { label: "Profile", to: "/manager/profile", icon: User },
];

export default function ManagerLayout() {
  return (
    <DashboardShell navItems={navItems} portalLabel="Manager">
      <Outlet />
    </DashboardShell>
  );
}
