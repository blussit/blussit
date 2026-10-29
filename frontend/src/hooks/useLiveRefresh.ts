import { useRef } from "react";
import { useQueryClient, type Query, type QueryClient } from "@tanstack/react-query";
import { useAuth } from "../context/AuthContext";
import { useLiveChannel } from "../lib/socket";

/**
 * "Something changed — refetch what this screen shows."
 *
 * The server pings the signed-in user's own `user:{id}` channel (plus, for a
 * manager, `center-bookings:{center}` and, for an admin, `admin:live`) after
 * every change a screen could be showing: a booking cancelled / rescheduled /
 * assigned / paid / edited, a plan bought / used / ended, the wallet moving,
 * a payment settling, a notification written. Each ping carries a `kind`
 * hint (booking | plan | wallet | payment | notification) — never data.
 *
 * `useLiveRefresh` (mounted once per portal shell) turns a ping into a
 * debounced `invalidateQueries` of the matching data queries; only queries
 * that are mounted refetch, the rest are just marked stale. One shared
 * socket (lib/socket.ts) carries all of it.
 *
 * `refreshLiveData` is the same invalidation without a socket: the app-wide
 * mutation cache calls it after every successful action, so whoever did
 * something sees every screen it touched update, even with the socket down.
 */

export type LiveKind = "booking" | "plan" | "wallet" | "payment" | "notification" | "all";

// First element of the react-query keys, by what changes them.
const BOOKINGS = [
  "my-bookings", "my-bookings-for-complaint", "booking", "booking-group", "booking-payment-state", "cancellation-charge-preview",
  "staff-booking", "center-bookings", "center-bookings-for-captain-detail", "admin-center-bookings", "admin-center-bookings-reviews",
  "admin-center-summaries", "admin-highlight-booking", "admin-recycle-bin", "booking-adjustments", "travel-status", "geo-status",
  "my-jobs", "captain-visit", "collect-status", "available-slots", "society-slots", "my-society-schedule", "center-captains-page",
  "charges", "customer-360", "my-reviews", "reviews-list", "eligible-captains",
] as const;
const PLANS = [
  "my-subscriptions", "my-custom-plans", "subscription-usage", "center-subscription-overview", "admin-subscriptions-overview",
  "custom-plans", "custom-plan", "customer-active-passes", "customer-360", "society-hub", "society-enrollments",
] as const;
const WALLET = ["my-wallet", "customer-wallet", "wallet-payouts", "customer-360", "charges", "my-withdrawals", "pending-withdrawals"] as const;
const NOTIFICATIONS = ["notifications"] as const;
// Tickets move with a message too (a customer's complaint, a manager's reply).
const TICKETS = ["my-complaints", "center-complaints-page", "admin-complaints", "complaint", "complaint-booking"] as const;
// Aggregates that cost the server real work: refreshed at most every few seconds.
const HEAVY = /^(kpi|manager-kpi|manager-dashboard|collections|center-collections|analytics)/;

const SETS: Record<LiveKind, ReadonlySet<string>> = {
  booking: new Set([...BOOKINGS, ...PLANS, ...WALLET, ...NOTIFICATIONS]),
  payment: new Set([...BOOKINGS, ...PLANS, ...WALLET]),
  plan: new Set([...PLANS, ...WALLET, "my-bookings", "center-bookings"]),
  wallet: new Set([...WALLET, "booking", "booking-group", "my-bookings"]),
  // A message is written for nearly every change worth telling someone
  // about, including ones with no ping of their own (a sweep's flag, a
  // WhatsApp action): bell + tickets + bookings + plans, which is what those touch.
  notification: new Set([...NOTIFICATIONS, ...TICKETS, ...BOOKINGS, ...PLANS]),
  all: new Set([...BOOKINGS, ...PLANS, ...WALLET, ...NOTIFICATIONS, ...TICKETS]),
};

const DEBOUNCE_MS = 500;
const MAX_WAIT_MS = 3_000;
const HEAVY_EVERY_MS = 10_000;
// A tab hidden at least this long refreshes everything when it comes back.
const STALE_AFTER_HIDDEN_MS = 60_000;

const tabHidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";
const firstKey = (query: Query) => (typeof query.queryKey[0] === "string" ? (query.queryKey[0] as string) : "");

interface Scheduler {
  pending: Set<LiveKind>;
  timer: ReturnType<typeof setTimeout> | null;
  firstAt: number;
  heavyTimer: ReturnType<typeof setTimeout> | null;
  heavyAt: number;
  hiddenAt: number | null;
  listening: boolean;
}

