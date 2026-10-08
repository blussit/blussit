/**
 * "Book A Plan Wash" for a CAR-BOUND pass — a custom-plan car (spec
 * 2026-10-07 §1.6) or a monthly pass bought for one car: pick the car, a
 * service it still has washes of, optional paid extras (a bike pass: how
 * many bikes — the booking page's own counter), a saved address or a new
 * one pinned on the map, the date & time (inside the pass) — then the
 * server's quote (the covered service at ₹0, extras paid) and book.
 *
 * The line names the car and the pass explicitly (vehicle_id +
 * subscription_id, quantity 1); extra quantities ride on that line's
 * `service_quantities` (extra bikes, bike polish per bike). The server never auto-applies a car-bound
 * pass (_usable_passes), so the normal booking page — which books by car
 * TYPE — can't use one.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ShieldCheck } from "lucide-react";
import { bookingApi, type QuickBookingLine } from "../../api/booking";
import { catalogApi, getSlotHolderKey, vehicleTypeApi } from "../../api/catalog";
import { addressApi } from "../../api/profile";
import type { CustomPlanCar, CustomPlanCart } from "../../api/customPlansMe";
import { MY_CUSTOM_PLANS_QUERY_KEY } from "../../api/customPlansMe";
import type { UserSubscription } from "../../types";
import { Modal, Select } from "../ui";
import { SlotPicker } from "../shared/SlotPicker";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { getErrorCode, getErrorMessage } from "../../lib/api-client";
import { format, formatShortDate, todayIST } from "../../lib/date";
import { addonKit, bikeCountRange, bikeTypeIds, composeBikeLine, offeredAddons } from "../../lib/serviceMix";
import { toTitle } from "../../lib/titleCase";
import { cn } from "../../lib/cn";
import type { Service } from "../../types";
import { btn } from "./ui";
import type { WalletQuoteLines } from "./money";
import { QtyStepper } from "../shared/QtyStepper";
import { lastBookingDay } from "./passDates";
import { CoverageNote, NewAddressPicker, useAddressCoverage, type NewAddress } from "./NewAddressPicker";

/** The address select's "Add New Address" choice. */
const NEW_ADDRESS = "__new__";

const chip = (on: boolean) =>
  cn(
    "min-h-[44px] rounded-xl border px-3.5 py-2 text-left text-sm transition-colors",
    on ? "border-[#0E1A33] bg-[#E8F0FE] font-semibold text-[#0E1A33]" : "border-[#E4E9F1] bg-white text-[#0E1A33] hover:border-[#CFDCF0]"
  );
const priceOf = (s: Service, typeId: string) => Math.round(s.vehicle_type_prices?.[typeId] ?? s.price ?? 0);

/** One car and its pass, whatever kind of plan it came from. */
export interface PlanWashCar {
  vehicle_id: string;
  registration_number?: string | null;
  vehicle_type: string;
  vehicle_type_name?: string | null;
  subscription_id: string;
  start_date?: string | null;
  last_bookable_day?: string | null;
  /** "18 Oct 2026" — shown as "Last Booking Day: 18 Oct 2026". */
  last_booking_day_label?: string | null;
  items: { service_id: string; service_name?: string | null; count: number; remaining: number }[];
}

/** A custom-plan car whose pass can book now: active (not a `scheduled`
 *  renewal waiting for its start, not refunded), washes left. */
export const bookableCar = (car: CustomPlanCar) =>
  !!car.vehicle_id &&
  car.status !== "refunded" &&
  !!car.subscription?.id &&
  car.subscription.status === "active" &&
  car.items.some((i) => i.remaining > 0);

export const carsFromCart = (cart: CustomPlanCart): PlanWashCar[] =>
  cart.cars.filter(bookableCar).map((c) => ({
    vehicle_id: c.vehicle_id!,
    registration_number: c.registration_number,
    vehicle_type: c.vehicle_type || "",
    vehicle_type_name: c.vehicle_type_name,
    subscription_id: c.subscription!.id,
    start_date: c.subscription!.start_date,
    last_bookable_day: c.subscription!.last_bookable_day,
    last_booking_day_label: c.subscription!.last_booking_day_label,
    items: c.items.map((i) => ({ service_id: i.service_id, service_name: i.service_name, count: i.count, remaining: i.remaining })),
  }));

/** A monthly pass bought for one car (vehicle_id + service_id) — not a society pass. */
export const isCarBoundPass = (sub: UserSubscription) => !!sub.vehicle_id && !!sub.service_id && !sub.society_id;

