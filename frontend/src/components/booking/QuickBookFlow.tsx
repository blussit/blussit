import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { BadgeCheck, Banknote, Car, CheckCircle2, CreditCard, MapPin, Plus, Trash2 } from "lucide-react";
import { bookingPolicyApi, catalogApi, coverageApi, serviceCenterApi, vehicleTypeApi, getSlotHolderKey } from "../../api/catalog";
import { bookingApi, type QuickBookingLine, type QuickBookingPayload } from "../../api/booking";
import { addressApi } from "../../api/profile";
import { Button, Input, Select, Spinner } from "../ui";
import { SlotPicker } from "../shared/SlotPicker";
import { LocationPicker, type LocationValue } from "../shared/LocationPicker";
import { WizardShell, WizardStepHeader } from "../shared/WizardShell";
import { ServicePrepNotice } from "../shared/ServicePrepNotice";
import { QtyStepper } from "../shared/QtyStepper";
import { CoverageLeadInline } from "../public/CoverageLeadInline";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { scrollToTopNow } from "../../lib/scroll";
import { todayIST } from "../../lib/date";
import { validateIndianMobile } from "../../lib/validators";
import { addonKit, baseGroups, bikeTypeIds, variantCount, type BaseGroup } from "../../lib/serviceMix";
import { parseIncludes, titleCase } from "../public/landing/shared";
import type { Address, Service, VehicleTypeOption } from "../../types";

