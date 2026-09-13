import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight, BadgeCheck, Car, CheckCircle2, Lock, MapPin, Plus, ShieldCheck, Sparkles } from "lucide-react";
import { bookingPolicyApi, catalogApi, serviceCenterApi, vehicleTypeApi, getSlotHolderKey, coverageApi } from "../../api/catalog";
import { authApi, guestAuthApi } from "../../api/auth";
import { bookingApi } from "../../api/booking";
import { couponApi } from "../../api/engagement";
import { vehicleApi, addressApi } from "../../api/profile";
import { Badge, Button, Input, Modal, OtpInput, Select, Spinner } from "../ui";
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
import { sanitizeVehicleName, validateIndianMobile, validateIndianPlate } from "../../lib/validators";
import type { Service } from "../../types";
import { groupServices, parseIncludes, priceForType, titleCase } from "./landing/shared";
import { addonKit, bikeTypeIds, variantCount } from "../../lib/serviceMix";
import { QtyStepper } from "../shared/QtyStepper";

export interface WizardPreselect {
  vehicleTypeId?: string;
  serviceId?: string;
}

/** One already-added car on a multi-vehicle guest visit — everything
 *  needed to create both the vehicle and its booking once the account
 *  exists, plus enough to show it back for editing. Mirrors
 *  NewBookingPage's AddedCar, adapted for a guest who has no saved
 *  vehicles yet: every car here is brand new. */
interface GuestAddedCar {
  vehicleTypeId: string;
  brandModel: string;
  regNumber: string;
  serviceIds: string[];
  serviceQty: Record<string, number>;
  serviceLabel: string;
  subtotal: number;
}

