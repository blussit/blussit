import { Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { adminInboxApi, whatsappCrmApi } from "../../api/admin";
import {
  AlertTriangle,
  Banknote,
  Building,
  Building2,
  CalendarDays,
  CalendarOff,
  CalendarX2,
  Car,
  ClipboardList,
  CreditCard,
  FileClock,
  Gift,
  Handshake,
  IndianRupee,
  Landmark,
  Layers,
  LayoutDashboard,
  ListChecks,
  Mail,
  MapPin,
  MessageCircle,
  MessageSquareQuote,
  Megaphone,
  Package,
  SlidersHorizontal,
  Star,
  Ticket,
  User,
  Users,
  Wrench,
} from "lucide-react";
import { DashboardShell, type NavEntry, type NavGroup, type NavItem } from "../../components/layout/DashboardShell";
import { CustomerLookup } from "../../components/shared/CustomerLookup";

// Dashboard, WhatsApp and Bookings stay on top as plain links; everything else
// sits in a collapsible group (every page appears exactly once).
const dashboard: NavItem = { label: "Dashboard", to: "/admin", icon: LayoutDashboard, end: true };
const whatsapp: NavItem = { label: "WhatsApp", to: "/admin/whatsapp", icon: MessageCircle };
const bookings: NavItem = { label: "Bookings", to: "/admin/bookings", icon: ListChecks };
const contactMessages: NavItem = { label: "Contact Messages", to: "/admin/contact-messages", icon: Mail };
const planRequests: NavItem = { label: "Custom Plan Requests", to: "/admin/plan-enquiries", icon: ClipboardList };
const coverageRequests: NavItem = { label: "Coverage Requests", to: "/admin/coverage-requests", icon: MapPin };

const groups: NavGroup[] = [
  {
    label: "Plans & Societies",
    icon: CreditCard,
    children: [
      { label: "Purchased Plans", to: "/admin/purchased-plans", icon: CreditCard },
      { label: "Custom Plans", to: "/admin/custom-plans", icon: Layers },
      { label: "Societies", to: "/admin/societies", icon: Building },
      { label: "Society Planner", to: "/admin/society-planner", icon: CalendarDays },
      { label: "Society Schedule Settings", to: "/admin/society-schedule-settings", icon: SlidersHorizontal },
      { label: "Society Plans", to: "/admin/society-plans", icon: Layers },
      { label: "Subscription Plans", to: "/admin/subscription-plans", icon: Gift },
    ],
  },
  {
    label: "Money",
    icon: Landmark,
    children: [
      { label: "Finance", to: "/admin/finance", icon: Landmark },
      { label: "Captain Salary", to: "/admin/captain-salary", icon: Banknote },
      { label: "Franchise", to: "/admin/franchise", icon: Handshake },
      { label: "Cancellation Charges", to: "/admin/charges", icon: CalendarX2 },
      { label: "Coupons", to: "/admin/coupons", icon: Ticket },
      { label: "Settings & Pricing", to: "/admin/pricing", icon: IndianRupee },
    ],
  },
  {
    label: "Operations",
    icon: Building2,
    children: [
      { label: "Service Centers", to: "/admin/service-centers", icon: Building2 },
      { label: "Service Zones", to: "/admin/service-zones", icon: MapPin },
      { label: "Holidays", to: "/admin/holidays", icon: CalendarOff },
      { label: "Vehicle Types", to: "/admin/vehicle-types", icon: Car },
      { label: "Services", to: "/admin/services", icon: Wrench },
      { label: "Combo Offers", to: "/admin/combo-offers", icon: Package },
    ],
  },
  { label: "People", icon: Users, children: [{ label: "Users", to: "/admin/users", icon: Users }] },
  {
    label: "Customer Care",
    icon: AlertTriangle,
    children: [
      { label: "Complaints", to: "/admin/complaints", icon: AlertTriangle },
      { label: "Reviews", to: "/admin/reviews", icon: Star },
      contactMessages,
      planRequests,
      coverageRequests,
    ],
  },
  {
    label: "Website",
    icon: Megaphone,
    children: [
      { label: "Homepage", to: "/admin/homepage", icon: Megaphone },
      { label: "Website Reviews & FAQs", to: "/admin/website-content", icon: MessageSquareQuote },
    ],
  },
  {
    label: "System",
    icon: FileClock,
    children: [
      { label: "Audit Logs", to: "/admin/audit-logs", icon: FileClock },
      { label: "Profile", to: "/admin/profile", icon: User },
    ],
  },
];

const withMarkers = (group: NavGroup, markers: Record<string, Partial<NavItem>>): NavGroup => ({
  ...group,
  children: group.children.map((c) => (markers[c.to] ? { ...c, ...markers[c.to] } : c)),
});

export default function AdminLayout() {
  // Paused while the tab is hidden (react-query default); the server
  // answers it from a 15 s shared cache, and marking a chat read
  // invalidates it directly.
  const { data: waBadge } = useQuery({
    queryKey: ["wa-badge"],
    queryFn: whatsappCrmApi.badge,
    refetchInterval: 30000,
  });
  // New (unseen) contact messages / custom-plan requests / coverage requests:
  // a red dot on the link and on its group. Refetched on focus and on a live
  // "inbox" ping, so a fresh submission lights it without a reload.
  const { data: inbox } = useQuery({
    queryKey: ["admin-nav-badges"],
    queryFn: adminInboxApi.badges,
    refetchInterval: 30000,
    refetchOnWindowFocus: true,
  });
  const markers: Record<string, Partial<NavItem>> = {
    [contactMessages.to]: { dot: (inbox?.contact_messages || 0) > 0 },
    [planRequests.to]: { dot: (inbox?.plan_enquiries || 0) > 0 },
    [coverageRequests.to]: { dot: (inbox?.coverage_requests || 0) > 0 },
  };
  const items: NavEntry[] = [
    dashboard,
    { ...whatsapp, badge: waBadge?.unread_conversations || 0 },
    bookings,
    ...groups.map((g) => withMarkers(g, markers)),
  ];
  return (
    <DashboardShell navItems={items} portalLabel="Super Admin" headerRight={<CustomerLookup />}>
      <Outlet />
    </DashboardShell>
  );
}
