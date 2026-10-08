/**
 * "Add Service" at the door (spec 1.4): pick services/add-ons for THIS
 * car's type at that type's price → a confirm screen with the new total →
 * POST /bookings/{id}/add-services. The server re-checks everything (who,
 * when, mix rules, price) and its plain-English refusals are shown as-is.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Minus, Plus } from "lucide-react";
import { captainOnsiteApi, type AddServicesResult, type OnsiteBooking } from "../../api/captainOnsite";
import { getErrorMessage } from "../../lib/api-client";
import { translateCaptainError } from "../../lib/captainErrorTranslations";
import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import { cn } from "../../lib/cn";
import type { Booking, Service } from "../../types";
import { vehicleTypeApi } from "../../api/catalog";
import { bikeTypeIds, eligibleFor, offeredAddons } from "../../lib/serviceMix";
import { priceForType, titleCase } from "../public/landing/shared";
import { carTitle, rupees, visitMoney, type CarTypes } from "./jobState";
import { Btn, Notice, Sheet } from "./ui";

const MAX_QTY = 10;

/** Per-unit lines (extra bikes, bike polish) take a count; everything else is one per car. */
const perUnit = (s: Service) => !!s.is_addon && /bike|scooter|two.?wheeler/i.test(s.name);

export function AddServicesSheet({
  open,
  onClose,
  car,
  cars,
  types,
  onAdded,
}: {
  open: boolean;
  onClose: () => void;
  /** The car the services go on. */
  car: Booking;
  /** The whole visit (for the new total / what's left to collect). */
  cars: Booking[];
  types?: CarTypes;
  onAdded: (result: AddServicesResult) => void;
}) {
  const { t, language } = useCaptainTranslation();
  const [picks, setPicks] = useState<Record<string, number>>({});
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");

  const vt = car.vehicle_type || car.vehicle_snapshot?.vehicle_type || "";
  const catalogue = useQuery({
    queryKey: ["captain-onsite-services"],
    queryFn: captainOnsiteApi.services,
    enabled: open,
    staleTime: 10 * 60_000,
  });

  // What the car already has — a one-per-car service can't go on twice,
  // and a sibling variant (2 bikes vs 4 bikes) can't join its group.
  const onCar = useMemo(() => {
    const ids = new Set<string>([...(car.service_ids ?? []), ...((car as OnsiteBooking).added_services ?? []).map((a) => a.service_id)]);
    return ids;
  }, [car]);
  const all = catalogue.data ?? [];
  const takenGroups = new Set(all.filter((s) => onCar.has(s.id) && s.variant_group).map((s) => s.variant_group as string));
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list(), enabled: open, staleTime: 30 * 60_000 });
  const bikeIds = useMemo(() => bikeTypeIds(vehicleTypes), [vehicleTypes]);
  // Add-ons: the booking page's own chips for this type (the server refuses the rest).
  const offered = new Set(vt ? offeredAddons(all.filter((s) => s.is_active !== false), vt, bikeIds).map((s) => s.id) : []);
  const eligible = all.filter(
    (s) =>
      s.is_active !== false &&
      (s.is_addon ? offered.has(s.id) : !s.vehicle_types?.length || (!!vt && eligibleFor(s, vt))) &&
      !(s.variant_group && takenGroups.has(s.variant_group) && !onCar.has(s.id)),
  );
  const price = (s: Service) => priceForType(s, vt).price;
  const addons = eligible.filter((s) => s.is_addon);
  const mains = eligible.filter((s) => !s.is_addon);
  const chosen = eligible.filter((s) => picks[s.id]);
  const addTotal = Math.round(chosen.reduce((sum, s) => sum + price(s) * (picks[s.id] || 0), 0) * 100) / 100;
  const money = visitMoney(cars);

  const blocked = (s: Service) => onCar.has(s.id) && !perUnit(s);
  const toggle = (s: Service) => {
    if (blocked(s)) return;
    setError("");
    setPicks((p) => {
      const next = { ...p };
      if (next[s.id]) {
        delete next[s.id];
        return next;
      }
      // One pick per variant group.
      if (s.variant_group) for (const o of eligible) if (o.variant_group === s.variant_group) delete next[o.id];
      next[s.id] = 1;
      return next;
    });
  };
  const setQty = (s: Service, qty: number) => setPicks((p) => ({ ...p, [s.id]: Math.max(1, Math.min(MAX_QTY, qty)) }));

  const add = useMutation({
    mutationFn: () =>
      captainOnsiteApi.addServices(car.id, {
        service_ids: chosen.map((s) => s.id),
        quantities: Object.fromEntries(chosen.filter((s) => (picks[s.id] || 1) > 1).map((s) => [s.id, picks[s.id]])),
      }),
    onMutate: () => setError(""),
    onSuccess: (res) => {
      setPicks({});
      setConfirming(false);
      onAdded(res);
    },
    onError: (e) => setError(translateCaptainError(getErrorMessage(e), language)),
  });

  const close = () => {
    if (add.isPending) return;
    setConfirming(false);
    setError("");
    onClose();
  };

  const row = (s: Service) => {
    const qty = picks[s.id] || 0;
    const on = qty > 0;
    const already = blocked(s);
    return (
      <div
        key={s.id}
        className={cn(
          "flex min-h-[60px] items-center gap-3 rounded-[16px] border px-3.5 py-2.5",
          on ? "border-2 border-[#0A66F0] bg-[#E8F0FE]" : "border-[#E4E9F1] bg-white",
          already && "opacity-60",
        )}
      >
        <button
          type="button"
          disabled={already}
          onClick={() => toggle(s)}
          aria-pressed={on}
          className="flex min-h-[44px] min-w-0 flex-1 items-center gap-3 text-left"
        >
          <span
            className={cn(
              "flex h-6 w-6 shrink-0 items-center justify-center rounded-md border-2",
              on ? "border-[#0A66F0] bg-[#0A66F0] text-white" : "border-[#C9D2E0] bg-white",
            )}
          >
            {on && <Check className="h-4 w-4" strokeWidth={3} />}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-[15px] font-bold text-[#0E1A33]">{titleCase(s.name)}</span>
            {already && <span className="block text-xs font-semibold text-[#5F6878]">{t("captain.onsite.onBooking")}</span>}
          </span>
          <span className="shrink-0 tabular-nums text-[15px] font-extrabold text-[#0E1A33]">{rupees(price(s))}</span>
        </button>
        {on && perUnit(s) && (
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              aria-label="Less"
              disabled={qty <= 1}
              onClick={() => setQty(s, qty - 1)}
              className="flex h-11 w-11 items-center justify-center rounded-full bg-white text-[#0A66F0] disabled:text-[#A3AAB6]"
            >
              <Minus className="h-4 w-4" />
            </button>
            <span className="w-6 text-center tabular-nums text-[15px] font-extrabold">{qty}</span>
            <button
              type="button"
              aria-label="More"
              disabled={qty >= MAX_QTY}
              onClick={() => setQty(s, qty + 1)}
              className="flex h-11 w-11 items-center justify-center rounded-full bg-white text-[#0A66F0] disabled:text-[#A3AAB6]"
            >
              <Plus className="h-4 w-4" />
            </button>
          </div>
        )}
      </div>
    );
  };

  const carLine = `${carTitle(car, types)}${car.booking_number ? ` · ${car.booking_number}` : ""}`;

  return (
    <Sheet open={open} onClose={close} title={confirming ? t("captain.onsite.confirmTitle") : t("captain.onsite.addService")}>
      <p className="-mt-1 mb-3 text-sm font-semibold text-[#5F6878]">{carLine}</p>
      {!confirming ? (
        <div data-testid="captain-addservice-pick">
          {catalogue.isLoading ? (
            <div className="space-y-2">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-[60px] animate-pulse rounded-[16px] bg-[#EEF3FA]" />
              ))}
            </div>
          ) : catalogue.isError ? (
            <Notice tone="red">
              {t("captain.onsite.loadFailed")}{" "}
              <button type="button" className="font-bold underline" onClick={() => void catalogue.refetch()}>
                {t("captain.v2.tryAgain")}
              </button>
            </Notice>
          ) : eligible.length === 0 ? (
            <Notice tone="gray">{t("captain.onsite.none")}</Notice>
          ) : (
            <div className="space-y-4 pb-2">
              {addons.length > 0 && (
                <section className="space-y-2">
                  <h3 className="text-xs font-bold uppercase tracking-wide text-[#5F6878]">{t("captain.onsite.addons")}</h3>
                  {addons.map(row)}
                </section>
              )}
              {mains.length > 0 && (
                <section className="space-y-2">
                  <h3 className="text-xs font-bold uppercase tracking-wide text-[#5F6878]">{t("captain.onsite.services")}</h3>
                  {mains.map(row)}
                </section>
              )}
            </div>
          )}
          <div className="sticky bottom-0 -mx-4 mt-2 border-t border-[#E4E9F1] bg-white px-4 pt-3">
            <Btn className="w-full" disabled={!chosen.length} onClick={() => setConfirming(true)}>
              {chosen.length ? t("captain.onsite.continue").replace("{amount}", `+${rupees(addTotal)}`) : t("captain.onsite.continue").split(" · ")[0]}
            </Btn>
          </div>
        </div>
      ) : (
        <div data-testid="captain-addservice-confirm" className="space-y-3">
          <div className="divide-y divide-[#E4E9F1] rounded-[16px] border border-[#E4E9F1]">
            {chosen.map((s) => (
              <div key={s.id} className="flex items-center justify-between gap-3 px-3.5 py-3 text-[15px]">
                <span className="font-semibold text-[#0E1A33]">
                  {titleCase(s.name)}
                  {(picks[s.id] || 1) > 1 ? ` ×${picks[s.id]}` : ""}
                </span>
                <span className="tabular-nums font-bold">{rupees(price(s) * (picks[s.id] || 1))}</span>
              </div>
            ))}
            <div className="flex items-center justify-between px-3.5 py-3 text-[15px]">
              <span className="font-medium text-[#5F6878]">{t("captain.onsite.adds")}</span>
              <span className="tabular-nums font-bold text-[#0A66F0]">+{rupees(addTotal)}</span>
            </div>
          </div>
          <div className="rounded-[16px] bg-[#EEF3FA] px-4 py-3.5 text-center">
            <p className="tabular-nums text-[24px] font-extrabold text-[#0E1A33]" data-testid="captain-addservice-newtotal">
              {t("captain.onsite.newTotal").replace("{amount}", rupees(money.total + addTotal))}
            </p>
            <p className="mt-0.5 text-sm font-semibold text-[#5F6878]">
              {t("captain.onsite.toCollect")}: <span className="tabular-nums text-[#0E1A33]">{rupees(money.due + addTotal)}</span>
            </p>
          </div>
          <p className="text-sm text-[#5F6878]">{t("captain.onsite.tellCustomer")}</p>
          {error && (
            <p className="text-sm font-medium text-[#B91C1C]" role="alert">
              {error}
            </p>
          )}
          <div className="grid grid-cols-[auto_1fr] gap-2">
            <Btn variant="outline" disabled={add.isPending} onClick={() => setConfirming(false)}>
              {t("captain.common.back")}
            </Btn>
            <Btn loading={add.isPending} onClick={() => !add.isPending && add.mutate()}>
              {t("captain.onsite.confirmCta").replace("{amount}", rupees(addTotal))}
            </Btn>
          </div>
        </div>
      )}
    </Sheet>
  );
}
