import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Calendar, Car, Check, CheckCircle2, Clock, Gift, MapPin } from "lucide-react";
import { vehicleApi, addressApi } from "../../api/profile";
import { catalogApi, comboOfferApi, bookingPolicyApi, vehicleTypeApi } from "../../api/catalog";
import { bookingApi } from "../../api/booking";
import { couponApi, subscriptionApi } from "../../api/engagement";
import { useAuth } from "../../context/AuthContext";
import { Badge, Button, Card, Input } from "../../components/ui";
import { MapPicker, type ResolvedAddress } from "../../components/shared/MapPicker";
import { SubscriptionPicker } from "../../components/shared/SubscriptionPicker";
import { AddressPicker } from "../../components/shared/AddressPicker";
import { SubscriptionQuickBook } from "../../components/shared/SubscriptionQuickBook";
import { getErrorMessage } from "../../lib/api-client";
import { todayIST } from "../../lib/date";
import type { Address, ComboOffer, Service, VehicleType } from "../../types";

const STEPS = ["Vehicle & Service", "Address", "Review & Pay"];
const STEP_HINTS = [
  "Which vehicle, what service, and when.",
  "Where should we come?",
  "Check the price and choose how to pay — including any subscription plan you own.",
];

function priceFor(item: Service | ComboOffer, vehicleType: VehicleType): { price: number; firstTime: number | null } {
  const price = item.vehicle_type_prices?.[vehicleType] ?? item.price;
  const firstTime = item.vehicle_type_discounted_prices?.[vehicleType] ?? item.discounted_price ?? null;
  return { price, firstTime };
}

