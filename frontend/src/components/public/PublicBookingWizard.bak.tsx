import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight, BadgeCheck, Car, CheckCircle2, Lock, MapPin, ShieldCheck, Sparkles } from "lucide-react";
import { catalogApi, serviceCenterApi, vehicleTypeApi } from "../../api/catalog";
import { authApi } from "../../api/auth";
import { bookingApi } from "../../api/booking";
import { vehicleApi, addressApi } from "../../api/profile";
import { Badge, Button, Input, Spinner } from "../ui";
import { SlotPicker } from "../shared/SlotPicker";
import { PhoneVerificationModal } from "../shared/PhoneVerificationModal";
import { CoverageLeadInline } from "./CoverageLeadInline";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";
import type { Service } from "../../types";

export interface WizardPreselect {
  vehicleTypeId?: string;
  serviceId?: string;
}

const STEPS = ["Choose service", "Time & place", "Confirm & verify"];
const STEP_DETAILS = [
  { eyebrow: "Step 01", title: "Choose your vehicle and service", description: "Select what you drive, then choose the care it needs." },
  { eyebrow: "Step 02", title: "Choose a convenient visit", description: "Confirm the location and pick an available time." },
  { eyebrow: "Step 03", title: "Review and confirm", description: "Add the final details to complete your booking." },
];

function priceFor(s: Service, vt: string): number {
  return s.vehicle_type_prices?.[vt] ?? s.price;
}

function serviceImageFor(name: string): string {
  const normalizedName = name.toLowerCase();
  if (normalizedName.includes("foam")) return "/service-2.png";
  if (normalizedName.includes("interior")) return "/service-3.png";
  if (normalizedName.includes("wax")) return "/service-4.png";
  if (normalizedName.includes("dashboard")) return "/service-5.png";
  if (normalizedName.includes("detail")) return "/service-6.png";
  return "/service-1.png";
}

function randomPassword(): string {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789";
  return Array.from({ length: 16 }, () => chars[Math.floor(Math.random() * chars.length)]).join("") + "9a";
}

/**
 * The landing page's 3-step booking flow — bookable by ANYONE, no login
 * wall (the single biggest conversion killer on booking pages). Behind the
 * scenes it composes only pre-existing product flows, in order:
 *   guest  → silent account creation (random password; claimed later via
 *            forgot-password → WhatsApp OTP) → vehicle + address created →
 *            one-time WhatsApp OTP verification (the same gate the app
 *            enforces) → booking.
 *   known number → inline password login, then same as logged-in.
 *   logged-in customer → everything prefilled (saved vehicles/addresses,
 *   no name/phone asked), OTP only if they were never verified — the
 *   "short form" version of the exact same three steps.
 * Out-of-coverage pincodes flip step 2 into the CoverageLeadInline capture
 * instead of a dead end.
 */
