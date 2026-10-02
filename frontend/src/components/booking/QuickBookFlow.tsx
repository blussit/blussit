import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Calendar, BadgeCheck, Banknote, CheckCircle2, Copy, CreditCard, Gift, Info, MapPin, Plus, Trash2, ShieldCheck, Leaf, Clock } from "lucide-react";
import { bookingPolicyApi, catalogApi, coverageApi, serviceCenterApi, vehicleTypeApi, getSlotHolderKey } from "../../api/catalog";
import { bookingApi, type BookingQuotePayload, type PhoneProof, type QuickBookingLine, type QuickBookingPayload } from "../../api/booking";
import { addressApi } from "../../api/profile";
import { adminServiceCenterApi } from "../../api/admin";
import { Button, DiscountBadge, Input, Modal, OfferTag, Select, Spinner, Switch, discountPercent } from "../ui";
import { SlotPicker } from "../shared/SlotPicker";
import { LocationPicker, type LocationValue } from "../shared/LocationPicker";
import { WizardShell, WizardStepHeader } from "../shared/WizardShell";
import { ServicePrepNotice } from "../shared/ServicePrepNotice";
import { QtyStepper } from "../shared/QtyStepper";
import { CustomerNamePhoneFields } from "../shared/CustomerNamePhoneFields";
import { BookingOtpModal } from "./BookingOtpModal";
import { CoverageLeadInline } from "../public/CoverageLeadInline";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { scrollToTopNow } from "../../lib/scroll";
import { ensureGoogleMaps } from "../../lib/googleMaps";
import { daysAgoIST, nowTimeIST, todayIST } from "../../lib/date";
import { validateIndianMobile, cleanMobileInput } from "../../lib/validators";
import { addonKit, baseGroups, bikeTypeIds, eligibleFor, variantCount, type BaseGroup } from "../../lib/serviceMix";
import { parseIncludes, priceForType, titleCase } from "../public/landing/shared";
import { subscriptionApi } from "../../api/engagement";
import type { Address, Service, TravelQuote, UserSubscription, VehicleTypeOption } from "../../types";

/**
 * THE booking flow (2026-09 quick-booking model) — two steps, no account;
 * anonymous bookings end with a one-time-code popup (BookingOtpModal):
 *      how to pay → Book (→ verify the number with an OTP if not signed in).
 *
 * One component, three seats:
 *   - "public":   anyone on /book — a profile is created silently from the
 *                 phone; login stays optional (OTP) for viewing history.
 *   - "customer": the same flow on /app/book, with name/phone/saved
 *                 addresses prefilled and a "use my plan" switch for every
 *                 vehicle a live plan covers (on by default).
 *   - "manager":  the same flow on a customer's behalf (phone-in bookings);
 *                 picking an existing customer brings their saved
 *                 addresses and plans.
 *
 * Deep links: ?service=<slug|id> preselects a service, ?subscription=<id>
 * (customer) preselects that plan's vehicle type + service.
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

const SECTION_LABEL = "mb-2 text-sm font-medium text-gray-600";

/** Every pickable box and chip: light tint + black border when chosen. */
function choiceClass(on: boolean): string {
  return `border-2 transition-colors ${on ? "border-black bg-[#FFF4CD] text-black" : "border-[#F3E5B5] bg-white text-gray-700 hover:border-gray-400"}`;
}

/** The latest value once it has stopped changing for `ms` — a string, so an
 *  equal payload rebuilt on every render never restarts the timer. */
function useSettled(value: string, ms: number): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

function priceFor(s: Service, vt: string): number {
  return s.vehicle_type_prices?.[vt] ?? s.price;
}
function firstWashPriceFor(s: Service, vt: string): number | null {
  return s.vehicle_type_discounted_prices?.[vt] ?? s.discounted_price ?? null;
}

