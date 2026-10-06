/**
 * The customer app's frame (theme v2). Phones and tablets get an app-style
 * top bar and a four-tab bottom bar (Home, Bookings, Plans, Profile — active
 * tab in blue); from 1024px a left sidebar takes over and the tab bar goes.
 *
 * Kept separate from the staff DashboardShell on purpose: the staff portals
 * keep their own look, and nothing here can leak into them. It carries the
 * same two account duties every logged-in shell has — the blocking
 * MandatoryGates, and new-notification toasts (customers poll only while
 * the tab is visible, once a minute, and get no socket here).
 */
import { type ReactNode, useEffect, useRef } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Bell, Building2, CalendarDays, CarFront, Gift, Home, LifeBuoy, LogOut, Plus, Tag, User as UserIcon, type LucideIcon } from "lucide-react";
import { MandatoryGates } from "../shared/MandatoryGates";
import { notificationApi, subscriptionApi } from "../../api/engagement";
import { isSocietyPass, societyPassPath } from "../../lib/passState";
import { useAuth } from "../../context/AuthContext";
import { useConfirm } from "../../context/ConfirmContext";
import { useToast } from "../../context/ToastContext";
import { notificationTargetPath } from "../../lib/notifications";
import { playNotificationChime } from "../../lib/notificationSound";
import { cn } from "../../lib/cn";
import type { Notification } from "../../types";
import { useCustomerTheme } from "./useCustomerTheme";

interface Tab {
  label: string;
  to: string;
  icon: LucideIcon;
  /** Extra path prefixes that light this tab up. */
  also?: string[];
}

const TABS: Tab[] = [
  { label: "Home", to: "/app", icon: Home },
  { label: "Bookings", to: "/app/bookings", icon: CalendarDays },
  { label: "Plans", to: "/app/subscriptions", icon: Gift },
  { label: "Profile", to: "/app/profile", icon: UserIcon, also: ["/app/garage", "/app/settings", "/app/addresses", "/app/support", "/app/notifications", "/app/offers"] },
];

const SIDEBAR: Tab[] = [
  { label: "Home", to: "/app", icon: Home },
  { label: "My Bookings", to: "/app/bookings", icon: CalendarDays },
  { label: "My Plans", to: "/app/subscriptions", icon: Gift },
  { label: "My Garage", to: "/app/garage", icon: CarFront },
  { label: "Offers", to: "/app/offers", icon: Tag },
  { label: "Help & Support", to: "/app/support", icon: LifeBuoy },
  { label: "Profile", to: "/app/profile", icon: UserIcon, also: ["/app/settings", "/app/addresses", "/app/notifications"] },
];

/** Society residents get a "Society" tab (their society's hub, in-app),
 *  placed right after Plans. One society per resident is the norm; with
 *  more, it opens the newest one and the others stay reachable from Plans. */
function withSociety(tabs: Tab[], societyPath: string | null, label: string): Tab[] {
  if (!societyPath) return tabs;
  const at = tabs.findIndex((t) => t.to === "/app/subscriptions") + 1;
  return [...tabs.slice(0, at), { label, to: societyPath, icon: Building2, also: ["/app/society"] }, ...tabs.slice(at)];
}