export default function NewBookingPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const [step, setStep] = useState(0);

  const [serviceIds, setServiceIds] = useState<string[]>([]);
  const [comboId, setComboId] = useState<string | null>(null);
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [timeError, setTimeError] = useState("");

  const [vehicleType, setVehicleType] = useState<VehicleType>("");
  const [carNumber, setCarNumber] = useState("");
  const [carModel, setCarModel] = useState("");
  const [addressLine, setAddressLine] = useState("");
  const [landmark, setLandmark] = useState("");
  const [addressLat, setAddressLat] = useState<number | null>(null);
  const [addressLng, setAddressLng] = useState<number | null>(null);
  const [city, setCity] = useState("");
  const [state, setState] = useState("");
  const [pincode, setPincode] = useState("");
  const [detectedAddress, setDetectedAddress] = useState<ResolvedAddress | null>(null);
  const [notes, setNotes] = useState("");
  const [selectedAddressId, setSelectedAddressId] = useState<string | null>(null);
  const [showNewAddressForm, setShowNewAddressForm] = useState(false);
  const [altContactName, setAltContactName] = useState("");
  const [altContactPhone, setAltContactPhone] = useState("");

  const [showNewVehicleForm, setShowNewVehicleForm] = useState(false);

  const [couponCode, setCouponCode] = useState("");
  const [couponDiscount, setCouponDiscount] = useState(0);
  const [couponError, setCouponError] = useState("");
  const [subscriptionId, setSubscriptionId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState("");

  const { data: policy } = useQuery({ queryKey: ["booking-policy"], queryFn: bookingPolicyApi.get });
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

  // The vehicle this booking will actually use — matches an existing vehicle
  // by plate if one exists, otherwise this is a not-yet-created vehicle (the
  // create-mutation below creates it at submit time). Subscription
  // eligibility (below) is locked to one specific vehicle, so it needs this
  // resolved id, not just "any active subscription with services left".
  const resolvedVehicleId = vehicles?.find((v) => v.registration_number.toUpperCase() === carNumber.toUpperCase())?.id;
  const eligibleSubscriptions = (mySubscriptions || []).filter(
    (s) => s.effective_status === "active" && s.remaining_service_count > 0 && !!resolvedVehicleId && s.vehicle_id === resolvedVehicleId
  );
  // Every currently-usable subscription, independent of whatever vehicle is
  // selected in the wizard below — the quick-book shortcut picks its own
  // vehicle (whichever one the subscription is locked to), so it isn't
  // gated on `resolvedVehicleId` the way the in-wizard picker above is.
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

  // Default the type selector to the first admin-configured type once
  // loaded, unless a default vehicle already set one above.
  useEffect(() => {
    if (vehicleTypes?.length && !vehicleType) setVehicleType(vehicleTypes[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicleTypes]);

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

  const subtotal = useMemo(() => {
    if (selectedCombo) {
      const { price, firstTime } = priceFor(selectedCombo, vehicleType);
      return isFirstOrderGuess && firstTime != null ? firstTime : price;
    }
    return selectedServices.reduce((sum, s) => {
      const { price, firstTime } = priceFor(s, vehicleType);
      return sum + (isFirstOrderGuess && firstTime != null ? firstTime : price);
    }, 0);
  }, [selectedServices, selectedCombo, vehicleType, isFirstOrderGuess]);

  const totalDuration = useMemo(() => {
    if (selectedCombo) {
      const includedServices = services.filter((s) => selectedCombo.service_ids.includes(s.id));
      return includedServices.reduce((sum, s) => sum + s.duration_minutes, 0) || 60;
    }
    return selectedServices.reduce((sum, s) => sum + s.duration_minutes, 0) || 60;
  }, [selectedServices, selectedCombo, services]);

  // A subscription (when picked) covers the service outright — mirrors the
  // backend's create_booking, which sets discount_amount = subtotal
  // whenever subscription_id is present, ignoring any coupon.
  const total = subscriptionId ? 0 : Math.max(subtotal - couponDiscount, 0);

  // Client-side mirror of the server's scheduling rules — catches obvious mistakes
  // immediately, but the backend re-validates authoritatively regardless.
  //
  // IST-anchored: every instant below is built from an explicit "+05:30"
  // offset string, so the comparison is correct regardless of the viewer's
  // browser timezone (the backend's now_ist()/to_ist() are always IST —
  // comparing against browser-local Date arithmetic here would silently
  // disagree with the backend for any non-IST browser). Date.now() is
  // always a true UTC epoch value, so it needs no conversion.
  useEffect(() => {
    setTimeError("");
    if (!policy || !date || !time) return;
    const requestedMs = new Date(`${date}T${time}:00+05:30`).getTime();
    const minAllowedMs = Date.now() + policy.min_lead_minutes * 60000;
    if (requestedMs < minAllowedMs) {
      setTimeError(`Please choose a time at least ${policy.min_lead_minutes} minutes from now.`);
      return;
    }
    const windowStartMs = new Date(`${date}T${policy.operating_start}:00+05:30`).getTime();
    const windowEndMs = new Date(`${date}T${policy.operating_end}:00+05:30`).getTime();
    const requestedEndMs = requestedMs + totalDuration * 60000;
    if (requestedMs < windowStartMs || requestedEndMs > windowEndMs) {
      setTimeError(`Please choose a time between ${policy.operating_start} and ${policy.operating_end} that leaves room for a ${totalDuration}-minute service.`);
    }
  }, [policy, date, time, totalDuration]);

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
          line1: addressLine,
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
        combo_id: selectedCombo ? selectedCombo.id : undefined,
        scheduled_date: date,
        scheduled_slot: time,
        subscription_id: subscriptionId || undefined,
        coupon_code: !subscriptionId && couponDiscount > 0 ? couponCode : undefined,
        customer_notes: notes || undefined,
        alternate_contact_name: altContactName || undefined,
        alternate_contact_phone: altContactPhone || undefined,
      });
    },
    onSuccess: (booking) => {
      queryClient.invalidateQueries({ queryKey: ["my-bookings"] });
      navigate(`/app/bookings/${booking.id}`);
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

  const toggleService = (id: string) => {
    setComboId(null);
    setServiceIds((prev) => (prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]));
    resetCoupon();
  };
  const selectCombo = (id: string) => {
    setServiceIds([]);
    setComboId((prev) => (prev === id ? null : id));
    resetCoupon();
  };

  // Tapping a saved vehicle fills the plate/model/type fields exactly as
  // stored, guaranteeing `resolvedVehicleId` above matches it — typing the
  // plate by hand (the only option before this) silently broke subscription
  // matching on the smallest typo, with no indication why.
  const selectVehicle = (v: NonNullable<typeof vehicles>[number]) => {
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
    if (!resolvedVehicleId) return "You have a subscription, but it's tied to a specific saved vehicle — pick that vehicle above (rather than typing a new plate) to use it here.";
    const forThisVehicle = mySubscriptions.some((s) => s.vehicle_id === resolvedVehicleId);
    if (!forThisVehicle) return "Your subscription is registered to a different vehicle — select that vehicle above to pay with it.";
    return "Your subscription for this vehicle is expired or has no washes left.";
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

  const hasSelection = selectedServices.length > 0 || !!selectedCombo;
  const stepValid = [
    !!carNumber && hasSelection && !!date && !!time && !timeError,
    !!addressLine && !!city && !!state && !!pincode,
    true,
  ][step];

  const todayStr = todayIST();

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="font-display text-2xl font-bold uppercase text-[var(--color-primary)]">Book Your Wash</h1>
        <p className="text-sm text-[var(--color-text-secondary)]">{STEP_HINTS[step]}</p>
      </div>

      <SubscriptionQuickBook
        subscriptions={allUsableSubscriptions}
        plans={subscriptionPlans}
        vehicles={vehicles}
        addresses={addresses}
        services={services}
        policy={policy}
      />

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
                  <div className="mb-3 flex flex-wrap gap-2">
                    {vehicles.map((v) => {
                      const selected = !showNewVehicleForm && v.registration_number.toUpperCase() === carNumber.toUpperCase();
                      return (
                        <button
                          key={v.id}
                          type="button"
                          onClick={() => selectVehicle(v)}
                          className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors ${
                            selected ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
                          }`}
                        >
                          {v.brand} {v.model} · {v.registration_number}
                        </button>
                      );
                    })}
                    <button
                      type="button"
                      onClick={() => {
                        setShowNewVehicleForm(true);
                        setCarNumber("");
                        setCarModel("");
                      }}
                      className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors ${
                        showNewVehicleForm ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white" : "border-dashed border-gray-300 text-[var(--color-text-secondary)] hover:border-gray-400"
                      }`}
                    >
                      + Different vehicle
                    </button>
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

                <p className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">Individual services</p>
                {servicesLoading ? (
                  <p className="text-sm text-[var(--color-text-secondary)]">Loading services…</p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {services.map((s) => {
                      const selected = serviceIds.includes(s.id);
                      const { price, firstTime } = priceFor(s, vehicleType);
                      const shown = isFirstOrderGuess && firstTime != null ? firstTime : price;
                      return (
                        <button
                          key={s.id}
                          disabled={!!comboId}
                          onClick={() => toggleService(s.id)}
                          className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors disabled:opacity-40 ${
                            selected ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
                          }`}
                        >
                          {s.name} · ₹{shown}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>

              <div className="border-t border-gray-100 pt-5">
                <p className="mb-3 text-sm font-medium text-[var(--color-text-primary)]">When should we come?</p>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <Input label="Select Date" type="date" min={todayStr} value={date} onChange={(e) => setDate(e.target.value)} required />
                  {policy && (
                    <Input
                      label="Select Time"
                      type="time"
                      min={policy.operating_start}
                      max={policy.operating_end}
                      value={time}
                      onChange={(e) => setTime(e.target.value)}
                      hint={`Available ${policy.operating_start}–${policy.operating_end}, ${policy.min_lead_minutes}+ min from now`}
                      required
                    />
                  )}
                </div>
                {timeError && <p className="mt-1.5 flex items-center gap-1 text-xs text-[var(--color-error)]"><Clock className="h-3 w-3" /> {timeError}</p>}
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="space-y-5">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Input label="Full Name" value={user?.full_name || ""} disabled hint="From your profile" />
                <Input label="Mobile Number" value={user?.phone || ""} disabled hint="From your profile" />
              </div>

              <AddressPicker
                addresses={addresses}
                selectedId={selectedAddressId}
                showingNewForm={showNewAddressForm}
                onSelect={selectAddress}
                onAddNew={() => {
                  setShowNewAddressForm(true);
                  setSelectedAddressId(null);
                  setAddressLine("");
                  setLandmark("");
                  setCity("");
                  setState("");
                  setPincode("");
                  setAddressLat(null);
                  setAddressLng(null);
                }}
              />

              <Input label="Address / Location" placeholder="Enter your full address" value={addressLine} onChange={(e) => { setAddressLine(e.target.value); setSelectedAddressId(null); }} required />
              <Input label="Landmark (Optional)" placeholder="Near Apollo Hospital" value={landmark} onChange={(e) => setLandmark(e.target.value)} />

              <div>
                <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Pin your exact location (recommended)</p>
                <MapPicker
                  latitude={addressLat}
                  longitude={addressLng}
                  onChange={(lat, lng) => {
                    setAddressLat(lat);
                    setAddressLng(lng);
                  }}
                  onAddressResolved={setDetectedAddress}
                  showUseMyLocation
                />
                {detectedAddress && (
                  <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-[var(--color-secondary-light)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
                    <span>
                      Detected: {detectedAddress.line1}
                      {detectedAddress.city ? `, ${detectedAddress.city}` : ""}
                      {detectedAddress.pincode ? ` - ${detectedAddress.pincode}` : ""}
                    </span>
                    <button
                      type="button"
                      className="shrink-0 font-semibold text-[var(--color-primary)]"
                      onClick={() => {
                        setAddressLine(detectedAddress.line1 || addressLine);
                        setCity(detectedAddress.city || city);
                        setState(detectedAddress.state || state);
                        setPincode(detectedAddress.pincode || pincode);
                        setDetectedAddress(null);
                      }}
                    >
                      Use this
                    </button>
                  </div>
                )}
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <Input label="City" value={city} onChange={(e) => setCity(e.target.value)} required />
                <Input label="State" value={state} onChange={(e) => setState(e.target.value)} required />
                <Input label="Pincode" value={pincode} onChange={(e) => setPincode(e.target.value)} required />
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

              <div className="space-y-2 rounded-xl bg-[var(--color-surface)] p-4">
                {selectedCombo ? (
                  <div className="flex justify-between text-sm">
                    <span className="text-[var(--color-text-secondary)]">{selectedCombo.name} (combo)</span>
                    <span className="font-mono-num text-[var(--color-text-primary)]">₹{subtotal}</span>
                  </div>
                ) : (
                  selectedServices.map((s) => {
                    const { price, firstTime } = priceFor(s, vehicleType);
                    const shown = isFirstOrderGuess && firstTime != null ? firstTime : price;
                    return (
                      <div key={s.id} className="flex justify-between text-sm">
                        <span className="text-[var(--color-text-secondary)]">{s.name}</span>
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
                  <div className="flex justify-between text-sm text-[var(--color-success)]">
                    <span>Covered by your plan</span>
                    <span className="font-mono-num">-₹{subtotal}</span>
                  </div>
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
              <Button isLoading={createMutation.isPending} onClick={() => createMutation.mutate()}>
                <CheckCircle2 className="h-4 w-4" /> Confirm Booking
              </Button>
            )}
          </div>
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
                {selectedCombo ? selectedCombo.name : selectedServices.length ? selectedServices.map((s) => s.name).join(", ") : "—"}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--color-text-secondary)]">Date</span>
              <span className="flex items-center gap-1 font-medium text-[var(--color-text-primary)]">
                <Calendar className="h-3.5 w-3.5" /> {date || "—"}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--color-text-secondary)]">Time</span>
              <span className="font-medium text-[var(--color-text-primary)]">{time || "—"}</span>
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
