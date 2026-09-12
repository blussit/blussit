import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight, BadgeCheck, Car, CheckCircle2, Lock, MapPin, Plus, ShieldCheck, Sparkles } from "lucide-react";
import { catalogApi, serviceCenterApi, vehicleTypeApi, getSlotHolderKey, coverageApi } from "../../api/catalog";
import { authApi, guestAuthApi } from "../../api/auth";
import { bookingApi } from "../../api/booking";
import { couponApi } from "../../api/engagement";
import { vehicleApi, addressApi } from "../../api/profile";
import { Badge, Button, Input, OtpInput, Select, Spinner } from "../ui";
import { SlotPicker } from "../shared/SlotPicker";
import { PhoneVerificationModal } from "../shared/PhoneVerificationModal";
import { WizardShell, WizardStepHeader } from "../shared/WizardShell";
import { PaymentCancelled, payWithRazorpay } from "../../lib/razorpay";
import { CoverageLeadInline } from "./CoverageLeadInline";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage, tokenStorage } from "../../lib/api-client";
import { ensureOtpWidget, widgetSendOtp, widgetVerifyOtp } from "../../lib/otpWidget";
import { LocationPicker, type LocationValue } from "../shared/LocationPicker";
import { ServicePrepNotice } from "../shared/ServicePrepNotice";
import { validateIndianMobile, validateIndianPlate } from "../../lib/validators";
import type { Service } from "../../types";
import { groupServices, parseIncludes, priceForType } from "./landing/shared";
import { addonKit, bikeTypeIds, variantCount } from "../../lib/serviceMix";
import { QtyStepper } from "../shared/QtyStepper";

export interface WizardPreselect {
  vehicleTypeId?: string;
  serviceId?: string;
}

const STEPS = ["Choose service", "Time & place", "Confirm & verify"];

function priceFor(s: Service, vt: string): number {
  return s.vehicle_type_prices?.[vt] ?? s.price;
}

