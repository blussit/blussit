import { type ReactNode, useEffect, useRef, useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { MandatoryGates } from "../shared/MandatoryGates";
import {
  Bell,
  BellOff,
  LogOut,
  Menu,
  Sparkles,
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
  brand = false,
  bottomNav,
  centerMenu,
  headerRight,
  children,
}: {
  navItems: NavItem[];
  portalLabel: string;
  /** Brand look (white surface, neutral hairline borders, black type, gold
      reserved for CTAs/selection — used by the customer and captain portals;
      staff portals keep the default theme. */
  brand?: boolean;
  /** Mobile app-style bottom tab bar (5 items; index 2 renders as the
      raised gold action button). When set, the sidebar is desktop-only. */
  bottomNav?: NavItem[];
  /** Options shown in a bottom sheet when the raised center button is
      tapped (instead of navigating directly). */
  centerMenu?: {
    label: string;
    description: string;
    icon: LucideIcon;
    to: string;
  }[];
  /** Custom element to render in the header next to the notification bell. */
  headerRight?: ReactNode;
  children: ReactNode;
}) {
  const { user, logout } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [centerMenuOpen, setCenterMenuOpen] = useState(false);
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
    refetchInterval: 30000,
    // Keep polling while the tab is in the background/unfocused — otherwise
    // React Query pauses the interval and a manager who's switched to
    // another tab would never actually get notified of anything until they
    // click back in, defeating the point of a system-level alert.
    refetchIntervalInBackground: true,
  });

  useLiveChannel(user ? `user:${user.id}` : null, () => {
    queryClient.invalidateQueries({ queryKey: notifQueryKey });
  });

  // Ask once, quietly, on mount — works in most browsers. Safari (and some
  // Chrome versions under stricter settings) only honor a permission
  // request that's triggered by an actual user gesture, so this is backed
  // up by a second request on the bell icon's own click handler below.
  useEffect(() => {
    if (
      typeof Notification !== "undefined" &&
      Notification.permission === "default"
    ) {
      Notification.requestPermission()
        .then(setNotifPermission)
        .catch(() => {});
    }
  }, []);

  const requestNotificationPermission = () => {
    if (
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

  // Landing-theme scope: same palette the public pages use. primary stays
  // BLACK (headings/buttons) with gold as the accent; primary-light stays a
  // LIGHT tint — selected states are tint + dark text, never solid black.
  const brandVars = brand
    ? ({
        // Settled after three user rounds: pure WHITE ground, BLACK type,
        // gray icon tiles/labels — but box BOUNDARIES are light yellow
        // (#F3E5B5, this var + the literal borders across customer/captain
        // views), which the user explicitly asked to keep. Beyond borders,
        // gold appears only where it means something: CTAs (bg-[#E8A900] +
        // glow), selected chips (tint + black border), progress fills, and
        // accents inside black panels. No cream page wash, no yellow tiles.
        "--color-surface": "#FFFFFF",
        "--color-card-border": "#E5E7EB",
        "--color-primary": "#0A0A0A",
        "--color-primary-dark": "#000000",
        "--color-primary-light": "#FFF4CD",
        "--color-secondary": "#E8A900",
        "--color-secondary-light": "#FFF4CD",
        "--color-accent": "#E8A900",
        "--color-accent-light": "#FFF4CD",
      } as React.CSSProperties)
    : undefined;

  return (
    <div
      className="min-h-screen bg-[var(--color-surface)] [&_a]:cursor-pointer [&_button]:cursor-pointer"
      style={brandVars}
    >
      {/* Sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 w-[248px] transform flex-col border-r-0 bg-[#F8F8F7] transition-transform duration-200 md:translate-x-0 ${
          bottomNav ? "hidden md:flex" : "flex"
        } ${sidebarOpen ? "translate-x-0" : "-translate-x-full"}`}
      >
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-gray-200 px-5">
          {brand ? (
            <Link
              to="/"
              className="flex w-full flex-col items-start"
              aria-label="Blussit home"
            >
              <img
                src="/blussit-logo.png"
                alt="BLUSSIT"
                className="h-auto w-[158px] object-contain object-left"
              />
              <span className="mt-0.5 text-[6.5px] font-bold uppercase tracking-[0.22em] text-[#E8A900]">
                Clean Car. Clean Mind.
              </span>
            </Link>
          ) : (
            <Link
              to="/"
              className="flex items-center gap-2 font-display text-base font-bold text-[var(--color-primary)]"
            >
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--color-primary)] text-[var(--color-secondary)]">
                <Sparkles className="h-4 w-4" />
              </span>
              BLUSSIT
            </Link>
          )}
          <button className="md:hidden" onClick={() => setSidebarOpen(false)}>
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="shrink-0 px-6 py-4">
          <span
            className={`text-xs font-semibold uppercase tracking-wide ${brand ? "text-gray-400" : "text-[var(--color-text-secondary)]"}`}
          >
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
                `flex items-center gap-3 rounded-lg px-3.5 py-2.5 text-[14px] font-medium transition-colors ${
                  isActive
                    ? brand
                      ? "bg-gray-100 font-semibold text-black"
                      : "bg-[var(--color-primary-light)] text-[var(--color-primary)]"
                    : brand
                      ? "text-gray-600 hover:bg-white hover:text-black"
                      : "text-gray-600 hover:bg-gray-50 hover:text-[var(--color-primary)]"
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
        <div className="mt-auto w-full shrink-0 border-t border-gray-200 p-4">
          <button
            onClick={logout}
            className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-gray-600 hover:bg-red-50 hover:text-[var(--color-error)]"
          >
            <LogOut className="h-[18px] w-[18px]" />
            Log out
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
          className={`sticky top-0 z-20 flex h-16 items-center justify-between border-b border-gray-200 bg-white px-6 md:px-8`}
        >
          {bottomNav ? (
            <Link
              to="/"
              className="flex flex-col md:hidden"
              aria-label="Blussit home"
            >
              <img
                src="/blussit-logo.png"
                alt="BLUSSIT"
                className="h-auto w-[104px] object-contain"
              />
              <span className="mt-0.5 text-[6px] font-bold uppercase tracking-[0.22em] text-[#E8A900]">
                Clean Car. Clean Mind.
              </span>
            </Link>
          ) : (
            <button className="md:hidden" onClick={() => setSidebarOpen(true)}>
              <Menu className="h-5 w-5" />
            </button>
          )}
          <div className="hidden md:block" />
          <div className="flex items-center gap-4">
            {headerRight}
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
              className={`flex items-center gap-2.5 rounded-full border py-1 pl-1 pr-3 ${brand ? "border-gray-200 hover:border-[#E8A900]/50" : "border-gray-200"}`}
            >
              <span
                className={`flex h-7 w-7 items-center justify-center rounded-full ${brand ? "bg-gray-100 text-black" : "bg-[var(--color-primary-light)] text-[var(--color-primary)]"}`}
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
          className={`dashboard-shell-main min-h-[calc(100vh-4rem)] bg-white p-6 md:p-8 ${bottomNav ? "pb-24 md:pb-8" : ""}`}
        >
          <style>{`
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
            ) {
              border: 1px solid #111827 !important;
              border-color: #111827 !important;
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
            ):hover,
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
            ):focus {
              border: 1px solid #E8A900 !important;
              border-color: #E8A900 !important;
              outline: none;
              box-shadow: 0 0 0 1px rgba(232, 169, 0, 0.10) !important;
            }
          `}</style>
          <MandatoryGates />
          <div className="mx-auto w-full max-w-[1320px]">{children}</div>
        </main>
      </div>

      {/* Mobile app-style bottom tab bar (customer portal) */}
      {bottomNav && (
        <nav
          className={`fixed inset-x-0 bottom-0 z-40 grid items-center border-t border-gray-200 bg-white px-1 pb-[max(0.6rem,env(safe-area-inset-bottom))] pt-2 md:hidden ${
            { 3: "grid-cols-3", 4: "grid-cols-4" }[bottomNav.length] ||
            "grid-cols-5"
          }`}
        >
          {bottomNav.map((item, i) =>
            // The raised gold center button exists only for portals that
            // pass a centerMenu (the customer's "Book" sheet) — a plain
            // 3/4-tab bar (captain) renders every item as a normal tab.
            centerMenu && i === 2 ? (
              <div key={item.to} className="flex items-center justify-center">
                <button
                  type="button"
                  aria-label={item.label}
                  onClick={() => setCenterMenuOpen(true)}
                  className="-mt-7 flex h-[52px] w-[52px] items-center justify-center rounded-full border-4 border-white bg-[#E8A900] text-white shadow-[0_10px_22px_rgba(232,169,0,0.4)]"
                >
                  <item.icon className="h-6 w-6" strokeWidth={2.4} />
                </button>
              </div>
            ) : (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `flex flex-col items-center gap-0.5 py-1 text-[10px] ${
                    isActive
                      ? "font-bold text-black"
                      : "font-medium text-gray-400"
                  }`
                }
              >
                <item.icon className="h-[21px] w-[21px]" />
                {item.label}
              </NavLink>
            ),
          )}
        </nav>
      )}

      {/* Bottom sheet for the center button's options */}
      {centerMenu && centerMenuOpen && (
        <div
          className="fixed inset-0 z-50 md:hidden"
          role="dialog"
          aria-modal="true"
        >
          <div
            className="absolute inset-0 bg-black/40 backdrop-blur-[2px]"
            onClick={() => setCenterMenuOpen(false)}
          />
          <div className="absolute inset-x-0 bottom-0 rounded-t-3xl bg-white p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] shadow-[0_-12px_40px_rgba(0,0,0,0.18)]">
            <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-gray-100" />
            <p className="mb-3 font-display text-lg font-bold text-black">
              How do you want to book?
            </p>
            <div className="space-y-2.5">
              {centerMenu.map((opt) => (
                <button
                  key={opt.to}
                  type="button"
                  onClick={() => {
                    setCenterMenuOpen(false);
                    navigate(opt.to);
                  }}
                  className="flex w-full items-center gap-4 rounded-2xl border border-gray-200 p-4 text-left transition-colors hover:border-[#E8A900]/60 hover:bg-[#FAFAFA]"
                >
                  <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-black">
                    <opt.icon className="h-5 w-5" />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-sm font-bold text-black">
                      {opt.label}
                    </span>
                    <span className="block text-xs text-gray-400">
                      {opt.description}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
