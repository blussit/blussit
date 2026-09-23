import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { BadgeCheck, Banknote, Car, CheckCircle2, CreditCard, MapPin, Plus, Trash2, ShieldCheck } from "lucide-react";
import { bookingPolicyApi, catalogApi, coverageApi, serviceCenterApi, vehicleTypeApi, getSlotHolderKey } from "../../api/catalog";
import { bookingApi, type PhoneProof, type QuickBookingLine, type QuickBookingPayload } from "../../api/booking";
import { addressApi } from "../../api/profile";
import { adminServiceCenterApi } from "../../api/admin";
import { Button, Input, Select, Spinner, Switch } from "../ui";
import { SlotPicker } from "../shared/SlotPicker";
import { LocationPicker, type LocationValue } from "../shared/LocationPicker";
import { WizardShell, WizardStepHeader } from "../shared/WizardShell";
import { ServicePrepNotice } from "../shared/ServicePrepNotice";
import { QtyStepper } from "../shared/QtyStepper";
import { CustomerNamePhoneFields } from "../shared/CustomerNamePhoneFields";
import { CustomerActivePasses } from "../shared/CustomerActivePasses";
import { BookingOtpModal } from "./BookingOtpModal";
import { CoverageLeadInline } from "../public/CoverageLeadInline";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { scrollToTopNow } from "../../lib/scroll";
import { ensureGoogleMaps } from "../../lib/googleMaps";
import { daysAgoIST, nowTimeIST, todayIST } from "../../lib/date";
import { validateIndianMobile, cleanMobileInput } from "../../lib/validators";
import { addonKit, baseGroups, bikeTypeIds, variantCount, type BaseGroup } from "../../lib/serviceMix";
import { parseIncludes, titleCase } from "../public/landing/shared";
import type { Address, Service, VehicleTypeOption } from "../../types";

/**
 * THE booking flow (2026-09 quick-booking model) — two steps, no account;
 * anonymous bookings end with a one-time-code popup (BookingOtpModal):
 *
 *   1. What are we washing?  pick a vehicle type, how many, one service
 *      (+ add-ons); "Add another vehicle" for a different type on the
 *      same visit ("Bike ×2 + SUV ×1").
 *   2. Where & when?         pin the address, pick a slot, name + phone,
 *      how to pay → Book (→ verify the number with an OTP if not signed in).
 *
 * One component, three seats:
 *   - "public":   anyone on /book — a profile is created silently from the
 *                 phone; login stays optional (OTP) for viewing history.
 *   - "customer": the same flow on /app/book, with name/phone/saved
 *                 addresses prefilled and any matching pass applied by
 *                 the server automatically.
 *   - "manager":  the same flow on a customer's behalf (phone-in bookings).
 *
 * Bikes keep the catalogue's per-bike pricing (base wash + N extra bikes
 * on ONE booking); every car of a type is its own booking on the visit —
 * exactly what the backend's group model expects.
 */
/** "manager-log": a job the manager already did himself — same vehicle/service
 *  picker, then who/where/when as it HAPPENED, saved directly as done. */
type Mode = "public" | "customer" | "manager" | "manager-log";

/** One vehicle line as the customer builds it: type, how many, which
 *  base service (group key) and which add-ons. */
interface Draft {
  typeId: string;
  count: number;
  base: string | null;
  addons: string[];
}
const EMPTY_DRAFT: Draft = { typeId: "", count: 1, base: null, addons: [] };

type Coverage = "idle" | "checking" | "covered" | "uncovered";

const STEPS = ["What Are We Washing?", "Where And When?"];
const LOG_STEPS = ["What Was Washed?", "Who, Where And When?"];
const LAUNCH_FREE_BIKE_OFFER = "free-bike-wash";
const LAUNCH_FREE_BIKE_COUPON = "FREEBIKE";

function priceFor(s: Service, vt: string): number {
  return s.vehicle_type_prices?.[vt] ?? s.price;
}
function firstWashPriceFor(s: Service, vt: string): number | null {
  return s.vehicle_type_discounted_prices?.[vt] ?? s.discounted_price ?? null;
}

