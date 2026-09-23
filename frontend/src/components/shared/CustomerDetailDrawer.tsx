import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Calendar, Car, IndianRupee, MapPin, Phone } from "lucide-react";
import { crmApi, type Customer360Booking } from "../../api/crm";
import { Badge, PageLoader, StatusBadge } from "../ui";
import { BookingDetailDrawer } from "./BookingDetailDrawer";
import { format, formatSlot } from "../../lib/date";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-[#F3E5B5] bg-white p-3">
      <p className="text-[11px] font-medium text-gray-500">{label}</p>
      <p className="font-mono-num mt-0.5 text-lg font-bold text-black">{value}</p>
    </div>
  );
}

/**
 * Admin's "click a customer to see everything" view — every booking, every
 * plan (with what was actually paid), vehicles, and the combined lifetime
 * amount (bookings + plans). Read-only: managing a booking or a plan still
 * happens from their own pages, this is the overview that gets you there.
 */
export function CustomerDetailDrawer({ customerId, onClose }: { customerId: string | null; onClose: () => void }) {
  const [openBooking, setOpenBooking] = useState<Customer360Booking | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["customer-360", customerId],
    queryFn: () => crmApi.customer360(customerId as string),
    enabled: !!customerId,
  });

  if (!customerId) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-gray-900/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative z-10 max-h-[88vh] w-full max-w-3xl overflow-y-auto rounded-2xl border border-[#F3E5B5] bg-white p-6 shadow-[0_24px_60px_-16px_rgba(0,0,0,0.28)]">
        {isLoading || !data ? (
          <PageLoader />
        ) : (
          <>
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <h3 className="text-lg font-semibold text-black">{data.profile.full_name}</h3>
                <p className="mt-0.5 flex items-center gap-3 text-sm text-gray-500">
                  {data.profile.phone && (
                    <a href={`tel:${data.profile.phone}`} className="flex items-center gap-1 hover:text-black">
                      <Phone className="h-3.5 w-3.5" /> {data.profile.phone}
                    </a>
                  )}
                  {data.preferred_service_center_name && (
                    <span className="flex items-center gap-1">
                      <MapPin className="h-3.5 w-3.5" /> {data.preferred_service_center_name}
                    </span>
                  )}
                </p>
              </div>
              <button onClick={onClose} className="rounded-full p-1.5 text-gray-400 hover:bg-gray-100 hover:text-black" aria-label="Close">
                ✕
              </button>
            </div>

            {data.same_day_repeat_dates.length > 0 && (
              <div className="mb-4 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>
                  Booked more than once on the same day: {data.same_day_repeat_dates.map((d) => format(d)).join(", ")}
                </span>
              </div>
            )}

            <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
              <Stat label="Total bookings" value={String(data.total_bookings)} />
              <Stat label="Booking spend" value={`₹${data.lifetime_spend.toLocaleString()}`} />
              <Stat label="Plan spend" value={`₹${data.lifetime_plan_spend.toLocaleString()}`} />
              <Stat label="Total amount" value={`₹${data.lifetime_total_spend.toLocaleString()}`} />
            </div>

            {data.subscriptions.length > 0 && (
              <div className="mt-5">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Plans</p>
                <div className="divide-y divide-[#FAF3DF] rounded-xl border border-[#F3E5B5]">
                  {data.subscriptions.map((s) => (
                    <div key={s.id} className="flex items-center justify-between gap-3 px-3.5 py-2.5">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-black">{s.plan_name}</p>
                        <p className="text-xs text-gray-500">
                          {s.remaining_service_count}/{s.total_service_count} washes left
                          {s.amount_paid != null ? ` · ₹${s.amount_paid} paid` : ""}
                        </p>
                      </div>
                      <StatusBadge status={s.effective_status} />
                    </div>
                  ))}
                </div>
              </div>
            )}

            {data.vehicles.length > 0 && (
              <div className="mt-5">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Vehicles</p>
                <div className="flex flex-wrap gap-2">
                  {data.vehicles.map((v) => (
                    <span key={v.id} className="flex items-center gap-1.5 rounded-full border border-[#F3E5B5] bg-white px-3 py-1 text-xs text-black">
                      <Car className="h-3 w-3 text-gray-400" /> {v.brand} {v.model}
                      {v.vehicle_type_name ? ` · ${v.vehicle_type_name}` : ""}
                      {v.registration_number ? ` · ${v.registration_number}` : ""}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div className="mt-5">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                Bookings {data.bookings.length ? `(${data.bookings.length})` : ""}
              </p>
              {!data.bookings.length ? (
                <p className="rounded-xl bg-gray-50 p-4 text-center text-sm text-gray-500">No bookings yet.</p>
              ) : (
                <div className="divide-y divide-[#FAF3DF] rounded-xl border border-[#F3E5B5]">
                  {data.bookings.map((b) => (
                    <button
                      key={b.id}
                      onClick={() => setOpenBooking(b)}
                      className="flex w-full items-center justify-between gap-3 px-3.5 py-2.5 text-left hover:bg-[#FFFCF0]"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-black">
                          {(b.service_names || []).join(", ") || "Service"} · {b.vehicle_label}
                        </p>
                        <p className="flex items-center gap-1 text-xs text-gray-500">
                          <Calendar className="h-3 w-3" /> {format(b.scheduled_date)} · {formatSlot(b.scheduled_slot)}
                          {b.address_text ? ` · ${b.address_text}` : ""}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <span className="flex items-center gap-0.5 font-mono-num text-sm font-semibold text-black">
                          <IndianRupee className="h-3 w-3" /> {b.total_amount}
                        </span>
                        <StatusBadge status={b.status} />
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {data.complaints.length > 0 && (
              <div className="mt-5">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Complaints</p>
                <div className="flex flex-wrap gap-2">
                  {data.complaints.map((c) => (
                    <Badge key={c.id} tone={c.status === "resolved" || c.status === "closed" ? "neutral" : "warning"}>
                      {c.subject}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
      <BookingDetailDrawer booking={openBooking} onClose={() => setOpenBooking(null)} centerName={openBooking?.service_center_name} />
    </div>
  );
}
