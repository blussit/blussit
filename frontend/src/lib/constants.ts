import type { Booking } from "../types";

// Shared across every place that renders a booking's issue_flag (manager
// queue, captain dashboard, booking detail) so they stay in sync — add a new
// flag value here once, not in three separate files.
export const ISSUE_LABELS: Record<string, string> = {
  captain_not_started: "Captain hasn't started — past scheduled time",
  captain_not_reached: "Never got a captain — window expired",
  captain_late_start: "Captain started late",
  captain_delay: "Taking unusually long on the way",
  captain_reported_risk: "Captain flagged a delay risk",
  service_overrun: "Wash taking longer than expected",
};

// Every role's dashboard base path — shared so route/redirect logic doesn't
// drift across DashboardShell, ProtectedRoute, GuestOnlyRoute, etc.
export const ROLE_BASE_PATH: Record<string, string> = {
  customer: "/app",
  manager: "/manager",
  admin: "/admin",
  captain: "/captain",
};

// Matches the backend's own acceptance check in assign_captain (pending OR
// rescheduled) — a rescheduled booking (the result of a manager rescheduling
// a stuck/flagged one) needs a captain exactly the same way a brand-new
// pending booking does. Shared between the booking queue and the manager
// dashboard's urgent-bookings widget so they agree on what "needs a
// captain" means.
export const needsCaptain = (b: Booking) => b.status === "pending" || b.status === "rescheduled";

// A flag only means "needs manager action" while the booking is still live —
// once it's completed/cancelled there's nothing left to do about it (the
// backend auto-resolves on those transitions too, this is a client-side
// safety net for any older data). Shared between the booking queue and the
// manager dashboard so a captain-not-started (etc.) flag shows up in both
// places consistently.
export const isOpenIssue = (b: Booking) => !!b.issue_flag && !b.issue_resolved && !["completed", "cancelled"].includes(b.status);
