import { Outlet } from "react-router-dom";
import { CalendarPlus, Car, Gift, LayoutDashboard, LifeBuoy, ListChecks, MapPin, User } from "lucide-react";
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

export default function CustomerLayout() {
  return (
    <DashboardShell navItems={navItems} portalLabel="Customer">
      <Outlet />
    </DashboardShell>
  );
}
