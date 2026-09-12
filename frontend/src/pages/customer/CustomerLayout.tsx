import { Outlet } from "react-router-dom";
import { CalendarPlus, Car, Gift, Home, LayoutDashboard, LifeBuoy, ListChecks, MapPin, Plus, User } from "lucide-react";
import { DashboardShell, type NavItem } from "../../components/layout/DashboardShell";

const navItems: NavItem[] = [
  { label: "Dashboard", to: "/app", icon: LayoutDashboard, end: true },
  { label: "Book a service", to: "/app/book", icon: CalendarPlus },
  { label: "My bookings", to: "/app/bookings", icon: ListChecks },
  { label: "Subscriptions", to: "/app/subscriptions", icon: Gift },
  { label: "Vehicles", to: "/app/vehicles", icon: Car },
  { label: "Addresses", to: "/app/addresses", icon: MapPin },
  { label: "Support", to: "/app/support", icon: LifeBuoy },
  { label: "Profile", to: "/app/profile", icon: User },
];

// Mobile bottom tab bar — index 2 is the raised gold "Book" action.
// Plans takes the fourth slot, not Support (founder call): buying or using
// a pass is a recurring reason to open the app, while Support is a rare
// reactive visit — it stays one tap away in the sidebar and the profile.
const bottomNav: NavItem[] = [
  { label: "Home", to: "/app", icon: Home, end: true },
  { label: "Bookings", to: "/app/bookings", icon: ListChecks },
  { label: "Book", to: "/app/book", icon: Plus },
  { label: "Plans", to: "/app/subscriptions", icon: Gift },
  { label: "Profile", to: "/app/profile", icon: User },
];

// Tapping the gold + asks HOW to book — with a plan or pay-per-wash.
const centerMenu = [
  { label: "Book with my plan", description: "Use your subscription washes — fastest way", icon: Gift, to: "/app/book?mode=plan" },
  { label: "Normal booking", description: "Pick a service and pay per wash", icon: CalendarPlus, to: "/app/book" },
];

export default function CustomerLayout() {
  return (
    <DashboardShell navItems={navItems} portalLabel="Customer" brand bottomNav={bottomNav} centerMenu={centerMenu}>
      <Outlet />
    </DashboardShell>
  );
}
