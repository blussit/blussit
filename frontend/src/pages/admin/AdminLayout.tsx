import { Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { whatsappCrmApi } from "../../api/admin";
import {
  AlertTriangle,
  Building,
  Building2,
  CalendarDays,
  Car,
  ClipboardList,
  CreditCard,
  FileClock,
  Gift,
  IndianRupee,
  Layers,
  LayoutDashboard,
  ListChecks,
  Mail,
  MapPin,
  MessageCircle,
  Megaphone,
  Package,
  Star,
  Ticket,
  User,
  Users,
  Wrench,
} from "lucide-react";
import { DashboardShell, type NavItem } from "../../components/layout/DashboardShell";
import { CustomerLookup } from "../../components/shared/CustomerLookup";

const navItems: NavItem[] = [
  { label: "Dashboard", to: "/admin", icon: LayoutDashboard, end: true },
  { label: "Bookings", to: "/admin/bookings", icon: ListChecks },
  { label: "WhatsApp", to: "/admin/whatsapp", icon: MessageCircle },
  { label: "Purchased Plans", to: "/admin/purchased-plans", icon: CreditCard },
  { label: "Societies", to: "/admin/societies", icon: Building },
  { label: "Society Planner", to: "/admin/society-planner", icon: CalendarDays },
  { label: "Users", to: "/admin/users", icon: Users },
  { label: "Service Centers", to: "/admin/service-centers", icon: Building2 },
  { label: "Service Zones", to: "/admin/service-zones", icon: MapPin },
  { label: "Services", to: "/admin/services", icon: Wrench },
  { label: "Subscription Plans", to: "/admin/subscription-plans", icon: Gift },
  { label: "Society Plans", to: "/admin/society-plans", icon: Layers },
  { label: "Vehicle Types", to: "/admin/vehicle-types", icon: Car },
  { label: "Combo Offers", to: "/admin/combo-offers", icon: Package },
  { label: "Homepage", to: "/admin/homepage", icon: Megaphone },
  { label: "Settings & Pricing", to: "/admin/pricing", icon: IndianRupee },
  { label: "Coupons", to: "/admin/coupons", icon: Ticket },
  { label: "Complaints", to: "/admin/complaints", icon: AlertTriangle },
  { label: "Reviews", to: "/admin/reviews", icon: Star },
  { label: "Contact Messages", to: "/admin/contact-messages", icon: Mail },
  { label: "Custom Plan Requests", to: "/admin/plan-enquiries", icon: ClipboardList },
  { label: "Coverage Requests", to: "/admin/coverage-requests", icon: MapPin },
  { label: "Audit Logs", to: "/admin/audit-logs", icon: FileClock },
  { label: "Profile", to: "/admin/profile", icon: User },
];

export default function AdminLayout() {
  // Paused while the tab is hidden (react-query default); the server
  // answers it from a 15 s shared cache, and marking a chat read
  // invalidates it directly.
  const { data: waBadge } = useQuery({
    queryKey: ["wa-badge"],
    queryFn: whatsappCrmApi.badge,
    refetchInterval: 30000,
  });
  const items = navItems.map((item) =>
    item.to === "/admin/whatsapp" ? { ...item, badge: waBadge?.unread_conversations || 0 } : item,
  );
  return (
    <DashboardShell navItems={items} portalLabel="Super Admin" brand headerRight={<CustomerLookup />}>
      <Outlet />
    </DashboardShell>
  );
}
