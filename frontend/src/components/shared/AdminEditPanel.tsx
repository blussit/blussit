import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, ShieldCheck, Trash2 } from "lucide-react";
import { adjustmentApi, newIdempotencyKey, type AdminEditResult, type StaffBooking } from "../../api/staffBookings";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { staffDirectoryApi } from "../../api/admin";
import { getErrorCode, getErrorMessage } from "../../lib/api-client";
import { useToast } from "../../context/ToastContext";
import { useConfirm } from "../../context/ConfirmContext";
import { formatSlot } from "../../lib/date";
import { Button, Input, Modal } from "../ui";
import { SlotPicker } from "./SlotPicker";
import { toTitle } from "../../lib/titleCase";

const REFRESH = /^(center-bookings|admin-center-bookings|admin-bookings|booking|staff-booking|manager-kpi|manager-dashboard|collections|customer-360|admin-dashboard)/;
const SECTIONS = ["Customer & Location", "Services & Prices", "Schedule & Captain", "Money", "Status"] as const;
type Section = (typeof SECTIONS)[number];
const STATUSES = ["awaiting_payment", "pending", "assigned", "rescheduled", "captain_on_the_way", "service_started", "completed", "cancelled"] as const;
const rupees = (n: unknown) => `₹${(Number(n) || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

interface Adj {
  delta: string;
  method: "cash" | "online";
  payment_key: string;
  note: string;
  affects_captain_cash: boolean;
}

interface Draft {
  customer_id: string;
  line1: string;
  landmark: string;
  city: string;
  state: string;
  pincode: string;
  latitude: string;
  longitude: string;
  recompute_center: boolean;
  recompute_travel: boolean;
  customer_notes: string;
  admin_notes: string;
  vehicle_type: string;
  service_ids: string[];
  remove_coupon: boolean;
  remove_added: number[];
  date: string;
  slot: string;
  captain_id: string;
  clear_captain: boolean;
  completed_at: string;
  discount: string;
  tip: string;
  tip_method: "cash" | "online";
  adjustments: Adj[];
  status: string;
  force_status: boolean;
  reason: string;
}

function initial(car: StaffBooking): Draft {
  const a = car.address_snapshot;
  return {
    customer_id: "",
    line1: a?.line1 || "",
    landmark: a?.landmark || "",
    city: a?.city || "",
    state: a?.state || "",
    pincode: a?.pincode || "",
    latitude: a?.latitude != null ? String(a.latitude) : "",
    longitude: a?.longitude != null ? String(a.longitude) : "",
    recompute_center: false,
    recompute_travel: false,
    customer_notes: car.customer_notes || "",
    admin_notes: car.admin_notes || "",
    vehicle_type: car.vehicle_type || "",
    service_ids: [...(car.service_ids || [])],
    remove_coupon: false,
    remove_added: [],
    date: "",
    slot: "",
    captain_id: car.captain_id || "",
    clear_captain: false,
    completed_at: "",
    discount: String(Number(car.manager_discount) || 0),
    tip: String(Number(car.tip_amount) || 0),
    tip_method: car.tip_method === "online" ? "online" : "cash",
    adjustments: [],
    status: car.status,
    force_status: false,
    reason: "",
  };
}

/** Only what the admin actually changed, in the shape PATCH /admin/bookings/{id} takes. */
function buildPayload(d: Draft, car: StaffBooking): Record<string, unknown> {
  const p: Record<string, unknown> = {};
  const a = car.address_snapshot;
  if (d.customer_id.trim()) p.customer_id = d.customer_id.trim();
  if (d.customer_notes !== (car.customer_notes || "")) p.customer_notes = d.customer_notes;
  if (d.admin_notes !== (car.admin_notes || "")) p.admin_notes = d.admin_notes;
  const addressChanged =
    d.line1 !== (a?.line1 || "") || d.landmark !== (a?.landmark || "") || d.city !== (a?.city || "") || d.state !== (a?.state || "") ||
    d.pincode !== (a?.pincode || "") || d.latitude !== (a?.latitude != null ? String(a.latitude) : "") || d.longitude !== (a?.longitude != null ? String(a.longitude) : "");
  if (addressChanged && d.line1.trim() && d.latitude !== "" && d.longitude !== "") {
    p.address = {
      line1: d.line1.trim(), landmark: d.landmark.trim() || undefined, city: d.city.trim() || undefined, state: d.state.trim() || undefined,
      pincode: d.pincode.trim() || undefined, latitude: Number(d.latitude), longitude: Number(d.longitude),
    };
  }
  if (d.recompute_center) p.recompute_center = true;
  if (d.recompute_travel) p.recompute_travel = true;
  const sameServices = d.service_ids.length === (car.service_ids || []).length && d.service_ids.every((s) => (car.service_ids || []).includes(s));
  if (!sameServices && d.service_ids.length) {
    p.service_ids = d.service_ids;
    const q: Record<string, number> = {};
    for (const id of d.service_ids) if ((car.service_quantities || {})[id]) q[id] = Number((car.service_quantities || {})[id]);
    if (Object.keys(q).length) p.service_quantities = q;
  }
  if (d.vehicle_type && d.vehicle_type !== car.vehicle_type) p.vehicle_type = d.vehicle_type;
  if (d.remove_coupon) p.remove_coupon = true;
  if (d.remove_added.length) p.remove_added_services = d.remove_added;
  if (d.date && d.slot) {
    p.scheduled_date = d.date;
    p.scheduled_slot = d.slot;
  }
  if (d.clear_captain) p.clear_captain = true;
  else if (d.captain_id && d.captain_id !== (car.captain_id || "")) p.captain_id = d.captain_id;
  if (d.completed_at) p.completed_at = new Date(d.completed_at).toISOString();
  if (d.discount !== "" && Number(d.discount) !== (Number(car.manager_discount) || 0)) p.manager_discount = Math.round(Number(d.discount));
  if (d.tip !== "" && (Number(d.tip) !== (Number(car.tip_amount) || 0) || (Number(d.tip) > 0 && d.tip_method !== (car.tip_method === "online" ? "online" : "cash")))) {
    p.tip_amount = Math.round(Number(d.tip));
    p.tip_method = d.tip_method;
  }
  const adjustments = d.adjustments
    .filter((x) => x.delta.trim() !== "" && Number(x.delta) !== 0)
    .map((x) => ({
      delta: Number(x.delta), method: x.method, payment_key: x.payment_key || undefined, note: x.note.trim() || undefined,
      affects_captain_cash: x.affects_captain_cash || undefined,
    }));
  if (adjustments.length) p.payment_adjustments = adjustments;
  if (d.status !== car.status) p.status = d.status;
  if (d.force_status) p.force_status = true;
  return p;
}

function Pills<T extends string>({ value, onChange, options, label }: { value: T; onChange: (v: T) => void; options: readonly T[]; label: (v: T) => string }) {
  return (
    <div className="flex flex-wrap gap-2" role="tablist">
      {options.map((o) => (
        <button
          key={o}
          type="button"
          role="tab"
          aria-selected={value === o}
          onClick={() => onChange(o)}
          className={`min-h-11 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors sm:min-h-0 ${value === o ? "border-black bg-[var(--color-primary-light)] text-black" : "border-gray-200 text-gray-600 hover:border-gray-400"}`}
        >
          {label(o)}
        </button>
      ))}
    </div>
  );
}

function Check({ checked, onChange, children }: { checked: boolean; onChange: (v: boolean) => void; children: React.ReactNode }) {
  return (
    <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-[var(--color-text-primary)] sm:min-h-0">
      <input type="checkbox" className="h-4 w-4 accent-black" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {children}
    </label>
  );
}

/**
 * Admin only: edit anything on a booking, in any state. Five groups (customer
 * & location, services & prices, schedule & captain, money, status), a live
 * "money impact" preview that is the real edit run and aborted, and a saved
 * history ("Edited By Admin"). Payments are never rewritten here — a
 * correction is added beside them.
 */
export function AdminEditPanel({ car, cars, open, onClose }: { car: StaffBooking; cars: StaffBooking[]; open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const confirm = useConfirm();
  const [section, setSection] = useState<Section>("Customer & Location");
  const [draft, setDraft] = useState<Draft>(() => initial(car));
  const [preview, setPreview] = useState<AdminEditResult | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState("");
  const [key, setKey] = useState(newIdempotencyKey());
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => ({ ...d, [k]: v }));

  useEffect(() => {
    if (open) {
      setDraft(initial(car));
      setSection("Customer & Location");
      setPreview(null);
      setPreviewError("");
      setError("");
      setKey(newIdempotencyKey());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, car.id]);

  const typeId = draft.vehicle_type || car.vehicle_type || "";
  const { data: types = [] } = useQuery({ queryKey: ["admin-edit-vehicle-types"], queryFn: () => vehicleTypeApi.list(true), enabled: open });
  const { data: services } = useQuery({
    queryKey: ["admin-edit-services", typeId],
    queryFn: () => catalogApi.services({ vehicle_type: typeId || undefined, active_only: true, page_size: 100 }),
    enabled: open && !!typeId,
  });
  const { data: captains } = useQuery({
    queryKey: ["admin-edit-captains", car.service_center_id],
    queryFn: () => staffDirectoryApi.captainsForCenter(car.service_center_id, { page: 1, page_size: 100 }),
    enabled: open && !!car.service_center_id,
  });

  const payload = useMemo(() => buildPayload(draft, car), [draft, car]);
  const changed = Object.keys(payload).length > 0;
  const payloadKey = JSON.stringify(payload);

  // Live money impact: the real edit, aborted (dry_run).
  useEffect(() => {
    if (!open || !changed) {
      setPreview(null);
      setPreviewError("");
      return;
    }
    let live = true;
    setPreviewing(true);
    const timer = window.setTimeout(() => {
      adjustmentApi
        .adminEdit(car.id, { ...payload, reason: draft.reason.trim().length >= 5 ? draft.reason.trim() : "Preview", dry_run: true, expected_version: car.version })
        .then((r) => live && (setPreview(r), setPreviewError("")))
        .catch((e) => live && (setPreview(null), setPreviewError(getErrorMessage(e))))
        .finally(() => live && setPreviewing(false));
    }, 700);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, payloadKey]);

  const save = useMutation({
    mutationFn: () => adjustmentApi.adminEdit(car.id, { ...payload, reason: draft.reason.trim(), expected_version: car.version, idempotency_key: key }),
    onSuccess: (r) => {
      queryClient.invalidateQueries({ predicate: (q) => typeof q.queryKey[0] === "string" && REFRESH.test(q.queryKey[0]) });
      queryClient.invalidateQueries({ queryKey: ["booking-group"] });
      queryClient.invalidateQueries({ queryKey: ["booking-adjustments"] });
      pushToast({ tone: "success", title: "Booking Updated", message: r.message });
      onClose();
    },
    onError: (e) => setError(getErrorCode(e) === "STALE_BOOKING" ? "This booking changed while you were editing — close this and open it again." : getErrorMessage(e)),
  });

  const submit = async () => {
    if (draft.reason.trim().length < 5) {
      setError("Say why you are changing this booking (at least 5 characters).");
      return;
    }
    const m = preview?.money;
    const ok = await confirm({
      title: `Save Changes To ${car.booking_number}?`,
      message:
        (preview?.changes || []).map((c) => `${c.label}: ${c.from ?? "—"} → ${c.to ?? "—"}`).join("; ") +
        (m ? `. Total ${rupees(m.before.total)} → ${rupees(m.after.total)}.` : "") +
        (m && m.customer_owes > 0 ? ` The customer owes ${rupees(m.customer_owes)} more.` : "") +
        (m && m.wallet_credit > 0 ? ` ${rupees(m.wallet_credit)} goes back to their wallet.` : "") +
        " This is recorded under your name.",
      confirmLabel: "Yes, Save",
      tone: "default",
    });
    if (ok) {
      setError("");
      save.mutate();
    }
  };

  const completed = car.status === "completed" || draft.status === "completed";
  const cancelled = car.status === "cancelled";
  const m = preview?.money;
  const payments = (car as unknown as { payment_keys?: string[] }).payment_keys || [];

  return (
    <Modal open={open} onClose={onClose} title={`Edit Booking (Admin) · ${car.booking_number}`} maxWidth="max-w-3xl">
      <div className="space-y-4" data-testid="admin-edit-panel">
        <p className="flex items-start gap-2 text-xs text-[var(--color-text-secondary)]">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            Only admins can do this, on any booking. Every change is saved with your name, the reason and the before and after.
            {cars.length > 1 ? " Date, time, address and captain apply to the whole visit; services and money to this vehicle only." : ""}
          </span>
        </p>
        <Pills value={section} onChange={setSection} options={SECTIONS} label={(s) => s} />

        {section === "Customer & Location" && (
          <div className="space-y-3">
            <Input
              label="Move To Another Customer (Customer ID)"
              value={draft.customer_id}
              onChange={(e) => set("customer_id", e.target.value.replace(/[^0-9a-fA-F]/g, "").slice(0, 24))}
              placeholder="24-character customer id"
              hint="Existing customers only. A booking that used the customer's wallet or plan stays with them."
            />
            <div className="grid gap-3 sm:grid-cols-2">
              <Input label="Address Line" value={draft.line1} maxLength={200} onChange={(e) => set("line1", e.target.value)} />
              <Input label="Landmark" value={draft.landmark} maxLength={120} onChange={(e) => set("landmark", e.target.value)} />
              <Input label="City" value={draft.city} maxLength={80} onChange={(e) => set("city", e.target.value)} />
              <Input label="State" value={draft.state} maxLength={80} onChange={(e) => set("state", e.target.value)} />
              <Input label="Pincode" inputMode="numeric" value={draft.pincode} onChange={(e) => set("pincode", e.target.value.replace(/\D/g, "").slice(0, 6))} />
              <span />
              <Input label="Map Pin Latitude" inputMode="decimal" value={draft.latitude} onChange={(e) => set("latitude", e.target.value.replace(/[^\d.-]/g, "").slice(0, 12))} hint="−90 to 90" />
              <Input label="Map Pin Longitude" inputMode="decimal" value={draft.longitude} onChange={(e) => set("longitude", e.target.value.replace(/[^\d.-]/g, "").slice(0, 12))} hint="−180 to 180" />
            </div>
            <Check checked={draft.recompute_center} onChange={(v) => set("recompute_center", v)}>
              Re-Check Which Service Center Serves This Pin
            </Check>
            <Check checked={draft.recompute_travel} onChange={(v) => set("recompute_travel", v)}>
              Recalculate The Distance Charge
            </Check>
            <Input label="Customer Notes" value={draft.customer_notes} maxLength={500} onChange={(e) => set("customer_notes", e.target.value)} />
            <Input label="Admin Notes (Staff Only)" value={draft.admin_notes} maxLength={500} onChange={(e) => set("admin_notes", e.target.value)} />
          </div>
        )}

        {section === "Services & Prices" && (
          <div className="space-y-3">
            {cancelled && <p className="text-sm text-[var(--color-text-secondary)]">A cancelled booking&apos;s money was settled to the wallet — prices can&apos;t be changed.</p>}
            <div>
              <p className="mb-1.5 text-sm font-medium">Car Type</p>
              <div className="flex flex-wrap gap-2">
                {types.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    disabled={cancelled}
                    onClick={() => set("vehicle_type", t.id)}
                    className={`min-h-11 rounded-full border px-3 py-1.5 text-xs font-medium sm:min-h-0 ${typeId === t.id ? "border-black bg-[var(--color-primary-light)] text-black" : "border-gray-200 text-gray-600 hover:border-gray-400"}`}
                  >
                    {toTitle(t.name)}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <p className="mb-1.5 text-sm font-medium">Services</p>
              <div className="max-h-56 space-y-1 overflow-y-auto rounded-xl border border-gray-100 p-2">
                {(services?.data || []).map((s) => (
                  <Check
                    key={s.id}
                    checked={draft.service_ids.includes(s.id)}
                    onChange={(v) => set("service_ids", v ? [...draft.service_ids, s.id] : draft.service_ids.filter((x) => x !== s.id))}
                  >
                    <span className="flex-1">{toTitle(s.name)}</span>
                    <span className="font-mono-num text-xs text-[var(--color-text-secondary)]">{rupees(s.vehicle_type_prices?.[typeId] ?? s.price)}</span>
                  </Check>
                ))}
                {!services && <p className="p-2 text-xs text-[var(--color-text-secondary)]">Loading…</p>}
              </div>
            </div>
            {car.coupon_code && (
              <Check checked={draft.remove_coupon} onChange={(v) => set("remove_coupon", v)}>
                Remove Coupon {car.coupon_code}
              </Check>
            )}
            {(car.added_services || []).length > 0 && (
              <div>
                <p className="mb-1.5 text-sm font-medium">Added On Site</p>
                {(car.added_services || []).map((a, i) => (
                  <Check key={i} checked={draft.remove_added.includes(i)} onChange={(v) => set("remove_added", v ? [...draft.remove_added, i] : draft.remove_added.filter((x) => x !== i))}>
                    Remove {a.name} ×{a.qty} ({rupees(a.amount)})
                  </Check>
                ))}
              </div>
            )}
          </div>
        )}

        {section === "Schedule & Captain" && (
          <div className="space-y-3">
            <p className="text-sm text-[var(--color-text-secondary)]">
              Now: <span className="font-semibold">{new Date(car.scheduled_date).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })} · {formatSlot(car.scheduled_slot)}</span>
            </p>
            {!["captain_on_the_way", "service_started"].includes(car.status) ? (
              <SlotPicker serviceCenterId={car.service_center_id} date={draft.date} onDateChange={(v) => set("date", v)} value={draft.slot} onChange={(v) => set("slot", v)} />
            ) : (
              <p className="text-sm text-[var(--color-text-secondary)]">The captain is on the job — the time can&apos;t move now.</p>
            )}
            <div>
              <p className="mb-1.5 text-sm font-medium">Captain</p>
              <select
                className="min-h-11 w-full rounded-xl border border-gray-200 bg-white px-3 text-sm"
                value={draft.clear_captain ? "" : draft.captain_id}
                onChange={(e) => {
                  set("clear_captain", e.target.value === "");
                  set("captain_id", e.target.value);
                }}
              >
                <option value="">No Captain</option>
                {(captains?.data || []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.full_name}
                  </option>
                ))}
              </select>
            </div>
            {completed && (
              <Input label="Completed At" type="datetime-local" value={draft.completed_at} onChange={(e) => set("completed_at", e.target.value)} hint="Leave empty to keep the time it was completed." />
            )}
          </div>
        )}

        {section === "Money" && (
          <div className="space-y-3">
            {cancelled && <p className="text-sm text-[var(--color-text-secondary)]">A cancelled booking&apos;s money was settled to the wallet — use the customer&apos;s wallet adjustment for corrections.</p>}
            <Input label="Discount (₹)" inputMode="numeric" value={draft.discount} disabled={cancelled} onChange={(e) => set("discount", e.target.value.replace(/\D/g, "").slice(0, 8))} hint="The total discount on this vehicle. Money already paid beyond the new total goes back to the customer's wallet." />
            <div className="grid gap-3 sm:grid-cols-2">
              <Input label="Tip (₹)" inputMode="numeric" value={draft.tip} disabled={!completed || cancelled} onChange={(e) => set("tip", e.target.value.replace(/\D/g, "").slice(0, 6))} hint={completed ? undefined : "Tips are recorded once the job is completed."} />
              <div>
                <p className="mb-1.5 text-sm font-medium">Tip Given In</p>
                <Pills value={draft.tip_method} onChange={(v) => set("tip_method", v)} options={["cash", "online"] as const} label={(v) => toTitle(v)} />
              </div>
            </div>
            <div className="rounded-xl border border-gray-100 p-3">
              <p className="mb-1 text-sm font-medium">Payment Corrections</p>
              <p className="mb-2 text-xs text-[var(--color-text-secondary)]">
                Received {rupees(car.amount_paid)}. Payment records are never changed or removed — a correction is added beside them (+ more was received, − less).
              </p>
              {draft.adjustments.map((a, i) => (
                <div key={i} className="mb-2 grid gap-2 rounded-lg bg-gray-50 p-2 sm:grid-cols-[1fr_auto]">
                  <div className="grid gap-2 sm:grid-cols-3">
                    <Input label="Amount (₹, + or −)" inputMode="decimal" value={a.delta} onChange={(e) => set("adjustments", draft.adjustments.map((x, j) => (j === i ? { ...x, delta: e.target.value.replace(/[^\d.-]/g, "").slice(0, 10) } : x)))} />
                    <div>
                      <p className="mb-1.5 text-sm font-medium">Method</p>
                      <Pills value={a.method} onChange={(v) => set("adjustments", draft.adjustments.map((x, j) => (j === i ? { ...x, method: v } : x)))} options={["cash", "online"] as const} label={(v) => toTitle(v)} />
                    </div>
                    <div>
                      <p className="mb-1.5 text-sm font-medium">Corrects Payment</p>
                      <select className="min-h-11 w-full rounded-xl border border-gray-200 bg-white px-2 text-sm" value={a.payment_key} onChange={(e) => set("adjustments", draft.adjustments.map((x, j) => (j === i ? { ...x, payment_key: e.target.value } : x)))}>
                        <option value="">None / New Money</option>
                        {payments.map((k) => (
                          <option key={k} value={k}>
                            {k}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="sm:col-span-3">
                      <Input label="Note" value={a.note} maxLength={200} onChange={(e) => set("adjustments", draft.adjustments.map((x, j) => (j === i ? { ...x, note: e.target.value } : x)))} />
                      {a.method === "cash" && (
                        <Check checked={a.affects_captain_cash} onChange={(v) => set("adjustments", draft.adjustments.map((x, j) => (j === i ? { ...x, affects_captain_cash: v } : x)))}>
                          This Cash Was In The Captain&apos;s Hands
                        </Check>
                      )}
                    </div>
                  </div>
                  <Button size="sm" variant="ghost" aria-label="Remove This Correction" onClick={() => set("adjustments", draft.adjustments.filter((_, j) => j !== i))}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
              {draft.adjustments.length < 5 && !cancelled && (
                <Button size="sm" variant="outline" onClick={() => set("adjustments", [...draft.adjustments, { delta: "", method: "cash", payment_key: "", note: "", affects_captain_cash: false }])}>
                  <Plus className="h-3.5 w-3.5" /> Add A Correction
                </Button>
              )}
            </div>
          </div>
        )}

        {section === "Status" && (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              {STATUSES.map((s) => (
                <button
                  key={s}
                  type="button"
                  disabled={cancelled && s !== "cancelled"}
                  onClick={() => set("status", s)}
                  className={`min-h-11 rounded-full border px-3 py-1.5 text-xs font-medium sm:min-h-0 ${draft.status === s ? "border-black bg-[var(--color-primary-light)] text-black" : "border-gray-200 text-gray-600 hover:border-gray-400"} disabled:opacity-40`}
                >
                  {toTitle(s.replace(/_/g, " "))}
                </button>
              ))}
            </div>
            <Check checked={draft.force_status} onChange={(v) => set("force_status", v)}>
              Override The Status Rules (Allow Any Step)
            </Check>
            <p className="text-xs text-[var(--color-text-secondary)]">
              Cancelling runs the normal cancel (seat, plan wash and paid money return to the customer, no charge). A cancelled booking can&apos;t be re-opened.
            </p>
          </div>
        )}

        {changed && (
          <div className="rounded-xl border border-[#F3E5B5] bg-white p-3 text-sm" data-testid="admin-edit-impact">
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Money Impact {previewing ? "· Checking…" : ""}</p>
            {previewError && <p className="text-[var(--color-error)]">{previewError}</p>}
            {m && (
              <>
                <p>
                  Total <span className="font-mono-num font-semibold">{rupees(m.before.total)}</span> → <span className="font-mono-num font-semibold">{rupees(m.after.total)}</span> · Paid{" "}
                  <span className="font-mono-num font-semibold">{rupees(m.after.amount_paid)}</span> · Due <span className="font-mono-num font-semibold">{rupees(m.after.amount_due)}</span>
                </p>
                {m.customer_owes > 0 && <p>The customer owes {rupees(m.customer_owes)} more.</p>}
                {m.wallet_credit > 0 && <p>{rupees(m.wallet_credit)} goes back to the customer&apos;s wallet.</p>}
                {m.wallet_refund_on_cancel > 0 && <p>{rupees(m.wallet_refund_on_cancel)} returns to the customer&apos;s wallet on cancel.</p>}
                {m.captain_wallet_delta !== 0 && <p>Captain wallet {m.captain_wallet_delta > 0 ? "+" : "−"}{rupees(Math.abs(m.captain_wallet_delta))}.</p>}
                {(preview?.warnings || []).map((w, i) => (
                  <p key={i} className="text-[var(--color-text-secondary)]">{w}</p>
                ))}
              </>
            )}
          </div>
        )}

        <Input label="Reason For The Change" value={draft.reason} maxLength={300} onChange={(e) => set("reason", e.target.value)} placeholder="Customer called to fix the address pin" hint="At least 5 characters — kept in the booking's history." />
        {error && (
          <p role="alert" className="text-sm text-[var(--color-error)]">
            {error}
          </p>
        )}
        <div className="flex gap-2">
          <Button variant="outline" className="min-h-11 flex-1" onClick={onClose}>
            Cancel
          </Button>
          <Button className="min-h-11 flex-1" disabled={!changed || !!previewError || previewing} isLoading={save.isPending} onClick={submit}>
            Save Changes
          </Button>
        </div>
      </div>
    </Modal>
  );
}
