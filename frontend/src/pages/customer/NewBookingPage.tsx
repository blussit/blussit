import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Calendar, Car, Check, CheckCircle2, ChevronLeft, Gift, MapPin } from "lucide-react";
import { vehicleApi, addressApi } from "../../api/profile";
import { catalogApi, comboOfferApi, vehicleTypeApi, getSlotHolderKey, coverageApi } from "../../api/catalog";
import { bookingApi } from "../../api/booking";
import { couponApi, subscriptionApi } from "../../api/engagement";
import { useAuth } from "../../context/AuthContext";
import { PaymentCancelled, payWithRazorpay } from "../../lib/razorpay";
import { Badge, Button, Card, Input, Select } from "../../components/ui";
import { LocationPicker, type LocationValue } from "../../components/shared/LocationPicker";
import { SubscriptionPicker } from "../../components/shared/SubscriptionPicker";
import { SubscriptionQuickBook } from "../../components/shared/SubscriptionQuickBook";
import { PhoneVerificationModal } from "../../components/shared/PhoneVerificationModal";
import { SlotPicker } from "../../components/shared/SlotPicker";
import { getErrorMessage } from "../../lib/api-client";
import { addonKit, baseGroups, bikeTypeIds, variantCount, type BaseGroup } from "../../lib/serviceMix";
import { estimatePlanTopUp, subscriptionCoversType } from "../../lib/planTier";
import { QtyStepper } from "../../components/shared/QtyStepper";
import type { Address, ComboOffer, Service, VehicleType } from "../../types";

const STEPS = ["Vehicle & Service", "Address & Time", "Review & Pay"];
const STEP_HINTS = [
  "Which vehicle and what service.",
  "Where and when should we come?",
  "Check the price and choose how to pay — including any subscription plan you own.",
];

function priceFor(item: Service | ComboOffer, vehicleType: VehicleType): { price: number; firstTime: number | null } {
  const price = item.vehicle_type_prices?.[vehicleType] ?? item.price;
  const firstTime = item.vehicle_type_discounted_prices?.[vehicleType] ?? item.discounted_price ?? null;
  return { price, firstTime };
}

