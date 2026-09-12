import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Car, CheckCircle2, Gift, MapPin, Pencil, RotateCcw } from "lucide-react";
import { vehicleApi, addressApi } from "../../api/profile";
import { bookingPolicyApi, catalogApi, comboOfferApi, vehicleTypeApi, getSlotHolderKey, coverageApi } from "../../api/catalog";
import { bookingApi } from "../../api/booking";
import { couponApi, subscriptionApi } from "../../api/engagement";
import { useAuth } from "../../context/AuthContext";
import { PaymentCancelled, payWithRazorpay } from "../../lib/razorpay";
import { Badge, Button, Input, Select } from "../../components/ui";
import { LocationPicker, type LocationValue } from "../../components/shared/LocationPicker";
import { PhoneVerificationModal } from "../../components/shared/PhoneVerificationModal";
import { WizardShell, WizardStepHeader } from "../../components/shared/WizardShell";
import { SlotPicker } from "../../components/shared/SlotPicker";
import { ServicePrepNotice } from "../../components/shared/ServicePrepNotice";
import { getErrorMessage } from "../../lib/api-client";
import { useToast } from "../../context/ToastContext";
import { format } from "../../lib/date";
import { addonKit, baseGroups, bikeTypeIds, variantCount, type BaseGroup } from "../../lib/serviceMix";
import { estimatePlanTopUp, subscriptionCoversType } from "../../lib/planTier";
import { QtyStepper } from "../../components/shared/QtyStepper";
import type { Address, Booking, ComboOffer, Service, VehicleType } from "../../types";

/** A normal booking picks a vehicle and a service first. A PASS booking
 *  already knows both — it goes straight to where and when (founder model:
 *  "directly choose address, date, slot and book"). */
/** A booking that exists but isn't paid yet — created by "Pay online",
 *  then the customer closed the checkout. It stays HELD (the payment
 *  window) while they retry, switch to cash, or change their mind; the
 *  wizard reuses it as long as `signature` (everything they chose) still
 *  matches, and replaces it the moment anything changes. */
interface HeldBooking {
  id: string;
  groupId: string | null;
  token?: string;
  number: string;
  total: number;
  signature: string;
}

/** One car already added to a multi-vehicle visit. */
interface AddedCar {
  vehicleId: string;
  label: string;
  vehicleType: string;
  serviceIds: string[];
  serviceQty: Record<string, number>;
  serviceLabel: string;
  subtotal: number;
}

const FULL_STEPS = ["Vehicle & Service", "Address & Time", "Review & Pay"] as const;
const PASS_STEPS = ["Address & Time", "Add-ons & Confirm"] as const;
const STEP_HINTS: Record<string, string> = {
  vehicle: "Which vehicle and what service.",
  address: "Where and when should we come?",
  review: "Check the price and choose how to pay — including any subscription plan you own.",
  passReview: "Add anything extra, then confirm — the wash itself is on your pass.",
};

function priceFor(item: Service | ComboOffer, vehicleType: VehicleType): { price: number; firstTime: number | null } {
  const price = item.vehicle_type_prices?.[vehicleType] ?? item.price;
  const firstTime = item.vehicle_type_discounted_prices?.[vehicleType] ?? item.discounted_price ?? null;
  return { price, firstTime };
}

