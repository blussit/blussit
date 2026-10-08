import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import "./staffTheme.css";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { MandatoryGates } from "../shared/MandatoryGates";
import {
  Bell,
  BellOff,
  LogOut,
  Menu,
  User as UserIcon,
  X,
  type LucideIcon,
} from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { notificationApi } from "../../api/engagement";
import { useToast, type ToastTone } from "../../context/ToastContext";
import { notificationTargetPath } from "../../lib/notifications";
import { playNotificationChime } from "../../lib/notificationSound";
import { useLiveChannel } from "../../lib/socket";
import type { Notification } from "../../types";

export interface NavItem {
  label: string;
  to: string;
  icon: LucideIcon;
  end?: boolean;
  /** Small red count pill after the label (e.g. unread WhatsApp chats). */
  badge?: number;
}

function toastToneFor(notification: Notification): ToastTone {
  if (notification.notification_type === "complaint") return "warning";
  return "info";
}

export function DashboardShell({
  navItems,
  portalLabel,
  headerRight,
  children,
}: {
  navItems: NavItem[];
  portalLabel: string;
  /** Custom element to render in the header next to the notification bell. */
  headerRight?: ReactNode;
  children: ReactNode;
}) {
  const { user, logout } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();

  // "Last seen" is persisted per-user so switching accounts on the same
  // browser doesn't replay stale toasts, and survives a page reload.
  const lastSeenRef = useRef<number | null>(null);
  const hasBaselinedRef = useRef(false);

  // Tracked in state (not just read from `Notification.permission` inline)
  // so the header can reactively show a "notifications are blocked" hint —
  // otherwise there's no visible explanation for why the OS-level popups
  // just silently never appear.
  const [notifPermission, setNotifPermission] = useState<
    NotificationPermission | "unsupported"
  >(
    typeof Notification !== "undefined"
      ? Notification.permission
      : "unsupported",
  );

  const isStaff = !!user && user.role !== "customer";
  const notifQueryKey = ["notifications", "unread"];
  const { data: notifData } = useQuery({
    queryKey: notifQueryKey,
    // Wide enough that a realistic burst (the reminder sweep can flag
    // several different bookings in one pass, or the tab was backgrounded
    // across a few poll ticks) never exceeds it — the "last seen" watermark
    // below advances past everything it fetched, so anything beyond this
    // page size would get silently marked seen without ever toasting.
    queryFn: () => notificationApi.list({ page: 1, page_size: 20 }),
    // Live-pushed over the "user:{id}" WebSocket channel below — this is
    // now just the fallback for while the socket is reconnecting.
    // Staff (a few dozen people) keep polling in background tabs: a manager
    // on another tab must still get the OS-level alert. Customers (the
    // 100k) poll only while the tab is visible, and less often — otherwise
    // every idle customer tab is a request every 30 s, forever.
    refetchInterval: isStaff ? 30000 : 60000,
    refetchIntervalInBackground: isStaff,
    refetchOnWindowFocus: true,
  });

  // Staff only: every open socket holds a server slot, and a customer's
  // notifications are fine on the visible-tab poll above. Customers get a
  // socket only on the pages where live updates matter (an active
  // booking's detail page, the slot picker).
  useLiveChannel(user && isStaff ? `user:${user.id}` : null, () => {
    queryClient.invalidateQueries({ queryKey: notifQueryKey });
  });

  // Only ever from a click (the bell, or the "Enable alerts" chip below):
  // browsers penalize sites that prompt on page load.
  const requestNotificationPermission = () => {
    if (
      !isStaff ||
      typeof Notification === "undefined" ||
      Notification.permission !== "default"
    )
      return;
    Notification.requestPermission()
      .then(setNotifPermission)
      .catch(() => {});
  };

  useEffect(() => {
    if (!notifData?.data?.length || !user) return;
    const storageKey = `notif_last_seen_${user.id}`;
    if (lastSeenRef.current === null && !hasBaselinedRef.current) {
      const stored = localStorage.getItem(storageKey);
      lastSeenRef.current = stored ? Number(stored) : 0;
    }

    // Never toast on the very first load — that would flood the screen with
    // every already-unread notification from before this session started.
    // Just set the baseline and wait for genuinely new ones on later polls.
    const isFirstRun = !hasBaselinedRef.current;
    hasBaselinedRef.current = true;

    const fresh = notifData.data
      .filter(
        (n) => new Date(n.created_at).getTime() > (lastSeenRef.current ?? 0),
      )
      .sort(
        (a, b) =>
          new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      );

    if (!isFirstRun && fresh.length) {
      let touchedBookingIssue = false;
      const goToNotification = (n: Notification) => {
        const path = notificationTargetPath(n, user.role);
        if (path) navigate(path);
      };

      for (const n of fresh) {
        const path = notificationTargetPath(n, user.role);
        toast.push({
          tone: toastToneFor(n),
          title: n.title,
          message: n.message,
          onClick: path ? () => goToNotification(n) : undefined,
        });
        if (n.notification_type === "booking") touchedBookingIssue = true;
      }

      // Sound + a real OS-level notification fire every time, regardless of
      // whether this tab is focused — that's the whole point of a
      // system-level alert (same as YouTube's desktop notifications): it
      // has to show up on top of everything, not just as an in-page toast
      // you only see if you happen to be looking at this exact tab.
      playNotificationChime();
      if (
        typeof Notification !== "undefined" &&
        Notification.permission === "granted"
      ) {
        fresh.slice(0, 5).forEach((n) => {
          // requireInteraction keeps it on screen until dismissed instead of
          // auto-hiding after a few seconds (Chrome/Edge desktop honor this;
          // other browsers just ignore the option and behave as before).
          const native = new Notification(n.title, {
            body: n.message,
            tag: n.id,
            requireInteraction: true,
          });
          native.onclick = () => {
            window.focus();
            goToNotification(n);
          };
        });
      }

      // Let the manager's booking queue reflect a new flag immediately
      // instead of waiting for its own refetch timer.
      if (touchedBookingIssue) {
        queryClient.invalidateQueries({ queryKey: ["center-bookings"] });
      }
    }

    if (fresh.length) {
      const newest = Math.max(
        ...notifData.data.map((n) => new Date(n.created_at).getTime()),
      );
      lastSeenRef.current = newest;
      localStorage.setItem(storageKey, String(newest));
    } else if (isFirstRun) {
      const newest = Math.max(
        0,
        ...notifData.data.map((n) => new Date(n.created_at).getTime()),
      );
      lastSeenRef.current = newest;
      localStorage.setItem(storageKey, String(newest));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notifData, user]);

  // The staff portals (manager + admin — the only users of this shell) run
  // the v2 theme: white ground, navy #0E1A33 type, blue #0A66F0 for the
  // active nav / primary buttons / focus, #E4E9F1 hairlines, #E8F0FE tints,
  // and yellow #FFD21F kept for the ONE key CTA a page has. The --ui-*
  // tokens are read by the shared ui components (StatCard, Panel,
  // DataTable, Modal, Badge, Switch, EmptyState).
  // On <body>, not just this frame: popups, drawers and the confirm box are
  // rendered at the end of <body> and must get the same v2 look.
  useLayoutEffect(() => {
    document.body.classList.add("staff-v2");
    return () => document.body.classList.remove("staff-v2");
  }, []);
  const staffVars = {
    "--color-surface": "#FFFFFF",
    "--color-card-border": "#E4E9F1",
    "--color-primary": "#0A66F0",
    "--color-primary-dark": "#0852C2",
    "--color-primary-light": "#E8F0FE",
    "--color-secondary": "#FFD21F",
    "--color-secondary-light": "#FFF6CC",
    "--color-accent": "#0E1A33",
    "--color-accent-light": "#EEF3FA",
    "--color-text-primary": "#0E1A33",
    "--color-text-secondary": "#5F6878",
    "--radius-card": "14px",
    "--ui-ink": "#0E1A33",
    "--ui-muted": "#5F6878",
    "--ui-hover-line": "#0A66F0",
    "--ui-row-line": "#EEF2F7",
    "--ui-row-hover": "#F5F8FC",
    "--ui-tint": "#E8F0FE",
    "--ui-tint-ink": "#0A66F0",
    "--ui-accent": "#0A66F0",
    "--ui-icon-bg": "#EEF3FA",
    "--ui-success-bg": "#E7F6EC",
    color: "#0E1A33",
  } as React.CSSProperties;

  return (
    <div
      className="min-h-screen bg-[var(--color-surface)] [&_a]:cursor-pointer [&_button]:cursor-pointer"
      style={staffVars}
    >
      {/* Sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-[248px] transform flex-col border-r border-[#E4E9F1] bg-white transition-transform duration-200 md:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-[#E4E9F1] px-5">
          <Link to="/" className="flex w-full flex-col items-start" aria-label="Blussit home">
            <img src="/img/blussit-logo-480.webp" alt="BLUSSIT" className="h-auto w-[158px] object-contain object-left" />
          </Link>
          <button className="md:hidden" onClick={() => setSidebarOpen(false)}>
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="shrink-0 px-6 py-4">
          <span className="inline-flex items-center rounded-full bg-[#EEF3FA] px-2.5 py-1 text-xs font-semibold text-[#0E1A33]">
            {portalLabel}
          </span>
        </div>
        {/* min-h-0 is required alongside flex-1 or overflow-y-auto silently
            does nothing inside a flex column — without it this <nav> just
            keeps growing past the viewport and overlaps the logout button
            pinned below it. */}
        <nav className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-3 pb-3">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={() => setSidebarOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-[10px] px-3.5 py-2.5 text-[14px] font-medium transition-colors ${
                  isActive ? "bg-[#E8F0FE] font-semibold text-[#0A66F0]" : "text-[#5F6878] hover:bg-[#F3F6FA] hover:text-[#0E1A33]"
                }`
              }
            >
              <item.icon className="h-[18px] w-[18px]" />
              {item.label}
              {item.badge != null && item.badge > 0 && (
                <span className="ml-auto inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-[var(--color-error)] px-1.5 py-0.5 text-[10px] font-bold text-white">
                  {item.badge > 99 ? "99+" : item.badge}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto w-full shrink-0 border-t border-[#E4E9F1] p-4">
          <button
            onClick={logout}
            className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-[#5F6878] hover:bg-red-50 hover:text-[var(--color-error)]"
          >
            <LogOut className="h-[18px] w-[18px]" />
            Log Out
          </button>
        </div>
      </aside>

      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-gray-900/30 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Main content */}
      <div className="md:pl-[248px]">
        <header
          className="sticky top-0 z-20 flex h-16 items-center justify-between gap-3 border-b border-[#E4E9F1] bg-white px-4 sm:px-6 md:px-8"
        >
          <button className="shrink-0 rounded-lg p-1.5 md:hidden" aria-label="Open menu" onClick={() => setSidebarOpen(true)}>
            <Menu className="h-5 w-5" />
          </button>
          <div className="hidden md:block" />
          <div className="flex min-w-0 items-center gap-2 sm:gap-4">
            {headerRight}
            {isStaff && notifPermission === "default" && (
              <button
                type="button"
                onClick={requestNotificationPermission}
                className="hidden items-center gap-1.5 rounded-full border border-gray-200 px-3 py-1.5 text-xs font-semibold text-gray-700 hover:border-gray-400 sm:inline-flex"
              >
                <Bell className="h-3.5 w-3.5" /> Enable Alerts
              </button>
            )}
            <div className="group relative">
              <button
                onClick={() => {
                  requestNotificationPermission();
                  navigate("notifications");
                }}
                className="relative rounded-full p-2 text-gray-500 hover:bg-gray-100"
                aria-label="Notifications"
              >
                {notifPermission === "denied" ? (
                  <BellOff className="h-5 w-5" />
                ) : (
                  <Bell className="h-5 w-5" />
                )}
                {!!notifData?.unread_count && (
                  <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-[var(--color-error)]" />
                )}
              </button>
              {notifPermission === "denied" && (
                <div className="pointer-events-none absolute right-0 top-full z-30 mt-1 hidden w-56 rounded-lg bg-gray-900 p-2.5 text-xs text-white group-hover:block">
                  Desktop notifications are blocked for this site. Enable them
                  in your browser's site settings to get alerts outside this
                  tab.
                </div>
              )}
            </div>
            <button
              onClick={() => navigate("profile")}
              className={`flex shrink-0 items-center gap-2.5 rounded-full border py-1 pl-1 pr-1 sm:pr-3 ${
                "border-[#E4E9F1] hover:border-[#C9D6EA]"
              }`}
            >
              <span
                className={`flex h-7 w-7 items-center justify-center rounded-full ${
                  "bg-[#E8F0FE] text-[#0A66F0]"
                }`}
              >
                <UserIcon className="h-4 w-4" />
              </span>
              <span className="hidden text-sm font-medium text-[var(--color-text-primary)] sm:block">
                {user?.full_name}
              </span>
            </button>
          </div>
        </header>
        <main
          className="dashboard-shell-main min-h-[calc(100vh-4rem)] bg-white px-4 py-5 sm:p-6 md:p-8"
        >
          <style>{`
            /* Portal form fields get the v2 field look — the same tokens
               as ui/fieldStyles (Input/Select/DatePicker): #E4E9F1
               hairline, #C9D6EA hover, blue #0A66F0 focus + soft ring.
               !important because older call sites carry their own border
               classes; a field flagged aria-invalid keeps its red border. */
            .dashboard-shell-main :is(
              input[type="text"],
              input[type="email"],
              input[type="password"],
              input[type="number"],
              input[type="tel"],
              input[type="url"],
              input[type="search"],
              input[type="date"],
              input[type="time"],
              input[type="datetime-local"],
              input:not([type]),
              select,
              textarea
            ):not([aria-invalid="true"]) {
              border: 1px solid #E4E9F1 !important;
              border-color: #E4E9F1 !important;
              outline: none;
              transition: border-color 160ms ease, box-shadow 160ms ease;
            }

            .dashboard-shell-main :is(
              input[type="text"],
              input[type="email"],
              input[type="password"],
              input[type="number"],
              input[type="tel"],
              input[type="url"],
              input[type="search"],
              input[type="date"],
              input[type="time"],
              input[type="datetime-local"],
              input:not([type]),
              select,
              textarea
            ):not([aria-invalid="true"]):not(:disabled):not(:focus):hover {
              border-color: #C9D6EA !important;
            }

            .dashboard-shell-main :is(
              input[type="text"],
              input[type="email"],
              input[type="password"],
              input[type="number"],
              input[type="tel"],
              input[type="url"],
              input[type="search"],
              input[type="date"],
              input[type="time"],
              input[type="datetime-local"],
              input:not([type]),
              select,
              textarea
            ):not([aria-invalid="true"]):focus {
              border: 1px solid #0A66F0 !important;
              border-color: #0A66F0 !important;
              outline: none;
              box-shadow: 0 0 0 3px rgba(10, 102, 240, 0.15) !important;
            }
          `}</style>
          <MandatoryGates />
          <div className="mx-auto w-full max-w-[1320px]">{children}</div>
        </main>
      </div>
    </div>
  );
}