export default function NewBookingPage() {
  const navigate = useNavigate();
  // "?mode=plan" (the + button's "Book with my plan"): jump straight into
  // the subscription quick-book flow.
  const [searchParams] = useSearchParams();
  const planMode = searchParams.get("mode") === "plan";
  const queryClient = useQueryClient();
  const { user } = useAuth();
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

  const [couponCode, setCouponCode] = useState("");
  const [couponDiscount, setCouponDiscount] = useState(0);
  const [couponError, setCouponError] = useState("");
  const [paymentMethod, setPaymentMethod] = useState("cash");
  const [subscriptionId, setSubscriptionId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState("");

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

  // Subscription eligibility is by vehicle TYPE: the plan must cover the
  // selected type AND the purchased tier must allow it (bought for one
  // type = usable on that type or a cheaper one, never bigger) — same
  // rules the backend enforces at plan_consumption time.
  const eligibleSubscriptions = (mySubscriptions || []).filter((s) => {
    if (s.effective_status !== "active" || s.remaining_service_count <= 0) return false;
    const plan = subscriptionPlans?.find((p) => p.id === s.plan_id);
    return subscriptionCoversType(s, plan, vehicleType);
  });
  // Every currently-usable subscription, independent of whatever vehicle
  // type is selected in the wizard below — the quick-book shortcut lets the
  // customer pick which of their matching-type vehicles to use, so it
  // isn't gated on the wizard's current selection the way the picker
  // above is.
  const allUsableSubscriptions = (mySubscriptions || []).filter((s) => s.effective_status === "active" && s.remaining_service_count > 0);

  useEffect(() => {
    const defaultVehicle = vehicles?.find((v) => v.is_default) || vehicles?.[0];
    if (defaultVehicle && !carNumber) {
      setVehicleType(defaultVehicle.vehicle_type);
      setCarNumber(defaultVehicle.registration_number);
      setCarModel(`${defaultVehicle.brand} ${defaultVehicle.model}`.trim());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicles]);

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
  }, [addresses]);

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
  };

  const services = servicesData?.data || [];
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

  const totalDuration = useMemo(() => {
    if (selectedCombo) {
      const includedServices = services.filter((s) => selectedCombo.service_ids.includes(s.id));
      return includedServices.reduce((sum, s) => sum + s.duration_minutes, 0) || 60;
    }
    return selectedServices.reduce((sum, s) => sum + s.duration_minutes * (serviceQty[s.id] || 1), 0) || 60;
  }, [selectedServices, selectedCombo, services, serviceQty]);

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

  const createMutation = useMutation({
    mutationFn: async () => {
      let vehicleId = vehicles?.find((v) => v.registration_number.toUpperCase() === carNumber.toUpperCase())?.id;
      if (!vehicleId) {
        const [brand, ...modelParts] = carModel.trim().split(" ");
        const vehicle = await vehicleApi.create({
          vehicle_type: vehicleType,
          brand: brand || "Vehicle",
          model: modelParts.join(" ") || carModel || "—",
          registration_number: carNumber.toUpperCase(),
          is_default: !vehicles?.length,
        });
        vehicleId = vehicle.id;
      }

      let addressId = !showNewAddressForm && selectedAddressId ? selectedAddressId : addresses?.find((a) => a.line1 === addressLine && a.pincode === pincode)?.id;
      if (!addressId) {
        const address = await addressApi.create({
          label: "Doorstep",
          line1: location ? [addressLine, location.area, location.city].filter(Boolean).join(", ") : addressLine,
          landmark: landmark || undefined,
          city,
          state,
          pincode,
          latitude: addressLat ?? undefined,
          longitude: addressLng ?? undefined,
          is_default: !addresses?.length,
        });
        addressId = address.id;
      }

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
      if (!subscriptionId && paymentMethod === "online" && booking.total_amount > 0) {
        try {
          await payWithRazorpay(
            { purpose: "booking", booking_id: booking.id },
            { name: user?.full_name, email: user?.email, contact: user?.phone }
          );
        } catch (err) {
          if (err instanceof PaymentCancelled) {
            navigate(`/app/bookings/${booking.id}`);
            return;
          }
          setSubmitError(getErrorMessage(err));
          navigate(`/app/bookings/${booking.id}`);
          return;
        }
      }
      // A fixed, opaque, single-purpose token (not the raw booking id) so
      // the confirmation page can't be reached by guessing or bookmarking
      // a URL — see PurchaseConfirmationModel / ThankYouPage.
      navigate(`/thank-you?token=${booking.confirmation_token}`);
    },
    onError: (err) => setSubmitError(getErrorMessage(err)),
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

  // Why the subscription section might look empty even though the customer
  // owns one — shown instead of just silently rendering nothing, which is
  // what made subscription-booking look like it didn't exist at all.
  const subscriptionHint = (() => {
    if (!mySubscriptions?.length || eligibleSubscriptions.length) return null;
    const usable = mySubscriptions.filter((s) => s.effective_status === "active" && s.remaining_service_count > 0);
    if (!usable.length) return "Your subscription is expired or has no washes left.";
    return "Your subscription doesn't cover this vehicle's type — switch to a matching vehicle above to pay with it.";
  })();

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
  const stepValid = [
    !!carNumber && hasSelection,
    !!addressLine && !!city && !!state && !!pincode && !!date && !!slot,
    true,
  ][step];

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => (step > 0 ? setStep((s) => s - 1) : navigate(-1))}
          aria-label="Back"
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-[#F3E5B5] bg-white text-black transition-colors hover:border-[#E8A900]/50"
        >
          <ChevronLeft className="h-5 w-5" />
        </button>
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-black">
            Step {step + 1} of {STEPS.length} · {STEPS[step]}
          </p>
          <h1 className="mt-0.5 font-display text-2xl font-bold uppercase text-[var(--color-primary)]">Book Your Wash</h1>
          <p className="text-sm text-[var(--color-text-secondary)]">{STEP_HINTS[step]}</p>
        </div>
      </div>

      {planMode && !allUsableSubscriptions.length && (
        <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-[#F3E5B5] bg-[#FAFAFA] p-4 text-sm">
          <Gift className="h-5 w-5 shrink-0 text-[#E8A900]" />
          <span className="flex-1 text-gray-600">No usable plan right now — subscribe once and save on every wash, or continue with a normal booking below.</span>
          <Button size="sm" variant="outline" onClick={() => navigate("/app/subscriptions")}>
            See plans
          </Button>
        </div>
      )}

      {/* The "book from a subscription" shortcut belongs ONLY to the plan
          flow (?mode=plan, the + menu's "Book with my plan"). A customer
          who explicitly chose a normal wash must not have plan cards
          pushed into their wizard (user call) — if they own a plan that
          covers this booking, the step-3 "Have a subscription plan?"
          payment picker still offers it at the right moment. */}
      {planMode && (
        <SubscriptionQuickBook
          subscriptions={allUsableSubscriptions}
          plans={subscriptionPlans}
          vehicles={vehicles}
          addresses={addresses}
          services={services}
          autoOpen
        />
      )}

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[220px_1fr_320px]">
        <div className="flex gap-3 lg:flex-col lg:gap-6">
          {STEPS.map((label, i) => (
            <div key={label} className="flex items-center gap-3 lg:items-start">
              <span
                className={`font-mono-num flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-bold ${
                  i < step ? "bg-[var(--color-success)] text-white" : i === step ? "bg-[var(--color-secondary)] text-white" : "bg-gray-100 text-gray-400"
                }`}
              >
                {i < step ? <Check className="h-4 w-4" /> : i + 1}
              </span>
              <span className={`hidden text-sm font-medium lg:block ${i === step ? "text-[var(--color-text-primary)]" : "text-gray-400"}`}>{label}</span>
            </div>
          ))}
        </div>

        <Card className="p-6">
          {step === 0 && (
            <div className="space-y-6">
              <div>
                <p className="mb-1.5 flex items-center gap-1.5 text-sm font-medium text-[var(--color-text-primary)]">
                  <Car className="h-3.5 w-3.5" /> Your vehicle
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
                    <p className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">Combo offers</p>
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

                <p className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">Main service</p>
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

                    {/* Add-ons — only the ones valid for what's selected */}
                    {selectedBase && (kit.simple.length > 0 || kit.addBike || kit.bikePolish) && (
                      <div className="mt-4">
                        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">Add-ons</p>
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
                )}
              </div>
            </div>
          )}

          {step === 1 && (
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
                      setShowNewAddressForm(true);
                      setSelectedAddressId(null);
                      setAddressLine("");
                      setLandmark("");
                      setCity("");
                      setState("");
                      setPincode("");
                      setAddressLat(null);
                      setAddressLng(null);
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
              <Input
                label="House / flat, gali no."
                placeholder="e.g. 75, Gali No. 2"
                value={addressLine}
                onChange={(e) => { setAddressLine(e.target.value); setSelectedAddressId(null); }}
                required
              />
              <Input label="Landmark (optional)" placeholder="Near Apollo Hospital" value={landmark} onChange={(e) => { setLandmark(e.target.value); setSelectedAddressId(null); }} />

              {mapsDown && (
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                  {/* Manual fallback ONLY when Google Maps can't load. */}
                  <Input label="City" value={city} onChange={(e) => setCity(e.target.value)} required />
                  <Input label="State" value={state} onChange={(e) => setState(e.target.value)} required />
                  <Input label="Pincode" value={pincode} onChange={(e) => setPincode(e.target.value)} required />
                </div>
              )}

              <div className="border-t border-gray-100 pt-5">
                <p className="mb-3 text-sm font-medium text-[var(--color-text-primary)]">When should we come?</p>
                {!mapsDown && addressLat == null ? (
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

          {step === 2 && (
            <div className="space-y-5">
              {(!!eligibleSubscriptions.length || !!subscriptionHint) && (
                <div className="rounded-xl border-2 border-dashed border-[var(--color-success)]/40 bg-[var(--color-success)]/5 p-4">
                  <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-[var(--color-text-primary)]">
                    <Gift className="h-4 w-4 text-[var(--color-success)]" /> Have a subscription plan?
                  </p>
                  {eligibleSubscriptions.length ? (
                    <SubscriptionPicker
                      subscriptions={eligibleSubscriptions}
                      plans={subscriptionPlans}
                      selectedId={subscriptionId}
                      onSelect={(id) => {
                        setSubscriptionId(id);
                        resetCoupon();
                      }}
                    />
                  ) : (
                    <p className="text-xs text-[var(--color-text-secondary)]">{subscriptionHint}</p>
                  )}
                </div>
              )}

              <div className="space-y-2 rounded-xl border border-[#F3E5B5] bg-[#FAFAFA] p-4">
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
                  <span className="text-[var(--color-text-secondary)]">Subtotal</span>
                  <span className="font-mono-num">₹{subtotal}</span>
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
                <div className="flex justify-between border-t border-gray-200 pt-2 text-base font-bold text-[var(--color-text-primary)]">
                  <span>Total</span>
                  <span className="font-mono-num">₹{total}</span>
                </div>
                <p className="pt-1 text-xs text-[var(--color-text-secondary)]">
                  First-time pricing above is an estimate — it's only confirmed if this vehicle and phone number genuinely have no prior booking on the platform.
                </p>
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
              {submitError && <p className="text-sm text-[var(--color-error)]">{submitError}</p>}
            </div>
          )}

          <div className="mt-8 flex justify-between gap-3">
            {step > 0 ? (
              <Button variant="outline" onClick={() => setStep((s) => s - 1)}>
                Back
              </Button>
            ) : (
              <span />
            )}
            {step < STEPS.length - 1 ? (
              <Button disabled={!stepValid} onClick={() => setStep((s) => s + 1)}>
                Continue
              </Button>
            ) : (
              <Button
                isLoading={createMutation.isPending}
                onClick={() => {
                  // Checked client-side, BEFORE this multi-step mutation
                  // starts creating a vehicle/address — retrying mid-flow
                  // after a server-side 403 would risk re-creating a
                  // vehicle/address that already succeeded on the first
                  // attempt. Pre-empting here means the OTP gate never
                  // interrupts a partially-completed submission.
                  if (!user?.phone_verified) {
                    setVerifyOpen(true);
                    return;
                  }
                  createMutation.mutate();
                }}
              >
                <CheckCircle2 className="h-4 w-4" /> Confirm Booking
              </Button>
            )}
          </div>

          <PhoneVerificationModal open={verifyOpen} onClose={() => setVerifyOpen(false)} onVerified={() => { setVerifyOpen(false); createMutation.mutate(); }} />
        </Card>

        <Card className="h-fit p-6 lg:sticky lg:top-24">
          <p className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--color-text-primary)]">Your Booking</p>
          <div className="mb-4 flex h-28 items-center justify-center rounded-xl bg-[var(--color-primary-light)]">
            <MapPin className="h-8 w-8 text-[var(--color-primary)]" />
          </div>
          <div className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-[var(--color-text-secondary)]">Service</span>
              <span className="text-right font-medium text-[var(--color-text-primary)]">
                {selectedCombo
                  ? selectedCombo.name
                  : selectedServices.length
                    ? selectedServices.map((s) => s.name + (qtyOf(s.id) > 1 ? ` ×${qtyOf(s.id)}` : "")).join(", ")
                    : "—"}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--color-text-secondary)]">Date</span>
              <span className="flex items-center gap-1 font-medium text-[var(--color-text-primary)]">
                <Calendar className="h-3.5 w-3.5" /> {date || "—"}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--color-text-secondary)]">Slot</span>
              <span className="font-mono-num font-medium text-[var(--color-text-primary)]">{slot || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--color-text-secondary)]">Duration</span>
              <span className="font-medium text-[var(--color-text-primary)]">{totalDuration} min</span>
            </div>
            <div className="flex justify-between border-t border-gray-100 pt-3 text-base">
              <span className="font-semibold text-[var(--color-text-primary)]">Price</span>
              <span className="font-mono-num font-bold text-[var(--color-secondary)]">₹{total || 0}</span>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
