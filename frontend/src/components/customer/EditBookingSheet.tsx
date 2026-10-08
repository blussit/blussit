/**
 * "Edit Booking" (spec 2026-10-07 §1.3) — the customer changes their own
 * visit until 1 hour before the slot: date & time, address (a saved one or
 * a new one pinned on the map — same service center), notes, and per car
 * its type and services (a bike: how many bikes — the booking page's own
 * counter and rules). Plan-covered cars keep their car and service.
 *
 * The exact new price is shown before saving — the same PATCH with
 * dry_run: true (priced and rolled back by the server) — and the save
 * carries `expected_total`: a higher server total comes back as 409
 * PRICE_CHANGED, shown here for the customer to confirm. Every other refusal is the server's own words.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CarFront, Gift } from "lucide-react";
import { bookingEditApi, type BookingCarEdit, type BookingEditPayload, type BookingEditPreview, type BookingEditResult } from "../../api/bookingEdit";
import { catalogApi } from "../../api/catalog";
import { addressApi } from "../../api/profile";
import { Input, Modal, Select } from "../ui";
import { SlotPicker } from "../shared/SlotPicker";
import { getErrorCode, getErrorMessage } from "../../lib/api-client";
import { addonKit, baseGroups, bikeCountRange, bikeTypeIds, composeBikeLine, offeredAddons, variantCount } from "../../lib/serviceMix";
import { toTitle } from "../../lib/titleCase";
import { cn } from "../../lib/cn";
import type { Booking, Service, VehicleTypeOption } from "../../types";
import { btn } from "./ui";
import { QtyStepper } from "../shared/QtyStepper";
import { CoverageNote, NewAddressPicker, useAddressCoverage, type NewAddress } from "./NewAddressPicker";

/** The address select's "Add New Address" choice. */
const NEW_ADDRESS = "__new__";

interface CarDraft {
  id: string;
  plan: boolean;
  typeId: string;
  base: string;
  /** Picked add-ons — on a bike, never the add-a-bike line (that's `bikes`). */
  addons: string[];
  /** A bike line's bike count (base variant + extra bikes); 1 on a car. */
  bikes: number;
  /** As booked — kept for a car's own per-unit lines. */
  quantities: Record<string, number>;
}

/** One car's services and quantities as the server takes them. */
interface CarMix {
  ids: string[];
  quantities: Record<string, number>;
}

/** A service's standard price for a car type (display only — the quote is the real price). */
const priceOf = (s: Service, typeId: string) => Math.round(s.vehicle_type_prices?.[typeId] ?? s.price ?? 0);
const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));

const sameMix = (a: CarMix, b: CarMix) => {
  if (!sameSet(a.ids, b.ids)) return false;
  return a.ids.every((id) => (a.quantities[id] || 1) === (b.quantities[id] || 1));
};

function draftOf(car: Booking, services: Service[], bikeIds: Set<string>): CarDraft {
  const ids = car.service_ids || [];
  const quantities = { ...(car.service_quantities || {}) };
  const byId = new Map(services.map((s) => [s.id, s]));
  const base = ids.find((id) => byId.get(id) && !byId.get(id)!.is_addon) || ids[0] || "";
  const typeId = car.vehicle_type || "";
  const draft = { id: car.id, plan: !!car.subscription_id, typeId, base, addons: ids.filter((id) => id !== base), bikes: 1, quantities };
  if (!bikeIds.has(typeId)) return draft;
  // A bike: the counter = the base variant's bikes + the added bikes.
  const addBike = addonKit(services, typeId, bikeIds).addBike;
  const extra = addBike && ids.includes(addBike.id) ? quantities[addBike.id] || 1 : 0;
  const baseSvc = byId.get(base);
  return { ...draft, addons: draft.addons.filter((id) => id !== addBike?.id), bikes: (baseSvc ? variantCount(baseSvc) : 1) + extra };
}

