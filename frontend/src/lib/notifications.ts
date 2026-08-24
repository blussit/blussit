import { ROLE_BASE_PATH } from "./constants";
import type { Notification, UserRole } from "../types";

/**
 * Where clicking a given notification should take the viewer, given their
 * role — shared between the toast popups (DashboardShell) and the full
 * notifications list page so they never drift out of sync. Returns null
 * when there's nowhere sensible to go (e.g. a system/promotion notice, or
 * a type with no per-item detail route for this role) — callers should
 * just mark it read in that case instead of navigating.
 */
export function notificationTargetPath(n: Notification, role: UserRole): string | null {
  const base = ROLE_BASE_PATH[role] ?? "/app";

  if (n.notification_type === "booking") {
    // Captains have no standalone booking-detail route — everything happens
    // on the jobs list itself, so send them there instead.
    if (role === "captain") return base;
    return n.reference_id ? `${base}/bookings/${n.reference_id}` : null;
  }

  if (n.notification_type === "complaint") {
    // No per-complaint detail route exists for any role yet — land on the
    // list instead of doing nothing.
    if (role === "customer") return `${base}/support`;
    if (role === "manager" || role === "admin") return `${base}/complaints`;
    return null;
  }

  return null;
}
