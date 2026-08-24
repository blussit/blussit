import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Calendar, Check, CheckCircle2, Clock, MapPin, Phone, Search, UserPlus } from "lucide-react";
import { crmApi } from "../../api/crm";
import { adminServiceCenterApi, adminUserApi } from "../../api/admin";
import { catalogApi, comboOfferApi, bookingPolicyApi, vehicleTypeApi } from "../../api/catalog";
import { subscriptionApi } from "../../api/engagement";
import { bookingApi, type ManagerCreateBookingPayload } from "../../api/booking";
import { Button, Card, Input, Modal, Select } from "../../components/ui";
import { MapPicker, type ResolvedAddress } from "../../components/shared/MapPicker";
import { SubscriptionPicker } from "../../components/shared/SubscriptionPicker";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { todayIST } from "../../lib/date";
import type { Address, ComboOffer, Service, User, Vehicle, VehicleType } from "../../types";

const STEPS = ["Find customer", "Service & time", "Vehicle & address", "Confirm"];
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
  const queryClient = useQueryClient();
  const [step, setStep] = useState(0);

  // Assign-a-plan (step 0, once a customer is found)
  const [assignOpen, setAssignOpen] = useState(false);
  const [assignPlanId, setAssignPlanId] = useState("");
  const [assignVehicleId, setAssignVehicleId] = useState("");
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

  // Step 1 — service & time
  const [serviceIds, setServiceIds] = useState<string[]>([]);
  const [comboId, setComboId] = useState<string | null>(null);
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [timeError, setTimeError] = useState("");

  // Step 2 — vehicle & address
  const [selectedVehicleId, setSelectedVehicleId] = useState<string | null>(null);
  const [selectedAddressId, setSelectedAddressId] = useState<string | null>(null);
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
  const [subscriptionId, setSubscriptionId] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [submitError, setSubmitError] = useState("");

  const { data: policy } = useQuery({ queryKey: ["booking-policy"], queryFn: bookingPolicyApi.get });
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
  // Only meaningful once an EXISTING vehicle is picked in step 2 — a
  // brand-new vehicle can't have a subscription linked to it yet.
  const eligibleSubscriptions = (customerSubscriptions || []).filter(
    (s) => s.effective_status === "active" && s.remaining_service_count > 0 && !!selectedVehicleId && s.vehicle_id === selectedVehicleId
  );

  const assignPlan = subscriptionPlans?.find((p) => p.id === assignPlanId) || null;
  const assignEligibleVehicles = (customerVehicles || []).filter(
    (v) => !assignPlan?.vehicle_types?.length || assignPlan.vehicle_types.includes(v.vehicle_type)
  );

  const assignSubMutation = useMutation({
    mutationFn: () => subscriptionApi.assign({ customer_id: customer!.id, plan_id: assignPlanId, vehicle_id: assignVehicleId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["customer-subscriptions", customer?.id] });
      setAssignOpen(false);
      setAssignPlanId("");
      setAssignVehicleId("");
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

  const subtotal = useMemo(() => {
    if (selectedCombo) return priceFor(selectedCombo, vehicleType);
    return selectedServices.reduce((sum, s) => sum + priceFor(s, vehicleType), 0);
  }, [selectedServices, selectedCombo, vehicleType]);

  const totalDuration = useMemo(() => {
    if (selectedCombo) {
      const included = services.filter((s) => selectedCombo.service_ids.includes(s.id));
      return included.reduce((sum, s) => sum + s.duration_minutes, 0) || 60;
    }
    return selectedServices.reduce((sum, s) => sum + s.duration_minutes, 0) || 60;
  }, [selectedServices, selectedCombo, services]);

  const searchMutation = useMutation({
    mutationFn: () => crmApi.searchCustomerByPhone(phone.trim()),
    onSuccess: async (found) => {
      setSearchedPhone(phone.trim());
      setCustomerError("");
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

  // Mirrors NewBookingPage.tsx's client-side check — IST-anchored so it
  // agrees with the backend's authoritative validation regardless of the
  // manager's browser timezone. Backend still re-validates regardless.
  useMemo(() => {
    setTimeError("");
    if (!policy || !date || !time) return "";
    const requestedMs = new Date(`${date}T${time}:00+05:30`).getTime();
    const minAllowedMs = Date.now() + policy.min_lead_minutes * 60000;
    if (requestedMs < minAllowedMs) {
      setTimeError(`Please choose a time at least ${policy.min_lead_minutes} minutes from now.`);
      return "";
    }
    const windowStartMs = new Date(`${date}T${policy.operating_start}:00+05:30`).getTime();
    const windowEndMs = new Date(`${date}T${policy.operating_end}:00+05:30`).getTime();
    if (requestedMs < windowStartMs || requestedMs + totalDuration * 60000 > windowEndMs) {
      setTimeError(`Please choose a time between ${policy.operating_start} and ${policy.operating_end} that leaves room for a ${totalDuration}-minute service.`);
    }
    return "";
  }, [policy, date, time, totalDuration]);

  const toggleService = (id: string) => {
    setComboId(null);
    setServiceIds((prev) => (prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]));
  };
  const selectCombo = (id: string) => {
    setServiceIds([]);
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
        combo_id: selectedCombo ? selectedCombo.id : undefined,
        scheduled_date: date,
        scheduled_slot: time,
        payment_method: paymentMethod,
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
      navigate(`/manager/bookings/${booking.id}`);
    },
    onError: (err) => setSubmitError(getErrorMessage(err)),
  });

  const hasSelection = selectedServices.length > 0 || !!selectedCombo;
  const hasVehicle = !!selectedVehicleId || (!!brand && !!model && !!regNumber);
  const hasAddress = !!selectedAddressId || (!!addressLine && !!city && !!state && !!pincode);
  const stepValid = [
    !!customer,
    hasSelection && !!date && !!time && !timeError,
    hasVehicle && hasAddress,
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
                  <p className="font-semibold text-[var(--color-text-primary)]">{customer.full_name}</p>
                  <p className="mt-0.5 flex items-center gap-1.5 text-sm text-[var(--color-text-secondary)]">
                    <Phone className="h-3.5 w-3.5" /> {customer.phone}
                  </p>
                  <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                    {customerVehicles.length} saved vehicle{customerVehicles.length === 1 ? "" : "s"} · {customerAddresses.length} saved address{customerAddresses.length === 1 ? "" : "es"}
                  </p>

                  <div className="mt-3 border-t border-green-200 pt-3">
                    <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Subscriptions</p>
                    {customerSubscriptions?.length ? (
                      <div className="space-y-1.5">
                        {customerSubscriptions.map((s) => {
                          const plan = subscriptionPlans?.find((p) => p.id === s.plan_id);
                          const vehicle = customerVehicles.find((v) => v.id === s.vehicle_id);
                          return (
                            <p key={s.id} className="text-xs text-[var(--color-text-secondary)]">
                              {plan?.name || "Plan"} — {s.remaining_service_count}/{s.total_service_count} left ·{" "}
                              {vehicle ? `${vehicle.brand} ${vehicle.model}` : "vehicle"} ·{" "}
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
                      onClick={() => setVehicleType(t.id)}
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

                <p className="mb-2 text-sm font-medium text-[var(--color-text-primary)]">Choose service(s)</p>
                {servicesLoading ? (
                  <p className="text-sm text-[var(--color-text-secondary)]">Loading services…</p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {services.map((s) => {
                      const selected = serviceIds.includes(s.id);
                      return (
                        <button
                          key={s.id}
                          disabled={!!comboId}
                          onClick={() => toggleService(s.id)}
                          className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors disabled:opacity-40 ${
                            selected ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
                          }`}
                        >
                          {s.name} · ₹{priceFor(s, vehicleType)}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>

              <Input label="Select Date" type="date" min={todayIST()} value={date} onChange={(e) => setDate(e.target.value)} required />
              {policy && (
                <div>
                  <Input
                    label="Select Time"
                    type="time"
                    min={policy.operating_start}
                    max={policy.operating_end}
                    value={time}
                    onChange={(e) => setTime(e.target.value)}
                    hint={`Available ${policy.operating_start}–${policy.operating_end}, at least ${policy.min_lead_minutes} min from now`}
                    required
                  />
                  {timeError && <p className="mt-1.5 flex items-center gap-1 text-xs text-[var(--color-error)]"><Clock className="h-3 w-3" /> {timeError}</p>}
                </div>
              )}
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
                        onClick={() => setSelectedVehicleId((prev) => (prev === v.id ? null : v.id))}
                        className={`rounded-xl border px-3.5 py-2 text-left text-sm ${
                          selectedVehicleId === v.id ? "border-[var(--color-primary)] bg-[var(--color-primary-light)]" : "border-gray-200 hover:border-gray-300"
                        }`}
                      >
                        {v.brand} {v.model} · {v.registration_number}
                      </button>
                    ))}
                  </div>
                )}
                {!selectedVehicleId && (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                    <Input label="Brand" value={brand} onChange={(e) => setBrand(e.target.value)} placeholder="Maruti" />
                    <Input label="Model" value={model} onChange={(e) => setModel(e.target.value)} placeholder="Swift" />
                    <Input label="Registration number" value={regNumber} onChange={(e) => setRegNumber(e.target.value.toUpperCase())} placeholder="MP09XX1234" />
                  </div>
                )}
              </div>

              <div>
                <p className="mb-2 text-sm font-medium text-[var(--color-text-primary)]">Address</p>
                {customerAddresses.length > 0 && (
                  <div className="mb-3 flex flex-wrap gap-2">
                    {customerAddresses.map((a) => (
                      <button
                        key={a.id}
                        onClick={() => setSelectedAddressId((prev) => (prev === a.id ? null : a.id))}
                        className={`rounded-xl border px-3.5 py-2 text-left text-sm ${
                          selectedAddressId === a.id ? "border-[var(--color-primary)] bg-[var(--color-primary-light)]" : "border-gray-200 hover:border-gray-300"
                        }`}
                      >
                        {a.label}: {a.line1}, {a.city}
                      </button>
                    ))}
                  </div>
                )}
                {!selectedAddressId && (
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
                      <span className="text-[var(--color-text-secondary)]">{s.name}</span>
                      <span className="font-mono-num text-[var(--color-text-primary)]">₹{priceFor(s, vehicleType)}</span>
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
            <div className="flex justify-between border-t border-gray-100 pt-3 text-base">
              <span className="font-semibold text-[var(--color-text-primary)]">Price</span>
              <span className="font-mono-num font-bold text-[var(--color-secondary)]">₹{subtotal || 0}</span>
            </div>
          </div>
        </Card>
      </div>

      <Modal open={assignOpen} onClose={() => setAssignOpen(false)} title="Assign a subscription">
        <div className="space-y-4">
          <Select label="Plan" value={assignPlanId} onChange={(e) => setAssignPlanId(e.target.value)}>
            <option value="">Choose a plan</option>
            {(subscriptionPlans || []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} — ₹{p.discounted_price ?? p.price}
              </option>
            ))}
          </Select>
          {assignPlanId && (
            <Select label="Vehicle" value={assignVehicleId} onChange={(e) => setAssignVehicleId(e.target.value)}>
              <option value="">Choose a vehicle</option>
              {assignEligibleVehicles.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.brand} {v.model} · {v.registration_number}
                </option>
              ))}
            </Select>
          )}
          {assignPlanId && assignEligibleVehicles.length === 0 && (
            <p className="text-sm text-[var(--color-error)]">None of this customer's vehicles match this plan's eligible types.</p>
          )}
          {assignError && <p className="text-sm text-[var(--color-error)]">{assignError}</p>}
          <Button
            className="w-full"
            disabled={!assignPlanId || !assignVehicleId}
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
