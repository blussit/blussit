import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  BadgeCheck,
  Banknote,
  Bike,
  CalendarDays,
  Car,
  CheckCircle2,
  Copy,
  CreditCard,
  Gift,
  Info,
  Lock,
  MapPin,
  Plus,
  ShieldCheck,
  SprayCan,
  Trash2,
  UserRound,
} from "lucide-react";
import { bookingPolicyApi, catalogApi, coverageApi, serviceCenterApi, vehicleTypeApi, getSlotHolderKey } from "../../api/catalog";
import { bookingApi, type BookingQuotePayload, type PhoneProof, type QuickBookingLine, type QuickBookingPayload } from "../../api/booking";
import { addressApi } from "../../api/profile";
import { adminServiceCenterApi } from "../../api/admin";
import { Button, DiscountBadge, Input, Modal, OfferTag, Spinner, Switch, discountPercent } from "../ui";
import { LocationPicker, INDORE_CENTER, type LocationValue } from "../shared/LocationPicker";
import { WizardShell, WizardStepHeader } from "../shared/WizardShell";
import { ServicePrepNotice } from "../shared/ServicePrepNotice";
import { QtyStepper } from "../shared/QtyStepper";
import { CustomerNamePhoneFields } from "../shared/CustomerNamePhoneFields";
import { BookingOtpModal } from "./BookingOtpModal";
import { BookHero } from "./BookHero";
import { PickerField, PickerOption } from "./PickerField";
import { SlotBoard } from "./SlotBoard";
import { useSlotHold } from "./useSlotHold";
import { CARD, CTA, FIELD, dayParts, vehicleLabel, vehicleMeta } from "./bookingTheme";
import { CoverageLeadInline } from "../public/CoverageLeadInline";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { scrollToTopNow } from "../../lib/scroll";
import { ensureGoogleMaps } from "../../lib/googleMaps";
import { daysAgoIST, formatSlot, formatTime12, nowTimeIST, todayIST } from "../../lib/date";
import { validateIndianMobile, cleanMobileInput } from "../../lib/validators";
import { addonKit, baseGroups, bikeTypeIds, eligibleFor, variantCount, type BaseGroup } from "../../lib/serviceMix";
import { INR, parseIncludes, priceForType, priceView, titleCase } from "../public/landing/shared";
import { subscriptionApi } from "../../api/engagement";
import type { Address, Service, TravelQuote, UserSubscription, VehicleTypeOption } from "../../types";

/**
 * THE booking flow (2026-09 quick-booking model) — two steps, no account;
 * anonymous bookings end with a one-time-code popup (BookingOtpModal).
 *
 * Customers (public + customer modes) get the v2 page from the approved
 * mockup (figmav2bookingpage.jpeg):
 *   1. Book Your Car Wash — Car Type · Service · Date & Time pickers, the
 *      day strip + slot cards, and a Booking Summary with the server's
 *      price → Continue. Slots show before any address: they are the slots
 *      of the center serving central Indore (or the customer's saved
 *      address), and the seat is held from the moment it's picked.
 *   2. Confirm — address (saved one preselected, else the pin), name +
 *      phone for a guest, how to pay when there's a choice → Book
 *      (→ OTP popup for a guest). The address's own coverage check decides
 *      the center; if that moves the visit to a center where the picked
 *      time isn't open, the slots come back on this step to re-pick.
 * Managers keep the two-step wizard (vehicles → who/where/when/pay).
 *
 * One component, four seats:
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
 * (customer) opens the confirm step straight away with that plan's vehicle
 * type + service and the slots on it, ?repeat=<id> replays a past visit,
 * ?type=<vehicleTypeId> preselects that car type (active types only).
 *
 * Bikes keep the catalogue's per-bike pricing (base wash + N extra bikes
 * on ONE booking); every car of a type is its own booking on the visit —
 * exactly what the backend's group model expects.
 */
/** "manager-log": a job the manager already did himself — same vehicle/service
 *  picker, then who/where/when as it HAPPENED, saved directly as done. */
type Mode = "public" | "customer" | "manager" | "manager-log";

/** The v2 palette for shared pieces that read theme variables (spinners,
 *  the OTP popup, the coverage lead form) inside the customer flow. */
const V2_THEME = {
  "--color-primary": "#0A66F0",
  "--color-primary-dark": "#0852C4",
  "--color-primary-light": "#E8F0FE",
  "--color-secondary": "#FFD21F",
  "--color-secondary-light": "#FFF6D6",
  "--color-text-primary": "#0E1A33",
  "--color-text-secondary": "#5F6878",
} as CSSProperties;

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

// Manager / log wizard only (the customer flows use the v2 page below).
/** The wizard's one key action — the v2 yellow CTA (navy text). */
const MGR_CTA =
  "inline-flex h-11 min-w-[150px] items-center justify-center gap-2 rounded-[12px] bg-[#FFD21F] px-5 text-sm font-bold text-[#0E1A33] shadow-[0_8px_20px_-12px_rgba(232,169,0,0.8)] transition hover:bg-[#FFC800] disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none";
const SECTION_LABEL = "mb-2 text-sm font-semibold text-[#0E1A33]";

/** Every pickable box and chip in the manager wizard — the v2 booking look:
 *  blue border + light blue tint when chosen, a quiet hairline otherwise. */
