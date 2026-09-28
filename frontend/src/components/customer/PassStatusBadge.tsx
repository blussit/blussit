import { Badge } from "../ui";
import { passState } from "../../lib/passState";
import type { UserSubscription } from "../../types";

/** One badge for a pass, in the customer's words (not the raw status). */
export function PassStatusBadge({ sub }: { sub: UserSubscription }) {
  const state = passState(sub);
  if (state === "paused") return <Badge tone="warning">Paused</Badge>;
  if (state === "renewing") return <Badge tone="info">Renewing…</Badge>;
  if (state === "ended") return <Badge tone="neutral">{sub.effective_status === "cancelled" ? "Cancelled" : "Ended"}</Badge>;
  if (sub.auto_renew) return <Badge tone="info">Auto-pay on</Badge>;
  if (state === "used_up") return <Badge tone="neutral">Used up</Badge>;
  return <Badge tone="success">Active</Badge>;
}
