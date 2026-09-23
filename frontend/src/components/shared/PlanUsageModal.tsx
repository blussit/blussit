import { useQuery } from "@tanstack/react-query";
import { subscriptionApi } from "../../api/engagement";
import { Modal, PageLoader, StatusBadge } from "../ui";
import { format, formatSlot } from "../../lib/date";

/** "Click on that plan and check last when the service was claimed, how
 *  many remaining" — one plan's full spend history. Shared by the
 *  manager's Subscriptions page and the admin's Purchased plans page. */
export function PlanUsageModal({ subscriptionId, onClose }: { subscriptionId: string | null; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["subscription-usage", subscriptionId],
    queryFn: () => subscriptionApi.usageHistory(subscriptionId as string),
    enabled: !!subscriptionId,
  });

  return (
    <Modal open={!!subscriptionId} onClose={onClose} title="Plan usage">
      {isLoading || !data ? (
        <PageLoader />
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2.5">
            <div className="rounded-xl border border-[#F3E5B5] bg-white p-3">
              <p className="text-[11px] font-medium text-gray-500">Remaining</p>
              <p className="font-mono-num mt-0.5 text-lg font-bold text-black">
                {data.remaining_service_count}/{data.total_service_count}
              </p>
            </div>
            <div className="rounded-xl border border-[#F3E5B5] bg-white p-3">
              <p className="text-[11px] font-medium text-gray-500">Last used</p>
              <p className="mt-0.5 text-sm font-semibold text-black">{data.last_used_at ? format(data.last_used_at) : "Never yet"}</p>
            </div>
          </div>

          {!data.bookings.length ? (
            <p className="rounded-xl bg-gray-50 p-4 text-center text-sm text-gray-500">Not used yet.</p>
          ) : (
            <div className="max-h-80 divide-y divide-[#FAF3DF] overflow-y-auto rounded-xl border border-[#F3E5B5]">
              {data.bookings.map((b) => (
                <div key={b.booking_id} className="flex items-center justify-between gap-3 px-3.5 py-2.5">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-black">{b.booking_number}</p>
                    <p className="text-xs text-gray-500">
                      {b.scheduled_date ? format(b.scheduled_date) : "—"} · {formatSlot(b.scheduled_slot)}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="font-mono-num text-sm text-black">₹{b.total_amount ?? 0}</span>
                    <StatusBadge status={b.status} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
