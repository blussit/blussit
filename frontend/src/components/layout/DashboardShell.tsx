import { type ReactNode, useEffect, useRef, useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { Bell, BellOff, LogOut, Menu, Sparkles, User as UserIcon, X, type LucideIcon } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { notificationApi } from "../../api/engagement";
import { useToast, type ToastTone } from "../../context/ToastContext";
import { notificationTargetPath } from "../../lib/notifications";
import { playNotificationChime } from "../../lib/notificationSound";
import type { Notification } from "../../types";

export interface NavItem {
  label: string;
  to: string;
  icon: LucideIcon;
  end?: boolean;
}

function toastToneFor(notification: Notification): ToastTone {
  if (notification.notification_type === "complaint") return "warning";
  return "info";
}

export function DashboardShell({
  navItems,
  portalLabel,
  children,
}: {
  navItems: NavItem[];
  portalLabel: string;
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
  const [notifPermission, setNotifPermission] = useState<NotificationPermission | "unsupported">(
    typeof Notification !== "undefined" ? Notification.permission : "unsupported"
  );

  const { data: notifData } = useQuery({
    queryKey: ["notifications", "unread"],
    // Wide enough that a realistic burst (the reminder sweep can flag
    // several different bookings in one pass, or the tab was backgrounded
    // across a few poll ticks) never exceeds it — the "last seen" watermark
    // below advances past everything it fetched, so anything beyond this
    // page size would get silently marked seen without ever toasting.
    queryFn: () => notificationApi.list({ page: 1, page_size: 20 }),
    refetchInterval: 7000,
    // Keep polling while the tab is in the background/unfocused — otherwise
    // React Query pauses the interval and a manager who's switched to
    // another tab would never actually get notified of anything until they
    // click back in, defeating the point of a system-level alert.
    refetchIntervalInBackground: true,
  });

  // Ask once, quietly, on mount — works in most browsers. Safari (and some
  // Chrome versions under stricter settings) only honor a permission
  // request that's triggered by an actual user gesture, so this is backed
  // up by a second request on the bell icon's own click handler below.
  useEffect(() => {
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission()
        .then(setNotifPermission)
        .catch(() => {});
    }
  }, []);

  const requestNotificationPermission = () => {
    if (typeof Notification === "undefined" || Notification.permission !== "default") return;
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
      .filter((n) => new Date(n.created_at).getTime() > (lastSeenRef.current ?? 0))
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());

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
      if (typeof Notification !== "undefined" && Notification.permission === "granted") {
        fresh.slice(0, 5).forEach((n) => {
          // requireInteraction keeps it on screen until dismissed instead of
          // auto-hiding after a few seconds (Chrome/Edge desktop honor this;
          // other browsers just ignore the option and behave as before).
          const native = new Notification(n.title, { body: n.message, tag: n.id, requireInteraction: true });
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
      const newest = Math.max(...notifData.data.map((n) => new Date(n.created_at).getTime()));
      lastSeenRef.current = newest;
      localStorage.setItem(storageKey, String(newest));
    } else if (isFirstRun) {
      const newest = Math.max(0, ...notifData.data.map((n) => new Date(n.created_at).getTime()));
      lastSeenRef.current = newest;
      localStorage.setItem(storageKey, String(newest));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notifData, user]);

  return (
    <div className="min-h-screen bg-[var(--color-surface)]">
      {/* Sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-64 transform flex-col border-r border-gray-100 bg-white transition-transform duration-200 md:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-gray-100 px-5">
          <Link to="/" className="flex items-center gap-2 font-display text-base font-bold text-[var(--color-primary)]">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--color-primary)] text-[var(--color-secondary)]">
              <Sparkles className="h-4 w-4" />
            </span>
            CLEANRIDE
          </Link>
          <button className="md:hidden" onClick={() => setSidebarOpen(false)}>
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="shrink-0 px-5 py-3">
          <span className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">{portalLabel}</span>
        </div>
        {/* min-h-0 is required alongside flex-1 or overflow-y-auto silently
            does nothing inside a flex column — without it this <nav> just
            keeps growing past the viewport and overlaps the logout button
            pinned below it. */}
        <nav className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto px-3 pb-2">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={() => setSidebarOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-[var(--color-primary-light)] text-[var(--color-primary)]"
                    : "text-gray-600 hover:bg-gray-50 hover:text-[var(--color-primary)]"
                }`
              }
            >
              <item.icon className="h-4.5 w-4.5" />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto w-full shrink-0 border-t border-gray-100 p-4">
          <button
            onClick={logout}
            className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-gray-600 hover:bg-red-50 hover:text-[var(--color-error)]"
          >
            <LogOut className="h-4.5 w-4.5" />
            Log out
          </button>
        </div>
      </aside>

      {sidebarOpen && <div className="fixed inset-0 z-30 bg-gray-900/30 md:hidden" onClick={() => setSidebarOpen(false)} />}

      {/* Main content */}
      <div className="md:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-gray-100 bg-white/90 px-5 backdrop-blur-md md:px-8">
          <button className="md:hidden" onClick={() => setSidebarOpen(true)}>
            <Menu className="h-5 w-5" />
          </button>
          <div className="hidden md:block" />
          <div className="flex items-center gap-4">
            <div className="group relative">
              <button
                onClick={() => {
                  requestNotificationPermission();
                  navigate("notifications");
                }}
                className="relative rounded-full p-2 text-gray-500 hover:bg-gray-100"
                aria-label="Notifications"
              >
                {notifPermission === "denied" ? <BellOff className="h-5 w-5" /> : <Bell className="h-5 w-5" />}
                {!!notifData?.unread_count && (
                  <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-[var(--color-error)]" />
                )}
              </button>
              {notifPermission === "denied" && (
                <div className="pointer-events-none absolute right-0 top-full z-30 mt-1 hidden w-56 rounded-lg bg-gray-900 p-2.5 text-xs text-white group-hover:block">
                  Desktop notifications are blocked for this site. Enable them in your browser's site settings to get alerts
                  outside this tab.
                </div>
              )}
            </div>
            <button onClick={() => navigate("profile")} className="flex items-center gap-2.5 rounded-full border border-gray-200 py-1 pl-1 pr-3">
              <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--color-primary-light)] text-[var(--color-primary)]">
                <UserIcon className="h-4 w-4" />
              </span>
              <span className="hidden text-sm font-medium text-[var(--color-text-primary)] sm:block">{user?.full_name}</span>
            </button>
          </div>
        </header>
        <main className="p-5 md:p-8">{children}</main>
      </div>
    </div>
  );
}
