import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Calendar, Check, CheckCircle2, KeyRound, MapPin, Phone, Search, UserPlus } from "lucide-react";
import { crmApi } from "../../api/crm";
import { adminServiceCenterApi, adminUserApi } from "../../api/admin";
import { authApi } from "../../api/auth";
import { catalogApi, comboOfferApi, serviceCenterApi, vehicleTypeApi } from "../../api/catalog";
import { subscriptionApi } from "../../api/engagement";
import { bookingApi, type ManagerCreateBookingPayload } from "../../api/booking";
import { Button, Card, Input, Modal, Select } from "../../components/ui";
import { MapPicker, type ResolvedAddress } from "../../components/shared/MapPicker";
import { SubscriptionPicker } from "../../components/shared/SubscriptionPicker";
import { SlotPicker } from "../../components/shared/SlotPicker";
import { useConfirm } from "../../context/ConfirmContext";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { planPriceFor, subscriptionCoversType } from "../../lib/planTier";
import { addonKit, baseGroups, bikeTypeIds, variantCount, type BaseGroup } from "../../lib/serviceMix";
import { QtyStepper } from "../../components/shared/QtyStepper";
import { PLATE_FORMAT_HINT, validateIndianPlate } from "../../lib/validators";
import type { Address, ComboOffer, Service, User, Vehicle, VehicleType } from "../../types";

const STEPS = ["Find customer", "Service", "Vehicle, address & time", "Confirm"];
const PAYMENT_METHODS = [
  { value: "cash", label: "Cash" },
  { value: "online", label: "Online" },
];

function priceFor(item: Service | ComboOffer, vehicleType: VehicleType): number {
  return item.vehicle_type_prices?.[vehicleType] ?? item.price;
}

function randomTempPassword(): string {
  return `Temp${Math.random().toString(36).slice(2, 8)}${Math.floor(Math.random() * 90 + 10)}`;
}

