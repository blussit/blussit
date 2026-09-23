import { useQuery } from "@tanstack/react-query";
import { BadgeCheck } from "lucide-react";
import { subscriptionApi } from "../../api/engagement";

/**
 * Shown once a manager picks an existing customer while booking on their
 * behalf (New booking / Log a done job) — confirms up front which pass(es)
 * they hold and how many visits are left, since a matching booking is
 * redeemed against one of these automatically at creation with no separate
 * picker step (see booking_service._cars_for_lines). Purely informational.
 */
export function CustomerActivePasses({ customerId }: { customerId: string }) {
  const { data } = useQuery({
    queryKey: ["customer-active-passes", customerId],
    queryFn: () => subscriptionApi.forCustomer(customerId),
    enabled: !!customerId,
  });

  const active = (data || []).filter((s) => s.effective_status === "active");
  if (!active.length) return null;

  return (
    <div className="rounded-xl border border-[#F3E5B5] bg-[#FFF9E6] px-4 py-3 text-sm">
      <p className="mb-1.5 flex items-center gap-1.5 font-medium text-black">
        <BadgeCheck className="h-4 w-4 text-[var(--color-primary)]" /> Holds an active pass
      </p>
      <ul className="space-y-0.5 text-[13px] text-gray-700">
        {active.map((s) => (
          <li key={s.id}>
            {s.plan_name} — {s.remaining_service_count}/{s.total_service_count} washes left
          </li>
        ))}
      </ul>
      <p className="mt-1.5 text-[11px] text-gray-500">A matching booking below is applied against this pass automatically — no charge.</p>
    </div>
  );
}
