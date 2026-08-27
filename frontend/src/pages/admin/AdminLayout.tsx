import { Outlet } from "react-router-dom";
import {
  AlertTriangle,
  Building2,
  Car,
  FileClock,
  Gift,
  IndianRupee,
  LayoutDashboard,
  ListChecks,
  Mail,
  Megaphone,
  Package,
  Star,
  Ticket,
  User,
  Users,
  Wrench,
} from "lucide-react";
import { DashboardShell, type NavItem } from "../../components/layout/DashboardShell";

const navItems: NavItem[] = [
  { label: "Dashboard", to: "/admin", icon: LayoutDashboard, end: true },
  { label: "Bookings", to: "/admin/bookings", icon: ListChecks },
  { label: "Users", to: "/admin/users", icon: Users },
  { label: "Service centers", to: "/admin/service-centers", icon: Building2 },
  { label: "Services", to: "/admin/services", icon: Wrench },
  { label: "Vehicle types", to: "/admin/vehicle-types", icon: Car },
  { label: "Combo offers", to: "/admin/combo-offers", icon: Package },
  { label: "Homepage", to: "/admin/homepage", icon: Megaphone },
  { label: "Pricing & wallets", to: "/admin/pricing", icon: IndianRupee },
  { label: "Subscription plans", to: "/admin/subscription-plans", icon: Gift },
  { label: "Coupons", to: "/admin/coupons", icon: Ticket },
  { label: "Complaints", to: "/admin/complaints", icon: AlertTriangle },
  { label: "Reviews", to: "/admin/reviews", icon: Star },
  { label: "Contact messages", to: "/admin/contact-messages", icon: Mail },
  { label: "Audit logs", to: "/admin/audit-logs", icon: FileClock },
  { label: "Profile", to: "/admin/profile", icon: User },
];

export default function AdminLayout() {
  return (
    <DashboardShell navItems={navItems} portalLabel="Super Admin">
      <Outlet />
    </DashboardShell>
  );
}