export function QuickBookFlow({ mode }: { mode: Mode }) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { user } = useAuth();
  const { push: pushToast } = useToast();
  const queryClient = useQueryClient();
  const isCustomer = mode === "customer";
  const isLog = mode === "manager-log";
  const isManager = mode === "manager" || isLog;
  const launchOfferActive = !isLog && searchParams.get("offer") === LAUNCH_FREE_BIKE_OFFER;
  // Guests are (almost always) first-time customers — quote the first-wash
  // price where one exists. The backend re-checks eligibility by phone.
  const showFirstWash = mode === "public" && !user;

  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const { data: catalogue, isLoading: servicesLoading } = useQuery({
    queryKey: ["public-services"],
    queryFn: () => catalogApi.services({ page_size: 100 }),
  });
  const { data: policy } = useQuery({ queryKey: ["booking-policy"], queryFn: bookingPolicyApi.get, staleTime: 5 * 60 * 1000 });
  const { data: myAddresses } = useQuery({ queryKey: ["addresses"], queryFn: addressApi.list, enabled: isCustomer });

  const services = useMemo(() => (catalogue?.data || []).filter((s) => s.is_active !== false), [catalogue]);
  const types: VehicleTypeOption[] = useMemo(
    () => (vehicleTypes || []).filter((t) => t.is_active !== false).sort((a, b) => a.display_order - b.display_order),
    [vehicleTypes]
  );
  const bikeIds = useMemo(() => bikeTypeIds(types), [types]);
  const maxVehicles = policy?.max_vehicles_per_booking || 5;

  // ---- step 1 state ------------------------------------------------------
  const [step, setStep] = useState(0);
  // Vehicles already added to the visit, plus the one being edited now.
  const [added, setAdded] = useState<Draft[]>([]);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);

  // ---- step 2 state ------------------------------------------------------
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  // Manager modes only: set when an existing customer is picked from the
  // typeahead, so their active passes can be shown alongside the form
  // (booking creation itself already auto-matches a pass server-side).
  const [pickedCustomerId, setPickedCustomerId] = useState<string | null>(null);
  const [savedAddressId, setSavedAddressId] = useState<string | null>(null);
  const [pinned, setPinned] = useState<LocationValue | null>(null);
  const [mapsUp, setMapsUp] = useState(true);
  const [typedAddress, setTypedAddress] = useState(false); // manager: type instead of pin
  const [line1, setLine1] = useState("");
  const [pincode, setPincode] = useState("");
  const [coverage, setCoverage] = useState<Coverage>("idle");
  const [checkedPincode, setCheckedPincode] = useState("");
  const [centerId, setCenterId] = useState("");
  const [centerCity, setCenterCity] = useState("");
  const [centerState, setCenterState] = useState("");
  const [date, setDate] = useState(todayIST());
  const [slot, setSlot] = useState("");
  const [paymentMethod, setPaymentMethod] = useState<"cash" | "online">("cash");
  const [couponCode, setCouponCode] = useState("");
  const [moreOpen, setMoreOpen] = useState(false);
  const [notes, setNotes] = useState("");
  const [altName, setAltName] = useState("");
  const [altPhone, setAltPhone] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [otpOpen, setOtpOpen] = useState(false);
  // A MSG91 widget token stays reusable for a retry on the same number; a
  // classic OTP code is spent by the booking call, so it isn't kept.
  const [verified, setVerified] = useState<{ phone: string; token: string } | null>(null);
  const [otpError, setOtpError] = useState("");
  const [forceOtp, setForceOtp] = useState(false);
  // manager-log only: the clock time of the job, and the WhatsApp switch.
  const [logTime, setLogTime] = useState("");
  const [sendWhatsApp, setSendWhatsApp] = useState(true);
  const [discount, setDiscount] = useState("");
  const phoneRef = useRef<HTMLInputElement>(null);

  // ---- keep an unfinished booking for THIS browser tab ---------------------
  // Back button, a tap on the logo, a refresh — the customer comes back to
  // exactly where they were. sessionStorage on purpose: this tab only,
  // gone when it closes, never days-old state resurfacing.
  const storageKey = `blussit:quickbook:${mode}${launchOfferActive ? ":launch-free-bike" : ""}`;
  const repeatId = searchParams.get("repeat");
  const [restored, setRestored] = useState(false);
  const restoreAddressRef = useRef<null | { pinned: LocationValue | null; savedAddressId: string | null; pincode: string; typed: boolean; slot: string }>(null);
  const skipAutoDateRef = useRef(false);
  useEffect(() => {
    if (launchOfferActive) {
      setRestored(true);
      return;
    }
    if (repeatId) {
      setRestored(true);
      return;
    }
    try {
      const raw = sessionStorage.getItem(storageKey);
      const saved = raw ? JSON.parse(raw) : null;
      if (saved && Date.now() - (saved.at || 0) < 3 * 60 * 60 * 1000) {
        setStep(saved.step === 1 ? 1 : 0);
        setAdded(Array.isArray(saved.added) ? saved.added : []);
        setDraft(saved.draft && typeof saved.draft === "object" ? { ...EMPTY_DRAFT, ...saved.draft } : EMPTY_DRAFT);
        if (!isCustomer) {
          setName(saved.name || "");
          setPhone(saved.phone || "");
        }
        setSavedAddressId(saved.savedAddressId ?? null);
        setPinned(saved.pinned || null);
        setTypedAddress(!!saved.typedAddress);
        setLine1(saved.line1 || "");
        setPincode(saved.pincode || "");
        if (saved.date) setDate(saved.date);
        setSlot(saved.slot || "");
        setPaymentMethod(saved.paymentMethod === "online" ? "online" : "cash");
        setCouponCode(saved.couponCode || "");
        setNotes(saved.notes || "");
        setLogTime(typeof saved.logTime === "string" ? saved.logTime : "");
        setSendWhatsApp(saved.sendWhatsApp !== false);
        setDiscount(typeof saved.discount === "string" ? saved.discount : "");
        setAltName(saved.altName || "");
        setAltPhone(saved.altPhone || "");
        setMoreOpen(!!(saved.notes || saved.altName || saved.altPhone));
        restoreAddressRef.current = {
          pinned: saved.pinned || null,
          savedAddressId: saved.savedAddressId ?? null,
          pincode: saved.pincode || "",
          typed: !!saved.typedAddress,
          slot: saved.slot || "",
        };
        if (saved.slot) skipAutoDateRef.current = true;
      }
    } catch {
      // ignore a corrupt/blocked store — start fresh
    }
    setRestored(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!restored || repeatId) return;
    try {
      sessionStorage.setItem(
        storageKey,
        JSON.stringify({ at: Date.now(), step, added, draft, name, phone, savedAddressId, pinned, typedAddress, line1, pincode, date, slot, paymentMethod, couponCode, notes, altName, altPhone, logTime, sendWhatsApp, discount })
      );
    } catch {
      // storage unavailable — nothing to keep
    }
  }, [restored, repeatId, storageKey, step, added, draft, name, phone, savedAddressId, pinned, typedAddress, line1, pincode, date, slot, paymentMethod, couponCode, notes, altName, altPhone, logTime, sendWhatsApp, discount]);

  useEffect(() => {
    scrollToTopNow();
  }, [step]);

  // The address step needs Google Maps (config call + ~300 KB script + its
  // libraries). Start that while the customer is still choosing their
  // vehicle and service, so step 2 opens with the map already there instead
  // of a spinner. Loads once; a no-op when maps aren't configured.
  useEffect(() => {
    if (isLog) return;
    const timer = window.setTimeout(() => void ensureGoogleMaps(), 800);
    return () => window.clearTimeout(timer);
  }, [isLog]);

  // A signed-in customer books as themselves — name/phone come from the
  // account, and their default address is preselected.
  useEffect(() => {
    if (isCustomer && user) {
      setName(user.full_name || "");
      setPhone(user.phone || "");
    }
  }, [isCustomer, user]);
  useEffect(() => {
    if (!isCustomer || !myAddresses?.length || savedAddressId !== null) return;
    const def = myAddresses.find((a) => a.is_default) || myAddresses[0];
    void chooseSavedAddress(def);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isCustomer, myAddresses]);

  // "Book again" — replay a previous booking's vehicles/services/address.
  const { data: repeatBooking } = useQuery({
    queryKey: ["booking", repeatId],
    queryFn: () => bookingApi.get(repeatId!),
    enabled: isCustomer && !!repeatId,
  });
  const { data: repeatGroup } = useQuery({
    queryKey: ["booking-group", repeatBooking?.booking_group_id],
    queryFn: () => bookingApi.getGroup(repeatBooking!.booking_group_id!),
    enabled: isCustomer && !!repeatBooking?.booking_group_id,
  });
  useEffect(() => {
    if (!repeatBooking || !services.length || !types.length) return;
    if (repeatBooking.booking_group_id && !repeatGroup) return;
    const cars = repeatGroup || [repeatBooking];
    const drafts: Draft[] = [];
    for (const car of cars) {
      const vt = car.vehicle_type || car.vehicle_snapshot?.vehicle_type;
      if (!vt) continue;
      const svc = (car.service_ids || []).map((id) => services.find((s) => s.id === id)).filter(Boolean) as Service[];
      const base = svc.find((s) => !s.is_addon);
      if (!base) continue;
      const addons = svc.filter((s) => s.is_addon && !isBikeLine(s)).map((s) => s.id);
      const extraBikes = svc.filter((s) => s.is_addon && isBikeLine(s)).reduce((n, s) => n + (car.service_quantities?.[s.id] || 1), 0);
      const key = base.variant_group || base.id;
      // Identical cars collapse into one line with a higher count.
      const same = drafts.find((d) => d.typeId === vt && d.base === key && d.addons.join(",") === addons.join(","));
      if (same && !bikeIds.has(vt)) same.count += 1;
      else drafts.push({ typeId: vt, count: bikeIds.has(vt) ? variantCount(base) + extraBikes : 1, base: key, addons });
    }
    if (drafts.length) {
      setAdded(drafts);
      setDraft(EMPTY_DRAFT);
    }
    if (isCustomer && myAddresses?.length) {
      const addr = myAddresses.find((a) => a.id === repeatBooking.address_id);
      if (addr) void chooseSavedAddress(addr);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repeatBooking, repeatGroup, services, types, myAddresses]);

  function isBikeLine(s: Service): boolean {
    return !!s.is_addon && /bike|scooter|two.?wheeler/i.test(s.name) && !/polish/i.test(s.name);
  }

  // ---- lines ---------------------------------------------------------------
  const draftReady = !!draft.typeId && !!draft.base;
  // The visit = every added vehicle + the one being edited (once it's
  // complete) — "Continue" never forces an empty editor to be filled.
  const drafts: Draft[] = draftReady ? [...added, draft] : added;
  const totalVehicles = drafts.reduce((n, d) => n + d.count, 0);

  interface Line {
    draft: Draft;
    type: VehicleTypeOption;
    count: number;
    isBike: boolean;
    groups: BaseGroup[];
    base: Service | null;
    kit: ReturnType<typeof addonKit>;
    addons: Service[];
    subtotal: number;
    regularSubtotal: number;
    offerDiscount: number;
    freeBikeAddon: Service | null;
    payload: QuickBookingLine | null;
    services: Service[];
  }

  const unit = (s: Service, vt: string) => {
    const regular = priceFor(s, vt);
    if (!showFirstWash) return regular;
    const first = firstWashPriceFor(s, vt);
    return first != null && first < regular ? first : regular;
  };
  function isLaunchOfferBase(s: Service | null | undefined) {
    const name = `${s?.name || ""} ${s?.slug || ""}`.toLowerCase();
    return /star/.test(name) || (/deep/.test(name) && /clean/.test(name));
  }
  function launchOfferBaseKey(typeId: string) {
    const groups = baseGroups(services, typeId);
    return (
      groups.find((g) => /star/.test(`${g.label} ${g.primary.slug}`.toLowerCase())) ||
      groups.find((g) => {
        const name = `${g.label} ${g.primary.slug}`.toLowerCase();
        return /deep/.test(name) && /clean/.test(name);
      }) ||
      groups[0]
    )?.key ?? null;
  }

  const lineFor = (d: Draft): Line | null => {
    const t = types.find((x) => x.id === d.typeId);
    if (!t) return null;
    const count = d.count;
    const isBike = bikeIds.has(t.id);
    const groups = baseGroups(services, t.id);
    const c = d;
    const group = groups.find((g) => g.key === c.base) || null;
    return (() => {
        const kit = addonKit(services, t.id, bikeIds);
        // Bikes: the count IS the bike count — base wash + (count-1) extra
        // bikes on one booking (the catalogue's per-bike pricing), or the
        // exact variant when the group sells one for that count.
        let base: Service | null = null;
        let extraBikes = 0;
        if (group) {
          if (isBike) {
            const exact = group.variants.find((v) => variantCount(v) === count);
            if (exact) base = exact;
            else {
              base = group.primary;
              extraBikes = kit.addBike ? Math.max(0, count - variantCount(group.primary)) : 0;
            }
          } else {
            base = group.primary;
          }
        }
        const addons = c.addons.map((id) => services.find((s) => s.id === id)).filter(Boolean) as Service[];
        const requestedFreeBike = !isLog && !!(kit.addBike && addons.some((a) => a.id === kit.addBike?.id));
        const freeBikeAddon = (launchOfferActive || requestedFreeBike) && !isBike && base && isLaunchOfferBase(base) && kit.addBike ? kit.addBike : null;
        if (freeBikeAddon && !addons.some((a) => a.id === freeBikeAddon.id)) addons.push(freeBikeAddon);
        const perUnit = (s: Service) => unit(s, t.id);
        const perUnitRegular = (s: Service) => priceFor(s, t.id);
        let subtotal = 0;
        let regularSubtotal = 0;
        let offerDiscount = 0;
        const serviceIds: string[] = [];
        const quantities: Record<string, number> = {};
        if (base) {
          serviceIds.push(base.id);
          subtotal += perUnit(base);
          regularSubtotal += perUnitRegular(base);
          if (extraBikes > 0 && kit.addBike) {
            serviceIds.push(kit.addBike.id);
            quantities[kit.addBike.id] = extraBikes;
            subtotal += perUnit(kit.addBike) * extraBikes;
            regularSubtotal += perUnitRegular(kit.addBike) * extraBikes;
          }
          for (const a of addons) {
            serviceIds.push(a.id);
            // Bike polish is per bike; every other add-on is one per vehicle.
            const qty = isBike && kit.bikePolish && a.id === kit.bikePolish.id ? count : 1;
            if (qty > 1) quantities[a.id] = qty;
            subtotal += perUnit(a) * qty;
            regularSubtotal += perUnitRegular(a) * qty;
            if (freeBikeAddon && a.id === freeBikeAddon.id) offerDiscount += perUnit(a) * qty;
          }
        }
        // Cars: every car of this type is its own booking with the same service.
        const quantity = isBike ? 1 : count;
        const lineServices = base ? [base, ...(extraBikes > 0 && kit.addBike ? [kit.addBike] : []), ...addons] : [];
        return {
          draft: d,
          type: t,
          count,
          isBike,
          groups,
          base,
          kit,
          addons,
          subtotal: subtotal * quantity,
          regularSubtotal: regularSubtotal * quantity,
          offerDiscount: offerDiscount * quantity,
          freeBikeAddon,
          payload: base ? { vehicle_type: t.id, quantity, service_ids: serviceIds, service_quantities: quantities } : null,
          services: lineServices,
        };
    })();
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const lines: Line[] = useMemo(() => drafts.map(lineFor).filter(Boolean) as Line[], [drafts, types, services, bikeIds, showFirstWash]);
  // The editor's own line (may be incomplete — used for the chips/prices).
  const editing = draft.typeId ? lineFor({ ...draft, base: draft.base }) : null;
  const editingGroups = draft.typeId ? baseGroups(services, draft.typeId) : [];
  const launchOfferGroups = launchOfferActive ? editingGroups.filter((g) => isLaunchOfferBase(g.primary)) : [];
  const editingKit = draft.typeId ? addonKit(services, draft.typeId, bikeIds) : null;
  const editingIsBike = bikeIds.has(draft.typeId);

  const total = lines.reduce((n, l) => n + l.subtotal, 0);
  const regularTotal = lines.reduce((n, l) => n + l.regularSubtotal, 0);
  const offerDiscount = lines.reduce((n, l) => n + l.offerDiscount, 0);
  const displayTotal = Math.max(0, total - offerDiscount);
  // Log mode only: rupees the manager took off the bill. What the customer
  // actually paid (finalTotal) drives the footer, the payment choice and the save.
  const discountNum = isLog ? Math.round(Number(discount) || 0) : 0;
  const finalTotal = Math.max(0, displayTotal - discountNum);
  const allServices = lines.flatMap((l) => l.services);
  const step1Ready = lines.length > 0 && lines.every((l) => !!l.base) && (!draft.typeId || draftReady);

  const otherVehicles = added.reduce((n, d) => n + d.count, 0);
  const selectableTypes = launchOfferActive ? types.filter((t) => !bikeIds.has(t.id)) : types;
  const pickType = (typeId: string) => {
    // Default the service to the first one offered for this type so the
    // dropdown pick is already a bookable line.
    const first = launchOfferActive ? launchOfferBaseKey(typeId) : baseGroups(services, typeId)[0]?.key ?? null;
    setDraft({ typeId, count: 1, base: first, addons: [] });
  };
  const setCount = (next: number) => {
    const clamped = Math.max(1, Math.min(10, next));
    if (otherVehicles + clamped > maxVehicles) {
      pushToast({ tone: "error", title: `Up to ${maxVehicles} vehicles on one visit`, message: "Book the rest as a second visit." });
      return;
    }
    setDraft((d) => ({ ...d, count: clamped }));
  };
  const pickBase = (key: string) => setDraft((d) => ({ ...d, base: key, addons: [] }));
  const toggleAddon = (id: string) =>
    setDraft((d) => ({ ...d, addons: d.addons.includes(id) ? d.addons.filter((x) => x !== id) : [...d.addons, id] }));
  /** "Add another vehicle": bank the editor as a line and open a fresh one. */
  const addAnother = () => {
    if (!draftReady) return;
    if (otherVehicles + draft.count >= maxVehicles) {
      pushToast({ tone: "error", title: `Up to ${maxVehicles} vehicles on one visit`, message: "Book the rest as a second visit." });
      return;
    }
    setAdded((a) => [...a, draft]);
    setDraft(EMPTY_DRAFT);
    scrollToTopNow();
  };
  const removeAdded = (index: number) => setAdded((a) => a.filter((_, i) => i !== index));
  const editAdded = (index: number) => {
    const d = added[index];
    if (!d) return;
    // Anything half-typed in the editor is kept as its own line.
    setAdded((a) => [...a.filter((_, i) => i !== index), ...(draftReady ? [draft] : [])]);
    setDraft(d);
    scrollToTopNow();
  };

  // ---- address / coverage --------------------------------------------------
  const resetCoverage = () => {
    setCoverage("idle");
    setCheckedPincode("");
    setCenterId("");
    setSlot("");
  };

  async function chooseSavedAddress(a: Address) {
    setSavedAddressId(a.id);
    setPinned(null);
    setCoverage("checking");
    setCheckedPincode(a.pincode);
    setCenterId("");
    setSlot("");
    try {
      const result = await coverageApi.check({ latitude: a.latitude ?? undefined, longitude: a.longitude ?? undefined, pincode: a.pincode });
      if (result.covered && result.center) {
        setCenterId(result.center.id);
        setCenterCity(result.center.city || a.city);
        setCenterState(result.center.state || a.state);
        setCoverage("covered");
      } else {
        setCoverage("uncovered");
      }
    } catch {
      setCoverage("uncovered");
    }
  }

  async function onPin(v: LocationValue) {
    setPinned(v);
    if (v.pincode) setPincode(v.pincode);
    setCoverage("checking");
    // A reverse-geocode can come back without a postal code — the pin
    // alone decides coverage, so never invent a placeholder here.
    setCheckedPincode(v.pincode || "");
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
  }

  const latestPincode = useRef("");
  async function checkPincode(pin: string) {
    latestPincode.current = pin;
    setCoverage("checking");
    setCheckedPincode(pin);
    setCenterId("");
    setSlot("");
    try {
      const centers = await serviceCenterApi.lookupByPincode(pin);
      // The customer kept typing while this was in flight — a newer check owns the screen.
      if (latestPincode.current !== pin) return;
      if (centers.length) {
        setCenterId(centers[0].id);
        setCenterCity(centers[0].location.city);
        setCenterState(centers[0].location.state);
        setCoverage("covered");
      } else {
        setCoverage("uncovered");
      }
    } catch {
      if (latestPincode.current === pin) setCoverage("uncovered");
    }
  }

  // A day that's already fully booked (or past its cutoff — e.g. booking
  // at night) shouldn't greet the customer with a wall of "Fully booked":
  // move the date to the first day that actually has an open slot, once
  // per center, and leave any date the customer picks themselves alone.
  const [autoDatedFor, setAutoDatedFor] = useState("");
  useEffect(() => {
    if (coverage !== "covered" || !centerId || autoDatedFor === centerId) return;
    setAutoDatedFor(centerId);
    if (skipAutoDateRef.current) {
      skipAutoDateRef.current = false; // a restored booking keeps its own date/slot
      return;
    }
    let cancelled = false;
    (async () => {
      const start = new Date(`${todayIST()}T00:00:00`);
      const horizon = Math.max(1, Math.min(policy?.max_advance_days || 7, 14));
      for (let i = 0; i < horizon; i++) {
        const d = new Date(start);
        d.setDate(start.getDate() + i);
        // Local date parts — toISOString() would shift an IST midnight
        // back to the previous UTC day.
        const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
        try {
          // Same cache key the slot picker reads — the day we land on is then
          // already loaded when the picker draws it (no second request).
          const slots = await queryClient.fetchQuery({
            queryKey: ["available-slots", centerId, iso],
            queryFn: () => serviceCenterApi.availableSlots(centerId, iso),
            staleTime: 15_000,
          });
          if (slots.some((x) => x.status !== "full")) {
            if (!cancelled && iso !== date) {
              setDate(iso);
              setSlot("");
            }
            return;
          }
        } catch {
          return;
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coverage, centerId]);

  // A restored address needs its coverage/center re-resolved (that lives
  // in memory only) before the slot picker can show; the saved slot is
  // put back once that's done.
  useEffect(() => {
    const r = restoreAddressRef.current;
    if (!restored || !r) return;
    const finish = async (run: () => Promise<void>) => {
      restoreAddressRef.current = null;
      await run();
      if (r.slot) setSlot(r.slot);
    };
    if (r.pinned) {
      void finish(() => onPin(r.pinned!));
      return;
    }
    if (r.savedAddressId) {
      if (!myAddresses) return; // wait for the list
      const a = myAddresses.find((x) => x.id === r.savedAddressId);
      void finish(async () => {
        if (a) await chooseSavedAddress(a);
      });
      return;
    }
    if (r.typed && r.pincode.trim().length >= 6) {
      void finish(() => checkPincode(r.pincode.trim()));
      return;
    }
    restoreAddressRef.current = null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restored, myAddresses]);

  const usingPin = !savedAddressId && mapsUp && !typedAddress;
  const addressReady = savedAddressId ? true : usingPin ? !!pinned : line1.trim().length >= 3 && pincode.trim().length >= 6;
  const oldestLogDate = daysAgoIST(89);
  // Bound the 12-hour picker to the center's working hours (and, for today,
  // to now) so a job can't be filed at a time that can't be accepted; that
  // also keeps AM/PM honest — the list runs in chronological order.
  const { data: logCenter } = useQuery({
    queryKey: ["center-detail-for-log", user?.service_center_id],
    queryFn: () => adminServiceCenterApi.get(user!.service_center_id!),
    enabled: isLog && !!user?.service_center_id,
    staleTime: 5 * 60 * 1000,
  });
  const logOpens = logCenter?.working_hours_start;
  const logCloses = logCenter?.working_hours_end;
  const logLatest = logCloses && date === todayIST() && nowTimeIST() < logCloses ? nowTimeIST() : logCloses;
  const logRangeOk = !!logOpens && !!logLatest && logOpens < logLatest;
  const logTimeError = (): string => {
    if (!logTime) return "Enter the time of the job.";
    if (date === todayIST() && logTime > nowTimeIST()) return "That time hasn't happened yet — pick a time that has passed.";
    return "";
  };
  // The button stays live once the fields are filled; a time that hasn't
  // happened yet (or a date past the limit) is explained inline on tap.
  const logReady = name.trim().length >= 2 && !!validateIndianMobile(phone) && line1.trim().length >= 3 && !!date && date <= todayIST() && !!logTime;
  const step2Ready = isLog
    ? logReady
    : coverage === "covered" && !!date && !!slot && addressReady && name.trim().length >= 2 && !!validateIndianMobile(phone);

  const validateStep2 = (): boolean => {
    const next: Record<string, string> = {};
    if (isLog) {
      if (name.trim().length < 2) next.name = "Enter the customer's name.";
      if (!validateIndianMobile(phone)) next.phone = "Enter a valid 10-digit mobile number.";
      if (line1.trim().length < 3) next.address = "Enter where the job was done.";
      if (!date) next.date = "Choose the date.";
      else if (date > todayIST()) next.date = "Pick today or an earlier date.";
      else if (date < oldestLogDate) next.date = "Jobs older than 90 days can't be logged.";
      const timeError = logTimeError();
      if (timeError) next.time = timeError;
      if (discountNum > displayTotal) next.discount = `The discount can't be more than the bill (₹${displayTotal}).`;
      setFieldErrors(next);
      return Object.keys(next).length === 0;
    }
    if (name.trim().length < 2) next.name = "Enter the name.";
    if (!validateIndianMobile(phone)) next.phone = "Enter a valid 10-digit mobile number.";
    if (!savedAddressId && usingPin && !pinned) next.location = "Drop the pin on the service address.";
    if (!savedAddressId && !usingPin && line1.trim().length < 3) next.address = "Enter the address.";
    if (!savedAddressId && !usingPin && pincode.trim().length < 6) next.pincode = "Enter the pincode.";
    if (coverage !== "covered") next.location = next.location || "We need a serviceable address to continue.";
    if (!date) next.date = "Choose a date.";
    if (!slot) next.slot = "Choose a time slot.";
    if (couponCode.trim().length > 20) next.couponCode = "Coupon code can be up to 20 characters.";
    if (altPhone.trim() && !validateIndianMobile(altPhone)) next.altPhone = "Enter a valid 10-digit mobile number.";
    setFieldErrors(next);
    return Object.keys(next).length === 0;
  };

  // Anonymous website bookings prove the phone with an OTP as the last step;
  // signed-in customers already did (OTP login) and managers book on behalf.
  const needsOtp = !isManager && (!user || user.role !== "customer" || forceOtp);

  const submitLog = async () => {
    setError("");
    setSubmitting(true);
    try {
      const result = await bookingApi.managerLogCompleted({
        customer_name: name.trim(),
        customer_phone: validateIndianMobile(phone) || phone.trim(),
        lines: lines.map((l) => l.payload!).filter(Boolean),
        scheduled_date: date,
        service_time: logTime,
        address_line: line1.trim(),
        customer_notes: notes.trim() || undefined,
        payment_method: finalTotal > 0 ? paymentMethod : "cash",
        discount_amount: discountNum > 0 ? discountNum : undefined,
        send_whatsapp: sendWhatsApp,
      });
      try {
        sessionStorage.removeItem(storageKey);
      } catch {
        // ignore
      }
      queryClient.invalidateQueries({
        predicate: (q) => typeof q.queryKey[0] === "string" && /^(center-bookings|manager-kpi|center-captains)/.test(q.queryKey[0]),
      });
      pushToast({
        tone: "success",
        title: "Job logged as done",
        message: `${result.booking_numbers.join(" + ")} · ₹${result.total_amount}${sendWhatsApp ? " · customer notified on WhatsApp" : ""}`,
      });
      navigate("/manager/bookings");
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const submit = async (freshProof?: PhoneProof) => {
    if (!validateStep2()) return;
    if (isLog) {
      await submitLog();
      return;
    }
    const canonicalPhone = validateIndianMobile(phone) || phone.trim();
    const proof: PhoneProof | undefined = freshProof ?? (verified?.phone === canonicalPhone ? { phone_access_token: verified.token } : undefined);
    if (needsOtp && !proof) {
      setOtpError("");
      setOtpOpen(true);
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      const payload: QuickBookingPayload = {
        customer_name: name.trim(),
        customer_phone: canonicalPhone,
        ...(needsOtp ? proof : {}),
        lines: lines.map((l) => l.payload!).filter(Boolean),
        scheduled_date: date,
        scheduled_slot: slot,
        payment_method: displayTotal > 0 ? paymentMethod : "cash",
        coupon_code: launchOfferActive || offerDiscount > 0 ? LAUNCH_FREE_BIKE_COUPON : isManager && couponCode.trim() ? couponCode.trim().toUpperCase() : undefined,
        customer_notes: notes.trim() || undefined,
        alternate_contact_name: altName.trim() || undefined,
        alternate_contact_phone: altPhone.trim() ? validateIndianMobile(altPhone) || undefined : undefined,
        hold_key: getSlotHolderKey(),
      };
      if (savedAddressId) payload.address_id = savedAddressId;
      else
        payload.address = {
          line1: pinned ? pinned.formatted || [pinned.area, pinned.city].filter(Boolean).join(", ") : line1.trim(),
          city: pinned?.city || centerCity || undefined,
          state: pinned?.state || centerState || undefined,
          // Optional with a pin: the backend falls back to the covering
          // center's pincode when the pin's geocode had none.
          pincode: (pinned?.pincode || pincode.trim() || checkedPincode).trim() || undefined,
          latitude: pinned?.latitude ?? null,
          longitude: pinned?.longitude ?? null,
        };
      const result = isManager ? await bookingApi.managerQuick(payload) : await bookingApi.quick(payload);
      try {
        sessionStorage.removeItem(storageKey);
      } catch {
        // ignore
      }
      if (isManager) {
        pushToast({
          tone: "success",
          title: "Booking created",
          message: `${result.booking_numbers.join(" + ")} · service code ${result.service_code || "—"}`,
        });
        navigate("/manager/bookings");
        return;
      }
      navigate(`/thank-you?token=${result.confirmation_token}`, {
        state: {
          type: "booking",
          booking_number: result.booking_numbers.join(" + "),
          scheduled_date: date,
          scheduled_slot: slot,
          service_label: lines.map((l) => `${l.count > 1 ? `${l.count} × ` : ""}${l.type.name} · ${titleCase(l.base?.name)}${l.freeBikeAddon ? " + Free Bike Wash" : ""}`).join(" + "),
          service_code: result.service_code,
          payment_link: result.payment_link,
          awaiting_payment: result.awaiting_payment,
          total_amount: result.total_amount,
        },
      });
    } catch (err) {
      const message = getErrorMessage(err);
      // The backend rejected the code (wrong/expired) — back to the popup.
      // A signed-in customer whose session lapsed mid-wizard reaches the
      // server as a guest — same answer: verify the number, then retry.
      if (!isManager && /^(Invalid or expired code|Please verify your mobile number)/.test(message)) {
        setForceOtp(true);
        setVerified(null);
        setError("");
        setOtpError(message);
        setOtpOpen(true);
      } else {
        setError(message);
      }
    } finally {
      setSubmitting(false);
    }
  };

  // ---- render --------------------------------------------------------------
  const summary = lines.map((l) => `${l.count} ${l.type.name}${l.base ? ` · ${titleCase(l.base.name)}` : ""}`).join("  +  ");
  const lineLabel = (l: Line) => `${l.count > 1 ? `${l.count} × ` : ""}${l.type.name}${l.base ? ` · ${titleCase(l.base.name)}` : ""}`;

  const footer = (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <span className="min-w-0 flex-1 truncate text-xs text-gray-500">{summary || "Pick your vehicles"}</span>
        <span className="text-right">
          {showFirstWash && regularTotal > total && <span className="mr-1.5 text-xs text-gray-400 line-through">₹{regularTotal}</span>}
          {offerDiscount > 0 && <span className="mr-1.5 text-xs font-bold text-[#E11D48]">FREE bike -₹{offerDiscount}</span>}
          {discountNum > 0 && discountNum <= displayTotal && (
            <span className="mr-1.5 text-xs text-gray-400">
              <span className="line-through">₹{displayTotal}</span> <span className="font-bold text-[#E11D48]">-₹{discountNum}</span>
            </span>
          )}
          <span className="font-mono-num text-lg font-bold text-black">₹{isLog ? finalTotal : displayTotal}</span>
        </span>
      </div>
      {!isLog && (
        <p className="flex items-center gap-1.5 text-xs font-medium text-gray-600">
          <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-black" aria-hidden="true" />
          Price shown is final — no extra or hidden charges.
        </p>
      )}
      {error && <p className="text-right text-xs font-medium text-[var(--color-error)]">{error}</p>}
      <div className="flex items-center justify-between gap-3">
        <Button variant="outline" onClick={() => (step > 0 ? setStep(0) : navigate(-1))}>
          Back
        </Button>
        {step === 0 ? (
          <Button className="min-w-[150px]" disabled={!step1Ready} onClick={() => setStep(1)}>
            Continue
          </Button>
        ) : (
          <Button className="min-w-[150px]" disabled={!step2Ready} isLoading={submitting} onClick={() => void submit()}>
            <CheckCircle2 className="h-4 w-4" />
            {isLog ? "Save As Done" : displayTotal > 0 && paymentMethod === "online" ? "Book And Pay" : "Book Now"}
          </Button>
        )}
      </div>
      {step === 0 && !step1Ready && (draft.typeId || added.length > 0) && (
        <p className="text-right text-xs text-[var(--color-error)]">Pick a service for this vehicle to continue.</p>
      )}
    </div>
  );

  return (
    <>
    {needsOtp && (
      <BookingOtpModal
        open={otpOpen}
        phone={validateIndianMobile(phone) || phone.trim()}
        initialError={otpError}
        onClose={() => setOtpOpen(false)}
        onEditNumber={() => {
          setOtpOpen(false);
          setTimeout(() => phoneRef.current?.focus(), 0);
        }}
        onVerified={(proof) => {
          if (proof.phone_access_token) setVerified({ phone: validateIndianMobile(phone) || phone.trim(), token: proof.phone_access_token });
          setOtpOpen(false);
          void submit(proof);
        }}
      />
    )}
    <WizardShell
      eyebrow={isLog ? "Log a job you did" : isManager ? "Book for a customer" : "Book a wash"}
      title={isLog ? "Log A Completed Job" : isManager ? "New Booking" : "Book Your Doorstep Wash"}
      steps={isLog ? LOG_STEPS : STEPS}
      current={step}
      onStepClick={(i) => i < step && setStep(i)}
      footer={footer}
    >
      {/* ---------------- STEP 1 ---------------- */}
      {step === 0 && (
        <div className="space-y-6">
          <WizardStepHeader
            title="What Are We Washing?"
            description={
              launchOfferActive
                ? "Your launch offer is ready: choose your car type, pick Star Wash or Deep Cleaning, and one Bike Wash is added free."
                : isLog
                  ? `Pick the vehicle and the service you did. Up to ${maxVehicles} vehicles on one visit.`
                  : `Pick the vehicle and the service. Up to ${maxVehicles} vehicles on one visit — one address, one slot.`
            }
          />

          {launchOfferActive && (
            <div className="overflow-hidden rounded-2xl border border-[#F3E5B5] bg-[#111] text-white shadow-[0_16px_38px_rgba(0,0,0,0.18)]">
              <div className="grid gap-0 sm:grid-cols-[1fr_auto] sm:items-center">
                <div className="p-4 sm:p-5">
                  <p className="text-xs font-bold uppercase tracking-wide text-[#FACC15]">Launch offer claimed</p>
                  <h3 className="mt-1 text-xl font-black">Star Wash Or Deep Cleaning + Bike Wash</h3>
                  <p className="mt-1 text-sm text-white/70">Select your car type and service. We add one Bike Wash free to this booking.</p>
                </div>
                <div className="flex items-center gap-2 border-t border-white/10 bg-white/[0.06] p-4 sm:border-l sm:border-t-0">
                  <span className="rounded-full bg-[#E11D48] px-3 py-1 text-xs font-black uppercase tracking-wide text-white">Bike FREE</span>
                  <span className="text-xs font-semibold text-white/70">Till 24 Sep</span>
                </div>
              </div>
            </div>
          )}

          {/* Vehicles already on the visit */}
          {added.length > 0 && (
            <div className="rounded-xl border border-[#F3E5B5] bg-[#FFFCF0] p-3.5">
              <p className="text-sm font-semibold text-black">On this visit ({totalVehicles} of {maxVehicles})</p>
              <div className="mt-2 space-y-1.5">
                {added.map((d, i) => {
                  const l = lineFor(d);
                  if (!l) return null;
                  return (
                    <div key={`${d.typeId}-${i}`} className="flex items-center gap-2 text-sm">
                      <span className="min-w-0 flex-1 truncate text-gray-700">
                        <span className="font-medium text-black">{lineLabel(l)}</span>
                        {l.addons.length ? ` + ${l.addons.map((x) => titleCase(x.name)).join(", ")}` : ""}
                      </span>
                      <span className="font-mono-num shrink-0 text-gray-600">₹{l.subtotal}</span>
                      <button type="button" onClick={() => editAdded(i)} className="shrink-0 text-xs font-bold text-gray-500 underline underline-offset-2 hover:text-black">
                        Edit
                      </button>
                      <button type="button" onClick={() => removeAdded(i)} aria-label="Remove this vehicle" className="shrink-0 text-gray-400 hover:text-[var(--color-error)]">
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* The editor: one vehicle at a time */}
          <div className="space-y-4">
            <p className="flex items-center gap-1.5 text-sm font-medium text-black">
              <Car className="h-3.5 w-3.5" />
              {added.length ? `Vehicle ${added.length + 1}` : "Your vehicle"}
              {added.length > 0 && !draft.typeId && <span className="text-xs font-normal text-gray-500">(optional — or just continue)</span>}
            </p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
              <Select label="Vehicle type" value={draft.typeId} onChange={(e) => pickType(e.target.value)}>
                <option value="">Select vehicle type…</option>
                {selectableTypes.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </Select>
              {draft.typeId && !launchOfferActive && (
                <div className="flex items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-3.5 py-2.5 sm:h-[46px]">
                  <span className="text-sm text-gray-600">How many?</span>
                  <QtyStepper value={draft.count} min={1} max={10} onChange={setCount} />
                </div>
              )}
            </div>

            {draft.typeId && editing && (
              <div className="space-y-3 rounded-2xl border border-gray-200 bg-white p-4">
                {servicesLoading ? (
                  <p className="text-sm text-gray-500">Loading services…</p>
                ) : editingGroups.length === 0 ? (
                  <p className="text-sm text-gray-500">No services are offered for this vehicle type yet.</p>
                ) : (
                  launchOfferActive ? (
                    <div className="space-y-3">
                      <div>
                        <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-gray-500">Choose Offer Service</p>
                        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                          {(launchOfferGroups.length ? launchOfferGroups : editingGroups).map((g) => {
                            const selected = draft.base === g.key;
                            const shown = unit(g.primary, draft.typeId);
                            return (
                              <button
                                key={g.key}
                                type="button"
                                onClick={() => pickBase(g.key)}
                                aria-pressed={selected}
                                className={`rounded-xl border-2 px-3.5 py-3 text-left transition-colors ${
                                  selected ? "border-black bg-[#FFF4CD]" : "border-gray-200 bg-white hover:border-gray-400"
                                }`}
                              >
                                <span className="flex items-center justify-between gap-3">
                                  <span className="text-sm font-semibold text-black">{titleCase(g.label)}</span>
                                  <span className="font-mono-num shrink-0 text-sm font-bold text-black">₹{shown}</span>
                                </span>
                                <span className="mt-1 block text-xs text-gray-600">Includes one Bike Wash free with this launch offer.</span>
                              </button>
                            );
                          })}
                        </div>
                      </div>

                      <div className="flex items-center justify-between gap-3 rounded-xl border border-[#F3E5B5] bg-white px-3.5 py-3">
                        <div>
                          <p className="text-sm font-semibold text-black">Bike Wash</p>
                          <p className="mt-0.5 text-xs text-gray-600">Added automatically with your selected offer service.</p>
                        </div>
                        <span className="rounded-full bg-[#E11D48] px-2.5 py-1 text-[11px] font-black uppercase tracking-wide text-white">Free</span>
                      </div>

                      {editing.offerDiscount > 0 && (
                        <p className="text-sm text-gray-700">
                          Offer discount: <span className="font-mono-num font-semibold text-[#E11D48]">-₹{editing.offerDiscount}</span>
                        </p>
                      )}
                    </div>
                  ) : (
                  <>
                    <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                      Service {draft.count > 1 ? `(same for all ${draft.count})` : ""}
                    </p>
                    {/* Every option shows exactly what it includes, so the
                        customer compares before tapping — not after. */}
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      {editingGroups.map((g) => {
                        const selected = draft.base === g.key;
                        const shown = unit(g.primary, draft.typeId);
                        const regular = priceFor(g.primary, draft.typeId);
                        const inc = parseIncludes(g.primary.description);
                        const items = inc.items.length ? inc.items.map(titleCase) : inc.summary ? [inc.summary] : [];
                        return (
                          <button
                            key={g.key}
                            type="button"
                            onClick={() => pickBase(g.key)}
                            aria-pressed={selected}
                            className={`rounded-xl border-2 px-3.5 py-3 text-left transition-colors ${
                              selected ? "border-black bg-[#FFF4CD]" : "border-gray-200 bg-white hover:border-gray-400"
                            }`}
                          >
                            <span className="flex items-center justify-between gap-3">
                              <span className="text-sm font-semibold text-black">{titleCase(g.label)}</span>
                              <span className="font-mono-num shrink-0 text-sm font-bold text-black">
                                ₹{shown}
                                {showFirstWash && shown < regular && <span className="ml-1 text-[10px] font-normal text-gray-500">first wash</span>}
                              </span>
                            </span>
                            {items.length > 0 && (
                              <ul className="mt-1.5 space-y-0.5">
                                {items.map((it) => (
                                  <li key={it} className="flex items-start gap-1.5 text-xs text-gray-600">
                                    <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-black" />
                                    <span>{it}</span>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </button>
                        );
                      })}
                    </div>

                    {editing.base && editingKit && (editingKit.simple.length > 0 || (editingIsBike && editingKit.bikePolish)) && (
                      <div>
                        <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-gray-500">Add-ons</p>
                        <div className="flex flex-wrap gap-2">
                          {[...editingKit.simple, ...(editingIsBike && editingKit.bikePolish ? [editingKit.bikePolish] : [])].map((a) => {
                            const on = draft.addons.includes(a.id);
                            const per = unit(a, draft.typeId);
                            const perBike = editingIsBike && editingKit.bikePolish && a.id === editingKit.bikePolish.id;
                            return (
                              <button
                                key={a.id}
                                type="button"
                                onClick={() => toggleAddon(a.id)}
                                className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
                                  on ? "border-black bg-[#FFF4CD] text-black" : "border-gray-300 bg-white text-gray-600 hover:border-black"
                                }`}
                              >
                                + {titleCase(a.name)} · ₹{per}
                                {perBike ? "/bike" : ""}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}

                    {!isLog && editing.base && !editingIsBike && editingKit?.addBike && isLaunchOfferBase(editing.base) && (
                      <div className="rounded-xl border border-[#F3E5B5] bg-[#FFFCF0] p-3">
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                          <div>
                            <p className="text-sm font-semibold text-black">Add Free Bike Wash</p>
                            <p className="mt-0.5 text-xs text-gray-600">Optional launch offer with {titleCase(editing.base.name)}.</p>
                          </div>
                          <button
                            type="button"
                            onClick={() => toggleAddon(editingKit.addBike!.id)}
                            className={`inline-flex shrink-0 items-center justify-center gap-2 rounded-full border px-3.5 py-2 text-xs font-black uppercase tracking-wide transition-colors ${
                              draft.addons.includes(editingKit.addBike.id)
                                ? "border-[#E11D48] bg-[#E11D48] text-white"
                                : "border-[#E11D48]/30 bg-white text-[#E11D48] hover:border-[#E11D48]"
                            }`}
                          >
                            {draft.addons.includes(editingKit.addBike.id) ? "Free Bike Added" : "Add Free Bike"}
                          </button>
                        </div>
                      </div>
                    )}

                    {editing.base && (
                      <p className="text-sm text-gray-700">
                        {lineLabel(editing)}:{" "}
                        {editing.offerDiscount > 0 && <span className="mr-1.5 text-xs font-bold text-[#E11D48]">FREE bike -₹{editing.offerDiscount}</span>}
                        <span className="font-mono-num font-semibold text-black">₹{editing.subtotal - editing.offerDiscount}</span>
                      </p>
                    )}
                  </>
                  )
                )}
              </div>
            )}

            {/* A different type on the same visit */}
            {!launchOfferActive && totalVehicles < maxVehicles && (
              <div>
                <Button type="button" variant="outline" className="w-full" disabled={!draftReady} onClick={addAnother}>
                  <Plus className="h-4 w-4" /> Add another vehicle
                </Button>
                <p className="mt-1.5 text-center text-xs text-gray-500">
                  {draftReady ? `Up to ${maxVehicles} vehicles washed on one visit, at one address.` : "Pick this vehicle's service first."}
                </p>
              </div>
            )}
            {!types.length && <p className="text-sm text-gray-500">Loading vehicle types…</p>}
          </div>
        </div>
      )}

      {/* ---------------- STEP 2 ---------------- */}
      {step === 1 && (
        <div className="space-y-6">
          <WizardStepHeader
            title={isLog ? "Who, Where And When?" : "Where And When?"}
            description={isLog ? "Enter the job as it happened. It is saved as done — no captain or photos needed." : "The captain drives to the pin you drop. Pick a slot that suits you."}
          />

          {/* Who — a signed-in customer books as themselves, unless the
              account has no phone yet (Google sign-in) and we still need one
              for the captain and the WhatsApp updates. */}
          {isCustomer && user && user.phone ? (
            <div className="rounded-xl border border-[#F3E5B5] bg-[#FAFAFA] px-4 py-3 text-sm">
              Booking as <span className="font-semibold text-black">{user.full_name}</span>
              {user.phone ? <span className="text-gray-500"> · {user.phone}</span> : null}
            </div>
          ) : isManager ? (
            <div className="space-y-3">
              <CustomerNamePhoneFields
                name={name}
                phone={phone}
                onChangeName={(v) => {
                  setName(v);
                  setPickedCustomerId(null);
                }}
                onChangePhone={(v) => {
                  setPhone(v);
                  setPickedCustomerId(null);
                }}
                onPick={(c) => setPickedCustomerId(c.id)}
                nameError={fieldErrors.name}
                phoneError={fieldErrors.phone}
                phoneInputRef={phoneRef}
              />
              {pickedCustomerId && <CustomerActivePasses customerId={pickedCustomerId} />}
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Input label="Your name" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} error={fieldErrors.name} placeholder="E.g. Rahul Sharma" />
              <Input
                ref={phoneRef}
                label="Mobile number"
                value={phone}
                inputMode="numeric"
                onChange={(e) => setPhone(cleanMobileInput(e.target.value))}
                error={fieldErrors.phone}
                placeholder="10-digit mobile"
                hint="Your booking updates and service code come here on WhatsApp."
              />
            </div>
          )}

          {isLog && (
            <div className="space-y-6">
              <div className="space-y-3">
                <p className="flex items-center gap-1.5 text-sm font-medium text-black">
                  <MapPin className="h-3.5 w-3.5" /> Where was it done?
                </p>
                <Input label="Address / area" maxLength={300} value={line1} onChange={(e) => setLine1(e.target.value)} error={fieldErrors.address} placeholder="House / flat, street, area" />
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Input type="date" label="Date" value={date} min={oldestLogDate} max={todayIST()} onChange={(e) => setDate(e.target.value)} error={fieldErrors.date} />
                <Input type="time" label="Time" min={logRangeOk ? logOpens : undefined} max={logRangeOk ? logLatest : undefined} value={logTime} onChange={(e) => setLogTime(e.target.value)} error={fieldErrors.time} />
              </div>

              <Input
                label="Discount given (₹, optional)"
                inputMode="numeric"
                value={discount}
                onChange={(e) => setDiscount(e.target.value.replace(/\D/g, "").slice(0, 6))}
                placeholder="0"
                error={fieldErrors.discount}
                hint={
                  discountNum > 0 && discountNum <= displayTotal
                    ? `Customer pays ₹${finalTotal} instead of ₹${displayTotal}.`
                    : "Only if you gave this customer a discount — it comes off the total."
                }
              />

              {finalTotal > 0 && (
                <div>
                  <p className="mb-2 text-sm font-medium text-black">How was it paid?</p>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    {(
                      [
                        { id: "cash", icon: Banknote, title: "Cash", sub: "Collected by you" },
                        { id: "online", icon: CreditCard, title: "Online / UPI", sub: "Paid to the business account" },
                      ] as const
                    ).map((opt) => (
                      <button
                        key={opt.id}
                        type="button"
                        onClick={() => setPaymentMethod(opt.id)}
                        className={`flex items-start gap-3 rounded-xl border-2 p-3 text-left ${paymentMethod === opt.id ? "border-black bg-[#FFF4CD]" : "border-gray-200 bg-white"}`}
                      >
                        <opt.icon className="mt-0.5 h-4 w-4 shrink-0 text-black" />
                        <span>
                          <span className="block text-sm font-semibold text-black">{opt.title}</span>
                          <span className="block text-xs text-gray-500">{opt.sub}</span>
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <Input label="Note (optional)" maxLength={500} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Anything worth remembering about this job" />

              <Switch
                checked={sendWhatsApp}
                onChange={setSendWhatsApp}
                label="Tell the customer on WhatsApp"
                description={`They get one message${validateIndianMobile(phone) ? ` on +91 ${validateIndianMobile(phone)}` : ""} saying the service is done. Nothing else is sent.`}
              />
            </div>
          )}

          {!isLog && (
          <>
          {/* Where */}
          <div className="space-y-3">
            <p className="flex items-center gap-1.5 text-sm font-medium text-black">
              <MapPin className="h-3.5 w-3.5" /> Service address
            </p>
            {isCustomer && !!myAddresses?.length && (
              <div className="flex flex-wrap gap-2">
                {myAddresses.map((a) => (
                  <button
                    key={a.id}
                    type="button"
                    onClick={() => void chooseSavedAddress(a)}
                    className={`max-w-full rounded-xl border-2 px-3.5 py-2 text-left text-sm ${
                      savedAddressId === a.id ? "border-black bg-[#FFF4CD]" : "border-gray-200 bg-white"
                    }`}
                  >
                    <span className="block font-semibold text-black">{a.label}</span>
                    <span className="block truncate text-xs text-gray-500">{a.line1} · {a.pincode}</span>
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() => {
                    setSavedAddressId("");
                    setPinned(null);
                    resetCoverage();
                  }}
                  className={`rounded-xl border-2 px-3.5 py-2 text-sm ${savedAddressId === "" ? "border-black bg-[#FFF4CD]" : "border-dashed border-gray-300 text-gray-600"}`}
                >
                  + New address
                </button>
              </div>
            )}

            {!savedAddressId && usingPin && <LocationPicker value={pinned} onUnavailable={() => setMapsUp(false)} onChange={(v) => void onPin(v)} />}
            {!savedAddressId && (isManager || !mapsUp) && (
              <div className="space-y-3">
                {isManager && mapsUp && (
                  <button
                    type="button"
                    onClick={() => {
                      setTypedAddress((v) => !v);
                      setPinned(null);
                      resetCoverage();
                    }}
                    className="text-xs font-semibold text-black underline underline-offset-2"
                  >
                    {typedAddress ? "Pin it on the map instead" : "Can't pin it? Type the address instead"}
                  </button>
                )}
                {!usingPin && (
                  <>
                    <Input
                      label="Address"
                      value={line1}
                      onChange={(e) => setLine1(e.target.value)}
                      error={fieldErrors.address}
                      placeholder="House / flat, street, area"
                      hint={!mapsUp ? "Maps are unavailable right now — type the address instead." : undefined}
                    />
                    <Input
                      label="Pincode"
                      value={pincode}
                      maxLength={10}
                      inputMode="numeric"
                      onChange={(e) => {
                        const value = e.target.value;
                        setPincode(value);
                        setCoverage("idle");
                        latestPincode.current = "";
                        // A full pincode is checked the moment it is typed — no
                        // need to tap away first; the slots open right under it.
                        if (/^\d{6}$/.test(value.trim())) void checkPincode(value.trim());
                      }}
                      onBlur={() => pincode.trim().length >= 6 && checkedPincode !== pincode.trim() && void checkPincode(pincode.trim())}
                      error={fieldErrors.pincode}
                      placeholder="E.g. 452001"
                    />
                  </>
                )}
              </div>
            )}

            {coverage === "checking" && (
              <p className="flex items-center gap-1.5 text-xs text-gray-500">
                <Spinner className="h-3.5 w-3.5" /> Checking coverage…
              </p>
            )}
            {coverage === "covered" && (
              <div className="rounded-xl border border-[#F3E5B5] bg-[#FAFAFA] p-3">
                <p className="flex items-center gap-1 text-xs font-medium text-[var(--color-success)]">
                  <BadgeCheck className="h-3.5 w-3.5" /> We serve this area
                </p>
                {pinned && (
                  <p className="mt-1 text-xs text-gray-500">
                    <span className="font-medium text-black">{pinned.formatted || [pinned.area, pinned.city].filter(Boolean).join(", ")}</span> — drag the pin if this isn't your exact gate.
                  </p>
                )}
              </div>
            )}
            {coverage === "uncovered" && (
              <CoverageLeadInline pincode={checkedPincode} prefillName={name} prefillPhone={phone} serviceInterest={allServices.map((s) => s.name).join(", ") || undefined} />
            )}
            {fieldErrors.location && <p className="text-xs font-medium text-[var(--color-error)]">{fieldErrors.location}</p>}
          </div>

          {/* When */}
          {coverage === "covered" && (
            <div className="space-y-2">
              <SlotPicker serviceCenterId={centerId} date={date} onDateChange={setDate} value={slot} onChange={setSlot} enableHold />
              {(fieldErrors.date || fieldErrors.slot) && <p className="text-xs font-medium text-[var(--color-error)]">{fieldErrors.date || fieldErrors.slot}</p>}
            </div>
          )}

          {/* How to pay */}
          {displayTotal > 0 && (
            <div>
              <p className="mb-2 text-sm font-medium text-black">How would you like to pay?</p>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {(
                  [
                    { id: "cash", icon: Banknote, title: "Pay at your doorstep", sub: "Cash or UPI to the captain after the wash" },
                    { id: "online", icon: CreditCard, title: "Pay online now", sub: "UPI, cards, netbanking — confirms instantly" },
                  ] as const
                ).map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() => setPaymentMethod(opt.id)}
                    className={`flex items-start gap-3 rounded-xl border-2 p-3 text-left ${paymentMethod === opt.id ? "border-black bg-[#FFF4CD]" : "border-gray-200 bg-white"}`}
                  >
                    <opt.icon className="mt-0.5 h-4 w-4 shrink-0 text-black" />
                    <span>
                      <span className="block text-sm font-semibold text-black">{opt.title}</span>
                      <span className="block text-xs text-gray-500">{opt.sub}</span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {isManager && displayTotal > 0 && (
            <div className="max-w-sm">
              <Input
                label="Coupon code"
                value={couponCode}
                maxLength={20}
                onChange={(e) => setCouponCode(e.target.value.toUpperCase().replace(/\s/g, ""))}
                error={fieldErrors.couponCode}
                placeholder="Optional"
                hint="Applied by the server when the booking is created."
              />
            </div>
          )}

          {/* Optional extras */}
          <div>
            <button type="button" onClick={() => setMoreOpen((v) => !v)} className="text-xs font-semibold text-gray-600 underline underline-offset-2">
              {moreOpen ? "Hide extra details" : "Add a note or a second contact (optional)"}
            </button>
            {moreOpen && (
              <div className="mt-3 space-y-3">
                <Input label="Note for the captain" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="E.g. car is in the basement parking, gate B" />
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Input label="Second contact name" value={altName} onChange={(e) => setAltName(e.target.value)} />
                  <Input
                    label="Second contact number"
                    value={altPhone}
                    inputMode="numeric"
                    onChange={(e) => setAltPhone(cleanMobileInput(e.target.value))}
                    error={fieldErrors.altPhone}
                  />
                </div>
              </div>
            )}
          </div>

          <ServicePrepNotice services={allServices} />
          </>
          )}
        </div>
      )}
    </WizardShell>
    </>
  );
}
