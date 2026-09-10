import { Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CalendarClock, LayoutDashboard, User, Wallet } from "lucide-react";
import { DashboardShell, type NavItem } from "../../components/layout/DashboardShell";
import { bookingPolicyApi } from "../../api/catalog";
import { CaptainI18nProvider, useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";

function LanguageToggle() {
  const { language, setLanguage } = useCaptainTranslation();
  return (
    <button
      onClick={() => setLanguage(language === "en" ? "hi" : "en")}
      className="flex cursor-pointer items-center gap-1 rounded-full border border-gray-200 px-3 py-1 text-xs font-semibold text-gray-600 transition-colors hover:border-[#E8A900] hover:text-black"
    >
      🌐 {language === "en" ? "EN / हिन्दी" : "हिन्दी / EN"}
    </button>
  );
}

function CaptainLayoutInner() {
  const { data: policy } = useQuery({ queryKey: ["booking-policy"], queryFn: bookingPolicyApi.get });
  const { t } = useCaptainTranslation();

  const BASE_NAV_ITEMS: NavItem[] = [
    { label: t("captain.nav.jobs"), to: "/captain", icon: LayoutDashboard, end: true },
    { label: t("captain.nav.attendance"), to: "/captain/attendance", icon: CalendarClock },
    { label: t("captain.nav.profile"), to: "/captain/profile", icon: User },
  ];

  const navItems = policy?.wallet_gating_enabled
    ? [...BASE_NAV_ITEMS.slice(0, 2), { label: t("captain.nav.earnings"), to: "/captain/earnings", icon: Wallet }, ...BASE_NAV_ITEMS.slice(2)]
    : BASE_NAV_ITEMS;

  return (
    <DashboardShell navItems={navItems} portalLabel={t("captain.nav.captain")} brand bottomNav={navItems} headerRight={<LanguageToggle />}>
      <Outlet />
    </DashboardShell>
  );
}

export default function CaptainLayout() {
  return (
    <CaptainI18nProvider>
      <CaptainLayoutInner />
    </CaptainI18nProvider>
  );
}