export function PublicBookingWizard({ preselect }: { preselect: WizardPreselect | null }) {
  const navigate = useNavigate();
  const { user, login, register } = useAuth();
  const isCustomer = !!user && user.role === "customer";
  const isStaff = !!user && user.role !== "customer";

  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const { data: servicesData } = useQuery({ queryKey: ["public-services"], queryFn: () => catalogApi.services({ page_size: 100 }) });
  const services = useMemo(() => servicesData?.data || [], [servicesData]);
  const { data: myVehicles } = useQuery({ queryKey: ["vehicles"], queryFn: vehicleApi.list, enabled: isCustomer });
  const { data: myAddresses } = useQuery({ queryKey: ["addresses"], queryFn: addressApi.list, enabled: isCustomer });

  const [step, setStep] = useState(0);
  const [vehicleTypeId, setVehicleTypeId] = useState("");
  const [serviceIds, setServiceIds] = useState<string[]>([]);

  // Vehicle: a saved one (logged-in) or details for a new one.
  const [savedVehicleId, setSavedVehicleId] = useState<string | null>(null);
  const [brandModel, setBrandModel] = useState("");
  const [regNumber, setRegNumber] = useState("");

  // Place & time.
  const [pincode, setPincode] = useState("");
  const [checkedPincode, setCheckedPincode] = useState("");
  const [coverage, setCoverage] = useState<"idle" | "checking" | "covered" | "uncovered">("idle");
  const [centerId, setCenterId] = useState("");
  const [centerCity, setCenterCity] = useState("");
  const [centerState, setCenterState] = useState("");
  const [savedAddressId, setSavedAddressId] = useState<string | null>(null);
  const [line1, setLine1] = useState("");
  const [date, setDate] = useState("");
  const [slot, setSlot] = useState("");

  // Guest identity.
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [needLogin, setNeedLogin] = useState(false);
  const [password, setPassword] = useState("");

  const [verifyOpen, setVerifyOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const createdRef = useRef<{ vehicleId?: string; addressId?: string }>({});

  // Landing-section "Book this" buttons drive the wizard from outside.
  useEffect(() => {
    if (!preselect) return;
    if (preselect.vehicleTypeId) setVehicleTypeId(preselect.vehicleTypeId);
    if (preselect.serviceId) setServiceIds([preselect.serviceId]);
    setStep(0);
  }, [preselect]);

  // Logged-in customers start prefilled from their default vehicle/address.
  useEffect(() => {
    if (!isCustomer || !myVehicles?.length || savedVehicleId) return;
    const def = myVehicles.find((v) => v.is_default) || myVehicles[0];
    setSavedVehicleId(def.id);
    setVehicleTypeId(def.vehicle_type);
  }, [isCustomer, myVehicles, savedVehicleId]);
  useEffect(() => {
    if (!isCustomer || !myAddresses?.length || savedAddressId) return;
    const def = myAddresses.find((a) => a.is_default) || myAddresses[0];
    setSavedAddressId(def.id);
    setPincode(def.pincode);
  }, [isCustomer, myAddresses, savedAddressId]);

  const eligibleServices = useMemo(
    () => services.filter((s) => !vehicleTypeId || !s.vehicle_types?.length || s.vehicle_types.includes(vehicleTypeId)),
    [services, vehicleTypeId]
  );
  const selectedServices = services.filter((s) => serviceIds.includes(s.id));
  const total = selectedServices.reduce((sum, s) => sum + priceFor(s, vehicleTypeId), 0);
  const totalDuration = selectedServices.reduce((sum, s) => sum + (s.duration_minutes || 30), 0);
  const selectedVehicle = myVehicles?.find((vehicle) => vehicle.id === savedVehicleId);
  const selectedVehicleLabel = savedVehicleId
    ? selectedVehicle ? `${selectedVehicle.brand} ${selectedVehicle.model}` : undefined
    : vehicleTypes?.find((vehicleType) => vehicleType.id === vehicleTypeId)?.name;

  const checkCoverage = async (pin: string) => {
    setCoverage("checking");
    setCheckedPincode(pin);
    setCenterId("");
    setSlot("");
    try {
      const centers = await serviceCenterApi.lookupByPincode(pin);
      if (centers.length) {
        setCenterId(centers[0].id);
        setCenterCity(centers[0].location.city);
        setCenterState(centers[0].location.state);
        setCoverage("covered");
      } else {
        setCoverage("uncovered");
      }
    } catch {
      setCoverage("uncovered");
    }
  };

  // Auto-check when a saved address supplies the pincode.
  useEffect(() => {
    if (savedAddressId && pincode.length >= 6 && checkedPincode !== pincode) void checkCoverage(pincode);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedAddressId, pincode]);

  const step1Valid = !!vehicleTypeId && serviceIds.length > 0;
  const step2Valid = coverage === "covered" && !!date && !!slot && (savedAddressId ? true : line1.trim().length >= 3);
  const vehicleValid = savedVehicleId ? true : brandModel.trim().length >= 2 && regNumber.trim().length >= 3;
  const identityValid = user ? true : name.trim().length >= 2 && /^\d{10}$/.test(phone.trim());
  const step3Valid = vehicleValid && identityValid && (!needLogin || password.length >= 8);

  const sidebarSummary = (
    <aside className="hidden h-fit rounded-2xl border border-[#e8dfcd] bg-[#fffcf6] p-5 shadow-[0_12px_32px_-25px_rgba(77,58,19,.5)] lg:sticky lg:top-24 lg:block">
      <p className="flex items-center gap-2 text-base font-semibold text-[var(--color-text-primary)]"><Sparkles className="h-5 w-5 text-[#a17800]" /> Your booking</p>
      <div className="mt-4 space-y-3 border-t border-[#e7dfd1] pt-4 text-sm">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">Vehicle</p>
          <p className="mt-1 font-medium text-[var(--color-text-primary)]">{selectedVehicleLabel || "Not selected yet"}</p>
        </div>
        <div className="border-t border-[#eee7da] pt-3">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">Services</p>
          {selectedServices.length ? selectedServices.map((service) => (
            <div key={service.id} className="mt-2 flex items-start justify-between gap-3">
              <span className="text-[var(--color-text-primary)]">{service.name}</span>
              <span className="font-mono-num font-medium text-[var(--color-text-primary)]">₹{priceFor(service, vehicleTypeId)}</span>
            </div>
          )) : <p className="mt-1 text-xs text-[var(--color-text-secondary)]">Choose a service to see your total.</p>}
        </div>
        <div className="flex items-center justify-between border-t border-[#dfd5bf] pt-3 text-base">
          <span className="text-sm font-medium text-[var(--color-text-secondary)]">Subtotal</span>
          <span className="font-mono-num font-semibold text-[var(--color-text-primary)]">₹{total}</span>
        </div>
        <div className="flex items-center justify-between border-t border-[#dfd5bf] pt-3 text-base">
          <span className="font-semibold text-[var(--color-text-primary)]">Total</span>
          <span className="font-mono-num font-bold text-[#8a6500]">₹{total}</span>
        </div>
      </div>
      <Button
        variant="secondary"
        size="md"
        className="mt-5 w-full"
        disabled={step === 0 ? !step1Valid : !step2Valid}
        onClick={() => setStep((currentStep) => Math.min(currentStep + 1, 2))}
      >
        {step === 0 ? "Continue to schedule" : "Continue to confirm"} <ArrowRight className="h-4 w-4" />
      </Button>
      <div className="mt-5 space-y-3 border-t border-[#e7dfd1] pt-4 text-xs text-[var(--color-text-secondary)]">
        <p className="flex gap-2"><BadgeCheck className="h-4 w-4 shrink-0 text-[#a17800]" /><span><b className="block text-[var(--color-text-primary)]">Professional experts</b>Trained and verified professionals.</span></p>
        <p className="flex gap-2"><Sparkles className="h-4 w-4 shrink-0 text-[#a17800]" /><span><b className="block text-[var(--color-text-primary)]">Eco-friendly products</b>Safe for your car and environment.</span></p>
        <p className="flex gap-2"><CheckCircle2 className="h-4 w-4 shrink-0 text-[#a17800]" /><span><b className="block text-[var(--color-text-primary)]">100% satisfaction</b>Quality service, every time.</span></p>
        <p className="flex gap-2"><Lock className="h-4 w-4 shrink-0 text-[#a17800]" /><span><b className="block text-[var(--color-text-primary)]">Secure booking</b>Your information is kept safe with us.</span></p>
      </div>
    </aside>
  );

  /** The whole pipeline, resumable and idempotent: every stage checks
   * whether its work already happened (crucial when the OTP modal pauses
   * us mid-way, or a retry follows a transient failure). */
  const submit = async (resumedAfterVerify = false) => {
    setSubmitting(true);
    setError("");
    try {
      // 1. An account to book under.
      let currentUser = user;
      if (!currentUser) {
        if (needLogin) {
          currentUser = await login(phone.trim(), password);
        } else {
          try {
            // Through AuthContext so the whole app (including the OTP
            // modal, which reads the logged-in user's phone) sees the new
            // session immediately. Random password — the customer claims
            // full access later via forgot-password → WhatsApp OTP.
            currentUser = await register({ full_name: name.trim(), phone: phone.trim(), password: randomPassword() });
          } catch (err) {
            if (getErrorMessage(err).toLowerCase().includes("already exists")) {
              setNeedLogin(true);
              setError("This number already has an account — enter your password to continue (or use Forgot password on the login page).");
              return;
            }
            throw err;
          }
        }
      }
      if (currentUser.role !== "customer") {
        setError("You're signed in as staff — bookings are for customer accounts.");
        return;
      }

      // 2. The vehicle.
      let vehicleId = savedVehicleId || createdRef.current.vehicleId;
      if (!vehicleId) {
        const existing = await vehicleApi.list();
        const match = existing.find((v) => v.registration_number.toUpperCase() === regNumber.trim().toUpperCase());
        if (match) {
          vehicleId = match.id;
        } else {
          const [brand, ...rest] = brandModel.trim().split(/\s+/);
          const created = await vehicleApi.create({
            vehicle_type: vehicleTypeId,
            brand: brand || "Vehicle",
            model: rest.join(" ") || brand || "—",
            registration_number: regNumber.trim().toUpperCase(),
            is_default: existing.length === 0,
          });
          vehicleId = created.id;
        }
        createdRef.current.vehicleId = vehicleId;
      }

      // 3. The address.
      let addressId = savedAddressId || createdRef.current.addressId;
      if (!addressId) {
        const existing = await addressApi.list();
        const match = existing.find((a) => a.line1 === line1.trim() && a.pincode === checkedPincode);
        if (match) {
          addressId = match.id;
        } else {
          const created = await addressApi.create({
            label: "Home",
            line1: line1.trim(),
            city: centerCity || "—",
            state: centerState || "—",
            pincode: checkedPincode,
            is_default: existing.length === 0,
          });
          addressId = created.id;
        }
        createdRef.current.addressId = addressId;
      }

      // 4. One-time WhatsApp OTP verification (the app's standard gate) —
      // skipped forever once done, and skipped here when the modal just
      // completed it (resumedAfterVerify).
      if (!resumedAfterVerify) {
        const me = await authApi.me();
        if (!me.phone_verified) {
          setVerifyOpen(true);
          return; // resumes via onVerified → submit(true)
        }
      }

      // 5. The booking itself — the same API, capacity checks, and
      // WhatsApp confirmation as every other booking in the system.
      const booking = await bookingApi.create({
        vehicle_id: vehicleId,
        address_id: addressId,
        service_ids: serviceIds,
        scheduled_date: date,
        scheduled_slot: slot,
      });
      navigate(`/thank-you?token=${booking.confirmation_token}`);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  if (isStaff) {
    return (
      <div className="rounded-3xl border border-gray-100 bg-white p-8 text-center shadow-[var(--shadow-soft)]">
        <ShieldCheck className="mx-auto h-10 w-10 text-[var(--color-primary)]" />
        <p className="mt-3 font-semibold text-[var(--color-text-primary)]">You're signed in as {user?.role}</p>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Customer bookings are made from a customer account — head to your dashboard instead.</p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-[28px] border border-[#e8dfcd] bg-white shadow-[0_18px_55px_-28px_rgba(77,58,19,0.30)]">
      {/* Step header */}
      <div className="border-b border-[#eee7da] bg-[#fffcf6] px-4 py-4 sm:px-6 md:px-8 md:py-5">
        <div className="hidden items-center sm:flex">
          {STEPS.map((label, i) => (
            <div key={label} className="flex min-w-0 flex-1 items-center last:flex-none">
              <button
                type="button"
                onClick={() => i < step && setStep(i)}
                className={`flex items-center gap-2 rounded-full px-2 py-1.5 text-sm font-semibold transition-colors ${
                  i === step
                    ? "text-black"
                    : i < step
                      ? "text-[#76623d] hover:text-black"
                      : "cursor-default text-neutral-400"
                }`}
              >
                <span className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${i === step ? "bg-[#e8a900] text-black" : i < step ? "bg-[#202020] text-white" : "bg-[#eae7e0] text-neutral-500"}`}>
                  {i < step ? <CheckCircle2 className="h-4 w-4" /> : `0${i + 1}`}
                </span>
                <span>{label}</span>
              </button>
              {i < STEPS.length - 1 && <span className={`mx-3 h-px min-w-5 flex-1 ${i < step ? "bg-[#d2b35b]" : "bg-[#e5dfd3]"}`} />}
            </div>
          ))}
          <span className="ml-5 hidden items-center gap-1.5 whitespace-nowrap text-xs text-[var(--color-text-secondary)] lg:flex">
            <Lock className="h-3.5 w-3.5" /> No account needed to start
          </span>
        </div>
        <div className="flex items-center justify-between sm:hidden">
          <span className="text-xs font-bold uppercase tracking-[0.12em] text-[#8a6500]">Step {step + 1} of {STEPS.length}</span>
          <span className="text-sm font-semibold text-black">{STEPS[step]}</span>
          <span className="h-1.5 w-16 overflow-hidden rounded-full bg-[#eae7e0]"><span className="block h-full rounded-full bg-[#e8a900] transition-all" style={{ width: `${((step + 1) / STEPS.length) * 100}%` }} /></span>
        </div>
      </div>

      <div className="p-4 sm:p-6 md:p-7">
        <div className="mb-5 max-w-2xl">
          <p className="text-xs font-bold uppercase tracking-[0.16em] text-[#a17800]">{STEP_DETAILS[step].eyebrow}</p>
          <h2 className="mt-2 font-display text-2xl font-bold tracking-tight text-[#151515] sm:text-3xl">{STEP_DETAILS[step].title}</h2>
          <p className="mt-2 text-sm leading-6 text-[var(--color-text-secondary)] sm:text-base">{STEP_DETAILS[step].description}</p>
        </div>
        {/* ---- STEP 1 — Choose service ---- */}
        {step === 0 && (
          <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(300px,1fr)] lg:items-start">
          <div className="space-y-5">
            <section className="rounded-2xl border border-[#ebe5da] bg-white p-4 shadow-[0_12px_30px_-28px_rgba(77,58,19,.45)] sm:p-5">
              <p className="mb-2.5 text-sm font-semibold text-[var(--color-text-primary)]">
                {isCustomer && myVehicles?.length ? "Which vehicle?" : "What do you drive?"}
              </p>
              {isCustomer && myVehicles?.length ? (
                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-3">
                  {myVehicles.map((v) => (
                    <button
                      key={v.id}
                      type="button"
                      onClick={() => {
                        setSavedVehicleId(v.id);
                        setVehicleTypeId(v.vehicle_type);
                      }}
                      className={`flex min-h-[72px] items-center gap-2.5 rounded-xl border-2 px-3.5 py-2.5 text-left text-sm font-medium transition-all ${
                        savedVehicleId === v.id ? "border-[#e8a900] bg-[#fff9e7] shadow-[0_8px_20px_-16px_rgba(148,105,0,.9)]" : "border-[#e6e3dd] hover:border-[#cfc7b8] hover:bg-[#fffcf6]"
                      }`}
                    >
                      <Car className="h-4 w-4" /> {v.brand} {v.model}
                      <span className="font-mono-num text-xs text-[var(--color-text-secondary)]">{v.registration_number}</span>
                    </button>
                  ))}
                  <button
                    type="button"
                    onClick={() => {
                      setSavedVehicleId(null);
                      setVehicleTypeId("");
                    }}
                    className={`min-h-[72px] rounded-xl border-2 px-3.5 py-2.5 text-sm font-semibold ${savedVehicleId === null ? "border-[#e8a900] bg-[#fff9e7]" : "border-dashed border-[#cfc7b8] text-[var(--color-text-secondary)] hover:bg-[#fffcf6]"}`}
                  >
                    + Different vehicle
                  </button>
                </div>
              ) : null}
              {(!isCustomer || !myVehicles?.length || savedVehicleId === null) && (
                <div className="mt-2.5 grid grid-cols-2 gap-2.5 sm:grid-cols-3 xl:grid-cols-6">
                  {(vehicleTypes || []).map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => setVehicleTypeId(t.id)}
                      className={`relative flex min-h-[92px] flex-col items-center justify-center gap-1 rounded-xl border-2 px-3 py-2 text-center text-sm font-semibold transition-all ${
                        vehicleTypeId === t.id ? "border-[#e8a900] bg-[#fff9e7] text-black" : "border-[#e6e3dd] text-[var(--color-text-secondary)] hover:border-[#cfc7b8] hover:bg-[#fffcf6]"
                      }`}
                    >
                      <span className="flex h-9 w-11 items-center justify-center rounded-lg bg-[#fff8e6] text-[#a17800]"><Car className="h-6 w-6" /></span>
                      <span>{t.name}</span>
                      {vehicleTypeId === t.id && <CheckCircle2 className="absolute right-2 top-2 h-4 w-4 rounded-full bg-[#e8a900] text-black" />}
                    </button>
                  ))}
                </div>
              )}
            </section>

            {vehicleTypeId && (
              <section className="rounded-2xl border border-[#ebe5da] bg-white p-4 shadow-[0_12px_30px_-28px_rgba(77,58,19,.45)] sm:p-5">
                <div className="mb-3 flex items-end justify-between gap-4"><div><p className="text-sm font-semibold text-[var(--color-text-primary)]">Choose your service</p><p className="mt-1 text-xs text-[var(--color-text-secondary)]">You can select more than one service.</p></div><span className="hidden text-xs font-medium text-[#8a6500] sm:block">{serviceIds.length} selected</span></div>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {eligibleServices.map((s) => {
                    const active = serviceIds.includes(s.id);
                    return (
                      <button
                        key={s.id}
                        type="button"
                        onClick={() => setServiceIds((prev) => (active ? prev.filter((id) => id !== s.id) : [...prev, s.id]))}
                        className={`relative flex min-h-[104px] items-center justify-between gap-3 rounded-xl border-2 px-3.5 py-3 text-left transition-all ${
                          active ? "border-[#e8a900] bg-[#fff9e7] shadow-[0_8px_24px_-18px_rgba(148,105,0,.8)]" : "border-[#e6e3dd] hover:border-[#cfc7b8] hover:bg-[#fffcf6]"
                        }`}
                      >
                        <img src={serviceImageFor(s.name)} alt="" className="h-14 w-14 shrink-0 rounded-xl object-cover" />
                        <span>
                          <span className="block font-semibold text-[var(--color-text-primary)]">{s.name}</span>
                          <span className="mt-0.5 block text-xs text-[var(--color-text-secondary)]">{s.duration_minutes} min · at your doorstep</span>
                        </span>
                        <span className="ml-auto shrink-0 self-end text-right">
                          <span className="block font-mono-num text-lg font-bold text-[var(--color-text-primary)]">₹{priceFor(s, vehicleTypeId)}</span>
                          {active && <CheckCircle2 className="ml-auto mt-1 h-4 w-4 text-[#a17800]" />}
                        </span>
                      </button>
                    );
                  })}
                </div>
                <p className="mt-3 flex items-center gap-2 rounded-lg bg-[#fff8e8] px-3 py-2 text-xs text-[#765b18]"><BadgeCheck className="h-3.5 w-3.5 shrink-0 text-[#a17800]" /> You can select more than one service.</p>
              </section>
            )}
          </div>
          {sidebarSummary}
          </div>
        )}

        {/* ---- STEP 2 — Time & place ---- */}
        {step === 1 && (
          <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(300px,1fr)] lg:items-start">
          <div className="space-y-5">
            {isCustomer && myAddresses?.length ? (
              <div>
                <p className="mb-2.5 text-sm font-semibold text-[var(--color-text-primary)]">Where should we come?</p>
                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                  {myAddresses.map((a) => (
                    <button
                      key={a.id}
                      type="button"
                      onClick={() => {
                        setSavedAddressId(a.id);
                        setPincode(a.pincode);
                      }}
                      className={`min-h-[72px] rounded-xl border-2 px-3.5 py-2.5 text-left text-sm transition-all ${
                        savedAddressId === a.id ? "border-[#e8a900] bg-[#fff9e7] shadow-[0_8px_20px_-16px_rgba(148,105,0,.9)]" : "border-[#e6e3dd] hover:border-[#cfc7b8] hover:bg-[#fffcf6]"
                      }`}
                    >
                      <span className="flex items-center gap-2 font-semibold"><MapPin className="h-4 w-4 text-[#a17800]" /> {a.label}{savedAddressId === a.id && <CheckCircle2 className="ml-auto h-4 w-4 text-[#a17800]" />}</span>
                      <span className="block text-xs text-[var(--color-text-secondary)]">{a.line1}</span>
                    </button>
                  ))}
                  <button
                    type="button"
                    onClick={() => {
                      setSavedAddressId(null);
                      setPincode("");
                      setCoverage("idle");
                      setCheckedPincode("");
                    }}
                    className={`min-h-[72px] rounded-xl border-2 px-3.5 py-2.5 text-sm font-semibold ${savedAddressId === null ? "border-[#e8a900] bg-[#fff9e7]" : "border-dashed border-[#cfc7b8] text-[var(--color-text-secondary)] hover:bg-[#fffcf6]"}`}
                  >
                    + New address
                  </button>
                </div>
              </div>
            ) : null}

            {!savedAddressId && (
              <div className="rounded-2xl border border-[#ebe5da] bg-[#fffcf7] p-4 sm:p-5"><p className="mb-4 text-sm font-semibold text-[var(--color-text-primary)]">Enter your service address</p><div className="grid grid-cols-1 gap-3 sm:grid-cols-[200px_1fr]">
                <div>
                  <Input
                    label="Pincode"
                    value={pincode}
                    maxLength={10}
                    onChange={(e) => {
                      setPincode(e.target.value);
                      setCoverage("idle");
                    }}
                    onBlur={() => pincode.trim().length >= 6 && checkedPincode !== pincode.trim() && checkCoverage(pincode.trim())}
                    placeholder="e.g. 452001"
                  />
                  {coverage === "checking" && (
                    <p className="mt-1.5 flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
                      <Spinner className="h-3.5 w-3.5" /> Checking coverage…
                    </p>
                  )}
                  {coverage === "covered" && (
                    <p className="mt-1.5 flex items-center gap-1 text-xs font-medium text-[var(--color-success)]">
                      <BadgeCheck className="h-3.5 w-3.5" /> We serve this area!
                    </p>
                  )}
                </div>
                <Input label="House / flat, street & area" value={line1} onChange={(e) => setLine1(e.target.value)} placeholder="e.g. 12, Palm Residency, MG Road" />
              </div></div>
            )}

            {coverage === "uncovered" && (
              <CoverageLeadInline
                pincode={checkedPincode}
                prefillName={user?.full_name || name}
                prefillPhone={user?.phone || phone}
                serviceInterest={selectedServices.map((s) => s.name).join(", ") || undefined}
              />
            )}

            {coverage === "covered" && (
              <div className="rounded-2xl border border-[#ebe5da] bg-white p-4 sm:p-5"><div className="mb-4 flex items-center gap-2"><span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#fff4c8] text-[#8a6500]"><BadgeCheck className="h-4 w-4" /></span><div><p className="text-sm font-semibold text-[var(--color-text-primary)]">Pick a suitable time</p><p className="text-xs text-[var(--color-text-secondary)]">Available slots update in real time.</p></div></div><SlotPicker serviceCenterId={centerId} date={date} onDateChange={setDate} value={slot} onChange={setSlot} /></div>
            )}
          </div>
          {sidebarSummary}
          </div>
        )}

        {/* ---- STEP 3 — Confirm & verify ---- */}
        {step === 2 && (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_340px] lg:gap-8">
            <div className="space-y-5">
              {!savedVehicleId && (
                <div>
                  <p className="mb-2.5 text-sm font-semibold text-[var(--color-text-primary)]">Vehicle details</p>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <Input label="Brand & model" value={brandModel} onChange={(e) => setBrandModel(e.target.value)} placeholder="e.g. Maruti Swift" />
                    <Input label="Registration number" value={regNumber} onChange={(e) => setRegNumber(e.target.value.toUpperCase())} placeholder="MP09AB1234" />
                  </div>
                </div>
              )}

              {!user && (
                <div>
                  <p className="mb-2.5 text-sm font-semibold text-[var(--color-text-primary)]">Contact details</p>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <Input label="Full name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" />
                    <Input
                      label="WhatsApp number"
                      value={phone}
                      maxLength={10}
                      onChange={(e) => {
                        setPhone(e.target.value.replace(/\D/g, ""));
                        setNeedLogin(false);
                      }}
                      placeholder="10-digit mobile"
                      hint="We'll send a one-time verification code here — no password needed."
                    />
                  </div>
                  {needLogin && (
                    <div className="mt-3 rounded-xl bg-[var(--color-secondary-light)] p-4">
                      <p className="text-sm text-[var(--color-text-primary)]">Welcome back! This number already has an account.</p>
                      <Input label="Password" type="password" className="mt-2" value={password} onChange={(e) => setPassword(e.target.value)} />
                      <a href="/forgot-password" className="mt-1.5 inline-block text-xs font-medium text-[var(--color-primary)] hover:underline">
                        Forgot password?
                      </a>
                    </div>
                  )}
                </div>
              )}

              {user && (
                <div className="flex items-center gap-2 rounded-xl bg-[var(--color-accent-light)] px-4 py-3 text-sm text-[var(--color-text-primary)]">
                  <BadgeCheck className="h-4 w-4 text-[var(--color-success)]" /> Booking as <span className="font-semibold">{user.full_name}</span>
                  {user.phone_verified && <Badge tone="success">Verified</Badge>}
                </div>
              )}

              {error && <p className="rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-[var(--color-error)]">{error}</p>}
            </div>

            {/* Summary */}
            <div className="h-fit rounded-2xl border border-[#e8dfcd] bg-[#fffcf6] p-5 shadow-[0_12px_32px_-25px_rgba(77,58,19,.6)] lg:sticky lg:top-24">
              <p className="mb-4 flex items-center gap-1.5 text-sm font-semibold text-[var(--color-text-primary)]">
                <Sparkles className="h-4 w-4 text-[#a17800]" /> Booking summary
              </p>
              <dl className="space-y-2 text-sm">
                {selectedServices.map((s) => (
                  <div key={s.id} className="flex justify-between">
                    <dt className="text-[var(--color-text-secondary)]">{s.name}</dt>
                    <dd className="font-mono-num font-medium">₹{priceFor(s, vehicleTypeId)}</dd>
                  </div>
                ))}
                <div className="flex justify-between border-t border-gray-200 pt-2">
                  <dt className="text-[var(--color-text-secondary)]">When</dt>
                  <dd className="text-right font-medium">
                    {date} <span className="block font-mono-num text-xs">{slot}</span>
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="flex items-center gap-1 text-[var(--color-text-secondary)]">
                    <MapPin className="h-3.5 w-3.5" /> Where
                  </dt>
                  <dd className="max-w-[160px] text-right text-xs">{savedAddressId ? myAddresses?.find((a) => a.id === savedAddressId)?.line1 : line1}</dd>
                </div>
                <div className="flex justify-between border-t border-[#dfd5bf] pt-3 text-base">
                  <dt className="font-semibold">Total</dt>
                  <dd className="font-mono-num font-bold">₹{total}</dd>
                </div>
              </dl>
              <p className="mt-3 text-[11px] leading-relaxed text-[var(--color-text-secondary)]">
                ~{totalDuration} min · pay after service · first-wash offer applied automatically if eligible.
              </p>
            </div>
          </div>
        )}

        {/* Footer nav */}
        <div className="mt-6 flex items-center justify-between gap-3 border-t border-[#eee7da] pt-4">
          {step > 0 ? (
            <Button variant="outline" onClick={() => setStep((s) => s - 1)}>
              <ArrowLeft className="h-4 w-4" /> Back
            </Button>
          ) : (
            <span />
          )}
          {step < 2 ? (
            <Button size="lg" className="min-w-36 lg:hidden" disabled={step === 0 ? !step1Valid : !step2Valid} onClick={() => setStep((s) => s + 1)}>
              Continue <ArrowRight className="h-4 w-4" />
            </Button>
          ) : (
            <Button size="lg" className="min-w-40" disabled={!step3Valid} isLoading={submitting} onClick={() => submit()}>
              {user?.phone_verified ? "Confirm booking" : "Verify & book"} <ArrowRight className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      <PhoneVerificationModal
        open={verifyOpen}
        onClose={() => setVerifyOpen(false)}
        onVerified={() => {
          setVerifyOpen(false);
          void submit(true);
        }}
      />
    </div>
  );
}