export function QuickBookFlow({ mode }: { mode: Mode }) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { user } = useAuth();
  const { push: pushToast } = useToast();
  const queryClient = useQueryClient();
  const isCustomer = mode === "customer";
  const isLog = mode === "manager-log";
  const isManager = mode === "manager" || isLog;
  // Whether the first-wash price applies is the server's call (by phone,
  // platform-wide) — it arrives with the quote below. Until then a guest is
  // assumed new and everyone else isn't.
  const [firstWashKnown, setFirstWashKnown] = useState<boolean | null>(null);
  const showFirstWash = firstWashKnown ?? (mode === "public" && !user);

  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const { data: catalogue, isLoading: servicesLoading } = useQuery({
    queryKey: ["public-services"],
    queryFn: () => catalogApi.services({ page_size: 100 }),
  });
  const { data: policy } = useQuery({ queryKey: ["booking-policy"], queryFn: bookingPolicyApi.get, staleTime: 5 * 60 * 1000 });
  const { data: myAddresses } = useQuery({ queryKey: ["addresses"], queryFn: addressApi.list, enabled: isCustomer });
  const { data: myPasses, isError: passesFailed } = useQuery({ queryKey: ["my-subscriptions"], queryFn: subscriptionApi.mySubscriptions, enabled: isCustomer });

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
  // Customer "Book now" on a pass (?subscription=): the pass it came from,
  // shown as a summary on the address step — or why it couldn't be used.
  const [planIntro, setPlanIntro] = useState<string | null>(null);
  const [planIssue, setPlanIssue] = useState("");

  // ---- step 2 state ------------------------------------------------------
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  // Manager modes only: set when an existing customer is picked from the
  // typeahead, so their saved addresses and active passes can be offered.
  const [pickedCustomerId, setPickedCustomerId] = useState<string | null>(null);
  // Per-line (index into `lines`) override of whether a matched pass is
  // actually used — absent = default to true (use it). Reset whenever the
  // line list is reshuffled, so an index always means the same line.
  const [subscriptionOverride, setSubscriptionOverride] = useState<Record<number, boolean>>({});
  useEffect(() => setSubscriptionOverride({}), [pickedCustomerId]);
  // null = nothing chosen yet (a default may be preselected), "" = "New address".
  const [savedAddressId, setSavedAddressId] = useState<string | null>(null);
  const [wizardStep, setWizardStep] = useState(1);
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
  // Distance quote from the coverage check that resolved the current address.
  const [travel, setTravel] = useState<TravelQuote | null>(null);
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
  // Kept for a retry on the same number: a widget token stays reusable, and
  // the server only spends a classic code once the booking has passed its
  // checks (a spent one comes back "Invalid or expired code" → the popup).
  const [verified, setVerified] = useState<{ phone: string; proof: PhoneProof } | null>(null);
  const [otpError, setOtpError] = useState("");
  const [forceOtp, setForceOtp] = useState(false);
  // manager-log only: the clock time of the job, and the WhatsApp switch.
  const [logTime, setLogTime] = useState("");
  const [sendWhatsApp, setSendWhatsApp] = useState(true);
  const [discount, setDiscount] = useState("");
  // Manager booking of a prepaid service: the pay link the customer was sent.
  const [sentLink, setSentLink] = useState<{ numbers: string; link: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const phoneRef = useRef<HTMLInputElement>(null);

  // ---- keep an unfinished booking for THIS browser tab ---------------------
  // Back button, a tap on the logo, a refresh — the customer comes back to
  // exactly where they were. sessionStorage on purpose: this tab only,
  // gone when it closes, never days-old state resurfacing.
  const storageKey = `blussit:quickbook:${mode}`;
  const repeatId = searchParams.get("repeat");
  const serviceParam = searchParams.get("service") || searchParams.get("serviceId");
  const subscriptionParam = isCustomer ? searchParams.get("subscription") : null;
  const [restored, setRestored] = useState(false);
  const restoreAddressRef = useRef<null | { pinned: LocationValue | null; savedAddressId: string | null; pincode: string; typed: boolean; slot: string }>(null);
  const skipAutoDateRef = useRef(false);
  useEffect(() => {
    // A deep link starts a fresh booking instead of resuming the last one.
    if (repeatId || serviceParam || subscriptionParam) {
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
        if (isCustomer && typeof saved.planIntro === "string") setPlanIntro(saved.planIntro);
        if (!isCustomer) {
          setName(saved.name || "");
          setPhone(saved.phone || "");
          setPickedCustomerId(typeof saved.pickedCustomerId === "string" ? saved.pickedCustomerId : null);
        }
        // A manager's saved address only means something with its customer.
        const savedAddr: string | null = isCustomer || saved.pickedCustomerId ? saved.savedAddressId ?? null : null;
        setSavedAddressId(savedAddr);
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
          savedAddressId: savedAddr,
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
        JSON.stringify({ at: Date.now(), step, added, draft, planIntro, name, phone, pickedCustomerId, savedAddressId, pinned, typedAddress, line1, pincode, date, slot, paymentMethod, couponCode, notes, altName, altPhone, logTime, sendWhatsApp, discount })
      );
    } catch {
      // storage unavailable — nothing to keep
    }
  }, [restored, repeatId, storageKey, step, added, draft, planIntro, name, phone, pickedCustomerId, savedAddressId, pinned, typedAddress, line1, pincode, date, slot, paymentMethod, couponCode, notes, altName, altPhone, logTime, sendWhatsApp, discount]);

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
  // Saved addresses on offer: the customer's own, or (manager) the picked
  // customer's — so a repeat customer's visit reuses the address on file.
  const { data: customerAddresses } = useQuery({
    queryKey: ["customer-addresses", pickedCustomerId],
    queryFn: () => addressApi.forCustomer(pickedCustomerId as string),
    enabled: mode === "manager" && !!pickedCustomerId,
  });
  const savedAddresses = isCustomer ? myAddresses : mode === "manager" && pickedCustomerId ? customerAddresses : undefined;
  useEffect(() => {
    if (!savedAddresses?.length || savedAddressId !== null) return;
    const def = savedAddresses.find((a) => a.is_default) || savedAddresses[0];
    void chooseSavedAddress(def);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedAddresses, savedAddressId]);
  /** Manager: a different (or no) customer — their saved address no longer applies. */
  const pickCustomer = (id: string | null) => {
    setPickedCustomerId(id);
    if (savedAddressId) resetCoverage();
    if (savedAddressId !== null) setSavedAddressId(null);
  };

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
  // Each half applies ONCE — a later refetch (window focus) must never
  // overwrite what the customer has changed since.
  const repeatLinesDone = useRef(false);
  const repeatAddressDone = useRef(false);
  useEffect(() => {
    if (!repeatBooking || !services.length || !types.length) return;
    if (repeatBooking.booking_group_id && !repeatGroup) return;
    if (!repeatLinesDone.current) {
    repeatLinesDone.current = true;
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
    }
    // Wins over the default address even when the default's coverage check
    // is still in flight — chooseSavedAddress is latest-wins.
    if (!repeatAddressDone.current && isCustomer && myAddresses) {
      repeatAddressDone.current = true;
      const addr = myAddresses.find((a) => a.id === repeatBooking.address_id);
      if (addr) void chooseSavedAddress(addr);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repeatBooking, repeatGroup, services, types, myAddresses]);

  // ?service= / ?subscription= deep links — applied once the catalogue (and,
  // for a plan, the customer's plans) are in, then dropped from the URL so a
  // refresh resumes the booking instead of starting it over.
  const [preferredBase, setPreferredBase] = useState<string | null>(null);
  const deepLinkDone = useRef(false);
  useEffect(() => {
    if (deepLinkDone.current || (!serviceParam && !subscriptionParam) || !services.length || !types.length) return;
    // A failed plans call must not leave the deep link waiting forever.
    if (subscriptionParam && !myPasses && !passesFailed) return;
    deepLinkDone.current = true;
    if (subscriptionParam) {
      // "Book now" on a pass: when its vehicle type and service are both on
      // sale, the "what" is already answered — straight to where & when.
      // Otherwise say so and let the customer pick; never guess a service
      // (a wrong one would silently skip the pass and charge full price).
      const sub = myPasses?.find((p) => p.id === subscriptionParam);
      const typeId = sub?.vehicle_type && types.some((t) => t.id === sub.vehicle_type) ? sub.vehicle_type : "";
      const svc = sub?.service_id ? services.find((s) => s.id === sub.service_id && !s.is_addon) : undefined;
      const usable = !!sub && sub.effective_status === "active" && (sub.remaining_service_count ?? 0) > 0;
      let resolved = false;
      if (sub && usable && typeId && svc) {
        // A bike pass covers one exact bike-count variant — book that count.
        const fromPass: Draft = { typeId, count: bikeIds.has(typeId) ? variantCount(svc) : 1, base: svc.variant_group || svc.id, addons: [] };
        if (lineFor(fromPass)?.payload?.service_ids.includes(svc.id)) {
          setAdded([]);
          setDraft(fromPass);
          setPlanIntro(sub.id);
          setPlanIssue("");
          setStep(1);
          resolved = true;
        }
      }
      if (!resolved) {
        setPlanIntro(null);
        setDraft(typeId ? { typeId, count: 1, base: null, addons: [] } : EMPTY_DRAFT);
        setPlanIssue(
          sub?.renewal_pending
            ? "Your pass is renewing — pick a service to book as usual."
            : sub && sub.effective_status !== "active"
            ? "This pass has ended — pick a service to book as usual."
            : sub && !usable
              ? "This pass has no washes left right now — pick a service to book as usual."
              : "We couldn't load your pass — pick your service and we'll apply it if it matches."
        );
      }
    } else {
      const mains = services.filter((s) => !s.is_addon);
      // Slug first; older shared links carry the id.
      const svc = mains.find((s) => s.slug === serviceParam) || mains.find((s) => s.id === serviceParam);
      if (svc) {
        const key = svc.variant_group || svc.id;
        setPreferredBase(key);
        const eligible = types.filter((t) => eligibleFor(svc, t.id));
        if (eligible.length === 1) setDraft({ typeId: eligible[0].id, count: 1, base: key, addons: [] });
      }
    }
    const next = new URLSearchParams(searchParams);
    ["service", "serviceId", "subscription"].forEach((k) => next.delete(k));
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serviceParam, subscriptionParam, services, types, myPasses, passesFailed]);

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
    baseUnitPrice: number;
    payload: QuickBookingLine | null;
    services: Service[];
  }

  const unit = (s: Service, vt: string) => {
    const regular = priceFor(s, vt);
    if (!showFirstWash) return regular;
    const first = firstWashPriceFor(s, vt);
    return first != null && first < regular ? first : regular;
  };
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
        const perUnit = (s: Service) => unit(s, t.id);
        const perUnitRegular = (s: Service) => priceFor(s, t.id);
        let subtotal = 0;
        let regularSubtotal = 0;
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
          // What ONE matching pass actually waives on this line — the base
          // service's own per-unit price, same figure the backend's
          // _subscription_discount computes (add-ons/extra-bikes never
          // waived). Used to price the "use the plan" switch.
          baseUnitPrice: base ? perUnit(base) : 0,
          payload: base ? { vehicle_type: t.id, quantity, service_ids: serviceIds, service_quantities: quantities } : null,
          services: lineServices,
        };
    })();
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const lines: Line[] = useMemo(() => drafts.map(lineFor).filter(Boolean) as Line[], [drafts, types, services, bikeIds, showFirstWash]);

  const summary = lines.map((l) => `${l.count} ${l.type.name}${l.base ? ` · ${titleCase(l.base.name)}` : ""}`).join("  +  ");
  const lineLabel = (l: Line) => `${l.count > 1 ? `${l.count} × ` : ""}${l.type.name}${l.base ? ` · ${titleCase(l.base.name)}` : ""}`;

  // The plans that can pay for this visit: the signed-in customer's own, or
  // (manager) the picked customer's.
  const { data: customerPasses } = useQuery({
    queryKey: ["customer-active-passes", pickedCustomerId],
    queryFn: () => subscriptionApi.forCustomer(pickedCustomerId as string),
    enabled: isManager && !!pickedCustomerId,
  });
  const passes = isCustomer ? myPasses : isManager && pickedCustomerId ? customerPasses : undefined;
  const usablePasses = useMemo(
    () => (passes || []).filter((p) => p.effective_status === "active" && (p.remaining_service_count ?? 0) > 0 && !!p.service_id),
    [passes]
  );
  const passFor = (typeId: string, serviceIds: string[], taken: Set<string>) =>
    usablePasses.find((p) => !taken.has(p.id) && p.vehicle_type === typeId && serviceIds.includes(p.service_id as string));
  interface LineMatch {
    /** The passes this line would use, one per car. */
    subs: UserSubscription[];
    /** Units of the payload (a bike line is ONE booking however many bikes) a pass covers. */
    coveredCount: number;
    /** The "use the plan" switch — a line switched off claims no pass. */
    on: boolean;
  }
  // Mirrors the backend's _cars_for_lines exactly: lines in order, each car
  // takes the first unused pass for its vehicle type whose service is on the
  // line, and one pass covers one car per booking.
  const lineMatches: (LineMatch | null)[] = lines.map(() => null);
  {
    const taken = new Set<string>();
    lines.forEach((line, i) => {
      if (!line.payload) return;
      const on = subscriptionOverride[i] !== false;
      const mine = new Set(taken);
      const subs: UserSubscription[] = [];
      for (let n = 0; n < line.payload.quantity; n++) {
        const sub = passFor(line.type.id, line.payload.service_ids, mine);
        if (!sub) break;
        mine.add(sub.id);
        subs.push(sub);
      }
      if (on) subs.forEach((sub) => taken.add(sub.id));
      if (subs.length) lineMatches[i] = { subs, coveredCount: subs.length, on };
    });
  }
  /** Cars on each line the plan actually pays for. */
  const coveredUnits = lineMatches.map((m) => (m?.on ? m.coveredCount : 0));
  const lineCost = (i: number) => lines[i].subtotal - coveredUnits[i] * lines[i].baseUnitPrice;
  // What one pass waives is the base service's own price — add-ons and extra
  // bikes are always paid (same figure as the backend's _subscription_discount).
  const passSavings = lines.reduce((sum, line, i) => sum + coveredUnits[i] * line.baseUnitPrice, 0);
  /** Services still on the bill — a base service every car of which is plan-covered isn't. */
  const billed = lines.flatMap((line, i) =>
    line.services.filter((s) => s.id !== line.base?.id || coveredUnits[i] < (line.payload?.quantity ?? 1))
  );
  const otherPasses = isManager ? usablePasses.filter((p) => !lineMatches.some((m) => m?.subs.some((x) => x.id === p.id))) : [];

  const planTogglesNode = lineMatches.some(Boolean) ? (
    <div className="space-y-2">
      {lines.map((line, i) => {
        const match = lineMatches[i];
        if (!match) return null;
        const plan = match.subs[0].plan_name || "plan";
        const left = match.subs[0].remaining_service_count;
        const units = line.payload?.quantity ?? 1;
        return (
          <Switch
            key={i}
            checked={match.on}
            onChange={(next) => setSubscriptionOverride((prev) => ({ ...prev, [i]: next }))}
            label={`${isCustomer ? "Use my" : "Use their"} ${plan} — ${left} wash${left === 1 ? "" : "es"} left`}
            description={`${lineLabel(line)}${match.coveredCount < units ? ` · covers ${match.coveredCount} of ${units}` : ""}`}
          />
        );
      })}
      {otherPasses.length > 0 && (
        <p className="text-xs text-gray-500">Other plans: {otherPasses.map((p) => p.plan_name).filter(Boolean).join(", ")} — not for this booking.</p>
      )}
    </div>
  ) : null;

  // Arrived from a pass's "Book now": the summary that replaces step 1 —
  // shown while that pass still covers what's on the visit.
  const introSub = planIntro ? (myPasses || []).find((p) => p.id === planIntro) : undefined;
  const introIndex = introSub ? lineMatches.findIndex((m) => !!m?.on && m.subs.some((x) => x.id === introSub.id)) : -1;
  const { data: introPlans } = useQuery({
    queryKey: ["public-plans"],
    queryFn: () => subscriptionApi.plans(true),
    enabled: isCustomer && !!introSub && !introSub.plan_name,
    staleTime: 5 * 60 * 1000,
  });
  const introLine = introIndex >= 0 ? lines[introIndex] : null;
  const introPlanName = introSub ? introSub.plan_name || introPlans?.find((p) => p.id === introSub.plan_id)?.name : undefined;
  const introLeft = introSub?.remaining_service_count ?? 0;
  const planIntroNode =
    isCustomer && introSub && introLine ? (
      <div className="flex items-center gap-3 rounded-xl border border-[#F3E5B5] bg-white p-3.5">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-black">
          <Gift className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-black">Using your {introPlanName ? `${introPlanName} pass` : "pass"}</p>
          <p className="text-xs text-gray-500">
            {introLine.type.name}
            {introLine.base ? ` · ${titleCase(introLine.base.name)}` : ""} · {introLeft} wash{introLeft === 1 ? "" : "es"} left
          </p>
        </div>
        <button type="button" onClick={() => setStep(0)} className="shrink-0 text-xs font-semibold text-gray-600 underline underline-offset-2 hover:text-black">
          Change
        </button>
      </div>
    ) : null;

  // The editor's own line (may be incomplete — used for the chips/prices).
  const editing = draft.typeId ? lineFor({ ...draft, base: draft.base }) : null;
  const editingGroups = draft.typeId ? baseGroups(services, draft.typeId) : [];
  const editingKit = draft.typeId ? addonKit(services, draft.typeId, bikeIds) : null;
  const editingIsBike = bikeIds.has(draft.typeId);

  // use_subscription defaults true (server auto-applies a matching pass) —
  // false only for a line whose "use the plan" switch was turned off.
  const linesForPayload = (): QuickBookingLine[] =>
    lines
      .map((l, i) => (l.payload ? { ...l.payload, use_subscription: subscriptionOverride[i] !== false } : null))
      .filter(Boolean) as QuickBookingLine[];

  // ---- the bill, from the server --------------------------------------------
  // POST /bookings/quote runs the same pricing code the booking will
  // (first-wash price by phone, plans, coupon, distance charge, prepaid), so
  // what this screen shows is what gets charged, in every mode. The local
  // maths below is only the placeholder until the first quote lands.
  const quoteLines = lines.length > 0 && lines.every((l) => !!l.payload) ? linesForPayload() : [];
  const quotePhone = isCustomer ? undefined : validateIndianMobile(phone) || undefined;
  const quoteAddress: Pick<BookingQuotePayload, "address_id" | "address"> =
    isLog || coverage !== "covered"
      ? {}
      : savedAddressId
        ? { address_id: savedAddressId }
        : pinned
          ? { address: { latitude: pinned.latitude, longitude: pinned.longitude, pincode: pinned.pincode || checkedPincode || undefined } }
          : checkedPincode
            ? { address: { pincode: checkedPincode } }
            : {};
  const quoteRequest: BookingQuotePayload | null = quoteLines.length
    ? {
        lines: quoteLines,
        customer_phone: quotePhone,
        ...quoteAddress,
        coupon_code: isManager && !isLog && couponCode.trim() ? couponCode.trim().toUpperCase() : undefined,
        ...(isLog ? { mode: "log" as const, scheduled_date: date, service_time: logTime || undefined } : {}),
      }
    : null;
  // Step 2 always; step 1 only for a signed-in customer (their first-wash
  // status and plans are known without typing anything).
  const quoteWanted = !!quoteRequest && (step === 1 || isCustomer);
  const settledQuoteKey = useSettled(quoteWanted ? JSON.stringify(quoteRequest) : "", 450);
  const { data: quote, error: quoteFailure, isFetching: quoting } = useQuery({
    queryKey: ["booking-quote", mode, settledQuoteKey],
    queryFn: () => bookingApi.quote(JSON.parse(settledQuoteKey) as BookingQuotePayload),
    enabled: !!settledQuoteKey,
    retry: false,
    staleTime: 15_000,
  });
  // Only a quote for exactly what is on screen right now counts.
  const liveQuote = quoteWanted && settledQuoteKey === JSON.stringify(quoteRequest) ? quote : undefined;
  useEffect(() => {
    if (quote) setFirstWashKnown(quote.first_time_eligible);
  }, [quote]);
  // A refusal the booking would also hit (e.g. a plan that can't be used)
  // is shown; a network blip or rate limit just leaves the local estimate.
  const quoteStatus = (quoteFailure as { response?: { status?: number } } | null)?.response?.status;
  const quoteError = quoteWanted && quoteFailure && quoteStatus && quoteStatus < 500 && quoteStatus !== 429 ? getErrorMessage(quoteFailure) : "";

  const total = liveQuote ? liveQuote.subtotal : lines.reduce((n, l) => n + l.subtotal, 0);
  const regularTotal = liveQuote ? liveQuote.regular_subtotal : lines.reduce((n, l) => n + l.regularSubtotal, 0);
  const planSavings = liveQuote ? liveQuote.plan_discount : passSavings;
  const couponSavings = liveQuote ? liveQuote.coupon_discount : 0;
  const displayTotal = Math.max(0, total - planSavings - couponSavings);
  // Distance charge: once per visit, only when a billed service charges
  // travel — the amount is the server's quote for this address.
  const travelQuote = liveQuote ? liveQuote.travel : travel;
  const travelDue = !isLog && !!travelQuote && (liveQuote ? true : billed.some((s) => s.charges_travel));
  const travelCharge = travelDue && travelQuote ? travelQuote.charge : 0;
  const payable = liveQuote && !isLog ? liveQuote.total_amount : displayTotal + travelCharge;
  // Online only: a billed prepaid service, or (self-serve) extras on a plan
  // wash — the server parks both until paid.
  const prepaidName = isLog ? undefined : liveQuote ? liveQuote.prepaid_service || undefined : billed.find((s) => s.prepaid_only)?.name;
  const planExtrasOnline = !isManager && lines.some((l, i) => coveredUnits[i] > 0 && l.subtotal / (l.payload?.quantity || 1) > l.baseUnitPrice);
  const onlineOnly = liveQuote && !isLog ? liveQuote.online_only : payable > 0 && (!!prepaidName || planExtrasOnline);
  const payMethod: "cash" | "online" = onlineOnly ? "online" : paymentMethod;
  const onlineReason = prepaidName
    ? `${titleCase(prepaidName)} is prepaid${isManager ? " — the customer gets a payment link on WhatsApp." : "."}`
    : "Extras on a plan wash are paid online.";
  const payOptions = [
    { id: "cash", icon: Banknote, title: isManager ? "Cash" : "Pay after the wash", sub: isManager ? "Collected at the visit" : "Cash or UPI to the captain" },
    { id: "online", icon: CreditCard, title: isManager ? "Online" : "Pay online now", sub: isManager ? "Customer pays online" : "UPI, card or netbanking" },
  ] as const;
  // Log mode only: rupees the manager took off the bill. What the customer
  // actually paid (finalTotal) drives the footer, the payment choice and the save.
  const discountNum = isLog ? Math.round(Number(discount) || 0) : 0;
  const finalTotal = Math.max(0, displayTotal - discountNum);
  const allServices = lines.flatMap((l) => l.services);
  const step1Ready = lines.length > 0 && lines.every((l) => !!l.base) && (!draft.typeId || draftReady);

  const otherVehicles = added.reduce((n, d) => n + d.count, 0);
  const pickType = (typeId: string) => {
    // Only auto-select if there is a deep-linked preferredBase, otherwise leave it empty
    const groups = baseGroups(services, typeId);
    const first = groups.find((g) => g.key === preferredBase)?.key ?? null;
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
  const removeAdded = (index: number) => {
    setAdded((a) => a.filter((_, i) => i !== index));
    setSubscriptionOverride({});
  };
  const editAdded = (index: number) => {
    const d = added[index];
    if (!d) return;
    // Anything half-typed in the editor is kept as its own line.
    setAdded((a) => [...a.filter((_, i) => i !== index), ...(draftReady ? [draft] : [])]);
    setSubscriptionOverride({});
    setDraft(d);
    scrollToTopNow();
  };

  // ---- address / coverage --------------------------------------------------
  // Latest wins: every address pick bumps this, and a coverage answer for an
  // older pick (a default address still checking when "Book again" or the
  // customer picks another) is dropped instead of overwriting the newer one.
  const addressSeq = useRef(0);
  const resetCoverage = () => {
    addressSeq.current += 1;
    setCoverage("idle");
    setCheckedPincode("");
    setCenterId("");
    setSlot("");
    setTravel(null);
  };

  async function chooseSavedAddress(a: Address) {
    const seq = ++addressSeq.current;
    setSavedAddressId(a.id);
    setPinned(null);
    setCoverage("checking");
    setCheckedPincode(a.pincode);
    setCenterId("");
    setSlot("");
    setTravel(null);
    try {
      const result = await coverageApi.check({ latitude: a.latitude ?? undefined, longitude: a.longitude ?? undefined, pincode: a.pincode });
      if (seq !== addressSeq.current) return;
      if (result.covered && result.center) {
        setCenterId(result.center.id);
        setCenterCity(result.center.city || a.city);
        setCenterState(result.center.state || a.state);
        setTravel(result.travel ?? null);
        setCoverage("covered");
      } else {
        setCoverage("uncovered");
      }
    } catch {
      if (seq === addressSeq.current) setCoverage("uncovered");
    }
  }

  async function onPin(v: LocationValue) {
    const seq = ++addressSeq.current;
    setPinned(v);
    if (v.pincode) setPincode(v.pincode);
    setCoverage("checking");
    // A reverse-geocode can come back without a postal code — the pin
    // alone decides coverage, so never invent a placeholder here.
    setCheckedPincode(v.pincode || "");
    setCenterId("");
    setSlot("");
    setTravel(null);
    try {
      const result = await coverageApi.check({ latitude: v.latitude, longitude: v.longitude, pincode: v.pincode });
      if (seq !== addressSeq.current) return;
      if (result.covered && result.center) {
        setCenterId(result.center.id);
        setCenterCity(result.center.city || v.city);
        setCenterState(result.center.state || v.state);
        setTravel(result.travel ?? null);
        setCoverage("covered");
      } else {
        setCoverage("uncovered");
      }
    } catch {
      if (seq === addressSeq.current) setCoverage("uncovered");
    }
  }

  const latestPincode = useRef("");
  async function checkPincode(pin: string) {
    const seq = ++addressSeq.current;
    latestPincode.current = pin;
    setCoverage("checking");
    setCheckedPincode(pin);
    setCenterId("");
    setSlot("");
    setTravel(null);
    try {
      const centers = await serviceCenterApi.lookupByPincode(pin);
      // The customer kept typing while this was in flight — a newer check owns the screen.
      if (latestPincode.current !== pin || seq !== addressSeq.current) return;
      if (centers.length) {
        setCenterId(centers[0].id);
        setCenterCity(centers[0].location.city);
        setCenterState(centers[0].location.state);
        setCoverage("covered");
      } else {
        setCoverage("uncovered");
      }
    } catch {
      if (latestPincode.current === pin && seq === addressSeq.current) setCoverage("uncovered");
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
      if (!savedAddresses) return; // wait for the list
      const a = savedAddresses.find((x) => x.id === r.savedAddressId);
      void finish(async () => {
        if (a) await chooseSavedAddress(a);
        else setSavedAddressId(null); // gone since — fall back to the default
      });
      return;
    }
    if (r.typed && r.pincode.trim().length >= 6) {
      void finish(() => checkPincode(r.pincode.trim()));
      return;
    }
    restoreAddressRef.current = null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restored, savedAddresses]);

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
        lines: linesForPayload(),
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
    const proof: PhoneProof | undefined = freshProof ?? (verified?.phone === canonicalPhone ? verified.proof : undefined);
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
        lines: linesForPayload(),
        scheduled_date: date,
        scheduled_slot: slot,
        payment_method: payable > 0 ? payMethod : "cash",
        coupon_code: isManager && couponCode.trim() ? couponCode.trim().toUpperCase() : undefined,
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
        if (result.payment_link) {
          setSentLink({ numbers: result.booking_numbers.join(" + "), link: result.payment_link });
          return;
        }
        // Prepaid but no link came back (e.g. the gateway was down): say so.
        const linkMissing = payMethod === "online" && !!prepaidName;
        pushToast({
          tone: linkMissing ? "warning" : "success",
          title: "Booking created",
          message: linkMissing
            ? `${result.booking_numbers.join(" + ")} · the payment link couldn't be sent.`
            : `${result.booking_numbers.join(" + ")} · service code ${result.service_code || "—"}`,
        });
        navigate("/manager/bookings");
        return;
      }
      if (isCustomer) {
        queryClient.invalidateQueries({ queryKey: ["my-subscriptions"] });
        queryClient.invalidateQueries({ queryKey: ["addresses"] });
      }
      navigate(`/thank-you?token=${result.confirmation_token}`, {
        state: {
          type: "booking",
          booking_number: result.booking_numbers.join(" + "),
          scheduled_date: date,
          scheduled_slot: slot,
          service_label: lines.map(lineLabel).join(" + "),
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

  const copyLink = async () => {
    if (!sentLink) return;
    try {
      await navigator.clipboard.writeText(sentLink.link);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard unavailable — the link is still visible to select by hand
    }
  };

  // ---- render --------------------------------------------------------------
  // Only what moves the total gets its own row; otherwise the summary alone.
  const travelBeyondKm = travelQuote ? Math.max(0, Math.round((travelQuote.distance_km - travelQuote.free_km) * 10) / 10) : 0;
  const billRows: { label: string; value: string }[] = [];
  if (planSavings > 0) billRows.push({ label: isCustomer ? "Covered by your plan" : "Covered by plan", value: `−₹${planSavings}` });
  if (couponSavings > 0) billRows.push({ label: `Coupon ${liveQuote?.coupon_code || ""}`.trim(), value: `−₹${couponSavings}` });
  if (travelDue && travelQuote)
    billRows.push(
      travelQuote.charge > 0
        ? { label: `Distance charge · ${travelBeyondKm} km`, value: `₹${travelQuote.charge}` }
        : { label: "Distance charge", value: `Free — within ${travelQuote.free_km} km` }
    );
  if (discountNum > 0 && discountNum <= displayTotal) billRows.push({ label: "Discount", value: `−₹${discountNum}` });
  const shownTotal = isLog ? finalTotal : payable;
  // The first-wash price struck against what a returning customer pays.
  const struckTotal = showFirstWash && regularTotal > total ? shownTotal + (regularTotal - total) : null;

  const footer = (
    <div className="space-y-3">
      {billRows.length > 0 && (
        <div className="space-y-1 text-sm text-gray-600">
          <div className="flex justify-between gap-3">
            <span className="min-w-0 truncate">{summary}</span>
            <span className="font-mono-num shrink-0">₹{total}</span>
          </div>
          {billRows.map((r) => (
            <div key={r.label} className="flex justify-between gap-3">
              <span>{r.label}</span>
              <span className="font-mono-num shrink-0">{r.value}</span>
            </div>
          ))}
        </div>
      )}
      <div className="flex items-end justify-between gap-2">
        <span className="min-w-0 flex-1 truncate text-sm text-gray-600">{billRows.length ? "Total" : summary || "Pick your vehicle"}</span>
        <span className="text-right">
          {struckTotal != null && <span className="mr-1.5 text-xs text-gray-400 line-through">₹{struckTotal}</span>}
          <span className="font-mono-num text-lg font-bold text-black">₹{shownTotal}</span>
        </span>
      </div>
      {!isLog && (
        <p className="flex items-center gap-1.5 text-xs text-gray-500">
          <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-black" aria-hidden="true" />
          {liveQuote ? (liveQuote.travel_pending ? "Any distance charge shows once you add the address." : "Price shown is final — no hidden charges.") : coverage !== "covered" && billed.some((s) => s.charges_travel) ? "Any distance charge shows once you add the address." : quoting ? "Checking the price…" : "Price shown is final — no hidden charges."}
        </p>
      )}
      {(error || quoteError) && <p className="text-right text-xs font-medium text-[var(--color-error)]">{error || quoteError}</p>}
      <div className="flex items-center justify-between gap-3">
        <Button variant="outline" onClick={() => (step > 0 ? setStep(0) : navigate(-1))}>
          Back
        </Button>
        {step === 0 ? (
          <Button
            variant="info"
            className="min-w-[150px] font-semibold"
            disabled={!step1Ready}
            onClick={() => {
              setPlanIssue("");
              setStep(1);
            }}
          >
            Continue
          </Button>
        ) : (
          <Button variant="info" className="min-w-[150px] font-semibold" disabled={!step2Ready} isLoading={submitting} onClick={() => void submit()}>
            {isLog ? "Save As Done" : !isManager && payable > 0 && payMethod === "online" ? "Book And Pay" : "Book Now"}
          </Button>
        )}
      </div>
      {step === 0 && !step1Ready && (draft.typeId || added.length > 0) && (
        <p className="text-right text-xs text-[var(--color-error)]">Pick a service to continue.</p>
      )}
    </div>
  );

  // Generate dates for the horizontal selector
  const availableDates = useMemo(() => {
    const start = new Date(`${todayIST()}T00:00:00`);
    const horizon = Math.max(1, Math.min(policy?.max_advance_days || 7, 14));
    const days = [];
    for (let i = 0; i < horizon; i++) {
      const d = new Date(start);
      d.setDate(start.getDate() + i);
      const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      days.push({ iso, dateObj: d });
    }
    return days;
  }, [policy]);

  const hasServiceSelected = !!(draft.typeId && draft.base) || added.length > 0;
  const hasValidLocation = coverage === "covered";
  const hasSlotSelected = !!slot;

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
          setVerified({ phone: validateIndianMobile(phone) || phone.trim(), proof });
          setOtpOpen(false);
          void submit(proof);
        }}
      />
    )}
    
    <div className="min-h-screen bg-[#F6FAFE] pb-16 font-sans">
      
      {/* FULL-WIDTH HERO SECTION */}
      <div 
        className="relative w-[100vw] ml-[calc(50%-50vw)] bg-cover bg-[position:center_right] sm:bg-[position:80%_center] bg-no-repeat -mt-4 sm:-mt-8" 
        style={{ backgroundImage: 'url(/booking.png)' }}
      >
        {/* Overlay to ensure text readability on left side */}
        <div 
          className="absolute inset-0 z-0 pointer-events-none" 
          style={{ background: 'linear-gradient(to right, rgba(255,255,255,0.72) 0%, rgba(255,255,255,0.52) 20%, rgba(255,255,255,0.25) 38%, rgba(255,255,255,0.05) 55%, rgba(255,255,255,0) 70%)' }}
        ></div>
        
        {/* Constrain content to align with booking area below */}
        <div className="relative z-10 mx-auto max-w-[1400px] w-full px-4 sm:px-6 lg:px-[24px] pt-[36px] sm:pt-[44px] pb-[20px] sm:pb-[28px]">
          <div className="w-full sm:w-[50%]">
            <span className="mb-2 block text-[11px] font-bold uppercase tracking-widest text-[#1677F2]">
              PREMIUM DOORSTEP CAR CARE
            </span>
            <h1 className="mb-3 text-[38px] sm:text-[50px] font-extrabold leading-[1.0] tracking-tight">
              <span className="text-[#0B1B3A]">Book Your</span><br/>
              <span className="text-[#1677F2]">Car Wash</span>
            </h1>
            <p className="text-[15px] sm:text-[18px] text-[#334155] font-semibold mb-5">
              Quick. Easy. At your doorstep in Indore.
            </p>
            
            <div className="flex flex-col sm:flex-row gap-2 sm:gap-4 mt-2">
               <div className="flex items-center gap-1.5">
                 <div className="w-[18px] h-[18px] rounded-full bg-green-50 flex items-center justify-center text-green-600"><Leaf className="w-2.5 h-2.5"/></div>
                 <span className="text-[12px] font-semibold text-[#0B1B3A]">Waterless Options</span>
               </div>
               <div className="flex items-center gap-1.5">
                 <div className="w-[18px] h-[18px] rounded-full bg-[#EBF4FF] flex items-center justify-center text-[#1677F2]"><Clock className="w-2.5 h-2.5"/></div>
                 <span className="text-[12px] font-semibold text-[#0B1B3A]">On-Time Service</span>
               </div>
               <div className="flex items-center gap-1.5">
                 <div className="w-[18px] h-[18px] rounded-full bg-[#EBF4FF] flex items-center justify-center text-[#1677F2]"><ShieldCheck className="w-2.5 h-2.5"/></div>
                 <span className="text-[12px] font-semibold text-[#0B1B3A]">Trusted Professionals</span>
               </div>
            </div>
          </div>
        </div>
      </div>

      {/* BOOKING AREA CONTAINER */}
      <div className="mx-auto max-w-[1100px] w-[calc(100%-32px)] sm:w-[calc(100%-48px)] pt-6 sm:pt-8">
        
        {/* BOOKING AREA GRID */}
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.6fr)_minmax(340px,0.9fr)] gap-5 items-start">
          
          {/* LEFT: PROGRESSIVE BOOKING FLOW */}
          <div className="space-y-4 sm:space-y-5">
            
              {wizardStep === 1 && (
                <div className="space-y-4 sm:space-y-5 animate-in fade-in slide-in-from-left-4 duration-300">
             {/* STEP 1 & 2: CAR TYPE & SERVICE */}
             <div className="space-y-4 rounded-[20px] bg-white p-4 sm:p-5 shadow-[0_4px_20px_rgba(15,35,70,0.06)] border border-gray-100 animate-in fade-in slide-in-from-bottom-4 duration-300">
                <div className="flex items-center gap-2 mb-2">
                   <div className="w-8 h-8 rounded-full bg-[#EBF4FF] flex items-center justify-center text-[#1677F2]"><BadgeCheck className="h-4 w-4" /></div>
                   <h2 className="text-[20px] sm:text-[22px] font-bold text-[#0B1B3A]">Service Details</h2>
                </div>
                
                {planIssue && (
                  <p role="status" className="flex items-start gap-2 rounded-[14px] border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900">
                    <Info className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" />
                    <span>{planIssue}</span>
                  </p>
                )}
                
                {added.length > 0 && (
                  <div className="rounded-[16px] border border-gray-100 bg-[#F8FAFC] p-4 mb-4">
                    <p className="text-[13px] font-semibold text-[#0B1B3A] mb-2">On this visit · {totalVehicles} of {maxVehicles}</p>
                    <div className="space-y-2">
                      {added.map((d, i) => {
                        const li = lines.findIndex((x) => x.draft === d);
                        const l = lines[li];
                        if (!l) return null;
                        const cost = lineCost(li);
                        return (
                          <div key={`${d.typeId}-${i}`} className="flex items-center gap-3 text-sm bg-white p-3 rounded-[16px] border border-gray-100 shadow-[0_4px_10px_rgba(15,35,70,0.03)]">
                            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#EBF4FF] text-[#1677F2]">
                              <BadgeCheck className="h-4 w-4" />
                            </div>
                            <span className="min-w-0 flex-1 truncate text-gray-600">
                              <span className="font-semibold text-gray-900">{lineLabel(l)}</span>
                              {l.addons.length ? ` + ${l.addons.map((x) => titleCase(x.name)).join(", ")}` : ""}
                            </span>
                            <span className="font-mono-num shrink-0 font-semibold text-[#0B1B3A]">{coveredUnits[li] > 0 && cost === 0 ? "Covered" : `₹${cost}`}</span>
                            <button type="button" onClick={() => editAdded(i)} className="shrink-0 text-[13px] font-semibold text-[#1677F2] hover:text-blue-800">Edit</button>
                            <button type="button" onClick={() => removeAdded(i)} className="shrink-0 text-[#94A3B8] hover:text-red-500"><Trash2 className="h-4 w-4" /></button>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                  {/* STEP 1: CAR TYPE */}
                  <div className="w-full">
                     <p className="mb-1.5 text-[12px] font-semibold text-[#64748B] uppercase tracking-wide">Car Type</p>
                     <div className="rounded-[14px] border border-gray-200 bg-white h-[56px] flex items-center px-3 transition-colors focus-within:border-[#1677F2] focus-within:ring-1 focus-within:ring-[#1677F2]">
                         <select className="w-full bg-transparent p-0 text-[14px] font-semibold text-[#0B1B3A] focus:outline-none border-none ring-0 h-full cursor-pointer"
                           value={draft.typeId} onChange={(e) => pickType(e.target.value)}>
                           <option value="">Select car type</option>
                           {types.map((t) => (
                             <option key={t.id} value={t.id}>{t.name}</option>
                           ))}
                         </select>
                     </div>
                  </div>
                  {/* STEP 2: SERVICE (Disabled until car type is selected) */}
                  <div className="w-full">
                    <p className="mb-1.5 text-[12px] font-semibold text-[#64748B] uppercase tracking-wide">Service</p>
                    <div className={`rounded-[14px] border h-[56px] flex items-center px-3 transition-colors ${draft.typeId ? "border-gray-200 bg-white focus-within:border-[#1677F2] focus-within:ring-1 focus-within:ring-[#1677F2]" : "border-transparent bg-[#F8FAFC] opacity-70"}`}>
                        <select className="w-full bg-transparent p-0 text-[14px] font-semibold text-[#0B1B3A] focus:outline-none border-none ring-0 h-full cursor-pointer disabled:cursor-not-allowed"
                          value={draft.base || ""} onChange={(e) => pickBase(e.target.value)} disabled={!draft.typeId || editingGroups.length === 0}>
                           <option value="">{servicesLoading ? "Loading..." : !draft.typeId ? "Select car type first" : "Select service"}</option>
                           {editingGroups.map((g) => (
                             <option key={g.key} value={g.key}>{titleCase(g.label)}</option>
                           ))}
                        </select>
                    </div>
                  </div>
                </div>
             </div>

             {/* STEP 3 & ADD-ONS */}
             {draft.typeId && draft.base && editing && (
               <>
               <div className="grid grid-cols-1 lg:grid-cols-[1fr_0.8fr] gap-4">
                 {/* SERVICE CARD */}
                 <div className="rounded-[20px] bg-white p-4 sm:p-5 shadow-[0_4px_20px_rgba(15,35,70,0.06)] border border-gray-100 animate-in fade-in slide-in-from-bottom-4 duration-300 flex flex-col justify-center">
                  {editingGroups.filter(g => g.key === draft.base).map(g => {
                     const shown = unit(g.primary, draft.typeId);
                     const { price, original } = priceForType(g.primary, draft.typeId);
                     const struck = shown < price ? price : original;
                     const pct = discountPercent(shown, struck);
                     const offerTag = g.variants.find((v) => v.offer_tag?.trim())?.offer_tag;
                     const cardLine = lineFor({ ...draft, base: g.key, addons: [] });
                     const inPlan = !!cardLine?.payload && !!passFor(draft.typeId, cardLine.payload.service_ids, new Set());
                     const inc = parseIncludes(g.primary.description);
                     const items = inc.items.length ? inc.items.map(titleCase) : inc.summary ? [inc.summary] : [];
                     return (
                        <div key={g.key} className="flex-1 w-full">
                           <div className="flex items-start justify-between gap-3 mb-2">
                             <div>
                               <div className="flex items-center gap-2">
                                 <span className="text-[18px] font-bold text-[#0B1B3A]">{titleCase(g.label)}</span>
                                 {(pct != null) && <span className="text-[11px] font-bold text-[#1677F2] bg-[#EBF4FF] px-2 py-0.5 rounded-full">{pct}% OFF</span>}
                               </div>
                               {(offerTag || inPlan || shown < price) && (
                                 <div className="mt-1 flex flex-wrap items-center gap-1.5">
                                   {offerTag && <OfferTag label={offerTag} />}
                                   {shown < price && <span className="text-[11px] font-semibold text-[#64748B]">First wash</span>}
                                   {inPlan && <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-[#1677F2]"><BadgeCheck className="h-3 w-3" /> In plan</span>}
                                 </div>
                               )}
                             </div>
                             <div className="text-right">
                               <div className="font-mono-num text-[22px] font-bold text-[#0B1B3A]">₹{shown}</div>
                               {struck != null && <div className="text-[12px] font-semibold text-[#94A3B8] line-through">₹{struck}</div>}
                             </div>
                           </div>
                           {items.length > 0 && (
                             <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
                               {items.map((it) => (
                                 <div key={it} className="flex items-center gap-1.5 text-[13px] font-medium text-[#64748B]">
                                   <CheckCircle2 className="h-[14px] w-[14px] text-[#1677F2]" />
                                   <span>{it}</span>
                                 </div>
                               ))}
                             </div>
                           )}
                           
                           <div className="mt-4 flex items-center gap-3 pt-3 border-t border-gray-100">
                             <span className="text-[13px] font-semibold text-[#64748B]">Number of vehicles:</span>
                             <div className="rounded-[12px] border border-gray-200 bg-white h-[40px] flex items-center px-1">
                                <QtyStepper value={draft.count} min={1} max={10} onChange={setCount} />
                             </div>
                           </div>
                        </div>
                     )
                  })}
               </div>

                 {/* ADD-ONS (Optional) */}
                 {editingKit && (editingKit.simple.length > 0 || (editingIsBike && editingKit.bikePolish)) && (
                   <div className="rounded-[20px] bg-white p-4 sm:p-5 shadow-[0_4px_20px_rgba(15,35,70,0.06)] border border-gray-100 animate-in fade-in slide-in-from-bottom-4 duration-300 flex flex-col justify-center">
                 <p className="mb-2 text-[12px] font-semibold text-[#64748B] uppercase tracking-wide">Add-ons</p>
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
                         aria-pressed={on}
                         className={`rounded-[12px] px-3 py-1.5 text-[13px] font-medium transition-colors border ${
                           on ? "border-[#1677F2] bg-[#1677F2] text-white" : "border-gray-200 bg-white text-[#0B1B3A] hover:border-gray-300"
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
             </div>
             
             {/* ACTIONS ROW */}
             <div className="flex flex-col sm:flex-row justify-between items-center gap-3 mt-4 pt-2">
               {totalVehicles < maxVehicles ? (
                 <Button type="button" variant="outline" className="w-full sm:w-auto text-[13px] h-10 font-semibold rounded-[12px] bg-white border-gray-200" disabled={!draftReady} onClick={addAnother}>
                   <Plus className="h-4 w-4 mr-1.5" /> Add another vehicle
                 </Button>
               ) : (
                 <div />
               )}

               {draftReady && (
                  <button 
                    type="button" 
                    onClick={() => setWizardStep(2)} 
                    className="w-full sm:w-auto px-12 h-[44px] bg-[#FBBF24] text-[#0B1B3A] text-[15px] font-bold rounded-[12px] hover:bg-[#F59E0B] transition-colors shadow-sm ml-auto"
                  >
                    Continue
                  </button>
               )}
             </div>
             </>
             )}
                </div>
              )}
              {wizardStep === 2 && (
                <div className="space-y-4 sm:space-y-5 animate-in fade-in slide-in-from-right-4 duration-300">

                 <button 
                   type="button" 
                   onClick={() => setWizardStep(1)} 
                   className="flex items-center text-[14px] font-semibold text-[#64748B] hover:text-[#0B1B3A] transition-colors mb-2"
                 >
                   ← Back to Service Details
                 </button>
             {/* STEP 7: CONTACT INFORMATION */}
             
               <div className="rounded-[20px] bg-white p-4 sm:p-5 shadow-[0_4px_20px_rgba(15,35,70,0.06)] border border-gray-100 animate-in fade-in slide-in-from-bottom-4 duration-300 space-y-4">
                 <div className="flex items-center gap-2">
                    <div className="w-8 h-8 rounded-full bg-[#EBF4FF] flex items-center justify-center text-[#1677F2]"><BadgeCheck className="h-4 w-4" /></div>
                    <h2 className="text-[20px] sm:text-[22px] font-bold text-[#0B1B3A]">Contact Details</h2>
                 </div>
                 
                 {isCustomer && user && user.phone ? (
                   <div className="flex items-center gap-3 bg-white p-3 rounded-[14px] border border-gray-100 mt-2 shadow-sm">
                      <div className="w-10 h-10 rounded-full bg-[#EBF4FF] flex items-center justify-center text-[#1677F2] font-semibold text-[14px]">{user.full_name?.charAt(0) || "U"}</div>
                      <div>
                        <p className="text-[12px] font-semibold text-[#64748B]">Booking as</p>
                        <p className="text-[15px] font-semibold text-[#0B1B3A]">{user.full_name} <span className="font-normal text-gray-400 mx-1">·</span> {user.phone}</p>
                      </div>
                   </div>
                 ) : isManager ? (
                   <div className="mt-2"><CustomerNamePhoneFields name={name} phone={phone} onChangeName={(v) => { setName(v); pickCustomer(null); }} onChangePhone={(v) => { setPhone(v); pickCustomer(null); }} onPick={(c) => pickCustomer(c.id)} nameError={fieldErrors.name} phoneError={fieldErrors.phone} phoneInputRef={phoneRef} /></div>
                 ) : (
                   <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 mt-2">
                     <div>
                       <p className="mb-1.5 text-[12px] font-semibold text-[#64748B] uppercase tracking-wide">Your Name</p>
                       <Input maxLength={100} value={name} onChange={(e) => setName(e.target.value)} error={fieldErrors.name} placeholder="Enter your name" className="h-[46px] text-[14px] rounded-[14px]" />
                     </div>
                     <div>
                       <p className="mb-1.5 text-[12px] font-semibold text-[#64748B] uppercase tracking-wide">Mobile Number</p>
                       <Input ref={phoneRef} value={phone} inputMode="numeric" onChange={(e) => setPhone(cleanMobileInput(e.target.value))} error={fieldErrors.phone} placeholder="10-digit mobile" className="h-[46px] text-[14px] rounded-[14px]" />
                     </div>
                   </div>
                 )}
               </div>

             {/* STEP 4: WHERE SECTION */}
             {hasServiceSelected && (
               <div className="rounded-[20px] bg-white p-4 sm:p-5 shadow-[0_4px_20px_rgba(15,35,70,0.06)] border border-gray-100 animate-in fade-in slide-in-from-bottom-4 duration-300 space-y-4">
                 <div className="flex items-center gap-2">
                    <div className="w-8 h-8 rounded-full bg-[#EBF4FF] flex items-center justify-center text-[#1677F2]"><MapPin className="h-4 w-4" /></div>
                    <h2 className="text-[20px] sm:text-[22px] font-bold text-[#0B1B3A]">Where should we wash your car?</h2>
                 </div>
                 
                 {!!savedAddresses?.length && (
                   <div className="flex flex-wrap gap-2 pt-2">
                     {savedAddresses.map((a) => (
                       <button key={a.id} type="button" onClick={() => void chooseSavedAddress(a)} aria-pressed={savedAddressId === a.id} className={`rounded-[14px] px-4 py-2.5 text-left border transition-all ${savedAddressId === a.id ? "border-[#1677F2] bg-[#F0F7FF] ring-1 ring-[#1677F2]" : "border-gray-200 bg-white hover:border-[#1677F2]"}`}>
                         <span className="block font-semibold text-[14px] text-[#0B1B3A]">{a.label}</span>
                         <span className="block truncate text-[12px] font-medium text-[#64748B] mt-0.5">{a.line1} · {a.pincode}</span>
                       </button>
                     ))}
                     <button type="button" onClick={() => { setSavedAddressId(""); setPinned(null); resetCoverage(); }} aria-pressed={savedAddressId === ""} className={`rounded-[14px] px-4 py-2.5 text-[13px] font-semibold border transition-all flex items-center gap-1.5 ${savedAddressId === "" ? "border-[#1677F2] bg-[#F0F7FF] ring-1 ring-[#1677F2] text-[#1677F2]" : "border-transparent bg-[#F8FAFC] text-[#64748B] hover:bg-gray-100"}`}><Plus className="h-4 w-4" /> New</button>
                   </div>
                 )}

                 {!savedAddressId && usingPin && <div className="mt-2"><LocationPicker value={pinned} onChange={(v) => void onPin(v)} /></div>}
                 
                 {!savedAddressId && (isManager || !mapsUp) && (
                   <div className="space-y-3 mt-2">
                     {isManager && mapsUp && <button type="button" onClick={() => { setTypedAddress((v) => !v); setPinned(null); resetCoverage(); }} className="text-[13px] font-semibold text-[#1677F2] hover:text-blue-800 transition-colors">{typedAddress ? "Pin on the map instead" : "Type the address instead"}</button>}
                     {!usingPin && (
                       <div className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_160px]">
                         <div>
                           <p className="mb-1.5 text-[12px] font-semibold text-[#64748B] uppercase tracking-wide">Address</p>
                           <Input value={line1} onChange={(e) => setLine1(e.target.value)} error={fieldErrors.address} placeholder="Enter your complete address" className="h-[46px] text-[14px] rounded-[14px]" />
                         </div>
                         <div>
                           <p className="mb-1.5 text-[12px] font-semibold text-[#64748B] uppercase tracking-wide">Pincode</p>
                           <Input value={pincode} maxLength={10} inputMode="numeric" onChange={(e) => {
                               const value = e.target.value; setPincode(value); setCoverage("idle"); latestPincode.current = "";
                               if (/^\d{6}$/.test(value.trim())) void checkPincode(value.trim());
                             }} onBlur={() => pincode.trim().length >= 6 && checkedPincode !== pincode.trim() && void checkPincode(pincode.trim())} error={fieldErrors.pincode} placeholder="Enter pincode" className="h-[46px] text-[14px] rounded-[14px]" />
                         </div>
                       </div>
                     )}
                   </div>
                 )}

                 {coverage === "checking" && <div className="flex items-center gap-2 p-3 rounded-[12px] bg-[#F0F7FF] text-[#1677F2] border border-[#EBF4FF] mt-2"><Spinner className="h-[18px] w-[18px]" /> <span className="text-[13px] font-semibold">Checking your area...</span></div>}
                 {coverage === "uncovered" && <div className="mt-2"><CoverageLeadInline pincode={checkedPincode} prefillName={name} prefillPhone={phone} serviceInterest={allServices.map((s) => s.name).join(", ") || undefined} /></div>}
                 {fieldErrors.location && <p className="text-[13px] font-semibold text-red-500 mt-1">{fieldErrors.location}</p>}
               </div>
             )}

             {/* STEP 5 & 6: DATE/TIME & AVAILABLE SLOTS */}
             {hasServiceSelected && (
               <div className="rounded-[20px] bg-white p-4 sm:p-5 shadow-[0_4px_20px_rgba(15,35,70,0.06)] border border-gray-100 animate-in fade-in slide-in-from-bottom-4 duration-300 space-y-5" id="slots-section">
                  <div className="flex items-center gap-2 mb-2">
                    <div className="w-8 h-8 rounded-full bg-[#EBF4FF] flex items-center justify-center text-[#1677F2]"><Calendar className="h-4 w-4" /></div>
                    <h2 className="text-[20px] sm:text-[22px] font-bold text-[#0B1B3A]">When should we come?</h2>
                  </div>

                  <div className="space-y-1">
                    <Input 
                       type="date" 
                       label="Date"
                       value={date} 
                       onChange={(e) => { setDate(e.target.value); setSlot(""); }}
                       min={(() => {
                         const d = new Date();
                         return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
                       })()}
                       max={(() => {
                         const d = new Date();
                         d.setDate(d.getDate() + 14);
                         return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
                       })()}
                       placeholder="Select date"
                       className="h-[52px] text-[15px] font-medium text-[#0B1B3A] rounded-[14px] !border-gray-200 shadow-sm"
                    />
                  </div>

                  {date && (
                    <div className="pt-2">
                      <label className="mb-3 block text-sm font-semibold text-[#0B1B3A]">Time Slot</label>
                      <SlotPicker serviceCenterId={centerId} date={date} onDateChange={setDate} value={slot} onChange={setSlot} enableHold hideDate={true} />
                      {(fieldErrors.date || fieldErrors.slot) && <p className="text-[13px] font-semibold text-red-500 mt-2">{fieldErrors.date || fieldErrors.slot}</p>}
                    </div>
                  )}
               </div>
             )}

             {/* Payment & Extras */}
                 {!(planIntroNode && lines.length === 1) && planTogglesNode}
                 {payable > 0 && (
                   <div className="pt-4 mt-2">
                     <p className="mb-2 text-[12px] font-semibold text-[#64748B] uppercase tracking-wide">Payment Method</p>
                     <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                       {payOptions.filter((opt) => !onlineOnly || opt.id === "online").map((opt) => (
                           <button key={opt.id} type="button" onClick={() => setPaymentMethod(opt.id)} aria-pressed={payMethod === opt.id} className={`flex items-center gap-3 rounded-[14px] p-3 text-left border transition-all ${payMethod === opt.id ? "border-[#1677F2] bg-[#F0F7FF] ring-1 ring-[#1677F2]" : "border-gray-200 bg-white hover:border-[#1677F2]"}`}>
                             <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${payMethod === opt.id ? "bg-[#1677F2] text-white" : "bg-[#F8FAFC] text-[#64748B]"}`}><opt.icon className="h-4 w-4" /></div>
                             <div>
                               <span className={`block text-[14px] font-semibold ${payMethod === opt.id ? "text-[#0B1B3A]" : "text-[#0B1B3A]"}`}>{opt.title}</span>
                               <span className="block text-[12px] text-[#64748B]">{opt.sub}</span>
                             </div>
                           </button>
                       ))}
                     </div>
                   </div>
                 )}
                 <div>
                   <button type="button" onClick={() => setMoreOpen((v) => !v)} className="text-[13px] font-semibold text-[#1677F2] hover:text-blue-800 transition-colors mt-2">
                     {moreOpen ? "Hide note and second contact" : "+ Add a note or second contact"}
                   </button>
                   {moreOpen && (
                     <div className="mt-3 space-y-3 p-4 rounded-[16px] bg-[#F8FAFC] border border-gray-100 animate-in fade-in slide-in-from-top-2">
                       <Input label="Note for the captain" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="E.g. basement parking, gate B" className="h-[46px] text-[14px] rounded-[14px]" />
                       <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                         <Input label="Second contact name" value={altName} onChange={(e) => setAltName(e.target.value)} className="h-[46px] text-[14px] rounded-[14px]" />
                         <Input label="Second contact number" value={altPhone} inputMode="numeric" onChange={(e) => setAltPhone(cleanMobileInput(e.target.value))} error={fieldErrors.altPhone} className="h-[46px] text-[14px] rounded-[14px]" />
                       </div>
                     </div>
                   )}
                 </div>
                </div>
              )}
          </div>
          
          {/* RIGHT: COMPACT BOOKING SUMMARY */}
          <div className="sticky top-[88px] rounded-[24px] border border-gray-100 bg-white p-5 sm:p-6 shadow-[0_4px_20px_rgba(15,35,70,0.06)] h-max">
             <h3 className="text-[20px] font-bold text-[#0B1B3A] mb-5">Booking Summary</h3>
             
             {/* Dynamic Summary Layout */}
             <div className="space-y-4 mb-6">
                {/* Always show vehicle & service initially, or placeholder if empty */}
                <div className="flex items-start gap-3">
                   <div className="w-8 h-8 rounded-full bg-[#EBF4FF] flex items-center justify-center shrink-0 text-[#1677F2]"><CheckCircle2 className="w-4 h-4"/></div>
                   <div>
                     <p className="text-[12px] font-semibold text-[#64748B]">Vehicle & Service</p>
                     <p className={`text-[14px] font-semibold ${summary ? "text-[#0B1B3A]" : "text-gray-400"}`}>{summary || "Pending selection"}</p>
                   </div>
                </div>
                
                {/* Progressively show Location */}
                {hasServiceSelected && !isLog && (
                  <div className="flex items-start gap-3 animate-in fade-in duration-300">
                     <div className="w-8 h-8 rounded-full bg-[#EBF4FF] flex items-center justify-center shrink-0 text-[#1677F2]"><MapPin className="w-4 h-4"/></div>
                     <div>
                       <p className="text-[12px] font-semibold text-[#64748B]">Location</p>
                       <p className={`text-[14px] font-semibold line-clamp-2 ${savedAddressId || line1 || pinned ? "text-[#0B1B3A]" : "text-gray-400"}`}>
                         {savedAddressId && savedAddresses ? savedAddresses.find(a => a.id === savedAddressId)?.label || savedAddresses.find(a => a.id === savedAddressId)?.line1 : pinned ? pinned.formatted || pinned.area || "Selected on map" : line1 ? `${line1}, ${pincode}` : "Pending address"}
                       </p>
                     </div>
                  </div>
                )}
                
                {/* Progressively show Date & Time */}
                {hasValidLocation && !isLog && (
                  <div className="flex items-start gap-3 animate-in fade-in duration-300">
                     <div className="w-8 h-8 rounded-full bg-[#EBF4FF] flex items-center justify-center shrink-0 text-[#1677F2]"><CheckCircle2 className="w-4 h-4 opacity-50"/></div>
                     <div>
                       <p className="text-[12px] font-semibold text-[#64748B]">Date & Time</p>
                       <p className={`text-[14px] font-semibold ${date && slot ? "text-[#0B1B3A]" : "text-gray-400"}`}>
                         {date && slot ? `${new Date(date).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })} at ${slot}` : "Pending slot selection"}
                       </p>
                     </div>
                  </div>
                )}
             </div>

             <div className="border-t border-gray-100 pt-4 space-y-2 mb-6">
               {billRows.length > 0 && billRows.map((r) => (
                 <div key={r.label} className="flex justify-between text-[13px] text-[#64748B]">
                   <span>{r.label}</span>
                   <span className="font-semibold text-[#0B1B3A]">{r.value}</span>
                 </div>
               ))}
               
               <div className="flex items-end justify-between pt-1 mt-1">
                 <span className="font-semibold text-[#64748B] text-[14px]">Total Price</span>
                 <span className="text-right">
                   {struckTotal != null && <span className="mr-1.5 text-[12px] font-semibold text-gray-400 line-through">₹{struckTotal}</span>}
                   <span className={`font-bold text-[28px] ${shownTotal > 0 ? "text-[#0B1B3A]" : "text-gray-400"}`}>₹{shownTotal || 0}</span>
                 </span>
               </div>
             </div>

             {(error || quoteError) && <p className="text-center text-[13px] font-semibold text-red-500 mb-3">{error || quoteError}</p>}
             
             {step1Ready && step2Ready ? (
                <button
                  className="w-full rounded-[14px] bg-[#E8A900] h-[52px] text-[16px] font-bold text-white shadow-[0_4px_14px_rgba(232,169,0,0.25)] transition-transform hover:scale-[1.02] active:scale-[0.98] disabled:opacity-70 disabled:hover:scale-100 flex items-center justify-center gap-2"
                  disabled={submitting}
                  onClick={() => void submit()}
                >
                  {submitting ? <Spinner className="h-5 w-5 text-white" /> : null}
                  {isLog ? "Save As Done" : !isManager && payable > 0 && payMethod === "online" ? "Book And Pay →" : "Continue →"}
                </button>
             ) : (
                <button
                  className="w-full rounded-[14px] bg-[#F1F5F9] h-[52px] text-[16px] font-semibold text-[#94A3B8] cursor-not-allowed"
                  disabled
                >
                  Complete details
                </button>
             )}
          </div>
        </div>
      </div>
    </div>
    
    <Modal open={!!sentLink} onClose={() => navigate("/manager/bookings")} title="Booking created" maxWidth="max-w-sm">
      {sentLink && (
        <div className="space-y-4 p-2 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-[#F0FDF4]">
            <CheckCircle2 className="h-6 w-6 text-[#22C55E]" />
          </div>
          <div>
            <h3 className="text-[18px] font-bold text-[#0B1B3A] mb-1">Link Sent Successfully</h3>
            <p className="text-[13px] text-[#64748B]">
              {sentLink.numbers} · payment link sent to the customer on WhatsApp. It confirms once paid.
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-[14px] border border-gray-200 bg-[#F8FAFC] p-3 text-left">
            <span className="font-mono-num min-w-0 flex-1 truncate text-[13px] font-medium text-[#0B1B3A]">{sentLink.link}</span>
            <Button size="sm" variant="outline" onClick={() => void copyLink()} className="shrink-0 rounded-[10px] h-8 text-[12px]">
              <Copy className="h-3 w-3 mr-1" /> {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <Button variant="info" className="w-full font-bold rounded-[14px] h-[44px]" onClick={() => navigate("/manager/bookings")}>
            Done
          </Button>
        </div>
      )}
    </Modal>
    </>
  );
}
