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

function priceFor(s: Service, vt: string): number {
  return s.vehicle_type_prices?.[vt] ?? s.price;
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
    <div className="overflow-hidden rounded-3xl border border-gray-100 bg-white shadow-[var(--shadow-lifted)]">
      {/* Step header */}
      <div className="border-b border-gray-100 bg-[var(--color-surface)] px-6 py-5">
        <div className="flex flex-wrap items-center gap-2 sm:gap-0">
          {STEPS.map((label, i) => (
            <div key={label} className="flex items-center">
              <button
                type="button"
                onClick={() => i < step && setStep(i)}
                className={`flex items-center gap-2 rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors ${
                  i === step
                    ? "bg-[var(--color-secondary)] text-black"
                    : i < step
                      ? "bg-[var(--color-accent-light)] text-[var(--color-success)]"
                      : "bg-gray-100 text-gray-400"
                }`}
              >
                <span className="font-mono-num">{i < step ? <CheckCircle2 className="h-4 w-4" /> : i + 1}</span>
                <span className="hidden sm:inline">{label}</span>
              </button>
              {i < STEPS.length - 1 && <span className="mx-2 hidden h-px w-8 bg-gray-200 sm:block" />}
            </div>
          ))}
          <span className="ml-auto hidden items-center gap-1.5 text-xs text-[var(--color-text-secondary)] md:flex">
            <Lock className="h-3.5 w-3.5" /> No account needed to start
          </span>
        </div>
      </div>

      <div className="p-6 md:p-8">
        {/* ---- STEP 1 — Choose service ---- */}
        {step === 0 && (
          <div className="space-y-6">
            <div>
              <p className="mb-2.5 text-sm font-semibold text-[var(--color-text-primary)]">
                {isCustomer && myVehicles?.length ? "Which vehicle?" : "What do you drive?"}
              </p>
              {isCustomer && myVehicles?.length ? (
                <div className="flex flex-wrap gap-2">
                  {myVehicles.map((v) => (
                    <button
                      key={v.id}
                      type="button"
                      onClick={() => {
                        setSavedVehicleId(v.id);
                        setVehicleTypeId(v.vehicle_type);
                      }}
                      className={`flex items-center gap-2 rounded-xl border-2 px-4 py-2.5 text-sm font-medium transition-colors ${
                        savedVehicleId === v.id ? "border-[var(--color-primary)] bg-[var(--color-primary-light)]" : "border-gray-200 hover:border-gray-300"
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
                    className={`rounded-xl border-2 px-4 py-2.5 text-sm font-medium ${savedVehicleId === null ? "border-[var(--color-primary)] bg-[var(--color-primary-light)]" : "border-dashed border-gray-300 text-[var(--color-text-secondary)]"}`}
                  >
                    + Different vehicle
                  </button>
                </div>
              ) : null}
              {(!isCustomer || !myVehicles?.length || savedVehicleId === null) && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {(vehicleTypes || []).map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => setVehicleTypeId(t.id)}
                      className={`rounded-xl border-2 px-4 py-2.5 text-sm font-medium transition-colors ${
                        vehicleTypeId === t.id ? "border-[var(--color-primary)] bg-[var(--color-primary-light)] text-[var(--color-primary)]" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
                      }`}
                    >
                      {t.name}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {vehicleTypeId && (
              <div>
                <p className="mb-2.5 text-sm font-semibold text-[var(--color-text-primary)]">Pick your service(s)</p>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {eligibleServices.map((s) => {
                    const active = serviceIds.includes(s.id);
                    return (
                      <button
                        key={s.id}
                        type="button"
                        onClick={() => setServiceIds((prev) => (active ? prev.filter((id) => id !== s.id) : [...prev, s.id]))}
                        className={`flex items-start justify-between gap-3 rounded-2xl border-2 p-4 text-left transition-colors ${
                          active ? "border-[var(--color-primary)] bg-[var(--color-primary-light)]" : "border-gray-200 hover:border-gray-300"
                        }`}
                      >
                        <span>
                          <span className="block font-semibold text-[var(--color-text-primary)]">{s.name}</span>
                          <span className="mt-0.5 block text-xs text-[var(--color-text-secondary)]">{s.duration_minutes} min · at your doorstep</span>
                        </span>
                        <span className="shrink-0 text-right">
                          <span className="block font-mono-num text-lg font-bold text-[var(--color-text-primary)]">₹{priceFor(s, vehicleTypeId)}</span>
                          {active && <CheckCircle2 className="ml-auto mt-1 h-4 w-4 text-[var(--color-success)]" />}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ---- STEP 2 — Time & place ---- */}
        {step === 1 && (
          <div className="space-y-6">
            {isCustomer && myAddresses?.length ? (
              <div>
                <p className="mb-2.5 text-sm font-semibold text-[var(--color-text-primary)]">Where should we come?</p>
                <div className="flex flex-wrap gap-2">
                  {myAddresses.map((a) => (
                    <button
                      key={a.id}
                      type="button"
                      onClick={() => {
                        setSavedAddressId(a.id);
                        setPincode(a.pincode);
                      }}
                      className={`rounded-xl border-2 px-4 py-2.5 text-left text-sm transition-colors ${
                        savedAddressId === a.id ? "border-[var(--color-primary)] bg-[var(--color-primary-light)]" : "border-gray-200 hover:border-gray-300"
                      }`}
                    >
                      <span className="font-medium">{a.label}</span>
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
                    className={`rounded-xl border-2 px-4 py-2.5 text-sm ${savedAddressId === null ? "border-[var(--color-primary)] bg-[var(--color-primary-light)]" : "border-dashed border-gray-300 text-[var(--color-text-secondary)]"}`}
                  >
                    + New address
                  </button>
                </div>
              </div>
            ) : null}

            {!savedAddressId && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-[200px_1fr]">
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
              </div>
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
              <SlotPicker serviceCenterId={centerId} date={date} onDateChange={setDate} value={slot} onChange={setSlot} />
            )}
          </div>
        )}

        {/* ---- STEP 3 — Confirm & verify ---- */}
        {step === 2 && (
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-[1fr_320px]">
            <div className="space-y-5">
              {!savedVehicleId && (
                <div>
                  <p className="mb-2.5 text-sm font-semibold text-[var(--color-text-primary)]">Your vehicle</p>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <Input label="Brand & model" value={brandModel} onChange={(e) => setBrandModel(e.target.value)} placeholder="e.g. Maruti Swift" />
                    <Input label="Registration number" value={regNumber} onChange={(e) => setRegNumber(e.target.value.toUpperCase())} placeholder="MP09AB1234" />
                  </div>
                </div>
              )}

              {!user && (
                <div>
                  <p className="mb-2.5 text-sm font-semibold text-[var(--color-text-primary)]">Your details</p>
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

              {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
            </div>

            {/* Summary */}
            <div className="h-fit rounded-2xl bg-[var(--color-surface)] p-5">
              <p className="mb-3 flex items-center gap-1.5 text-sm font-semibold text-[var(--color-text-primary)]">
                <Sparkles className="h-4 w-4 text-[var(--color-secondary)]" /> Your booking
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
                <div className="flex justify-between border-t border-gray-200 pt-2 text-base">
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
        <div className="mt-8 flex items-center justify-between gap-3">
          {step > 0 ? (
            <Button variant="outline" onClick={() => setStep((s) => s - 1)}>
              <ArrowLeft className="h-4 w-4" /> Back
            </Button>
          ) : (
            <span />
          )}
          {step < 2 ? (
            <Button size="lg" disabled={step === 0 ? !step1Valid : !step2Valid} onClick={() => setStep((s) => s + 1)}>
              Continue <ArrowRight className="h-4 w-4" />
            </Button>
          ) : (
            <Button size="lg" disabled={!step3Valid} isLoading={submitting} onClick={() => submit()}>
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