const STEPS = ["Choose Service", "Time And Place", "Confirm And Verify"];
const required = (label: string) => `${label} *`;

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
  const { data: bookingPolicy } = useQuery({ queryKey: ["booking-policy"], queryFn: bookingPolicyApi.get });

  const [step, setStep] = useState(0);
  const [vehicleTypeId, setVehicleTypeId] = useState("");
  const [serviceIds, setServiceIds] = useState<string[]>([]);
  // Per-unit add-on counts (Extra Bike Wash ×N, Bike Polish ×N).
  const [serviceQty, setServiceQty] = useState<Record<string, number>>({});

  // Vehicle: a saved one (logged-in) or details for a new one.
  const [savedVehicleId, setSavedVehicleId] = useState<string | null>(null);
  const [brandModel, setBrandModel] = useState("");
  const [regNumber, setRegNumber] = useState("");
  // Multiple vehicles on ONE visit — same capability the logged-in wizard
  // has, now available before login too. Each entry is a fully-specified
  // car (type + service + brand/reg) already "settled"; the fields above
  // (vehicleTypeId/serviceIds/.../brandModel/regNumber) always describe
  // whichever car is currently being configured — the next one to add,
  // or the last one that rides along with the main submit.
  const [extraCars, setExtraCars] = useState<GuestAddedCar[]>([]);
  const [addCarError, setAddCarError] = useState("");
  const [showAddCar, setShowAddCar] = useState(false);
  const maxCars = bookingPolicy?.max_vehicles_per_booking ?? 5;

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
  // Abandoned-signup / stale-verification recovery: OTP is always the
  // default way back in — it proves phone ownership on the spot, whatever
  // the account's history. A password is only ever offered as an
  // alternative INSIDE that same popup, and only when the account
  // genuinely has one on file (recoveryHasPassword) — an account created
  // via a guest checkout or the WhatsApp bot never got a real password,
  // so it never sees that option at all.
  const [needOtp, setNeedOtp] = useState(false);
  const [recoveryHasPassword, setRecoveryHasPassword] = useState(false);
  const [useLoginPassword, setUseLoginPassword] = useState(false);
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
  const [autoSendVerification, setAutoSendVerification] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const createdRef = useRef<{ vehicleId?: string; addressId?: string }>({});
  // A booking that exists but isn't paid — "Pay online" was chosen and the
  // checkout was closed. It stays held (the payment window) while the
  // customer retries, switches to cash, or changes something; they are
  // NOT sent anywhere else. Guests included: by now they're signed in.
  const [held, setHeld] = useState<{ id: string; groupId?: string; token?: string; number: string; total: number; signature: string } | null>(null);
  const [paying, setPaying] = useState(false);

  const openCheckout = async (h: { id: string; groupId?: string; token?: string; number?: string }) => {
    setPaying(true);
    setError("");
    try {
      await payWithRazorpay(
        h.groupId ? { purpose: "booking_group", booking_group_id: h.groupId } : { purpose: "booking", booking_id: h.id },
        { name: name || user?.full_name, contact: phone || user?.phone }
      );
    } catch (payErr) {
      setPaying(false);
      if (!(payErr instanceof PaymentCancelled)) setError(getErrorMessage(payErr));
      return false;
    }
    setPaying(false);
    navigate(`/thank-you?token=${h.token}`, { state: bookingConfirmationState(h.number) });
    return true;
  };
  const payHeldInCash = async () => {
    if (!held) return;
    setPaying(true);
    setError("");
    try {
      if (held.groupId) await bookingApi.switchGroupToCash(held.groupId);
      else await bookingApi.switchToCash(held.id);
      navigate(`/thank-you?token=${held.token}`, { state: bookingConfirmationState(held.number) });
    } catch (err) {
      setPaying(false);
      setError(getErrorMessage(err));
    }
  };
  const releaseHeld = async () => {
    if (!held) return;
    try {
      if (held.groupId) await bookingApi.cancelGroup(held.groupId, "Changed before paying");
      else await bookingApi.cancel(held.id, "Changed before paying");
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

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [step]);

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
  const vehicleNumberLabel = bookingIsBike ? "Bike Number" : "Car Number";
  const vehicleModelLabel = bookingIsBike ? "Bike Model" : "Car Model";
  const vehicleModelPlaceholder = bookingIsBike ? "E.g. Royal Enfield Classic" : "E.g. Hyundai i20";
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
  function bookingConfirmationState(bookingNumber?: string) {
    return {
      type: "booking" as const,
      booking_number: bookingNumber,
      scheduled_date: date,
      scheduled_slot: slot,
      service_label: selectedServices.map((s) => titleCase(s.name)).join(", "),
    };
  }

  // A fingerprint of everything that decides what gets booked — if the
  // customer goes back (including via the wizard's clickable step rail,
  // which — unlike the Back button — doesn't call releaseHeld()) and
  // changes anything, the held booking/visit from before no longer
  // matches this and must never be silently reused for payment; a fresh
  // one reflecting the edit has to be created instead. Mirrors
  // NewBookingPage's currentSignature()/heldIsCurrent exactly.
  const currentSignature = () =>
    JSON.stringify({
      vt: vehicleTypeId,
      services: serviceIds,
      qty: serviceQty,
      car: regNumber.trim().toUpperCase(),
      extras: extraCars.map((c) => [c.vehicleTypeId, c.regNumber, c.serviceIds, c.serviceQty]),
      date,
      slot,
      address: savedAddressId || line1,
      pin: pinned ? [pinned.latitude, pinned.longitude] : null,
      coupon: couponDiscount > 0 ? couponCode : null,
      pay: paymentMethod,
    });
  const heldIsCurrent = !!held && held.signature === currentSignature();

  // Multiple vehicles, one visit — same capability NewBookingPage has for
  // a logged-in customer, now available before login too.
  const canAddMoreCars = extraCars.length + 1 < maxCars;
  const currentCarReady = !!vehicleTypeId && hasMain && brandModel.trim().length >= 2 && !!validateIndianPlate(regNumber);

  const addCurrentCar = () => {
    setAddCarError("");
    const plate = validateIndianPlate(regNumber);
    if (!plate) {
      setAddCarError("Enter a valid registration number for this vehicle first.");
      return;
    }
    if (extraCars.some((c) => c.regNumber === plate)) {
      setAddCarError("That vehicle is already on this visit.");
      return;
    }
    setExtraCars((cars) => [
      ...cars,
      {
        vehicleTypeId,
        brandModel: brandModel.trim(),
        regNumber: plate,
        serviceIds: [...serviceIds],
        serviceQty: { ...serviceQty },
        serviceLabel: selectedServices.map((s) => titleCase(s.name)).join(", "),
        subtotal: total,
      },
    ]);
    setShowAddCar(true);
    // Clear the editor for the NEXT vehicle — very likely a different
    // type needing different services.
    setVehicleTypeId("");
    setBrandModel("");
    setRegNumber("");
    setServiceIds([]);
    setServiceQty({});
    // The vehicle-type picker for that next car is back at the TOP of the
    // step — without this the page stays scrolled down near the button
    // that was just clicked, now looking at empty space where the
    // (collapsed, type-not-chosen-yet) service section used to be.
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const removeCar = (regNumber: string) => setExtraCars((cars) => cars.filter((c) => c.regNumber !== regNumber));

  /** "Accidentally added the wrong car/service" recovery — pulls that car
   *  back out of the settled list and loads it back into the editor
   *  fields above, so fixing a mistake is "change it and re-add", not
   *  "remove and start over". */
  const editCar = (regNumberToEdit: string) => {
    const car = extraCars.find((c) => c.regNumber === regNumberToEdit);
    if (!car) return;
    setExtraCars((cars) => cars.filter((c) => c.regNumber !== regNumberToEdit));
    setAddCarError("");
    setVehicleTypeId(car.vehicleTypeId);
    setBrandModel(car.brandModel);
    setRegNumber(car.regNumber);
    setServiceIds(car.serviceIds);
    setServiceQty(car.serviceQty);
  };

  const extrasSubtotal = extraCars.reduce((sum, c) => sum + c.subtotal, 0);
  // Once the guest has opted into a multi-vehicle visit, the current
  // car's plate/model is collected right here in step 1 (same as every
  // other car on the visit) instead of step 3 — a single-vehicle guest
  // never sees this and keeps the exact flow they have today.
  const multiCarMode = showAddCar || extraCars.length > 0;

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

  // Once at least one vehicle is already on the visit, the open editor is
  // an OPTIONAL extra car, not a required one — without this, adding a
  // car and simply wanting to continue with just that one was impossible:
  // the freshly-cleared "next vehicle" editor made this step look
  // unfinished forever.
  const step1Valid = extraCars.length > 0 ? true : !!vehicleTypeId && hasMain;
  // Rapido model: the PIN is the address. A typed house/flat line is only
  // required in the no-maps fallback, where there's no pin to stand in.
  const step2Valid =
    coverage === "covered" && !!date && !!slot && (savedAddressId ? true : mapsUp ? !!pinned : line1.trim().length >= 3);
  const validateStep = (targetStep = step) => {
    const next: Record<string, string> = {};
    if (targetStep === 0 && extraCars.length === 0) {
      // No vehicle added yet, so the open editor IS the (only) car —
      // required. Once at least one is already on the visit, this editor
      // becomes a purely optional "add one more" — Continue works fine
      // with what's already added, and a half-filled attempt at another
      // car is simply dropped rather than blocking anything.
      if (!vehicleTypeId) next.vehicleType = "Choose Your Vehicle Type.";
      if (!hasMain) next.service = "Choose One Service.";
      if (multiCarMode) {
        if (brandModel.trim().length < 2) next.brandModel = `Enter ${vehicleModelLabel}.`;
        if (!validateIndianPlate(regNumber)) next.regNumber = "Enter A Valid Registration Number.";
      }
    }
    if (targetStep === 1) {
      if (!savedAddressId && mapsUp && !pinned) next.location = "Drop The Pin On Your Service Address.";
      if (!savedAddressId && !mapsUp && line1.trim().length < 3) next.address = "Enter Your Address.";
      if (coverage !== "covered") next.location = next.location || "Choose A Service Address In Our Coverage Area.";
      if (!date) next.date = "Choose A Date.";
      if (!slot) next.slot = "Choose A Time Slot.";
    }
    if (targetStep === 2) {
      // Multi-car mode already asked (and validated) this back in step 1.
      if (!savedVehicleId && !multiCarMode && brandModel.trim().length < 2) next.brandModel = `Enter ${vehicleModelLabel}.`;
      if (!savedVehicleId && !multiCarMode && !validateIndianPlate(regNumber)) next.regNumber = "Enter A Valid Registration Number.";
      if (!user && name.trim().length < 2) next.name = "Enter Your Full Name.";
      if (!user && !validateIndianMobile(phone)) next.phone = "Enter A Valid 10-Digit WhatsApp Number.";
    }
    setFieldErrors(next);
    return Object.keys(next).length === 0;
  };

  const goNext = () => {
    setError("");
    if (validateStep(step)) setStep((s) => s + 1);
  };

  const submitFromButton = () => {
    setError("");
    if (!step1Valid) {
      setStep(0);
      setTimeout(() => validateStep(0), 0);
      return;
    }
    if (!step2Valid) {
      setStep(1);
      setTimeout(() => validateStep(1), 0);
      return;
    }
    if (!validateStep(2)) return;
    // A held booking with nothing changed is finished, not re-made: retry
    // the payment, or confirm it as cash. If anything was edited since it
    // was held (including via the step rail, which — unlike Back —
    // doesn't release it), it's stale: quietly let it go and create a
    // fresh booking that actually reflects the edit, instead of paying
    // for/confirming the old one.
    if (held && heldIsCurrent) void (paymentMethod === "online" ? openCheckout(held) : payHeldInCash());
    else if (held) void releaseHeld().then(() => submit());
    else void submit();
  };

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
        try {
          // Through AuthContext so the whole app (including the OTP
          // modal, which reads the logged-in user's phone) sees the new
          // session immediately. Random password — the customer claims
          // full access later via forgot-password → WhatsApp OTP.
          currentUser = await register({ full_name: name.trim(), phone: validateIndianMobile(phone) || phone.trim(), password: randomPassword(), guest: true });
        } catch (err) {
          if (getErrorMessage(err).toLowerCase().includes("already exists")) {
            const phoneN = validateIndianMobile(phone) || phone.trim();
            const access = await guestAuthApi.bookingAccess(phoneN).catch(() => ({ mode: "otp" as const }));
            // OTP is always the default recovery path, whether or not this
            // account has a real password — proving the phone again is no
            // more friction than typing a password, and it's the ONLY way
            // in for an account that never had one (guest/WhatsApp-made).
            // A real password is offered as an alternative inside the same
            // popup only when one genuinely exists (recoveryHasPassword).
            setRecoveryHasPassword(access.mode === "password");
            setUseLoginPassword(false);
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
            if (!sentViaWidget) await authApi.requestOtp(phoneN);
            setOtpViaWidget(sentViaWidget);
            setNeedOtp(true);
            setError("");
            return;
          }
          throw err;
        }
      }
      if (currentUser.role !== "customer") {
        setError("You're signed in as staff — bookings are for customer accounts.");
        return;
      }

      // 2. The vehicle. Skipped entirely when at least one car is already
      // on the visit and this "next vehicle" editor was left empty — it's
      // an optional extra, not a required one (see step1Valid/validateStep).
      const includeCurrentCar = extraCars.length === 0 || currentCarReady;
      let vehicleId = "";
      if (includeCurrentCar) {
        vehicleId = savedVehicleId || createdRef.current.vehicleId || "";
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
          setAutoSendVerification(true);
          setVerifyOpen(true);
          return; // resumes via onVerified → submit(true)
        }
      }

      // 5. The booking(s) — same API, same capacity checks, same WhatsApp
      // confirmation as every other booking in the system. Multiple
      // vehicles on this visit means every extra car needs its own real
      // vehicle record too (each was collected as plain fields in step 1
      // — nobody had an account yet to save them under until just now).
      // The "current" editor only rides along when it was actually
      // completed (includeCurrentCar) — left empty, it's simply dropped.
      const allCars: { vehicleId: string; serviceIds: string[]; serviceQty: Record<string, number> }[] = [];
      if (extraCars.length) {
        // Sequential, not parallel — each create can change what "already
        // exists" means for the next one, and there's no real time
        // pressure for 2-5 cars.
        for (const car of extraCars) {
          const existing = await vehicleApi.list();
          const match = existing.find((v) => v.registration_number.toUpperCase() === car.regNumber);
          const carVehicleId =
            match?.id ||
            (
              await vehicleApi.create({
                vehicle_type: car.vehicleTypeId,
                brand: car.brandModel.trim().split(/\s+/)[0] || "Vehicle",
                model: car.brandModel.trim().split(/\s+/).slice(1).join(" ") || car.brandModel.trim() || "—",
                registration_number: car.regNumber,
                is_default: false,
              })
            ).id;
          allCars.push({ vehicleId: carVehicleId, serviceIds: car.serviceIds, serviceQty: car.serviceQty });
        }
      }
      if (includeCurrentCar) allCars.push({ vehicleId, serviceIds, serviceQty });

      if (allCars.length > 1) {
        const visit = await bookingApi.createGroup({
          vehicles: allCars.map((c) => ({ vehicle_id: c.vehicleId, service_ids: c.serviceIds, service_quantities: c.serviceQty })),
          address_id: addressId,
          scheduled_date: date,
          scheduled_slot: slot,
          hold_key: getSlotHolderKey(),
          payment_method: paymentMethod,
          coupon_code: couponCode.trim() || undefined,
        });
        const visitNumber = visit.bookings.map((b) => b.booking_number).join(" + ");
        const unpaid = visit.bookings.some((b) => b.status === "awaiting_payment");
        if (unpaid && visit.total_amount > 0) {
          const h = { id: visit.bookings[0].id, groupId: visit.booking_group_id, token: visit.confirmation_token, number: visitNumber, total: visit.total_amount, signature: currentSignature() };
          setHeld(h);
          await openCheckout(h);
          return;
        }
        navigate(`/thank-you?token=${visit.confirmation_token}`, { state: bookingConfirmationState(visitNumber) });
        return;
      }
      // Exactly one car in the end — whether that's the "current" editor
      // (the common case) or the sole extraCars entry (current was left
      // empty and simply dropped) — a single normal booking, same as ever.
      const solo = allCars[0];
      const booking = await bookingApi.create({
        vehicle_id: solo.vehicleId,
        address_id: addressId,
        service_ids: solo.serviceIds,
        service_quantities: solo.serviceQty,
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
        const h = { id: booking.id, token: booking.confirmation_token, number: booking.booking_number, total: booking.total_amount, signature: currentSignature() };
        setHeld(h);
        await openCheckout(h);
        return;
      }
      navigate(`/thank-you?token=${booking.confirmation_token}`, { state: bookingConfirmationState(booking.booking_number) });
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
              <p className="font-medium text-[var(--color-text-primary)]">+ Add Bikes To This Visit</p>
              <p className="text-xs text-[var(--color-text-secondary)]">₹{priceForType(kit.addBike, vehicleTypeId).price} Per Bike, Washed At The Same Doorstep</p>
            </div>
            <QtyStepper value={extraBikes} min={0} max={10} onChange={setExtraBikes} />
          </div>
        )}
        {kit.bikePolish && bikesInBooking > 0 && (
          <div className="flex items-center justify-between rounded-xl border-2 border-gray-200 bg-white px-3.5 py-2.5 text-sm">
            <div>
              <p className="font-medium text-[var(--color-text-primary)]">+ {kit.bikePolish.name}</p>
              <p className="text-xs text-[var(--color-text-secondary)]">
                ₹{priceForType(kit.bikePolish, vehicleTypeId).price} Per Bike · Up To {bikesInBooking}
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
        <Button size="lg" className="w-full sm:w-auto sm:min-w-[150px]" onClick={goNext}>
          Continue <ArrowRight className="h-4 w-4" />
        </Button>
      ) : (
        <Button
          size="lg"
          className="w-full sm:w-auto sm:min-w-[150px]"
          isLoading={submitting || paying}
          onClick={submitFromButton}
        >
          {held && heldIsCurrent
            ? paymentMethod === "online"
              ? `Retry Payment ₹${held.total}`
              : "Confirm And Pay Cash On Service"
            : user?.phone_verified
              ? "Confirm Booking"
              : "Verify And Book"}{" "}
          <ArrowRight className="h-4 w-4" />
        </Button>
      )}
    </div>
  );

  return (
    <WizardShell
      eyebrow="Book A Service"
      title="Book Your Wash"
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
        {/* ---- STEP 1 — Choose Service ---- */}
        {step === 0 && (
          <div className="space-y-6">
            {/* Multiple vehicles on one visit — same "On this visit" list
                and Edit/Remove the logged-in wizard has, now before login
                too. Guest-only: a logged-in customer here already has the
                full NewBookingPage flow for this. */}
            {!user && !!extraCars.length && (
              <div className="rounded-xl border border-[#F3E5B5] bg-[#FFFCF0] p-3.5">
                <p className="text-sm font-semibold text-black">
                  On this visit ({extraCars.length + (currentCarReady ? 1 : 0)} of {maxCars})
                </p>
                <div className="mt-2 space-y-1.5">
                  {extraCars.map((car) => (
                    <div key={car.regNumber} className="flex items-center gap-2 text-sm">
                      <span className="min-w-0 flex-1 truncate text-gray-700">
                        <span className="font-medium text-black">{car.brandModel} · {car.regNumber}</span>
                        {car.serviceLabel ? ` · ${car.serviceLabel}` : ""}
                      </span>
                      <span className="font-mono-num shrink-0 text-gray-600">₹{car.subtotal}</span>
                      <button
                        type="button"
                        onClick={() => editCar(car.regNumber)}
                        aria-label={`Edit ${car.brandModel} on this visit`}
                        className="shrink-0 text-xs font-bold text-gray-500 underline underline-offset-2 hover:text-black"
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => removeCar(car.regNumber)}
                        aria-label={`Remove ${car.brandModel} from this visit`}
                        className="shrink-0 text-xs font-bold text-gray-400 underline underline-offset-2 hover:text-black"
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
                <p className="mt-2 border-t border-[#F3E5B5] pt-2 text-xs text-gray-500">
                  One visit, one slot — the captain washes them all at the same address.
                </p>
              </div>
            )}

            <div>
              <p className="mb-2.5 text-sm font-semibold text-[var(--color-text-primary)]">
                {required(
                  extraCars.length
                    ? `Next Vehicle (${extraCars.length + 1})`
                    : isCustomer && myVehicles?.length
                      ? "Which Vehicle?"
                      : "What Do You Drive?"
                )}
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
                          {titleCase(t.name)}
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
                        {titleCase(t.name)}
                      </button>
                    ))}
                  </div>
                  {fieldErrors.vehicleType && <p className="mt-2 text-xs font-medium text-[var(--color-error)]">{fieldErrors.vehicleType}</p>}
                </>
              )}
            </div>

            {vehicleTypeId && (
              <div className="space-y-6">
                <div>
                  <p className="text-sm font-semibold text-[var(--color-text-primary)]">{required("Pick Your Service")}</p>
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
                              <span className="block font-semibold text-[var(--color-text-primary)]">{titleCase(g.primary.name)}</span>
                              {/* No service time here (founder call) — a
                                  duration on the picker reads as a promise
                                  before we even know the vehicle. */}
                              <span className="mt-0.5 block text-xs text-[var(--color-text-secondary)]">At Your Doorstep</span>
                              {(includes.items.length > 0 || includes.summary) && (
                                <span className="mt-1.5 block text-xs leading-relaxed text-[var(--color-text-secondary)]">
                                  {includes.items.length > 0 ? includes.items.map(titleCase).join(" · ") : titleCase(includes.summary)}
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
                                How Many Bikes?
                                <span className="block text-[11px] font-normal">
                                  First Bike ₹{priceForType(g.primary, vehicleTypeId).price}, ₹{priceForType(kit.addBike, vehicleTypeId).price} Each Additional
                                </span>
                              </span>
                              <QtyStepper value={bikesInBooking} min={1} max={10} onChange={(n) => setExtraBikes(Math.max(0, n - bikeCount))} />
                            </div>
                          )}
                          {multi && !bookingIsBike && (
                            <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-black/5 pt-3">
                              <span className="mr-1 text-xs font-medium text-[var(--color-text-secondary)]">Choose An Option</span>
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
                                    {titleCase(v.variant_label ?? v.name)} · ₹{priceForType(v, vehicleTypeId).price}
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
                    <p className="text-sm font-semibold text-[var(--color-text-primary)]">Add-Ons (Optional)</p>
                    <p className="mb-2.5 mt-0.5 text-xs text-[var(--color-text-secondary)]">Extras done in the same visit.</p>
                    {addonChips}
                  </div>
                )}
                {fieldErrors.service && <p className="text-xs font-medium text-[var(--color-error)]">{fieldErrors.service}</p>}

                {/* Multi-vehicle mode: this car's plate/model is collected
                    right here (same step as its service), not deferred to
                    step 3 — exactly like every other car on the visit.
                    A single-vehicle guest never sees this; it still asks
                    in step 3 as before. */}
                {!user && hasMain && multiCarMode && (
                  <div className="rounded-lg border border-dashed border-gray-200 p-3">
                    <p className="mb-2.5 text-sm font-semibold text-[var(--color-text-primary)]">{required("This Vehicle")}</p>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <Input
                        label={required(vehicleModelLabel)}
                        value={brandModel}
                        onChange={(e) => setBrandModel(sanitizeVehicleName(e.target.value))}
                        placeholder={vehicleModelPlaceholder}
                      />
                      <Input
                        label={required(vehicleNumberLabel)}
                        value={regNumber}
                        onChange={(e) => setRegNumber(e.target.value.toUpperCase())}
                        placeholder="MP09AB1234"
                      />
                    </div>
                    {addCarError && <p className="mt-2 text-xs font-medium text-[var(--color-error)]">{addCarError}</p>}
                  </div>
                )}

                {!user && hasMain && (
                  <div>
                    {multiCarMode ? (
                      canAddMoreCars && (
                        <Button type="button" variant="outline" size="sm" disabled={!currentCarReady} onClick={addCurrentCar}>
                          <Plus className="h-3.5 w-3.5" /> Add This Vehicle To The Visit
                        </Button>
                      )
                    ) : (
                      <button
                        type="button"
                        onClick={() => setShowAddCar(true)}
                        className="flex items-center gap-1.5 text-xs font-semibold text-black underline underline-offset-2 hover:opacity-70"
                      >
                        <Plus className="h-3.5 w-3.5" /> Add another vehicle to this visit
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ---- STEP 2 — Time And Place ---- */}
        {step === 1 && (
          <div className="space-y-6">
            {isCustomer && myAddresses?.length ? (
              <div>
                <p className="mb-2.5 text-sm font-semibold text-[var(--color-text-primary)]">{required("Where Should We Come?")}</p>
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
                    + New Address
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
                  <BadgeCheck className="h-3.5 w-3.5" /> We Serve This Area!
                </p>
                {pinned && (
                  <p className="mt-1.5 flex items-start gap-1.5 text-xs text-[var(--color-text-secondary)]">
                    <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-black" />
                    <span className="min-w-0">
                      <span className="block font-medium text-black">{pinned.formatted || [pinned.area, pinned.city].filter(Boolean).join(", ")}</span>
                      Drag The Pin Above If This Isn't Your Exact Gate. The Captain Drives To This Point.
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
                    error={fieldErrors.address}
                    value={line1}
                    onChange={(e) => setLine1(e.target.value)}
                    placeholder="House / Flat, Street, Area"
                    hint="Maps Are Unavailable Right Now. Type The Address Instead."
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
                    placeholder="E.g. 452001"
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
            {(fieldErrors.location || fieldErrors.date || fieldErrors.slot) && (
              <p className="text-xs font-medium text-[var(--color-error)]">{fieldErrors.location || fieldErrors.date || fieldErrors.slot}</p>
            )}
          </div>
        )}

        {/* ---- STEP 3 — Confirm And Verify ---- */}
        {step === 2 && (
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-[1fr_320px]">
            <div className="space-y-5">
              {/* Multi-car mode already asked this back in step 1, right
                  alongside that car's service — asking again here would
                  just be the same question twice. */}
              {!savedVehicleId && !multiCarMode && (
                <div>
                  <p className="mb-2.5 text-sm font-semibold text-[var(--color-text-primary)]">{required("Your Vehicle")}</p>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <Input label={required(vehicleModelLabel)} error={fieldErrors.brandModel} value={brandModel} onChange={(e) => setBrandModel(sanitizeVehicleName(e.target.value))} placeholder={vehicleModelPlaceholder} />
                    <Input label={required(vehicleNumberLabel)} error={fieldErrors.regNumber} value={regNumber} onChange={(e) => setRegNumber(e.target.value.toUpperCase())} placeholder="MP09AB1234" />
                  </div>
                </div>
              )}

              {!user && (
                <div>
                  <p className="mb-2.5 text-sm font-semibold text-[var(--color-text-primary)]">{required("Your Details")}</p>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <Input label={required("Full Name")} error={fieldErrors.name} value={name} onChange={(e) => setName(e.target.value)} placeholder="Your Name" />
                    <Input
                      label={required("WhatsApp Number")}
                      error={fieldErrors.phone}
                      value={phone}
                      maxLength={10}
                      onChange={(e) => setPhone(e.target.value.replace(/\D/g, ""))}
                      placeholder="10-Digit Mobile"
                      hint="We'll send a one-time verification code here — no password needed."
                    />
                  </div>
                </div>
              )}

              {user && (
                <div className="flex items-center gap-2 rounded-xl bg-[var(--color-accent-light)] px-4 py-3 text-sm text-[var(--color-text-primary)]">
                  <BadgeCheck className="h-4 w-4 text-[var(--color-success)]" /> Booking As <span className="font-semibold">{user.full_name}</span>
                  {user.phone_verified && <Badge tone="success">Verified</Badge>}
                </div>
              )}

              {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
            </div>

            {/* Summary */}
            <div className="h-fit rounded-2xl bg-[var(--color-surface)] p-5">
              <p className="mb-3 flex items-center gap-1.5 text-sm font-semibold text-[var(--color-text-primary)]">
                <Sparkles className="h-4 w-4 text-[var(--color-secondary)]" /> Your Booking
              </p>
              <dl className="space-y-2 text-sm">
                {!!extraCars.length && (
                  <div className="border-b border-gray-200 pb-2">
                    <dt className="mb-1 text-[var(--color-text-secondary)]">{extraCars.length} Other Vehicle{extraCars.length > 1 ? "s" : ""} On This Visit</dt>
                    {extraCars.map((car) => (
                      <div key={car.regNumber} className="flex justify-between text-xs">
                        <dd className="text-[var(--color-text-secondary)]">
                          {car.brandModel} · {car.regNumber}
                        </dd>
                        <dd className="font-mono-num">₹{car.subtotal}</dd>
                      </div>
                    ))}
                  </div>
                )}
                {selectedServices.map((s) => (
                  <div key={s.id} className="flex justify-between">
                    <dt className="text-[var(--color-text-secondary)]">
                      {titleCase(s.name)}
                      {qtyOf(s.id) > 1 ? ` ×${qtyOf(s.id)}` : ""}
                    </dt>
                    <dd className="font-mono-num font-medium">₹{priceFor(s, vehicleTypeId) * qtyOf(s.id)}</dd>
                  </div>
                ))}
                {addonChips && (
                  <div className="border-t border-gray-200 pt-2">
                    <dt className="mb-2 text-[var(--color-text-secondary)]">Add Extras To This Visit</dt>
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
                  <dt className="font-semibold">Total{extraCars.length ? ` · ${extraCars.length + 1} Vehicles` : ""}</dt>
                  <dd className="text-right">
                    {hasFirstWashOffer && <span className="mr-2 font-mono-num text-sm text-gray-400 line-through">₹{total + extrasSubtotal}</span>}
                    <span className="font-mono-num font-bold">₹{Math.max(payableTotal - couponDiscount, 0) + extrasSubtotal}</span>
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
              <p className="mb-1.5 text-sm font-medium text-black">Have A Coupon Code?</p>
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
                  {couponApplying ? "Checking..." : "Apply"}
                </Button>
              </div>
              {couponError && <p className="mt-1 text-xs text-[var(--color-error)]">{couponError}</p>}
              {couponDiscount > 0 && (
                <p className="mt-1 text-xs font-medium text-[var(--color-success)]">Coupon Applied. ₹{couponDiscount} Off.</p>
              )}
            </div>

            {/* Cash or online — asked once, right before booking. */}
            <div>
              <p className="mb-1.5 text-sm font-medium text-black">{required("How Would You Like To Pay?")}</p>
              <div className="flex gap-2">
                {([
                  { value: "cash" as const, label: "Cash On Service", hint: "Pay The Captain At Your Door" },
                  { value: "online" as const, label: "Pay Online Now", hint: "UPI, Cards, Netbanking" },
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
        autoSend={autoSendVerification}
        onClose={() => {
          setVerifyOpen(false);
          setAutoSendVerification(false);
        }}
        onVerified={() => {
          setVerifyOpen(false);
          setAutoSendVerification(false);
          void submit(true);
        }}
      />

      {/* This number already has an account — OTP is always the default
          way back in (it's the ONLY way in for a guest/WhatsApp-made
          account with no real password); a real password is offered as a
          switch inside this same popup, only when the account actually
          has one. Nothing here ever mentions "forgot password" — that's
          a dead end for the accounts that hit this the most. */}
      <Modal
        open={needOtp}
        onClose={() => {
          setNeedOtp(false);
          setLoginOtp("");
          setPassword("");
          setUseLoginPassword(false);
          setError("");
        }}
        title="Verify It's You"
      >
        <div className="space-y-4">
          <p className="text-sm text-[var(--color-text-secondary)]">This number already has an account with us — verify it's you to continue this booking.</p>
          {!useLoginPassword ? (
            <>
              <p className="text-sm text-[var(--color-text-primary)]">
                We sent a code to <span className="font-semibold">{phone}</span>.
              </p>
              <div>
                <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">{required("Verification Code")}</p>
                <OtpInput value={loginOtp} onChange={setLoginOtp} />
              </div>
              {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
              <Button
                className="w-full"
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
                Verify And Continue Booking
              </Button>
              <div className="flex items-center justify-between text-xs">
                <button
                  type="button"
                  className="font-medium text-[var(--color-primary)] hover:underline"
                  onClick={async () => {
                    setError("");
                    const phoneN = validateIndianMobile(phone) || phone.trim();
                    try {
                      if (otpViaWidget) await widgetSendOtp(phoneN);
                      else await authApi.requestOtp(phoneN);
                    } catch (err) {
                      if (!otpViaWidget) {
                        setError(getErrorMessage(err));
                        return;
                      }
                      // The widget failed again — fall back to WhatsApp
                      // rather than let "Resend" keep failing the same way.
                      try {
                        await authApi.requestOtp(phoneN);
                        setOtpViaWidget(false);
                      } catch (fallbackErr) {
                        setError(getErrorMessage(fallbackErr));
                      }
                    }
                  }}
                >
                  Resend Code
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
                  Use A Different Number
                </button>
              </div>
              {recoveryHasPassword && (
                <button
                  type="button"
                  className="w-full text-center text-xs font-medium text-[var(--color-primary)] hover:underline"
                  onClick={() => {
                    setUseLoginPassword(true);
                    setError("");
                  }}
                >
                  Have a password instead? Log in with it
                </button>
              )}
            </>
          ) : (
            <>
              <Input label={required("Password")} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
              {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
              <Button
                className="w-full"
                disabled={password.length < 8}
                isLoading={submitting}
                onClick={async () => {
                  setError("");
                  setSubmitting(true);
                  try {
                    const phoneN = validateIndianMobile(phone) || phone.trim();
                    otpUserRef.current = await login(phoneN, password);
                    setNeedOtp(false);
                    setSubmitting(false);
                    void submit(true);
                  } catch (err) {
                    setSubmitting(false);
                    setError(getErrorMessage(err));
                  }
                }}
              >
                Log In And Continue Booking
              </Button>
              <button
                type="button"
                className="w-full text-center text-xs font-medium text-[var(--color-text-secondary)] hover:underline"
                onClick={() => {
                  setUseLoginPassword(false);
                  setPassword("");
                  setError("");
                }}
              >
                Use verification code instead
              </button>
            </>
          )}
        </div>
      </Modal>
    </WizardShell>
  );
}
