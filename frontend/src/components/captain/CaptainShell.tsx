/**
 * The captain app frame: one phone-width column (centred on tablet/desktop),
 * bottom tabs Today · Jobs · Earnings · Profile, and the always-on plumbing
 * every tab needs — live job pushes, the location pings while a job is
 * active, and new-notification toasts with a chime.
 */
import { type CSSProperties, type ReactNode, useEffect, useLayoutEffect, useRef } from "react";
import "../layout/staffTheme.css";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardList, House, User, Wallet } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { staffDirectoryApi } from "../../api/admin";
import { notificationApi } from "../../api/engagement";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import { useLiveChannel } from "../../lib/socket";
import { notificationTargetPath } from "../../lib/notifications";
import { playNotificationChime } from "../../lib/notificationSound";
import { MandatoryGates } from "../shared/MandatoryGates";

// A manager watching the live map sees real movement; sparse enough for a
// phone battery. Only while he has at least one active job.
const LOCATION_PING_INTERVAL_MS = 25_000;
// New assignments arrive over the "user:{id}" push; this only catches one
// the socket missed while reconnecting.
const ACTIVE_FALLBACK_POLL_MS = 3 * 60_000;

export const ACTIVE_JOBS_KEY = ["my-jobs", "active"] as const;

/** Every tab reads the same active-jobs query (server-sorted, soonest first). */
export function useActiveJobs() {
  return useQuery({
    queryKey: ACTIVE_JOBS_KEY,
    queryFn: () => bookingApi.myJobs({ scope: "active", page: 1, page_size: 100 }),
    refetchInterval: ACTIVE_FALLBACK_POLL_MS,
  });
}

// Shared components (Input, Modal, BookingDetailDrawer, NotificationsPage)
// read these — the v2 palette instead of the old black/gold brand.
const V2_VARS = {
  "--color-surface": "#FFFFFF",
  "--color-card-border": "#E4E9F1",
  "--color-primary": "#0A66F0",
  "--color-primary-dark": "#0852C2",
  "--color-primary-light": "#E8F0FE",
  "--color-secondary": "#0A66F0",
  "--color-secondary-light": "#E8F0FE",
  "--color-accent": "#0A66F0",
  "--color-accent-light": "#E8F0FE",
  "--color-text-primary": "#0E1A33",
  "--color-text-secondary": "#5F6878",
} as CSSProperties;

function useJobPlumbing() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const active = useActiveJobs();

  useLiveChannel(user ? `user:${user.id}` : null, () => {
    queryClient.invalidateQueries({ queryKey: ["my-jobs"] });
    queryClient.invalidateQueries({ queryKey: ["captain-visit"] });
    queryClient.invalidateQueries({ queryKey: ["notifications"] });
  });

  const hasActiveJob = (active.data?.meta.total ?? 0) > 0;
  useEffect(() => {
    if (!hasActiveJob || !navigator.geolocation) return;
    const send = () =>
      navigator.geolocation.getCurrentPosition(
        (pos) => staffDirectoryApi.pingLocation(pos.coords.latitude, pos.coords.longitude).catch(() => {}),
        () => {},
        { enableHighAccuracy: true, timeout: 15000 },
      );
    send();
    const timer = setInterval(send, LOCATION_PING_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [hasActiveJob]);
}

/** Toast + chime for notifications that arrive while the app is open. */
export function useUnreadNotifications() {
  return useQuery({
    queryKey: ["notifications", "unread"],
    queryFn: () => notificationApi.list({ page: 1, page_size: 20 }),
    refetchInterval: 30_000,
    refetchIntervalInBackground: true,
  });
}

function useNotificationAlerts() {
  const { user } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const { data } = useUnreadNotifications();
  const seen = useRef<number | null>(null);

  useEffect(() => {
    if (!data?.data || !user) return;
    const key = `notif_last_seen_${user.id}`;
    const newest = Math.max(0, ...data.data.map((n) => new Date(n.created_at).getTime()));
    if (seen.current === null) {
      // First load: set the baseline — never replay old unread ones.
      let stored = 0;
      try {
        stored = Number(localStorage.getItem(key) || 0);
      } catch {
        /* storage blocked */
      }
      seen.current = Math.max(stored, newest);
      return;
    }
    const fresh = data.data
      .filter((n) => new Date(n.created_at).getTime() > (seen.current ?? 0))
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
    if (!fresh.length) return;
    for (const n of fresh) {
      const path = notificationTargetPath(n, user.role);
      toast.push({ tone: "info", title: n.title, message: n.message, onClick: path ? () => navigate(path) : undefined });
    }
    playNotificationChime();
    seen.current = newest;
    try {
      localStorage.setItem(key, String(newest));
    } catch {
      /* storage blocked */
    }
  }, [data, user, toast, navigate]);
}

export function CaptainShell({ children }: { children: ReactNode }) {
  const { t } = useCaptainTranslation();
  const { pathname } = useLocation();
  useJobPlumbing();
  useNotificationAlerts();
  // Same as the manager/admin shell: `staff-v2` on <body>, so whatever is
  // portalled out of this frame — the set-password gate, the confirm box,
  // toasts, the document viewer — gets the v2 look too, not the old
  // black/gold primitives.
  useLayoutEffect(() => {
    document.body.classList.add("staff-v2");
    return () => document.body.classList.remove("staff-v2");
  }, []);

  // A job's own screens carry their own bottom action bar.
  const onJobScreen = /^\/captain\/jobs\/[^/]+/.test(pathname);
  const tabs = [
    { to: "/captain", end: true, label: t("captain.v2.tab.today"), icon: House },
    { to: "/captain/jobs", end: true, label: t("captain.v2.tab.jobs"), icon: ClipboardList },
    { to: "/captain/earnings", end: false, label: t("captain.v2.tab.earnings"), icon: Wallet },
    { to: "/captain/profile", end: false, label: t("captain.v2.tab.profile"), icon: User },
  ];

  return (
    <div className="min-h-screen bg-[#EEF3FA] text-[#0E1A33] [&_a]:cursor-pointer [&_button]:cursor-pointer" style={V2_VARS}>
      <div className="mx-auto min-h-screen w-full max-w-[480px] bg-white md:border-x md:border-[#E4E9F1]">
        <main className={`px-4 ${onJobScreen ? "pb-32" : "pb-28"}`}>
          <MandatoryGates />
          {children}
        </main>
      </div>
      {!onJobScreen && (
        <nav className="fixed inset-x-0 bottom-0 z-40 mx-auto grid w-full max-w-[480px] grid-cols-4 border-t border-[#E4E9F1] bg-white px-1 pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-1.5">
          {tabs.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              end={tab.end}
              className={({ isActive }) =>
                `flex min-h-[52px] flex-col items-center justify-center gap-0.5 rounded-xl text-[11px] ${
                  isActive ? "font-bold text-[#0A66F0]" : "font-semibold text-[#5F6878]"
                }`
              }
            >
              <tab.icon className="h-[22px] w-[22px]" />
              {tab.label}
            </NavLink>
          ))}
        </nav>
      )}
    </div>
  );
}
