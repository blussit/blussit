import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Minus, Plus } from "lucide-react";
import { Button, Input, Modal, Select, Switch } from "../ui";
import { serviceCenterApi } from "../../api/catalog";
import {
  planServices,
  planWithServices,
  premiumUsage,
  rupees,
  societyApi,
  type CarInput,
  type CouponPreview,
  type CustomCombo,
  type SocietyEnrollment,
  type SocietyQuote,
} from "../../api/society";
import { getErrorMessage } from "../../lib/api-client";
import { formatDay, formatSlot, todayIST } from "../../lib/date";
import { validateIndianMobile, validateIndianPlate } from "../../lib/validators";
import { titleCase } from "../public/landing/shared";

const IST = "Asia/Kolkata";
const dayIST = (offset: number) => new Intl.DateTimeFormat("en-CA", { timeZone: IST }).format(new Date(Date.now() + offset * 86_400_000));

/** Manager adds a resident (name, phone, flat, cars, plan) — optionally
 * with the cash already collected, which activates it at once. */
export function AddResidentModal({ open, onClose, societyId, onSaved }: { open: boolean; onClose: () => void; societyId: string; onSaved: (e: SocietyEnrollment) => void }) {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [flat, setFlat] = useState("");
  const [cars, setCars] = useState<CarInput[]>([{ vehicle_type: "", registration_number: "" }]);
  const [planId, setPlanId] = useState<string>("");
  const [custom, setCustom] = useState<CustomCombo | null>(null);
  const [cash, setCash] = useState(false);
  const [coupon, setCoupon] = useState("");
  const [appliedCoupon, setAppliedCoupon] = useState("");
  const [quote, setQuote] = useState<SocietyQuote | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const validPhone = validateIndianMobile(phone);
  const offered = useQuery({
    queryKey: ["society-plans-offered", societyId, validPhone],
    queryFn: () => societyApi.plansOffered(societyId, validPhone || undefined),
    enabled: open,
  });
  const data = offered.data;

  useEffect(() => {
    if (!open) return;
    setName(""); setPhone(""); setFlat(""); setCash(false); setError(""); setQuote(null); setPlanId(""); setCustom(null);
    setCoupon(""); setAppliedCoupon("");
    setCars([{ vehicle_type: "", registration_number: "" }]);
  }, [open]);
  useEffect(() => {
    if (!data) return;
    setCars((prev) => prev.map((c) => (c.vehicle_type ? c : { ...c, vehicle_type: data.vehicle_types[0]?.id || "" })));
    if (!planId && !custom) {
      if (data.plans[0]) setPlanId(data.plans[0].id);
      else if (data.customise.enabled && data.customise.premium_services[0]) {
        setCustom({ bucket_days: data.customise.bucket_day_options.at(-1)?.days || 25, premium_service_id: data.customise.premium_services[0].id, premium_count: data.customise.premium_count_options[0] || 1 });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const types = cars.map((c) => c.vehicle_type).filter(Boolean);
  const key = JSON.stringify({ planId, custom, types, appliedCoupon });
  useEffect(() => {
    if (!open || !types.length || (!planId && !custom)) return setQuote(null);
    let live = true;
    societyApi
      .quote(societyId, { ...(planId ? { plan_id: planId } : { custom: custom! }), vehicle_types: types, coupon_code: appliedCoupon || undefined })
      .then((q) => live && setQuote(q))
      .catch(() => live && setQuote(null));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, open]);

  const setCount = (n: number) => {
    const next = Math.max(1, Math.min(10, n));
    setCars((prev) => (next > prev.length ? [...prev, ...Array.from({ length: next - prev.length }, () => ({ vehicle_type: prev.at(-1)?.vehicle_type || "", registration_number: "" }))] : prev.slice(0, next)));
  };

  const save = async () => {
    if (name.trim().length < 2) return setError("Enter the resident's name.");
    if (!validPhone) return setError("Enter a valid mobile number.");
    if (!flat.trim()) return setError("Enter the flat number.");
    if (cars.some((c) => !c.vehicle_type || !validateIndianPlate(c.registration_number))) return setError("Every car needs a type and a valid number (MP09AB1234).");
    if (!planId && !custom) return setError("Pick a plan.");
    setSaving(true);
    setError("");
    try {
      const saved = await societyApi.addResident(societyId, {
        ...(planId ? { plan_id: planId } : { custom: custom! }),
        resident_name: name.trim(), phone: validPhone, flat: flat.trim(),
        cars: cars.map((c) => ({ vehicle_type: c.vehicle_type, registration_number: validateIndianPlate(c.registration_number)! })),
        collect_cash: cash,
        coupon_code: quote?.coupon?.valid ? quote.coupon.code : undefined,
      });
      onSaved(saved);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Add Resident" maxWidth="max-w-xl">
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-3">
          <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} />
          <Input label="Mobile" value={phone} onChange={(e) => setPhone(e.target.value)} inputMode="tel" />
          <Input label="Flat" value={flat} onChange={(e) => setFlat(e.target.value)} placeholder="B-402" />
        </div>
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium">Cars</p>
          <div className="flex items-center gap-2">
            <button type="button" aria-label="Fewer cars" className="rounded-full border p-1.5 disabled:opacity-40" disabled={cars.length <= 1} onClick={() => setCount(cars.length - 1)}><Minus className="h-4 w-4" /></button>
            <span className="w-5 text-center font-semibold">{cars.length}</span>
            <button type="button" aria-label="More cars" className="rounded-full border p-1.5" onClick={() => setCount(cars.length + 1)}><Plus className="h-4 w-4" /></button>
          </div>
        </div>
        {cars.map((c, i) => (
          <div key={i} className="grid gap-2 sm:grid-cols-2">
            <Select aria-label={`Car ${i + 1} type`} value={c.vehicle_type} onChange={(e) => setCars((p) => p.map((x, j) => (j === i ? { ...x, vehicle_type: e.target.value } : x)))}>
              {(data?.vehicle_types || []).map((t) => <option key={t.id} value={t.id}>{titleCase(t.name)}</option>)}
            </Select>
            <Input aria-label={`Car ${i + 1} number`} value={c.registration_number} placeholder="MP09AB1234"
              onChange={(e) => setCars((p) => p.map((x, j) => (j === i ? { ...x, registration_number: e.target.value.toUpperCase() } : x)))} />
          </div>
        ))}
        <Select label="Plan" value={planId || "__custom"} onChange={(e) => {
          if (e.target.value === "__custom" && data) {
            setPlanId("");
            setCustom({ bucket_days: data.customise.bucket_day_options.at(-1)?.days || 25, premium_service_id: data.customise.premium_services[0]?.id || "", premium_count: data.customise.premium_count_options[0] || 1 });
          } else { setPlanId(e.target.value); setCustom(null); }
        }}>
          {(data?.plans || []).map((p) => <option key={p.id} value={p.id}>{planWithServices(p)}{p.scope === "customer" ? " (Personal)" : ""}</option>)}
          {data?.customise.enabled && <option value="__custom">Customise…</option>}
        </Select>
        {custom && data && (
          <div className="grid gap-2 sm:grid-cols-3">
            <Select label="Bucket Wash" value={String(custom.bucket_days)} onChange={(e) => setCustom({ ...custom, bucket_days: Number(e.target.value) })}>
              {data.customise.bucket_day_options.map((o) => <option key={o.days} value={String(o.days)}>{titleCase(o.label)}</option>)}
            </Select>
            <Select label="Premium Wash" value={custom.premium_service_id} onChange={(e) => setCustom({ ...custom, premium_service_id: e.target.value })}>
              {data.customise.premium_services.map((s) => <option key={s.id} value={s.id}>{titleCase(s.name)}</option>)}
            </Select>
            <Select label="Per Month" value={String(custom.premium_count)} onChange={(e) => setCustom({ ...custom, premium_count: Number(e.target.value) })}>
              {data.customise.premium_count_options.map((n) => <option key={n} value={String(n)}>{n}</option>)}
            </Select>
          </div>
        )}
        <CouponRow value={coupon} onChange={setCoupon} onApply={() => setAppliedCoupon(coupon.trim().toUpperCase())}
          onClear={() => { setCoupon(""); setAppliedCoupon(""); }} check={appliedCoupon ? quote?.coupon ?? null : null} />
        {quote ? (
          <>
          <p className="text-xs text-gray-500" data-testid="quote-covers">
            {[planServices(quote), ...quote.cars.map((c, i) => `Car ${i + 1} · ${titleCase(c.vehicle_type_name) || "Car"}`)].filter(Boolean).join(" · ")}
          </p>
          <PriceSummary subtotal={quote.total} mrp={quote.mrp_total} discount={quote.discount} code={quote.coupon?.valid ? quote.coupon.code : null} payable={quote.payable_total} />
          </>
        ) : (
          <p className="text-sm text-gray-500">Pick a plan to see the price.</p>
        )}
        <Switch checked={cash} onChange={setCash} label="Cash Collected" description="Activates the plan now and records you as the collector." />
        {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void save()} isLoading={saving}>{cash ? "Add & Activate" : "Add Resident"}</Button>
        </div>
      </div>
    </Modal>
  );
}

/** Manager books premium washes for a resident (any day from today; the
 * resident's own one-day-ahead rule doesn't bind staff). */
export function StaffBookPremiumModal({ open, onClose, societyId, centerId, enrollment, onBooked }: {
  open: boolean; onClose: () => void; societyId: string; centerId: string; enrollment: SocietyEnrollment | null; onBooked: (msg: string) => void;
}) {
  const cars = useMemo(() => (enrollment?.cars || []).filter((c) => c.status === "active" && c.subscription?.status === "active" && (c.subscription?.remaining ?? 0) > 0), [enrollment]);
  const [picked, setPicked] = useState<string[]>([]);
  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => dayIST(i)), []);
  const [day, setDay] = useState(todayIST());
  const [slot, setSlot] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const slots = useQuery({ queryKey: ["society-slots", centerId, day], queryFn: () => serviceCenterApi.availableSlots(centerId, day), enabled: open && !!centerId });
  useEffect(() => {
    if (open) { setPicked(cars.slice(0, 1).map((c) => c.subscription!.id)); setSlot(""); setError(""); setDay(dayIST(1)); }
  }, [open, cars]);

  const book = async () => {
    setSaving(true);
    setError("");
    try {
      const r = await societyApi.bookPremium(societyId, { subscription_ids: picked, scheduled_date: day, scheduled_slot: slot });
      onBooked(`Booked ${r.bookings.map((b) => b.booking_number).join(", ")} for ${formatDay(day)}, ${r.slot_label}.`);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title={`Book Premium Wash${enrollment ? ` — ${enrollment.resident_name}` : ""}`}>
      {!cars.length ? (
        <p className="text-sm text-gray-500">No car on this plan has premium washes left this cycle.</p>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {cars.map((c) => {
              const id = c.subscription!.id;
              const on = picked.includes(id);
              return (
                <button key={id} type="button" onClick={() => setPicked((p) => (on ? p.filter((x) => x !== id) : [...p, id]))}
                  className={`rounded-xl border px-3 py-2 text-sm font-semibold ${on ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0A66F0]" : "border-[#E4E9F1]"}`}>
                  <span className="font-mono">{c.registration_number}</span>{c.vehicle_type_name ? ` · ${titleCase(c.vehicle_type_name)}` : ""}
                  <span className="block text-xs font-normal text-gray-600">{premiumUsage(enrollment?.premium_service_name, c.subscription!.remaining, c.subscription!.total)} Left</span>
                </button>
              );
            })}
          </div>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {days.map((d) => (
              <button key={d} type="button" onClick={() => { setDay(d); setSlot(""); }}
                className={`shrink-0 rounded-xl border px-3 py-2 text-sm ${day === d ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0A66F0] font-semibold" : "border-[#E4E9F1]"}`}>{formatDay(d)}</button>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-2">
            {(slots.data || []).map((s) => (
              <button key={s.key} type="button" disabled={s.status === "full"} onClick={() => setSlot(s.key)}
                className={`rounded-xl border px-3 py-2 text-sm disabled:opacity-40 ${slot === s.key ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0A66F0] font-semibold" : "border-[#E4E9F1]"}`}>{formatSlot(s.key)}</button>
            ))}
          </div>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            <Button disabled={!picked.length || !slot} isLoading={saving} onClick={() => void book()}>Book</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

/** Coupon code box + apply — the result line shows the discount or why not. */
function CouponRow({ value, onChange, onApply, onClear, check }: {
  value: string; onChange: (v: string) => void; onApply: () => void; onClear: () => void; check: { valid: boolean; discount: number; error: string | null; code: string } | null;
}) {
  return (
    <div>
      <div className="flex items-end gap-2">
        <Input label="Coupon (Optional)" value={value} onChange={(e) => onChange(e.target.value.toUpperCase().replace(/\s/g, "").slice(0, 30))}
          placeholder="e.g. SOCIETY10" aria-label="Coupon code" />
        <Button variant="outline" disabled={!value.trim()} onClick={onApply}>Apply</Button>
        {check && <Button variant="ghost" onClick={onClear}>Remove</Button>}
      </div>
      {check && (
        <p className={`mt-1 text-xs ${check.valid ? "text-[#0A66F0]" : "text-[var(--color-error)]"}`} data-testid="coupon-result">
          {check.valid ? `${check.code} applied — ${rupees(check.discount)} off` : check.error || "That coupon can't be used."}
        </p>
      )}
    </div>
  );
}

/** Subtotal, coupon and what's due — whole rupees. */
export function PriceSummary({ subtotal, mrp, discount, code, payable, suffix = "a month" }: {
  subtotal: number; mrp?: number; discount: number; code?: string | null; payable: number; suffix?: string;
}) {
  return (
    <div className="rounded-xl border border-[#E4E9F1] bg-[#F7F9FC] px-3 py-2 text-sm" data-testid="price-summary">
      <div className="flex justify-between gap-3"><span className="text-[#5F6878]">Plan Total</span>
        <span>{mrp && mrp > subtotal && <span className="mr-1.5 text-gray-400 line-through">{rupees(mrp)}</span>}{rupees(subtotal)}</span></div>
      {discount > 0 && <div className="flex justify-between gap-3 text-[#0A66F0]"><span>Coupon{code ? ` ${code}` : ""}</span><span>−{rupees(discount)}</span></div>}
      <div className="mt-1 flex justify-between gap-3 border-t border-[#E4E9F1] pt-1 font-semibold text-[#0E1A33]"><span>To Collect</span><span>{rupees(payable)} {suffix}</span></div>
    </div>
  );
}

/** Manager collects cash — first activation ("Mark paid") or a renewal —
 * with an optional coupon. The price shown comes from the server preview,
 * so it's exactly what gets recorded. */
export function CollectCashModal({ open, onClose, enrollment, mode, onDone }: {
  open: boolean; onClose: () => void; enrollment: SocietyEnrollment | null; mode: "activate" | "renew"; onDone: (msg: string) => void;
}) {
  const [coupon, setCoupon] = useState("");
  const [applied, setApplied] = useState<string>("");
  const [preview, setPreview] = useState<CouponPreview | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const renewal = mode === "renew";

  useEffect(() => {
    if (!open || !enrollment) return;
    const own = renewal ? "" : enrollment.coupon_code || "";
    setCoupon(own); setApplied(own); setError(""); setPreview(null);
  }, [open, enrollment, renewal]);

  useEffect(() => {
    if (!open || !enrollment) return;
    let live = true;
    societyApi.couponPreview(enrollment.id, { coupon_code: applied || undefined, renewal: renewal || undefined })
      .then((p) => live && setPreview(p))
      .catch((err) => live && setError(getErrorMessage(err)));
    return () => { live = false; };
  }, [open, enrollment, applied, renewal]);

  if (!enrollment) return null;
  const good = preview?.coupon?.valid ? preview.coupon.code : "";
  const removing = !renewal && !!enrollment.coupon_code && !applied;
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      if (renewal) await societyApi.renew(enrollment.id, good || undefined);
      else await societyApi.activate(enrollment.id, enrollment.revision, good ? { coupon_code: good } : removing ? { remove_coupon: true } : undefined);
      onDone(renewal ? `Renewed — ${rupees(preview?.payable)} collected` : `Marked paid — ${rupees(preview?.payable)} collected, plan active`);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title={renewal ? `Renew — ${enrollment.resident_name}` : `Mark Paid — ${enrollment.resident_name}`}>
      <div className="space-y-3">
        <p className="text-sm text-[#5F6878]"><b className="font-semibold text-[#0E1A33]">{planWithServices(enrollment)}</b> · {enrollment.flat} · {enrollment.cars.filter((c) => c.status !== "cancelled").map((c) => [c.registration_number, titleCase(c.vehicle_type_name)].filter(Boolean).join(" · ")).join(", ")}. {renewal ? "Adds the next plan month (to the same date next month) for every car in its renewal window." : "The plan starts now for every car."}</p>
        <CouponRow value={coupon} onChange={setCoupon} onApply={() => setApplied(coupon.trim().toUpperCase())}
          onClear={() => { setCoupon(""); setApplied(""); }} check={applied ? preview?.coupon ?? null : null} />
        {preview ? <PriceSummary subtotal={preview.subtotal} discount={preview.discount} code={good} payable={preview.payable} suffix="cash" /> : !error && <p className="text-sm text-gray-500">Working out the price…</p>}
        {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void save()} isLoading={saving} disabled={!preview || (!!applied && !good)}>
            {preview ? `${renewal ? "Renew" : "Mark"} ${rupees(preview.payable)} Collected` : "Collect"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
