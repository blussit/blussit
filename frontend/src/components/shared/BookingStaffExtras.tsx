import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PencilLine, Plus } from "lucide-react";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { EDITED_FIELD_LABELS, staffBookingApi, type AddedService, type StaffBooking, type StatusHistoryRow } from "../../api/staffBookings";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { asUtcInstant, formatDateTime } from "../../lib/date";
import { bikeTypeIds, eligibleFor, offeredAddons } from "../../lib/serviceMix";
import { toTitle } from "../../lib/titleCase";
import type { Service } from "../../types";
import { Badge, Button, Input, Modal, Spinner } from "../ui";

/**
 * Staff-side pieces for the wallet / edits / on-site add-ons backend:
 * the "Customer Edited" chip, "Added On Site By …" lines, the edit diff on
 * the timeline, the payment-status words (incl. "Part Paid"), and the
 * manager's "Add Service" dialog.
 */

const rupees = (n?: number | null) => `₹${Math.round(Number(n || 0)).toLocaleString("en-IN")}`;
const when = (iso?: string | null) => (iso ? formatDateTime(asUtcInstant(iso)) : "");

export const fieldLabel = (f: string) => EDITED_FIELD_LABELS[f] || toTitle(f.replace(/_/g, " "));

/** Payment status words staff read ("Part Paid" for partially_paid). */
export const PAYMENT_STATUS_LABELS: Record<string, string> = {
  pending: "Pending",
  partially_paid: "Part Paid",
  paid: "Paid",
  refund_due: "Refund Due",
  refunded: "Refunded",
};
export const paymentTone = (s?: string | null): "neutral" | "warning" | "success" =>
  s === "paid" ? "success" : s === "partially_paid" || s === "refund_due" ? "warning" : "neutral";

/** "Customer Edited · Date, Slot · 7 Oct, 2:35 PM" — shown while it's news. */
export function CustomerEditedChip({ booking, withTime = false }: { booking: StaffBooking; withTime?: boolean }) {
  if (!booking.customer_edited_at) return null;
  const fields = [...new Set((booking.customer_edited_fields || []).filter((f) => f !== "total").map(fieldLabel))];
  return (
    <Badge tone="warning" data-testid="customer-edited-chip">
      <PencilLine className="h-3 w-3" /> Customer Edited
      {fields.length ? ` · ${fields.join(", ")}` : ""}
      {withTime ? ` · ${when(booking.customer_edited_at)}` : ""}
    </Badge>
  );
}

function addedLine(a: AddedService): string {
  const who = a.by_name ? `${a.by_name}${a.role ? ` (${toTitle(a.role)})` : ""}` : toTitle(a.role) || "Staff";
  return `Added On Site By ${who}`;
}

/** Every service added after booking, with who, when and the stage. */
export function AddedOnSiteLines({ cars, showCar = false }: { cars: StaffBooking[]; showCar?: boolean }) {
  const rows = cars.flatMap((c) => (c.added_services || []).map((a) => ({ car: c, a })));
  if (!rows.length) return null;
  return (
    <ul className="space-y-1.5" data-testid="added-on-site">
      {rows.map(({ car, a }, i) => (
        <li key={`${car.id}-${i}`} className="rounded-lg bg-amber-50 px-2.5 py-1.5 text-sm">
          <div className="flex items-start justify-between gap-3">
            <span className="min-w-0 font-medium text-[var(--color-text-primary)]">
              {toTitle(a.name) || "Service"}
              {a.qty > 1 ? ` ×${a.qty}` : ""}
              {showCar ? <span className="font-mono-num ml-1.5 text-xs font-normal text-gray-500">{car.booking_number}</span> : null}
            </span>
            <span className="font-mono-num shrink-0 font-semibold">{rupees(a.amount)}</span>
          </div>
          <p className="text-xs text-amber-900">
            {addedLine(a)}
            {a.stage === "completed" ? " · After The Wash" : ""}
            {a.at ? ` · ${when(a.at)}` : ""}
          </p>
          {a.note && <p className="text-xs text-gray-600">&ldquo;{a.note}&rdquo;</p>}
        </li>
      ))}
    </ul>
  );
}

function changeValue(field: string, v: unknown): string {
  if (v == null || v === "") return "—";
  if ((field === "total" || field === "total_amount") && !Number.isNaN(Number(v))) return rupees(Number(v));
  return String(v);
}