const schedulers = new WeakMap<QueryClient, Scheduler>();

function schedulerFor(client: QueryClient): Scheduler {
  let s = schedulers.get(client);
  if (!s) {
    s = { pending: new Set(), timer: null, firstAt: 0, heavyTimer: null, heavyAt: 0, hiddenAt: null, listening: false };
    schedulers.set(client, s);
  }
  if (!s.listening && typeof document !== "undefined") {
    s.listening = true;
    const scheduler = s;
    document.addEventListener("visibilitychange", () => {
      if (tabHidden()) {
        scheduler.hiddenAt = Date.now();
        return;
      }
      const away = scheduler.hiddenAt ? Date.now() - scheduler.hiddenAt : 0;
      scheduler.hiddenAt = null;
      // Whatever arrived while the tab was hidden, or everything if it was away a while.
      if (away >= STALE_AFTER_HIDDEN_MS) scheduler.pending.add("all");
      if (scheduler.pending.size) flush(client, scheduler);
    });
  }
  return s;
}

function flush(client: QueryClient, s: Scheduler) {
  if (s.timer) clearTimeout(s.timer);
  s.timer = null;
  if (tabHidden()) return; // kept in `pending`; the visibilitychange listener flushes it
  const kinds = [...s.pending];
  s.pending.clear();
  if (!kinds.length) return;
  const wanted = new Set<string>();
  for (const kind of kinds) for (const key of SETS[kind]) wanted.add(key);
  // The cheap, user-facing lists now …
  void client.invalidateQueries({ predicate: (q) => wanted.has(firstKey(q)) });
  // … the expensive aggregates on their own, slower clock.
  scheduleHeavy(client, s);
}

function scheduleHeavy(client: QueryClient, s: Scheduler) {
  if (s.heavyTimer) return;
  const wait = Math.max(0, s.heavyAt + HEAVY_EVERY_MS - Date.now());
  s.heavyTimer = setTimeout(() => {
    s.heavyTimer = null;
    if (tabHidden()) {
      // Hidden: don't refetch aggregates nobody is looking at; the next
      // visible flush asks again.
      s.pending.add("all");
      return;
    }
    s.heavyAt = Date.now();
    void client.invalidateQueries({ predicate: (q) => HEAVY.test(firstKey(q)) });
  }, wait);
}

/** Ask for a debounced refresh of everything a `kind` of change can touch. */
export function refreshLiveData(client: QueryClient, kind: LiveKind = "all") {
  const s = schedulerFor(client);
  s.pending.add(kind);
  const now = Date.now();
  if (!s.timer) s.firstAt = now;
  if (s.timer) clearTimeout(s.timer);
  // Debounced, but a steady stream still refreshes at least every MAX_WAIT_MS.
  const wait = Math.max(0, Math.min(DEBOUNCE_MS, s.firstAt + MAX_WAIT_MS - now));
  s.timer = setTimeout(() => flush(client, s), wait);
}

const KINDS: readonly string[] = ["booking", "plan", "wallet", "payment", "notification"];

/**
 * Mount once in each portal shell. Subscribes the signed-in user's own
 * channel (customers, captains, managers, admins alike) and, by role, the
 * center's / admin feed, and refreshes the right queries on every ping.
 * Re-subscribing after a dropped socket (the server acks every subscribe)
 * refreshes everything, since pings may have been missed meanwhile.
 */
export function useLiveRefresh() {
  const { user } = useAuth();
  const client = useQueryClient();
  const seen = useRef(new Set<string>());

  const onMessage = (message: { type: string; channel?: string; kind?: unknown }) => {
    if (message.type === "subscribed") {
      // The first ack per channel is just "connected"; later ones mean a
      // reconnect, and anything pinged in between was missed.
      const channel = message.channel || "";
      if (seen.current.has(channel)) refreshLiveData(client, "all");
      else seen.current.add(channel);
      return;
    }
    if (message.type !== "changed") return;
    // A ping without a kind is a booking change (older server code).
    const kind = typeof message.kind === "string" && KINDS.includes(message.kind) ? (message.kind as LiveKind) : "booking";
    refreshLiveData(client, kind);
  };

  const centerId = user?.role === "manager" ? user.service_center_id : null;
  useLiveChannel(user ? `user:${user.id}` : null, onMessage);
  useLiveChannel(centerId ? `center-bookings:${centerId}` : null, onMessage);
  useLiveChannel(user?.role === "admin" ? "admin:live" : null, onMessage);
}
