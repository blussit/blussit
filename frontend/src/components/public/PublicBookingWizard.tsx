import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight, BadgeCheck, Car, CheckCircle2, Lock, MapPin, ShieldCheck, Sparkles } from "lucide-react";
import { catalogApi, serviceCenterApi, vehicleTypeApi } from "../../api/catalog";
import { authApi } from "../../api/auth";
import { bookingApi } from "../../api/booking";
import { vehicleApi, addressApi } from "../../api/profile";
import { Button, Input, Spinner } from "../ui";
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
    <div className="relative mx-auto max-w-6xl">
      {/* 3-Step Indicator */}
      <div className="mb-8 flex items-center justify-between sm:justify-start sm:gap-6 overflow-x-auto pb-2 scrollbar-hide">
        {STEPS.map((label, i) => {
          const active = i === step;
          const completed = i < step;
          return (
            <div key={label} className="flex items-center gap-3 shrink-0">
              <button
                type="button"
                onClick={() => completed && setStep(i)}
                className={`flex items-center gap-2.5 transition-colors ${active ? "text-black" : completed ? "text-[#312D26] hover:text-black cursor-pointer" : "text-neutral-400 cursor-default"}`}
              >
                <span className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${active ? "bg-[#E8A900] text-black" : completed ? "bg-[#FFF4CD] text-[#E8A900]" : "bg-[#F3F0EB] text-neutral-400"}`}>
                  {completed ? <CheckCircle2 className="h-4 w-4" /> : `0${i + 1}`}
                </span>
                <span className={`text-sm font-bold tracking-tight ${active ? "text-black" : "text-[#312D26]"}`}>{label}</span>
              </button>
              {i < STEPS.length - 1 && <span className={`h-px w-8 sm:w-16 ml-3 ${completed ? "bg-[#E8A900]/50" : "bg-[#E1D7C4]/60"}`} />}
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-8 items-start">
        
        {/* LEFT / MAIN AREA */}
        <div className="min-w-0 space-y-8 rounded-3xl border border-[#E1D7C4] bg-white p-5 sm:p-8 shadow-[0_8px_30px_rgba(49,45,38,0.04)]">
          
          <div className="mb-6">
            <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-[#E8A900]">{STEP_DETAILS[step].eyebrow}</p>
            <h2 className="mt-2 font-display text-2xl font-bold tracking-tight text-[#312D26] sm:text-3xl">{STEP_DETAILS[step].title}</h2>
          </div>

          {/* ---- STEP 1 — Choose service ---- */}
          {step === 0 && (
            <div className="space-y-10">
              
              {/* Vehicle Selection */}
              <div>
                <p className="mb-4 text-[15px] font-bold text-[#312D26]">
                  {isCustomer && myVehicles?.length ? "Which vehicle?" : "What do you drive?"}
                </p>
                
                {isCustomer && myVehicles?.length ? (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {myVehicles.map((v) => (
                      <button
                        key={v.id}
                        type="button"
                        onClick={() => {
                          setSavedVehicleId(v.id);
                          setVehicleTypeId(v.vehicle_type);
                        }}
                        className={`group relative flex min-h-[72px] items-center gap-3 rounded-[16px] border px-4 py-3 text-left transition-all ${
                          savedVehicleId === v.id ? "border-[#E8A900] bg-[#FFF4CD]/40 ring-1 ring-[#E8A900] shadow-[0_4px_12px_rgba(232,169,0,0.12)]" : "border-[#E1D7C4] hover:border-[#D4C5A9] hover:bg-[#FFFCF5]"
                        }`}
                      >
                        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl transition-colors ${savedVehicleId === v.id ? 'bg-white shadow-sm text-[#E8A900]' : 'bg-[#FFFCF5] text-[#312D26]/60 group-hover:text-[#E8A900]'}`}>
                          <Car className="h-5 w-5" />
                        </div>
                        <div>
                          <div className="text-sm font-bold text-[#312D26]">{v.brand} {v.model}</div>
                          <div className="mt-0.5 font-mono-num text-[11px] font-semibold text-[#312D26]/50 uppercase">{v.registration_number}</div>
                        </div>
                        {savedVehicleId === v.id && <CheckCircle2 className="absolute right-3 top-3 h-4 w-4 text-[#E8A900]" />}
                      </button>
                    ))}
                    <button
                      type="button"
                      onClick={() => {
                        setSavedVehicleId(null);
                        setVehicleTypeId("");
                      }}
                      className={`flex min-h-[72px] items-center justify-center rounded-[16px] border border-dashed px-4 py-3 text-sm font-bold transition-all ${savedVehicleId === null ? "border-[#E8A900] bg-[#FFF4CD]/30 text-[#E8A900]" : "border-[#E1D7C4] text-[#312D26]/60 hover:bg-[#FFFCF5] hover:text-[#312D26]"}`}
                    >
                      + Different vehicle
                    </button>
                  </div>
                ) : null}

                {(!isCustomer || !myVehicles?.length || savedVehicleId === null) && (
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
                    {(vehicleTypes || []).map((t) => (
                      <button
                        key={t.id}
                        type="button"
                        onClick={() => setVehicleTypeId(t.id)}
                        className={`group relative flex flex-col items-center justify-center gap-2 rounded-[16px] border px-3 py-4 text-center transition-all ${
                          vehicleTypeId === t.id ? "border-[#E8A900] bg-[#FFF4CD]/40 ring-1 ring-[#E8A900] shadow-[0_4px_12px_rgba(232,169,0,0.12)]" : "border-[#E1D7C4] hover:border-[#D4C5A9] hover:bg-[#FFFCF5]"
                        }`}
                      >
                        <span className={`flex h-12 w-12 items-center justify-center rounded-2xl transition-colors ${vehicleTypeId === t.id ? 'bg-white shadow-sm text-[#E8A900]' : 'bg-[#F3F0EB] text-[#312D26]/60 group-hover:text-[#E8A900]'}`}>
                          <Car className="h-6 w-6" />
                        </span>
                        <span className="text-[13px] font-bold text-[#312D26]">{t.name}</span>
                        {vehicleTypeId === t.id && <CheckCircle2 className="absolute right-2 top-2 h-4 w-4 rounded-full bg-[#E8A900] text-black" />}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Service Selection */}
              {vehicleTypeId && (
                <div className="pt-6 border-t border-[#E1D7C4]/60">
                  <div className="mb-4 flex items-end justify-between gap-4">
                    <div>
                      <p className="text-[15px] font-bold text-[#312D26]">Choose your service</p>
                      <p className="mt-1 text-[13px] text-[#312D26]/60">You can select more than one service.</p>
                    </div>
                  </div>
                  
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                    {eligibleServices.map((s) => {
                      const active = serviceIds.includes(s.id);
                      return (
                        <button
                          key={s.id}
                          type="button"
                          onClick={() => setServiceIds((prev) => (active ? prev.filter((id) => id !== s.id) : [...prev, s.id]))}
                          className={`relative flex flex-col rounded-[20px] border p-4 text-left transition-all ${
                            active ? "border-[#E8A900] bg-[#FFF4CD]/30 ring-1 ring-[#E8A900] shadow-[0_6px_20px_rgba(232,169,0,0.15)]" : "border-[#E1D7C4] bg-white hover:border-[#D4C5A9] hover:bg-[#FFFCF5] hover:shadow-sm"
                          }`}
                        >
                          <div className="flex justify-between items-start w-full mb-3">
                             <img src={serviceImageFor(s.name)} alt="" className="h-12 w-12 shrink-0 rounded-xl object-cover bg-[#FFFCF5] shadow-sm border border-[#E1D7C4]/50" />
                             {active ? <CheckCircle2 className="h-5 w-5 text-[#E8A900]" /> : <div className="h-5 w-5 rounded-full border-2 border-[#E1D7C4]" />}
                          </div>
                          
                          <div className="text-[15px] font-bold text-[#312D26] leading-snug">{s.name}</div>
                          <div className="mt-1 flex items-center gap-1.5 text-[11px] font-bold tracking-wide text-[#312D26]/50">
                             <span>{s.duration_minutes} MIN</span>
                             <span className="h-1 w-1 rounded-full bg-[#E1D7C4]" />
                             <span>AT DOORSTEP</span>
                          </div>
                          
                          <div className="mt-4 pt-3 border-t border-[#E1D7C4]/40 w-full flex justify-between items-center">
                            <span className="font-mono-num text-[17px] font-black text-[#312D26]">₹{priceFor(s, vehicleTypeId)}</span>
                          </div>
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
            <div className="space-y-8">
              {/* Location */}
              <div>
                {isCustomer && myAddresses?.length ? (
                  <div className="mb-6">
                    <p className="mb-4 text-[15px] font-bold text-[#312D26]">Where should we come?</p>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      {myAddresses.map((a) => (
                        <button
                          key={a.id}
                          type="button"
                          onClick={() => {
                            setSavedAddressId(a.id);
                            setPincode(a.pincode);
                          }}
                          className={`group flex min-h-[72px] items-center gap-3 rounded-[16px] border px-4 py-3 text-left transition-all ${
                            savedAddressId === a.id ? "border-[#E8A900] bg-[#FFF4CD]/40 ring-1 ring-[#E8A900] shadow-[0_4px_12px_rgba(232,169,0,0.12)]" : "border-[#E1D7C4] hover:border-[#D4C5A9] hover:bg-[#FFFCF5]"
                          }`}
                        >
                          <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl transition-colors ${savedAddressId === a.id ? 'bg-white shadow-sm text-[#E8A900]' : 'bg-[#FFFCF5] text-[#312D26]/60 group-hover:text-[#E8A900]'}`}>
                            <MapPin className="h-5 w-5" />
                          </div>
                          <div>
                             <div className="text-sm font-bold text-[#312D26]">{a.label}</div>
                             <div className="mt-0.5 text-[12px] font-medium text-[#312D26]/60 line-clamp-1">{a.line1}</div>
                          </div>
                          {savedAddressId === a.id && <CheckCircle2 className="ml-auto h-5 w-5 text-[#E8A900]" />}
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
                        className={`flex min-h-[72px] items-center justify-center gap-2 rounded-[16px] border border-dashed px-4 py-3 text-sm font-bold transition-all ${savedAddressId === null ? "border-[#E8A900] bg-[#FFF4CD]/30 text-[#E8A900]" : "border-[#E1D7C4] text-[#312D26]/60 hover:bg-[#FFFCF5] hover:text-[#312D26]"}`}
                      >
                        + New address
                      </button>
                    </div>
                  </div>
                ) : null}

                {!savedAddressId && (
                  <div className="rounded-[20px] border border-[#E1D7C4]/70 bg-[#FFFCF5] p-5">
                    <p className="mb-4 text-[15px] font-bold text-[#312D26]">Enter your service address</p>
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-[200px_1fr]">
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
                          <p className="mt-2 flex items-center gap-1.5 text-xs font-bold text-[#312D26]/50 uppercase tracking-wide">
                            <Spinner className="h-3.5 w-3.5" /> Checking...
                          </p>
                        )}
                        {coverage === "covered" && (
                          <p className="mt-2 flex items-center gap-1 text-[11px] font-bold uppercase tracking-wider text-[var(--color-success)]">
                            <BadgeCheck className="h-4 w-4" /> We serve this area
                          </p>
                        )}
                      </div>
                      <Input label="House / flat, street & area" value={line1} onChange={(e) => setLine1(e.target.value)} placeholder="e.g. 12, Palm Residency, MG Road" />
                    </div>
                  </div>
                )}

                {coverage === "uncovered" && (
                  <div className="mt-5">
                    <CoverageLeadInline
                      pincode={checkedPincode}
                      prefillName={user?.full_name || name}
                      prefillPhone={user?.phone || phone}
                      serviceInterest={selectedServices.map((s) => s.name).join(", ") || undefined}
                    />
                  </div>
                )}
              </div>

              {/* Time */}
              {coverage === "covered" && (
                <div className="pt-6 border-t border-[#E1D7C4]/60">
                  <div className="mb-4 flex items-center gap-2">
                    <div>
                      <p className="text-[15px] font-bold text-[#312D26]">Pick a suitable time</p>
                      <p className="mt-1 text-[13px] text-[#312D26]/60">Available slots update in real time.</p>
                    </div>
                  </div>
                  <div className="rounded-[20px] border border-[#E1D7C4]/50 bg-[#FFFCF5]/50 p-1">
                    <SlotPicker serviceCenterId={centerId} date={date} onDateChange={setDate} value={slot} onChange={setSlot} />
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ---- STEP 3 — Confirm & verify ---- */}
          {step === 2 && (
            <div className="space-y-6">
              {!savedVehicleId && (
                <div className="rounded-[20px] border border-[#E1D7C4]/70 bg-[#FFFCF5] p-5">
                  <p className="mb-4 text-[15px] font-bold text-[#312D26]">Vehicle details</p>
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <Input label="Brand & model" value={brandModel} onChange={(e) => setBrandModel(e.target.value)} placeholder="e.g. Maruti Swift" />
                    <Input label="Registration number" value={regNumber} onChange={(e) => setRegNumber(e.target.value.toUpperCase())} placeholder="MP09AB1234" />
                  </div>
                </div>
              )}

              {!user && (
                <div className="rounded-[20px] border border-[#E1D7C4]/70 bg-[#FFFCF5] p-5">
                  <p className="mb-4 text-[15px] font-bold text-[#312D26]">Contact details</p>
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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
                      hint="We'll send a one-time verification code here."
                    />
                  </div>
                  {needLogin && (
                    <div className="mt-5 rounded-xl border border-[#E8A900]/30 bg-[#FFF4CD]/40 p-4">
                      <p className="text-sm font-bold text-[#A87400]">Welcome back! This number already has an account.</p>
                      <Input label="Password" type="password" className="mt-3 bg-white" value={password} onChange={(e) => setPassword(e.target.value)} />
                      <a href="/forgot-password" className="mt-2 inline-block text-[13px] font-bold text-[#A87400] hover:underline">
                        Forgot password?
                      </a>
                    </div>
                  )}
                </div>
              )}

              {user && (
                <div className="flex items-center gap-3 rounded-[16px] bg-[#F0FDF4] px-5 py-4 text-sm border border-[#BBF7D0]">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-white shadow-sm text-[#16A34A]">
                     <BadgeCheck className="h-5 w-5" />
                  </div>
                  <div>
                     <p className="font-bold text-[#166534]">Booking as {user.full_name}</p>
                     {user.phone_verified && <p className="text-xs font-semibold text-[#15803D] mt-0.5">Your phone number is verified.</p>}
                  </div>
                </div>
              )}

              {error && (
                <div className="rounded-[16px] border border-red-200 bg-red-50 px-5 py-4 text-[13px] font-bold text-red-700">
                  {error}
                </div>
              )}
            </div>
          )}

          {/* Footer Nav Mobile */}
          <div className="mt-8 flex items-center justify-between gap-3 lg:hidden">
            {step > 0 ? (
              <Button variant="outline" onClick={() => setStep((s) => s - 1)}>
                <ArrowLeft className="h-4 w-4" /> Back
              </Button>
            ) : (
              <span />
            )}
          </div>
        </div>

        {/* RIGHT / SUMMARY AREA */}
        <div className="sticky top-24 z-10 w-full">
          <div className="rounded-[24px] border border-[#E1D7C4] bg-[#FFFCF5] p-6 shadow-[0_8px_30px_rgba(49,45,38,0.06)]">
            <h3 className="text-lg font-black text-[#312D26]">Your Booking</h3>
            
            <div className="mt-6 space-y-5">
              <div>
                 <div className="flex items-center justify-between">
                   <p className="text-[11px] font-bold uppercase tracking-[0.15em] text-[#312D26]/50">Vehicle</p>
                   {step > 0 && <button onClick={() => setStep(0)} className="text-[11px] font-bold text-[#E8A900] hover:underline">Edit</button>}
                 </div>
                 <p className="mt-1 text-[15px] font-bold text-[#312D26]">{selectedVehicleLabel || "—"}</p>
              </div>

              <div className="border-t border-[#E1D7C4]/60 pt-5">
                 <div className="flex items-center justify-between mb-3">
                   <p className="text-[11px] font-bold uppercase tracking-[0.15em] text-[#312D26]/50">Services ({selectedServices.length})</p>
                   {step > 0 && <button onClick={() => setStep(0)} className="text-[11px] font-bold text-[#E8A900] hover:underline">Edit</button>}
                 </div>
                 {selectedServices.length ? (
                   <div className="space-y-2.5">
                     {selectedServices.map(s => (
                       <div key={s.id} className="flex items-start justify-between text-[14px]">
                         <span className="font-bold text-[#312D26] pr-4">{s.name}</span>
                         <span className="font-black text-[#312D26] font-mono-num">₹{priceFor(s, vehicleTypeId)}</span>
                       </div>
                     ))}
                   </div>
                 ) : (
                   <p className="text-sm font-medium text-[#312D26]/40">No services selected</p>
                 )}
              </div>

              {(date || line1 || savedAddressId) && (
                <div className="border-t border-[#E1D7C4]/60 pt-5 space-y-3">
                   <div className="flex items-center justify-between mb-1">
                     <p className="text-[11px] font-bold uppercase tracking-[0.15em] text-[#312D26]/50">Schedule & Location</p>
                     {step > 1 && <button onClick={() => setStep(1)} className="text-[11px] font-bold text-[#E8A900] hover:underline">Edit</button>}
                   </div>
                   {date && (
                     <div className="flex justify-between text-[14px]">
                       <span className="text-[#312D26] font-bold">{date} {slot && <span className="text-[11px] ml-1.5 bg-[#FFF4CD] text-[#A87400] px-2 py-0.5 rounded-[6px] font-bold">{slot}</span>}</span>
                     </div>
                   )}
                   {(line1 || savedAddressId) && (
                     <div className="flex items-start gap-2 text-[13px] text-[#312D26]/70 font-medium">
                       <MapPin className="h-4 w-4 shrink-0 mt-0.5 text-[#E8A900]" />
                       <span className="line-clamp-2 leading-tight">{savedAddressId ? myAddresses?.find((a) => a.id === savedAddressId)?.line1 : line1}</span>
                     </div>
                   )}
                </div>
              )}

              <div className="border-t border-[#E1D7C4] pt-5 space-y-2">
                 <div className="flex justify-between text-[14px]">
                   <span className="text-[#312D26]/60 font-bold uppercase tracking-wide">Subtotal</span>
                   <span className="font-black text-[#312D26] font-mono-num">₹{total}</span>
                 </div>
                 <div className="flex justify-between text-xl mt-1">
                   <span className="font-black text-[#312D26] uppercase">Total</span>
                   <span className="font-black text-[#E8A900] font-mono-num">₹{total}</span>
                 </div>
                 <p className="text-[11px] font-bold text-[#312D26]/40 pt-1">
                   ~{totalDuration} MIN · PAY AFTER SERVICE
                 </p>
              </div>
            </div>
            
            <div className="mt-8">
              {step === 0 && (
                <Button size="lg" className="w-full text-[15px] font-bold shadow-[0_6px_16px_rgba(232,169,0,0.24)]" disabled={!step1Valid} onClick={() => setStep(1)}>
                  Choose Time <ArrowRight className="h-4 w-4 ml-1.5" />
                </Button>
              )}
              {step === 1 && (
                <Button size="lg" className="w-full text-[15px] font-bold shadow-[0_6px_16px_rgba(232,169,0,0.24)]" disabled={!step2Valid} onClick={() => setStep(2)}>
                  Confirm & Pay <ArrowRight className="h-4 w-4 ml-1.5" />
                </Button>
              )}
              {step === 2 && (
                <Button size="lg" className="w-full text-[15px] font-bold shadow-[0_6px_16px_rgba(232,169,0,0.24)]" disabled={!step3Valid} isLoading={submitting} onClick={() => submit()}>
                  {user?.phone_verified ? "Confirm Booking" : "Verify & Book"} <ArrowRight className="h-4 w-4 ml-1.5" />
                </Button>
              )}
            </div>

            {/* Trust Strip */}
            <div className="mt-6 flex flex-wrap items-center justify-center gap-x-4 gap-y-2 text-[10px] font-bold uppercase tracking-wider text-[#312D26]/50">
               <span className="flex items-center gap-1.5"><CheckCircle2 className="h-3.5 w-3.5 text-[#E8A900]" /> Trained Pros</span>
               <span className="flex items-center gap-1.5"><Sparkles className="h-3.5 w-3.5 text-[#E8A900]" /> Eco-Friendly</span>
               <span className="flex items-center gap-1.5"><Lock className="h-3.5 w-3.5 text-[#E8A900]" /> Secure</span>
            </div>
          </div>
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