/** History rows that carry a field-by-field diff (edits, reschedules, add-ons). */
export function HistoryChanges({ rows }: { rows?: StatusHistoryRow[] | null }) {
  const withChanges = (rows || []).filter((r) => r.changes?.length || /edit|added|reschedul/i.test(r.note || ""));
  if (!withChanges.length) return null;
  return (
    <ol className="mt-2 space-y-2 border-t border-gray-100 pt-2" data-testid="history-changes">
      {[...withChanges].reverse().map((r, i) => (
        <li key={i} className="text-sm">
          <p className="flex flex-wrap items-baseline justify-between gap-x-3">
            <span className="font-medium text-[var(--color-text-primary)]">{r.changes?.some((c) => c.field === "added_services") ? "Services Added" : "Changed"}</span>
            <span className="font-mono-num text-xs text-[var(--color-text-secondary)]">{when(r.created_at)}</span>
          </p>
          {r.changes?.length ? (
            <ul className="mt-0.5 space-y-0.5">
              {r.changes.map((c, j) => (
                <li key={j} className="text-xs text-[var(--color-text-secondary)]">
                  <span className="font-semibold text-[var(--color-text-primary)]">{fieldLabel(c.field)}</span>
                  {c.field === "added_services" ? `: ${changeValue(c.field, c.to)}` : `: ${changeValue(c.field, c.from)} → ${changeValue(c.field, c.to)}`}
                  {c.booking_number ? <span className="font-mono-num ml-1 text-gray-400">{c.booking_number}</span> : null}
                </li>
              ))}
            </ul>
          ) : r.note ? (
            <p className="text-xs text-[var(--color-text-secondary)]">{r.note}</p>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

/** The price this car type pays for a service (standard, not first-wash). */
const priceFor = (s: Service, typeId?: string | null) => Number((typeId && s.vehicle_type_prices?.[typeId]) ?? s.price ?? 0);

/** Manager / admin: add services or add-ons to one car, any time until
 *  cancelled (even after completion). The added amount becomes due. */
export function AddServiceDialog({ booking, onClose, onAdded }: { booking: StaffBooking | null; onClose: () => void; onAdded?: () => void }) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const [picked, setPicked] = useState<Record<string, number>>({});
  const [note, setNote] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setPicked({});
    setNote("");
    setConfirming(false);
    setError("");
  }, [booking?.id]);

  const { data, isLoading } = useQuery({
    queryKey: ["services-for-add-on-site"],
    queryFn: () => catalogApi.services({ page_size: 100 }),
    enabled: !!booking,
    staleTime: 5 * 60 * 1000,
  });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list(), enabled: !!booking, staleTime: 30 * 60_000 });
  const typeId = booking?.vehicle_type || "";
  const menu = useMemo(() => {
    const active = (data?.data || []).filter((s) => s.is_active !== false);
    const mains = active.filter((s) => !s.is_addon && (!typeId || eligibleFor(s, typeId)));
    // Add-ons: the booking page's own chips for this type (the server refuses the rest).
    const addons = typeId ? offeredAddons(active, typeId, bikeTypeIds(vehicleTypes)) : [];
    return [...mains, ...addons];
  }, [data, typeId, vehicleTypes]);
  const lines = menu.filter((s) => picked[s.id]).map((s) => ({ s, qty: picked[s.id], amount: priceFor(s, typeId) * picked[s.id] }));
  const addedTotal = lines.reduce((sum, l) => sum + l.amount, 0);
  const currentTotal = Number(booking?.total_amount || 0);

  const save = useMutation({
    mutationFn: () =>
      staffBookingApi.addServices(booking!.id, {
        service_ids: lines.map((l) => l.s.id),
        quantities: Object.fromEntries(lines.filter((l) => l.qty > 1).map((l) => [l.s.id, l.qty])),
        note: note.trim() || undefined,
      }),
    onSuccess: (r) => {
      queryClient.invalidateQueries({
        predicate: (q) => typeof q.queryKey[0] === "string" && /^(center-bookings|admin-center-bookings|admin-bookings|booking|manager-dashboard|staff-booking)/.test(q.queryKey[0]),
      });
      pushToast({
        tone: "success",
        title: `Added ${rupees(r.added_total)}`,
        message: `New total ${rupees(r.booking.total_amount)} · ${rupees(r.visit_amount_due)} due now.`,
      });
      onAdded?.();
      onClose();
    },
    onError: (err) => {
      setError(getErrorMessage(err));
      setConfirming(false);
    },
  });

  const toggle = (s: Service) => setPicked((p) => (p[s.id] ? Object.fromEntries(Object.entries(p).filter(([k]) => k !== s.id)) : { ...p, [s.id]: 1 }));
  const setQty = (id: string, n: number) => setPicked((p) => ({ ...p, [id]: Math.max(1, Math.min(10, n)) }));

  return (
    <Modal open={!!booking} onClose={onClose} title={confirming ? "Confirm Added Services" : "Add Service"} maxWidth="max-w-md">
      {booking &&
        (confirming ? (
          <div className="space-y-4" data-testid="add-service-confirm">
            <ul className="space-y-1 text-sm">
              {lines.map((l) => (
                <li key={l.s.id} className="flex justify-between gap-3">
                  <span>
                    {toTitle(l.s.name)}
                    {l.qty > 1 ? ` ×${l.qty}` : ""}
                  </span>
                  <span className="font-mono-num">{rupees(l.amount)}</span>
                </li>
              ))}
            </ul>
            <div className="space-y-1 rounded-xl bg-gray-50 p-3 text-sm">
              <p className="flex justify-between gap-3 text-gray-600">
                <span>Current Total</span>
                <span className="font-mono-num">{rupees(currentTotal)}</span>
              </p>
              <p className="flex justify-between gap-3 text-gray-600">
                <span>Added</span>
                <span className="font-mono-num">+{rupees(addedTotal)}</span>
              </p>
              <p className="flex justify-between gap-3 border-t border-gray-200 pt-1 text-base font-bold text-black">
                <span>New Total</span>
                <span className="font-mono-num">{rupees(currentTotal + addedTotal)}</span>
              </p>
            </div>
            <p className="text-xs text-gray-500">The added {rupees(addedTotal)} becomes due — collected by cash or online. Captain pay doesn&apos;t change.</p>
            {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
            <div className="flex gap-2">
              <Button variant="outline" className="min-h-11 flex-1" onClick={() => setConfirming(false)}>
                Back
              </Button>
              <Button className="min-h-11 flex-1" isLoading={save.isPending} onClick={() => save.mutate()}>
                Add {rupees(addedTotal)}
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-sm text-[var(--color-text-secondary)]">
              <span className="font-mono-num font-semibold text-black">{booking.booking_number}</span>
              {booking.vehicle_type_name ? ` · ${toTitle(booking.vehicle_type_name)}` : ""} · now {rupees(currentTotal)}
            </p>
            {isLoading ? (
              <Spinner className="h-5 w-5" />
            ) : (
              <ul className="max-h-[45dvh] space-y-2 overflow-y-auto">
                {menu.map((s) => {
                  const on = !!picked[s.id];
                  return (
                    <li key={s.id}>
                      <div
                        className={`flex items-center justify-between gap-3 rounded-xl border px-3 py-2 ${on ? "border-black bg-[var(--color-primary-light)]" : "border-gray-200"}`}
                      >
                        <button type="button" onClick={() => toggle(s)} aria-pressed={on} className="flex min-h-11 min-w-0 flex-1 items-center gap-2 text-left">
                          <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border ${on ? "border-black bg-black text-white" : "border-gray-300"}`}>
                            {on ? "✓" : ""}
                          </span>
                          <span className="min-w-0">
                            <span className="block text-sm font-medium text-black">{toTitle(s.name)}</span>
                            <span className="block text-xs text-gray-500">
                              {s.is_addon ? "Add-On · " : ""}
                              {rupees(priceFor(s, typeId))}
                              {s.is_addon && on ? " each" : ""}
                            </span>
                          </span>
                        </button>
                        {on && s.is_addon && (
                          <span className="flex shrink-0 items-center gap-1">
                            <button type="button" className="h-11 w-11 rounded-full border border-gray-300 text-lg font-bold" onClick={() => setQty(s.id, picked[s.id] - 1)} aria-label="Fewer">
                              −
                            </button>
                            <span className="w-6 text-center font-mono-num font-bold">{picked[s.id]}</span>
                            <button type="button" className="h-11 w-11 rounded-full border border-gray-300 text-lg font-bold" onClick={() => setQty(s.id, picked[s.id] + 1)} aria-label="More">
                              +
                            </button>
                          </span>
                        )}
                      </div>
                    </li>
                  );
                })}
                {!menu.length && <li className="text-sm text-gray-500">No services for this car type.</li>}
              </ul>
            )}
            <Input label="Note (Optional)" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Customer asked at the door" />
            {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
            <div className="flex gap-2">
              <Button variant="outline" className="min-h-11 flex-1" onClick={onClose}>
                Cancel
              </Button>
              <Button className="min-h-11 flex-1" disabled={!lines.length} onClick={() => { setError(""); setConfirming(true); }}>
                <Plus className="h-4 w-4" /> {lines.length ? `Add ${rupees(addedTotal)}` : "Pick A Service"}
              </Button>
            </div>
          </div>
        ))}
    </Modal>
  );
}