export const carFromPass = (sub: UserSubscription, typeName?: string | null, serviceName?: string | null): PlanWashCar => ({
  vehicle_id: sub.vehicle_id!,
  registration_number: sub.registration_number,
  vehicle_type: sub.vehicle_type || "",
  vehicle_type_name: typeName,
  subscription_id: sub.id,
  start_date: sub.start_date,
  last_bookable_day: sub.last_bookable_day,
  last_booking_day_label: sub.last_booking_day_label,
  items: [{ service_id: sub.service_id!, service_name: serviceName, count: sub.total_service_count, remaining: sub.remaining_service_count ?? 0 }],
});

export function PlanWashSheet({
  cars: allCars,
  serviceCenterId,
  open,
  onClose,
}: {
  cars: PlanWashCar[];
  /** Slots fall back to this center until the address names its own. */
  serviceCenterId?: string | null;
  open: boolean;
  onClose: () => void;
}) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();
  const cars = allCars.filter((c) => c.vehicle_type && c.items.some((i) => i.remaining > 0));

  const [carId, setCarId] = useState("");
  const [serviceId, setServiceId] = useState("");
  const [addons, setAddons] = useState<string[]>([]);
  const [bikes, setBikes] = useState(1);
  const [newAddress, setNewAddress] = useState<NewAddress | null>(null);
  const [date, setDate] = useState("");
  const [slot, setSlot] = useState("");
  const [addressId, setAddressId] = useState("");
  const [payMethod, setPayMethod] = useState<"cash" | "online">("online");
  const [error, setError] = useState("");
  const [confirmTotal, setConfirmTotal] = useState<number | null>(null);

  const { data: servicesPage } = useQuery({ queryKey: ["public-services"], queryFn: () => catalogApi.services({ page_size: 100 }), staleTime: 60_000, enabled: open });
  const services = useMemo(() => (servicesPage?.data || []).filter((s) => s.is_active !== false), [servicesPage]);
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list(), staleTime: 30 * 60_000, enabled: open });
  const bikeIds = useMemo(() => bikeTypeIds(vehicleTypes), [vehicleTypes]);
  const { data: addresses } = useQuery({ queryKey: ["addresses"], queryFn: addressApi.list, staleTime: 60_000, enabled: open });

  useEffect(() => {
    if (!open) return;
    const first = cars[0];
    setCarId(first?.vehicle_id || "");
    setServiceId(first?.items.find((i) => i.remaining > 0)?.service_id || "");
    setAddons([]);
    setBikes(0);
    // Tomorrow by default (today's slots are often gone), inside the pass.
    const tomorrow = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date(Date.now() + 86_400_000));
    const last = first?.last_bookable_day || "";
    setDate(!last || tomorrow <= last ? tomorrow : todayIST());
    setSlot("");
    setError("");
    setConfirmTotal(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  useEffect(() => {
    if (open && !addressId && addresses) setAddressId(addresses.length ? (addresses.find((a) => a.is_default) || addresses[0]).id : NEW_ADDRESS);
  }, [open, addresses, addressId]);

  const car = cars.find((c) => c.vehicle_id === carId);
  const typeId = car?.vehicle_type || "";
  // Slots of the center that serves the chosen address (as the booking page
  // does) — re-checked for a new pin.
  const isNew = addressId === NEW_ADDRESS;
  const address = isNew ? undefined : (addresses || []).find((a) => a.id === addressId);
  const probe = isNew ? newAddress : address ? { latitude: address.latitude, longitude: address.longitude, pincode: address.pincode } : null;
  const coverage = useAddressCoverage(probe, open);
  const notCovered = coverage.data ? !coverage.data.covered : false;
  const addressReady = isNew ? !!newAddress && !!coverage.data?.covered : !!address;
  const slotCenter = coverage.data?.center?.id || serviceCenterId || "";
  // The booking page's own add-on chips for this car type.
  const extras = typeId ? offeredAddons(services, typeId, bikeIds) : [];
  // The pass's own window: from its start (or today) to its last bookable day.
  const firstDay = [todayIST(), (car?.start_date || "").slice(0, 10)].filter(Boolean).sort().pop() || todayIST();
  const lastDay = car?.last_bookable_day || "";
  const lastDayLabel = car ? lastBookingDay(car) : "";
  const dateOutside = !!date && (date < firstDay || (!!lastDay && date > lastDay));

  // A bike pass: the booking page's bike counter — the pass's own wash
  // stays the base (covered), extra bikes ride as the add-a-bike line and
  // bike polish is per bike. A car line has no per-unit extras.
  const isBike = !!typeId && bikeIds.has(typeId);
  const passService = services.find((s) => s.id === serviceId) || null;
  const kit = typeId ? addonKit(services, typeId, bikeIds) : null;
  const bikeRange = isBike && kit && passService ? bikeCountRange([passService], kit, passService) : null;
  const bikeCount = bikeRange ? Math.min(bikeRange.max, Math.max(bikeRange.min, bikes)) : 1;
  const mix =
    isBike && kit && passService
      ? composeBikeLine({ variants: [passService], count: bikeCount, kit, addonIds: addons, fixedBase: passService })
      : { serviceIds: serviceId ? [serviceId, ...addons] : [], quantities: {} as Record<string, number> };
  const line: QuickBookingLine | null =
    car && serviceId && typeId
      ? {
          vehicle_type: typeId,
          quantity: 1,
          service_ids: mix.serviceIds,
          ...(Object.keys(mix.quantities).length ? { service_quantities: mix.quantities } : {}),
          vehicle_id: car.vehicle_id,
          subscription_id: car.subscription_id,
        }
      : null;
  // The paid extras on the line, × quantity (the base is the plan's).
  const extraRows = mix.serviceIds
    .filter((id) => id !== serviceId)
    .map((id) => ({ service: services.find((x) => x.id === id), qty: mix.quantities[id] || 1 }))
    .filter((r): r is { service: Service; qty: number } => !!r.service);
  const addressBody = isNew
    ? newAddress && coverage.data?.covered
      ? { address: { latitude: newAddress.latitude, longitude: newAddress.longitude, pincode: newAddress.pincode } }
      : null
    : address
      ? { address_id: address.id }
      : null;
  const quoteBody = line && addressBody ? { lines: [line], ...addressBody, ...(date ? { scheduled_date: date } : {}) } : null;
  const quoteQuery = useQuery({
    queryKey: ["plan-wash-quote", JSON.stringify(quoteBody)],
    queryFn: () => bookingApi.quote(quoteBody!),
    enabled: open && !!quoteBody,
    staleTime: 15_000,
    retry: false,
  });
  const quote = quoteQuery.data as (typeof quoteQuery.data & WalletQuoteLines) | undefined;
  const toPay = quote ? Math.max(0, quote.amount_payable ?? quote.total_amount) : null;
  const onlineOnly = !!quote?.online_only;
  const method: "cash" | "online" = toPay && toPay > 0 ? (onlineOnly ? "online" : payMethod) : "cash";
  const quoteError = quoteQuery.isError ? getErrorMessage(quoteQuery.error) : "";

  const book = useMutation({
    mutationFn: () =>
      bookingApi.quick({
        customer_name: user?.full_name || "Customer",
        customer_phone: user?.phone || "",
        lines: [line!],
        ...(isNew && newAddress
          ? { address: { ...newAddress, city: newAddress.city || coverage.data?.center?.city || undefined, state: newAddress.state || coverage.data?.center?.state || undefined } }
          : { address_id: addressId }),
        scheduled_date: date,
        scheduled_slot: slot,
        payment_method: method,
        hold_key: getSlotHolderKey(),
        expected_total: confirmTotal ?? quote!.total_amount,
      }),
    onMutate: () => setError(""),
    onSuccess: (result) => {
      for (const key of [["my-subscriptions"], MY_CUSTOM_PLANS_QUERY_KEY, ["my-bookings"], ["my-wallet"], ["available-slots"]]) {
        void queryClient.invalidateQueries({ queryKey: key as readonly unknown[] });
      }
      onClose();
      toast.push({ tone: "success", title: "Plan Wash Booked", message: `${result.booking_numbers.join(" + ")} · ${format(date)}` });
      const first = result.bookings[0];
      if (first) navigate(`/app/bookings/${first.id}`);
    },
    onError: (err) => {
      if (getErrorCode(err) === "PRICE_CHANGED") {
        const details = ((err as { response?: { data?: { details?: Record<string, unknown> } } }).response?.data?.details || {}) as Record<string, unknown>;
        if (typeof details.total_amount === "number") {
          setConfirmTotal(details.total_amount);
          setError(`The price is now ₹${Math.round(details.total_amount)}. Tap Book again to confirm.`);
          return;
        }
      }
      setError(getErrorMessage(err));
    },
  });

  const ready = !!line && addressReady && !notCovered && !!date && !!slot && !dateOutside && !!quote && !quoteError;
  const serviceName = (id: string) => toTitle(car?.items.find((i) => i.service_id === id)?.service_name || services.find((s) => s.id === id)?.name) || "Wash";

  return (
    <Modal open={open} onClose={onClose} title="Book A Plan Wash" maxWidth="max-w-xl">
      {!cars.length ? (
        <p className="text-sm text-[#5F6878]">No washes left to book on this plan.</p>
      ) : (
        <div className="space-y-5">
          <section>
            <h3 className="mb-2 text-sm font-semibold text-[#0E1A33]">Car</h3>
            <div className="grid gap-2 sm:grid-cols-2">
              {cars.map((c) => (
                <button
                  key={c.vehicle_id}
                  type="button"
                  aria-pressed={c.vehicle_id === carId}
                  className={chip(c.vehicle_id === carId)}
                  onClick={() => {
                    setCarId(c.vehicle_id);
                    setServiceId(c.items.find((i) => i.remaining > 0)?.service_id || "");
                    setAddons([]);
                    setBikes(0);
                    setConfirmTotal(null);
                  }}
                >
                  <span className="block tabular-nums">{c.registration_number || "Car"}</span>
                  <span className="block text-xs font-normal text-[#5F6878]">{toTitle(c.vehicle_type_name) || "Car"}</span>
                </button>
              ))}
            </div>
          </section>

          {car && (
            <section>
              <h3 className="mb-2 text-sm font-semibold text-[#0E1A33]">Wash From Your Plan</h3>
              <div className="grid gap-2 sm:grid-cols-2">
                {car.items.map((i) => {
                  const left = i.remaining > 0;
                  return (
                    <button
                      key={i.service_id}
                      type="button"
                      disabled={!left}
                      aria-pressed={i.service_id === serviceId}
                      className={cn(chip(i.service_id === serviceId), !left && "cursor-not-allowed opacity-50")}
                      onClick={() => {
                        setServiceId(i.service_id);
                        setBikes(0);
                        setConfirmTotal(null);
                      }}
                    >
                      <span className="block">{toTitle(i.service_name) || "Wash"}</span>
                      <span className="block text-xs font-normal text-[#5F6878]">
                        {i.remaining} Of {i.count} Left
                      </span>
                    </button>
                  );
                })}
              </div>
            </section>
          )}

          {car && bikeRange && bikeRange.max > bikeRange.min && (
            <section className="flex min-h-[56px] items-center justify-between gap-3 rounded-xl border border-[#E4E9F1] px-3.5 py-1.5" data-testid="plan-wash-bikes">
              <span className="text-sm text-[#0E1A33]">
                Bikes
                {kit?.addBike && priceOf(kit.addBike, typeId) > 0 && (
                  <span className="block text-xs text-[#5F6878]">+₹{priceOf(kit.addBike, typeId)} Per Extra Bike</span>
                )}
              </span>
              <QtyStepper
                size="lg"
                label="Bikes"
                value={bikeCount}
                min={bikeRange.min}
                max={bikeRange.max}
                onChange={(n) => {
                  setBikes(n);
                  setConfirmTotal(null);
                }}
              />
            </section>
          )}

          {car && extras.length > 0 && (
            <section>
              <h3 className="mb-2 text-sm font-semibold text-[#0E1A33]">
                Extras <span className="font-normal text-[#5F6878]">· Paid, Optional</span>
              </h3>
              <div className="flex flex-wrap gap-2">
                {extras.map((a) => {
                  const on = addons.includes(a.id);
                  return (
                    <button
                      key={a.id}
                      type="button"
                      aria-pressed={on}
                      className={cn(chip(on), "rounded-full")}
                      onClick={() => {
                        setAddons(on ? addons.filter((x) => x !== a.id) : [...addons, a.id]);
                        setConfirmTotal(null);
                      }}
                    >
                      {toTitle(a.name)}
                      {priceOf(a, typeId) > 0 && (
                        <span className="ml-1 font-normal text-[#5F6878]">
                          +₹{priceOf(a, typeId)}
                          {isBike && a.id === kit?.bikePolish?.id ? "/Bike" : ""}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            </section>
          )}

          <section className="space-y-2">
            <Select
              label="Address"
              value={addressId}
              onChange={(e) => {
                setAddressId(e.target.value);
                setConfirmTotal(null);
              }}
            >
              {(addresses || []).map((a) => (
                <option key={a.id} value={a.id}>
                  {[a.label ? toTitle(a.label) : "", a.line1].filter(Boolean).join(" · ")}
                </option>
              ))}
              <option value={NEW_ADDRESS}>+ Add New Address</option>
            </Select>
            {isNew && <NewAddressPicker onChange={(v) => {
              setNewAddress(v);
              setConfirmTotal(null);
            }} />}
            {(isNew ? !!newAddress : !!address) && <CoverageNote coverage={coverage} newPin={isNew} />}
          </section>

          <section>
            <h3 className="mb-2 text-sm font-semibold text-[#0E1A33]">Date & Time</h3>
            <SlotPicker
              serviceCenterId={slotCenter || undefined}
              date={date}
              onDateChange={(d) => {
                setDate(d);
                setSlot("");
                setConfirmTotal(null);
              }}
              value={slot}
              onChange={setSlot}
              enableHold
            />
            {lastDay && (
              <p className={cn("mt-1.5 text-xs", dateOutside ? "font-semibold text-[#C62828]" : "text-[#5F6878]")}>
                {dateOutside && date < firstDay
                  ? `Pick a date from ${formatShortDate(firstDay)} — your plan starts then.`
                  : dateOutside
                    ? `Last Booking Day: ${lastDayLabel} — pick an earlier date.`
                    : `Last Booking Day: ${lastDayLabel}`}
              </p>
            )}
          </section>


          <section className="space-y-1.5 rounded-xl border border-[#E4E9F1] p-3.5 text-sm" data-testid="plan-wash-quote">
            {!quoteBody ? (
              <p className="text-[#5F6878]">{isNew && !newAddress ? "Pin the new address to see the price." : "Pick a wash and an address to see the price."}</p>
            ) : quoteQuery.isLoading ? (
              <p className="text-[#5F6878]">Checking the price…</p>
            ) : quoteError ? (
              <p className="text-[#C62828]">{quoteError}</p>
            ) : quote ? (
              <>
                <div className="flex justify-between gap-3">
                  <span className="text-[#0E1A33]">{serviceName(serviceId)}</span>
                  <span className="font-semibold text-[#1E7B3C]">Covered By Your Plan</span>
                </div>
                {extraRows.map(({ service: s, qty }) => (
                  <div key={s.id} className="flex justify-between gap-3 text-[#5F6878]">
                    <span>
                      {toTitle(s.name)}
                      {qty > 1 && (
                        <span className="tabular-nums">
                          {" "}
                          · ₹{priceOf(s, typeId)} × {qty}
                        </span>
                      )}
                    </span>
                    <span className="tabular-nums">₹{priceOf(s, typeId) * qty}</span>
                  </div>
                ))}
                {quote.travel_charge > 0 && (
                  <div className="flex justify-between gap-3 text-[#5F6878]">
                    <span>Distance Charge</span>
                    <span className="tabular-nums">₹{Math.round(quote.travel_charge)}</span>
                  </div>
                )}
                {(quote.previous_balance_due ?? 0) > 0 && (
                  <div className="flex justify-between gap-3 text-[#5F6878]">
                    <span>Previous Balance Due</span>
                    <span className="tabular-nums">₹{Math.round(quote.previous_balance_due ?? 0)}</span>
                  </div>
                )}
                {(quote.wallet_applied ?? 0) > 0 && (
                  <div className="flex justify-between gap-3 text-[#1E7B3C]">
                    <span>Wallet Credit</span>
                    <span className="tabular-nums">−₹{Math.round(quote.wallet_applied ?? 0)}</span>
                  </div>
                )}
                <div className="flex justify-between gap-3 border-t border-[#EEF2F7] pt-2 text-base font-bold text-[#0E1A33]">
                  <span>To Pay</span>
                  <span className="tabular-nums">₹{Math.round(confirmTotal != null ? Math.max(0, confirmTotal - (quote.wallet_applied ?? 0)) : toPay ?? 0)}</span>
                </div>
                <p className="flex items-center gap-1.5 text-xs text-[#5F6878]">
                  <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-[#12A150]" /> {onlineOnly ? "Extras on a plan wash are paid online." : "Price shown is final — no hidden charges."}
                </p>
              </>
            ) : null}
          </section>

          {toPay != null && toPay > 0 && !onlineOnly && (
            <section className="grid grid-cols-2 gap-2">
              <button type="button" aria-pressed={payMethod === "cash"} className={chip(payMethod === "cash")} onClick={() => setPayMethod("cash")}>
                Pay After The Wash
              </button>
              <button type="button" aria-pressed={payMethod === "online"} className={chip(payMethod === "online")} onClick={() => setPayMethod("online")}>
                Pay Online Now
              </button>
            </section>
          )}

          {error && <p className="text-sm text-[#C62828]">{error}</p>}
          <button type="button" className={btn("primary", "lg", "w-full")} disabled={!ready || book.isPending} onClick={() => book.mutate()}>
            {book.isPending ? "Booking…" : !date || !slot ? "Pick A Date & Time" : toPay && toPay > 0 && method === "online" ? `Book & Pay ₹${Math.round(toPay)}` : "Book Plan Wash"}
          </button>
        </div>
      )}
    </Modal>
  );
}