/**
 * THE booking flow (2026-09 quick-booking model) — two steps, no account,
 * no OTP:
 *
 *   1. What are we washing?  pick a vehicle type, how many, one service
 *      (+ add-ons); "Add another vehicle" for a different type on the
 *      same visit ("Bike ×2 + SUV ×1").
 *   2. Where & when?         pin the address, pick a slot, name + phone,
 *      how to pay → Book.
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
type Mode = "public" | "customer" | "manager";

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
  const isCustomer = mode === "customer";
  const isManager = mode === "manager";
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
  const [moreOpen, setMoreOpen] = useState(false);
  const [notes, setNotes] = useState("");
  const [altName, setAltName] = useState("");
  const [altPhone, setAltPhone] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  // ---- keep an unfinished booking for THIS browser tab ---------------------
  // Back button, a tap on the logo, a refresh — the customer comes back to
  // exactly where they were. sessionStorage on purpose: this tab only,
  // gone when it closes, never days-old state resurfacing.
  const storageKey = `blussit:quickbook:${mode}`;
  const repeatId = searchParams.get("repeat");
  const [restored, setRestored] = useState(false);
  const restoreAddressRef = useRef<null | { pinned: LocationValue | null; savedAddressId: string | null; pincode: string; typed: boolean; slot: string }>(null);
  const skipAutoDateRef = useRef(false);
  useEffect(() => {
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
        setNotes(saved.notes || "");
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
        JSON.stringify({ at: Date.now(), step, added, draft, name, phone, savedAddressId, pinned, typedAddress, line1, pincode, date, slot, paymentMethod, notes, altName, altPhone })
      );
    } catch {
      // storage unavailable — nothing to keep
    }
  }, [restored, repeatId, storageKey, step, added, draft, name, phone, savedAddressId, pinned, typedAddress, line1, pincode, date, slot, paymentMethod, notes, altName, altPhone]);

  useEffect(() => {
    scrollToTopNow();
  }, [step]);

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
  const editingKit = draft.typeId ? addonKit(services, draft.typeId, bikeIds) : null;
  const editingIsBike = bikeIds.has(draft.typeId);

  const total = lines.reduce((n, l) => n + l.subtotal, 0);
  const regularTotal = lines.reduce((n, l) => n + l.regularSubtotal, 0);
  const allServices = lines.flatMap((l) => l.services);
  const step1Ready = lines.length > 0 && lines.every((l) => !!l.base) && (!draft.typeId || draftReady);

  const otherVehicles = added.reduce((n, d) => n + d.count, 0);
  const pickType = (typeId: string) => {
    // Default the service to the first one offered for this type so the
    // dropdown pick is already a bookable line.
    const first = baseGroups(services, typeId)[0];
    setDraft({ typeId, count: 1, base: first ? first.key : null, addons: [] });
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

  async function checkPincode(pin: string) {
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
          const slots = await serviceCenterApi.availableSlots(centerId, iso);
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
  const step2Ready = coverage === "covered" && !!date && !!slot && addressReady && name.trim().length >= 2 && !!validateIndianMobile(phone);

  const validateStep2 = (): boolean => {
    const next: Record<string, string> = {};
    if (name.trim().length < 2) next.name = "Enter the name.";
    if (!validateIndianMobile(phone)) next.phone = "Enter a valid 10-digit mobile number.";
    if (!savedAddressId && usingPin && !pinned) next.location = "Drop the pin on the service address.";
    if (!savedAddressId && !usingPin && line1.trim().length < 3) next.address = "Enter the address.";
    if (!savedAddressId && !usingPin && pincode.trim().length < 6) next.pincode = "Enter the pincode.";
    if (coverage !== "covered") next.location = next.location || "We need a serviceable address to continue.";
    if (!date) next.date = "Choose a date.";
    if (!slot) next.slot = "Choose a time slot.";
    if (altPhone.trim() && !validateIndianMobile(altPhone)) next.altPhone = "Enter a valid 10-digit mobile number.";
    setFieldErrors(next);
    return Object.keys(next).length === 0;
  };

  const submit = async () => {
    if (!validateStep2()) return;
    setError("");
    setSubmitting(true);
    try {
      const payload: QuickBookingPayload = {
        customer_name: name.trim(),
        customer_phone: validateIndianMobile(phone) || phone.trim(),
        lines: lines.map((l) => l.payload!).filter(Boolean),
        scheduled_date: date,
        scheduled_slot: slot,
        payment_method: total > 0 ? paymentMethod : "cash",
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
          service_label: lines.map((l) => `${l.count > 1 ? `${l.count} × ` : ""}${l.type.name} · ${titleCase(l.base?.name)}`).join(" + "),
          service_code: result.service_code,
          payment_link: result.payment_link,
          awaiting_payment: result.awaiting_payment,
          total_amount: result.total_amount,
        },
      });
    } catch (err) {
      setError(getErrorMessage(err));
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
          <span className="font-mono-num text-lg font-bold text-black">₹{total}</span>
        </span>
      </div>
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
          <Button className="min-w-[150px]" disabled={!step2Ready} isLoading={submitting} onClick={submit}>
            <CheckCircle2 className="h-4 w-4" />
            {total > 0 && paymentMethod === "online" ? "Book And Pay" : "Book Now"}
          </Button>
        )}
      </div>
      {step === 0 && !step1Ready && (draft.typeId || added.length > 0) && (
        <p className="text-right text-xs text-[var(--color-error)]">Pick a service for this vehicle to continue.</p>
      )}
    </div>
  );

  return (
    <WizardShell
      eyebrow={isManager ? "Book for a customer" : "Book a wash"}
      title={isManager ? "New Booking" : "Book Your Doorstep Wash"}
      steps={STEPS}
      current={step}
      onStepClick={(i) => i < step && setStep(i)}
      footer={footer}
    >
      {/* ---------------- STEP 1 ---------------- */}
      {step === 0 && (
        <div className="space-y-6">
          <WizardStepHeader title="What Are We Washing?" description={`Pick the vehicle and the service. Up to ${maxVehicles} vehicles on one visit — one address, one slot.`} />

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
                {types.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </Select>
              {draft.typeId && (
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

                    {editing.base && (
                      <p className="text-sm text-gray-700">
                        {lineLabel(editing)}: <span className="font-mono-num font-semibold text-black">₹{editing.subtotal}</span>
                      </p>
                    )}
                  </>
                )}
              </div>
            )}

            {/* A different type on the same visit */}
            {totalVehicles < maxVehicles && (
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
          <WizardStepHeader title="Where And When?" description="The captain drives to the pin you drop. Pick a slot that suits you." />

          {/* Who — a signed-in customer books as themselves, unless the
              account has no phone yet (Google sign-in) and we still need one
              for the captain and the WhatsApp updates. */}
          {isCustomer && user && user.phone ? (
            <div className="rounded-xl border border-[#F3E5B5] bg-[#FAFAFA] px-4 py-3 text-sm">
              Booking as <span className="font-semibold text-black">{user.full_name}</span>
              {user.phone ? <span className="text-gray-500"> · {user.phone}</span> : null}
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Input label={isManager ? "Customer name" : "Your name"} value={name} onChange={(e) => setName(e.target.value)} error={fieldErrors.name} placeholder="E.g. Rahul Sharma" />
              <Input
                label={isManager ? "Customer mobile" : "Mobile number"}
                value={phone}
                inputMode="numeric"
                maxLength={10}
                onChange={(e) => setPhone(e.target.value.replace(/\D/g, "").slice(0, 10))}
                error={fieldErrors.phone}
                placeholder="10-digit mobile"
                hint={isManager ? undefined : "Your booking updates and service code come here on WhatsApp."}
              />
            </div>
          )}

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
                        setPincode(e.target.value);
                        setCoverage("idle");
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
          {total > 0 && (
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
                    maxLength={10}
                    onChange={(e) => setAltPhone(e.target.value.replace(/\D/g, "").slice(0, 10))}
                    error={fieldErrors.altPhone}
                  />
                </div>
              </div>
            )}
          </div>

          <ServicePrepNotice services={allServices} />
        </div>
      )}
    </WizardShell>
  );
}
