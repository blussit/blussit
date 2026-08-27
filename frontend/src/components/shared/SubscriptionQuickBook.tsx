import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Car, Gift, Zap } from "lucide-react";
import { addressApi } from "../../api/profile";
import { serviceCenterApi } from "../../api/catalog";
import { bookingApi } from "../../api/booking";
import { Badge, Button, Card, Input } from "../ui";
import { AddressPicker } from "./AddressPicker";
import { PhoneVerificationModal } from "./PhoneVerificationModal";
import { SlotPicker } from "./SlotPicker";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";
import { format } from "../../lib/date";
import type { Address, Booking, Service, SubscriptionPlan, UserSubscription, Vehicle } from "../../types";

/**
 * The one-tap path for a customer who just wants to spend a subscription
 * they already own — no service catalog, no vehicle picking, no pricing
 * screen, because a subscription already answers all of that up front. All
 * that's genuinely still needed per visit is *when* and *where*.
 *
 * Renders as a row of cards (one per usable subscription); tapping one
 * opens a small modal collecting only date/time + address (+ optional
 * secondary contact), then books directly. Self-contained — owns its own
 * mutation and navigation, callers just drop it in and pass data they
 * already have loaded.
 */
export function SubscriptionQuickBook({
  subscriptions,
  plans,
  vehicles,
  addresses,
  services,
}: {
  subscriptions: UserSubscription[];
  plans: SubscriptionPlan[] | undefined;
  vehicles: Vehicle[] | undefined;
  addresses: Address[] | undefined;
  services: Service[];
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [openSub, setOpenSub] = useState<UserSubscription | null>(null);

  if (!subscriptions.length) return null;

  return (
    <div>
      <p className="mb-3 flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-[var(--color-text-primary)]">
        <Zap className="h-4 w-4 text-[var(--color-success)]" /> Book from a subscription
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {subscriptions.map((sub) => {
          const plan = plans?.find((p) => p.id === sub.plan_id);
          const coversLabel = plan?.vehicle_types?.length ? `Covers ${plan.vehicle_types.length} vehicle type(s)` : "Covers any vehicle type";
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
  onClose,
  onBooked,
}: {
  sub: UserSubscription;
  plan: SubscriptionPlan | undefined;
  vehicles: Vehicle[] | undefined;
  addresses: Address[] | undefined;
  services: Service[];
  onClose: () => void;
  onBooked: (booking: Booking) => void;
}) {
  // Subscriptions aren't locked to one vehicle anymore — any owned vehicle
  // matching the plan's covered type(s) qualifies (see
  // UserSubscriptionModel.vehicle_id's docstring). Empty vehicle_types on
  // the plan means every type is eligible.
  const eligibleVehicles = (vehicles || []).filter((v) => !plan?.vehicle_types?.length || plan.vehicle_types.includes(v.vehicle_type));
  const [vehicleId, setVehicleId] = useState<string | null>(eligibleVehicles.find((v) => v.is_default)?.id || eligibleVehicles[0]?.id || null);
  const vehicle = eligibleVehicles.find((v) => v.id === vehicleId);

  // What this redemption covers is fixed by the plan, not chosen here —
  // admin decided that when the plan was created. Older/unrestricted plans
  // (no included_service_ids, sold before this existed) fall back to the
  // original free-pick-anything behavior for backward compatibility.
  const includedServices = services.filter((s) => plan?.included_service_ids?.includes(s.id));
  const isFixedPlan = !!plan?.included_service_ids?.length;
  const eligibleCategories = sub.remaining_by_category && Object.keys(sub.remaining_by_category).length
    ? new Set(Object.entries(sub.remaining_by_category).filter(([, v]) => v > 0).map(([k]) => k))
    : null;
  const pickableServices = !isFixedPlan ? services.filter((s) => !eligibleCategories || eligibleCategories.has(s.category_id)) : [];

  // Rough client-side estimate of what the backend will actually charge for
  // a swap (BookingService._subscription_discount is the source of truth):
  // pay only the gap between the alternate service and the cheapest service
  // this plan already includes — not its full price. Doesn't account for
  // first-time pricing (a subscriber is essentially never first-time
  // anyway), so treat this as an estimate, not the final total.
  const vehicleTypeId = vehicle?.vehicle_type || "";
  const priceOf = (s: Service) => s.vehicle_type_prices?.[vehicleTypeId] ?? s.price;
  const baselinePrice = includedServices.length ? Math.min(...includedServices.map(priceOf)) : 0;
  const swapCandidates = services.filter((s) => !plan?.included_service_ids?.includes(s.id));

  const [pickedServiceId, setPickedServiceId] = useState<string | null>(null);
  const [swapOpen, setSwapOpen] = useState(false);
  const [swappedServiceId, setSwappedServiceId] = useState<string | null>(null);
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

  const serviceIds = !isFixedPlan
    ? (pickedServiceId ? [pickedServiceId] : [])
    : swappedServiceId
      ? [swappedServiceId]
      : includedServices.map((s) => s.id);
  const estimatedExtra = isFixedPlan && swappedServiceId ? Math.max(0, priceOf(services.find((s) => s.id === swappedServiceId)!) - baselinePrice) : 0;
  const canSubmit = !!vehicleId && !!date && !!slot && serviceIds.length > 0 && (showNewAddress ? !!addressLine && !!city && !!state && !!pincode : !!addressId);

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
        <div className="mt-2 rounded-lg bg-[var(--color-surface)] p-3 text-xs text-[var(--color-text-secondary)]">
          <p>{sub.remaining_service_count} of {sub.total_service_count} visits left · valid until {format(sub.end_date)}</p>
          {isFixedPlan && !!includedServices.length && <p className="mt-0.5">Covers: {includedServices.map((s) => s.name).join(", ")}</p>}
        </div>

        <div className="mt-4 space-y-4">
          <div>
            <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Which vehicle?</p>
            {!eligibleVehicles.length ? (
              <p className="text-xs text-[var(--color-error)]">None of your saved vehicles match this plan's covered type — add one first.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {eligibleVehicles.map((v) => (
                  <button
                    key={v.id}
                    type="button"
                    onClick={() => setVehicleId(v.id)}
                    className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors ${
                      vehicleId === v.id ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
                    }`}
                  >
                    {v.brand} {v.model} · {v.registration_number}
                  </button>
                ))}
              </div>
            )}
          </div>

          {!isFixedPlan && (
            <div>
              <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Which service this time?</p>
              {!pickableServices.length ? (
                <p className="text-xs text-[var(--color-error)]">No services are covered by this plan's remaining quota.</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {pickableServices.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      onClick={() => setPickedServiceId(s.id)}
                      className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors ${
                        pickedServiceId === s.id ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
                      }`}
                    >
                      {s.name}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {isFixedPlan && !!swapCandidates.length && (
            <div>
              {!swapOpen ? (
                <button type="button" className="text-sm font-medium text-[var(--color-primary)] underline" onClick={() => setSwapOpen(true)}>
                  Want something else instead?
                </button>
              ) : (
                <div>
                  <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Swap this visit for a different service</p>
                  <p className="mb-2 text-xs text-[var(--color-text-secondary)]">You pay just the difference — the rest is still covered by your plan.</p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => setSwappedServiceId(null)}
                      className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors ${
                        !swappedServiceId ? "border-[var(--color-success)] bg-[var(--color-success)] text-white" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
                      }`}
                    >
                      Keep what's included
                    </button>
                    {swapCandidates.map((s) => {
                      const extra = Math.max(0, priceOf(s) - baselinePrice);
                      return (
                        <button
                          key={s.id}
                          type="button"
                          onClick={() => setSwappedServiceId(s.id)}
                          className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors ${
                            swappedServiceId === s.id ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
                          }`}
                        >
                          {s.name} {extra > 0 ? `· +₹${extra}` : "· free"}
                        </button>
                      );
                    })}
                  </div>
                  {swappedServiceId && (
                    <p className="mt-1.5 text-xs text-[var(--color-text-secondary)]">
                      Estimated top-up: ₹{estimatedExtra} (final amount is confirmed on the booking).
                    </p>
                  )}
                </div>
              )}
            </div>
          )}

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
