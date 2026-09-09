import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Car, Gift, Zap } from "lucide-react";
import { addressApi } from "../../api/profile";
import { serviceCenterApi, vehicleTypeApi } from "../../api/catalog";
import { bookingApi } from "../../api/booking";
import { Badge, Button, Card, Input } from "../ui";
import { AddressPicker } from "./AddressPicker";
import { PhoneVerificationModal } from "./PhoneVerificationModal";
import { QtyStepper } from "./QtyStepper";
import { SlotPicker } from "./SlotPicker";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";
import { format } from "../../lib/date";
import { addonKit, baseGroups, bikeTypeIds } from "../../lib/serviceMix";
import { subscriptionCoversType } from "../../lib/planTier";
import type { Address, Booking, Service, SubscriptionPlan, UserSubscription, Vehicle, VehicleTypeOption } from "../../types";

/**
 * The one-tap path for a customer who just wants to spend a subscription
 * they already own. What the plan covers is fixed (admin decided); what's
 * still chosen per visit: which eligible vehicle, when, where — plus
 * optional PAID extras, priced by the same rules as a normal booking:
 *
 *  - swap the covered service for a costlier MAIN service → pay the gap;
 *    a cheaper one is simply covered (still one full visit, no credit);
 *  - add-ons (extra bike washes at ₹60/bike, polish, ...) are NEVER
 *    covered by a plan — always a real extra charge;
 *  - a subscription bought for one vehicle type works on that type or a
 *    smaller one, never bigger (lib/planTier.ts mirrors the server rule).
 */
