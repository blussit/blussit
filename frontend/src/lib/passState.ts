import type { SubscriptionPlan, UserSubscription } from "../types";

/**
 * Where a monthly pass stands for the CUSTOMER, in one word:
 *
 *   - "active":   washes left and still valid — bookable.
 *   - "used_up":  no washes left but the pass is still running (an auto-pay
 *                 pass at 0 stays "active" until it refills; older data may
 *                 read "expired" with a future end date). Nothing to book
 *                 with and nothing to re-buy.
 *   - "renewing": auto-pay pass past its end date, next charge still due
 *                 (`renewal_pending`). Unusable until it lands; the server
 *                 refuses a second purchase meanwhile.
 *   - "ended":    expired or cancelled — the one that gets "Buy again".
 *   - "paused":   held by staff; shown, never actionable.
 */
export type PassState = "active" | "used_up" | "renewing" | "ended" | "paused";

export function passState(sub: UserSubscription, now = Date.now()): PassState {
  if (sub.effective_status === "paused") return "paused";
  if (sub.renewal_pending && sub.effective_status !== "cancelled") return "renewing";
  const left = sub.remaining_service_count ?? 0;
  if (sub.effective_status === "active") return left > 0 ? "active" : "used_up";
  const endMs = new Date(sub.end_date).getTime();
  if (sub.effective_status === "expired" && sub.auto_renew && left <= 0 && endMs > now) return "used_up";
  return "ended";
}

/** Still running (bookable now, used up, or renewing) — what sits on top of the dashboard. */
export const isLivePass = (sub: UserSubscription) => {
  const state = passState(sub);
  return state === "active" || state === "used_up" || state === "renewing";
};

/** A society pass (docs/SOCIETY_PLANS.md): managed by the society manager —
 *  no cancel / upgrade / auto-pay / buy again here. */
export const isSocietyPass = (sub: Pick<UserSubscription, "society_id">): boolean => !!sub.society_id;

/** Where a society pass is booked and renewed — only ever a same-site
 *  /society/<token> path (never an arbitrary URL from the response). */
export function societyPassPath(sub: Pick<UserSubscription, "society_form_path">): string | null {
  const path = sub.society_form_path || "";
  return /^\/society\/[A-Za-z0-9_-]{16,64}$/.test(path) ? path : null;
}

/** The plan's name for a pass — the pass carries it on some responses only. */
export function passPlanName(sub: UserSubscription, plans?: SubscriptionPlan[] | null): string {
  return sub.plan_name || plans?.find((p) => p.id === sub.plan_id)?.name || "Monthly pass";
}

/**
 * Ended passes worth a "Buy again": the latest one per plan + vehicle type +
 * service, and only while no live pass covers that same type + service
 * (the server refuses a second live pass for it anyway).
 */
export function buyAgainCandidates(subs: UserSubscription[]): UserSubscription[] {
  const live = subs.filter(isLivePass);
  const seen = new Set<string>();
  return subs
    // A society pass is renewed on its society page, never re-bought here.
    .filter((s) => passState(s) === "ended" && !isSocietyPass(s))
    .sort((a, b) => new Date(b.end_date).getTime() - new Date(a.end_date).getTime())
    .filter((s) => {
      const key = `${s.plan_id}|${s.vehicle_type || ""}|${s.service_id || ""}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return !live.some((l) => l.vehicle_type === s.vehicle_type && l.service_id === s.service_id);
    });
}