export default function ManagerNewBookingPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const [step, setStep] = useState(0);

  // Assign-a-plan (step 0, once a customer is found)
  const [assignOpen, setAssignOpen] = useState(false);
  const [assignPlanId, setAssignPlanId] = useState("");
  // The tier the plan is granted at — same meaning as a customer purchase:
  // price for that vehicle type, redeemable on that type or smaller.
  const [assignTypeId, setAssignTypeId] = useState("");
  const [assignError, setAssignError] = useState("");

  // Step 0 — customer
  const [phone, setPhone] = useState("");
  const [searchedPhone, setSearchedPhone] = useState<string | null>(null);
  const [customer, setCustomer] = useState<User | null>(null);
  const [customerVehicles, setCustomerVehicles] = useState<Vehicle[]>([]);
  const [customerAddresses, setCustomerAddresses] = useState<Address[]>([]);
  const [isNewCustomer, setIsNewCustomer] = useState(false);
  const [newName, setNewName] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [tempPassword, setTempPassword] = useState(randomTempPassword());
  const [customerError, setCustomerError] = useState("");

  // Step 1 — service
  const [serviceIds, setServiceIds] = useState<string[]>([]);
  // Per-unit add-on counts (Extra Bike Wash ×N, Bike Polish ×N) — kept in
  // lockstep with serviceIds by the handlers below; sent as-is to the API.
  const [serviceQty, setServiceQty] = useState<Record<string, number>>({});
  const [comboId, setComboId] = useState<string | null>(null);
  // Step 2 — vehicle, address & time (date/slot depend on the address, so
  // they live here, not with service selection)
  const [date, setDate] = useState("");
  const [slot, setSlot] = useState("");

  // Step 2 — vehicle & address. "New" is an explicit choice (its own chip),
  // not the implicit absence of a selection — with saved chips present
  // there was previously no visible way to add a new vehicle/address on
  // the customer's behalf.
  const [selectedVehicleId, setSelectedVehicleId] = useState<string | null>(null);
  const [selectedAddressId, setSelectedAddressId] = useState<string | null>(null);
  const [newVehicleOpen, setNewVehicleOpen] = useState(false);
  const [newAddressOpen, setNewAddressOpen] = useState(false);
  const [vehicleType, setVehicleType] = useState<VehicleType>("");
  const [brand, setBrand] = useState("");
  const [model, setModel] = useState("");
  const [regNumber, setRegNumber] = useState("");
  const [addressLine, setAddressLine] = useState("");
  const [landmark, setLandmark] = useState("");
  const [city, setCity] = useState("");
  const [state, setState] = useState("");
  const [pincode, setPincode] = useState("");
  const [addressLat, setAddressLat] = useState<number | null>(null);
  const [addressLng, setAddressLng] = useState<number | null>(null);
  const [detectedAddress, setDetectedAddress] = useState<ResolvedAddress | null>(null);

  // Step 3 — confirm
  const [paymentMethod, setPaymentMethod] = useState("cash");
  const [couponCode, setCouponCode] = useState("");
  const [subscriptionId, setSubscriptionId] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [submitError, setSubmitError] = useState("");

  const { data: servicesData, isLoading: servicesLoading } = useQuery({
    queryKey: ["services-for-booking"],
    queryFn: () => catalogApi.services({ page_size: 50 }),
  });
  const { data: combos } = useQuery({ queryKey: ["combos-for-booking"], queryFn: () => comboOfferApi.list(true) });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const { data: subscriptionPlans } = useQuery({ queryKey: ["subscription-plans-for-booking"], queryFn: () => subscriptionApi.plans(true) });
  const { data: customerSubscriptions } = useQuery({
    queryKey: ["customer-subscriptions", customer?.id],
    queryFn: () => subscriptionApi.forCustomer(customer!.id),
    enabled: !!customer,
  });
  // Subscription eligibility is by vehicle TYPE: the plan must cover the
  // selected type AND the purchased tier must allow it (bought for one
  // type = that type or cheaper only) — same rules the backend enforces
  // at plan_consumption time.
  const selectedVehicleType = selectedVehicleId ? customerVehicles.find((v) => v.id === selectedVehicleId)?.vehicle_type : vehicleType;
  const eligibleSubscriptions = (customerSubscriptions || []).filter((s) => {
    if (s.effective_status !== "active" || s.remaining_service_count <= 0) return false;
    const plan = subscriptionPlans?.find((p) => p.id === s.plan_id);
    return !!selectedVehicleType && subscriptionCoversType(s, plan, selectedVehicleType);
  });

  // Slots are generated per service center, resolved from whichever
  // address (saved or newly entered) is currently picked.
  const resolvedPincode = selectedAddressId ? customerAddresses.find((a) => a.id === selectedAddressId)?.pincode || "" : pincode;
  const { data: matchedCenters } = useQuery({
    queryKey: ["center-lookup", resolvedPincode],
    queryFn: () => serviceCenterApi.lookupByPincode(resolvedPincode),
    enabled: resolvedPincode.length >= 6,
  });
  const serviceCenterId = matchedCenters?.[0]?.id;

  const assignSubMutation = useMutation({
    mutationFn: () => subscriptionApi.assign({ customer_id: customer!.id, plan_id: assignPlanId, vehicle_type: assignTypeId || undefined }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["customer-subscriptions", customer?.id] });
      setAssignOpen(false);
      setAssignPlanId("");
      setAssignTypeId("");
      setAssignError("");
    },
    onError: (err) => setAssignError(getErrorMessage(err)),
  });

  // Default the type selector to the first admin-configured type once loaded.
  useEffect(() => {
    if (vehicleTypes?.length && !vehicleType) setVehicleType(vehicleTypes[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicleTypes]);

  const services = servicesData?.data || [];
  const selectedServices = services.filter((s) => serviceIds.includes(s.id));
  const selectedCombo = combos?.find((c) => c.id === comboId) || null;

  // Same catalogue rules as the customer wizard (lib/serviceMix.ts, which
  // mirrors the server's _validate_service_mix): base services collapsed by
  // variant group, add-ons matched to the vehicle's class, one −/+ counter
  // for bikes, polish capped at the bikes in the booking.
  const bikeIds = useMemo(() => bikeTypeIds(vehicleTypes), [vehicleTypes]);
  const bookingIsBike = bikeIds.has(vehicleType);
  const groups = useMemo(() => baseGroups(services, vehicleType), [services, vehicleType]);
  const kit = useMemo(() => addonKit(services, vehicleType, bikeIds), [services, vehicleType, bikeIds]);
  const selectedBase = selectedServices.find((s) => !s.is_addon) || null;
  const selectedGroup = selectedBase ? groups.find((g) => g.variants.some((v) => v.id === selectedBase.id)) || null : null;
  const bikeCount = bookingIsBike && selectedBase ? variantCount(selectedBase) : 0;
  const extraBikes = kit.addBike && serviceIds.includes(kit.addBike.id) ? serviceQty[kit.addBike.id] || 1 : 0;
  const polishCount = kit.bikePolish && serviceIds.includes(kit.bikePolish.id) ? serviceQty[kit.bikePolish.id] || 1 : 0;
  const bikesInBooking = bookingIsBike ? bikeCount + extraBikes : extraBikes;
  const qtyOf = (id: string) => serviceQty[id] || 1;

  const subtotal = useMemo(() => {
    if (selectedCombo) return priceFor(selectedCombo, vehicleType);
    return selectedServices.reduce((sum, s) => sum + priceFor(s, vehicleType) * (serviceQty[s.id] || 1), 0);
  }, [selectedServices, selectedCombo, vehicleType, serviceQty]);

  const totalDuration = useMemo(() => {
    if (selectedCombo) {
      const included = services.filter((s) => selectedCombo.service_ids.includes(s.id));
      return included.reduce((sum, s) => sum + s.duration_minutes, 0) || 60;
    }
    return selectedServices.reduce((sum, s) => sum + s.duration_minutes * (serviceQty[s.id] || 1), 0) || 60;
  }, [selectedServices, selectedCombo, services, serviceQty]);

  const searchMutation = useMutation({
    mutationFn: () => crmApi.searchCustomerByPhone(phone.trim()),
    onSuccess: async (found) => {
      setSearchedPhone(phone.trim());
      setCustomerError("");
      setNewVehicleOpen(false);
      setNewAddressOpen(false);
      setSelectedVehicleId(null);
      setSelectedAddressId(null);
      if (found) {
        setCustomer(found);
        setIsNewCustomer(false);
        const detail = await crmApi.customer360(found.id);
        setCustomerVehicles(detail.vehicles);
        setCustomerAddresses(detail.addresses);
      } else {
        setCustomer(null);
        setIsNewCustomer(true);
        setCustomerVehicles([]);
        setCustomerAddresses([]);
      }
    },
    onError: (err) => setCustomerError(getErrorMessage(err)),
  });

  const createCustomerMutation = useMutation({
    mutationFn: () => adminUserApi.createCustomer({ full_name: newName.trim(), phone: phone.trim(), email: newEmail.trim() || undefined, temp_password: tempPassword }),
    onSuccess: (created) => {
      setCustomer(created);
      setCustomerError("");
    },
    onError: (err) => setCustomerError(getErrorMessage(err)),
  });

  // "Forgot password" for an EXISTING customer who called in — deliberately
  // never sees the generated password itself: it's sent straight to the
  // customer's own WhatsApp by the backend (AuthService.
  // staff_reset_customer_password), this mutation's response has no
  // password field to display even if we wanted to.
  const resetPasswordMutation = useMutation({
    mutationFn: () => authApi.resetCustomerPassword(customer!.id),
    onSuccess: () => toast.push({ tone: "success", title: "Reset sent", message: "A new temporary password was sent to the customer's WhatsApp." }),
    onError: (err) => toast.push({ tone: "error", title: "Couldn't reset password", message: getErrorMessage(err) }),
  });

  // Same handlers as the customer wizard: one main service (variant group)
  // at a time, contextual add-ons, per-bike quantities.
  const clearServices = () => {
    setServiceIds([]);
    setServiceQty({});
  };
  const pickGroup = (g: BaseGroup) => {
    setComboId(null);
    setServiceQty({});
    setServiceIds(selectedGroup?.key === g.key ? [] : [g.primary.id]);
  };
  const toggleSimpleAddon = (id: string) => {
    setComboId(null);
    setServiceIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
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
  const setPolish = (n: number) => setPolishRaw(Math.max(0, Math.min(bikesInBooking, n)));
  const selectCombo = (id: string) => {
    clearServices();
    setComboId((prev) => (prev === id ? null : id));
  };

  const submitMutation = useMutation({
    mutationFn: () => {
      if (!customer) throw new Error("No customer selected");
      const payload: ManagerCreateBookingPayload = {
        customer_id: customer.id,
        vehicle_id: selectedVehicleId || undefined,
        new_vehicle: selectedVehicleId
          ? undefined
          : { vehicle_type: vehicleType, brand, model, registration_number: regNumber.toUpperCase() },
        address_id: selectedAddressId || undefined,
        new_address: selectedAddressId
          ? undefined
          : { line1: addressLine, landmark: landmark || undefined, city, state, pincode, latitude: addressLat ?? undefined, longitude: addressLng ?? undefined },
        service_ids: selectedCombo ? undefined : serviceIds,
        service_quantities: selectedCombo || !Object.keys(serviceQty).length ? undefined : serviceQty,
        combo_id: selectedCombo ? selectedCombo.id : undefined,
        scheduled_date: date,
        scheduled_slot: slot,
        payment_method: paymentMethod,
        coupon_code: subscriptionId ? undefined : couponCode.trim() || undefined,
        subscription_id: subscriptionId || undefined,
        customer_notes: notes || undefined,
      };
      return bookingApi.managerCreate(payload);
    },
    onSuccess: async (booking) => {
      // Dispatch is resolved purely by the address's geocoded location —
      // it can land at a center other than the manager's own, and there
      // was previously no way for them to see that before or after
      // submitting. A quick toast beats silently navigating away.
      try {
        const center = await adminServiceCenterApi.get(booking.service_center_id);
        toast.push({ tone: "info", title: "Booking dispatched", message: `Assigned to ${center.name} based on the customer's address.` });
      } catch {
        // Non-critical — the booking itself already succeeded either way.
      }
      // /manager/bookings/:id isn't a real route (booking detail is a
      // drawer on the queue page, not its own page) — ?highlight jumps
      // the queue straight to this booking, same convention the queue's
      // own internal links already use.
      navigate(`/manager/bookings?highlight=${booking.id}`);
    },
    onError: (err) => setSubmitError(getErrorMessage(err)),
  });

  const hasSelection = selectedServices.length > 0 || !!selectedCombo;
  // New-entry fields only count when the new-entry form is actually the
  // active choice — leftover typed state behind a hidden form must not
  // satisfy the step.
  const hasVehicle =
    !!selectedVehicleId ||
    ((customerVehicles.length === 0 || newVehicleOpen) && !!brand && !!model && validateIndianPlate(regNumber) !== null);
  const hasAddress =
    !!selectedAddressId || ((customerAddresses.length === 0 || newAddressOpen) && !!addressLine && !!city && !!state && !!pincode);
  const stepValid = [
    !!customer,
    hasSelection,
    hasVehicle && hasAddress && !!date && !!slot,
    true,
  ][step];

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Book on behalf of a customer</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">For phone-in or walk-in bookings — find an existing customer or create a new one.</p>
      </div>

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
            <div className="space-y-5">
              <div className="flex gap-2">
                <Input
                  label="Customer phone number"
                  placeholder="9876543210"
                  value={phone}
                  onChange={(e) => {
                    setPhone(e.target.value);
                    setSearchedPhone(null);
                    setCustomer(null);
                  }}
                />
                <Button className="mt-6 shrink-0" variant="outline" disabled={phone.trim().length < 10} isLoading={searchMutation.isPending} onClick={() => searchMutation.mutate()}>
                  <Search className="h-4 w-4" /> Search
                </Button>
              </div>
              {customerError && <p className="text-sm text-[var(--color-error)]">{customerError}</p>}

              {customer && (
                <Card className="border-2 border-[var(--color-success)] bg-green-50/50 p-4">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="font-semibold text-[var(--color-text-primary)]">{customer.full_name}</p>
                      <p className="mt-0.5 flex items-center gap-1.5 text-sm text-[var(--color-text-secondary)]">
                        <Phone className="h-3.5 w-3.5" /> {customer.phone}
                      </p>
                    </div>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      isLoading={resetPasswordMutation.isPending}
                      onClick={async () => {
                        if (
                          await confirm({
                            title: "Reset this customer's password?",
                            message: "A new temporary password will be sent directly to their WhatsApp — you won't see it.",
                          })
                        )
                          resetPasswordMutation.mutate();
                      }}
                    >
                      <KeyRound className="h-3.5 w-3.5" /> Forgot password?
                    </Button>
                  </div>
                  <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                    {customerVehicles.length} saved vehicle{customerVehicles.length === 1 ? "" : "s"} · {customerAddresses.length} saved address{customerAddresses.length === 1 ? "" : "es"}
                  </p>

                  <div className="mt-3 border-t border-green-200 pt-3">
                    <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Subscriptions</p>
                    {customerSubscriptions?.length ? (
                      <div className="space-y-1.5">
                        {customerSubscriptions.map((s) => {
                          const plan = subscriptionPlans?.find((p) => p.id === s.plan_id);
                          const coversLabel = plan?.vehicle_types?.length ? `${plan.vehicle_types.length} vehicle type(s)` : "any vehicle type";
                          return (
                            <p key={s.id} className="text-xs text-[var(--color-text-secondary)]">
                              {plan?.name || "Plan"} — {s.remaining_service_count}/{s.total_service_count} left ·{" "}
                              covers {coversLabel} ·{" "}
                              <span className={s.effective_status === "active" ? "text-[var(--color-success)]" : ""}>{s.effective_status}</span>
                            </p>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="text-xs text-[var(--color-text-secondary)]">No subscriptions yet.</p>
                    )}
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="mt-2"
                      disabled={!customerVehicles.length}
                      onClick={() => setAssignOpen(true)}
                    >
                      Assign a plan
                    </Button>
                    {!customerVehicles.length && (
                      <p className="mt-1 text-xs text-[var(--color-text-secondary)]">Customer needs a saved vehicle first.</p>
                    )}
                  </div>
                </Card>
              )}

              {searchedPhone && isNewCustomer && !customer && (
                <Card className="border-2 border-amber-300 bg-amber-50/50 p-4">
                  <p className="flex items-center gap-2 font-semibold text-[var(--color-text-primary)]">
                    <UserPlus className="h-4 w-4" /> No account found for {searchedPhone} — create one
                  </p>
                  <div className="mt-3 space-y-3">
                    <Input label="Full name" value={newName} onChange={(e) => setNewName(e.target.value)} required />
                    <Input label="Email (optional)" value={newEmail} onChange={(e) => setNewEmail(e.target.value)} />
                    <div className="flex items-end gap-2">
                      <Input label="Temporary password" value={tempPassword} onChange={(e) => setTempPassword(e.target.value)} />
                      <Button type="button" variant="outline" className="shrink-0" onClick={() => setTempPassword(randomTempPassword())}>
                        Generate
                      </Button>
                    </div>
                    <p className="text-xs text-[var(--color-text-secondary)]">The customer will be asked to change this on their first login.</p>
                    <Button disabled={!newName.trim() || tempPassword.length < 8} isLoading={createCustomerMutation.isPending} onClick={() => createCustomerMutation.mutate()}>
                      Create customer account
                    </Button>
                  </div>
                </Card>
              )}
            </div>
          )}

          {step === 1 && (
            <div className="space-y-6">
              <div>
                <p className="mb-1 text-sm font-medium text-[var(--color-text-primary)]">Vehicle type</p>
                <div className="mb-4 flex flex-wrap gap-2">
                  {(vehicleTypes || []).map((t) => (
                    <button
                      key={t.id}
                      onClick={() => {
                        // car vs bike changes what's bookable — start clean
                        if (t.id !== vehicleType) clearServices();
                        setVehicleType(t.id);
                      }}
                      className={`rounded-full px-3.5 py-1.5 text-xs font-medium ${
                        vehicleType === t.id ? "bg-[var(--color-primary)] text-white" : "bg-gray-100 text-gray-600"
                      }`}
                    >
                      {t.name}
                    </button>
                  ))}
                </div>

                {!!combos?.length && (
                  <>
                    <p className="mb-2 text-sm font-medium text-[var(--color-text-primary)]">Combo offers</p>
                    <div className="mb-4 flex flex-wrap gap-2">
                      {combos.map((c) => (
                        <button
                          key={c.id}
                          onClick={() => selectCombo(c.id)}
                          className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors ${
                            comboId === c.id ? "border-[var(--color-secondary)] bg-[var(--color-secondary)] text-white" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
                          }`}
                        >
                          {c.name} · ₹{priceFor(c, vehicleType)}
                        </button>
                      ))}
                    </div>
                  </>
                )}

                <p className="mb-2 text-sm font-medium text-[var(--color-text-primary)]">Main service</p>
                {servicesLoading ? (
                  <p className="text-sm text-[var(--color-text-secondary)]">Loading services…</p>
                ) : (
                  <>
                    <div className="flex flex-wrap gap-2">
                      {groups.map((g) => {
                        const selected = selectedGroup?.key === g.key;
                        return (
                          <button
                            key={g.key}
                            disabled={!!comboId}
                            onClick={() => pickGroup(g)}
                            className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors disabled:opacity-40 ${
                              selected ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
                            }`}
                          >
                            {g.label} · {g.variants.length > 1 ? "from " : ""}₹{priceFor(g.primary, vehicleType)}
                          </button>
                        );
                      })}
                    </div>

                    {/* Bike bookings: one −/+ counter, priced base + extras. */}
                    {selectedBase && bookingIsBike && (
                      <div className="mt-3 flex items-center justify-between rounded-xl border border-gray-100 bg-[var(--color-surface)] p-3">
                        <div>
                          <p className="text-xs font-medium text-[var(--color-text-primary)]">How many bikes?</p>
                          <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
                            {kit.addBike
                              ? `First bike ₹${priceFor(selectedBase, vehicleType)}, ₹${priceFor(kit.addBike, vehicleType)} each additional`
                              : `₹${priceFor(selectedBase, vehicleType)} per bike`}
                          </p>
                        </div>
                        <QtyStepper value={bikesInBooking} min={1} max={10} onChange={(n) => setExtraBikes(Math.max(0, n - bikeCount))} />
                      </div>
                    )}

                    {/* Add-ons — only the ones valid for what's selected */}
                    {selectedBase && (kit.simple.length > 0 || kit.addBike || kit.bikePolish) && (
                      <div className="mt-4">
                        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">Add-ons</p>
                        <div className="space-y-2.5">
                          {kit.simple.map((s) => {
                            const on = serviceIds.includes(s.id);
                            return (
                              <button
                                key={s.id}
                                onClick={() => toggleSimpleAddon(s.id)}
                                className={`flex w-full items-center justify-between rounded-xl border px-3.5 py-2.5 text-sm transition-colors ${
                                  on ? "border-[var(--color-primary)] bg-[var(--color-primary-light)]" : "border-gray-200 hover:border-gray-300"
                                }`}
                              >
                                <span className="font-medium text-[var(--color-text-primary)]">+ {s.name}</span>
                                <span className="font-mono-num text-[var(--color-text-primary)]">₹{priceFor(s, vehicleType)}</span>
                              </button>
                            );
                          })}

                          {!bookingIsBike && kit.addBike && (
                            <div className="flex items-center justify-between rounded-xl border border-gray-200 px-3.5 py-2.5 text-sm">
                              <div>
                                <p className="font-medium text-[var(--color-text-primary)]">+ Add bikes to this visit</p>
                                <p className="text-xs text-[var(--color-text-secondary)]">₹{priceFor(kit.addBike, vehicleType)} per bike, washed at the same doorstep</p>
                              </div>
                              <QtyStepper value={extraBikes} min={0} max={10} onChange={setExtraBikes} />
                            </div>
                          )}

                          {kit.bikePolish && bikesInBooking > 0 && (
                            <div className="flex items-center justify-between rounded-xl border border-gray-200 px-3.5 py-2.5 text-sm">
                              <div>
                                <p className="font-medium text-[var(--color-text-primary)]">+ {kit.bikePolish.name}</p>
                                <p className="text-xs text-[var(--color-text-secondary)]">
                                  ₹{priceFor(kit.bikePolish, vehicleType)} per bike · up to {bikesInBooking} bike{bikesInBooking > 1 ? "s" : ""}
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

          {step === 2 && (
            <div className="space-y-6">
              <div>
                <p className="mb-2 text-sm font-medium text-[var(--color-text-primary)]">Vehicle</p>
                {customerVehicles.length > 0 && (
                  <div className="mb-3 flex flex-wrap gap-2">
                    {customerVehicles.map((v) => (
                      <button
                        key={v.id}
                        onClick={() => {
                          setSelectedVehicleId((prev) => (prev === v.id ? null : v.id));
                          setNewVehicleOpen(false);
                          // The real vehicle's type is what the backend will
                          // price against — realign the step-1 pricing type,
                          // and restart the service pick if the CLASS flips
                          // (a car service can't ride on a bike booking).
                          if (v.vehicle_type !== vehicleType) {
                            if (bikeIds.has(v.vehicle_type) !== bikeIds.has(vehicleType)) clearServices();
                            setVehicleType(v.vehicle_type);
                          }
                        }}
                        className={`max-w-full truncate rounded-xl border px-3.5 py-2 text-left text-sm ${
                          selectedVehicleId === v.id ? "border-2 border-black bg-[var(--color-primary-light)] font-medium" : "border-gray-200 hover:border-gray-300"
                        }`}
                      >
                        {v.brand} {v.model} · {v.registration_number}
                      </button>
                    ))}
                    <button
                      onClick={() => {
                        setSelectedVehicleId(null);
                        setNewVehicleOpen((o) => !o);
                      }}
                      className={`rounded-xl border border-dashed px-3.5 py-2 text-sm font-medium ${
                        newVehicleOpen && !selectedVehicleId
                          ? "border-2 border-solid border-black bg-[var(--color-primary-light)]"
                          : "border-gray-300 text-[var(--color-text-secondary)] hover:border-gray-400"
                      }`}
                    >
                      + New vehicle
                    </button>
                  </div>
                )}
                {!selectedVehicleId && (customerVehicles.length === 0 || newVehicleOpen) && (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                    <Input label="Brand" value={brand} onChange={(e) => setBrand(e.target.value)} placeholder="Maruti" />
                    <Input label="Model" value={model} onChange={(e) => setModel(e.target.value)} placeholder="Swift" />
                    <Input label="Registration number" value={regNumber} onChange={(e) => setRegNumber(e.target.value.toUpperCase())} placeholder="MP09XX1234" hint={regNumber && !validateIndianPlate(regNumber) ? PLATE_FORMAT_HINT : undefined} />
                  </div>
                )}
                {!selectedVehicleId && customerVehicles.length > 0 && !newVehicleOpen && (
                  <p className="text-xs text-[var(--color-text-secondary)]">Pick a saved vehicle, or "+ New vehicle" to add one to the customer's account.</p>
                )}
              </div>

              <div>
                <p className="mb-2 text-sm font-medium text-[var(--color-text-primary)]">Address</p>
                {customerAddresses.length > 0 && (
                  <div className="mb-3 flex flex-wrap gap-2">
                    {customerAddresses.map((a) => (
                      <button
                        key={a.id}
                        onClick={() => {
                          setSelectedAddressId((prev) => (prev === a.id ? null : a.id));
                          setNewAddressOpen(false);
                        }}
                        className={`max-w-full truncate rounded-xl border px-3.5 py-2 text-left text-sm ${
                          selectedAddressId === a.id ? "border-2 border-black bg-[var(--color-primary-light)] font-medium" : "border-gray-200 hover:border-gray-300"
                        }`}
                      >
                        {a.label}: {a.line1}, {a.city}
                      </button>
                    ))}
                    <button
                      onClick={() => {
                        setSelectedAddressId(null);
                        setNewAddressOpen((o) => !o);
                      }}
                      className={`rounded-xl border border-dashed px-3.5 py-2 text-sm font-medium ${
                        newAddressOpen && !selectedAddressId
                          ? "border-2 border-solid border-black bg-[var(--color-primary-light)]"
                          : "border-gray-300 text-[var(--color-text-secondary)] hover:border-gray-400"
                      }`}
                    >
                      + New address
                    </button>
                  </div>
                )}
                {!selectedAddressId && customerAddresses.length > 0 && !newAddressOpen && (
                  <p className="text-xs text-[var(--color-text-secondary)]">Pick a saved address, or "+ New address" to add one — it's saved to the customer's account for next time.</p>
                )}
                {!selectedAddressId && (customerAddresses.length === 0 || newAddressOpen) && (
                  <div className="space-y-3">
                    <Input label="Address line" value={addressLine} onChange={(e) => setAddressLine(e.target.value)} required />
                    <Input label="Landmark (optional)" value={landmark} onChange={(e) => setLandmark(e.target.value)} />
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
                      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-[var(--color-secondary-light)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
                        <span>
                          Detected: {detectedAddress.line1}
                          {detectedAddress.city ? `, ${detectedAddress.city}` : ""}
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
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                      <Input label="City" value={city} onChange={(e) => setCity(e.target.value)} required />
                      <Input label="State" value={state} onChange={(e) => setState(e.target.value)} required />
                      <Input label="Pincode" value={pincode} onChange={(e) => setPincode(e.target.value)} required />
                    </div>
                  </div>
                )}
              </div>

              <div className="border-t border-gray-100 pt-5">
                <p className="mb-3 text-sm font-medium text-[var(--color-text-primary)]">When should we come?</p>
                {resolvedPincode.length >= 6 && !serviceCenterId ? (
                  <p className="text-sm text-[var(--color-error)]">Doorstep service isn't available in this area yet.</p>
                ) : (
                  <SlotPicker serviceCenterId={serviceCenterId} date={date} onDateChange={setDate} value={slot} onChange={setSlot} />
                )}
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-5">
              <div className="space-y-2 rounded-xl bg-[var(--color-surface)] p-4">
                {selectedCombo ? (
                  <div className="flex justify-between text-sm">
                    <span className="text-[var(--color-text-secondary)]">{selectedCombo.name} (combo)</span>
                    <span className="font-mono-num text-[var(--color-text-primary)]">₹{subtotal}</span>
                  </div>
                ) : (
                  selectedServices.map((s) => (
                    <div key={s.id} className="flex justify-between text-sm">
                      <span className="text-[var(--color-text-secondary)]">
                        {s.name}
                        {qtyOf(s.id) > 1 ? ` ×${qtyOf(s.id)}` : ""}
                      </span>
                      <span className="font-mono-num text-[var(--color-text-primary)]">₹{priceFor(s, vehicleType) * qtyOf(s.id)}</span>
                    </div>
                  ))
                )}
                <div className="flex justify-between border-t border-gray-200 pt-2 text-base font-bold text-[var(--color-text-primary)]">
                  <span>Total</span>
                  <span className="font-mono-num">₹{subtotal}</span>
                </div>
                <p className="pt-1 text-xs text-[var(--color-text-secondary)]">Final pricing (incl. any first-time discount) is confirmed server-side on submit.</p>
              </div>

              {!!eligibleSubscriptions.length && (
                <div>
                  <p className="mb-2 text-sm font-medium text-[var(--color-text-primary)]">Pay with the customer's plan</p>
                  <SubscriptionPicker
                    subscriptions={eligibleSubscriptions}
                    plans={subscriptionPlans}
                    selectedId={subscriptionId}
                    onSelect={setSubscriptionId}
                  />
                </div>
              )}

              {!subscriptionId && (
                <div>
                  <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Payment method</p>
                  <div className="flex gap-2">
                    {PAYMENT_METHODS.map((m) => (
                      <button
                        key={m.value}
                        onClick={() => setPaymentMethod(m.value)}
                        className={`rounded-full px-3.5 py-1.5 text-sm font-medium ${
                          paymentMethod === m.value ? "bg-[var(--color-primary)] text-white" : "bg-gray-100 text-gray-600"
                        }`}
                      >
                        {m.label}
                      </button>
                    ))}
                  </div>
                  {/* Phone-in customers quote coupon codes too — the
                    payload always supported it; the input didn't exist. */}
                  {!subscriptionId && (
                    <Input
                      className="mt-3"
                      label="Coupon code (optional)"
                      value={couponCode}
                      onChange={(e) => setCouponCode(e.target.value.toUpperCase())}
                      placeholder="e.g. WELCOME50"
                    />
                  )}
                </div>
              )}

              <Input label="Notes (optional)" value={notes} onChange={(e) => setNotes(e.target.value)} />
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
              <Button isLoading={submitMutation.isPending} onClick={() => submitMutation.mutate()}>
                <CheckCircle2 className="h-4 w-4" /> Confirm Booking
              </Button>
            )}
          </div>
        </Card>

        <Card className="h-fit p-6 lg:sticky lg:top-24">
          <p className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--color-text-primary)]">Booking summary</p>
          <div className="mb-4 flex h-28 items-center justify-center rounded-xl bg-[var(--color-primary-light)]">
            <MapPin className="h-8 w-8 text-[var(--color-primary)]" />
          </div>
          <div className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-[var(--color-text-secondary)]">Customer</span>
              <span className="text-right font-medium text-[var(--color-text-primary)]">{customer?.full_name || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--color-text-secondary)]">Service</span>
              <span className="text-right font-medium text-[var(--color-text-primary)]">
                {selectedCombo ? selectedCombo.name : selectedServices.length ? selectedServices.map((s) => (qtyOf(s.id) > 1 ? `${s.name} ×${qtyOf(s.id)}` : s.name)).join(", ") : "—"}
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
              <span className="font-mono-num font-bold text-[var(--color-secondary)]">₹{subtotal || 0}</span>
            </div>
          </div>
        </Card>
      </div>

      <Modal open={assignOpen} onClose={() => setAssignOpen(false)} title="Assign a subscription">
        <div className="space-y-4">
          <Select label="Plan" value={assignPlanId} onChange={(e) => { setAssignPlanId(e.target.value); setAssignTypeId(""); }}>
            <option value="">Choose a plan</option>
            {(subscriptionPlans || []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} — ₹{p.discounted_price ?? p.price}
              </option>
            ))}
          </Select>
          {(() => {
            const plan = subscriptionPlans?.find((p) => p.id === assignPlanId);
            if (!plan) return null;
            const candidateTypes = plan.vehicle_types?.length
              ? plan.vehicle_types
              : (vehicleTypes || []).filter((t) => t.is_active !== false).map((t) => t.id);
            if (!candidateTypes.length) return null;
            return (
              <Select label="Vehicle type (tier)" value={assignTypeId} onChange={(e) => setAssignTypeId(e.target.value)}>
                <option value="">Choose the vehicle type it's sold for</option>
                {candidateTypes.map((id) => (
                  <option key={id} value={id}>
                    {vehicleTypes?.find((t) => t.id === id)?.name || id} — ₹{planPriceFor(plan, id)}
                  </option>
                ))}
              </Select>
            );
          })()}
          <p className="text-xs text-[var(--color-text-secondary)]">
            Priced for the chosen vehicle type — redeemable on that type or a smaller one, never bigger.
          </p>
          {assignError && <p className="text-sm text-[var(--color-error)]">{assignError}</p>}
          <Button
            className="w-full"
            disabled={!assignPlanId}
            isLoading={assignSubMutation.isPending}
            onClick={() => assignSubMutation.mutate()}
          >
            Assign subscription
          </Button>
        </div>
      </Modal>
    </div>
  );
}