export function SubscriptionQuickBook({
  subscriptions,
  plans,
  vehicles,
  addresses,
  services,
  autoOpen = false,
}: {
  subscriptions: UserSubscription[];
  plans: SubscriptionPlan[] | undefined;
  vehicles: Vehicle[] | undefined;
  addresses: Address[] | undefined;
  services: Service[];
  /** Arriving via "Book with my plan": jump straight into the flow. */
  autoOpen?: boolean;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [openSub, setOpenSub] = useState<UserSubscription | null>(null);
  const [autoOpened, setAutoOpened] = useState(false);
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const typeName = (id: string) => vehicleTypes?.find((t) => t.id === id)?.name || id;

  useEffect(() => {
    if (autoOpen && !autoOpened && subscriptions.length) {
      setOpenSub(subscriptions[0]);
      setAutoOpened(true);
    }
  }, [autoOpen, autoOpened, subscriptions]);

  if (!subscriptions.length) return null;

  return (
    <div>
      <p className="mb-3 flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-[var(--color-text-primary)]">
        <Zap className="h-4 w-4 text-[var(--color-success)]" /> Book from a subscription
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {subscriptions.map((sub) => {
          const plan = plans?.find((p) => p.id === sub.plan_id);
          const coversLabel = sub.vehicle_type
            ? `For ${typeName(sub.vehicle_type)} & smaller`
            : plan?.vehicle_types?.length
              ? `Covers ${plan.vehicle_types.map(typeName).join(", ")}`
              : "Covers any vehicle type";
          return (
            <Card
              key={sub.id}
              className="cursor-pointer border-2 border-[var(--color-success)]/30 bg-[var(--color-success)]/5 p-4 transition-colors hover:border-[var(--color-success)]"
              onClick={() => setOpenSub(sub)}
            >
              <div className="flex items-start justify-between gap-2">
                <p className="font-semibold text-[var(--color-text-primary)]">{plan?.name || "Subscription"}</p>
                <Badge tone="success">{sub.remaining_service_count} left</Badge>
              </div>
              <p className="mt-1.5 flex items-center gap-1 text-xs text-[var(--color-text-secondary)]">
                <Car className="h-3 w-3" /> {coversLabel}
              </p>
              <p className="mt-1 text-xs text-[var(--color-text-secondary)]">Valid until {format(sub.end_date)}</p>
              <Button size="sm" className="mt-3 w-full">
                <Gift className="h-3.5 w-3.5" /> Book now
              </Button>
            </Card>
          );
        })}
      </div>

      {openSub && (
        <QuickBookModal
          sub={openSub}
          plan={plans?.find((p) => p.id === openSub.plan_id)}
          vehicles={vehicles}
          addresses={addresses}
          services={services}
          vehicleTypes={vehicleTypes}
          onClose={() => setOpenSub(null)}
          onBooked={(booking) => {
            queryClient.invalidateQueries({ queryKey: ["my-bookings"] });
            queryClient.invalidateQueries({ queryKey: ["my-subscriptions"] });
            // Same confirmation page NewBookingPage sends to — a booking is
            // a booking whether paid for directly or spent from a
            // subscription, so it gets the same "purchase completed" URL.
            navigate(`/thank-you?token=${booking.confirmation_token}`);
          }}
        />
      )}
    </div>
  );
}

function QuickBookModal({
  sub,
  plan,
  vehicles,
  addresses,
  services,
  vehicleTypes,
  onClose,
  onBooked,
}: {
  sub: UserSubscription;
  plan: SubscriptionPlan | undefined;
  vehicles: Vehicle[] | undefined;
  addresses: Address[] | undefined;
  services: Service[];
  vehicleTypes: VehicleTypeOption[] | undefined;
  onClose: () => void;
  onBooked: (booking: Booking) => void;
}) {
  // Vehicles this subscription can actually serve: the plan's covered
  // type(s) AND the purchased tier (that type or cheaper — never bigger).
  const eligibleVehicles = (vehicles || []).filter((v) => subscriptionCoversType(sub, plan, v.vehicle_type));
  const [vehicleId, setVehicleId] = useState<string | null>(eligibleVehicles.find((v) => v.is_default)?.id || eligibleVehicles[0]?.id || null);
  const vehicle = eligibleVehicles.find((v) => v.id === vehicleId);
  const vehicleTypeId = vehicle?.vehicle_type || "";
  const bikeIds = useMemo(() => bikeTypeIds(vehicleTypes), [vehicleTypes]);
  const bookingIsBike = bikeIds.has(vehicleTypeId);
  const priceOf = (s: Service) => s.vehicle_type_prices?.[vehicleTypeId] ?? s.price;

  // What this redemption covers is fixed by the plan, not chosen here —
  // admin decided that when the plan was created. Older/unrestricted plans
  // (no included_service_ids, sold before this existed) fall back to the
  // original pick-a-main-service behavior for backward compatibility.
  const includedServices = services.filter((s) => plan?.included_service_ids?.includes(s.id));
  const isFixedPlan = !!plan?.included_service_ids?.length;
  const eligibleCategories = sub.remaining_by_category && Object.keys(sub.remaining_by_category).length
    ? new Set(Object.entries(sub.remaining_by_category).filter(([, v]) => v > 0).map(([k]) => k))
    : null;

  // Only MAIN services for this vehicle's class can stand in for the plan's
  // service — variant groups collapsed, add-ons never listed here (they're
  // extras below, not the covered wash). Same rules as the booking wizard.
  const groups = useMemo(() => baseGroups(services, vehicleTypeId), [services, vehicleTypeId]);
  const swapGroups = groups.filter((g) => !g.variants.some((v) => plan?.included_service_ids?.includes(v.id)));
  const pickableGroups = !isFixedPlan
    ? groups.filter((g) => !eligibleCategories || eligibleCategories.has(g.primary.category_id))
    : [];

  // Client-side estimate of the top-up (BookingService._subscription_discount
  // is the source of truth): a swap pays only the gap above the cheapest
  // service the plan includes; a cheaper swap is covered but still burns
  // one full visit — no credit. First-time pricing is ignored (a
  // subscriber is essentially never first-time), so it's an estimate.
  const baselinePrice = includedServices.length ? Math.min(...includedServices.map(priceOf)) : 0;

  const [pickedServiceId, setPickedServiceId] = useState<string | null>(null);
  const [swapOpen, setSwapOpen] = useState(false);
  const [swappedServiceId, setSwappedServiceId] = useState<string | null>(null);

  // Add-ons — ALWAYS paid extra on top of the plan (₹60 per extra bike,
  // polish per bike, ...): a plan visit covers exactly its main service.
  const kit = useMemo(() => addonKit(services, vehicleTypeId, bikeIds), [services, vehicleTypeId, bikeIds]);
  const [simpleAddonIds, setSimpleAddonIds] = useState<string[]>([]);
  const [extraBikes, setExtraBikes] = useState(0);
  const [polishCount, setPolishCount] = useState(0);

  // Vehicle switch can change the class (car ↔ bike) — every service pick
  // below is class-dependent, so start clean.
  useEffect(() => {
    setSwappedServiceId(null);
    setPickedServiceId(null);
    setSimpleAddonIds([]);
    setExtraBikes(0);
    setPolishCount(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicleTypeId]);

  // Polish is per bike — never more polishes than bikes in the visit.
  const bikesInBooking = bookingIsBike ? 1 + extraBikes : extraBikes;
  useEffect(() => {
    if (polishCount > bikesInBooking) setPolishCount(bikesInBooking);
  }, [bikesInBooking, polishCount]);

  const [date, setDate] = useState("");
  const [slot, setSlot] = useState("");
  const [addressId, setAddressId] = useState<string | null>(addresses?.find((a) => a.is_default)?.id || addresses?.[0]?.id || null);
  const [showNewAddress, setShowNewAddress] = useState(!addresses?.length);
  const [addressLine, setAddressLine] = useState("");
  const [city, setCity] = useState("");
  const [state, setState] = useState("");
  const [pincode, setPincode] = useState("");
  const [altContactName, setAltContactName] = useState("");
  const [altContactPhone, setAltContactPhone] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState("");
  const [verifyOpen, setVerifyOpen] = useState(false);
  const { user } = useAuth();

  // Slots are generated per service center, resolved from whichever
  // address (saved or newly entered) is currently picked — same
  // pincode-lookup approach as the main booking wizard.
  const resolvedPincode = showNewAddress ? pincode : addresses?.find((a) => a.id === addressId)?.pincode || "";
  const { data: matchedCenters } = useQuery({
    queryKey: ["center-lookup", resolvedPincode],
    queryFn: () => serviceCenterApi.lookupByPincode(resolvedPincode),
    enabled: resolvedPincode.length >= 6,
  });
  const serviceCenterId = matchedCenters?.[0]?.id;

  const baseServiceIds = !isFixedPlan
    ? (pickedServiceId ? [pickedServiceId] : [])
    : swappedServiceId
      ? [swappedServiceId]
      : includedServices.map((s) => s.id);
  const addonIds = [
    ...simpleAddonIds,
    ...(extraBikes > 0 && kit.addBike ? [kit.addBike.id] : []),
    ...(polishCount > 0 && kit.bikePolish ? [kit.bikePolish.id] : []),
  ];
  const serviceIds = [...baseServiceIds, ...addonIds];
  const serviceQuantities: Record<string, number> = {};
  if (extraBikes > 1 && kit.addBike) serviceQuantities[kit.addBike.id] = extraBikes;
  if (polishCount > 1 && kit.bikePolish) serviceQuantities[kit.bikePolish.id] = polishCount;

  const swapExtra = isFixedPlan && swappedServiceId
    ? Math.max(0, priceOf(services.find((s) => s.id === swappedServiceId)!) - baselinePrice)
    : 0;
  const addonsExtra =
    simpleAddonIds.reduce((sum, id) => {
      const s = services.find((x) => x.id === id);
      return sum + (s ? priceOf(s) : 0);
    }, 0) +
    (extraBikes > 0 && kit.addBike ? priceOf(kit.addBike) * extraBikes : 0) +
    (polishCount > 0 && kit.bikePolish ? priceOf(kit.bikePolish) * polishCount : 0);
  const estimatedExtra = swapExtra + addonsExtra;
  const canSubmit = !!vehicleId && !!date && !!slot && baseServiceIds.length > 0 && (showNewAddress ? !!addressLine && !!city && !!state && !!pincode : !!addressId);

  const chip = (selected: boolean) =>
    `rounded-full border px-3.5 py-2 text-sm font-medium transition-colors ${
      selected ? "border-black bg-[#FFF4CD] text-black" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
    }`;

  const mutation = useMutation({
    mutationFn: async () => {
      let resolvedAddressId = addressId;
      if (showNewAddress || !resolvedAddressId) {
        const created = await addressApi.create({
          label: "Doorstep",
          line1: addressLine,
          city,
          state,
          pincode,
          is_default: !addresses?.length,
        });
        resolvedAddressId = created.id;
      }
      return bookingApi.create({
        vehicle_id: vehicleId!,
        address_id: resolvedAddressId!,
        service_ids: serviceIds,
        service_quantities: Object.keys(serviceQuantities).length ? serviceQuantities : undefined,
        scheduled_date: date,
        scheduled_slot: slot,
        subscription_id: sub.id,
        customer_notes: notes || undefined,
        alternate_contact_name: altContactName || undefined,
        alternate_contact_phone: altContactPhone || undefined,
      });
    },
    onSuccess: onBooked,
    onError: (err) => setError(getErrorMessage(err)),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-gray-900/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative z-10 max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl bg-white p-6 shadow-[var(--shadow-lifted)]">
        <p className="text-lg font-semibold text-[var(--color-text-primary)]">Quick book — {plan?.name || "Subscription"}</p>
        <div className="mt-2 rounded-lg border border-[#F3E5B5] bg-[#FAFAFA] p-3 text-xs text-[var(--color-text-secondary)]">
          <p>{sub.remaining_service_count} of {sub.total_service_count} visits left · valid until {format(sub.end_date)}</p>
          {isFixedPlan && !!includedServices.length && <p className="mt-0.5">Covers: {includedServices.map((s) => s.name).join(", ")}</p>}
        </div>

        <div className="mt-4 space-y-4">
          <div>
            <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Which vehicle?</p>
            {!eligibleVehicles.length ? (
              <p className="text-xs text-[var(--color-error)]">
                {sub.vehicle_type
                  ? "None of your saved vehicles fit this plan — it works for the vehicle type it was bought for, or smaller."
                  : "None of your saved vehicles match this plan's covered type — add one first."}
              </p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {eligibleVehicles.map((v) => (
                  <button key={v.id} type="button" onClick={() => setVehicleId(v.id)} className={chip(vehicleId === v.id)}>
                    {v.brand} {v.model} · {v.registration_number}
                  </button>
                ))}
              </div>
            )}
          </div>

          {!isFixedPlan && (
            <div>
              <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Which service this time?</p>
              {!pickableGroups.length ? (
                <p className="text-xs text-[var(--color-error)]">No services are covered by this plan's remaining quota.</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {pickableGroups.map((g) => (
                    <button key={g.key} type="button" onClick={() => setPickedServiceId(g.primary.id)} className={chip(pickedServiceId === g.primary.id)}>
                      {g.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {isFixedPlan && !!swapGroups.length && (
            <div>
              {!swapOpen ? (
                <button type="button" className="text-sm font-medium text-[var(--color-primary)] underline" onClick={() => setSwapOpen(true)}>
                  Want a different service this visit?
                </button>
              ) : (
                <div>
                  <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Swap this visit for a different service</p>
                  <p className="mb-2 text-xs text-[var(--color-text-secondary)]">
                    A costlier service just costs the difference. A cheaper one is covered — it still uses one full visit, with no credit back.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <button type="button" onClick={() => setSwappedServiceId(null)} className={chip(!swappedServiceId)}>
                      Keep what's included
                    </button>
                    {swapGroups.map((g) => {
                      const extra = Math.max(0, priceOf(g.primary) - baselinePrice);
                      return (
                        <button key={g.key} type="button" onClick={() => setSwappedServiceId(g.primary.id)} className={chip(swappedServiceId === g.primary.id)}>
                          {g.label} {extra > 0 ? `· +₹${extra}` : "· covered"}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}

          {(!!kit.simple.length || !!kit.addBike || (!!kit.bikePolish && bikesInBooking > 0)) && (
            <div>
              <p className="mb-1 text-sm font-medium text-[var(--color-text-primary)]">Add-ons</p>
              <p className="mb-2 text-xs text-[var(--color-text-secondary)]">Paid extra — add-ons aren't covered by your plan.</p>
              <div className="space-y-2">
                {kit.simple.map((s) => {
                  const on = simpleAddonIds.includes(s.id);
                  return (
                    <button
                      key={s.id}
                      type="button"
                      onClick={() => setSimpleAddonIds((ids) => (on ? ids.filter((i) => i !== s.id) : [...ids, s.id]))}
                      className={`flex w-full items-center justify-between rounded-xl border px-3.5 py-2.5 text-sm transition-colors ${
                        on ? "border-black bg-[#FFF4CD] text-black" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
                      }`}
                    >
                      <span className="font-medium">{s.name}</span>
                      <span className="font-mono-num">+₹{priceOf(s)}</span>
                    </button>
                  );
                })}
                {kit.addBike && (
                  <div className="flex items-center justify-between rounded-xl border border-gray-200 px-3.5 py-2.5 text-sm">
                    <div>
                      <p className="font-medium text-[var(--color-text-primary)]">{bookingIsBike ? "Extra bikes" : "Add bike wash"}</p>
                      <p className="text-xs text-[var(--color-text-secondary)]">₹{priceOf(kit.addBike)} per bike</p>
                    </div>
                    <QtyStepper value={extraBikes} min={0} max={10} onChange={setExtraBikes} />
                  </div>
                )}
                {kit.bikePolish && bikesInBooking > 0 && (
                  <div className="flex items-center justify-between rounded-xl border border-gray-200 px-3.5 py-2.5 text-sm">
                    <div>
                      <p className="font-medium text-[var(--color-text-primary)]">{kit.bikePolish.name}</p>
                      <p className="text-xs text-[var(--color-text-secondary)]">₹{priceOf(kit.bikePolish)} per bike · up to {bikesInBooking}</p>
                    </div>
                    <QtyStepper value={polishCount} min={0} max={bikesInBooking} onChange={setPolishCount} />
                  </div>
                )}
              </div>
            </div>
          )}

          <div className="rounded-lg border border-[#F3E5B5] bg-[#FAFAFA] p-3 text-sm">
            {estimatedExtra > 0 ? (
              <div className="space-y-1">
                {swapExtra > 0 && (
                  <div className="flex justify-between text-xs text-[var(--color-text-secondary)]">
                    <span>Service swap</span>
                    <span className="font-mono-num">+₹{swapExtra}</span>
                  </div>
                )}
                {addonsExtra > 0 && (
                  <div className="flex justify-between text-xs text-[var(--color-text-secondary)]">
                    <span>Add-ons</span>
                    <span className="font-mono-num">+₹{addonsExtra}</span>
                  </div>
                )}
                <div className="flex justify-between font-semibold text-[var(--color-text-primary)]">
                  <span>To pay (estimated)</span>
                  <span className="font-mono-num">₹{estimatedExtra}</span>
                </div>
                <p className="text-[11px] text-[var(--color-text-secondary)]">1 visit from your plan + the amount above, confirmed on the booking.</p>
              </div>
            ) : (
              <p className="font-medium text-[var(--color-success)]">Fully covered — this visit uses 1 plan credit, nothing to pay.</p>
            )}
          </div>

          <AddressPicker
            addresses={addresses}
            selectedId={addressId}
            showingNewForm={showNewAddress}
            onSelect={(a) => {
              setAddressId(a.id);
              setShowNewAddress(false);
            }}
            onAddNew={() => {
              setShowNewAddress(true);
              setAddressId(null);
            }}
          />

          {showNewAddress && (
            <div className="space-y-3 rounded-lg border border-dashed border-gray-200 p-3">
              <Input placeholder="Address / Location" value={addressLine} onChange={(e) => setAddressLine(e.target.value)} required />
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <Input placeholder="City" value={city} onChange={(e) => setCity(e.target.value)} required />
                <Input placeholder="State" value={state} onChange={(e) => setState(e.target.value)} required />
                <Input placeholder="Pincode" value={pincode} onChange={(e) => setPincode(e.target.value)} required />
              </div>
            </div>
          )}

          {resolvedPincode.length >= 6 && !serviceCenterId ? (
            <p className="text-sm text-[var(--color-error)]">Doorstep service isn't available in this area yet.</p>
          ) : (
            <SlotPicker serviceCenterId={serviceCenterId} date={date} onDateChange={setDate} value={slot} onChange={setSlot} />
          )}

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Input placeholder="Secondary contact name (Optional)" value={altContactName} onChange={(e) => setAltContactName(e.target.value)} />
            <Input placeholder="Secondary contact phone (Optional)" value={altContactPhone} onChange={(e) => setAltContactPhone(e.target.value)} />
          </div>

          <Input placeholder="Notes (Optional)" value={notes} onChange={(e) => setNotes(e.target.value)} />

          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}

          <div className="flex gap-3">
            <Button type="button" variant="outline" className="flex-1" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="button"
              className="flex-1"
              disabled={!canSubmit}
              isLoading={mutation.isPending}
              onClick={() => {
                // Same client-side pre-check as the main booking wizard —
                // this mutation can also create a new address before the
                // booking call, so the gate is checked BEFORE that starts
                // rather than caught reactively after a partial success.
                if (!user?.phone_verified) {
                  setVerifyOpen(true);
                  return;
                }
                mutation.mutate();
              }}
            >
              Confirm booking
            </Button>
          </div>
        </div>
      </div>
      <PhoneVerificationModal open={verifyOpen} onClose={() => setVerifyOpen(false)} onVerified={() => { setVerifyOpen(false); mutation.mutate(); }} />
    </div>
  );
}