export default function NewBookingPage() {
  const navigate = useNavigate();
  // Booking WITH a plan is this exact wizard, not a separate flow: the
  // catalogue, the map, the slots and the confirm button are all the same
  // — the only difference is that the plan pays instead of the customer.
  // "?subscription=<id>" spends that specific plan; "?mode=plan" (the +
  // menu) spends the only usable one, or asks which on step 1.
  const [searchParams] = useSearchParams();
  const requestedSubscriptionId = searchParams.get("subscription");
  const planMode = searchParams.get("mode") === "plan" || !!requestedSubscriptionId;
  // "Book again" on a past booking: "?repeat=<booking id>" replays exactly
  // what was booked — the car, the services, the add-on counts and the
  // address — so the only thing left to choose is a new date and slot.
  const repeatBookingId = searchParams.get("repeat");
  // "?edit=<booking id>": the same replay, but the date and slot come along
  // and confirming REPLACES the original — it's cancelled first. Only an
  // unpaid or still-unassigned booking offers the button (detail page);
  // that cancel is what enforces it here.
  const editBookingId = searchParams.get("edit");
  const replayBookingId = editBookingId || repeatBookingId;
  // "?service=<id>": "Book now" on a service card on the public site,
  // landing here because the customer is already signed in.
  const wantedServiceId = searchParams.get("service");
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const { push: pushToast } = useToast();
  const [step, setStep] = useState(0);
  const [verifyOpen, setVerifyOpen] = useState(false);

  const [serviceIds, setServiceIds] = useState<string[]>([]);
  // Per-unit add-on counts (Extra Bike Wash ×N, Bike Polish ×N) — kept in
  // lockstep with serviceIds by the setters below; sent as-is to the API.
  const [serviceQty, setServiceQty] = useState<Record<string, number>>({});
  const [comboId, setComboId] = useState<string | null>(null);
  const [date, setDate] = useState("");
  const [slot, setSlot] = useState("");

  const [vehicleType, setVehicleType] = useState<VehicleType>("");
  const [carNumber, setCarNumber] = useState("");
  const [carModel, setCarModel] = useState("");
  const [addressLine, setAddressLine] = useState("");
  // The pin is the single source of truth (Swiggy pattern) — city/state/
  // pincode derive from it silently and are never asked separately.
  const [location, setLocation] = useState<LocationValue | null>(null);
  const [mapsDown, setMapsDown] = useState(false);
  const [landmark, setLandmark] = useState("");
  const [addressLat, setAddressLat] = useState<number | null>(null);
  const [addressLng, setAddressLng] = useState<number | null>(null);
  const [city, setCity] = useState("");
  const [state, setState] = useState("");
  const [pincode, setPincode] = useState("");
  const [notes, setNotes] = useState("");
  const [selectedAddressId, setSelectedAddressId] = useState<string | null>(null);
  const [showNewAddressForm, setShowNewAddressForm] = useState(false);
  const [altContactName, setAltContactName] = useState("");
  const [altContactPhone, setAltContactPhone] = useState("");

  const [showNewVehicleForm, setShowNewVehicleForm] = useState(false);
  // Cars already added to THIS visit. The editor below always configures
  // one more car; these are the ones already settled. Keeping them separate
  // means the single-car flow is untouched — a one-car booking never goes
  // near the group endpoint.
  const [extraCars, setExtraCars] = useState<AddedCar[]>([]);
  const [addCarError, setAddCarError] = useState("");

  const [couponCode, setCouponCode] = useState("");
  const [couponDiscount, setCouponDiscount] = useState(0);
  const [couponError, setCouponError] = useState("");
  const [paymentMethod, setPaymentMethod] = useState("cash");
  const [subscriptionId, setSubscriptionId] = useState<string | null>(null);

  const { data: vehicles } = useQuery({ queryKey: ["vehicles"], queryFn: vehicleApi.list });
  const { data: addresses } = useQuery({ queryKey: ["addresses"], queryFn: addressApi.list });
  const { data: servicesData, isLoading: servicesLoading } = useQuery({
    queryKey: ["services-for-booking"],
    queryFn: () => catalogApi.services({ page_size: 50 }),
  });
  const { data: combos } = useQuery({ queryKey: ["combos-for-booking"], queryFn: () => comboOfferApi.list(true) });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const { data: mySubscriptions } = useQuery({ queryKey: ["my-subscriptions"], queryFn: subscriptionApi.mySubscriptions });
  const { data: subscriptionPlans } = useQuery({ queryKey: ["subscription-plans-for-booking"], queryFn: () => subscriptionApi.plans(true) });
  const { data: bookingPolicy } = useQuery({ queryKey: ["booking-policy"], queryFn: bookingPolicyApi.get });
  const maxCars = bookingPolicy?.max_vehicles_per_booking ?? 5;

  // The booking being repeated, and — when it was one car of a visit — the
  // rest of that visit, so "Book again" on a three-car wash rebuilds all
  // three rather than just the car whose page they tapped it from.
  const { data: repeatSource, isError: repeatFailed } = useQuery({
    queryKey: ["booking", replayBookingId],
    queryFn: () => bookingApi.get(replayBookingId as string),
    enabled: !!replayBookingId,
    retry: false,
  });
  const repeatGroupId = repeatSource?.booking_group_id || null;
  const { data: repeatGroup } = useQuery({
    queryKey: ["booking-group", repeatGroupId],
    queryFn: () => bookingApi.getGroup(repeatGroupId as string),
    enabled: !!repeatGroupId,
  });
  // Flipped once the replay has run (or has been given up on). Until then
  // the "default vehicle"/"default address" effects below stand down, so
  // the customer never sees the defaults flash in and get replaced.
  const [repeatApplied, setRepeatApplied] = useState(false);
  const repeatPending = !!replayBookingId && !repeatApplied;
  /** The booking being edited — confirming replaces it. */
  const [editing, setEditing] = useState<{ id: string; groupId: string | null; number: string } | null>(null);
  const [held, setHeld] = useState<HeldBooking | null>(null);
  const [paying, setPaying] = useState(false);
  const paymentWindow = (bookingPolicy as { payment_window_minutes?: number } | undefined)?.payment_window_minutes ?? 30;

  // Slots are generated per service center, and which center applies is
  // only knowable once we have an address — resolved by pincode the same
  // way the backend's own routing does at booking-creation time (an exact
  // nearest-by-coordinates match can differ in rare edge cases, but the
  // backend's own resolution at submit time is always the authoritative
  // one regardless of what's shown here).
  const { data: matchedCenters } = useQuery({
    queryKey: ["center-lookup", pincode],
    queryFn: () => coverageApi.check({ pincode }),
    enabled: pincode.length >= 6 && addressLat == null,
  });
  // Zone-aware: when the address has a pin, THE PIN decides which center
  // serves it (and whether it's served at all); pincode lookup is only
  // the fallback for pinless addresses.
  const { data: pinCoverage } = useQuery({
    queryKey: ["pin-coverage", addressLat, addressLng],
    queryFn: () => coverageApi.check({ latitude: addressLat!, longitude: addressLng!, pincode }),
    enabled: addressLat != null && addressLng != null,
  });
  const pinlessCoverage = matchedCenters as unknown as { covered: boolean; center: { id: string } | null; pin_required?: boolean } | undefined;
  const serviceCenterId = addressLat != null
    ? (pinCoverage?.covered ? pinCoverage.center?.id : undefined)
    : (pinlessCoverage?.covered ? pinlessCoverage.center?.id : undefined);
  // Zones active + no pin -> the backend demands a real location.
  const pinRequired = addressLat == null && pinlessCoverage?.pin_required === true;

  // Every currently-usable subscription, independent of whatever vehicle
  // type is selected in the wizard below — the quick-book shortcut lets the
  // customer pick which of their matching-type vehicles to use, so it
  // isn't gated on the wizard's current selection the way the picker
  // above is.
  const allUsableSubscriptions = (mySubscriptions || []).filter((s) => s.effective_status === "active" && s.remaining_service_count > 0);
  const activeSubscription = subscriptionId ? (mySubscriptions || []).find((s) => s.id === subscriptionId) || null : null;
  // A PASS names its car AND its service, so there is nothing to choose on
  // step one — the wizard drops to two steps. (A pre-pass subscription,
  // which is scoped only by vehicle type, still uses the full flow.)
  const passMode = !!activeSubscription?.vehicle_id && !!activeSubscription?.service_id;
  const passPlate = vehicles?.find((v) => v.id === activeSubscription?.vehicle_id)?.registration_number || "";
  const stepKeys: string[] = passMode ? ["address", "review"] : ["vehicle", "address", "review"];
  const STEPS: string[] = [...(passMode ? PASS_STEPS : FULL_STEPS)];
  const stepKey = stepKeys[Math.min(step, stepKeys.length - 1)];
  useEffect(() => {
    // Choosing a pass mid-flow drops the wizard from three steps to two —
    // don't strand the customer on a step that no longer exists.
    if (step > stepKeys.length - 1) setStep(stepKeys.length - 1);
  }, [step, stepKeys.length]);
  const planNameOf = (sub: { plan_id: string }) => subscriptionPlans?.find((p) => p.id === sub.plan_id)?.name || "";

  useEffect(() => {
    if (repeatPending) return; // the booking being repeated names the car
    const defaultVehicle = vehicles?.find((v) => v.is_default) || vehicles?.[0];
    if (defaultVehicle && !carNumber) {
      setVehicleType(defaultVehicle.vehicle_type);
      setCarNumber(defaultVehicle.registration_number);
      setCarModel(`${defaultVehicle.brand} ${defaultVehicle.model}`.trim());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicles, repeatPending]);

  // Default the type selector to the first admin-configured type — ONLY
  // for customers with no saved vehicles (a saved vehicle sets the real
  // type above; racing this against that query mispriced the services).
  useEffect(() => {
    if (vehicles === undefined) return; // wait for the vehicles query
    if (vehicles.length) return;
    if (vehicleTypes?.length && !vehicleType) setVehicleType(vehicleTypes[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicleTypes, vehicles]);

  // Whatever the load order, the selected saved vehicle's type is the
  // truth for pricing — realign if anything ever knocks them apart.
  useEffect(() => {
    const match = vehicles?.find((v) => v.registration_number.toUpperCase() === carNumber.toUpperCase());
    if (match && match.vehicle_type !== vehicleType) setVehicleType(match.vehicle_type);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicles, carNumber, vehicleType]);

  useEffect(() => {
    if (repeatPending) return; // the booking being repeated names the address
    const defaultAddress = addresses?.find((a) => a.is_default) || addresses?.[0];
    if (defaultAddress && !addressLine) {
      setAddressLine(defaultAddress.line1);
      setLandmark(defaultAddress.landmark || "");
      setCity(defaultAddress.city);
      setState(defaultAddress.state);
      setPincode(defaultAddress.pincode);
      setAddressLat(defaultAddress.latitude ?? null);
      setAddressLng(defaultAddress.longitude ?? null);
      setSelectedAddressId(defaultAddress.id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addresses, repeatPending]);

  // Arriving from "Book with my plan" / a plan card: pick the plan to
  // spend, once, as soon as the subscriptions load. A named plan wins; a
  // bare ?mode=plan auto-picks only when there's exactly one usable plan
  // (otherwise step 1 asks which).
  const [planPreselected, setPlanPreselected] = useState(false);
  useEffect(() => {
    if (!planMode || planPreselected || !mySubscriptions) return;
    const usable = mySubscriptions.filter((s) => s.effective_status === "active" && s.remaining_service_count > 0);
    const chosen = requestedSubscriptionId
      ? usable.find((s) => s.id === requestedSubscriptionId)
      : usable.length === 1
        ? usable[0]
        : undefined;
    if (chosen) setSubscriptionId(chosen.id);
    setPlanPreselected(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planMode, mySubscriptions, requestedSubscriptionId]);

  // A plan only covers certain vehicle TYPES. When one is being spent,
  // move the wizard onto a vehicle it actually covers instead of letting
  // the customer build a whole booking the backend will refuse.
  useEffect(() => {
    if (!subscriptionId || !vehicles?.length) return;
    const sub = mySubscriptions?.find((s) => s.id === subscriptionId);
    if (!sub) return;
    // A PASS belongs to one specific car — use that one, full stop.
    if (sub.vehicle_id) {
      const owned = vehicles.find((v) => v.id === sub.vehicle_id);
      if (owned && owned.registration_number.toUpperCase() !== carNumber.toUpperCase()) selectVehicle(owned);
      return;
    }
    const plan = subscriptionPlans?.find((p) => p.id === sub.plan_id);
    if (vehicleType && subscriptionCoversType(sub, plan, vehicleType)) return;
    const covered = vehicles.find((v) => subscriptionCoversType(sub, plan, v.vehicle_type));
    if (covered) selectVehicle(covered);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subscriptionId, vehicles, mySubscriptions, subscriptionPlans]);

  // ...and if the customer then switches to a vehicle the plan can't cover,
  // drop the plan rather than sending a booking that would be rejected at
  // the last step. The step-3 picker offers it back the moment it fits.
  useEffect(() => {
    if (!subscriptionId || !vehicleType) return;
    const sub = mySubscriptions?.find((s) => s.id === subscriptionId);
    const plan = subscriptionPlans?.find((p) => p.id === sub?.plan_id);
    if (sub && !subscriptionCoversType(sub, plan, vehicleType)) setSubscriptionId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicleType, subscriptionId, mySubscriptions, subscriptionPlans]);

  // Tapping a saved address fills every field from it and remembers its id
  // directly — skips the fuzzy "does this line1+pincode match something
  // saved" guess the create-mutation otherwise has to make, and guarantees
  // no duplicate address gets silently created for a retyped one.
  const selectAddress = (a: Address) => {
    setAddressLine(a.line1);
    setLandmark(a.landmark || "");
    setCity(a.city);
    setState(a.state);
    setPincode(a.pincode);
    setAddressLat(a.latitude ?? null);
    setAddressLng(a.longitude ?? null);
    setSelectedAddressId(a.id);
    setShowNewAddressForm(false);
    setLocation(null);
  };

  /** Drop back to a blank, map-pinned address — the ONLY path that asks
   *  the customer to pin, and the one "+ New address" takes everywhere. */
  const startNewAddress = () => {
    setShowNewAddressForm(true);
    setSelectedAddressId(null);
    setLocation(null);
    setAddressLine("");
    setLandmark("");
    setCity("");
    setState("");
    setPincode("");
    setAddressLat(null);
    setAddressLng(null);
    setDate("");
    setSlot("");
  };

  // The saved address currently in play (null while adding a new one) —
  // it already carries the coordinates pinned when it was saved, which is
  // why this branch never asks for the map again.
  const savedAddress = !showNewAddressForm && selectedAddressId
    ? (addresses || []).find((a) => a.id === selectedAddressId) || null
    : null;

  const services = servicesData?.data || [];
  const passServiceName = services.find((s) => s.id === activeSubscription?.service_id)?.name || "";
  const selectedServices = services.filter((s) => serviceIds.includes(s.id));
  const selectedCombo = combos?.find((c) => c.id === comboId) || null;
  const isFirstOrderGuess = !vehicles?.length; // best-effort UI hint only — backend is the source of truth on eligibility

  // The catalogue's add-on/base rules (mirrors the server's enforcement —
  // see lib/serviceMix.ts): base services collapsed by variant group, and
  // add-ons matched to the vehicle's class (car vs bike).
  const bikeIds = useMemo(() => bikeTypeIds(vehicleTypes), [vehicleTypes]);
  const bookingIsBike = bikeIds.has(vehicleType);
  const groups = useMemo(() => baseGroups(services, vehicleType), [services, vehicleType]);
  const kit = useMemo(() => addonKit(services, vehicleType, bikeIds), [services, vehicleType, bikeIds]);
  // The plan already says what it covers — start the catalogue on that
  // service so a plan booking is two taps, while leaving every other
  // service (and every add-on) selectable exactly as normal.
  useEffect(() => {
    if (!subscriptionId || comboId || !services.length) return;
    const sub = mySubscriptions?.find((s) => s.id === subscriptionId);
    // A pass covers exactly its own service: keep it selected even if the
    // customer had picked something else before choosing the pass, since
    // the backend refuses any other main service on a pass booking.
    if (sub?.service_id) {
      if (!serviceIds.includes(sub.service_id)) {
        setServiceIds((prev) => [sub.service_id!, ...prev.filter((id) => services.find((s) => s.id === id)?.is_addon)]);
      }
      return;
    }
    if (serviceIds.length) return;
    const plan = subscriptionPlans?.find((p) => p.id === sub?.plan_id);
    const includedId = plan?.included_service_ids?.[0];
    if (!includedId) return;
    const group = groups.find((g) => g.variants.some((v) => v.id === includedId));
    if (group) setServiceIds([group.primary.id]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subscriptionId, groups, services, mySubscriptions, subscriptionPlans]);

  const selectedBase = selectedServices.find((s) => !s.is_addon) || null;
  const selectedGroup = selectedBase ? groups.find((g) => g.variants.some((v) => v.id === selectedBase.id)) || null : null;
  const bikeCount = bookingIsBike && selectedBase ? variantCount(selectedBase) : 0;
  const extraBikes = kit.addBike && serviceIds.includes(kit.addBike.id) ? serviceQty[kit.addBike.id] || 1 : 0;
  const polishCount = kit.bikePolish && serviceIds.includes(kit.bikePolish.id) ? serviceQty[kit.bikePolish.id] || 1 : 0;
  // Total bikes: on a bike booking the −/+ counter books base wash + N
  // extra-bike lines (₹99 + ₹60 each additional); on a car booking bikes
  // come purely from the add-bike line.
  const bikesInBooking = bookingIsBike ? bikeCount + extraBikes : extraBikes;
  const qtyOf = (id: string) => serviceQty[id] || 1;

  const subtotal = useMemo(() => {
    if (selectedCombo) {
      const { price, firstTime } = priceFor(selectedCombo, vehicleType);
      return isFirstOrderGuess && firstTime != null ? firstTime : price;
    }
    return selectedServices.reduce((sum, s) => {
      const { price, firstTime } = priceFor(s, vehicleType);
      return sum + (isFirstOrderGuess && firstTime != null ? firstTime : price) * (serviceQty[s.id] || 1);
    }, 0);
  }, [selectedServices, selectedCombo, vehicleType, isFirstOrderGuess, serviceQty]);

  // A subscription (when picked) covers the MAIN service — add-ons and any
  // swap-to-costlier gap stay a real charge (mirrors the backend's
  // _subscription_discount; coupons are ignored on subscription bookings).
  const planTopUp = useMemo(() => {
    if (!subscriptionId || selectedCombo) return 0;
    const sub = mySubscriptions?.find((s) => s.id === subscriptionId);
    const plan = subscriptionPlans?.find((p) => p.id === sub?.plan_id);
    return estimatePlanTopUp(selectedServices, qtyOf, plan, services, (s) => priceFor(s, vehicleType).price);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subscriptionId, selectedServices, selectedCombo, mySubscriptions, subscriptionPlans, services, vehicleType, serviceQty]);
  const total = subscriptionId ? planTopUp : Math.max(subtotal - couponDiscount, 0);

  /** The id of the car currently in the editor, saving it to the garage
   *  first if it's a plate we haven't seen. */
  const resolveCurrentVehicleId = async (): Promise<string> => {
    const existing = vehicles?.find((v) => v.registration_number.toUpperCase() === carNumber.toUpperCase())?.id;
    if (existing) return existing;
    const [brand, ...modelParts] = carModel.trim().split(" ");
    const vehicle = await vehicleApi.create({
      vehicle_type: vehicleType,
      brand: brand || "Vehicle",
      model: modelParts.join(" ") || carModel || "—",
      registration_number: carNumber.toUpperCase(),
      is_default: !vehicles?.length,
    });
    await queryClient.invalidateQueries({ queryKey: ["vehicles"] });
    return vehicle.id;
  };

  const resolveAddressId = async (): Promise<string> => {
    const derivedLine1 = location ? location.formatted || [location.area, location.city].filter(Boolean).join(", ") : addressLine;
    const existing = !showNewAddressForm && selectedAddressId
      ? selectedAddressId
      : addresses?.find((a) => a.line1 === derivedLine1 && a.pincode === pincode)?.id;
    if (existing) return existing;
    const address = await addressApi.create({
      label: "Doorstep",
      line1: derivedLine1,
      landmark: landmark || undefined,
      city,
      state,
      pincode,
      latitude: addressLat ?? undefined,
      longitude: addressLng ?? undefined,
      is_default: !addresses?.length,
    });
    return address.id;
  };

  /** That car's own monthly pass, if it has one covering this wash. A pass
   *  belongs to one car, so on a multi-car visit each car redeems its own. */
  const passForCar = (vehicleId: string, ids: string[]): string | undefined =>
    (mySubscriptions || []).find(
      (sub) =>
        sub.effective_status === "active" &&
        sub.remaining_service_count > 0 &&
        sub.vehicle_id === vehicleId &&
        !!sub.service_id &&
        ids.includes(sub.service_id)
    )?.id;

  // A pass belongs to ONE car, so a pass booking is a one-car booking by
  // definition — the multi-car controls stay out of that flow.
  const canAddMoreCars = !passMode && extraCars.length + 1 < maxCars;
  const currentCarReady = !!carNumber && !!selectedBase && !selectedCombo;

  const addCurrentCar = async () => {
    setAddCarError("");
    try {
      const vehicleId = await resolveCurrentVehicleId();
      if (extraCars.some((c) => c.vehicleId === vehicleId)) {
        setAddCarError("That car is already on this visit.");
        return;
      }
      setExtraCars((cars) => [
        ...cars,
        {
          vehicleId,
          label: `${carModel || "Vehicle"} · ${carNumber.toUpperCase()}`,
          vehicleType,
          serviceIds: [...serviceIds],
          serviceQty: { ...serviceQty },
          serviceLabel: chosenService,
          subtotal,
        },
      ]);
      // Clear the editor for the NEXT car — the vehicle picker reopens and
      // the service selection starts fresh, because the next car is very
      // likely a different type needing different services.
      setCarNumber("");
      setCarModel("");
      setShowNewVehicleForm(false);
      clearServices();
      setComboId(null);
    } catch (err) {
      setAddCarError(getErrorMessage(err));
    }
  };

  const removeCar = (vehicleId: string) => setExtraCars((cars) => cars.filter((c) => c.vehicleId !== vehicleId));

  const extrasSubtotal = extraCars.reduce((sum, c) => sum + c.subtotal, 0);
  /** Every service across the whole visit — one waterless car means the
   *  whole visit needs a shaded spot, so the prep checklist reads them all. */
  const visitServices = [
    ...selectedServices,
    ...extraCars.flatMap((c) => c.serviceIds.map((id) => services.find((s) => s.id === id)).filter(Boolean)),
  ] as Service[];

  /** Everything the customer has chosen, as one string. A held (unpaid)
   *  booking is reused only while this is unchanged — any edit means a
   *  fresh booking, and the held one goes back. */
  const currentSignature = () =>
    JSON.stringify({
      car: carNumber.toUpperCase(),
      vt: vehicleType,
      services: serviceIds,
      qty: serviceQty,
      combo: comboId,
      extras: extraCars.map((c) => [c.vehicleId, c.serviceIds, c.serviceQty]),
      date,
      slot,
      address: selectedAddressId || addressLine,
      pin: [addressLat, addressLng],
      sub: subscriptionId,
      coupon: couponDiscount > 0 ? couponCode : null,
      notes,
      alt: [altContactName, altContactPhone],
    });
  const heldIsCurrent = !!held && held.signature === currentSignature();

  /** Open Razorpay for a held booking and STAY on this page if it doesn't
   *  complete — the customer can retry, pick cash, or change something.
   *  Dragging them to the booking page took every one of those away. */
  const attemptPayment = async (h: HeldBooking): Promise<boolean> => {
    setPaying(true);
    try {
      await payWithRazorpay(
        h.groupId ? { purpose: "booking_group", booking_group_id: h.groupId } : { purpose: "booking", booking_id: h.id },
        { name: user?.full_name, email: user?.email, contact: user?.phone }
      );
    } catch (err) {
      setPaying(false);
      if (err instanceof PaymentCancelled) {
        pushToast({
          tone: "info",
          title: "Payment not completed",
          message: `${h.number} is held for you for ${paymentWindow} minutes — retry the payment, or choose cash on service.`,
        });
      } else {
        pushToast({ tone: "error", title: "Payment didn't go through", message: getErrorMessage(err) });
      }
      return false;
    }
    setPaying(false);
    setHeld(null);
    queryClient.invalidateQueries({ queryKey: ["my-bookings"] });
    navigate(h.token ? `/thank-you?token=${h.token}` : "/app/bookings");
    return true;
  };

  /** Before creating anything, retire what the new booking supersedes: an
   *  earlier unpaid attempt with different choices, and — in edit mode —
   *  the original booking, which confirming replaces. */
  const prepareToCreate = async () => {
    if (held) {
      try {
        if (held.groupId) await bookingApi.cancelGroup(held.groupId, "Changed before paying");
        else await bookingApi.cancel(held.id, "Changed before paying");
      } catch {
        // Already released (payment window ran out) — nothing to undo.
      }
      setHeld(null);
    }
    if (editing) {
      if (editing.groupId) await bookingApi.cancelGroup(editing.groupId, "Edited by customer — replaced by a new booking");
      else await bookingApi.cancel(editing.id, "Edited by customer — replaced by a new booking");
      setEditing(null);
    }
  };

  // "Pay cash instead" on a held online booking — it's confirmed as it is.
  const switchHeldMutation = useMutation({
    mutationFn: async () => {
      if (!held) return;
      if (held.groupId) await bookingApi.switchGroupToCash(held.groupId);
      else await bookingApi.switchToCash(held.id);
    },
    onSuccess: () => {
      const h = held;
      setHeld(null);
      queryClient.invalidateQueries({ queryKey: ["my-bookings"] });
      navigate(h?.token ? `/thank-you?token=${h.token}` : "/app/bookings");
    },
    onError: (err) => pushToast({ tone: "error", title: "Couldn't switch to cash", message: getErrorMessage(err) }),
  });

  const createGroupMutation = useMutation({
    mutationFn: async () => {
      await prepareToCreate();
      const currentVehicleId = await resolveCurrentVehicleId();
      const addressId = await resolveAddressId();
      const cars = [
        ...extraCars.map((c) => ({
          vehicle_id: c.vehicleId,
          service_ids: c.serviceIds,
          service_quantities: c.serviceQty,
          subscription_id: passForCar(c.vehicleId, c.serviceIds),
        })),
        {
          vehicle_id: currentVehicleId,
          service_ids: serviceIds,
          service_quantities: serviceQty,
          subscription_id: passForCar(currentVehicleId, serviceIds),
        },
      ];
      return bookingApi.createGroup({
        vehicles: cars,
        address_id: addressId,
        scheduled_date: date,
        scheduled_slot: slot,
        hold_key: getSlotHolderKey(),
        payment_method: paymentMethod,
        customer_notes: notes || undefined,
        alternate_contact_name: altContactName || undefined,
        alternate_contact_phone: altContactPhone || undefined,
      });
    },
    onSuccess: async (visit) => {
      queryClient.invalidateQueries({ queryKey: ["my-bookings"] });
      // ONE payment covers the whole visit — every car on it is settled by
      // the same verified signature. If it doesn't complete, the visit is
      // held and the customer stays right here.
      const unpaid = visit.bookings.some((b) => b.status === "awaiting_payment");
      if (unpaid && visit.total_amount > 0) {
        const h: HeldBooking = {
          id: visit.bookings[0].id,
          groupId: visit.booking_group_id,
          token: visit.confirmation_token,
          number: visit.bookings.map((b) => b.booking_number).join(" + "),
          total: visit.total_amount,
          signature: currentSignature(),
        };
        setHeld(h);
        await attemptPayment(h);
        return;
      }
      navigate(visit.confirmation_token ? `/thank-you?token=${visit.confirmation_token}` : "/app/bookings");
    },
    onError: (err) => pushToast({ tone: "error", title: "Couldn't book this visit", message: getErrorMessage(err) }),
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      await prepareToCreate();
      const vehicleId = await resolveCurrentVehicleId();

      const derivedLine1 = location ? location.formatted || [location.area, location.city].filter(Boolean).join(", ") : addressLine;
      void derivedLine1;
      const addressId = await resolveAddressId();

      return bookingApi.create({
        vehicle_id: vehicleId,
        address_id: addressId,
        service_ids: selectedCombo ? undefined : serviceIds,
        service_quantities: selectedCombo ? undefined : serviceQty,
        combo_id: selectedCombo ? selectedCombo.id : undefined,
        scheduled_date: date,
        scheduled_slot: slot,
        hold_key: getSlotHolderKey(),
        subscription_id: subscriptionId || undefined,
        payment_method: subscriptionId ? undefined : paymentMethod,
        coupon_code: !subscriptionId && couponDiscount > 0 ? couponCode : undefined,
        customer_notes: notes || undefined,
        alternate_contact_name: altContactName || undefined,
        alternate_contact_phone: altContactPhone || undefined,
      });
    },
    onSuccess: async (booking) => {
      queryClient.invalidateQueries({ queryKey: ["my-bookings"] });
      // Online payment: the booking exists either way — launch Razorpay
      // now; if the customer closes the modal, the booking stays
      // payment-pending and the detail page offers "Pay online" to
      // finish later.
      // The backend parks anything that must be paid before it counts as a
      // booking — an online-pay wash, or a pass booking with paid add-ons
      // (no cash option on those). Whatever it parked, finish paying now.
      if (booking.status === "awaiting_payment" && booking.total_amount > 0) {
        const h: HeldBooking = {
          id: booking.id,
          groupId: null,
          token: booking.confirmation_token,
          number: booking.booking_number,
          total: booking.total_amount,
          signature: currentSignature(),
        };
        setHeld(h);
        await attemptPayment(h);
        return;
      }
      // A fixed, opaque, single-purpose token (not the raw booking id) so
      // the confirmation page can't be reached by guessing or bookmarking
      // a URL — see PurchaseConfirmationModel / ThankYouPage.
      navigate(`/thank-you?token=${booking.confirmation_token}`);
    },
    onError: (err) => {
      pushToast({ tone: "error", title: "Couldn't confirm this booking", message: getErrorMessage(err) });
    },
  });

  // Changing the selection changes subtotal, and a coupon discount computed
  // against the OLD subtotal would otherwise sit there stale — the preview
  // total on step 3 could show a number the backend would never actually
  // charge (it recomputes the coupon correctly at submit time regardless,
  // so this was never an overcharge risk, just a misleading preview).
  const resetCoupon = () => {
    setCouponCode("");
    setCouponDiscount(0);
    setCouponError("");
  };

  // One main service per booking; add-ons stack on top of it and are
  // cleared whenever the main service (or the vehicle's class) changes —
  // a car add-on has no business surviving a switch to a bike wash.
  const clearServices = () => {
    setServiceIds([]);
    setServiceQty({});
    resetCoupon();
  };
  const pickGroup = (g: BaseGroup) => {
    setComboId(null);
    setServiceQty({});
    setServiceIds(selectedGroup?.key === g.key ? [] : [g.primary.id]);
    resetCoupon();
  };
  const toggleSimpleAddon = (id: string) => {
    setComboId(null);
    setServiceIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
    resetCoupon();
  };
  const setExtraBikes = (n: number) => {
    if (!kit.addBike) return;
    const id = kit.addBike.id;
    n = Math.max(0, Math.min(10, n));
    setServiceIds((prev) => (n > 0 ? (prev.includes(id) ? prev : [...prev, id]) : prev.filter((x) => x !== id)));
    setServiceQty((prev) => {
      const next = { ...prev };
      if (n > 0) next[id] = n;
      else delete next[id];
      return next;
    });
    const totalBikes = bookingIsBike ? bikeCount + n : n;
    if (kit.bikePolish && polishCount > totalBikes) setPolishRaw(totalBikes);
    resetCoupon();
  };
  const setPolishRaw = (n: number) => {
    if (!kit.bikePolish) return;
    const id = kit.bikePolish.id;
    setServiceIds((prev) => (n > 0 ? (prev.includes(id) ? prev : [...prev, id]) : prev.filter((x) => x !== id)));
    setServiceQty((prev) => {
      const next = { ...prev };
      if (n > 0) next[id] = n;
      else delete next[id];
      return next;
    });
  };
  const setPolish = (n: number) => {
    setPolishRaw(Math.max(0, Math.min(bikesInBooking, n)));
    resetCoupon();
  };
  const selectCombo = (id: string) => {
    setServiceIds([]);
    setServiceQty({});
    setComboId((prev) => (prev === id ? null : id));
    resetCoupon();
  };

  // Tapping a saved vehicle fills the plate/model/type fields exactly as
  // stored — setting the correct vehicleType is what actually drives
  // subscription eligibility now (see eligibleSubscriptions above).
  const selectVehicle = (v: NonNullable<typeof vehicles>[number]) => {
    if (v.vehicle_type !== vehicleType) clearServices(); // car vs bike changes what's bookable
    setVehicleType(v.vehicle_type);
    setCarNumber(v.registration_number);
    setCarModel(`${v.brand} ${v.model}`.trim());
    setShowNewVehicleForm(false);
    resetCoupon();
  };

  /** How much of an old booking can still be booked today. The car has to
   *  still be in the garage and the services still on the menu FOR that car
   *  — a sold car or a discontinued wash can't be replayed, and quietly
   *  substituting something else would be worse than saying so. */
  const replayCar = (b: Booking) => {
    const vehicle = vehicles?.find((v) => v.id === b.vehicle_id);
    if (!vehicle) return null;
    const blank = { vehicle, serviceIds: [] as string[], serviceQty: {} as Record<string, number> };
    if (b.combo_id) {
      const combo = combos?.find((c) => c.id === b.combo_id);
      return combo ? { ...blank, comboId: combo.id } : null;
    }
    // Exactly what the picker itself would offer this car today — built
    // from the same base groups and add-on kit, so a repeat can never
    // preselect something the customer couldn't have chosen by hand.
    const offerable = new Set<string>();
    for (const g of baseGroups(services, vehicle.vehicle_type)) for (const v of g.variants) offerable.add(v.id);
    const carKit = addonKit(services, vehicle.vehicle_type, bikeIds);
    for (const a of carKit.simple) offerable.add(a.id);
    if (carKit.addBike) offerable.add(carKit.addBike.id);
    if (carKit.bikePolish) offerable.add(carKit.bikePolish.id);

    const ids = (b.service_ids || []).filter((id) => offerable.has(id));
    // An add-on never rides alone: without its main service there is no
    // booking left to repeat.
    if (!ids.some((id) => { const s = services.find((x) => x.id === id); return !!s && !s.is_addon; })) return null;
    const qty: Record<string, number> = {};
    for (const [id, n] of Object.entries(b.service_quantities || {})) if (ids.includes(id) && n > 1) qty[id] = n;
    return { ...blank, comboId: null as string | null, serviceIds: ids, serviceQty: qty };
  };

  // "Book again": replay everything the customer chose last time — car,
  // services, add-on counts, address, notes — and leave only the one thing
  // that can't be repeated, WHEN. Runs once, after the catalogue and the
  // customer's own garage have loaded, and lands on the date/slot step.
  useEffect(() => {
    if (!replayBookingId || repeatApplied) return;
    if (repeatFailed) {
      setRepeatApplied(true); // gone, or not theirs — carry on with an empty wizard
      return;
    }
    if (!repeatSource || !vehicles || !addresses || !services.length || !vehicleTypes?.length) return;
    if (repeatGroupId && !repeatGroup) return;
    if (mySubscriptions === undefined) return;
    setRepeatApplied(true);

    // Cars in the order they were washed, because that's the order that
    // prices them the same way (the first-visit offer lands on one car).
    const before: Booking[] = repeatGroup?.length ? repeatGroup : [repeatSource];
    const usable = before.map(replayCar).filter(Boolean) as NonNullable<ReturnType<typeof replayCar>>[];
    if (!usable.length) {
      pushToast({
        tone: "error",
        title: "Couldn't repeat that booking",
        message: "That vehicle or service isn't available any more — please choose again.",
      });
      return;
    }

    // The editor always holds the LAST car; the ones before it are already
    // settled on the visit — the same shape "add more cars" builds. A combo
    // is single-car only, so it can never be one of the settled ones.
    const settled = usable.slice(0, -1).filter((c) => !c.comboId);
    const editing = usable[usable.length - 1];
    if (usable.length < before.length || settled.length < usable.length - 1) {
      pushToast({
        tone: "info",
        title: "Some of that booking couldn't be repeated",
        message: "A vehicle or service from it is no longer available — the rest is ready below.",
      });
    }

    setExtraCars(
      settled.map((car) => {
        const picked = car.serviceIds.map((id) => services.find((s) => s.id === id)).filter(Boolean) as Service[];
        const qtyFor = (id: string) => car.serviceQty[id] || 1;
        return {
          vehicleId: car.vehicle.id,
          label: `${`${car.vehicle.brand} ${car.vehicle.model}`.trim()} · ${car.vehicle.registration_number}`,
          vehicleType: car.vehicle.vehicle_type,
          serviceIds: car.serviceIds,
          serviceQty: car.serviceQty,
          serviceLabel: picked.map((s) => s.name + (qtyFor(s.id) > 1 ? ` ×${qtyFor(s.id)}` : "")).join(", "),
          subtotal: picked.reduce((sum, s) => sum + priceFor(s, car.vehicle.vehicle_type).price * qtyFor(s.id), 0),
        };
      })
    );

    // selectVehicle clears the service picks when the class changes, so the
    // selection has to be written after it, not before.
    selectVehicle(editing.vehicle);
    setComboId(editing.comboId ?? null);
    setServiceIds(editing.serviceIds);
    setServiceQty(editing.serviceQty);

    const address = addresses.find((a) => a.id === repeatSource.address_id);
    if (address) selectAddress(address); // a saved address never re-asks for the map
    setNotes(repeatSource.customer_notes || "");
    setAltContactName(repeatSource.alternate_contact_name || "");
    setAltContactPhone(repeatSource.alternate_contact_phone || "");

    // A monthly pass belongs to one car and one wash — if this car still has
    // one covering it, spend that instead of charging for the wash again.
    const pass = settled.length ? undefined : passForCar(editing.vehicle.id, editing.serviceIds);
    if (pass) setSubscriptionId(pass);
    if (editBookingId) {
      // Editing: the old date and slot come along too, and the wizard opens
      // on the review step — every earlier step is one tap away on the rail.
      setDate(String(repeatSource.scheduled_date).slice(0, 10));
      setSlot(repeatSource.scheduled_slot);
      if (repeatSource.payment_method === "online" || repeatSource.payment_method === "cash") setPaymentMethod(repeatSource.payment_method);
      setEditing({ id: repeatSource.id, groupId: repeatSource.booking_group_id || null, number: repeatSource.booking_number });
      setStep(pass ? 1 : 2);
      return;
    }
    // Everything is chosen but the date and slot: go straight to them. (A
    // pass booking has no vehicle step, so its address step is index 0.)
    setStep(pass ? 0 : 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replayBookingId, repeatApplied, repeatFailed, repeatSource, repeatGroup, repeatGroupId, vehicles, addresses, servicesData, vehicleTypes, combos, mySubscriptions]);

  // A "Book now" on a specific service: start the catalogue on it, once,
  // if it's offered for the vehicle in the editor.
  useEffect(() => {
    if (!wantedServiceId || replayBookingId || planMode || serviceIds.length || !groups.length) return;
    const group = groups.find((g) => g.variants.some((v) => v.id === wantedServiceId));
    if (group) setServiceIds([group.primary.id]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantedServiceId, groups]);


  const applyCoupon = async () => {
    setCouponError("");
    if (!couponCode) return;
    try {
      const result = await couponApi.validate(couponCode, subtotal);
      setCouponDiscount(result.discount_amount);
      setSubscriptionId(null);
    } catch (err) {
      setCouponDiscount(0);
      setCouponError(getErrorMessage(err));
    }
  };

  const hasSelection = !!selectedBase || !!selectedCombo; // an add-on never rides alone
  // A saved address already carries its own coordinates; otherwise the PIN
  // is the address (typed line only in the no-maps fallback).
  const addressStepValid =
    (selectedAddressId ? true : mapsDown ? !!addressLine : !!location) && !!city && !!state && !!pincode && !!date && !!slot;
  const stepValid = { vehicle: !!carNumber && hasSelection, address: addressStepValid, review: true }[stepKey] ?? true;

  // What's chosen so far, as one quiet line above the action — replaces
  // the old summary sidebar (the reference layout has no third column).
  const chosenService = selectedCombo
    ? selectedCombo.name
    : selectedServices.length
      ? selectedServices.map((s) => s.name + (qtyOf(s.id) > 1 ? ` ×${qtyOf(s.id)}` : "")).join(", ")
      : "";
  // Service DURATION is never shown to a customer (founder call) — it
  // drives scheduling server-side only. This line is what's chosen and when.
  const summaryLine = [
    extraCars.length ? `${extraCars.length + (chosenService ? 1 : 0)} vehicles` : chosenService,
    date && slot ? `${date} · ${slot}` : "",
  ]
    .filter(Boolean)
    .join("  ·  ");

  // The add-on kit, hoisted so it can render in TWO places: after the
  // main service on a normal booking, and on the confirm step of a PASS
  // booking — where there is no service to pick, only extras to add.
  const addonsBlock = (
    <>
                    {/* Add-ons — only the ones valid for what's selected */}
                    {selectedBase && (kit.simple.length > 0 || kit.addBike || kit.bikePolish) && (
                      <div className="mt-4">
                        <p className="mb-2 text-sm font-medium text-black">Add-ons</p>
                        <div className="space-y-2.5">
                          {kit.simple.map((s) => {
                            const on = serviceIds.includes(s.id);
                            const { price, firstTime } = priceFor(s, vehicleType);
                            const shown = isFirstOrderGuess && firstTime != null ? firstTime : price;
                            return (
                              <button
                                key={s.id}
                                onClick={() => toggleSimpleAddon(s.id)}
                                className={`flex w-full items-center justify-between rounded-xl border px-3.5 py-2.5 text-sm transition-colors ${
                                  on ? "border-[var(--color-primary)] bg-[var(--color-primary-light)]" : "border-gray-200 hover:border-gray-300"
                                }`}
                              >
                                <span className="font-medium text-[var(--color-text-primary)]">+ {s.name}</span>
                                <span className="font-mono-num text-[var(--color-text-primary)]">₹{shown}</span>
                              </button>
                            );
                          })}

                          {!bookingIsBike && kit.addBike && (
                            <div className="flex items-center justify-between rounded-xl border border-gray-200 px-3.5 py-2.5 text-sm">
                              <div>
                                <p className="font-medium text-[var(--color-text-primary)]">+ Add bikes to this visit</p>
                                <p className="text-xs text-[var(--color-text-secondary)]">₹{priceFor(kit.addBike, vehicleType).price} per bike, washed at the same doorstep</p>
                              </div>
                              <QtyStepper value={extraBikes} min={0} max={10} onChange={setExtraBikes} />
                            </div>
                          )}

                          {kit.bikePolish && bikesInBooking > 0 && (
                            <div className="flex items-center justify-between rounded-xl border border-gray-200 px-3.5 py-2.5 text-sm">
                              <div>
                                <p className="font-medium text-[var(--color-text-primary)]">+ {kit.bikePolish.name}</p>
                                <p className="text-xs text-[var(--color-text-secondary)]">
                                  ₹{priceFor(kit.bikePolish, vehicleType).price} per bike · up to {bikesInBooking} bike{bikesInBooking > 1 ? "s" : ""}
                                </p>
                              </div>
                              <QtyStepper value={polishCount} min={0} max={bikesInBooking} onChange={setPolish} />
                            </div>
                          )}
                        </div>
                      </div>
                    )}
    </>
  );

  const wizardFooter = (
    <div className="space-y-4">
      {(summaryLine || total > 0 || activeSubscription) && (
        <div className="flex flex-wrap items-end justify-between gap-2">
          <span className="min-w-0 flex-1 truncate text-xs text-gray-500">{summaryLine || "Nothing selected yet"}</span>
          {activeSubscription && total === 0 ? (
            <span className="text-sm font-bold text-[var(--color-success)]">Covered by your plan</span>
          ) : (
            <span className="font-mono-num text-lg font-bold text-black">
              ₹{(total || 0) + extrasSubtotal}
              {activeSubscription && <span className="ml-1.5 text-[11px] font-normal text-gray-400">extra</span>}
            </span>
          )}
        </div>
      )}
      <div className="flex items-center justify-between gap-3">
        <Button variant="outline" onClick={() => (step > 0 ? setStep((s) => s - 1) : navigate(-1))}>
          Back
        </Button>
        {step < STEPS.length - 1 ? (
          <Button className="min-w-[140px]" disabled={!stepValid} onClick={() => setStep((s) => s + 1)}>
            Continue
          </Button>
        ) : (
          <Button
            className="min-w-[140px]"
            isLoading={createMutation.isPending || createGroupMutation.isPending || switchHeldMutation.isPending || paying}
            onClick={() => {
              // Checked client-side, BEFORE this multi-step mutation starts
              // creating a vehicle/address — retrying mid-flow after a
              // server-side 403 would risk re-creating a vehicle/address
              // that already succeeded on the first attempt.
              if (!user?.phone_verified) {
                setVerifyOpen(true);
                return;
              }
              // A held booking with nothing changed is finished, not
              // re-made: retry the payment, or confirm it as cash.
              if (held && heldIsCurrent) {
                if (paymentMethod === "online") void attemptPayment(held);
                else switchHeldMutation.mutate();
                return;
              }
              if (extraCars.length) createGroupMutation.mutate();
              else createMutation.mutate();
            }}
          >
            <CheckCircle2 className="h-4 w-4" />
            {held && heldIsCurrent
              ? paymentMethod === "online"
                ? `Retry payment ₹${held.total}`
                : "Confirm — pay cash on service"
              : editing
                ? `Save changes to ${editing.number}`
                : extraCars.length
                  ? `Confirm ${extraCars.length + 1} vehicles`
                  : "Confirm booking"}
          </Button>
        )}
      </div>
    </div>
  );

  return (
    <div className="space-y-5">

      <WizardShell
        eyebrow={activeSubscription ? "Book with your plan" : "Book a service"}
        title="Book your wash"
        steps={STEPS}
        current={step}
        onStepClick={(i) => setStep(i)}
        footer={wizardFooter}
      >
        {/* Paying with a plan is a BANNER on this page, never a second page
            or a pop-up over it — the customer stays in one flow from the
            first tap to the confirmation. */}
        {activeSubscription && (
          <div className="mb-5 flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border border-[#F3E5B5] bg-[#FFFCF0] px-3.5 py-3">
            <Gift className="h-4 w-4 shrink-0 text-[#E8A900]" />
            <span className="text-sm font-semibold text-black">
              {passServiceName || planNameOf(activeSubscription) || "Your plan"}
            </span>
            {passPlate && <span className="font-mono-num text-xs text-gray-600">{passPlate}</span>}
            <span className="font-mono-num text-xs text-gray-500">
              {activeSubscription.remaining_service_count} of {activeSubscription.total_service_count} washes left
            </span>
            <button
              type="button"
              onClick={() => setSubscriptionId(null)}
              className="ml-auto text-xs font-bold text-gray-500 underline underline-offset-2 hover:text-black"
            >
              Pay normally instead
            </button>
          </div>
        )}

        {/* Arrived from "Book again": say plainly that last time's choices
            were carried over, so the prefilled selection reads as
            deliberate rather than as something already half-committed. */}
        {editing && !held && (
          <div className="mb-5 flex flex-wrap items-center gap-x-2 gap-y-1.5 rounded-xl border border-[#F3E5B5] bg-[#FFFCF0] px-3.5 py-3">
            <Pencil className="h-4 w-4 shrink-0 text-[#E8A900]" />
            <span className="text-sm text-gray-700">
              Editing <span className="font-mono-num font-semibold text-black">{editing.number}</span> — change anything, then confirm.
              The original booking is replaced.
            </span>
          </div>
        )}

        {repeatBookingId && !editBookingId && repeatApplied && !!carNumber && (
          <div className="mb-5 flex flex-wrap items-center gap-x-2 gap-y-1.5 rounded-xl border border-[#F3E5B5] bg-[#FFFCF0] px-3.5 py-3">
            <RotateCcw className="h-4 w-4 shrink-0 text-[#E8A900]" />
            <span className="text-sm text-gray-700">
              Repeating{" "}
              <span className="font-mono-num font-semibold text-black">{repeatSource?.booking_number || "your last booking"}</span> — same{" "}
              {extraCars.length ? `${extraCars.length + 1} vehicles` : "vehicle"}, service and address. Just pick a new date and
              slot.
            </span>
          </div>
        )}

        {/* Arrived via "Book with my plan" but nothing is usable — say so
            once, here, and let them carry on with a normal booking. */}
        {planMode && !activeSubscription && !allUsableSubscriptions.length && (
          <div className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-[#F3E5B5] bg-[#FAFAFA] px-3.5 py-3 text-sm">
            <Gift className="h-4 w-4 shrink-0 text-[#E8A900]" />
            <span className="flex-1 text-gray-600">No usable plan right now — this will be a normal, paid booking.</span>
            <Button size="sm" variant="outline" onClick={() => navigate("/app/subscriptions")}>
              See plans
            </Button>
          </div>
        )}

        <WizardStepHeader
          title={STEPS[Math.min(step, STEPS.length - 1)]}
          description={STEP_HINTS[passMode && stepKey === "review" ? "passReview" : stepKey]}
        />

        {/* More than one usable plan and none chosen yet: ask once, right
            where the booking starts, instead of a floating card deck. */}
        {planMode && !activeSubscription && allUsableSubscriptions.length > 1 && stepKey === "vehicle" && (
          <div className="mb-6">
            <p className="mb-2 text-sm font-medium text-[var(--color-text-primary)]">Which plan do you want to use?</p>
            <div className="space-y-2">
              {allUsableSubscriptions.map((sub) => (
                <button
                  key={sub.id}
                  type="button"
                  onClick={() => setSubscriptionId(sub.id)}
                  className="flex w-full items-center justify-between gap-3 rounded-xl border border-[#F3E5B5] bg-white px-3.5 py-3 text-left transition-colors hover:border-[#E8A900]"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-semibold text-black">{planNameOf(sub) || "Subscription"}</span>
                    <span className="block text-xs text-gray-500">Valid until {format(sub.end_date)}</span>
                  </span>
                  <span className="font-mono-num shrink-0 text-xs font-bold text-black">{sub.remaining_service_count} left</span>
                </button>
              ))}
            </div>
          </div>
        )}
          {stepKey === "vehicle" && (
            <div className="space-y-6">
              {/* Cars already on this visit. They're settled — the editor
                  below is always configuring one MORE car. */}
              {!!extraCars.length && (
                <div className="rounded-xl border border-[#F3E5B5] bg-[#FFFCF0] p-3.5">
                  <p className="text-sm font-semibold text-black">
                    On this visit ({extraCars.length + (currentCarReady ? 1 : 0)} of {maxCars})
                  </p>
                  <div className="mt-2 space-y-1.5">
                    {extraCars.map((car) => (
                      <div key={car.vehicleId} className="flex items-center gap-2 text-sm">
                        <span className="min-w-0 flex-1 truncate text-gray-700">
                          <span className="font-medium text-black">{car.label}</span>
                          {car.serviceLabel ? ` · ${car.serviceLabel}` : ""}
                        </span>
                        <span className="font-mono-num shrink-0 text-gray-600">₹{car.subtotal}</span>
                        <button
                          type="button"
                          onClick={() => removeCar(car.vehicleId)}
                          aria-label={`Remove ${car.label} from this visit`}
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
                <p className="mb-1.5 flex items-center gap-1.5 text-sm font-medium text-[var(--color-text-primary)]">
                  <Car className="h-3.5 w-3.5" />
                  {extraCars.length ? `Next vehicle (${extraCars.length + 1})` : "Your vehicle"}
                </p>
                {!!vehicles?.length && (
                  <div className="mb-3">
                    {/* One clean dropdown — same control on mobile and desktop. */}
                    <Select
                      value={showNewVehicleForm ? "__new" : vehicles.find((v) => v.registration_number.toUpperCase() === carNumber.toUpperCase())?.id || ""}
                      onChange={(e) => {
                        if (e.target.value === "__new") {
                          setShowNewVehicleForm(true);
                          setCarNumber("");
                          setCarModel("");
                          return;
                        }
                        const v = vehicles.find((x) => x.id === e.target.value);
                        if (v) selectVehicle(v);
                      }}
                    >
                      <option value="">Select your vehicle…</option>
                      {vehicles.map((v) => (
                        <option key={v.id} value={v.id}>
                          {v.brand} {v.model} · {v.registration_number}
                        </option>
                      ))}
                      <option value="__new">+ Different vehicle</option>
                    </Select>
                  </div>
                )}

                {(showNewVehicleForm || !vehicles?.length) && (
                  <div className="mb-4 space-y-3 rounded-lg border border-dashed border-gray-200 p-3">
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <Input label="Car Number" placeholder="MP09XX1234" value={carNumber} onChange={(e) => setCarNumber(e.target.value)} required />
                      <Input label="Car Model (Optional)" placeholder="e.g. Hyundai i20" value={carModel} onChange={(e) => setCarModel(e.target.value)} />
                    </div>
                    <div>
                      <p className="mb-1.5 text-xs font-medium text-[var(--color-text-primary)]">Vehicle type</p>
                      <div className="flex flex-wrap gap-2">
                        {(vehicleTypes || []).map((t) => (
                          <button
                            key={t.id}
                            type="button"
                            onClick={() => {
                              if (t.id !== vehicleType) clearServices();
                              setVehicleType(t.id);
                              resetCoupon();
                            }}
                            className={`rounded-full px-3.5 py-1.5 text-xs font-medium ${
                              vehicleType === t.id ? "bg-[var(--color-primary)] text-white" : "bg-gray-100 text-gray-600"
                            }`}
                          >
                            {t.name}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                )}

              </div>

              <div className="border-t border-gray-100 pt-5">
                <p className="mb-3 text-sm font-medium text-[var(--color-text-primary)]">What do you need done?</p>
                {!!combos?.length && (
                  <>
                    <p className="mb-2 text-sm font-medium text-black">Combo offers</p>
                    <div className="mb-4 flex flex-wrap gap-2">
                      {combos.map((c) => {
                        const { price, firstTime } = priceFor(c, vehicleType);
                        const shown = isFirstOrderGuess && firstTime != null ? firstTime : price;
                        return (
                          <button
                            key={c.id}
                            onClick={() => selectCombo(c.id)}
                            className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors ${
                              comboId === c.id ? "border-[var(--color-secondary)] bg-[var(--color-secondary)] text-white" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
                            }`}
                          >
                            {c.name} · ₹{shown}
                          </button>
                        );
                      })}
                    </div>
                  </>
                )}

                <p className="mb-2 text-sm font-medium text-black">Main service</p>
                {servicesLoading ? (
                  <p className="text-sm text-[var(--color-text-secondary)]">Loading services…</p>
                ) : !vehicleType ? (
                  <p className="text-sm text-[var(--color-text-secondary)]">Choose your vehicle above to see what's available for it.</p>
                ) : (
                  <>
                    <div className="flex flex-wrap gap-2">
                      {groups.map((g) => {
                        const selected = selectedGroup?.key === g.key;
                        const { price, firstTime } = priceFor(g.primary, vehicleType);
                        const shown = isFirstOrderGuess && firstTime != null ? firstTime : price;
                        return (
                          <button
                            key={g.key}
                            disabled={!!comboId}
                            onClick={() => pickGroup(g)}
                            className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors disabled:opacity-40 ${
                              selected ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
                            }`}
                          >
                            {g.label} · {g.variants.length > 1 ? "from " : ""}₹{shown}
                          </button>
                        );
                      })}
                    </div>

                    {/* Bike bookings: one −/+ counter for the number of bikes.
                        Price = base wash + extra-bike line per additional
                        bike (₹99 + ₹60 each), no separate variant chips. */}
                    {selectedBase && bookingIsBike && (
                      <div className="mt-3 flex items-center justify-between rounded-xl border border-[#F3E5B5] bg-[#FAFAFA] p-3">
                        <div>
                          <p className="text-xs font-medium text-[var(--color-text-primary)]">How many bikes?</p>
                          <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
                            {kit.addBike
                              ? `First bike ₹${priceFor(selectedBase, vehicleType).price}, ₹${priceFor(kit.addBike, vehicleType).price} each additional`
                              : `₹${priceFor(selectedBase, vehicleType).price} per bike`}
                          </p>
                        </div>
                        <QtyStepper
                          value={bikesInBooking}
                          min={1}
                          max={10}
                          onChange={(n) => setExtraBikes(Math.max(0, n - bikeCount))}
                        />
                      </div>
                    )}

                    {addonsBlock}
                  </>
                )}
              </div>

              {/* One customer, several of their cars, one trip. Adding a car
                  banks the current selection and clears the editor for the
                  next one. */}
              {canAddMoreCars && (
                <div className="border-t border-gray-100 pt-5">
                  <Button
                    type="button"
                    variant="outline"
                    className="w-full"
                    disabled={!currentCarReady}
                    onClick={addCurrentCar}
                  >
                    + Add another car to this visit
                  </Button>
                  <p className="mt-1.5 text-center text-xs text-gray-500">
                    {currentCarReady
                      ? `Up to ${maxCars} cars washed on one visit, at one address.`
                      : "Pick this car's service first."}
                  </p>
                  {addCarError && <p className="mt-1.5 text-center text-xs text-[var(--color-error)]">{addCarError}</p>}
                </div>
              )}
            </div>
          )}

          {stepKey === "address" && (
            <div className="space-y-5">
              {/* Contact — one compact line from the profile, not two form fields */}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-[#F3E5B5] bg-[#FAFAFA] px-3.5 py-2.5 text-sm">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-100 text-xs font-bold text-black">
                  {(user?.full_name || "?").trim().charAt(0).toUpperCase()}
                </span>
                <span className="font-semibold text-black">{user?.full_name}</span>
                <span className="font-mono-num text-gray-400">{user?.phone}</span>
                <span className="ml-auto text-[11px] text-gray-400">From your profile</span>
              </div>

              {/* Saved addresses — one dropdown, same control as the vehicle */}
              {!!addresses?.length && (
                <Select
                  label="Where should we come?"
                  value={showNewAddressForm ? "__new" : selectedAddressId || ""}
                  onChange={(e) => {
                    if (e.target.value === "__new") {
                      startNewAddress();
                      return;
                    }
                    const a = addresses.find((x) => x.id === e.target.value);
                    if (a) selectAddress(a);
                  }}
                >
                  <option value="">Choose a saved address…</option>
                  {addresses.map((a) => (
                    <option key={a.id} value={a.id}>
                      {(a.label ? `${a.label} — ` : "") + a.line1}
                    </option>
                  ))}
                  <option value="__new">+ New address</option>
                </Select>
              )}

              {/* A SAVED address was pinned when it was saved — asking for
                  the pin again on every booking is the thing customers
                  complained about. Show what we have and move on; the map
                  is for a NEW address (or a first-time customer) only. */}
              {savedAddress ? (
                <div className="flex items-start gap-2.5 rounded-xl border border-[#F3E5B5] bg-[#FAFAFA] p-3.5">
                  <MapPin className="mt-0.5 h-4 w-4 shrink-0 text-black" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-black">
                      {savedAddress.label ? `${savedAddress.label} — ` : ""}
                      {savedAddress.line1}
                    </p>
                    <p className="mt-0.5 truncate text-xs text-gray-500">
                      {[savedAddress.landmark, savedAddress.city, savedAddress.pincode].filter(Boolean).join(" · ")}
                    </p>
                    <button
                      type="button"
                      className="mt-1.5 text-xs font-bold text-black underline underline-offset-2"
                      onClick={startNewAddress}
                    >
                      Use a different address
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <LocationPicker
                    value={location}
                    onUnavailable={() => setMapsDown(true)}
                    onChange={(v) => {
                      setLocation(v);
                      setSelectedAddressId(null);
                      setAddressLat(v.latitude);
                      setAddressLng(v.longitude);
                      // Captured silently — never asked again.
                      setCity(v.city || "Indore");
                      setState(v.state || "Madhya Pradesh");
                      if (v.pincode) setPincode(v.pincode);
                    }}
                  />
                  {/* No house/flat text box (founder call): the pin IS the
                      address, Rapido-style — the captain navigates to those
                      coordinates. It returns only in the no-maps fallback. */}
                  {!mapsDown && location && (
                    <p className="flex items-start gap-1.5 rounded-xl border border-[#F3E5B5] bg-[#FAFAFA] p-3 text-xs text-[var(--color-text-secondary)]">
                      <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-black" />
                      <span className="min-w-0">
                        <span className="block font-medium text-black">{location.formatted || [location.area, location.city].filter(Boolean).join(", ")}</span>
                        Drag the pin if this isn't your exact gate.
                      </span>
                    </p>
                  )}
                </>
              )}
              <Input label="Landmark (optional)" placeholder="Near Apollo Hospital" value={landmark} onChange={(e) => setLandmark(e.target.value)} />

              {mapsDown && !savedAddress && (
                <>
                  {/* Manual fallback ONLY when Google Maps can't load. */}
                  <Input
                    label="Address"
                    placeholder="House / flat, street, area"
                    value={addressLine}
                    onChange={(e) => { setAddressLine(e.target.value); setSelectedAddressId(null); }}
                    hint="Maps are unavailable right now — type the address instead."
                    required
                  />
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                    <Input label="City" value={city} onChange={(e) => setCity(e.target.value)} required />
                    <Input label="State" value={state} onChange={(e) => setState(e.target.value)} required />
                    <Input label="Pincode" value={pincode} onChange={(e) => setPincode(e.target.value)} required />
                  </div>
                </>
              )}

              <div className="border-t border-gray-100 pt-5">
                <p className="mb-3 text-sm font-medium text-[var(--color-text-primary)]">When should we come?</p>
                {!savedAddress && !mapsDown && addressLat == null ? (
                  <p className="rounded-xl border border-[#F3E5B5] bg-[#FAFAFA] px-3 py-2.5 text-sm text-[var(--color-text-secondary)]">
                    📍 Set your location on the map above (Use my location, search, or tap) — slots appear once we know where to come.
                  </p>
                ) : pinRequired ? (
                  <p className="rounded-xl bg-amber-50 px-3 py-2.5 text-sm text-amber-800">
                    Select your <span className="font-semibold">Area / locality</span> from the suggestions above (or pin on the map) to see
                    available slots — typed addresses can't be checked against our service area.
                  </p>
                ) : pincode.length >= 6 && !serviceCenterId ? (
                  <p className="text-sm text-[var(--color-error)]">Doorstep service isn't available in this area yet.</p>
                ) : (
                  <SlotPicker
                    serviceCenterId={serviceCenterId}
                    date={date}
                    onDateChange={setDate}
                    value={slot}
                    onChange={setSlot}
                    enableHold
                  />
                )}
              </div>

              <Input label="Notes (Optional)" placeholder="Any special instructions?" value={notes} onChange={(e) => setNotes(e.target.value)} />

              <div>
                <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Secondary contact (Optional)</p>
                <p className="mb-2 text-xs text-[var(--color-text-secondary)]">Someone else who'll be at the address, e.g. a driver or family member.</p>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <Input placeholder="Their name" value={altContactName} onChange={(e) => setAltContactName(e.target.value)} />
                  <Input placeholder="Their phone number" value={altContactPhone} onChange={(e) => setAltContactPhone(e.target.value)} />
                </div>
              </div>
            </div>
          )}

          {stepKey === "review" && (
            <div className="space-y-5">
              {/* Pass bookings pick nothing but extras — the wash itself is
                  already decided by the pass. */}
              {passMode && (
                <div className="border-b border-gray-100 pb-5">
                  <p className="text-sm font-medium text-[var(--color-text-primary)]">Want anything extra?</p>
                  <p className="mt-0.5 text-xs text-gray-500">Optional — added to this visit and paid online with it.</p>
                  {addonsBlock}
                </div>
              )}
              <div className="space-y-2 rounded-xl border border-[#F3E5B5] bg-[#FAFAFA] p-4">
                {/* Cars already on the visit, each named with what it's
                    having done. Without these the customer sees one service
                    and a total that doesn't match it. */}
                {extraCars.map((car) => (
                  <div key={car.vehicleId}>
                    <p className="text-xs font-semibold text-black">{car.label}</p>
                    <div className="flex justify-between text-sm">
                      <span className="text-[var(--color-text-secondary)]">{car.serviceLabel || "Service"}</span>
                      <span className="font-mono-num text-[var(--color-text-primary)]">₹{car.subtotal}</span>
                    </div>
                  </div>
                ))}

                {/* ...and the car still in the editor. On a single-car
                    booking this is the whole list, unchanged. */}
                {!!extraCars.length && (
                  <p className="text-xs font-semibold text-black">
                    {carModel || "Vehicle"}
                    {carNumber ? ` · ${carNumber.toUpperCase()}` : ""}
                  </p>
                )}
                {selectedCombo ? (
                  <div className="flex justify-between text-sm">
                    <span className="text-[var(--color-text-secondary)]">{selectedCombo.name} (combo)</span>
                    <span className="font-mono-num text-[var(--color-text-primary)]">₹{subtotal}</span>
                  </div>
                ) : (
                  selectedServices.map((s) => {
                    const { price, firstTime } = priceFor(s, vehicleType);
                    const shown = (isFirstOrderGuess && firstTime != null ? firstTime : price) * qtyOf(s.id);
                    return (
                      <div key={s.id} className="flex justify-between text-sm">
                        <span className="text-[var(--color-text-secondary)]">
                          {s.name}
                          {qtyOf(s.id) > 1 ? ` ×${qtyOf(s.id)}` : ""}
                        </span>
                        <span className="font-mono-num text-[var(--color-text-primary)]">₹{shown}</span>
                      </div>
                    );
                  })
                )}
                <div className="flex justify-between border-t border-gray-200 pt-2 text-sm">
                  <span className="text-[var(--color-text-secondary)]">
                    Subtotal{extraCars.length ? ` · ${extraCars.length + 1} vehicles` : ""}
                  </span>
                  <span className="font-mono-num">₹{subtotal + extrasSubtotal}</span>
                </div>
                {subscriptionId ? (
                  <>
                    <div className="flex justify-between text-sm text-[var(--color-success)]">
                      <span>Covered by your plan</span>
                      <span className="font-mono-num">-₹{Math.max(subtotal - planTopUp, 0)}</span>
                    </div>
                    {planTopUp > 0 && (
                      <p className="text-xs text-[var(--color-text-secondary)]">
                        Add-ons and service upgrades aren't covered by a plan — they're payable on the booking.
                      </p>
                    )}
                  </>
                ) : (
                  couponDiscount > 0 && (
                    <div className="flex justify-between text-sm text-[var(--color-success)]">
                      <span>Coupon discount</span>
                      <span className="font-mono-num">-₹{couponDiscount}</span>
                    </div>
                  )
                )}
                <div
                  className="flex justify-between border-t border-gray-200 pt-2 text-base font-bold text-[var(--color-text-primary)]"
                  title={
                    isFirstOrderGuess
                      ? "First-time pricing is confirmed only if this vehicle and phone have no earlier booking."
                      : undefined
                  }
                >
                  <span>Total</span>
                  <span className="font-mono-num">₹{total + extrasSubtotal}</span>
                </div>
              </div>

              {!subscriptionId && (
                <>
                  <p className="text-sm font-medium text-[var(--color-text-primary)]">Have a coupon instead? (optional)</p>
                  <div className="flex gap-2">
                    <Input placeholder="Coupon code" value={couponCode} onChange={(e) => setCouponCode(e.target.value.toUpperCase())} />
                    <Button type="button" variant="outline" onClick={applyCoupon}>
                      Apply
                    </Button>
                  </div>
                  {couponError && <p className="text-xs text-[var(--color-error)]">{couponError}</p>}
                  {couponDiscount > 0 && <Badge tone="success">Coupon applied</Badge>}

                  {/* Founder rule: service bookings take BOTH — cash at the
                      door, or Razorpay right now. (Plan bookings skip this:
                      the plan IS the payment.) */}
                  <p className="text-sm font-medium text-[var(--color-text-primary)]">How would you like to pay?</p>
                  <div className="flex gap-2">
                    {[
                      { value: "cash", label: "Cash on service", hint: "Pay the captain at your door" },
                      { value: "online", label: "Pay online", hint: "UPI, cards, netbanking" },
                    ].map((m) => (
                      <button
                        key={m.value}
                        type="button"
                        onClick={() => setPaymentMethod(m.value)}
                        className={`flex-1 rounded-xl border px-3.5 py-2.5 text-left text-sm transition-colors ${
                          paymentMethod === m.value ? "border-2 border-black bg-[#FFF4CD] font-semibold text-black" : "border-[#F3E5B5] bg-white text-gray-600 hover:border-[#E8A900]/50"
                        }`}
                      >
                        {m.label}
                        <span className="mt-0.5 block text-[11px] font-normal text-gray-400">{m.hint}</span>
                      </button>
                    ))}
                  </div>
                </>
              )}

              {/* The last moment this is still actionable — a captain who
                  arrives to no water is a wasted trip for both sides. */}
              <ServicePrepNotice services={visitServices} />
            </div>
          )}

          <PhoneVerificationModal open={verifyOpen} onClose={() => setVerifyOpen(false)} onVerified={() => { setVerifyOpen(false); if (extraCars.length) createGroupMutation.mutate(); else createMutation.mutate(); }} />
      </WizardShell>
    </div>
  );
}
