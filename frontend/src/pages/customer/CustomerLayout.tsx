import { Outlet } from "react-router-dom";
import { CalendarPlus, Gift, Home, LayoutDashboard, LifeBuoy, ListChecks, MapPin, Plus, User } from "lucide-react";
import { DashboardShell, type NavItem } from "../../components/layout/DashboardShell";

// Quick-booking model: no "My vehicles" page (a booking is for a vehicle
// TYPE, nothing to register) and no "how do you want to book" menu — a
// matching pass is applied to a booking automatically by the server.
const navItems: NavItem[] = [
  { label: "Dashboard", to: "/app", icon: LayoutDashboard, end: true },
  { label: "Book A Service", to: "/app/book", icon: CalendarPlus },
  { label: "My bookings", to: "/app/bookings", icon: ListChecks },
  { label: "Subscriptions", to: "/app/subscriptions", icon: Gift },
  { label: "Addresses", to: "/app/addresses", icon: MapPin },
  { label: "Support", to: "/app/support", icon: LifeBuoy },
  { label: "Profile", to: "/app/profile", icon: User },
];

// Mobile bottom tab bar — index 2 is the raised gold "Book" action.
const bottomNav: NavItem[] = [
  { label: "Home", to: "/app", icon: Home, end: true },
  { label: "Bookings", to: "/app/bookings", icon: ListChecks },
  { label: "Book", to: "/app/book", icon: Plus },
  { label: "Plans", to: "/app/subscriptions", icon: Gift },
  { label: "Profile", to: "/app/profile", icon: User },
];

export default function CustomerLayout() {
  return (
    <DashboardShell navItems={navItems} portalLabel="Customer" brand bottomNav={bottomNav}>
      <Outlet />
    </DashboardShell>
  );
}