function firstWashPriceFor(s: Service, vt: string): number | null {
  return s.vehicle_type_discounted_prices?.[vt] ?? s.discounted_price ?? null;
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
  const { user, login, register, refreshUser } = useAuth();
  const isCustomer = !!user && user.role === "customer";
  const isStaff = !!user && user.role !== "customer";

  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const { data: servicesData } = useQuery({ queryKey: ["public-services"], queryFn: () => catalogApi.services({ page_size: 100 }) });
  const services = useMemo(() => (servicesData?.data || []).filter((s) => s.is_active !== false), [servicesData]);
  const { data: myVehicles } = useQuery({ queryKey: ["vehicles"], queryFn: vehicleApi.list, enabled: isCustomer });
  const { data: myAddresses } = useQuery({ queryKey: ["addresses"], queryFn: addressApi.list, enabled: isCustomer });

  const [step, setStep] = useState(0);
  const [vehicleTypeId, setVehicleTypeId] = useState("");
  const [serviceIds, setServiceIds] = useState<string[]>([]);
  // Per-unit add-on counts (Extra Bike Wash ×N, Bike Polish ×N).
  const [serviceQty, setServiceQty] = useState<Record<string, number>>({});

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
  // Map pin (Swiggy-style): the coordinates are the truth; area/city/
  // pincode fill from it silently. Null = maps unavailable -> manual fields.
  const [pinned, setPinned] = useState<LocationValue | null>(null);
  const [mapsUp, setMapsUp] = useState(true);
  const [date, setDate] = useState("");
  const [slot, setSlot] = useState("");

  // Guest identity.
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [needLogin, setNeedLogin] = useState(false);
  // Abandoned-signup / stale-verification recovery: prove the phone by
  // OTP instead of a password that may never have been set.
  const [needOtp, setNeedOtp] = useState(false);
  const [couponCode, setCouponCode] = useState("");
  const [couponDiscount, setCouponDiscount] = useState(0);
  const [couponError, setCouponError] = useState("");
  const [couponApplying, setCouponApplying] = useState(false);
  // Founder rule: every booking picks cash-on-service or pay-online here —
  // never silently defaulted.
  const [paymentMethod, setPaymentMethod] = useState<"cash" | "online">("cash");
  const [loginOtp, setLoginOtp] = useState("");
  const [otpViaWidget, setOtpViaWidget] = useState(false);
  const otpUserRef = useRef<typeof user>(null);
  const [password, setPassword] = useState("");

  const [verifyOpen, setVerifyOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const createdRef = useRef<{ vehicleId?: string; addressId?: string }>({});
  // A booking that exists but isn't paid — "Pay online" was chosen and the
  // checkout was closed. It stays held (the payment window) while the
  // customer retries, switches to cash, or changes something; they are
  // NOT sent anywhere else. Guests included: by now they're signed in.
  const [held, setHeld] = useState<{ id: string; token?: string; number: string; total: number } | null>(null);
  const [paying, setPaying] = useState(false);

  const openCheckout = async (h: { id: string; token?: string }) => {
    setPaying(true);
    setError("");
    try {
      await payWithRazorpay({ purpose: "booking", booking_id: h.id }, { name: name || user?.full_name, contact: phone || user?.phone });
    } catch (payErr) {
      setPaying(false);
      if (!(payErr instanceof PaymentCancelled)) setError(getErrorMessage(payErr));
      return false;
    }
    setPaying(false);
    navigate(`/thank-you?token=${h.token}`);
    return true;
  };
  const payHeldInCash = async () => {
    if (!held) return;
    setPaying(true);
    setError("");
    try {
      await bookingApi.switchToCash(held.id);
      navigate(`/thank-you?token=${held.token}`);
    } catch (err) {
      setPaying(false);
      setError(getErrorMessage(err));
    }
  };
  const releaseHeld = async () => {
    if (!held) return;
    try {
      await bookingApi.cancel(held.id, "Changed before paying");
    } catch {
      // Already released by the payment window — nothing to undo.
    }
    setHeld(null);
    setError("");
  };

  // Landing-section "Book this" buttons drive the wizard from outside.
  useEffect(() => {
    if (!preselect) return;
    if (preselect.vehicleTypeId) setVehicleTypeId(preselect.vehicleTypeId);
    if (preselect.serviceId) setServiceIds([preselect.serviceId]);
    setStep(0);
  }, [preselect]);

  // Logged-in customers start prefilled from their default vehicle/address —
  // ONCE, when their data first arrives.
  //
  // These used to guard on `savedVehicleId`/`savedAddressId` being empty AND
  // list them as dependencies, which made "+ Different vehicle" and
  // "+ New address" impossible to use: the click set the id to null, the
  // effect re-ran because that id had changed, the guard saw "nothing
  // chosen" and put the default straight back. Both buttons looked dead.
  // A ref records that the prefill has happened, so a deliberate "none"
  // is never mistaken for "not chosen yet".
  const prefilledVehicle = useRef(false);
  const prefilledAddress = useRef(false);
  useEffect(() => {
    if (!isCustomer || !myVehicles?.length || prefilledVehicle.current) return;
    prefilledVehicle.current = true;
    const def = myVehicles.find((v) => v.is_default) || myVehicles[0];
    setSavedVehicleId(def.id);
    setVehicleTypeId(def.vehicle_type);
  }, [isCustomer, myVehicles]);
  useEffect(() => {
    if (!isCustomer || !myAddresses?.length || prefilledAddress.current) return;
    prefilledAddress.current = true;
    const def = myAddresses.find((a) => a.is_default) || myAddresses[0];
    setSavedAddressId(def.id);
    setPincode(def.pincode);
  }, [isCustomer, myAddresses]);

  const eligibleServices = useMemo(
    () => services.filter((s) => !vehicleTypeId || !s.vehicle_types?.length || s.vehicle_types.includes(vehicleTypeId)),
    [services, vehicleTypeId]
  );
  // Main services (variant siblings such as Bike Wash 1–4 bikes collapse to
  // one card with a chooser) vs. add-ons offered once a main service is in.
  const mainGroups = useMemo(() => groupServices(eligibleServices.filter((s) => !s.is_addon)), [eligibleServices]);
  const selectedServices = services.filter((s) => serviceIds.includes(s.id));
  const isAddonId = (id: string) => !!services.find((s) => s.id === id)?.is_addon;
  const hasMain = selectedServices.some((s) => !s.is_addon);
  // Add-ons matched to the vehicle's class (mirrors the server's rules —
  // see lib/serviceMix.ts): car add-ons with car washes, bike polish with
  // bike washes, and the car+bikes combo (add bikes, polish those bikes).
  const bikeIdSet = useMemo(() => bikeTypeIds(vehicleTypes), [vehicleTypes]);
  const bookingIsBike = bikeIdSet.has(vehicleTypeId);
  const kit = useMemo(() => addonKit(services, vehicleTypeId, bikeIdSet), [services, vehicleTypeId, bikeIdSet]);
  const selectedBase = selectedServices.find((s) => !s.is_addon) || null;
  const bikeCount = bookingIsBike && selectedBase ? variantCount(selectedBase) : 0;
  const extraBikes = kit.addBike && serviceIds.includes(kit.addBike.id) ? serviceQty[kit.addBike.id] || 1 : 0;
  const polishCount = kit.bikePolish && serviceIds.includes(kit.bikePolish.id) ? serviceQty[kit.bikePolish.id] || 1 : 0;
  // Total bikes: a bike booking's −/+ counter books base wash + N extra-bike
  // lines (₹99 + ₹60 each additional); a car booking's bikes are all extras.
  const bikesInBooking = bookingIsBike ? bikeCount + extraBikes : extraBikes;
  const qtyOf = (id: string) => serviceQty[id] || 1;
  const total = selectedServices.reduce((sum, s) => sum + priceFor(s, vehicleTypeId) * qtyOf(s.id), 0);
  // A guest IS (almost always) a first-time customer — quote the
  // first-wash price up front instead of promising a discount while
  // showing the full number. The backend remains the authority (it
  // re-checks eligibility by plate+phone at booking time).
  const firstWashTotal = selectedServices.reduce(
    (sum, s) => sum + (firstWashPriceFor(s, vehicleTypeId) ?? priceFor(s, vehicleTypeId)) * qtyOf(s.id),
    0
  );
  const hasFirstWashOffer = firstWashTotal < total;
  // What the customer is actually being asked to pay before any coupon —
  // the first-wash price when it applies, otherwise the list total.
  const payableTotal = hasFirstWashOffer ? firstWashTotal : total;

  const setUnitQty = (svc: Service | null, n: number) => {
    if (!svc) return;
    setServiceIds((prev) => (n > 0 ? (prev.includes(svc.id) ? prev : [...prev, svc.id]) : prev.filter((x) => x !== svc.id)));
    setServiceQty((prev) => {
      const next = { ...prev };
      if (n > 0) next[svc.id] = n;
      else delete next[svc.id];
      return next;
    });
  };
  const setExtraBikes = (n: number) => {
    n = Math.max(0, Math.min(10, n));
    setUnitQty(kit.addBike, n);
    const totalBikes = bookingIsBike ? bikeCount + n : n;
    if (kit.bikePolish && polishCount > totalBikes) setUnitQty(kit.bikePolish, totalBikes);
  };
  const setPolish = (n: number) => {
    setUnitQty(kit.bikePolish, Math.max(0, Math.min(bikesInBooking, n)));
  };

  // Quantities never outlive their service selection; polish never exceeds
  // the bikes actually in the booking (variant shrank, extras removed…).
  useEffect(() => {
    setServiceQty((prev) => {
      const next = Object.fromEntries(Object.entries(prev).filter(([id]) => serviceIds.includes(id)));
      return Object.keys(next).length === Object.keys(prev).length ? prev : next;
    });
  }, [serviceIds]);
  useEffect(() => {
    if (!kit.bikePolish || !serviceIds.includes(kit.bikePolish.id)) return;
    if (polishCount > bikesInBooking) setUnitQty(kit.bikePolish, bikesInBooking);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bikesInBooking]);

  // Switching vehicle type drops services that no longer apply; add-ons
  // never survive without a main service to attach to.
  useEffect(() => {
    if (!vehicleTypeId || !services.length) return;
    setServiceIds((prev) => {
      const eligible = new Set(eligibleServices.map((s) => s.id));
      const isAddon = (id: string) => !!services.find((s) => s.id === id)?.is_addon;
      const kept = prev.filter((id) => eligible.has(id));
      const next = kept.some((id) => !isAddon(id)) ? kept : kept.filter((id) => !isAddon(id));
      return next.length === prev.length ? prev : next;
    });
  }, [vehicleTypeId, eligibleServices, services]);

  const applyCoupon = async () => {
    setCouponError("");
    if (!couponCode.trim()) return;
    setCouponApplying(true);
    try {
      const result = await couponApi.validate(couponCode.trim(), payableTotal);
      setCouponDiscount(result.discount_amount);
    } catch (err) {
      setCouponDiscount(0);
      setCouponError(getErrorMessage(err));
    } finally {
      setCouponApplying(false);
    }
  };

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

  const step1Valid = !!vehicleTypeId && hasMain;
  // Rapido model: the PIN is the address. A typed house/flat line is only
  // required in the no-maps fallback, where there's no pin to stand in.
  const step2Valid =
    coverage === "covered" && !!date && !!slot && (savedAddressId ? true : mapsUp ? !!pinned : line1.trim().length >= 3);
  const vehicleValid = savedVehicleId ? true : brandModel.trim().length >= 2 && validateIndianPlate(regNumber) !== null;
  const identityValid = user ? true : name.trim().length >= 2 && validateIndianMobile(phone) !== null;
  const step3Valid = vehicleValid && identityValid && (!needLogin || password.length >= 8);

  /** The whole pipeline, resumable and idempotent: every stage checks
   * whether its work already happened (crucial when the OTP modal pauses
   * us mid-way, or a retry follows a transient failure). */
  const submit = async (resumedAfterVerify = false) => {
    setSubmitting(true);
    setError("");
    try {
      // 1. An account to book under.
      let currentUser = user ?? otpUserRef.current;
      if (!currentUser) {
        if (needLogin) {
          currentUser = await login(validateIndianMobile(phone) || phone.trim(), password);
        } else {
          try {
            // Through AuthContext so the whole app (including the OTP
            // modal, which reads the logged-in user's phone) sees the new
            // session immediately. Random password — the customer claims
            // full access later via forgot-password → WhatsApp OTP.
            currentUser = await register({ full_name: name.trim(), phone: validateIndianMobile(phone) || phone.trim(), password: randomPassword(), guest: true });
          } catch (err) {
            if (getErrorMessage(err).toLowerCase().includes("already exists")) {
              const phoneN = validateIndianMobile(phone) || phone.trim();
              const access = await guestAuthApi.bookingAccess(phoneN).catch(() => ({ mode: "password" as const }));
              if (access.mode === "otp") {
                // Unverified/stale account (e.g. an earlier abandoned
                // signup): its password was never really set — send a
                // code instead of dead-ending on a password prompt.
                const widgetOk = await ensureOtpWidget();
                let sentViaWidget = false;
                if (widgetOk) {
                  try {
                    await widgetSendOtp(phoneN);
                    sentViaWidget = true;
                  } catch {
                    // The widget loaded but couldn't actually send (e.g.
                    // MSG91's account is out of balance) — fall back to
                    // our own WhatsApp OTP instead of leaving the customer
                    // stuck on a code that will never arrive.
                  }
                }
                if (!sentViaWidget) await authApi.forgotPassword(phoneN);
                setOtpViaWidget(sentViaWidget);
                setNeedOtp(true);
                setError("");
                return;
              }
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
        // The pinned point IS the address (the captain navigates to the
        // coordinates); its resolved label is what we store and show.
        const resolvedLine1 = pinned
          ? pinned.formatted || [pinned.area, pinned.city].filter(Boolean).join(", ")
          : line1.trim();
        const match = existing.find((a) => a.line1 === resolvedLine1 && a.pincode === checkedPincode);
        if (match) {
          addressId = match.id;
        } else {
          const created = await addressApi.create({
            label: "Home",
            line1: resolvedLine1,
            city: pinned?.city || centerCity || "—",
            state: centerState || "—",
            pincode: checkedPincode,
            latitude: pinned?.latitude ?? null,
            longitude: pinned?.longitude ?? null,
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
        if (!me.phone_verified || me.phone_verification_stale) {
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
        service_quantities: serviceQty,
        scheduled_date: date,
        scheduled_slot: slot,
        coupon_code: couponCode.trim() || undefined,
        payment_method: paymentMethod,
        hold_key: getSlotHolderKey(),
      });
      // Online: the booking is created UNCONFIRMED (awaiting_payment) and
      // only becomes real when this payment verifies. An abandoned or
      // failed checkout keeps the customer RIGHT HERE with the booking
      // held: retry, pay cash instead, or change something — every option
      // stays on this page. Only a completed payment moves them on.
      if (paymentMethod === "online" && booking.total_amount > 0) {
        const h = { id: booking.id, token: booking.confirmation_token, number: booking.booking_number, total: booking.total_amount };
        setHeld(h);
        await openCheckout(h);
        return;
      }
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

  // Add-ons: only the ones that fit the selected vehicle class (mirrors
  // the server's rules). Shown under the service list (step 1) and again
  // on the review step, so extras can still be added right before
  // confirming.
  const addonChips =
    hasMain && (kit.simple.length > 0 || kit.addBike || kit.bikePolish) ? (
      <div className="space-y-2.5">
        <div className="flex flex-wrap gap-2">
          {kit.simple.map((a) => {
            const on = serviceIds.includes(a.id);
            const { price } = priceForType(a, vehicleTypeId);
            return (
              <button
                key={a.id}
                type="button"
                onClick={() => setServiceIds((prev) => (on ? prev.filter((x) => x !== a.id) : [...prev, a.id]))}
                className={`inline-flex items-center gap-1.5 rounded-xl border-2 px-3.5 py-2 text-sm font-medium transition-colors ${
                  on
                    ? "border-[var(--color-primary)] bg-[var(--color-primary-light)] text-[var(--color-primary)]"
                    : "border-gray-200 bg-white text-[var(--color-text-secondary)] hover:border-gray-300"
                }`}
              >
                {on ? <CheckCircle2 className="h-4 w-4 text-[var(--color-success)]" /> : <Plus className="h-4 w-4" />}
                {a.name} · +₹{price}
              </button>
            );
          })}
        </div>
        {!bookingIsBike && kit.addBike && (
          <div className="flex items-center justify-between rounded-xl border-2 border-gray-200 bg-white px-3.5 py-2.5 text-sm">
            <div>
              <p className="font-medium text-[var(--color-text-primary)]">+ Add bikes to this visit</p>
              <p className="text-xs text-[var(--color-text-secondary)]">₹{priceForType(kit.addBike, vehicleTypeId).price} per bike, washed at the same doorstep</p>
            </div>
            <QtyStepper value={extraBikes} min={0} max={10} onChange={setExtraBikes} />
          </div>
        )}
        {kit.bikePolish && bikesInBooking > 0 && (
          <div className="flex items-center justify-between rounded-xl border-2 border-gray-200 bg-white px-3.5 py-2.5 text-sm">
            <div>
              <p className="font-medium text-[var(--color-text-primary)]">+ {kit.bikePolish.name}</p>
              <p className="text-xs text-[var(--color-text-secondary)]">
                ₹{priceForType(kit.bikePolish, vehicleTypeId).price} per bike · up to {bikesInBooking}
              </p>
            </div>
            <QtyStepper value={polishCount} min={0} max={bikesInBooking} onChange={setPolish} />
          </div>
        )}
      </div>
    ) : null;

  // No overflow-hidden on the card: the vehicle-type dropdown must be able to open past its edge.
  const wizardFooter = (
    <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
      {step > 0 ? (
        <Button
          variant="outline"
          className="w-full sm:w-auto"
          onClick={() => {
            // Going back means wanting to change something — release
            // whatever's held rather than let it linger unpaid.
            if (held) void releaseHeld();
            setStep((s) => s - 1);
          }}
        >
          <ArrowLeft className="h-4 w-4" /> Back
        </Button>
      ) : (
        <span className="hidden sm:block" />
      )}
      {step < 2 ? (
        <Button size="lg" className="w-full sm:w-auto sm:min-w-[150px]" disabled={step === 0 ? !step1Valid : !step2Valid} onClick={() => setStep((s) => s + 1)}>
          Continue <ArrowRight className="h-4 w-4" />
        </Button>
      ) : (
        <Button
          size="lg"
          className="w-full sm:w-auto sm:min-w-[150px]"
          disabled={!step3Valid}
          isLoading={submitting || paying}
          onClick={() => {
            // A held booking (an earlier "pay online" that didn't finish)
            // just gets retried — same page, same button. Switching to
            // cash here confirms it as cash directly, no new booking made.
            if (held) void (paymentMethod === "online" ? openCheckout(held) : payHeldInCash());
            else void submit();
          }}
        >
          {held
            ? paymentMethod === "online"
              ? `Retry payment ₹${held.total}`
              : "Confirm — pay cash on service"
            : user?.phone_verified
              ? "Confirm booking"
              : "Verify & book"}{" "}
          <ArrowRight className="h-4 w-4" />
        </Button>
      )}
    </div>
  );

  return (
    <WizardShell
      eyebrow="Book a service"
      title="Book your wash"
      steps={STEPS}
      current={step}
      onStepClick={(i) => setStep(i)}
      aside={
        <p className="flex items-center gap-1.5 text-xs text-gray-400">
          <Lock className="h-3.5 w-3.5" /> No account needed to start
        </p>
      }
      footer={wizardFooter}
    >
      <WizardStepHeader title={STEPS[step]} />
      <div>
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
                <>
                  {/* Phones: one dropdown instead of three rows of chips. */}
                  <div className="mt-2 sm:hidden">
                    <Select value={vehicleTypeId} onChange={(e) => setVehicleTypeId(e.target.value)} aria-label="Vehicle type">
                      <option value="">Select your vehicle type</option>
                      {(vehicleTypes || []).map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name}
                        </option>
                      ))}
                    </Select>
                  </div>
                  <div className="mt-2 hidden flex-wrap gap-2 sm:flex">
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
                </>
              )}
            </div>

            {vehicleTypeId && (
              <div className="space-y-6">
                <div>
                  <p className="text-sm font-semibold text-[var(--color-text-primary)]">Pick your service</p>
                  <p className="mb-2.5 mt-0.5 text-xs text-[var(--color-text-secondary)]">One service per vehicle — extras can be added below.</p>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    {mainGroups.map((g) => {
                      const chosen = g.variants.find((v) => serviceIds.includes(v.id));
                      const current = chosen ?? g.primary;
                      const active = !!chosen;
                      let { price, original } = priceForType(current, vehicleTypeId);
                      // A bike card's shown price grows with the −/+ counter:
                      // base + ₹60 per additional bike.
                      if (active && bookingIsBike && kit.addBike && extraBikes > 0) {
                        price += extraBikes * priceForType(kit.addBike, vehicleTypeId).price;
                        original = original != null ? original + extraBikes * priceForType(kit.addBike, vehicleTypeId).price : original;
                      }
                      const includes = parseIncludes(current.description);
                      const variantIds = g.variants.map((v) => v.id);
                      // One main service per vehicle: picking this one replaces any other main service, add-ons stay.
                      const selectVariant = (id: string) => setServiceIds((prev) => [...prev.filter(isAddonId), id]);
                      // Removing the main service takes its add-ons with it —
                      // an add-on can never remain selected on its own.
                      const clearGroup = () => setServiceIds((prev) => prev.filter((x) => !variantIds.includes(x) && !isAddonId(x)));
                      const multi = g.variants.length > 1;
                      return (
                        <div
                          key={g.primary.id}
                          className={`rounded-2xl border-2 p-4 transition-colors ${
                            active ? "border-[var(--color-primary)] bg-[var(--color-primary-light)]" : "border-gray-200 hover:border-gray-300"
                          }`}
                        >
                          <button
                            type="button"
                            onClick={() => (active ? clearGroup() : selectVariant(current.id))}
                            className="flex w-full items-start justify-between gap-3 text-left"
                          >
                            <span className="min-w-0">
                              <span className="block font-semibold text-[var(--color-text-primary)]">{g.primary.name}</span>
                              {/* No service time here (founder call) — a
                                  duration on the picker reads as a promise
                                  before we even know the vehicle. */}
                              <span className="mt-0.5 block text-xs text-[var(--color-text-secondary)]">at your doorstep</span>
                              {(includes.items.length > 0 || includes.summary) && (
                                <span className="mt-1.5 block text-xs leading-relaxed text-[var(--color-text-secondary)]">
                                  {includes.items.length > 0 ? includes.items.join(" · ") : includes.summary}
                                </span>
                              )}
                            </span>
                            <span className="shrink-0 text-right">
                              <span className="block font-mono-num text-lg font-bold text-[var(--color-text-primary)]">₹{price}</span>
                              {original != null && <span className="block font-mono-num text-xs text-gray-400 line-through">₹{original}</span>}
                              {active && <CheckCircle2 className="ml-auto mt-1 h-4 w-4 text-[var(--color-success)]" />}
                            </span>
                          </button>

                          {/* Bike bookings: one −/+ counter, priced base +
                              ₹60 per additional bike — no variant chips. */}
                          {active && bookingIsBike && kit.addBike && (
                            <div className="mt-3 flex items-center justify-between border-t border-black/5 pt-3">
                              <span className="text-xs font-medium text-[var(--color-text-secondary)]">
                                How many bikes?
                                <span className="block text-[11px] font-normal">
                                  First bike ₹{priceForType(g.primary, vehicleTypeId).price}, ₹{priceForType(kit.addBike, vehicleTypeId).price} each additional
                                </span>
                              </span>
                              <QtyStepper value={bikesInBooking} min={1} max={10} onChange={(n) => setExtraBikes(Math.max(0, n - bikeCount))} />
                            </div>
                          )}
                          {multi && !bookingIsBike && (
                            <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-black/5 pt-3">
                              <span className="mr-1 text-xs font-medium text-[var(--color-text-secondary)]">Choose an option</span>
                              {g.variants.map((v) => {
                                const on = current.id === v.id;
                                return (
                                  <button
                                    key={v.id}
                                    type="button"
                                    onClick={() => selectVariant(v.id)}
                                    className={`rounded-full border-2 px-3 py-1 text-xs font-medium transition-colors ${
                                      on
                                        ? "border-[var(--color-primary)] bg-white font-semibold text-[var(--color-primary)]"
                                        : "border-gray-200 bg-white text-[var(--color-text-secondary)] hover:border-gray-300"
                                    }`}
                                  >
                                    {v.variant_label ?? v.name} · ₹{priceForType(v, vehicleTypeId).price}
                                  </button>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>

                {addonChips && (
                  <div>
                    <p className="text-sm font-semibold text-[var(--color-text-primary)]">Add-ons (optional)</p>
                    <p className="mb-2.5 mt-0.5 text-xs text-[var(--color-text-secondary)]">Extras done in the same visit.</p>
                    {addonChips}
                  </div>
                )}
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

            {!savedAddressId && mapsUp && (
              <LocationPicker
                value={pinned}
                onUnavailable={() => setMapsUp(false)}
                onChange={async (v) => {
                  setPinned(v);
                  if (v.pincode) setPincode(v.pincode);
                  // Zone-aware coverage: the PIN decides (backend falls
                  // back to pincode rules when no zones are drawn yet).
                  setCoverage("checking");
                  setCheckedPincode(v.pincode || "pin");
                  setCenterId("");
                  setSlot("");
                  try {
                    const result = await coverageApi.check({ latitude: v.latitude, longitude: v.longitude, pincode: v.pincode });
                    if (result.covered && result.center) {
                      setCenterId(result.center.id);
                      setCenterCity(result.center.city || v.city);
                      setCenterState(result.center.state || v.state);
                      setCoverage("covered");
                    } else {
                      setCoverage("uncovered");
                    }
                  } catch {
                    setCoverage("uncovered");
                  }
                }}
              />
            )}
            {!savedAddressId && coverage === "checking" && (
              <p className="flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
                <Spinner className="h-3.5 w-3.5" /> Checking coverage…
              </p>
            )}
            {!savedAddressId && coverage === "covered" && (
              <div className="rounded-xl border border-[#F3E5B5] bg-[#FAFAFA] p-3">
                <p className="flex items-center gap-1 text-xs font-medium text-[var(--color-success)]">
                  <BadgeCheck className="h-3.5 w-3.5" /> We serve this area!
                </p>
                {pinned && (
                  <p className="mt-1.5 flex items-start gap-1.5 text-xs text-[var(--color-text-secondary)]">
                    <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-black" />
                    <span className="min-w-0">
                      <span className="block font-medium text-black">{pinned.formatted || [pinned.area, pinned.city].filter(Boolean).join(", ")}</span>
                      Drag the pin above if this isn't your exact gate — the captain drives to this point.
                    </span>
                  </p>
                )}
              </div>
            )}
            {!savedAddressId && (
              <>
                {/* No house/flat text box (founder call): the pin is the
                    address, Rapido-style. It comes back only when maps
                    are unavailable, where there IS no pin. */}
                {!mapsUp && (
                  <Input
                    label="Address"
                    value={line1}
                    onChange={(e) => setLine1(e.target.value)}
                    placeholder="House / flat, street, area"
                    hint="Maps are unavailable right now — type the address instead."
                  />
                )}
                {!mapsUp && (
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
                )}
              </>
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
              <SlotPicker serviceCenterId={centerId} date={date} onDateChange={setDate} value={slot} onChange={setSlot} enableHold />
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
                  {needOtp && (
                    <div className="mt-3 rounded-xl bg-[var(--color-secondary-light)] p-4">
                      <p className="text-sm text-[var(--color-text-primary)]">
                        Welcome back! We sent a verification code to <span className="font-semibold">{phone}</span> — enter it to continue.
                      </p>
                      <div className="mt-2">
                        <p className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">Verification code</p>
                        <OtpInput value={loginOtp} onChange={setLoginOtp} />
                      </div>
                      <Button
                        className="mt-3 w-full"
                        disabled={loginOtp.trim().length < 6}
                        isLoading={submitting}
                        onClick={async () => {
                          setError("");
                          setSubmitting(true);
                          try {
                            const phoneN = validateIndianMobile(phone) || phone.trim();
                            const payload = otpViaWidget
                              ? { phone: phoneN, access_token: await widgetVerifyOtp(loginOtp.trim()) }
                              : { phone: phoneN, otp: loginOtp.trim() };
                            const result = await guestAuthApi.otpLogin(payload);
                            tokenStorage.set(result.access_token, result.refresh_token);
                            otpUserRef.current = result.user;
                            await refreshUser();
                            setNeedOtp(false);
                            setSubmitting(false);
                            void submit(true);
                          } catch (err) {
                            setSubmitting(false);
                            setError(getErrorMessage(err));
                          }
                        }}
                      >
                        Verify &amp; continue booking
                      </Button>
                      <div className="mt-2 flex items-center justify-between text-xs">
                        <button
                          type="button"
                          className="font-medium text-[var(--color-primary)] hover:underline"
                          onClick={async () => {
                            setError("");
                            const phoneN = validateIndianMobile(phone) || phone.trim();
                            try {
                              if (otpViaWidget) await widgetSendOtp(phoneN);
                              else await authApi.forgotPassword(phoneN);
                            } catch (err) {
                              if (!otpViaWidget) {
                                setError(getErrorMessage(err));
                                return;
                              }
                              // The widget failed again — fall back to
                              // WhatsApp rather than let "Resend" keep
                              // failing the same way forever.
                              try {
                                await authApi.forgotPassword(phoneN);
                                setOtpViaWidget(false);
                              } catch (fallbackErr) {
                                setError(getErrorMessage(fallbackErr));
                              }
                            }
                          }}
                        >
                          Resend code
                        </button>
                        <button
                          type="button"
                          className="font-medium text-[var(--color-text-secondary)] hover:underline"
                          onClick={() => {
                            setNeedOtp(false);
                            setLoginOtp("");
                            setError("");
                          }}
                        >
                          Use a different number
                        </button>
                      </div>
                    </div>
                  )}
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
                    <dt className="text-[var(--color-text-secondary)]">
                      {s.name}
                      {qtyOf(s.id) > 1 ? ` ×${qtyOf(s.id)}` : ""}
                    </dt>
                    <dd className="font-mono-num font-medium">₹{priceFor(s, vehicleTypeId) * qtyOf(s.id)}</dd>
                  </div>
                ))}
                {addonChips && (
                  <div className="border-t border-gray-200 pt-2">
                    <dt className="mb-2 text-[var(--color-text-secondary)]">Add extras to this visit</dt>
                    <dd>{addonChips}</dd>
                  </div>
                )}
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
                {couponDiscount > 0 && (
                  <div className="flex justify-between text-[var(--color-success)]">
                    <dt>Coupon {couponCode}</dt>
                    <dd className="font-mono-num">-₹{couponDiscount}</dd>
                  </div>
                )}
                <div className="flex justify-between border-t border-gray-200 pt-2 text-base">
                  <dt className="font-semibold">Total</dt>
                  <dd className="text-right">
                    {hasFirstWashOffer && <span className="mr-2 font-mono-num text-sm text-gray-400 line-through">₹{total}</span>}
                    <span className="font-mono-num font-bold">₹{Math.max(payableTotal - couponDiscount, 0)}</span>
                  </dd>
                </div>
              </dl>
              {hasFirstWashOffer && (
                <p className="mt-3 text-[11px] leading-relaxed text-[var(--color-text-secondary)]">
                  First-wash price shown — confirmed at booking if this vehicle &amp; number are new to Blussit.
                </p>
              )}
            </div>

            {/* Coupon — a real labelled field with its own Apply button and
                plain feedback, instead of a nameless box in the price list. */}
            <div>
              <p className="mb-1.5 text-sm font-medium text-black">Have a coupon code?</p>
              <div className="flex gap-2">
                <Input
                  value={couponCode}
                  onChange={(e) => {
                    setCouponCode(e.target.value.toUpperCase());
                    setCouponDiscount(0);
                    setCouponError("");
                  }}
                  placeholder="e.g. WELCOME50"
                  className="uppercase"
                />
                <Button type="button" variant="outline" className="shrink-0" disabled={!couponCode.trim() || couponApplying} onClick={applyCoupon}>
                  {couponApplying ? "Checking…" : "Apply"}
                </Button>
              </div>
              {couponError && <p className="mt-1 text-xs text-[var(--color-error)]">{couponError}</p>}
              {couponDiscount > 0 && (
                <p className="mt-1 text-xs font-medium text-[var(--color-success)]">Coupon applied — ₹{couponDiscount} off.</p>
              )}
            </div>

            {/* Cash or online — asked once, right before booking. */}
            <div>
              <p className="mb-1.5 text-sm font-medium text-black">How would you like to pay?</p>
              <div className="flex gap-2">
                {([
                  { value: "cash" as const, label: "Cash on service", hint: "Pay the captain at your door" },
                  { value: "online" as const, label: "Pay online now", hint: "UPI, cards, netbanking" },
                ]).map((m) => (
                  <button
                    key={m.value}
                    type="button"
                    onClick={() => setPaymentMethod(m.value)}
                    className={`flex-1 rounded-xl border px-3.5 py-2.5 text-left text-sm transition-colors ${
                      paymentMethod === m.value
                        ? "border-2 border-black bg-[#FFF4CD] font-semibold text-black"
                        : "border-[#F3E5B5] bg-white text-gray-600 hover:border-black"
                    }`}
                  >
                    {m.label}
                    <span className="mt-0.5 block text-[11px] font-normal text-gray-400">{m.hint}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Same checklist the logged-in wizard shows, at the same
                moment — a first-time customer needs it most. */}
            <ServicePrepNotice services={selectedServices} className="mt-4" />
          </div>
        )}

      </div>

      <PhoneVerificationModal
        open={verifyOpen}
        onClose={() => setVerifyOpen(false)}
        onVerified={() => {
          setVerifyOpen(false);
          void submit(true);
        }}
      />
    </WizardShell>
  );
}