export function EditBookingSheet({
  open,
  onClose,
  cars,
  groupId,
  vehicleTypes,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  /** The visit's cars still on it (not cancelled), lead first. */
  cars: Booking[];
  groupId: string | null;
  vehicleTypes?: VehicleTypeOption[];
  onSaved: (result: BookingEditResult) => void;
}) {
  const lead = cars[0];
  const { data: servicesPage } = useQuery({ queryKey: ["public-services"], queryFn: () => catalogApi.services({ page_size: 100 }), staleTime: 60_000, enabled: open });
  const services = useMemo(() => (servicesPage?.data || []).filter((s) => s.is_active !== false), [servicesPage]);
  const { data: addresses } = useQuery({ queryKey: ["addresses"], queryFn: addressApi.list, staleTime: 60_000, enabled: open });
  const bikeIds = useMemo(() => bikeTypeIds(vehicleTypes), [vehicleTypes]);

  const origDate = lead?.scheduled_date.slice(0, 10) || "";
  const origSlot = lead?.scheduled_slot || "";
  const [date, setDate] = useState(origDate);
  const [slot, setSlot] = useState(origSlot);
  const [addressId, setAddressId] = useState(lead?.address_id || "");
  const [newAddress, setNewAddress] = useState<NewAddress | null>(null);
  const [notes, setNotes] = useState(lead?.customer_notes || "");
  const [drafts, setDrafts] = useState<CarDraft[]>([]);
  const [error, setError] = useState("");
  // The server's higher price (409), waiting for the customer's OK.
  const [confirmTotal, setConfirmTotal] = useState<number | null>(null);

  // Fresh form every time it opens (and once the catalogue is in).
  useEffect(() => {
    if (!open || !lead) return;
    setDate(origDate);
    setSlot(origSlot);
    setAddressId(lead.address_id || "");
    setNewAddress(null);
    setNotes(lead.customer_notes || "");
    setError("");
    setConfirmTotal(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, lead?.id]);
  useEffect(() => {
    if (open && services.length) setDrafts(cars.map((c) => draftOf(c, services, bikeIds)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, services.length, bikeIds, cars.map((c) => c.id).join()]);

  const originals = useMemo(() => (services.length ? cars.map((c) => draftOf(c, services, bikeIds)) : []), [cars, services, bikeIds]);
  const svcById = useMemo(() => new Map(services.map((s) => [s.id, s])), [services]);
  // The base's variant group (Bike Wash 1 / 2 bikes), smallest first.
  const variantsOf = (baseId: string, typeId: string) => {
    const g = baseGroups(services, typeId).find((x) => x.variants.some((v) => v.id === baseId));
    const own = svcById.get(baseId);
    return g ? g.variants : own ? [own] : [];
  };
  const isBikeType = (typeId: string) => bikeIds.has(typeId);
  /** What the car is booked for, as the booking page builds it. */
  const mixOf = (d: CarDraft): CarMix => {
    if (isBikeType(d.typeId) && svcById.get(d.base)) {
      const kit = addonKit(services, d.typeId, bikeIds);
      const m = composeBikeLine({ variants: variantsOf(d.base, d.typeId), count: d.bikes, kit, addonIds: d.addons });
      return { ids: m.serviceIds, quantities: m.quantities };
    }
    const ids = [d.base, ...d.addons].filter(Boolean);
    return { ids, quantities: Object.fromEntries(ids.filter((id) => (d.quantities[id] || 1) > 1).map((id) => [id, d.quantities[id]])) };
  };
  const carChanged = (d: CarDraft) => {
    const o = originals.find((x) => x.id === d.id);
    return !!o && !d.plan && (o.typeId !== d.typeId || !sameMix(mixOf(o), mixOf(d)));
  };
  const changedCars = drafts.filter(carChanged);
  const timeChanged = !!date && !!slot && (date !== origDate || slot !== origSlot);
  // A new address: the shared map pin (or typed fallback), re-checked for coverage.
  const isNewAddress = addressId === NEW_ADDRESS;
  const coverage = useAddressCoverage(isNewAddress ? newAddress : null, open && isNewAddress);
  const newAddressPending = isNewAddress && (!newAddress || coverage.data?.covered !== true);
  const addressChanged = isNewAddress ? !!newAddress && coverage.data?.covered === true : !!addressId && addressId !== (lead?.address_id || "");
  const notesChanged = notes.trim() !== (lead?.customer_notes || "").trim();
  const anyChange = timeChanged || addressChanged || notesChanged || changedCars.length > 0;

  const currentTotal = cars.reduce((s, c) => s + (c.total_amount || 0), 0);

  /** The edit as the server takes it (no expected_total — added on save). */
  const buildPayload = (): BookingEditPayload => {
    const payload: BookingEditPayload = {};
    if (timeChanged) {
      payload.scheduled_date = date;
      payload.scheduled_slot = slot;
    }
    if (addressChanged) {
      if (isNewAddress && newAddress) {
        payload.address = {
          ...newAddress,
          city: newAddress.city || coverage.data?.center?.city || undefined,
          state: newAddress.state || coverage.data?.center?.state || undefined,
        };
      } else payload.address_id = addressId;
    }
    if (notesChanged) payload.customer_notes = notes.trim();
    const edits: BookingCarEdit[] = changedCars.map((d) => {
      const o = originals.find((x) => x.id === d.id)!;
      const mix = mixOf(d);
      return {
        booking_id: d.id,
        ...(o.typeId !== d.typeId ? { vehicle_type: d.typeId } : {}),
        service_ids: mix.ids,
        // Only per-unit lines (extra bikes, bike polish) carry a count.
        service_quantities: mix.quantities,
      };
    });
    if (groupId) {
      if (edits.length) payload.cars = edits;
    } else if (edits[0]) {
      const { booking_id: _ignored, ...car } = edits[0];
      Object.assign(payload, car);
    }
    return payload;
  };
  const send = (payload: BookingEditPayload) => (groupId ? bookingEditApi.editGroup(groupId, payload) : bookingEditApi.edit(lead.id, payload));

  // The exact new price: the same PATCH with dry_run (validated, priced and
  // rolled back by the server — nothing written).
  const previewReady = open && !!lead && anyChange && !(date !== origDate && !slot) && !drafts.some((d) => !d.plan && !d.base) && !newAddressPending;
  const previewPayload = previewReady ? buildPayload() : null;
  const preview = useQuery({
    queryKey: ["edit-preview", lead?.id, groupId, JSON.stringify(previewPayload)],
    queryFn: (): Promise<BookingEditPreview> => send({ ...previewPayload!, dry_run: true }),
    enabled: !!previewPayload,
    staleTime: 15_000,
    retry: false,
  });
  const previewError = preview.isError ? getErrorMessage(preview.error) : "";
  const shownNew = confirmTotal ?? preview.data?.total_amount ?? null;
  const delta = shownNew == null ? null : Math.round(shownNew - currentTotal);
  const walletBack = Math.round(preview.data?.wallet_credit ?? 0);
  // The visit's distance charge after the edit, inside the new total — the
  // dry run's own figure (never the coverage check's estimate).
  const travelShown = Math.round(preview.data?.travel_charge ?? 0);
  const travelBefore = Math.round(cars.reduce((s, c) => s + (c.travel_charge || 0), 0));

  const setDraft = (id: string, patch: Partial<CarDraft>) => {
    setConfirmTotal(null);
    setDrafts((list) => list.map((d) => (d.id === id ? { ...d, ...patch } : d)));
  };

  // The booking page's service cards: one per product (bike-wash variants collapsed).
  const groupsFor = (typeId: string) => baseGroups(services, typeId);
  // The booking page's own add-on chips for this type (never Extra Bike Wash on a car).
  const addonsFor = (typeId: string) => offeredAddons(services, typeId, bikeIds);

  const save = useMutation({
    mutationFn: () => {
      const expected = confirmTotal ?? preview.data?.total_amount ?? currentTotal;
      return send({ ...buildPayload(), expected_total: Math.round(expected * 100) / 100 });
    },
    onMutate: () => setError(""),
    onSuccess: (result) => {
      setConfirmTotal(null);
      onSaved(result);
    },
    onError: (err) => {
      if (getErrorCode(err) === "PRICE_CHANGED") {
        const details = ((err as { response?: { data?: { details?: Record<string, unknown> } } }).response?.data?.details || {}) as Record<string, unknown>;
        const fresh = typeof details.total_amount === "number" ? details.total_amount : null;
        if (fresh != null) {
          setConfirmTotal(fresh);
          setError(`The new price is ₹${Math.round(fresh)}. Tap Confirm to save at this price.`);
          return;
        }
      }
      setError(getErrorMessage(err));
    },
  });

  if (!lead) return null;
  const typeName = (id: string) => toTitle(vehicleTypes?.find((t) => t.id === id)?.name) || "Car";
  const savedHasCurrent = (addresses || []).some((a) => a.id === lead.address_id);

  return (
    <Modal open={open} onClose={onClose} title="Edit Booking" maxWidth="max-w-xl">
      <div className="space-y-5">
        <section>
          <h3 className="mb-2 text-sm font-semibold text-[#0E1A33]">Date & Time</h3>
          <SlotPicker
            serviceCenterId={lead.service_center_id}
            date={date}
            onDateChange={(d) => {
              setDate(d);
              setSlot("");
            }}
            value={slot}
            onChange={setSlot}
          />
          {date && !slot && <p className="mt-1.5 text-xs text-[#5F6878]">Pick a time for the new date.</p>}
        </section>

        <section className="space-y-2">
          <Select
            label="Address"
            value={addressId}
            onChange={(e) => {
              setAddressId(e.target.value);
              setConfirmTotal(null);
            }}
          >
            {!savedHasCurrent && <option value={lead.address_id}>{lead.address_snapshot?.line1 || "Current Address"}</option>}
            {(addresses || []).map((a) => (
              <option key={a.id} value={a.id}>
                {[a.label ? toTitle(a.label) : "", a.line1].filter(Boolean).join(" · ")}
              </option>
            ))}
            <option value={NEW_ADDRESS}>+ Add New Address</option>
          </Select>
          {isNewAddress && (
            <NewAddressPicker
              onChange={(v) => {
                setNewAddress(v);
                setConfirmTotal(null);
              }}
            />
          )}
          {isNewAddress && newAddress && <CoverageNote coverage={coverage} newPin={coverage.data?.center?.id === lead.service_center_id} />}
          <p className="text-xs text-[#5F6878]">A new address must be in the same area.</p>
        </section>

        {drafts.map((d, i) => {
          const car = cars.find((c) => c.id === d.id);
          const head = [`Car ${cars.length > 1 ? i + 1 : ""}`.trim(), typeName(d.typeId), car?.vehicle_registration_number || ""].filter(Boolean).join(" · ");
          if (d.plan) {
            return (
              <section key={d.id} className="rounded-xl bg-[#F7F9FC] p-3 text-sm">
                <p className="flex items-center gap-2 font-semibold text-[#0E1A33]">
                  <Gift className="h-4 w-4 text-[#0A66F0]" /> {head}
                </p>
                <p className="mt-1 text-xs text-[#5F6878]">On your plan — only the date, time, address and notes can change.</p>
              </section>
            );
          }
          const groups = groupsFor(d.typeId);
          const baseGroup = groups.find((g) => g.variants.some((v) => v.id === d.base));
          const addons = addonsFor(d.typeId);
          const isBike = isBikeType(d.typeId);
          const kit = addonKit(services, d.typeId, bikeIds);
          // The booking page's own counter: extra bikes beyond the largest
          // variant ride as the add-a-bike line (accepted on a bike edit).
          const bikeRange = isBike && baseGroup ? bikeCountRange(baseGroup.variants, kit) : null;
          return (
            <section key={d.id} className="space-y-3 rounded-xl bg-[#F7F9FC] p-3">
              <p className="flex items-center gap-2 text-sm font-semibold text-[#0E1A33]">
                <CarFront className="h-4 w-4 text-[#5F6878]" /> {head}
              </p>
              <Select
                label="Car Type"
                value={d.typeId}
                onChange={(e) => {
                  const typeId = e.target.value;
                  const keep = groupsFor(typeId).find((g) => g.variants.some((v) => v.id === d.base));
                  const bikes = bikeIds.has(typeId) ? d.bikes : 1;
                  setDraft(d.id, {
                    typeId,
                    base: keep ? d.base : groupsFor(typeId)[0]?.primary.id || "",
                    addons: d.addons.filter((id) => addonsFor(typeId).some((s) => s.id === id)),
                    bikes: keep || !bikeIds.has(typeId) ? bikes : 1,
                  });
                }}
              >
                {(vehicleTypes || []).map((t) => (
                  <option key={t.id} value={t.id}>
                    {toTitle(t.name)}
                  </option>
                ))}
              </Select>
              <Select
                label="Service"
                value={baseGroup?.key || ""}
                onChange={(e) => {
                  const g = groups.find((x) => x.key === e.target.value);
                  // A bike product keeps the count; its variant follows the count.
                  setDraft(d.id, { base: g?.primary.id || "", bikes: isBike && g ? Math.max(variantCount(g.primary), d.bikes) : 1 });
                }}
              >
                {!baseGroup && <option value="">Pick A Service</option>}
                {groups.map((g) => (
                  <option key={g.key} value={g.key}>
                    {toTitle(g.label)}
                  </option>
                ))}
              </Select>
              {bikeRange && bikeRange.max > bikeRange.min && (
                <div className="flex min-h-[56px] items-center justify-between gap-3 rounded-xl border border-[#E4E9F1] bg-white px-3.5 py-1.5" data-testid="edit-bikes">
                  <span className="text-sm text-[#0E1A33]">
                    Bikes
                    {kit.addBike && priceOf(kit.addBike, d.typeId) > 0 && (
                      <span className="block text-xs text-[#5F6878]">+₹{priceOf(kit.addBike, d.typeId)} Per Extra Bike</span>
                    )}
                  </span>
                  <QtyStepper
                    size="lg"
                    label="Bikes"
                    value={Math.min(bikeRange.max, Math.max(bikeRange.min, d.bikes))}
                    min={bikeRange.min}
                    max={bikeRange.max}
                    onChange={(n) => setDraft(d.id, { bikes: n })}
                  />
                </div>
              )}
              {addons.length > 0 && (
                <div>
                  <p className="mb-1.5 text-sm font-medium text-[#0E1A33]">Extras</p>
                  <div className="flex flex-wrap gap-2">
                    {addons.map((a) => {
                      const on = d.addons.includes(a.id);
                      return (
                        <button
                          key={a.id}
                          type="button"
                          aria-pressed={on}
                          onClick={() => setDraft(d.id, { addons: on ? d.addons.filter((x) => x !== a.id) : [...d.addons, a.id] })}
                          className={cn(
                            "min-h-[44px] rounded-full border px-3.5 text-sm transition-colors",
                            on ? "border-[#0E1A33] bg-[#E8F0FE] font-semibold text-[#0E1A33]" : "border-[#E4E9F1] bg-white text-[#0E1A33] hover:border-[#CFDCF0]"
                          )}
                        >
                          {toTitle(a.name)}
                          {priceOf(a, d.typeId) > 0 && (
                            <span className="ml-1 text-[#5F6878]">
                              +₹{priceOf(a, d.typeId)}
                              {isBike && a.id === kit.bikePolish?.id ? "/Bike" : ""}
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </section>
          );
        })}

        <Input label="Notes For The Captain" value={notes} maxLength={500} onChange={(e) => setNotes(e.target.value)} placeholder="Gate code, parking spot…" />

        <section className="rounded-xl border border-[#E4E9F1] p-3.5 text-sm" data-testid="edit-price">
          {newAddressPending ? (
            <p className="text-[#5F6878]">
              {!newAddress ? "Pin the new address to see the price." : coverage.data && !coverage.data.covered ? "Pick an address we serve." : "Checking the area…"}
            </p>
          ) : !anyChange ? (
            <p className="text-[#5F6878]">Make a change above to see the price.</p>
          ) : previewError && confirmTotal == null ? (
            <p className="text-[#C62828]">{previewError}</p>
          ) : shownNew == null ? (
            <p className="text-[#5F6878]">{date !== origDate && !slot ? "Pick a time to see the price." : "Checking the new price…"}</p>
          ) : (
            <>
              {changedCars.map((d) => {
                const mix = mixOf(d);
                const n = cars.findIndex((c) => c.id === d.id);
                return (
                  <div key={d.id} className="mb-2 space-y-0.5 border-b border-[#EEF2F7] pb-2" data-testid="edit-lines">
                    <p className="text-xs font-semibold text-[#5F6878]">
                      {[cars.length > 1 ? `Car ${n + 1}` : "", typeName(d.typeId)].filter(Boolean).join(" · ")}
                    </p>
                    {mix.ids.map((id) => {
                      const s = svcById.get(id);
                      if (!s) return null;
                      const qty = mix.quantities[id] || 1;
                      const each = priceOf(s, d.typeId);
                      return (
                        <div key={id} className="flex justify-between gap-3 text-[#0E1A33]">
                          <span>
                            {toTitle(s.name)}
                            {qty > 1 && <span className="tabular-nums text-[#5F6878]"> · ₹{each} × {qty}</span>}
                          </span>
                          <span className="tabular-nums">₹{each * qty}</span>
                        </div>
                      );
                    })}
                  </div>
                );
              })}
              {(travelShown > 0 || travelBefore > 0) && (
                <div className="mb-1 flex justify-between gap-3 text-[#5F6878]" data-testid="edit-travel">
                  <span>Distance Charge</span>
                  <span className="tabular-nums">
                    {travelBefore !== travelShown && travelBefore > 0 && <span className="mr-1.5 text-[#8A94A6] line-through">₹{travelBefore}</span>}₹{travelShown}
                  </span>
                </div>
              )}
              <div className="flex justify-between gap-3 text-[#5F6878]">
                <span>Current Total</span>
                <span className="tabular-nums">₹{Math.round(currentTotal)}</span>
              </div>
              <div className="mt-1 flex justify-between gap-3 font-semibold text-[#0E1A33]">
                <span>New Total</span>
                <span className="tabular-nums">₹{Math.round(shownNew ?? currentTotal)}</span>
              </div>
              {delta != null && delta > 0 && <p className="mt-2 font-semibold text-[#B25E00]">₹{delta} More To Pay</p>}
              {delta != null && delta < 0 && walletBack > 0 && <p className="mt-2 font-semibold text-[#1E7B3C]">₹{walletBack} Will Be Added To Your Wallet</p>}
              {delta != null && delta < 0 && walletBack === 0 && <p className="mt-2 font-semibold text-[#1E7B3C]">₹{-delta} Less To Pay</p>}
              {preview.data && preview.data.amount_due > 0 && confirmTotal == null && (
                <p className="mt-1 text-[#0E1A33]">Amount Due After Saving: ₹{Math.round(preview.data.amount_due)}</p>
              )}
              {(preview.data?.notices || []).map((n, i) => (
                <p key={i} className="mt-1 text-xs text-[#5F6878]">{n}</p>
              ))}
              {delta === 0 && <p className="mt-2 text-[#5F6878]">Price unchanged.</p>}
            </>
          )}
        </section>

        {error && <p className="text-sm text-[#C62828]">{error}</p>}
        <button
          type="button"
          className={btn("primary", "lg", "w-full")}
          disabled={!anyChange || newAddressPending || (date !== origDate && !slot) || save.isPending || drafts.some((d) => !d.plan && !d.base)}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "Saving…" : confirmTotal != null ? `Confirm ₹${Math.round(confirmTotal)}` : "Save Changes"}
        </button>
      </div>
    </Modal>
  );
}

const FIELD_LABELS: Record<string, string> = {
  date: "Date",
  slot: "Time",
  address: "Address",
  notes: "Notes",
  vehicle: "Car",
  services: "Services",
  total: "Total",
};

/** The saved edit, line by line: "Time · 9:00 AM – 12:00 PM → 12:00 PM – 3:00 PM". */
export function EditResultSheet({ result, onClose, onPay }: { result: BookingEditResult | null; onClose: () => void; onPay?: () => void }) {
  if (!result) return null;
  const fmt = (field: string, v: string | number | null) => (v == null || v === "" ? "—" : field === "total" ? `₹${Math.round(Number(v))}` : String(v));
  const multi = new Set(result.changes.map((c) => c.booking_id)).size > 1;
  return (
    <Modal open={!!result} onClose={onClose} title="Booking Updated">
      <div className="space-y-4">
        {result.changes.length ? (
          <ul className="space-y-2 text-sm" data-testid="edit-changes">
            {result.changes.map((c, i) => (
              <li key={i} className="rounded-xl bg-[#F7F9FC] px-3 py-2">
                <p className="text-xs font-semibold text-[#5F6878]">
                  {FIELD_LABELS[c.field] || toTitle(c.field)}
                  {multi && c.booking_number ? ` · ${c.booking_number}` : ""}
                </p>
                <p className="text-[#0E1A33]">
                  <span className="text-[#8A94A6] line-through">{fmt(c.field, c.from)}</span> → <span className="font-semibold">{fmt(c.field, c.to)}</span>
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-[#5F6878]">Nothing changed.</p>
        )}
        {result.notices.map((n, i) => (
          <p key={i} className="rounded-xl bg-[#FFF8EE] px-3 py-2 text-sm text-[#0E1A33]">
            {n}
          </p>
        ))}
        {result.wallet_credit > 0 && <p className="text-sm font-semibold text-[#1E7B3C]">₹{Math.round(result.wallet_credit)} Added To Your Wallet</p>}
        <div className="flex justify-between text-sm font-bold text-[#0E1A33]">
          <span>New Total</span>
          <span className="tabular-nums">₹{Math.round(result.total_amount)}</span>
        </div>
        {result.amount_due > 0 && onPay ? (
          <button type="button" className={btn("primary", "md", "w-full")} onClick={onPay}>
            Pay ₹{Math.round(result.amount_due)} Now
          </button>
        ) : (
          <button type="button" className={btn("outline", "md", "w-full")} onClick={onClose}>
            Done
          </button>
        )}
      </div>
    </Modal>
  );
}
