/**
 * /society/:token — the link a manager shares with a housing society.
 * Not signed in (or no plan here yet): a short enrollment form — details,
 * cars, plan (or customise), price, OTP, then pay now or request. Signed in
 * with a plan here: the resident's society hub — cars, premium washes left,
 * bucket washes this cycle, book a premium wash, pay / renew.
 * See docs/SOCIETY_PLANS.md.
 */
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarDays, Car, Check, CheckCircle2, Minus, Plus, Sparkles, Tag } from "lucide-react";
import {
  bucketUsage,
  carLabel,
  planCovers,
  planWithServices,
  premiumUsage,
  societyFormApi,
  societyIssueApi,
  rupees,
  type CarInput,
  type CouponPreview,
  type CustomCombo,
  type SocietyEnrollment,
  type SocietyFormData,
  type SocietyHub,
  type SocietyQuote,
} from "../../api/society";
import { SocietyIssueModal } from "../../components/society/SocietyIssueModal";
import { ResidentScheduleCard } from "../../components/society/schedule/ResidentScheduleCard";
import { serviceCenterApi } from "../../api/catalog";
import { useAuth } from "../../context/AuthContext";
import { tokenStorage, getErrorMessage } from "../../lib/api-client";
import { validateIndianMobile, validateIndianPlate } from "../../lib/validators";
import { formatDay, formatShortDate, formatSlot } from "../../lib/date";
import { payWithRazorpay, paymentErrorMessage } from "../../lib/razorpay";
import { SocietyOtpModal, type SocietyPhoneProof } from "../../components/society/SocietyOtpModal";
import { Spinner } from "../../components/ui";
import { titleCase } from "../../components/public/landing/shared";

const card = "rounded-[18px] border border-[#E4E9F1] bg-white p-4 sm:p-5";
const label = "mb-1.5 block text-[13px] font-semibold text-[#0E1A33]";
const input =
  "h-12 w-full rounded-[14px] border border-[#E4E9F1] bg-white px-4 text-[15px] text-[#0E1A33] outline-none transition placeholder:text-[#9AA3B2] focus:border-[#0A66F0] focus:ring-4 focus:ring-[#0A66F0]/10";
const primaryBtn =
  "flex h-12 w-full items-center justify-center gap-2 rounded-[14px] bg-[#0A66F0] px-5 text-[15px] font-bold text-white transition hover:bg-[#0857CC] disabled:cursor-not-allowed disabled:opacity-50";
const ctaBtn =
  "flex h-12 w-full items-center justify-center gap-2 rounded-[14px] bg-[#FFD21F] px-5 text-[15px] font-bold text-[#0E1A33] transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-50";
const ghostBtn =
  "flex h-11 items-center justify-center gap-2 rounded-[14px] border border-[#E4E9F1] bg-white px-4 text-sm font-semibold text-[#0E1A33] transition hover:border-[#0A66F0] hover:text-[#0A66F0] disabled:opacity-50";
const chip = (on: boolean) =>
  `min-h-10 rounded-[12px] border px-3.5 py-2 text-sm font-semibold transition ${on ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0A66F0]" : "border-[#E4E9F1] bg-white text-[#0E1A33] hover:border-[#0A66F0]/50"}`;

const IST = "Asia/Kolkata";
const dayIST = (offset: number) => new Intl.DateTimeFormat("en-CA", { timeZone: IST }).format(new Date(Date.now() + offset * 86_400_000));

const statusText: Record<SocietyEnrollment["status"], string> = {
  requested: "Request Sent",
  awaiting_payment: "Awaiting Payment",
  active: "Active",
  cancelled: "Cancelled",
};

export default function SocietyFormPage() {
  const { token = "" } = useParams();
  const { user, isLoading: authLoading } = useAuth();
  const isCustomer = user?.role === "customer";
  const form = useQuery({ queryKey: ["society-form", token, isCustomer ? user?.id : "anon"], queryFn: () => societyFormApi.get(token), retry: false });
  const hub = useQuery({ queryKey: ["society-hub", token, user?.id], queryFn: () => societyFormApi.me(token), enabled: isCustomer, retry: false });
  const [adding, setAdding] = useState(false);
  const [flash, setFlash] = useState("");

  useEffect(() => {
    document.title = form.data ? `${form.data.society.name} — Blussit Society Plan` : "Blussit Society Plan";
  }, [form.data]);

  if (form.isLoading || authLoading) {
    return (
      <Shell>
        <div className="flex justify-center py-24"><Spinner /></div>
      </Shell>
    );
  }
  if (form.isError || !form.data) {
    return (
      <Shell>
        <div className={`${card} mt-6 text-center`}>
          <p className="text-lg font-bold text-[#0E1A33]">This Link Isn't Active</p>
          <p className="mt-1 text-sm text-[#5F6878]">Ask your society manager for the latest link.</p>
        </div>
      </Shell>
    );
  }
  const hasPlan = !!hub.data?.enrollments.length;
  return (
    <Shell society={form.data.society}>
      {hasPlan && !adding ? (
        <ResidentHub token={token} hub={hub.data!} flash={flash} onAdd={() => { setFlash(""); setAdding(true); }} />
      ) : (
        <EnrollForm token={token} data={form.data} onCancel={hasPlan ? () => setAdding(false) : undefined} onDone={(message) => { setFlash(message); setAdding(false); }} />
      )}
    </Shell>
  );
}

