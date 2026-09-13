import { Outlet } from "react-router-dom";
import { CalendarPlus, Car, Gift, Home, LayoutDashboard, LifeBuoy, ListChecks, MapPin, Plus, User } from "lucide-react";
import { DashboardShell, type NavItem } from "../../components/layout/DashboardShell";
import { useQuery } from "@tanstack/react-query";
import { subscriptionApi } from "../../api/engagement";

const navItems: NavItem[] = [
  { label: "Dashboard", to: "/app", icon: LayoutDashboard, end: true },
  { label: "Book A Service", to: "/app/book", icon: CalendarPlus },
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

// Tapping the gold + only asks HOW to book when a usable pass exists.
const centerMenu = [
  { label: "Book With My Plan", description: "Use Your Subscription Washes. Fastest Way.", icon: Gift, to: "/app/book?mode=plan" },
  { label: "Normal Booking", description: "Pick A Service And Pay Per Wash", icon: CalendarPlus, to: "/app/book" },
];

export default function CustomerLayout() {
  const { data: subscriptions } = useQuery({
    queryKey: ["my-subscriptions"],
    queryFn: subscriptionApi.mySubscriptions,
  });
  const hasUsablePlan = (subscriptions || []).some((s) => s.status === "active" && (s.remaining_service_count ?? 0) > 0);

  return (
    <DashboardShell navItems={navItems} portalLabel="Customer" brand bottomNav={bottomNav} centerMenu={hasUsablePlan ? centerMenu : undefined}>
      <Outlet />
    </DashboardShell>
  );
}
