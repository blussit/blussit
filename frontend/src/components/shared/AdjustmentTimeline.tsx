import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";
import { adjustmentApi, type BookingAdjustment } from "../../api/staffBookings";
import { asUtcInstant, formatDateTime } from "../../lib/date";

const rupees = (n: unknown) => `₹${Math.round((Number(n) || 0) * 100) / 100}`;

function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return String(value);
  return String(value);
}

function Entry({ row }: { row: BookingAdjustment }) {
  const admin = row.kind === "admin_edit";
  return (
    <li className="rounded-xl border border-gray-100 px-3 py-2.5 text-sm">
      <p className="flex flex-wrap items-center justify-between gap-x-3">
        <span className="font-semibold text-[var(--color-text-primary)]">
          {admin ? "Edited By Admin" : row.after.manager_discount > 0 ? "Discount" : "Discount Removed"}
          {row.actor_name ? ` · ${row.actor_name}` : ""}
        </span>
        <span className="font-mono-num text-xs text-[var(--color-text-secondary)]">{formatDateTime(asUtcInstant(row.created_at))}</span>
      </p>
      {!admin && (
        <p className="font-mono-num mt-0.5">
          {rupees(row.before.manager_discount)} → {rupees(row.after.manager_discount)} · Total {rupees(row.before.total)} → {rupees(row.after.total)}
        </p>
      )}
      {admin && (row.changes || []).slice(0, 12).map((c, i) => (
        <p key={i} className="mt-0.5 text-[var(--color-text-secondary)]">
          <span className="font-medium text-[var(--color-text-primary)]">{c.label}</span>: {show(c.from)} → {show(c.to)}
        </p>
      ))}
      {admin && row.after.total !== row.before.total && (
        <p className="font-mono-num mt-0.5">
          Total {rupees(row.before.total)} → {rupees(row.after.total)}
        </p>
      )}
      {(row.wallet_credit || 0) > 0 && <p className="mt-0.5 text-[var(--color-text-secondary)]">{rupees(row.wallet_credit)} went back to the customer&apos;s wallet.</p>}
      <p className="mt-0.5 text-[var(--color-text-secondary)]">Reason: {row.reason}</p>
    </li>
  );
}

/** The booking's money-change history (discounts and admin edits) — manager (own center) and admin only. */
export function AdjustmentTimeline({ bookingIds, kind }: { bookingIds: string[]; kind?: "admin_edit" | "discount" }) {
  const lead = bookingIds[0];
  const { data = [], isLoading } = useQuery({
    queryKey: ["booking-adjustments", ...bookingIds],
    queryFn: async () => (await Promise.all(bookingIds.map((id) => adjustmentApi.history(id)))).flat().sort((a, b) => (a.created_at < b.created_at ? 1 : -1)),
    enabled: !!lead,
  });
  const rows = kind ? data.filter((r) => r.kind === kind) : data;
  if (isLoading || rows.length === 0) return null;
  return (
    <div data-testid="adjustment-timeline">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-[var(--color-text-secondary)]">
        <History className="h-3.5 w-3.5" /> {kind === "admin_edit" ? "Edited By Admin" : "Discount And Edit History"}
      </p>
      <ul className="space-y-2">
        {rows.map((r) => (
          <Entry key={r.id} row={r} />
        ))}
      </ul>
    </div>
  );
}