function isActive(tab: Tab, pathname: string): boolean {
  if (tab.to === "/app") return pathname === "/app" || pathname === "/app/";
  return [tab.to, ...(tab.also || [])].some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

/** New notifications → in-page toast (+ chime, + OS popup if already allowed). */
function useNotificationToasts() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const lastSeenRef = useRef<number | null>(null);
  const baselinedRef = useRef(false);
  const { data } = useQuery({
    queryKey: ["notifications", "unread"],
    queryFn: () => notificationApi.list({ page: 1, page_size: 20 }),
    refetchInterval: 60000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });

  useEffect(() => {
    if (!data?.data?.length || !user) return;
    const storageKey = `notif_last_seen_${user.id}`;
    if (lastSeenRef.current === null && !baselinedRef.current) {
      let stored: string | null = null;
      try {
        stored = localStorage.getItem(storageKey);
      } catch {
        stored = null;
      }
      lastSeenRef.current = stored ? Number(stored) : 0;
    }
    // Never toast on the first load — only on what arrives afterwards.
    const firstRun = !baselinedRef.current;
    baselinedRef.current = true;
    const fresh = data.data
      .filter((n) => new Date(n.created_at).getTime() > (lastSeenRef.current ?? 0))
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());

    if (!firstRun && fresh.length) {
      const go = (n: Notification) => {
        const path = notificationTargetPath(n, user.role);
        if (path) navigate(path);
      };
      for (const n of fresh) {
        const path = notificationTargetPath(n, user.role);
        toast.push({ tone: n.notification_type === "complaint" ? "warning" : "info", title: n.title, message: n.message, onClick: path ? () => go(n) : undefined });
      }
      playNotificationChime();
      if (typeof Notification !== "undefined" && Notification.permission === "granted") {
        fresh.slice(0, 5).forEach((n) => {
          const native = new Notification(n.title, { body: n.message, tag: n.id });
          native.onclick = () => {
            window.focus();
            go(n);
          };
        });
      }
    }
    if (fresh.length || firstRun) {
      const newest = Math.max(0, ...data.data.map((n) => new Date(n.created_at).getTime()));
      lastSeenRef.current = Math.max(lastSeenRef.current ?? 0, newest);
      try {
        localStorage.setItem(storageKey, String(lastSeenRef.current));
      } catch {
        // storage blocked — toasts just baseline per session
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, user]);

  return data?.unread_count || 0;
}

export function CustomerShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const confirm = useConfirm();
  const { pathname } = useLocation();
  const unread = useNotificationToasts();
  const { data: subs } = useQuery({ queryKey: ["my-subscriptions"], queryFn: subscriptionApi.mySubscriptions, staleTime: 60_000 });
  const societySub = (subs || [])
    .filter((s) => isSocietyPass(s) && societyPassPath(s))
    .sort((a, b) => new Date(b.end_date || 0).getTime() - new Date(a.end_date || 0).getTime())[0];
  const societyPath = societySub ? societyPassPath(societySub) : null;
  const tabs = withSociety(TABS, societyPath, "Society");
  const sidebar = withSociety(SIDEBAR, societyPath, "My Society");
  // The booking flow is shared with the public /book page and styles itself;
  // it renders here exactly as it does there (no v2 re-pointing, no vars).
  const inBookingFlow = /^\/app\/book\/?$/.test(pathname);
  // Theme v2 hangs off <body> so dialogs, toasts and the confirm box —
  // mounted outside this tree — match the pages too.
  useCustomerTheme(!inBookingFlow);
  const firstName = user?.full_name?.trim().split(/\s+/)[0] || "";
  const initial = (user?.full_name || "?").trim().charAt(0).toUpperCase();

  const askLogout = async () => {
    if (await confirm({ title: "Log Out?", message: "You'll sign back in with an OTP on your phone.", confirmLabel: "Log Out", tone: "default" })) logout();
  };

  return (
    <div className="min-h-screen bg-white text-[#0E1A33] [&_a]:cursor-pointer [&_button]:cursor-pointer">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-[248px] flex-col border-r border-[#E4E9F1] bg-white lg:flex">
        <Link to="/" className="flex h-16 shrink-0 items-center px-6" aria-label="Blussit home">
          <img src="/img/blussit-logo-480.webp" alt="BLUSSIT" className="h-auto w-[132px] object-contain object-left" />
        </Link>
        <div className="px-4 pb-2 pt-1">
          <Link to="/app/book" className="flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-[#0A66F0] text-sm font-semibold text-white shadow-[0_8px_18px_-8px_rgba(10,102,240,0.55)] transition-colors hover:bg-[#0857D0]">
            <Plus className="h-4 w-4" /> Book A Wash
          </Link>
        </div>
        <nav className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-3 py-3">
          {sidebar.map((item) => {
            const active = isActive(item, pathname);
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/app"}
                className={cn(
                  "flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-[14px] font-medium transition-colors",
                  active ? "bg-[#E8F0FE] font-semibold text-[#0A66F0]" : "text-[#5F6878] hover:bg-[#F5F8FC] hover:text-[#0E1A33]"
                )}
              >
                <item.icon className="h-[18px] w-[18px]" />
                {item.label}
              </NavLink>
            );
          })}
        </nav>
        <div className="shrink-0 border-t border-[#E4E9F1] p-3">
          <button type="button" onClick={askLogout} className="flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium text-[#5F6878] transition-colors hover:bg-[#FDECEC] hover:text-[#C62828]">
            <LogOut className="h-[18px] w-[18px]" /> Log Out
          </button>
        </div>
      </aside>

      <div className="lg:pl-[248px]">
        {/* Top bar */}
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b border-[#E4E9F1] bg-white/95 px-4 backdrop-blur sm:px-6 lg:h-16 lg:px-10">
          <Link to="/app" className="lg:hidden" aria-label="Blussit home">
            <img src="/img/blussit-logo-480.webp" alt="BLUSSIT" className="h-auto w-[96px] object-contain" />
          </Link>
          <div className="hidden lg:block" />
          <div className="flex items-center gap-1.5 sm:gap-2.5">
            <Link
              to="/app/notifications"
              aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
              className="relative flex h-10 w-10 items-center justify-center rounded-full text-[#0E1A33] transition-colors hover:bg-[#EEF3FA]"
            >
              <Bell className="h-5 w-5" />
              {unread > 0 && <span className="absolute right-2 top-2 h-2.5 w-2.5 rounded-full border-2 border-white bg-[#E5484D]" />}
            </Link>
            <Link to="/app/profile" className="flex items-center gap-2 rounded-full border border-[#E4E9F1] py-1 pl-1 pr-1 transition-colors hover:border-[#CFDCF0] sm:pr-3">
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[#E8F0FE] text-sm font-bold text-[#0A66F0]">{initial}</span>
              <span className="hidden max-w-[140px] truncate text-sm font-medium sm:block">{firstName}</span>
            </Link>
          </div>
        </header>

        <main className="min-h-[calc(100vh-3.5rem)] px-4 pb-28 pt-5 sm:px-6 lg:min-h-[calc(100vh-4rem)] lg:px-10 lg:pb-12 lg:pt-8">
          <MandatoryGates />
          <div className="mx-auto w-full max-w-[1120px]">{children}</div>
        </main>
      </div>

      {/* Phone / tablet tab bar */}
      <nav
        aria-label="Main"
        className={cn(
          "fixed inset-x-0 bottom-0 z-40 grid border-t border-[#E4E9F1] bg-white/95 px-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-1.5 backdrop-blur lg:hidden",
          tabs.length > 4 ? "grid-cols-5" : "grid-cols-4"
        )}
      >
        {tabs.map((tab) => {
          const active = isActive(tab, pathname);
          return (
            <Link
              key={tab.to}
              to={tab.to}
              aria-current={active ? "page" : undefined}
              className={cn("flex flex-col items-center gap-1 rounded-xl py-1.5 text-[11px] transition-colors", active ? "font-semibold text-[#0A66F0]" : "font-medium text-[#8A94A6] hover:text-[#0E1A33]")}
            >
              <span className={cn("flex h-7 w-12 items-center justify-center rounded-full transition-colors", active && "bg-[#E8F0FE]")}>
                <tab.icon className="h-[20px] w-[20px]" strokeWidth={active ? 2.3 : 1.9} />
              </span>
              {tab.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
