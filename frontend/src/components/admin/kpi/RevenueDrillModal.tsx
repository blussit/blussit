import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Modal, PageLoader, StatusBadge } from "../../ui";
import { bookingApi } from "../../../api/booking";
import { subscriptionApi } from "../../../api/engagement";
import { BookingDetailDrawer } from "../../shared/BookingDetailDrawer";
import { CustomerDetailDrawer } from "../../shared/CustomerDetailDrawer";
import { format, formatSlot } from "../../../lib/date";
import { formatINR } from "./charts";
import type { KpiPeriodParams } from "../../../api/admin";
import type { Booking } from "../../../types";

type Tab = "bookings" | "plans";

/**
 * The revenue tile's drill-down — "Bookings + Plans" means the number on
 * the tile can come from either source (or both), so the founder needs to
 * see which: a plan-only day showed an empty "Completed washes" list with
 * no hint the money came from a plan sale instead. Two tabs, one modal;
 * whichever source actually contributed opens by default.
 */
export function RevenueDrillModal({
  open,
  onClose,
  params,
  defaultTab,
  serviceCenterId,
}: {
  open: boolean;
  onClose: () => void;
  params: KpiPeriodParams;
  defaultTab: Tab;
  /** Scopes both tabs to one center — a manager's own Sales drill-down.
   *  Omitted (undefined) for the admin dashboard's platform-wide view. */
  serviceCenterId?: string;
}) {
  const [tab, setTab] = useState<Tab>(defaultTab);
  const [openBooking, setOpenBooking] = useState<Booking | null>(null);
  const [openCustomerId, setOpenCustomerId] = useState<string | null>(null);

  // Re-open on whichever tab actually explains the number that was
  // clicked (revenueScope="plans" -> Plans tab), every time the modal
  // opens fresh — not just on first mount.
  useEffect(() => {
    if (open) setTab(defaultTab);
  }, [open, defaultTab]);

  const bookings = useQuery({
    queryKey: ["kpi-drill-revenue-bookings", params, serviceCenterId],
    queryFn: () =>
      serviceCenterId
        ? bookingApi.forCenter(serviceCenterId, { ...params, page_size: 100, date_field: "completed", status: "completed" })
        : bookingApi.all({ ...params, page_size: 100, date_field: "completed", status: "completed" }),
    enabled: open && tab === "bookings",
  });
  const plans = useQuery({
    queryKey: ["kpi-drill-revenue-plans", params, serviceCenterId],
    queryFn: () =>
      serviceCenterId
        ? subscriptionApi.centerPlanPurchases(serviceCenterId, { ...params, page_size: 100 })
        : subscriptionApi.planPurchases({ ...params, page_size: 100 }),
    enabled: open && tab === "plans",
  });

  return (
    <>
      <Modal open={open} onClose={onClose} title="Revenue" maxWidth="max-w-2xl">
        <div className="space-y-3">
          <div className="flex gap-1 rounded-xl border border-[#F3E5B5] bg-white p-1">
            {(
              [
                { key: "bookings", label: "Bookings" },
                { key: "plans", label: "Plans" },
              ] as const
            ).map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => setTab(t.key)}
                className={`flex-1 rounded-lg px-4 py-2 text-sm font-semibold transition-colors ${tab === t.key ? "bg-black text-white" : "text-gray-600 hover:bg-[#FFF4CD]"}`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {tab === "bookings" ? (
            bookings.isLoading || !bookings.data ? (
              <PageLoader />
            ) : !bookings.data.data.length ? (
              <p className="rounded-xl bg-gray-50 p-4 text-center text-sm text-gray-500">No completed washes in this period.</p>
            ) : (
              <div className="space-y-2">
                <p className="text-xs text-gray-400">
                  {bookings.data.meta.total} total
                  {bookings.data.meta.total > bookings.data.data.length ? ` — showing the first ${bookings.data.data.length}` : ""}
                </p>
                <div className="max-h-[60vh] divide-y divide-[#FAF3DF] overflow-y-auto rounded-xl border border-[#F3E5B5]">
                  {bookings.data.data.map((b) => (
                    <div key={b.id} className="px-3.5 py-2.5">
                      <button type="button" onClick={() => setOpenBooking(b)} className="flex w-full items-center justify-between gap-3 text-left">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium text-black">{b.customer_name || "—"} · {b.booking_number}</p>
                          <p className="text-xs text-gray-500">{format(b.scheduled_date)} · {formatSlot(b.scheduled_slot)}</p>
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          <span className="font-mono-num text-sm text-black">₹{b.total_amount}</span>
                          <StatusBadge status={b.status} />
                        </div>
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )
          ) : plans.isLoading || !plans.data ? (
            <PageLoader />
          ) : !plans.data.data.length ? (
            <p className="rounded-xl bg-gray-50 p-4 text-center text-sm text-gray-500">No plans purchased in this period.</p>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-gray-400">
                {plans.data.meta.total} total
                {plans.data.meta.total > plans.data.data.length ? ` — showing the first ${plans.data.data.length}` : ""}
              </p>
              <div className="max-h-[60vh] divide-y divide-[#FAF3DF] overflow-y-auto rounded-xl border border-[#F3E5B5]">
                {plans.data.data.map((p) => (
                  <div key={p.id} className="px-3.5 py-2.5">
                    <button
                      type="button"
                      onClick={() => p.customer_id && setOpenCustomerId(p.customer_id)}
                      className="flex w-full items-center justify-between gap-3 text-left"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-black">{p.customer_name} · {p.plan_name}</p>
                        <p className="text-xs text-gray-500">
                          {format(p.created_at)}
                          {p.payment_method ? ` · ${p.payment_method}` : ""}
                          {p.discount ? ` · ${formatINR(p.discount)} off` : ""}
                        </p>
                      </div>
                      <span className="font-mono-num shrink-0 text-sm text-black">{formatINR(p.amount)}</span>
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </Modal>
      <BookingDetailDrawer booking={openBooking} onClose={() => setOpenBooking(null)} />
      <CustomerDetailDrawer customerId={openCustomerId} onClose={() => setOpenCustomerId(null)} />
    </>
  );
}