function choiceClass(on: boolean): string {
  return `border transition-colors ${
    on ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0E1A33] ring-1 ring-[#0A66F0]" : "border-[#E4E9F1] bg-white text-[#0E1A33] hover:border-[#C9D6EA]"
  }`;
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

export function QuickBookFlow({ mode, layout }: { mode: Mode; layout?: "page" | "app" }) {
  const navigate = useNavigate();
  const location = useLocation();
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
  const { data: myAddresses, isError: addressesFailed } = useQuery({ queryKey: ["addresses"], queryFn: addressApi.list, enabled: isCustomer });
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
  // Customer flows: the slots also show on the confirm step — when it was
  // opened without one (a plan's "Book now") or the address moved the
  // visit to a center where the picked time isn't open.
  const [confirmSlots, setConfirmSlots] = useState(false);

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
  // Per signed-in person: on a shared device the next login must never see
  // the previous person's pin, phone or notes (logout also wipes these).
  const storageKey = `blussit:quickbook:${mode}:${user?.id || "guest"}`;
  const repeatId = searchParams.get("repeat");
  const serviceParam = searchParams.get("service") || searchParams.get("serviceId");
  const subscriptionParam = isCustomer ? searchParams.get("subscription") : null;
  // ?type=<vehicleTypeId> (garage "Book wash", home "Clean again"): that car type preselected.
  const typeParam = isManager ? null : searchParams.get("type");
  const [restored, setRestored] = useState(false);
  const restoreAddressRef = useRef<null | { pinned: LocationValue | null; savedAddressId: string | null; pincode: string; typed: boolean; slot: string }>(null);
  const skipAutoDateRef = useRef(false);
  useEffect(() => {
    // A deep link starts a fresh booking instead of resuming the last one.
    if (repeatId || serviceParam || subscriptionParam || typeParam) {
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
        setConfirmSlots(!!saved.confirmSlots);
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
          // Customer flows keep their slot through an address change; the
          // console wizard clears it with the address and puts it back after.
          slot: isManager ? saved.slot || "" : "",
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
        JSON.stringify({ at: Date.now(), step, added, draft, planIntro, name, phone, pickedCustomerId, savedAddressId, pinned, typedAddress, line1, pincode, date, slot, confirmSlots, paymentMethod, couponCode, notes, altName, altPhone, logTime, sendWhatsApp, discount })
      );
    } catch {
      // storage unavailable — nothing to keep
    }
  }, [restored, repeatId, storageKey, step, added, draft, planIntro, name, phone, pickedCustomerId, savedAddressId, pinned, typedAddress, line1, pincode, date, slot, confirmSlots, paymentMethod, couponCode, notes, altName, altPhone, logTime, sendWhatsApp, discount]);

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
      // The last vehicle sits in the editor (its pickers show it), the rest
      // as added lines — a one-car repeat looks exactly like a fresh pick.
      setAdded(drafts.slice(0, -1));
      setDraft(drafts[drafts.length - 1]);
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
    if (deepLinkDone.current || (!serviceParam && !subscriptionParam && !typeParam) || !services.length || !types.length) return;
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
          setConfirmSlots(true);
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
      const svc = serviceParam ? mains.find((s) => s.slug === serviceParam) || mains.find((s) => s.id === serviceParam) : undefined;
      const key = svc ? svc.variant_group || svc.id : null;
      if (key) setPreferredBase(key);
      // Only an active vehicle type counts (`types` holds just those); anything else is ignored.
      const linkedType = typeParam && types.some((t) => t.id === typeParam) ? typeParam : "";
      if (linkedType) {
        const sold = !!key && baseGroups(services, linkedType).some((g) => g.key === key);
        setDraft({ typeId: linkedType, count: 1, base: sold ? key : null, addons: [] });
      } else if (svc && key) {
        const eligible = types.filter((t) => eligibleFor(svc, t.id));
        if (eligible.length === 1) setDraft({ typeId: eligible[0].id, count: 1, base: key, addons: [] });
      }
    }
    const next = new URLSearchParams(searchParams);
    ["service", "serviceId", "subscription", "type"].forEach((k) => next.delete(k));
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serviceParam, subscriptionParam, typeParam, services, types, myPasses, passesFailed]);

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

  const summary = lines.map((l) => `${l.count} ${titleCase(l.type.name)}${l.base ? ` · ${titleCase(l.base.name)}` : ""}`).join("  +  ");
  const lineLabel = (l: Line) => `${l.count > 1 ? `${l.count} × ` : ""}${titleCase(l.type.name)}${l.base ? ` · ${titleCase(l.base.name)}` : ""}`;

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
        const plan = titleCase(match.subs[0].plan_name) || "Plan";
        const left = match.subs[0].remaining_service_count;
        const units = line.payload?.quantity ?? 1;
        return (
          <Switch
            key={i}
            checked={match.on}
            onChange={(next) => setSubscriptionOverride((prev) => ({ ...prev, [i]: next }))}
            label={`${isCustomer ? "Use My" : "Use Their"} ${plan} — ${left} Wash${left === 1 ? "" : "es"} Left`}
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
  // Customer flows: as soon as the vehicles are picked (the summary shows
  // the server's price from the first screen). Console: on step 2.
  const quoteWanted = !!quoteRequest && (step === 1 || !isManager);
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
    { id: "cash", icon: Banknote, title: isManager ? "Cash" : "Pay After The Wash", sub: isManager ? "Collected at the visit" : "Cash or UPI to the captain" },
    { id: "online", icon: CreditCard, title: isManager ? "Online" : "Pay Online Now", sub: isManager ? "Customer pays online" : "UPI, card or netbanking" },
  ] as const;
  // Log mode only: rupees the manager took off the bill. What the customer
  // actually paid (finalTotal) drives the footer, the payment choice and the save.
  const discountNum = isLog ? Math.round(Number(discount) || 0) : 0;
  const finalTotal = Math.max(0, displayTotal - discountNum);
  const allServices = lines.flatMap((l) => l.services);
  const step1Ready = lines.length > 0 && lines.every((l) => !!l.base) && (!draft.typeId || draftReady);

  const otherVehicles = added.reduce((n, d) => n + d.count, 0);
  /** A new vehicle type keeps the chosen service when it's sold for that
   *  type (else the deep-linked one); the console wizard falls back to the
   *  first service so its dropdown pick is already a bookable line. Returns
   *  the service it landed on. */
  const pickType = (typeId: string): string | null => {
    const groups = baseGroups(services, typeId);
    const keep = draft.base ?? preferredBase;
    const base = (groups.find((g) => g.key === keep) || (isManager ? groups[0] : undefined))?.key ?? null;
    const sameType = draft.typeId === typeId;
    setDraft({ typeId, count: sameType ? draft.count : 1, base, addons: sameType && base === draft.base ? draft.addons : [] });
    return base;
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
  // The console wizard picks the slot AFTER the address, so a new address
  // clears it; customer flows pick it first and keep it (the slot hold
  // re-checks it against whichever center the address resolves to).
  const clearSlotForAddress = () => {
    if (isManager) setSlot("");
  };
  const resetCoverage = () => {
    addressSeq.current += 1;
    setCoverage("idle");
    setCheckedPincode("");
    setCenterId("");
    clearSlotForAddress();
    setTravel(null);
  };

  async function chooseSavedAddress(a: Address) {
    const seq = ++addressSeq.current;
    setSavedAddressId(a.id);
    setPinned(null);
    setCoverage("checking");
    setCheckedPincode(a.pincode);
    setCenterId("");
    clearSlotForAddress();
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
    clearSlotForAddress();
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
  // Customer typed a pincode, but the service area is drawn as zones: only
  // a map pin can be verified (the server refuses a pinless customer
  // booking — PIN_REQUIRED), so say so instead of a dead-end "covered".
  const [pinRequired, setPinRequired] = useState(false);
  async function checkPincode(pin: string) {
    const seq = ++addressSeq.current;
    latestPincode.current = pin;
    setCoverage("checking");
    setCheckedPincode(pin);
    setCenterId("");
    setPinRequired(false);
    clearSlotForAddress();
    setTravel(null);
    if (!isManager) {
      try {
        const result = (await coverageApi.check({ pincode: pin })) as Awaited<ReturnType<typeof coverageApi.check>> & { pin_required?: boolean };
        if (latestPincode.current !== pin || seq !== addressSeq.current) return;
        if (result.covered && result.center) {
          setCenterId(result.center.id);
          setCenterCity(result.center.city || "");
          setCenterState(result.center.state || "");
          setCoverage("covered");
        } else {
          setPinRequired(!!result.pin_required);
          setCoverage("uncovered");
        }
      } catch {
        if (latestPincode.current === pin && seq === addressSeq.current) setCoverage("uncovered");
      }
      return;
    }
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
    if (!isManager || coverage !== "covered" || !centerId || autoDatedFor === centerId) return;
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
  // A signed-in customer with NO phone yet (Google sign-in) proves the number
  // they type too — the server refuses to attach an unproven number.
  const needsOtp = !isManager && (!user || user.role !== "customer" || !user.phone || forceOtp);

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
        // A REUSED code (kept from an earlier attempt that the server
        // refused after spending it — e.g. the slot filled) isn't the
        // customer's mistake: reopen with no error so a fresh code is sent.
        setOtpError(!freshProof && /^Invalid or expired code/.test(message) ? "" : message);
        setOtpOpen(true);
      } else {
        setError(message);
        // The time itself went (filled up, closed, past its cutoff): drop
        // it and show the slots again right here, fresh from the server.
        if (/fully booked|no longer available|closed for booking|pick another slot/i.test(message)) {
          setSlot("");
          if (!isManager) setConfirmSlots(true);
          queryClient.invalidateQueries({ queryKey: ["available-slots"] });
        }
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
  if (planSavings > 0) billRows.push({ label: isCustomer ? "Covered By Your Plan" : "Covered By Plan", value: `−₹${planSavings}` });
  if (couponSavings > 0) billRows.push({ label: `Coupon ${liveQuote?.coupon_code || ""}`.trim(), value: `−₹${couponSavings}` });
  if (travelDue && travelQuote)
    billRows.push(
      travelQuote.charge > 0
        ? { label: `Distance Charge · ${travelBeyondKm} km`, value: `₹${travelQuote.charge}` }
        : { label: "Distance Charge", value: `Free — within ${travelQuote.free_km} km` }
    );
  if (discountNum > 0 && discountNum <= displayTotal) billRows.push({ label: "Discount", value: `−₹${discountNum}` });
  const shownTotal = isLog ? finalTotal : payable;
  // The first-wash price struck against what a returning customer pays.
  const struckTotal = showFirstWash && regularTotal > total ? shownTotal + (regularTotal - total) : null;

  const footer = (
    <div className="space-y-3">
      {billRows.length > 0 && (
        <div className="space-y-1 text-sm text-[#5F6878]">
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
        <span className="min-w-0 flex-1 truncate text-sm text-[#5F6878]">{billRows.length ? "Total" : summary || "Pick Your Vehicle"}</span>
        <span className="text-right">
          {struckTotal != null && <span className="mr-1.5 text-xs text-[#9AA3B2] line-through">₹{struckTotal}</span>}
          <span className="font-mono-num text-xl font-bold text-[#0E1A33]">₹{shownTotal}</span>
        </span>
      </div>
      {!isLog && (
        <p className="flex items-center gap-1.5 text-xs text-[#5F6878]">
          <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-[#16A34A]" aria-hidden="true" />
          {liveQuote ? (liveQuote.travel_pending ? "Any distance charge shows once you add the address." : "Price shown is final — no hidden charges.") : coverage !== "covered" && billed.some((s) => s.charges_travel) ? "Any distance charge shows once you add the address." : quoting ? "Checking the price…" : "Estimated price — confirmed before you book."}
        </p>
      )}
      {(error || quoteError) && <p className="text-right text-xs font-medium text-[var(--color-error)]">{error || quoteError}</p>}
      <div className="flex items-center justify-between gap-3">
        <Button variant="outline" className="rounded-[12px]" disabled={submitting} onClick={() => (step > 0 ? setStep(0) : navigate(-1))}>
          Back
        </Button>
        {step === 0 ? (
          <button
            type="button"
            className={MGR_CTA}
            disabled={!step1Ready}
            onClick={() => {
              setPlanIssue("");
              setStep(1);
            }}
          >
            Continue
          </button>
        ) : (
          <button type="button" className={MGR_CTA} disabled={!step2Ready || submitting} aria-busy={submitting} onClick={() => void submit()}>
            {submitting && <Spinner className="h-4 w-4" />}
            {isLog ? "Save As Done" : !isManager && payable > 0 && payMethod === "online" ? "Book And Pay" : "Book Now"}
          </button>
        )}
      </div>
      {step === 0 && !step1Ready && (draft.typeId || added.length > 0) && (
        <p className="text-right text-xs text-[var(--color-error)]">Pick a service to continue.</p>
      )}
    </div>
  );

  // ---- customer flows: the v2 page -------------------------------------------
  const pageLayout = (layout ?? (mode === "public" ? "page" : "app")) === "page";
  const [openPicker, setOpenPicker] = useState<"type" | "service" | null>(null);
  const [slotNotice, setSlotNotice] = useState("");
  const [slotCenter, setSlotCenter] = useState("");
  const slotsRef = useRef<HTMLDivElement>(null);
  const stepRef = useRef(step);
  stepRef.current = step;

  // Slots before any address: the center that serves central Indore. A
  // customer's saved address (or a pin) replaces it with their own center
  // as soon as its coverage check answers.
  const { data: previewCenter, isFetched: previewFetched } = useQuery({
    queryKey: ["preview-center"],
    queryFn: async () => {
      const r = await coverageApi.check({ latitude: INDORE_CENTER.lat, longitude: INDORE_CENTER.lng });
      return r.covered && r.center ? r.center.id : "";
    },
    enabled: !isManager,
    staleTime: 30 * 60 * 1000,
    retry: 1,
  });
  // A signed-in customer's saved address is about to answer — wait for it
  // instead of flashing another center's slots first.
  const savedPending =
    isCustomer &&
    (myAddresses === undefined ? !addressesFailed : myAddresses.length > 0 && savedAddressId !== "" && (coverage === "idle" || coverage === "checking"));
  useEffect(() => {
    if (isManager) return;
    if (coverage === "covered" && centerId) {
      setSlotCenter(centerId);
      return;
    }
    // Otherwise keep whatever the slot was picked on while an address is (re)checked.
    if (!slotCenter && !savedPending && previewCenter) setSlotCenter(previewCenter);
  }, [isManager, coverage, centerId, slotCenter, savedPending, previewCenter]);
  const slotsNeedAddress = !isManager && !slotCenter && !savedPending && previewFetched && !previewCenter;

  const hold = useSlotHold({
    centerId: slotCenter,
    date,
    slot,
    enabled: !isManager,
    onLost: (message) => {
      setSlot("");
      setSlotNotice(message);
      if (stepRef.current === 1) setConfirmSlots(true);
    },
  });
  // The manager wizard holds its slot the same way (it used to sit inside
  // the old SlotPicker; the v2 SlotBoard leaves holding to the flow).
  const [mgrSlotNotice, setMgrSlotNotice] = useState("");
  const mgrHold = useSlotHold({
    centerId,
    date,
    slot,
    enabled: mode === "manager" && coverage === "covered" && !!centerId,
    onLost: (message) => {
      setSlot("");
      setMgrSlotNotice(message);
    },
  });
  const pickMgrSlot = (key: string) => {
    if (key === slot) {
      mgrHold.reclaim();
      return;
    }
    setSlot(key);
    setMgrSlotNotice("");
    setFieldErrors((prev) => {
      const next = { ...prev };
      delete next.slot;
      return next;
    });
  };
  const pickSlot = (key: string) => {
    if (key === slot) {
      hold.reclaim();
      return;
    }
    setSlot(key);
    setSlotNotice("");
    setFieldErrors((prev) => {
      const next = { ...prev };
      delete next.slot;
      return next;
    });
  };

  // The confirm step is its own history entry (#confirm), so a phone's Back
  // button returns to the slots instead of leaving the page.
  const CONFIRM_HASH = "#confirm";
  const pushedConfirm = useRef(false);
  const prevHash = useRef(location.hash);
  const canConfirm = useRef(false);
  canConfirm.current = step1Ready && !!slot;
  useEffect(() => {
    const was = prevHash.current;
    prevHash.current = location.hash;
    if (isManager || was === location.hash) return;
    if (was === CONFIRM_HASH && stepRef.current === 1) {
      // Back
      pushedConfirm.current = false;
      setStep(0);
      setConfirmSlots(false);
    } else if (location.hash === CONFIRM_HASH && stepRef.current === 0 && canConfirm.current) {
      // Forward again
      pushedConfirm.current = true;
      setStep(1);
    }
  }, [location.hash, isManager]);

  const scrollToFirstError = () =>
    requestAnimationFrame(() => document.querySelector('[data-field-error="true"]')?.scrollIntoView({ behavior: "smooth", block: "center" }));
  const jumpToSlots = () => slotsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });

  const goConfirm = () => {
    if (!draft.typeId && !added.length) return setOpenPicker("type");
    if (draft.typeId && !draft.base) return setOpenPicker("service");
    if (!step1Ready) return;
    if (!slot) {
      setFieldErrors((prev) => ({ ...prev, slot: slotsNeedAddress ? "Add your location to see the open times." : "Pick a time slot." }));
      jumpToSlots();
      return;
    }
    setPlanIssue("");
    setConfirmSlots(false);
    setFieldErrors({});
    setStep(1);
    if (location.hash !== CONFIRM_HASH) {
      pushedConfirm.current = true;
      navigate({ search: location.search, hash: CONFIRM_HASH });
    }
  };
  const goBack = () => {
    if (pushedConfirm.current && location.hash === CONFIRM_HASH) {
      navigate(-1);
      return;
    }
    setStep(0);
    setConfirmSlots(false);
    if (location.hash === CONFIRM_HASH) navigate({ search: location.search, hash: "" }, { replace: true });
  };
  const confirmBooking = () => {
    if (!validateStep2()) {
      if (!slot) setConfirmSlots(true);
      scrollToFirstError();
      return;
    }
    void submit();
  };

  // Every service sold, variants collapsed — for a service picked before the car type.
  const allGroups = useMemo(() => {
    const seen = new Map<string, BaseGroup>();
    const out: BaseGroup[] = [];
    for (const s of services) {
      if (s.is_addon) continue;
      const key = s.variant_group || s.id;
      const existing = seen.get(key);
      if (existing) {
        existing.variants.push(s);
        continue;
      }
      const g: BaseGroup = { key, label: s.variant_group ? s.name.split("(")[0].trim() : s.name, primary: s, variants: [s] };
      seen.set(key, g);
      out.push(g);
    }
    for (const g of out) {
      g.variants.sort((a, b) => variantCount(a) - variantCount(b));
      g.primary = g.variants[0];
    }
    return out;
  }, [services]);

  const pickService = (key: string) => {
    setPreferredBase(key);
    if (draft.typeId) setDraft((d) => ({ ...d, base: key, addons: [] }));
  };

  // ---- shared bits of the v2 page -------------------------------------------
  const editingType = types.find((t) => t.id === draft.typeId) || null;
  const firstType = lines[0]?.type || editingType;
  const heroVehicle = vehicleMeta(firstType);
  const serviceGroupLabel = (key: string | null) => {
    const g = (draft.typeId ? editingGroups : allGroups).find((x) => x.key === key) || allGroups.find((x) => x.key === key);
    return g ? titleCase(g.label) : "";
  };
  const shownServiceKey = draft.base ?? (!draft.typeId ? preferredBase : null);
  const today = todayIST();
  const dayText = (() => {
    const { top, bottom } = dayParts(date, today);
    return `${top}, ${bottom}`;
  })();
  const whenText = slot ? `${dayText} · ${formatSlot(slot)}` : "";
  const savedAddress = savedAddressId && savedAddresses ? savedAddresses.find((a) => a.id === savedAddressId) : undefined;
  const addressText = savedAddress
    ? `${savedAddress.label} · ${savedAddress.line1}`
    : pinned
      ? pinned.formatted || [pinned.area, pinned.city].filter(Boolean).join(", ")
      : !mapsUp && line1.trim()
        ? `${line1.trim()}${pincode.trim() ? `, ${pincode.trim()}` : ""}`
        : "";
  const lineTotal = (i: number) => liveQuote?.lines.find((l) => l.line_index === i)?.total ?? lineCost(i);
  const lineService = (l: Line) => (l.base ? `${titleCase(l.isBike ? l.groups.find((g) => g.key === l.draft.base)?.label || l.base.name : l.base.name)}${l.addons.length ? ` + ${l.addons.map((a) => titleCase(a.name)).join(", ")}` : ""}` : "");
  const pricePending = lines.length > 0 && !liveQuote && !quoteFailure;
  const priceNote = !lines.length
    ? "Pick your car type and service to see the price."
    : liveQuote?.travel_pending || (!liveQuote && coverage !== "covered" && billed.some((s) => s.charges_travel))
      ? "Any distance charge shows once you add the address."
      : liveQuote
        ? "Price shown is final — no hidden charges."
        : // No live server quote (still loading, or it failed): this is
          // the local estimate — never call it final.
          "Estimated price — confirmed before you book.";
  const bookLabel = payable > 0 && payMethod === "online" ? `Book & Pay ${INR(payable)}` : "Confirm Booking";

  const billBlock = (
    <div className="space-y-2">
      {lines.length > 0 && billRows.length > 0 && (
        <div className="space-y-1.5 text-[13px] text-[#5F6878]">
          <div className="flex justify-between gap-3">
            <span>{lines.length > 1 ? "Services" : "Service"}</span>
            <span className="font-medium text-[#0E1A33]">{INR(total)}</span>
          </div>
          {billRows.map((r) => (
            <div key={r.label} className="flex justify-between gap-3">
              <span className="min-w-0">{r.label}</span>
              <span className="shrink-0 font-medium text-[#0E1A33]">{r.value}</span>
            </div>
          ))}
        </div>
      )}
      <div className="flex items-end justify-between gap-3">
        <span className="pb-1 text-[14px] font-semibold text-[#0E1A33]">{step === 1 ? "Total" : "Estimated Price"}</span>
        <span className="text-right">
          {!pricePending && struckTotal != null && <span className="mr-2 text-[14px] text-[#9AA3B2] line-through">{INR(struckTotal)}</span>}
          {pricePending ? (
            <span className="inline-block h-8 w-20 animate-pulse rounded-[8px] bg-[#EEF1F5] align-bottom" aria-label="Checking the price" />
          ) : (
            <span className={`font-display text-[30px] font-extrabold leading-none tracking-[-0.02em] ${lines.length ? "text-[#0E1A33]" : "text-[#C3CBD8]"}`}>{lines.length ? INR(shownTotal) : "—"}</span>
          )}
        </span>
      </div>
      <p className="flex items-start gap-1.5 text-[12px] text-[#5F6878]">
        <ShieldCheck className="mt-px h-3.5 w-3.5 shrink-0 text-[#12A150]" aria-hidden="true" />
        {priceNote}
      </p>
      {(error || quoteError) && <p className="text-[12px] font-medium text-[var(--color-error)]">{error || quoteError}</p>}
    </div>
  );

  const ctaButton =
    step === 0 ? (
      <button type="button" className={CTA} onClick={goConfirm}>
        Continue <ArrowRight className="h-5 w-5" />
      </button>
    ) : (
      <button type="button" className={CTA} disabled={submitting} onClick={confirmBooking}>
        {submitting ? <Spinner className="h-5 w-5" /> : null}
        {bookLabel} {!submitting && <ArrowRight className="h-5 w-5" />}
      </button>
    );
  const secureNote = (
    <p className="flex items-center justify-center gap-1.5 text-[12px] text-[#5F6878]">
      <Lock className="h-3.5 w-3.5" /> Secure &amp; Safe Booking
    </p>
  );

  const summaryRow = (icon: ReactNode, label: string, value: ReactNode, muted = false) => (
    <div className="flex items-start gap-3">
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] bg-[#EEF3FA] text-[#0A66F0]">{icon}</span>
      <div className="min-w-0 flex-1">
        <p className="text-[12px] text-[#5F6878]">{label}</p>
        <div className={`text-[14px] font-semibold leading-snug ${muted ? "text-[#9AA3B2]" : "text-[#0E1A33]"}`}>{value}</div>
      </div>
    </div>
  );

  const summaryCard = (
    <aside className={`${CARD} p-5`}>
      <h2 className="font-display text-[19px] font-bold text-[#0E1A33]">Booking Summary</h2>
      <div className="mt-4 flex h-[128px] items-center justify-center overflow-hidden rounded-[14px] bg-gradient-to-b from-[#F5F8FF] to-[#E6EEFC]">
        <img src={heroVehicle.image} alt={firstType?.name || "Your vehicle"} loading="lazy" className={`max-h-[108px] max-w-[84%] object-contain drop-shadow-[0_10px_12px_rgba(14,26,51,0.18)] ${firstType ? "" : "opacity-40 grayscale"}`} />
      </div>
      <div className="mt-4 space-y-3.5">
        {lines.length > 1 ? (
          summaryRow(
            <Car className="h-4 w-4" />,
            `Vehicles · ${totalVehicles}`,
            <ul className="space-y-1">
              {lines.map((l, i) => (
                <li key={i} className="flex justify-between gap-2">
                  <span className="min-w-0">
                    {lineLabel(l).split(" · ")[0]}
                    <span className="block text-[12px] font-normal text-[#5F6878]">{lineService(l)}</span>
                  </span>
                  <span className="shrink-0">{coveredUnits[i] > 0 && lineTotal(i) === 0 ? "Covered" : INR(lineTotal(i))}</span>
                </li>
              ))}
            </ul>
          )
        ) : (
          <>
            {summaryRow(
              firstType && vehicleMeta(firstType).bike ? <BikeIcon className="h-4 w-4" /> : <Car className="h-4 w-4" />,
              "Car Type",
              firstType ? `${lines[0]?.count > 1 ? `${lines[0].count} × ` : ""}${vehicleLabel(firstType)}` : "Not Selected",
              !firstType
            )}
            {summaryRow(<SprayCan className="h-4 w-4" />, "Service", lines[0] ? lineService(lines[0]) : serviceGroupLabel(shownServiceKey) || "Not Selected", !lines[0])}
          </>
        )}
        {summaryRow(<CalendarDays className="h-4 w-4" />, "Date & Time", whenText || "Not Selected", !whenText)}
        {step === 1 && summaryRow(<MapPin className="h-4 w-4" />, "Address", addressText || "Add Your Address", !addressText)}
      </div>
      <div className="my-4 h-px bg-[#EEF1F5]" />
      {billBlock}
      <div className="mt-4">{ctaButton}</div>
      <div className="mt-3">{secureNote}</div>
    </aside>
  );

  // Where — saved address chips (preselected), else the pin.
  const addressBlock = (
    <div className="space-y-3">
      {!!savedAddresses?.length && (
        <div className="grid grid-cols-1 gap-2 @lg:grid-cols-2">
          {savedAddresses.map((a) => {
            const on = savedAddressId === a.id;
            return (
              <button
                key={a.id}
                type="button"
                onClick={() => void chooseSavedAddress(a)}
                aria-pressed={on}
                className={`flex min-w-0 items-start gap-3 rounded-[14px] border p-3 text-left transition ${on ? "border-[#0A66F0] bg-[#F3F7FF] ring-1 ring-[#0A66F0]" : "border-[#E4E9F1] bg-white hover:border-[#0A66F0]/50"}`}
              >
                <MapPin className={`mt-0.5 h-4 w-4 shrink-0 ${on ? "text-[#0A66F0]" : "text-[#9AA3B2]"}`} />
                <span className="min-w-0">
                  <span className="block text-[14px] font-semibold text-[#0E1A33]">{a.label}</span>
                  <span className="block truncate text-[12px] text-[#5F6878]">
                    {a.line1} · {a.pincode}
                  </span>
                </span>
              </button>
            );
          })}
          <button
            type="button"
            onClick={() => {
              setSavedAddressId("");
              setPinned(null);
              resetCoverage();
            }}
            aria-pressed={savedAddressId === ""}
            className={`flex items-center gap-2 rounded-[14px] border border-dashed p-3 text-[14px] font-semibold transition ${savedAddressId === "" ? "border-[#0A66F0] bg-[#F3F7FF] text-[#0A66F0]" : "border-[#C9D3E2] text-[#0A66F0] hover:bg-[#F6F8FC]"}`}
          >
            <Plus className="h-4 w-4" /> New Address
          </button>
        </div>
      )}
      {!savedAddressId && usingPin && <LocationPicker value={pinned} onUnavailable={() => setMapsUp(false)} onChange={(v) => void onPin(v)} height="11rem" />}
      {!savedAddressId && !mapsUp && (
        <div className="grid grid-cols-1 gap-3 @lg:grid-cols-[1fr_150px]">
          <label className="block">
            <span className="mb-1.5 block text-[12px] font-medium text-[#5F6878]">Address</span>
            <input className={FIELD} value={line1} onChange={(e) => setLine1(e.target.value)} placeholder="House / flat, street, area" />
            {fieldErrors.address && (
              <span data-field-error="true" className="mt-1 block text-[12px] font-medium text-[var(--color-error)]">
                {fieldErrors.address}
              </span>
            )}
          </label>
          <label className="block">
            <span className="mb-1.5 block text-[12px] font-medium text-[#5F6878]">Pincode</span>
            <input
              className={FIELD}
              value={pincode}
              maxLength={6}
              inputMode="numeric"
              placeholder="452001"
              onChange={(e) => {
                const value = e.target.value.replace(/\D/g, "");
                setPincode(value);
                setCoverage("idle");
                latestPincode.current = "";
                if (/^\d{6}$/.test(value)) void checkPincode(value);
              }}
            />
            {fieldErrors.pincode && (
              <span data-field-error="true" className="mt-1 block text-[12px] font-medium text-[var(--color-error)]">
                {fieldErrors.pincode}
              </span>
            )}
          </label>
        </div>
      )}
      {coverage === "checking" && (
        <p className="flex items-center gap-2 text-[13px] text-[#5F6878]">
          <Spinner className="h-4 w-4" /> Checking your area…
        </p>
      )}
      {coverage === "covered" && !savedAddress && (
        <p className="flex items-start gap-1.5 text-[13px] text-[#12804A]">
          <BadgeCheck className="mt-px h-4 w-4 shrink-0" />
          <span>
            <span className="font-semibold">We come here.</span>
            {pinned && <span className="text-[#5F6878]"> {pinned.formatted || [pinned.area, pinned.city].filter(Boolean).join(", ")}</span>}
          </span>
        </p>
      )}
      {coverage === "uncovered" && pinRequired && (
        <p data-field-error="true" className="text-[12px] font-medium text-[var(--color-error)]">
          Please pin your exact location on the map — we can't confirm the area from a pincode alone.
        </p>
      )}
      {coverage === "uncovered" && !pinRequired && (
        <CoverageLeadInline pincode={checkedPincode} prefillName={name} prefillPhone={phone} serviceInterest={allServices.map((s) => s.name).join(", ") || undefined} />
      )}
      {fieldErrors.location && (
        <p data-field-error="true" className="text-[12px] font-medium text-[var(--color-error)]">
          {fieldErrors.location}
        </p>
      )}
    </div>
  );

  const slotsBlock = (
    <>
      {slotsNeedAddress ? (
        <div className="rounded-[14px] border border-dashed border-[#C9D3E2] p-4">
          <p className="text-[14px] font-semibold text-[#0E1A33]">Where Should We Come?</p>
          <p className="mb-3 text-[13px] text-[#5F6878]">Set your location to see the open times near you.</p>
          {addressBlock}
        </div>
      ) : slotCenter ? (
        <SlotBoard centerId={slotCenter} date={date} onDateChange={setDate} value={slot} onChange={pickSlot} maxAdvanceDays={policy?.max_advance_days} heldSeconds={hold.secondsLeft} notice={slotNotice} />
      ) : (
        <div className="space-y-3">
          <div className="flex gap-2">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="h-[58px] w-[76px] shrink-0 animate-pulse rounded-[12px] bg-[#F1F4F9]" />
            ))}
          </div>
          <div className="h-[54px] animate-pulse rounded-[14px] bg-[#F1F4F9]" />
        </div>
      )}
      {fieldErrors.slot && (
        <p data-field-error="true" className="mt-2 text-[12px] font-medium text-[var(--color-error)]">
          {fieldErrors.slot}
        </p>
      )}
    </>
  );

  // ---- console wizard (manager + log) -----------------------------------------
  if (isManager) {
    return (
      <>
        <WizardShell
          title={isLog ? "Log A Completed Job" : "New Booking"}
          steps={isLog ? LOG_STEPS : STEPS}
          current={step}
          onStepClick={(i) => i < step && setStep(i)}
          footer={footer}
        >
          {/* ---------------- STEP 1 ---------------- */}
          {step === 0 && (
            <div className="space-y-5">
              <WizardStepHeader title={(isLog ? LOG_STEPS : STEPS)[0]} />

              {/* Vehicles already on the visit */}
              {added.length > 0 && (
                <div className="rounded-xl border border-[#E4E9F1] p-3.5">
                  <p className="text-sm font-medium text-[#5F6878]">
                    On This Visit · {totalVehicles} Of {maxVehicles}
                  </p>
                  <div className="mt-2 space-y-1.5">
                    {added.map((d, i) => {
                      const li = lines.findIndex((x) => x.draft === d);
                      const l = lines[li];
                      if (!l) return null;
                      const cost = lineCost(li);
                      return (
                        <div key={`${d.typeId}-${i}`} className="flex items-center gap-2 text-sm">
                          <span className="min-w-0 flex-1 truncate text-[#5F6878]">
                            <span className="font-medium text-[#0E1A33]">{lineLabel(l)}</span>
                            {l.addons.length ? ` + ${l.addons.map((x) => titleCase(x.name)).join(", ")}` : ""}
                          </span>
                          <span className="font-mono-num shrink-0 text-[#5F6878]">{coveredUnits[li] > 0 && cost === 0 ? "Covered" : `₹${cost}`}</span>
                          <button type="button" onClick={() => editAdded(i)} className="shrink-0 text-xs font-semibold text-[#5F6878] underline underline-offset-2 hover:text-[#0A66F0]">
                            Edit
                          </button>
                          <button type="button" onClick={() => removeAdded(i)} aria-label="Remove this vehicle" className="shrink-0 text-[#9AA3B2] hover:text-[#0A66F0]">
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* The editor: one vehicle at a time */}
              <div className="space-y-5">
                {added.length > 0 && (
                  <p className="text-sm font-medium text-[#0E1A33]">
                    Vehicle {added.length + 1}
                    {!draft.typeId && <span className="font-normal text-[#5F6878]"> · Optional</span>}
                  </p>
                )}
                <div>
                  <p className={SECTION_LABEL}>Vehicle Type</p>
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                    {types.map((t) => {
                      const meta = vehicleMeta(t);
                      const on = draft.typeId === t.id;
                      return (
                        <button
                          key={t.id}
                          type="button"
                          onClick={() => pickType(t.id)}
                          aria-pressed={on}
                          className={`flex min-w-0 items-center gap-2.5 rounded-[12px] p-2.5 text-left ${choiceClass(on)}`}
                        >
                          <span className="flex h-10 w-[54px] shrink-0 items-center justify-center rounded-[9px] bg-[#EEF3FA]">
                            <img src={meta.image} alt="" loading="lazy" className="max-h-8 max-w-[48px] object-contain" />
                          </span>
                          <span className="min-w-0">
                            <span className="block truncate text-sm font-semibold text-[#0E1A33]">{titleCase(t.name)}</span>
                            {meta.examples && <span className="block truncate text-[11px] text-[#5F6878]">{meta.examples}</span>}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                  {draft.typeId && (
                    <div className="mt-3 flex items-center justify-between gap-3 rounded-[12px] border border-[#E4E9F1] bg-white px-3.5 py-2.5 sm:max-w-xs">
                      <span className="text-sm text-[#5F6878]">How Many?</span>
                      <QtyStepper value={draft.count} min={1} max={10} onChange={setCount} />
                    </div>
                  )}
                </div>

                {draft.typeId &&
                  editing &&
                  (servicesLoading ? (
                    <p className="text-sm text-[#5F6878]">Loading services…</p>
                  ) : editingGroups.length === 0 ? (
                    <p className="text-sm text-[#5F6878]">No services for this vehicle yet.</p>
                  ) : (
                    <>
                      <div>
                        <p className={SECTION_LABEL}>Service</p>
                        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                          {editingGroups.map((g) => {
                            const selected = draft.base === g.key;
                            const shown = unit(g.primary, draft.typeId);
                            const { price, original } = priceForType(g.primary, draft.typeId);
                            // A first-wash price is struck against the regular one, else against the MRP.
                            const struck = shown < price ? price : original;
                            const pct = discountPercent(shown, struck);
                            const offerTag = g.variants.find((v) => v.offer_tag?.trim())?.offer_tag;
                            const cardLine = lineFor({ ...draft, base: g.key, addons: [] });
                            const inPlan = !!cardLine?.payload && !!passFor(draft.typeId, cardLine.payload.service_ids, new Set());
                            const inc = parseIncludes(g.primary.description);
                            const items = inc.items.length ? inc.items.map(titleCase) : inc.summary ? [inc.summary] : [];
                            return (
                              <button
                                key={g.key}
                                type="button"
                                onClick={() => pickBase(g.key)}
                                aria-pressed={selected}
                                className={`rounded-xl px-3.5 py-3 text-left ${choiceClass(selected)}`}
                              >
                                <span className="flex items-start justify-between gap-3">
                                  <span className="text-sm font-semibold text-[#0E1A33]">{titleCase(g.label)}</span>
                                  <span className="shrink-0 text-right">
                                    {struck != null && <span className="mr-1 text-xs text-[#9AA3B2] line-through">₹{struck}</span>}
                                    <span className="font-mono-num text-sm font-bold text-[#0E1A33]">₹{shown}</span>
                                  </span>
                                </span>
                                {(pct != null || offerTag || inPlan || shown < price) && (
                                  <span className="mt-1.5 flex flex-wrap items-center gap-1.5">
                                    <DiscountBadge percent={pct} />
                                    <OfferTag label={offerTag} />
                                    {shown < price && <span className="text-[11px] text-[#5F6878]">First Wash</span>}
                                    {inPlan && (
                                      <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-[#0E1A33]">
                                        <BadgeCheck className="h-3 w-3" /> In Their Plan
                                      </span>
                                    )}
                                  </span>
                                )}
                                {items.length > 0 && (
                                  <ul className="mt-2 space-y-0.5">
                                    {items.map((it) => (
                                      <li key={it} className="flex items-start gap-1.5 text-xs text-[#5F6878]">
                                        <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-[#0A66F0]" />
                                        <span>{it}</span>
                                      </li>
                                    ))}
                                  </ul>
                                )}
                              </button>
                            );
                          })}
                        </div>
                      </div>

                      {editing.base && editingKit && (editingKit.simple.length > 0 || (editingIsBike && editingKit.bikePolish)) && (
                        <div>
                          <p className={SECTION_LABEL}>Add-ons</p>
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
                                  className={`rounded-full px-3 py-1.5 text-xs font-medium ${choiceClass(on)}`}
                                >
                                  + {titleCase(a.name)} · ₹{per}
                                  {perBike ? "/bike" : ""}
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      )}
                    </>
                  ))}

                {/* A different type on the same visit */}
                {totalVehicles < maxVehicles && (
                  <Button type="button" variant="outline" className="w-full" disabled={!draftReady} onClick={addAnother}>
                    <Plus className="h-4 w-4" /> Add Another Vehicle
                  </Button>
                )}
                {!types.length && <p className="text-sm text-[#5F6878]">Loading vehicle types…</p>}
              </div>
            </div>
          )}

          {/* ---------------- STEP 2 ---------------- */}
          {step === 1 && (
            <div className="space-y-6">
              <WizardStepHeader title={(isLog ? LOG_STEPS : STEPS)[1]} description={isLog ? "Saved as done — no captain or photos needed." : undefined} />

              <CustomerNamePhoneFields
                name={name}
                phone={phone}
                onChangeName={(v) => {
                  setName(v);
                  pickCustomer(null);
                }}
                onChangePhone={(v) => {
                  setPhone(v);
                  pickCustomer(null);
                }}
                onPick={(c) => pickCustomer(c.id)}
                nameError={fieldErrors.name}
                phoneError={fieldErrors.phone}
                phoneInputRef={phoneRef}
              />

              {isLog && (
                <div className="space-y-6">
                  <Input label="Where Was It Done?" maxLength={300} value={line1} onChange={(e) => setLine1(e.target.value)} error={fieldErrors.address} placeholder="House / flat, street, area" />

                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <Input type="date" label="Date" value={date} min={oldestLogDate} max={todayIST()} onChange={(e) => setDate(e.target.value)} error={fieldErrors.date} />
                    <Input type="time" label="Time" min={logRangeOk ? logOpens : undefined} max={logRangeOk ? logLatest : undefined} value={logTime} onChange={(e) => setLogTime(e.target.value)} error={fieldErrors.time} />
                  </div>

                  <Input
                    label="Discount Given (₹, Optional)"
                    inputMode="numeric"
                    value={discount}
                    onChange={(e) => setDiscount(e.target.value.replace(/\D/g, "").slice(0, 6))}
                    placeholder="0"
                    error={fieldErrors.discount}
                    hint={discountNum > 0 && discountNum <= displayTotal ? `Customer pays ₹${finalTotal} instead of ₹${displayTotal}.` : undefined}
                  />

                  {planTogglesNode}

                  {finalTotal > 0 && (
                    <div>
                      <p className={SECTION_LABEL}>How Was It Paid?</p>
                      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                        {(
                          [
                            { id: "cash", icon: Banknote, title: "Cash", sub: "Collected by you" },
                            { id: "online", icon: CreditCard, title: "Online / UPI", sub: "To the business account" },
                          ] as const
                        ).map((opt) => (
                          <button
                            key={opt.id}
                            type="button"
                            onClick={() => setPaymentMethod(opt.id)}
                            aria-pressed={paymentMethod === opt.id}
                            className={`flex items-start gap-3 rounded-xl p-3 text-left ${choiceClass(paymentMethod === opt.id)}`}
                          >
                            <opt.icon className="mt-0.5 h-4 w-4 shrink-0 text-[#0A66F0]" />
                            <span>
                              <span className="block text-sm font-semibold text-[#0E1A33]">{opt.title}</span>
                              <span className="block text-xs text-[#5F6878]">{opt.sub}</span>
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  <Input label="Note (Optional)" maxLength={500} value={notes} onChange={(e) => setNotes(e.target.value)} />

                  <Switch checked={sendWhatsApp} onChange={setSendWhatsApp} label="Tell The Customer On WhatsApp" description="One message saying the service is done." />
                </div>
              )}

              {!isLog && (
                <>
                  {/* Where */}
                  <div className="space-y-3">
                    <p className={`${SECTION_LABEL} flex items-center gap-1.5`}>
                      <MapPin className="h-3.5 w-3.5" /> Address
                    </p>
                    {!!savedAddresses?.length && (
                      <div className="flex flex-wrap gap-2">
                        {savedAddresses.map((a) => (
                          <button
                            key={a.id}
                            type="button"
                            onClick={() => void chooseSavedAddress(a)}
                            aria-pressed={savedAddressId === a.id}
                            className={`max-w-full rounded-xl px-3.5 py-2 text-left text-sm ${choiceClass(savedAddressId === a.id)}`}
                          >
                            <span className="block font-semibold text-[#0E1A33]">{a.label}</span>
                            <span className="block truncate text-xs text-[#5F6878]">
                              {a.line1} · {a.pincode}
                            </span>
                          </button>
                        ))}
                        <button
                          type="button"
                          onClick={() => {
                            setSavedAddressId("");
                            setPinned(null);
                            resetCoverage();
                          }}
                          aria-pressed={savedAddressId === ""}
                          className={`rounded-xl px-3.5 py-2 text-sm font-medium ${choiceClass(savedAddressId === "")}`}
                        >
                          + New Address
                        </button>
                      </div>
                    )}

                    {!savedAddressId && usingPin && <LocationPicker value={pinned} onUnavailable={() => setMapsUp(false)} onChange={(v) => void onPin(v)} />}
                    {!savedAddressId && (
                      <div className="space-y-3">
                        {mapsUp && (
                          <button
                            type="button"
                            onClick={() => {
                              setTypedAddress((v) => !v);
                              setPinned(null);
                              resetCoverage();
                            }}
                            className="text-xs font-semibold text-[#5F6878] underline underline-offset-2 hover:text-[#0A66F0]"
                          >
                            {typedAddress ? "Pin On The Map Instead" : "Type The Address Instead"}
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
                              hint={!mapsUp ? "Maps are unavailable — type the address." : undefined}
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
                      <p className="flex items-center gap-1.5 text-xs text-[#5F6878]">
                        <Spinner className="h-3.5 w-3.5" /> Checking the area…
                      </p>
                    )}
                    {coverage === "covered" && (
                      <p className="flex items-start gap-1.5 text-xs text-[#5F6878]">
                        <BadgeCheck className="mt-px h-3.5 w-3.5 shrink-0 text-[#16A34A]" />
                        <span>
                          <span className="font-medium text-[#0E1A33]">We serve this area.</span>
                          {pinned && <> {pinned.formatted || [pinned.area, pinned.city].filter(Boolean).join(", ")} — drag the pin if this isn't the exact gate.</>}
                        </span>
                      </p>
                    )}
                    {coverage === "uncovered" && pinRequired && (
                      <p className="text-xs font-medium text-[var(--color-error)]">Please pin your exact location on the map — we can't confirm the area from a pincode alone.</p>
                    )}
                    {coverage === "uncovered" && !pinRequired && (
                      <CoverageLeadInline pincode={checkedPincode} prefillName={name} prefillPhone={phone} serviceInterest={allServices.map((s) => s.name).join(", ") || undefined} />
                    )}
                    {fieldErrors.location && <p className="text-xs font-medium text-[var(--color-error)]">{fieldErrors.location}</p>}
                  </div>

                  {/* When */}
                  {coverage === "covered" && (
                    <div className="space-y-2">
                      <p className={`${SECTION_LABEL} flex items-center gap-1.5`}>
                        <CalendarDays className="h-3.5 w-3.5" /> Date &amp; Time
                      </p>
                      <SlotBoard
                        centerId={centerId}
                        date={date}
                        onDateChange={setDate}
                        value={slot}
                        onChange={pickMgrSlot}
                        maxAdvanceDays={policy?.max_advance_days}
                        heldSeconds={mgrHold.secondsLeft}
                        notice={mgrSlotNotice}
                      />
                      {(fieldErrors.date || fieldErrors.slot) && <p className="text-xs font-medium text-[var(--color-error)]">{fieldErrors.date || fieldErrors.slot}</p>}
                    </div>
                  )}

                  {planTogglesNode}

                  {/* How to pay */}
                  {payable > 0 && (
                    <div>
                      <p className={SECTION_LABEL}>Payment</p>
                      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                        {payOptions
                          .filter((opt) => !onlineOnly || opt.id === "online")
                          .map((opt) => (
                            <button
                              key={opt.id}
                              type="button"
                              onClick={() => setPaymentMethod(opt.id)}
                              aria-pressed={payMethod === opt.id}
                              className={`flex items-start gap-3 rounded-xl p-3 text-left ${choiceClass(payMethod === opt.id)}`}
                            >
                              <opt.icon className="mt-0.5 h-4 w-4 shrink-0 text-[#0A66F0]" />
                              <span>
                                <span className="block text-sm font-semibold text-[#0E1A33]">{opt.title}</span>
                                <span className="block text-xs text-[#5F6878]">{opt.sub}</span>
                              </span>
                            </button>
                          ))}
                      </div>
                      {onlineOnly && <p className="mt-2 text-xs text-[#5F6878]">{onlineReason}</p>}
                    </div>
                  )}

                  {payable > 0 && (
                    <div className="max-w-sm">
                      <Input
                        label="Coupon Code (Optional)"
                        value={couponCode}
                        maxLength={20}
                        onChange={(e) => setCouponCode(e.target.value.toUpperCase().replace(/\s/g, ""))}
                        error={fieldErrors.couponCode || liveQuote?.coupon_error || undefined}
                      />
                    </div>
                  )}

                  {/* Optional extras */}
                  <div>
                    <button type="button" onClick={() => setMoreOpen((v) => !v)} className="text-xs font-semibold text-[#5F6878] underline underline-offset-2 hover:text-[#0A66F0]">
                      {moreOpen ? "Hide Note And Second Contact" : "Add A Note Or Second Contact"}
                    </button>
                    {moreOpen && (
                      <div className="mt-3 space-y-3">
                        <Input label="Note For The Captain" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="E.g. basement parking, gate B" />
                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                          <Input label="Second Contact Name" value={altName} onChange={(e) => setAltName(e.target.value)} />
                          <Input label="Second Contact Number" value={altPhone} inputMode="numeric" onChange={(e) => setAltPhone(cleanMobileInput(e.target.value))} error={fieldErrors.altPhone} />
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
        <Modal open={!!sentLink} onClose={() => navigate("/manager/bookings")} title="Booking Created" maxWidth="max-w-md">
          {sentLink && (
            <div className="space-y-4">
              <p className="text-sm text-[#5F6878]">{sentLink.numbers} · payment link sent to the customer on WhatsApp. It confirms once paid.</p>
              <div className="flex items-center gap-2 rounded-xl border border-[#E4E9F1] bg-[#F7F9FC] px-3.5 py-2.5">
                <span className="font-mono-num min-w-0 flex-1 truncate text-sm text-[#0E1A33]">{sentLink.link}</span>
                <Button size="sm" variant="outline" onClick={() => void copyLink()}>
                  <Copy className="h-3.5 w-3.5" /> {copied ? "Copied" : "Copy"}
                </Button>
              </div>
              <Button className="w-full font-semibold" onClick={() => navigate("/manager/bookings")}>
                Done
              </Button>
            </div>
          )}
        </Modal>
      </>
    );
  }

  // ---- customer flows: render ---------------------------------------------------
  const sectionTitle = (icon: ReactNode, title: string, aside?: ReactNode) => (
    <div className="mb-3 flex items-center justify-between gap-3">
      <h2 className="flex items-center gap-2 font-display text-[16px] font-bold text-[#0E1A33]">
        <span className="flex h-7 w-7 items-center justify-center rounded-[9px] bg-[#EEF3FA] text-[#0A66F0]">{icon}</span>
        {title}
      </h2>
      {aside}
    </div>
  );
  const divider = <div className="my-5 h-px bg-[#EEF1F5]" />;
  const planSwitches = lines.map((line, i) => {
    const match = lineMatches[i];
    if (!match) return null;
    const plan = titleCase(match.subs[0].plan_name) || "Plan";
    const left = match.subs[0].remaining_service_count;
    const units = line.payload?.quantity ?? 1;
    return (
      <button
        key={i}
        type="button"
        role="switch"
        aria-checked={match.on}
        onClick={() => setSubscriptionOverride((prev) => ({ ...prev, [i]: !match.on }))}
        className={`flex w-full items-center justify-between gap-3 rounded-[14px] border p-3 text-left transition ${match.on ? "border-[#0A66F0] bg-[#F3F7FF]" : "border-[#E4E9F1] bg-white"}`}
      >
        <span className="min-w-0">
          <span className="block text-[14px] font-semibold text-[#0E1A33]">
            Use My {plan} — {left} Wash{left === 1 ? "" : "es"} Left
          </span>
          <span className="block text-[12px] text-[#5F6878]">
            {lineLabel(line)}
            {match.coveredCount < units ? ` · covers ${match.coveredCount} of ${units}` : ""}
          </span>
        </span>
        <span aria-hidden className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${match.on ? "bg-[#0A66F0]" : "bg-[#C9D3E2]"}`}>
          <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${match.on ? "translate-x-[22px]" : "translate-x-0.5"}`} />
        </span>
      </button>
    );
  });
  const introSummary =
    isCustomer && introSub && introLine ? (
      <div className="flex items-center gap-3 rounded-[16px] border border-[#CFE0FD] bg-[#F3F7FF] p-3.5">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[12px] bg-white text-[#0A66F0]">
          <Gift className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[14px] font-semibold text-[#0E1A33]">Using Your {introPlanName ? `${titleCase(introPlanName)} Pass` : "Pass"}</p>
          <p className="text-[12px] text-[#5F6878]">
            {titleCase(introLine.type.name)}
            {introLine.base ? ` · ${titleCase(introLine.base.name)}` : ""} · {introLeft} wash{introLeft === 1 ? "" : "es"} left
          </p>
        </div>
        <button type="button" onClick={goBack} className="shrink-0 text-[13px] font-semibold text-[#0A66F0] hover:underline">
          Change
        </button>
      </div>
    ) : null;

  const typePanel = (close: () => void) => (
    <div className="space-y-1">
      {!types.length && <p className="px-3 py-4 text-[13px] text-[#5F6878]">Loading…</p>}
      {types.map((t) => {
        const meta = vehicleMeta(t);
        const on = draft.typeId === t.id;
        return (
          <PickerOption
            key={t.id}
            selected={on}
            onSelect={() => {
              const base = pickType(t.id);
              close();
              if (!base) window.setTimeout(() => setOpenPicker("service"), 200);
            }}
          >
            <span className="flex h-11 w-[70px] shrink-0 items-center justify-center rounded-[10px] bg-[#EEF3FA]">
              <img src={meta.image} alt="" loading="lazy" className="max-h-9 max-w-[62px] object-contain" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[14px] font-semibold text-[#0E1A33]">{titleCase(t.name)}</span>
              {meta.examples && <span className="block text-[12px] text-[#5F6878]">{meta.examples}</span>}
            </span>
            {on && <CheckCircle2 className="h-5 w-5 shrink-0 text-[#0A66F0]" />}
          </PickerOption>
        );
      })}
    </div>
  );

  const servicePanel = (close: () => void) => {
    const groups = draft.typeId ? editingGroups : allGroups;
    return (
      <div className="space-y-1">
        {servicesLoading && <p className="px-3 py-4 text-[13px] text-[#5F6878]">Loading services…</p>}
        {!servicesLoading && !groups.length && <p className="px-3 py-4 text-[13px] text-[#5F6878]">No services for this vehicle yet.</p>}
        {groups.map((g) => {
          const on = shownServiceKey === g.key;
          const offerTag = g.variants.find((v) => v.offer_tag?.trim())?.offer_tag?.trim();
          const inc = parseIncludes(g.primary.description);
          const items = inc.items.length ? inc.items.map(titleCase) : inc.summary ? [inc.summary] : [];
          let shown: number;
          let struck: number | null;
          let from = false;
          let inPlan = false;
          if (draft.typeId) {
            shown = unit(g.primary, draft.typeId);
            const { price, original } = priceForType(g.primary, draft.typeId);
            struck = shown < price ? price : original;
            const cardLine = lineFor({ ...draft, base: g.key, addons: [] });
            inPlan = !!cardLine?.payload && !!passFor(draft.typeId, cardLine.payload.service_ids, new Set());
          } else {
            const pv = priceView(g.primary);
            shown = pv.final;
            struck = pv.original;
            from = pv.varies;
          }
          return (
            <PickerOption
              key={g.key}
              selected={on}
              onSelect={() => {
                pickService(g.key);
                close();
                if (!draft.typeId) window.setTimeout(() => setOpenPicker("type"), 200);
              }}
            >
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[14px] font-semibold text-[#0E1A33]">{titleCase(g.label)}</span>
                  {offerTag && <span className="rounded-full bg-[#FFD21F] px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-[#0E1A33]">{offerTag}</span>}
                  {inPlan && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-[#E8F0FE] px-2 py-0.5 text-[10px] font-bold text-[#0A66F0]">
                      <BadgeCheck className="h-3 w-3" /> In Your Plan
                    </span>
                  )}
                </span>
                {items.length > 0 && <span className="mt-0.5 block truncate text-[12px] text-[#5F6878]">{items.slice(0, 3).join(" · ")}</span>}
                <span className="mt-0.5 block text-[11px] text-[#8A93A3]">
                  {g.primary.duration_minutes ? `${g.primary.duration_minutes} mins` : ""}
                  {g.variants.some((v) => v.prepaid_only) ? `${g.primary.duration_minutes ? " · " : ""}Pay Online` : ""}
                </span>
              </span>
              <span className="shrink-0 text-right">
                {struck != null && struck > shown && <span className="block text-[11px] text-[#9AA3B2] line-through">{INR(struck)}</span>}
                <span className="font-display text-[15px] font-bold text-[#0E1A33]">
                  {from ? <span className="mr-0.5 text-[11px] font-medium text-[#5F6878]">from</span> : null}
                  {INR(shown)}
                </span>
              </span>
            </PickerOption>
          );
        })}
      </div>
    );
  };

  const addonList = editingKit ? [...editingKit.simple, ...(editingIsBike && editingKit.bikePolish ? [editingKit.bikePolish] : [])] : [];
  const extrasRow =
    editing?.base ? (
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="inline-flex h-9 items-center gap-2 rounded-full border border-[#E4E9F1] bg-white pl-3 pr-1">
          <span className="text-[13px] text-[#5F6878]">{editingIsBike ? "Bikes" : "Cars"}</span>
          <button type="button" aria-label="One less" disabled={draft.count <= 1} onClick={() => setCount(draft.count - 1)} className="flex h-7 w-7 items-center justify-center rounded-full bg-[#F1F4F9] text-[15px] font-bold text-[#0E1A33] disabled:opacity-35">
            −
          </button>
          <span className="w-4 text-center text-[14px] font-semibold text-[#0E1A33]">{draft.count}</span>
          <button type="button" aria-label="One more" disabled={draft.count >= 10} onClick={() => setCount(draft.count + 1)} className="flex h-7 w-7 items-center justify-center rounded-full bg-[#F1F4F9] text-[15px] font-bold text-[#0E1A33] disabled:opacity-35">
            +
          </button>
        </span>
        {addonList.map((a) => {
          const on = draft.addons.includes(a.id);
          const perBike = editingIsBike && editingKit?.bikePolish && a.id === editingKit.bikePolish.id;
          return (
            <button
              key={a.id}
              type="button"
              onClick={() => toggleAddon(a.id)}
              aria-pressed={on}
              className={`inline-flex h-9 items-center gap-1 rounded-full border px-3 text-[13px] font-medium transition ${on ? "border-[#0A66F0] bg-[#F3F7FF] text-[#0A66F0]" : "border-[#E4E9F1] bg-white text-[#0E1A33] hover:border-[#0A66F0]/50"}`}
            >
              {on ? <CheckCircle2 className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
              {titleCase(a.name)} · {INR(unit(a, draft.typeId))}
              {perBike ? "/bike" : ""}
            </button>
          );
        })}
        {totalVehicles < maxVehicles && (
          <button type="button" onClick={addAnother} className="inline-flex h-9 items-center gap-1 px-1 text-[13px] font-semibold text-[#0A66F0] hover:underline">
            <Plus className="h-3.5 w-3.5" /> Add Another Vehicle
          </button>
        )}
      </div>
    ) : null;

  const addedList =
    added.length > 0 ? (
      <div className="mb-4 space-y-2">
        {added.map((d, i) => {
          const li = lines.findIndex((x) => x.draft === d);
          const l = lines[li];
          if (!l) return null;
          const cost = lineTotal(li);
          return (
            <div key={`${d.typeId}-${i}`} className="flex items-center gap-3 rounded-[14px] border border-[#E4E9F1] bg-[#FAFBFD] p-2.5">
              <span className="flex h-9 w-[54px] shrink-0 items-center justify-center rounded-[10px] bg-[#EEF3FA]">
                <img src={vehicleMeta(l.type).image} alt="" className="max-h-7 max-w-[48px] object-contain" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[14px] font-semibold text-[#0E1A33]">{lineLabel(l).split(" · ")[0]}</span>
                <span className="block truncate text-[12px] text-[#5F6878]">{lineService(l)}</span>
              </span>
              <span className="shrink-0 text-[14px] font-semibold text-[#0E1A33]">{coveredUnits[li] > 0 && cost === 0 ? "Covered" : INR(cost)}</span>
              <button type="button" onClick={() => editAdded(i)} className="shrink-0 text-[13px] font-semibold text-[#0A66F0] hover:underline">
                Edit
              </button>
              <button type="button" onClick={() => removeAdded(i)} aria-label="Remove this vehicle" className="shrink-0 text-[#9AA3B2] hover:text-[var(--color-error)]">
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          );
        })}
      </div>
    ) : null;

  const slotArea = slotCenter && slotCenter === centerId && coverage === "covered" ? savedAddress?.label || "Your Address" : "Indore";

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

      <div className={pageLayout ? "min-h-screen bg-[#F6F8FC] pb-16 font-sans" : "font-sans"} style={V2_THEME}>
        {step === 0 && <BookHero variant={pageLayout ? "page" : "app"} />}

        <div className={pageLayout ? `relative mx-auto w-full max-w-[1200px] px-4 sm:px-6 ${step === 0 ? "-mt-6 sm:-mt-12" : "pt-5 sm:pt-8"}` : ""}>
          {step === 1 && (
            <div className="mb-4 flex items-center gap-3">
              <button type="button" onClick={goBack} aria-label="Back to the slots" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-[#E4E9F1] bg-white text-[#0E1A33] transition hover:border-[#0A66F0]">
                <ArrowLeft className="h-5 w-5" />
              </button>
              <div className="min-w-0">
                <p className="text-[12px] font-semibold uppercase tracking-[0.14em] text-[#0A66F0]">Step 2 of 2</p>
                <h1 className="font-display text-[24px] font-extrabold leading-tight tracking-[-0.02em] text-[#0E1A33] sm:text-[30px]">Confirm &amp; Book</h1>
              </div>
            </div>
          )}

          <div className="@container/book">
            <div className="grid grid-cols-1 items-start gap-5 @4xl/book:grid-cols-[minmax(0,1fr)_340px]">
              {/* LEFT */}
              <div className="min-w-0 space-y-4">
                {step === 0 ? (
                  <>
                    <div className={`${CARD} @container p-4 sm:p-5`}>
                      {planIssue && (
                        <p role="status" className="mb-4 flex items-start gap-2 rounded-[12px] bg-[#F3F7FF] px-3 py-2.5 text-[13px] text-[#0E1A33]">
                          <Info className="mt-0.5 h-4 w-4 shrink-0 text-[#0A66F0]" />
                          <span>{planIssue}</span>
                        </p>
                      )}
                      {addedList}
                      {added.length > 0 && (
                        <div className="mb-2 flex items-center justify-between gap-3">
                          <p className="text-[13px] font-semibold text-[#0E1A33]">
                            Vehicle {added.length + 1}
                            {!draftReady && <span className="font-normal text-[#5F6878]"> · Optional</span>}
                          </p>
                          {draft.typeId && !draftReady && (
                            <button type="button" onClick={() => setDraft(EMPTY_DRAFT)} className="text-[12px] font-semibold text-[#5F6878] hover:text-[#0E1A33]">
                              Cancel
                            </button>
                          )}
                        </div>
                      )}

                      <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-3">
                        <PickerField
                          icon={editingIsBike ? <BikeIcon /> : <Car className="h-5 w-5" />}
                          label="Car Type"
                          value={editingType ? keepTogether(vehicleLabel(editingType), "(") : ""}
                          placeholder="Select Car Type"
                          sheetTitle="Choose Your Vehicle"
                          open={openPicker === "type"}
                          onOpenChange={(o) => setOpenPicker(o ? "type" : null)}
                        >
                          {typePanel}
                        </PickerField>
                        <PickerField
                          icon={<SprayCan className="h-5 w-5" />}
                          label="Service"
                          value={serviceGroupLabel(shownServiceKey)}
                          placeholder="Select Service"
                          sheetTitle={editingType ? `Services For ${titleCase(editingType.name)}` : "Choose A Service"}
                          open={openPicker === "service"}
                          onOpenChange={(o) => setOpenPicker(o ? "service" : null)}
                        >
                          {servicePanel}
                        </PickerField>
                        <PickerField
                          icon={<CalendarDays className="h-5 w-5" />}
                          label="Date & Time"
                          value={slot ? `${dayText.replace(/ /g, NBSP)} · ${formatTime12(slot.split("-")[0]).replace(" ", NBSP)}` : ""}
                          placeholder="Pick A Slot Below"
                          open={false}
                          onOpenChange={() => undefined}
                          onClick={jumpToSlots}
                          invalid={!!fieldErrors.slot}
                        />
                      </div>
                      {extrasRow}

                      <div ref={slotsRef} className="mt-6 scroll-mt-24">
                        <div className="mb-3 flex items-center justify-between gap-3">
                          <h2 className="font-display text-[18px] font-bold text-[#0E1A33]">Available Slots</h2>
                        </div>
                        {slotsBlock}
                      </div>
                    </div>

                    {/* Phone / narrow: price + Continue right under the slots */}
                    <div className="space-y-3 @4xl/book:hidden">
                      {lines.length > 0 && <div className={`${CARD} p-4`}>{billBlock}</div>}
                      {ctaButton}
                      {secureNote}
                    </div>
                  </>
                ) : (
                  <>
                    {/* Phone / narrow: what's being booked, with a way back */}
                    <div className={`${CARD} flex items-center gap-3 p-3 @4xl/book:hidden`}>
                      <span className="flex h-12 w-[72px] shrink-0 items-center justify-center rounded-[12px] bg-[#EEF3FA]">
                        <img src={heroVehicle.image} alt="" loading="lazy" className="max-h-10 max-w-[64px] object-contain" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[14px] font-semibold text-[#0E1A33]">{lines.map((l) => `${lineLabel(l).split(" · ")[0]} · ${lineService(l)}`).join(" + ")}</p>
                        <p className="truncate text-[12px] text-[#5F6878]">{whenText || "Pick A Time Below"}</p>
                      </div>
                      <button type="button" onClick={goBack} className="shrink-0 text-[13px] font-semibold text-[#0A66F0]">
                        Edit
                      </button>
                    </div>

                    {introSummary}

                    <div className={`${CARD} @container p-4 sm:p-5`}>
                      {confirmSlots && (
                        <>
                          {sectionTitle(<CalendarDays className="h-4 w-4" />, "When Should We Come?")}
                          {slotsBlock}
                          {divider}
                        </>
                      )}

                      {sectionTitle(<MapPin className="h-4 w-4" />, "Where Should We Come?")}
                      {addressBlock}

                      {divider}
                      {sectionTitle(<UserRound className="h-4 w-4" />, "Your Details")}
                      {isCustomer && user && user.phone ? (
                        <p className="text-[14px] text-[#5F6878]">
                          Booking as <span className="font-semibold text-[#0E1A33]">{user.full_name}</span> · +91 {user.phone}
                        </p>
                      ) : (
                        <div className="grid grid-cols-1 gap-3 @lg:grid-cols-2">
                          <label className="block">
                            <span className="mb-1.5 block text-[12px] font-medium text-[#5F6878]">Your Name</span>
                            <input className={FIELD} maxLength={100} value={name} onChange={(e) => setName(e.target.value)} placeholder="E.g. Rahul Sharma" autoComplete="name" />
                            {fieldErrors.name && (
                              <span data-field-error="true" className="mt-1 block text-[12px] font-medium text-[var(--color-error)]">
                                {fieldErrors.name}
                              </span>
                            )}
                          </label>
                          <label className="block">
                            <span className="mb-1.5 block text-[12px] font-medium text-[#5F6878]">Mobile Number</span>
                            <span className="relative block">
                              <span className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-[15px] text-[#5F6878]">+91</span>
                              <input
                                ref={phoneRef}
                                className={`${FIELD} pl-12`}
                                value={phone}
                                inputMode="numeric"
                                autoComplete="tel-national"
                                onChange={(e) => setPhone(cleanMobileInput(e.target.value))}
                                placeholder="10-digit mobile"
                              />
                            </span>
                            {fieldErrors.phone ? (
                              <span data-field-error="true" className="mt-1 block text-[12px] font-medium text-[var(--color-error)]">
                                {fieldErrors.phone}
                              </span>
                            ) : (
                              <span className="mt-1 block text-[12px] text-[#8A93A3]">Booking updates come on WhatsApp.</span>
                            )}
                          </label>
                        </div>
                      )}

                      {!(introSummary && lines.length === 1) && planSwitches.some(Boolean) && <div className="mt-4 space-y-2">{planSwitches}</div>}

                      {payable > 0 && (
                        <>
                          {divider}
                          {sectionTitle(<CreditCard className="h-4 w-4" />, "How Would You Like To Pay?")}
                          <div className="grid grid-cols-1 gap-2 @lg:grid-cols-2">
                            {payOptions
                              .filter((opt) => !onlineOnly || opt.id === "online")
                              .map((opt) => {
                                const on = payMethod === opt.id;
                                return (
                                  <button
                                    key={opt.id}
                                    type="button"
                                    onClick={() => setPaymentMethod(opt.id)}
                                    aria-pressed={on}
                                    className={`flex items-center gap-3 rounded-[14px] border p-3 text-left transition ${on ? "border-[#0A66F0] bg-[#F3F7FF] ring-1 ring-[#0A66F0]" : "border-[#E4E9F1] bg-white hover:border-[#0A66F0]/50"}`}
                                  >
                                    <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] ${on ? "bg-[#0A66F0] text-white" : "bg-[#EEF3FA] text-[#0A66F0]"}`}>
                                      <opt.icon className="h-4 w-4" />
                                    </span>
                                    <span className="min-w-0 flex-1">
                                      <span className="block text-[14px] font-semibold text-[#0E1A33]">{opt.title}</span>
                                      <span className="block text-[12px] text-[#5F6878]">{opt.sub}</span>
                                    </span>
                                    <span aria-hidden className={`flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border-2 ${on ? "border-[#0A66F0]" : "border-[#C3CBD8]"}`}>
                                      {on && <span className="h-2 w-2 rounded-full bg-[#0A66F0]" />}
                                    </span>
                                  </button>
                                );
                              })}
                          </div>
                          {onlineOnly && <p className="mt-2 text-[12px] text-[#5F6878]">{onlineReason}</p>}
                        </>
                      )}

                      <div className="mt-5">
                        <button type="button" onClick={() => setMoreOpen((v) => !v)} className="text-[13px] font-semibold text-[#0A66F0] hover:underline">
                          {moreOpen ? "Hide Note And Second Contact" : "+ Add A Note Or Second Contact"}
                        </button>
                        {moreOpen && (
                          <div className="mt-3 grid grid-cols-1 gap-3 @lg:grid-cols-2">
                            <label className="block @lg:col-span-2">
                              <span className="mb-1.5 block text-[12px] font-medium text-[#5F6878]">Note For The Captain</span>
                              <input className={FIELD} maxLength={500} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="E.g. flat 302, basement parking, gate B" />
                            </label>
                            <label className="block">
                              <span className="mb-1.5 block text-[12px] font-medium text-[#5F6878]">Second Contact Name</span>
                              <input className={FIELD} maxLength={100} value={altName} onChange={(e) => setAltName(e.target.value)} />
                            </label>
                            <label className="block">
                              <span className="mb-1.5 block text-[12px] font-medium text-[#5F6878]">Second Contact Number</span>
                              <input className={FIELD} value={altPhone} inputMode="numeric" onChange={(e) => setAltPhone(cleanMobileInput(e.target.value))} />
                              {fieldErrors.altPhone && (
                                <span data-field-error="true" className="mt-1 block text-[12px] font-medium text-[var(--color-error)]">
                                  {fieldErrors.altPhone}
                                </span>
                              )}
                            </label>
                          </div>
                        )}
                      </div>

                      {allServices.some((s) => !s.is_addon) && (
                        <p className="mt-4 flex items-start gap-2 rounded-[12px] bg-[#F6F8FC] p-3 text-[12px] leading-relaxed text-[#5F6878]">
                          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0A66F0]" aria-hidden="true" />
                          <span>
                            Please keep water and a power point near the vehicle.{" "}
                            <a href="/service-policy" target="_blank" rel="noreferrer" className="font-semibold text-[#0A66F0] hover:underline">
                              Read More
                            </a>
                          </span>
                        </p>
                      )}
                    </div>

                    {/* Phone / narrow: the bill and Book */}
                    <div className="space-y-3 @4xl/book:hidden">
                      <div className={`${CARD} p-4`}>{billBlock}</div>
                      {ctaButton}
                      {secureNote}
                    </div>
                  </>
                )}
              </div>

              {/* RIGHT: Booking Summary (wide screens) */}
              <div className="hidden @4xl/book:sticky @4xl/book:top-24 @4xl/book:block">{summaryCard}</div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

const NBSP = "\u00A0";
/** Glues the part from `from` on into one unbreakable phrase ("(i10, Swift etc.)"). */
function keepTogether(text: string, from: string): string {
  const i = text.indexOf(from);
  return i < 0 ? text : text.slice(0, i) + text.slice(i).replace(/ /g, NBSP);
}

function BikeIcon({ className = "h-5 w-5" }: { className?: string }) {
  return <Bike className={className} />;
}