function Shell({ society, children }: { society?: SocietyFormData["society"]; children: React.ReactNode }) {
  return (
    <main className="min-h-dvh overflow-x-hidden bg-[#F7F9FC] text-[#0E1A33]">
      <header className="border-b border-[#E4E9F1] bg-white">
        <div className="mx-auto flex max-w-xl items-center justify-between px-4 py-3">
          <a href="/" aria-label="Blussit home">
            <img src="/img/blussit-logo-480.webp" alt="Blussit" className="h-7 w-auto" />
          </a>
          <span className="rounded-full bg-[#EEF3FA] px-3 py-1 text-xs font-semibold text-[#0A66F0]">Society Plan</span>
        </div>
      </header>
      <div className="mx-auto max-w-xl px-4 pb-16 pt-5">
        {society && (
          <div className="mb-4">
            <h1 className="text-[22px] font-extrabold leading-tight sm:text-2xl">{society.name}</h1>
            <p className="mt-0.5 text-sm text-[#5F6878]">{[society.area, society.city].filter(Boolean).join(", ") || "Daily Car Wash At Your Society"}</p>
          </div>
        )}
        {children}
      </div>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Enrollment form
// ---------------------------------------------------------------------------

function EnrollForm({ token, data, onCancel, onDone }: { token: string; data: SocietyFormData; onCancel?: () => void; onDone: (message: string) => void }) {
  const { user, refreshUser } = useAuth();
  const queryClient = useQueryClient();
  const isCustomer = user?.role === "customer";
  const isStaff = !!user && !isCustomer;
  const firstType = data.vehicle_types[0]?.id || "";
  const [name, setName] = useState(isCustomer ? user?.full_name || "" : "");
  const [phone, setPhone] = useState(isCustomer ? user?.phone || "" : "");
  const [flat, setFlat] = useState("");
  const [cars, setCars] = useState<CarInput[]>([{ vehicle_type: firstType, registration_number: "" }]);
  const [planId, setPlanId] = useState<string | null>(data.plans[0]?.id ?? null);
  const [custom, setCustom] = useState<CustomCombo>({
    bucket_days: data.customise.bucket_day_options[data.customise.bucket_day_options.length - 1]?.days ?? 25,
    premium_service_id: data.customise.premium_services[0]?.id ?? "",
    premium_count: data.customise.premium_count_options.includes(2) ? 2 : data.customise.premium_count_options[0] ?? 1,
  });
  const customising = planId === null;
  const [quote, setQuote] = useState<SocietyQuote | null>(null);
  const [quoteError, setQuoteError] = useState("");
  const [error, setError] = useState("");
  const [otpOpen, setOtpOpen] = useState(false);
  const [otpError, setOtpError] = useState("");
  const [payNow, setPayNow] = useState(data.online_payment);
  const [couponInput, setCouponInput] = useState("");
  const [coupon, setCoupon] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ enrollment: SocietyEnrollment; paid: boolean; note?: string } | null>(null);

  const types = useMemo(() => cars.map((c) => c.vehicle_type).filter(Boolean), [cars]);
  const choiceKey = JSON.stringify({ planId, custom: customising ? custom : null, types, coupon });

  useEffect(() => {
    if (!types.length || (!planId && !(data.customise.enabled && custom.premium_service_id))) {
      setQuote(null);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      societyFormApi
        .quote(token, { ...(planId ? { plan_id: planId } : { custom }), vehicle_types: types, coupon_code: coupon || undefined })
        .then((q) => live && (setQuote(q), setQuoteError("")))
        .catch((err) => live && (setQuote(null), setQuoteError(getErrorMessage(err))));
    }, 250);
    return () => {
      live = false;
      clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [choiceKey, token]);

  const setCount = (n: number) => {
    const next = Math.max(1, Math.min(6, n));
    setCars((prev) => (next > prev.length ? [...prev, ...Array.from({ length: next - prev.length }, () => ({ vehicle_type: prev[prev.length - 1]?.vehicle_type || firstType, registration_number: "" }))] : prev.slice(0, next)));
  };

  const validate = (): string => {
    if (name.trim().length < 2) return "Enter your name.";
    if (!validateIndianMobile(phone)) return "Enter a valid 10-digit mobile number.";
    if (!flat.trim()) return "Enter your flat or house number.";
    for (const [i, c] of cars.entries()) {
      if (!c.vehicle_type) return `Pick the type of car ${i + 1}.`;
      if (!validateIndianPlate(c.registration_number)) return `Enter car ${i + 1}'s number, e.g. MP09AB1234.`;
    }
    const plates = cars.map((c) => validateIndianPlate(c.registration_number));
    if (new Set(plates).size !== plates.length) return "Each car can be added once.";
    if (!quote) return quoteError || "Pick a plan.";
    if (coupon && quote.coupon && !quote.coupon.valid) return `${quote.coupon.error || "That coupon can't be used."} Remove it to continue.`;
    return "";
  };

  const submit = async (proof: SocietyPhoneProof | null) => {
    setBusy(true);
    setError("");
    setOtpError("");
    try {
      const normalizedPhone = validateIndianMobile(phone)!;
      const result = await societyFormApi.enroll(token, {
        ...(planId ? { plan_id: planId } : { custom }),
        resident_name: name.trim(),
        phone: normalizedPhone,
        flat: flat.trim(),
        cars: cars.map((c) => ({ vehicle_type: c.vehicle_type, registration_number: validateIndianPlate(c.registration_number)! })),
        pay_now: payNow && data.online_payment,
        coupon_code: quote?.coupon?.valid ? quote.coupon.code : undefined,
        ...(proof || {}),
      });
      setOtpOpen(false);
      // Signed in as the resident now — unless a staff member is testing
      // the link on their own browser (never replace their session).
      if (result.auth && !isStaff) {
        tokenStorage.set(result.auth.access_token, result.auth.refresh_token);
        await refreshUser();
      }
      let paid = false;
      let note: string | undefined;
      if (payNow && data.online_payment && !isStaff) {
        try {
          await payWithRazorpay(
            { purpose: "society", society_enrollment_id: result.enrollment.id },
            { name: name.trim(), contact: normalizedPhone },
          );
          paid = true;
        } catch (err) {
          note = `${paymentErrorMessage(err)} Your request is saved — you can pay from this page any time.`;
        }
      }
      setDone({ enrollment: result.enrollment, paid, note });
      // A signed-in resident lands on their hub (the page swaps over as soon
      // as it loads) — this line rides along as its banner.
      onDone(paid ? "You're all set — your plan is active." : note || "Request sent — your society manager will confirm and collect payment.");
      queryClient.invalidateQueries({ queryKey: ["society-hub", token] });
      queryClient.invalidateQueries({ queryKey: ["my-subscriptions"] });
    } catch (err) {
      const message = getErrorMessage(err);
      if (otpOpen && /code|otp|verify/i.test(message)) setOtpError(message);
      else {
        setOtpOpen(false);
        setError(message);
      }
    } finally {
      setBusy(false);
    }
  };

  const start = () => {
    const problem = validate();
    if (problem) {
      setError(problem);
      return;
    }
    setError("");
    const signedInHere = isCustomer && validateIndianMobile(phone) === user?.phone;
    if (signedInHere) void submit(null);
    else setOtpOpen(true);
  };

  if (done) {
    return (
      <div className={`${card} text-center`}>
        <CheckCircle2 className="mx-auto h-12 w-12 text-[#0A66F0]" />
        <p className="mt-3 text-xl font-extrabold">{done.paid ? "You're All Set" : "Request Sent"}</p>
        <p className="mx-auto mt-1 max-w-sm text-sm text-[#5F6878]">
          {done.paid
            ? "Your plan is active. Your captain starts with the next daily round."
            : "Your society manager will confirm and collect payment. We'll message you once it's active."}
        </p>
        {done.note && <p className="mt-3 rounded-[12px] bg-[#FFF8DB] px-3 py-2 text-sm text-[#7A5B00]">{done.note}</p>}
        <div className="mt-5 grid gap-2">
          <button className={primaryBtn} onClick={() => window.location.reload()}>Done</button>
        </div>
      </div>
    );
  }

  const planOptions = data.plans;
  const firstPrice = (prices: Record<string, { price: number; mrp: number }>) => {
    const t = types[0] && prices[types[0]] ? prices[types[0]] : Object.values(prices)[0];
    return t;
  };

  return (
    <div className="space-y-4">
      {isStaff && (
        <p className="rounded-[14px] bg-[#FFF8DB] px-4 py-3 text-sm text-[#7A5B00]">
          You're signed in as staff. Residents fill this form themselves — to add someone yourself, use "Add Resident" in the panel.
        </p>
      )}

      <section className={card}>
        <h2 className="mb-3 text-base font-bold">Your Details</h2>
        <div className="grid gap-3">
          <div>
            <label className={label} htmlFor="soc-name">Name</label>
            <input id="soc-name" className={input} value={name} onChange={(e) => setName(e.target.value)} placeholder="Full name" autoComplete="name" />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className={label} htmlFor="soc-phone">Mobile</label>
              <input id="soc-phone" className={input} value={phone} onChange={(e) => setPhone(e.target.value.replace(/[^\d+ ]/g, "").slice(0, 14))} placeholder="10-digit number" inputMode="tel" autoComplete="tel" />
            </div>
            <div>
              <label className={label} htmlFor="soc-flat">Flat / House No.</label>
              <input id="soc-flat" className={input} value={flat} onChange={(e) => setFlat(e.target.value.slice(0, 60))} placeholder="e.g. B-402" />
            </div>
          </div>
        </div>
      </section>

      <section className={card}>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-base font-bold">Your Cars</h2>
          <div className="flex items-center gap-2" aria-label="Number of cars">
            <button type="button" aria-label="One car fewer" className="flex h-9 w-9 items-center justify-center rounded-full border border-[#E4E9F1] disabled:opacity-40" disabled={cars.length <= 1} onClick={() => setCount(cars.length - 1)}>
              <Minus className="h-4 w-4" />
            </button>
            <span className="w-6 text-center text-base font-bold" data-testid="car-count">{cars.length}</span>
            <button type="button" aria-label="One more car" className="flex h-9 w-9 items-center justify-center rounded-full border border-[#E4E9F1] disabled:opacity-40" disabled={cars.length >= 6} onClick={() => setCount(cars.length + 1)}>
              <Plus className="h-4 w-4" />
            </button>
          </div>
        </div>
        <div className="space-y-3">
          {cars.map((c, i) => (
            <div key={i} className="rounded-[14px] bg-[#F7F9FC] p-3">
              <p className="mb-2 text-xs font-semibold text-[#5F6878]">Car {i + 1}</p>
              <div className="mb-2 flex flex-wrap gap-2">
                {data.vehicle_types.map((t) => (
                  <button key={t.id} type="button" className={chip(c.vehicle_type === t.id)} onClick={() => setCars((prev) => prev.map((x, j) => (j === i ? { ...x, vehicle_type: t.id } : x)))}>
                    {titleCase(t.name)}
                  </button>
                ))}
              </div>
              <input
                className={input}
                aria-label={`Car ${i + 1} number`}
                value={c.registration_number}
                onChange={(e) => setCars((prev) => prev.map((x, j) => (j === i ? { ...x, registration_number: e.target.value.toUpperCase().slice(0, 14) } : x)))}
                placeholder="Car number, e.g. MP09AB1234"
                autoCapitalize="characters"
              />
            </div>
          ))}
        </div>
      </section>

      <section className={card}>
        <h2 className="mb-1 text-base font-bold">Choose A Plan</h2>
        <p className="mb-3 text-sm text-[#5F6878]">Per car, per month. Premium washes are booked a day ahead.</p>
        <div className="grid gap-2.5">
          {planOptions.map((p) => {
            const on = planId === p.id;
            const price = firstPrice(p.prices);
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => setPlanId(p.id)}
                className={`flex w-full items-start gap-3 rounded-[16px] border p-3.5 text-left transition ${on ? "border-[#0A66F0] bg-[#E8F0FE]" : "border-[#E4E9F1] bg-white hover:border-[#0A66F0]/50"}`}
              >
                <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border ${on ? "border-[#0A66F0] bg-[#0A66F0] text-white" : "border-[#C9D2E0]"}`}>
                  {on && <Check className="h-3.5 w-3.5" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[15px] font-bold leading-snug">{titleCase(p.name)}</span>
                  {planCovers(p) ? <span className="mt-0.5 block text-[13px] text-[#5F6878]">{planCovers(p)}</span> : null}
                </span>
                {price && (
                  <span className="shrink-0 text-right">
                    <span className="block text-[15px] font-extrabold">{rupees(price.price)}</span>
                    {price.mrp > price.price && <span className="block text-xs text-[#9AA3B2] line-through">{rupees(price.mrp)}</span>}
                  </span>
                )}
              </button>
            );
          })}
          {data.customise.enabled && data.customise.premium_services.length > 0 && (
            <div className={`rounded-[16px] border p-3.5 transition ${customising ? "border-[#0A66F0] bg-[#E8F0FE]" : "border-[#E4E9F1] bg-white"}`}>
              <button type="button" className="flex w-full items-center gap-3 text-left" onClick={() => setPlanId(null)}>
                <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border ${customising ? "border-[#0A66F0] bg-[#0A66F0] text-white" : "border-[#C9D2E0]"}`}>
                  {customising && <Check className="h-3.5 w-3.5" />}
                </span>
                <span className="flex items-center gap-1.5 text-[15px] font-bold"><Sparkles className="h-4 w-4 text-[#0A66F0]" /> Customise</span>
              </button>
              {customising && (
                <div className="mt-3 space-y-3">
                  <div>
                    <p className="mb-1.5 text-xs font-semibold text-[#5F6878]">Bucket Wash</p>
                    <div className="flex flex-wrap gap-2">
                      {data.customise.bucket_day_options.map((o) => (
                        <button key={o.days} type="button" className={chip(custom.bucket_days === o.days)} onClick={() => setCustom({ ...custom, bucket_days: o.days })}>
                          {o.days >= 25 ? "Daily" : `${o.days} Days`}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div>
                    <p className="mb-1.5 text-xs font-semibold text-[#5F6878]">Premium Wash</p>
                    <div className="flex flex-wrap gap-2">
                      {data.customise.premium_services.map((s) => (
                        <button key={s.id} type="button" className={chip(custom.premium_service_id === s.id)} onClick={() => setCustom({ ...custom, premium_service_id: s.id })}>
                          {titleCase(s.name)}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div>
                    <p className="mb-1.5 text-xs font-semibold text-[#5F6878]">Premium Washes A Month</p>
                    <div className="flex flex-wrap gap-2">
                      {data.customise.premium_count_options.map((n) => (
                        <button key={n} type="button" className={chip(custom.premium_count === n)} onClick={() => setCustom({ ...custom, premium_count: n })}>
                          {n}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
          {!planOptions.length && !data.customise.enabled && <p className="text-sm text-[#5F6878]">No plans are open here yet — check back soon.</p>}
        </div>
      </section>

      <section className={card} aria-live="polite">
        <h2 className="mb-2 text-base font-bold">Your Price</h2>
        {quote ? (
          <>
            <p className="mb-2 text-sm text-[#5F6878]">{planWithServices(quote)}</p>
            <ul className="space-y-1.5 text-sm">
              {quote.cars.map((c, i) => (
                <li key={i} className="flex items-center justify-between gap-3">
                  <span className="flex items-center gap-2 text-[#5F6878]"><Car className="h-4 w-4" /> Car {i + 1} · {titleCase(c.vehicle_type_name)}</span>
                  <span className="font-semibold">{rupees(c.price)}</span>
                </li>
              ))}
            </ul>
            {quote.discount > 0 && (
              <>
                <div className="mt-3 flex items-center justify-between border-t border-[#E4E9F1] pt-3 text-sm">
                  <span className="text-[#5F6878]">Plan Total</span>
                  <span className="font-semibold">{rupees(quote.total)}</span>
                </div>
                <div className="mt-1.5 flex items-center justify-between text-sm text-[#0A66F0]" data-testid="society-discount">
                  <span className="flex items-center gap-1.5"><Tag className="h-4 w-4" /> Coupon {quote.coupon?.code}</span>
                  <span className="font-semibold">−{rupees(quote.discount)}</span>
                </div>
              </>
            )}
            <div className="mt-3 flex items-end justify-between border-t border-[#E4E9F1] pt-3">
              <span className="text-sm font-semibold">{quote.discount > 0 ? "First Month" : "Total Per Month"}</span>
              <span className="text-right">
                {quote.mrp_total > quote.payable_total && <span className="mr-2 text-sm text-[#9AA3B2] line-through">{rupees(quote.mrp_total)}</span>}
                <span className="text-xl font-extrabold" data-testid="society-total">{rupees(quote.payable_total)}</span>
              </span>
            </div>
          </>
        ) : (
          <p className="text-sm text-[#5F6878]">{quoteError || "Pick a plan to see your price."}</p>
        )}
        <div className="mt-4">
          <label className={label} htmlFor="soc-coupon">Coupon Code <span className="font-normal text-[#5F6878]">(Optional)</span></label>
          <div className="flex gap-2">
            <input id="soc-coupon" className={input} value={couponInput} placeholder="Have a code?" autoCapitalize="characters"
              onChange={(e) => setCouponInput(e.target.value.toUpperCase().replace(/\s/g, "").slice(0, 30))}
              onKeyDown={(e) => { if (e.key === "Enter" && couponInput.trim()) setCoupon(couponInput.trim()); }} />
            {coupon ? (
              <button type="button" className={`${ghostBtn} h-12 shrink-0`} onClick={() => { setCoupon(""); setCouponInput(""); }}>Remove</button>
            ) : (
              <button type="button" className={`${ghostBtn} h-12 shrink-0`} disabled={!couponInput.trim()} onClick={() => setCoupon(couponInput.trim())}>Apply</button>
            )}
          </div>
          {coupon && quote?.coupon && (
            <p className={`mt-1.5 text-sm ${quote.coupon.valid ? "font-semibold text-[#0A66F0]" : "text-[var(--color-error)]"}`} data-testid="society-coupon-result">
              {quote.coupon.valid ? `${quote.coupon.code} applied — ${rupees(quote.coupon.discount)} off your first month.` : quote.coupon.error}
            </p>
          )}
        </div>
      </section>

      {data.online_payment && !isStaff && (
        <div className="grid grid-cols-2 gap-2">
          <button type="button" className={chip(payNow)} onClick={() => setPayNow(true)}>Pay Now</button>
          <button type="button" className={chip(!payNow)} onClick={() => setPayNow(false)}>Pay Later</button>
        </div>
      )}
      {error && <p className="rounded-[12px] bg-red-50 px-3 py-2 text-sm text-[var(--color-error)]">{error}</p>}
      <button type="button" className={payNow && data.online_payment && !isStaff ? ctaBtn : primaryBtn} disabled={busy || !quote} onClick={start}>
        {busy ? "Saving…" : payNow && data.online_payment && !isStaff ? `Verify & Pay ${quote ? rupees(quote.payable_total) : ""}` : "Verify & Send Request"}
      </button>
      {!data.online_payment && <p className="text-center text-xs text-[#5F6878]">Your society manager confirms the plan and collects payment.</p>}
      {onCancel && (
        <button type="button" className={`${ghostBtn} w-full`} onClick={onCancel}>Back To My Plan</button>
      )}

      <SocietyOtpModal
        open={otpOpen}
        phone={validateIndianMobile(phone) || phone}
        busy={busy}
        error={otpError}
        onClose={() => setOtpOpen(false)}
        onVerified={(proof) => void submit(proof)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Resident hub
// ---------------------------------------------------------------------------

function ResidentHub({ token, hub, flash, onAdd }: { token: string; hub: SocietyHub; flash?: string; onAdd: () => void }) {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["society-hub", token] });
    queryClient.invalidateQueries({ queryKey: ["my-subscriptions"] });
  };

  const [issueOpen, setIssueOpen] = useState(false);
  const [renewFor, setRenewFor] = useState<string | null>(null);
  const issues = useQuery({ queryKey: ["society-issues", "mine", hub.society.id], queryFn: () => societyIssueApi.mine(hub.society.id) });
  const pay = useMutation({
    mutationFn: ({ id, renewal, coupon }: { id: string; renewal: boolean; coupon?: string }) =>
      payWithRazorpay(
        { purpose: "society", society_enrollment_id: id, society_renewal: renewal, society_coupon_code: renewal ? coupon || undefined : undefined },
        { name: user?.full_name, contact: user?.phone },
      ),
    onSuccess: (_r, v) => {
      setMessage(v.renewal ? "Payment received — your plan is renewed." : "Payment received — your plan is active.");
      setRenewFor(null);
      refresh();
    },
    onError: (err) => setError(paymentErrorMessage(err)),
  });
  const withdraw = useMutation({
    mutationFn: (id: string) => societyFormApi.withdraw(token, id),
    onSuccess: refresh,
    onError: (err) => setError(getErrorMessage(err)),
  });

  const liveCars = hub.enrollments
    .filter((e) => e.status === "active")
    .flatMap((e) => e.cars.filter((c) => c.status === "active" && c.subscription?.status === "active").map((c) => ({ ...c, flat: e.flat, plan: e.plan_name, premium_service_name: e.premium_service_name })));

  return (
    <div className="space-y-4">
      {flash && !message && <p className="flex items-start gap-2 rounded-[12px] bg-[#E8F0FE] px-3 py-2.5 text-sm font-semibold text-[#0A66F0]" data-testid="society-flash"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> {flash}</p>}
      {message && <p className="rounded-[12px] bg-[#E8F0FE] px-3 py-2 text-sm font-semibold text-[#0A66F0]">{message}</p>}
      {error && <p className="rounded-[12px] bg-red-50 px-3 py-2 text-sm text-[var(--color-error)]">{error}</p>}

      {hub.enrollments.map((e) => (
        <section key={e.id} className={card} data-testid="society-enrollment">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-[15px] font-bold leading-snug">{planWithServices(e)}</p>
              <p className="mt-0.5 text-[13px] text-[#5F6878]">
                {e.flat} · {rupees(e.total_amount)} a month
                {e.status !== "active" && e.discount_amount > 0 && <span className="text-[#0A66F0]"> · coupon {e.coupon_code} −{rupees(e.discount_amount)}</span>}
              </p>
            </div>
            <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-bold ${e.status === "active" ? "bg-[#E8F0FE] text-[#0A66F0]" : "bg-[#FFF8DB] text-[#7A5B00]"}`}>{statusText[e.status]}</span>
          </div>
          <ul className="mt-3 divide-y divide-[#EEF2F7]">
            {e.cars.map((c) => (
              <li key={c.vehicle_id} className="flex flex-wrap items-center justify-between gap-2 py-2.5">
                <span className="min-w-0">
                  <span className="block font-mono text-[14px] font-bold">{c.registration_number}</span>
                  <span className="block text-xs text-[#5F6878]">{titleCase(c.vehicle_type_name)}{c.status === "cancelled" ? " · Cancelled" : ""}</span>
                </span>
                {c.subscription && c.status === "active" ? (
                  <span className="text-right text-xs text-[#5F6878]">
                    <span className="block" title="Premium washes left this cycle"><b className="text-[#0E1A33]">{premiumUsage(e.premium_service_name, c.subscription.remaining, c.subscription.total)}</b> Left</span>
                    {c.bucket_used != null && <span className="block" title="Daily washes done this cycle">{bucketUsage(e.bucket_short_label, c.bucket_used, c.bucket_allowance)} This Cycle</span>}
                    {c.subscription.end_date && <span className="block">Till {formatShortDate(c.subscription.end_date.slice(0, 10))}</span>}
                  </span>
                ) : (
                  <span className="text-sm font-semibold">{rupees(c.price)}</span>
                )}
              </li>
            ))}
          </ul>
          <div className="mt-3 flex flex-wrap gap-2">
            {(e.status === "requested" || e.status === "awaiting_payment") && hub.online_payment && (
              <button className={`${ctaBtn} sm:w-auto`} disabled={pay.isPending} onClick={() => { setError(""); pay.mutate({ id: e.id, renewal: false }); }}>
                Pay {rupees(e.payable_amount)}
              </button>
            )}
            {e.renew_open && hub.online_payment && renewFor !== e.id && (
              <button className={`${ctaBtn} sm:w-auto`} disabled={pay.isPending} onClick={() => { setError(""); setRenewFor(e.id); }}>
                Renew {rupees(e.renew_amount)}
              </button>
            )}
            {(e.status === "requested" || e.status === "awaiting_payment") && (
              <button className={ghostBtn} disabled={withdraw.isPending} onClick={() => withdraw.mutate(e.id)}>Withdraw Request</button>
            )}
          </div>
          {e.status === "requested" && !hub.online_payment && <p className="mt-2 text-xs text-[#5F6878]">Your manager will confirm and collect payment.</p>}
          {renewFor === e.id && (
            <RenewBox token={token} enrollment={e} busy={pay.isPending} onCancel={() => setRenewFor(null)}
              onPay={(coupon) => { setError(""); pay.mutate({ id: e.id, renewal: true, coupon }); }} />
          )}
        </section>
      ))}

      {liveCars.length > 0 && <ResidentScheduleCard societyId={hub.society.id} />}
      {liveCars.length > 0 && <BookPremium token={token} hub={hub} cars={liveCars} />}

      {hub.upcoming.length > 0 && (
        <section className={card}>
          <h2 className="mb-2 text-base font-bold">Upcoming Premium Washes</h2>
          <ul className="space-y-2 text-sm">
            {hub.upcoming.map((b) => (
              <li key={b.id} className="flex items-center justify-between gap-3 rounded-[12px] bg-[#F7F9FC] px-3 py-2.5">
                <span>
                  <span className="block font-semibold">{formatDay(b.scheduled_date)} · {b.slot_label}</span>
                  <span className="block text-xs text-[#5F6878]">{[titleCase(b.service_name), b.registration_number ? carLabel(b.registration_number, b.vehicle_type_name) : "", b.booking_number].filter(Boolean).join(" · ")}</span>
                </span>
                <CalendarDays className="h-4 w-4 text-[#0A66F0]" />
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className={card} data-testid="society-issues-card">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-bold">Something Not Right?</h2>
            <p className="mt-0.5 text-sm text-[#5F6878]">Missed wash, captain didn't come, billing — tell your society manager.</p>
          </div>
          <AlertTriangle className="h-5 w-5 shrink-0 text-[#0A66F0]" />
        </div>
        <button type="button" className={`${ghostBtn} mt-3 w-full`} onClick={() => setIssueOpen(true)}>Report An Issue</button>
        {!!issues.data?.length && (
          <ul className="mt-3 space-y-2 text-sm">
            {issues.data.slice(0, 5).map((i) => (
              <li key={i.id} className="flex items-center justify-between gap-3 rounded-[12px] bg-[#F7F9FC] px-3 py-2.5">
                <span className="min-w-0">
                  <span className="block font-semibold">{titleCase(i.issue_label)}</span>
                  <span className="block truncate text-xs text-[#5F6878]">{[i.registration_number, titleCase(i.vehicle_type_name), formatShortDate(i.created_at.slice(0, 10))].filter(Boolean).join(" · ")}{i.replies?.length ? ` · ${i.replies.length} repl${i.replies.length > 1 ? "ies" : "y"}` : ""}</span>
                </span>
                <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-bold ${i.status === "open" || i.status === "in_progress" ? "bg-[#FFF8DB] text-[#7A5B00]" : "bg-[#E8F0FE] text-[#0A66F0]"}`}>
                  {i.status === "open" ? "Open" : i.status === "in_progress" ? "In Progress" : i.status === "resolved" ? "Resolved" : "Closed"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <button className={`${ghostBtn} w-full`} onClick={onAdd}><Plus className="h-4 w-4" /> Add Another Car</button>

      <SocietyIssueModal
        open={issueOpen}
        onClose={() => setIssueOpen(false)}
        societies={[{
          id: hub.society.id,
          name: hub.society.name,
          cars: hub.enrollments.flatMap((e) => e.cars.filter((c) => c.status === "active" || c.status === "pending").map((c) => ({ vehicle_id: c.vehicle_id, registration_number: c.registration_number, vehicle_type_name: c.vehicle_type_name }))),
        }]}
        onRaised={() => { void issues.refetch(); queryClient.invalidateQueries({ queryKey: ["my-complaints"] }); }}
      />
    </div>
  );
}

/** Renew online with an optional coupon — the price comes from the server. */
function RenewBox({ token, enrollment, busy, onCancel, onPay }: {
  token: string; enrollment: SocietyEnrollment; busy: boolean; onCancel: () => void; onPay: (coupon?: string) => void;
}) {
  const [couponInput, setCouponInput] = useState("");
  const [applied, setApplied] = useState("");
  const [preview, setPreview] = useState<CouponPreview | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    societyFormApi.couponPreview(token, enrollment.id, { coupon_code: applied || undefined, renewal: true })
      .then((p) => live && (setPreview(p), setError("")))
      .catch((err) => live && setError(getErrorMessage(err)));
    return () => { live = false; };
  }, [token, enrollment.id, applied]);
  const good = preview?.coupon?.valid ? preview.coupon.code : "";
  return (
    <div className="mt-3 rounded-[14px] border border-[#E4E9F1] bg-[#F7F9FC] p-3" data-testid="society-renew-box">
      <p className="text-sm font-semibold">Renew For The Next Month</p>
      <div className="mt-2 flex gap-2">
        <input className={input} value={couponInput} placeholder="Coupon Code (Optional)" aria-label="Renewal coupon code"
          onChange={(e) => setCouponInput(e.target.value.toUpperCase().replace(/\s/g, "").slice(0, 30))} />
        {applied ? (
          <button type="button" className={`${ghostBtn} h-12 shrink-0`} onClick={() => { setApplied(""); setCouponInput(""); }}>Remove</button>
        ) : (
          <button type="button" className={`${ghostBtn} h-12 shrink-0`} disabled={!couponInput.trim()} onClick={() => setApplied(couponInput.trim())}>Apply</button>
        )}
      </div>
      {applied && preview?.coupon && (
        <p className={`mt-1.5 text-sm ${preview.coupon.valid ? "font-semibold text-[#0A66F0]" : "text-[var(--color-error)]"}`}>
          {preview.coupon.valid ? `${preview.coupon.code} applied — ${rupees(preview.coupon.discount)} off` : preview.coupon.error}
        </p>
      )}
      {preview && (
        <div className="mt-2 space-y-1 text-sm">
          <div className="flex justify-between"><span className="text-[#5F6878]">Renewal</span><span>{rupees(preview.subtotal)}</span></div>
          {preview.discount > 0 && <div className="flex justify-between text-[#0A66F0]"><span>Coupon {good}</span><span>−{rupees(preview.discount)}</span></div>}
          <div className="flex justify-between border-t border-[#E4E9F1] pt-1 font-bold"><span>To Pay</span><span>{rupees(preview.payable)}</span></div>
        </div>
      )}
      {error && <p className="mt-2 text-sm text-[var(--color-error)]">{error}</p>}
      <div className="mt-3 flex gap-2">
        <button type="button" className={`${ctaBtn} flex-1`} disabled={busy || !preview || (!!applied && !good)} onClick={() => onPay(good || undefined)}>
          {busy ? "Opening Payment…" : `Pay ${preview ? rupees(preview.payable) : ""}`}
        </button>
        <button type="button" className={ghostBtn} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function BookPremium({ token, hub, cars }: { token: string; hub: SocietyHub; cars: (SocietyEnrollment["cars"][number] & { flat: string; plan: string; premium_service_name: string })[] }) {
  const queryClient = useQueryClient();
  const bookable = cars.filter((c) => (c.subscription?.remaining ?? 0) > 0);
  const [picked, setPicked] = useState<string[]>(bookable.slice(0, 1).map((c) => c.subscription!.id));
  const days = useMemo(() => Array.from({ length: 6 }, (_, i) => dayIST(i + hub.lead_days)), [hub.lead_days]);
  const [day, setDay] = useState(days[0]);
  const [slot, setSlot] = useState("");
  const [done, setDone] = useState("");
  const [error, setError] = useState("");
  const slots = useQuery({
    queryKey: ["society-slots", hub.service_center_id, day],
    queryFn: () => serviceCenterApi.availableSlots(hub.service_center_id, day),
    enabled: !!hub.service_center_id && bookable.length > 0,
  });
  const book = useMutation({
    mutationFn: () => societyFormApi.bookPremium({ subscription_ids: picked, scheduled_date: day, scheduled_slot: slot }),
    onSuccess: (r) => {
      setDone(`Booked for ${formatDay(r.scheduled_date)}, ${r.slot_label}.`);
      setError("");
      setSlot("");
      queryClient.invalidateQueries({ queryKey: ["society-hub", token] });
      queryClient.invalidateQueries({ queryKey: ["my-subscriptions"] });
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  if (!bookable.length) {
    return (
      <section className={card}>
        <h2 className="text-base font-bold">Book A Premium Wash</h2>
        <p className="mt-1 text-sm text-[#5F6878]">All premium washes are used this cycle. They refill when you renew.</p>
      </section>
    );
  }
  return (
    <section className={card} data-testid="book-premium">
      <h2 className="text-base font-bold">Book A Premium Wash</h2>
      <p className="mb-3 mt-0.5 text-sm text-[#5F6878]">Book at least a day ahead. Covered by your plan.</p>
      <div className="mb-3 flex flex-wrap gap-2">
        {bookable.map((c) => {
          const id = c.subscription!.id;
          const on = picked.includes(id);
          return (
            <button key={id} type="button" className={chip(on)} onClick={() => setPicked((p) => (on ? p.filter((x) => x !== id) : [...p, id]))}>
              <span className="font-mono">{c.registration_number}</span>{c.vehicle_type_name ? ` · ${titleCase(c.vehicle_type_name)}` : ""}
              <span className="block text-xs font-normal">{premiumUsage(c.premium_service_name, c.subscription!.remaining, c.subscription!.total)} Left</span>
            </button>
          );
        })}
      </div>
      <p className="mb-1.5 text-xs font-semibold text-[#5F6878]">Day</p>
      <div className="-mx-1 mb-3 flex gap-2 overflow-x-auto px-1 pb-1">
        {days.map((d) => (
          <button key={d} type="button" className={`${chip(day === d)} shrink-0`} onClick={() => { setDay(d); setSlot(""); }}>
            {formatDay(d)}
          </button>
        ))}
      </div>
      <p className="mb-1.5 text-xs font-semibold text-[#5F6878]">Time</p>
      {slots.isLoading ? (
        <Spinner />
      ) : (
        <div className="mb-3 grid grid-cols-2 gap-2">
          {(slots.data || []).map((s) => (
            <button key={s.key} type="button" disabled={s.status === "full"} className={`${chip(slot === s.key)} disabled:opacity-40`} onClick={() => setSlot(s.key)}>
              {formatSlot(s.key)}
            </button>
          ))}
          {!slots.data?.length && <p className="col-span-2 text-sm text-[#5F6878]">No times open that day.</p>}
        </div>
      )}
      {error && <p className="mb-2 text-sm text-[var(--color-error)]">{error}</p>}
      {done && <p className="mb-2 rounded-[12px] bg-[#E8F0FE] px-3 py-2 text-sm font-semibold text-[#0A66F0]">{done}</p>}
      <button className={primaryBtn} disabled={!picked.length || !slot || book.isPending} onClick={() => { setDone(""); book.mutate(); }}>
        {book.isPending ? "Booking…" : `Book ${picked.length > 1 ? `${picked.length} Cars` : "Premium Wash"}`}
      </button>
    </section>
  );
}

