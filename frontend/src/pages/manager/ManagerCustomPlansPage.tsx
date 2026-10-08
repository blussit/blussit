import { useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Car, Gauge, Layers, Plus, Trash2 } from "lucide-react";
import {
  customPlanApi,
  type CustomPlan,
  type CustomPlanCarInput,
  type CustomPlanPreview,
  type CustomPlanStatus,
} from "../../api/customPlans";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { crmApi } from "../../api/crm";
import { Button, EmptyState, ErrorState, Input, PageLoader, Select, Spinner } from "../../components/ui";
import { CustomerNamePhoneFields } from "../../components/shared/CustomerNamePhoneFields";
import { CustomerDetailDrawer } from "../../components/shared/CustomerDetailDrawer";
import { Pager, useDebouncedValue } from "../../components/shared/ListControls";
import { VehicleIcon } from "../../components/shared/VehicleIcon";
import { CustomPlanCard } from "../../components/manager/CustomPlanCard";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage, getErrorStatus } from "../../lib/api-client";
import { baseGroups } from "../../lib/serviceMix";
import { toTitle } from "../../lib/titleCase";
import { validateIndianMobile } from "../../lib/validators";

/**
 * Custom Plans (spec §1.6): one plan for several of a customer's cars. Per
 * car pick the main services and how many washes of each; the server
 * prices it live (each car type's standard price, less an optional
 * discount — a manager can give up to half). Save it, then send one
 * payment link or mark it paid in cash; paying starts a 30-day pass per car.
 * The list below shows every cart with its status, washes left per car and
 * the open link; an unpaid one can be revised or cancelled. A paid one can
 * be renewed (the same builder, prefilled — a new cart whose cars each start
 * the day after their current pass) and refunded car by car.
 */

const MAX_CARS = 10;
const MAX_ITEMS = 8;
const MAX_COUNT = 30;
const MANAGER_MAX_DISCOUNT_PCT = 50;

let seq = 0;
const key = () => `k${++seq}`;

interface DraftItem {
  key: string;
  serviceId: string;
  count: number;
}
interface DraftCar {
  key: string;
  mode: "saved" | "new";
  vehicleId: string;
  plate: string;
  vehicleType: string;
  items: DraftItem[];
}

const newItem = (): DraftItem => ({ key: key(), serviceId: "", count: 1 });
const newCar = (mode: "saved" | "new" = "new"): DraftCar => ({ key: key(), mode, vehicleId: "", plate: "", vehicleType: "", items: [newItem()] });

const rupees = (n?: number | null) => `₹${Math.round(Number(n || 0)).toLocaleString("en-IN")}`;

/** 44px −/+ counter (the shared QtyStepper is 28px — too small for a thumb). */
function Counter({ value, onChange, label }: { value: number; onChange: (n: number) => void; label: string }) {
  return (
    <span className="inline-flex items-center gap-1" aria-label={label}>
      <button
        type="button"
        onClick={() => onChange(Math.max(1, value - 1))}
        disabled={value <= 1}
        className="flex h-11 w-11 items-center justify-center rounded-full border border-gray-300 text-lg font-bold text-black disabled:opacity-30"
        aria-label={`Fewer ${label}`}
      >
        −
      </button>
      <span className="w-7 text-center font-mono-num text-base font-bold text-black">{value}</span>
      <button
        type="button"
        onClick={() => onChange(Math.min(MAX_COUNT, value + 1))}
        disabled={value >= MAX_COUNT}
        className="flex h-11 w-11 items-center justify-center rounded-full border border-gray-300 text-lg font-bold text-black disabled:opacity-30"
        aria-label={`More ${label}`}
      >
        +
      </button>
    </span>
  );
}

export default function ManagerCustomPlansPage() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "all" ? "all" : "new";
  const reviseId = params.get("revise");
  const renewId = params.get("renew");
  const planId = params.get("plan");
  const [customerId, setCustomerId] = useState<string | null>(null);

  if (!user?.service_center_id) {
    return (
      <EmptyState
        icon={Gauge}
        title="No Service Center Linked"
        description="Your manager account isn't linked to a service center yet — ask an admin to assign one before selling a custom plan."
      />
    );
  }

  const go = (next: Record<string, string>) => setParams(next);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Custom Plans</h1>
          <p className="mt-1 max-w-2xl text-sm text-[var(--color-text-secondary)]">One plan for several cars — pick the washes per car. Valid 30 days from payment.</p>
        </div>
        <div className="flex max-w-full gap-1 rounded-xl border border-[var(--color-card-border)] bg-white p-1" role="tablist">
          {(
            [
              { key: "new", label: renewId ? "Renew Plan" : reviseId ? "Revise Plan" : "New Plan" },
              { key: "all", label: "All Plans" },
            ] as const
          ).map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={tab === t.key}
              onClick={() => go(t.key === "all" ? { tab: "all" } : {})}
              className={`min-h-11 shrink-0 whitespace-nowrap rounded-lg px-3.5 text-sm font-semibold transition-colors sm:min-h-9 ${
                tab === t.key ? "bg-[var(--color-primary)] text-white" : "text-[var(--color-text-secondary)] hover:bg-[var(--color-primary-light)]"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {tab === "all" ? (
        <CustomPlanList
          onOpenCustomer={setCustomerId}
          onRevise={(p) => go({ revise: p.id })}
          onRenew={(p) => go({ renew: p.id })}
          onOpenPlan={(id) => go({ plan: id })}
        />
      ) : planId ? (
        <SavedPlan
          key={planId}
          planId={planId}
          onOpenCustomer={setCustomerId}
          onRevise={(p) => go({ revise: p.id })}
          onRenew={(p) => go({ renew: p.id })}
          onOpenPlan={(id) => go({ plan: id })}
          onNew={() => go({})}
          onAll={() => go({ tab: "all" })}
        />
      ) : renewId ? (
        <RenewLoader key={renewId} planId={renewId} onSaved={(p) => go({ plan: p.id })} onCancel={() => go({ plan: renewId })} />
      ) : reviseId ? (
        <ReviseLoader key={reviseId} planId={reviseId} onSaved={(p) => go({ plan: p.id })} onCancel={() => go({ tab: "all" })} />
      ) : (
        <PlanBuilder key="new" onSaved={(p) => go({ plan: p.id })} />
      )}

      <CustomerDetailDrawer customerId={customerId} onClose={() => setCustomerId(null)} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Saved plan: the card with Send Link / Mark Cash Paid               */
/* ------------------------------------------------------------------ */

export function SavedPlan({
  planId,
  onOpenCustomer,
  onRevise,
  onRenew,
  onOpenPlan,
  onNew,
  onAll,
  adminActions = false,
}: {
  planId: string;
  onOpenCustomer: (id: string) => void;
  onRevise: (p: CustomPlan) => void;
  onRenew: (p: CustomPlan) => void;
  onOpenPlan: (id: string) => void;
  /** Manager only — admin doesn't build new carts here. */
  onNew?: () => void;
  onAll: () => void;
  /** Admin: Renew / Refund (and finishing a renewal) on the reused card. */
  adminActions?: boolean;
}) {
  const { data, isLoading, isError, isFetching, refetch } = useQuery({ queryKey: ["custom-plan", planId], queryFn: () => customPlanApi.get(planId) });
  if (isLoading) return <PageLoader />;
  if (isError || !data) return <ErrorState message="Couldn't load this plan." busy={isFetching} onRetry={() => void refetch()} />;
  return (
    <div className="max-w-xl space-y-4">
      <CustomPlanCard
        plan={data}
        canAct={!adminActions}
        adminActions={adminActions}
        onOpenCustomer={onOpenCustomer}
        onRevise={onRevise}
        onRenew={onRenew}
        onOpenPlan={onOpenPlan}
      />
      <div className="flex flex-wrap gap-2">
        {onNew && (
          <Button variant="outline" className="min-h-11" onClick={onNew}>
            <Plus className="h-4 w-4" /> New Custom Plan
          </Button>
        )}
        <Button variant="outline" className="min-h-11" onClick={onAll}>
          All Plans
        </Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Revise: load the cart into the builder                              */
/* ------------------------------------------------------------------ */

export function ReviseLoader({ planId, onSaved, onCancel }: { planId: string; onSaved: (p: CustomPlan) => void; onCancel: () => void }) {
  const { data, isLoading, isError, isFetching, refetch } = useQuery({ queryKey: ["custom-plan", planId], queryFn: () => customPlanApi.get(planId) });
  if (isLoading) return <PageLoader />;
  if (isError || !data) return <ErrorState message="Couldn't load this plan." busy={isFetching} onRetry={() => void refetch()} />;
  if (data.status !== "draft" && data.status !== "awaiting_payment") {
    return (
      <EmptyState
        icon={Layers}
        title="Can't Revise This Plan"
        description={data.status === "cancelled" ? "It was cancelled." : "It's already paid — passes are handled car by car now."}
        action={
          <Button variant="outline" onClick={onCancel}>
            All Plans
          </Button>
        }
      />
    );
  }
  return <PlanBuilder key={`${data.id}:${data.revision}`} revising={data} onSaved={onSaved} onCancel={onCancel} />;
}

/* ------------------------------------------------------------------ */
/* Renew: a paid cart's cars and counts, prefilled into the builder     */
/* ------------------------------------------------------------------ */

export function RenewLoader({ planId, onSaved, onCancel }: { planId: string; onSaved: (p: CustomPlan) => void; onCancel: () => void }) {
  const { data, isLoading, isError, isFetching, refetch } = useQuery({ queryKey: ["custom-plan", planId], queryFn: () => customPlanApi.get(planId) });
  if (isLoading) return <PageLoader />;
  if (isError || !data) return <ErrorState message="Couldn't load this plan." busy={isFetching} onRetry={() => void refetch()} />;
  if (data.status !== "active" && data.status !== "needs_review") {
    return (
      <EmptyState
        icon={Layers}
        title="Can't Renew This Plan"
        description="Only a paid plan can be renewed."
        action={
          <Button variant="outline" onClick={onCancel}>
            Back
          </Button>
        }
      />
    );
  }
  if (data.renewal_cart_id) {
    return (
      <EmptyState
        icon={Layers}
        title="Already Renewed"
        description="Open its renewal to send the link or take cash."
        action={
          <Button variant="outline" onClick={onCancel}>
            Back
          </Button>
        }
      />
    );
  }
  return <PlanBuilder key={`renew:${data.id}`} renewing={data} onSaved={onSaved} onCancel={onCancel} />;
}

/* ------------------------------------------------------------------ */
/* The builder                                                         */
/* ------------------------------------------------------------------ */

/** The cars a renewal starts from: the ones that got a pass (never a
 *  refunded or skipped car) — the server's own default. */
function renewableCars(plan: CustomPlan) {
  const live = plan.cars.filter((c) => c.status === "active" && c.vehicle_id && c.subscription_id);
  return live.length ? live : plan.cars.filter((c) => c.status !== "refunded" && c.status !== "skipped");
}

function carsFromPlan(plan: CustomPlan, cars = plan.cars): DraftCar[] {
  return cars.map((c) => ({
    key: key(),
    mode: c.vehicle_id ? "saved" : "new",
    vehicleId: c.vehicle_id || "",
    plate: c.registration_number || "",
    vehicleType: c.vehicle_type || "",
    items: c.items.map((i) => ({ key: key(), serviceId: i.service_id, count: i.count })),
  }));
}

function PlanBuilder({
  revising: revisingCart,
  renewing,
  onSaved,
  onCancel,
}: {
  revising?: CustomPlan;
  /** A paid cart being renewed: same customer, its cars prefilled. */
  renewing?: CustomPlan;
  onSaved: (p: CustomPlan) => void;
  onCancel?: () => void;
}) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  // The customer is fixed for a revision and for a renewal alike.
  const fixed = revisingCart || renewing;
  const revising = revisingCart;

  const [name, setName] = useState(fixed?.customer_name || "");
  const [phone, setPhone] = useState(fixed?.customer_phone || "");
  const [picked, setPicked] = useState<{ id: string; phone: string } | null>(
    fixed ? { id: fixed.customer_id, phone: fixed.customer_phone || "" } : null,
  );
  const [cars, setCars] = useState<DraftCar[]>(() =>
    revising ? carsFromPlan(revising) : renewing ? carsFromPlan(renewing, renewableCars(renewing)) : [newCar()],
  );
  const [discount, setDiscount] = useState(revising?.discount_amount ? String(Math.round(revising.discount_amount)) : "");
  const [note, setNote] = useState(revising?.note || "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [tried, setTried] = useState(false);

  // Typing a different number after picking a customer drops the pick.
  useEffect(() => {
    if (picked && !fixed && phone !== picked.phone) setPicked(null);
  }, [phone, picked, fixed]);
  const pickedId = picked?.id || "";
  const cleanPhone = validateIndianMobile(phone);

  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const { data: servicesData, isError: servicesFailed, refetch: refetchServices } = useQuery({
    queryKey: ["services-for-custom-plan"],
    queryFn: () => catalogApi.services({ page_size: 100 }),
  });
  const { data: customer } = useQuery({
    queryKey: ["customer-360", pickedId],
    queryFn: () => crmApi.customer360(pickedId),
    enabled: !!pickedId,
    retry: false,
  });
  // Saved cars (and pricing them) need a customer this center knows.
  const customerId = fixed ? fixed.customer_id : customer ? pickedId : "";

  const types = useMemo(() => (vehicleTypes || []).filter((t) => t.is_active !== false).sort((a, b) => a.display_order - b.display_order), [vehicleTypes]);
  const typeName = (id: string) => toTitle(types.find((t) => t.id === id)?.name) || "";
  const savedVehicles = customer?.vehicles ?? [];
  // Cars already holding a live plan — one car carries one plan. A
  // renewal may name the cars whose passes it renews.
  const renewedCars = useMemo(
    () =>
      new Set(
        [
          ...(renewing?.cars ?? []).filter((c) => c.subscription_id),
          ...(revising?.renewal_of ? revising.cars.filter((c) => c.renews_subscription_id) : []),
        ]
          .map((c) => c.vehicle_id)
          .filter(Boolean) as string[],
      ),
    [renewing, revising],
  );
  const carsOnPlan = useMemo(
    () =>
      new Set(
        (customer?.subscriptions ?? [])
          .filter((s) => (s.effective_status === "active" || s.effective_status === "scheduled") && s.vehicle_id && !renewedCars.has(s.vehicle_id))
          .map((s) => s.vehicle_id as string),
      ),
    [customer, renewedCars],
  );
  // Saved cars drop out when the customer is unpicked.
  useEffect(() => {
    if (customerId || fixed) return;
    setCars((prev) => (prev.some((c) => c.mode === "saved") ? prev.map((c) => (c.mode === "saved" ? { ...c, mode: "new", vehicleId: "" } : c)) : prev));
  }, [customerId, fixed]);

  const carType = (c: DraftCar) => (c.mode === "saved" ? savedVehicles.find((v) => v.id === c.vehicleId)?.vehicle_type || c.vehicleType : c.vehicleType);
  const menuFor = (typeId: string) => (typeId ? baseGroups((servicesData?.data || []).filter((s) => s.is_active !== false), typeId).map((g) => g.primary) : []);

  const update = (carKey: string, fn: (c: DraftCar) => DraftCar) => setCars((prev) => prev.map((c) => (c.key === carKey ? fn(c) : c)));

  // What goes to the server (preview / create / revise) — only complete parts.
  const payloadCars: CustomPlanCarInput[] = cars.map((c) => ({
    ...(c.mode === "saved" && c.vehicleId ? { vehicle_id: c.vehicleId } : { vehicle_type: c.vehicleType || undefined, registration_number: c.plate.trim() || undefined }),
    items: c.items.filter((i) => i.serviceId).map((i) => ({ service_id: i.serviceId, count: i.count })),
  }));
  const carsReady = cars.length > 0 && cars.every((c, i) => (c.mode === "saved" ? !!c.vehicleId : !!c.vehicleType) && payloadCars[i].items.length > 0);
  const discountNum = discount.trim() === "" ? 0 : Math.round(Number(discount)) || 0;
  // The customer named exactly as Save names them (phone for a new cart), so
  // the preview refuses what Save would (e.g. a car already on a plan).
  // A renewal (new, or a saved renewal being revised) previews with
  // `renewal_of`: the server prices it for the plan's customer and lets the
  // cars through whose own passes it renews — exactly what renew / revise
  // will accept; a car on any other live plan is still refused.
  const renewalOf = renewing?.id || (revising?.renewal_of ? revising.id : "");
  const previewBody = JSON.stringify(
    renewalOf
      ? { renewal_of: renewalOf, cars: payloadCars, discount_amount: discountNum }
      : {
          ...(revising || !cleanPhone ? { customer_id: customerId || undefined } : { customer_phone: cleanPhone }),
          cars: payloadCars,
          discount_amount: discountNum,
        },
  );
  const previewKey = useDebouncedValue(previewBody, 350);
  const preview = useQuery({
    queryKey: ["custom-plan-preview", previewKey],
    queryFn: () => customPlanApi.preview(JSON.parse(previewKey)),
    enabled: carsReady && previewKey === previewBody,
    retry: false,
    placeholderData: keepPreviousData,
    staleTime: 10_000,
  });
  const priced: CustomPlanPreview | undefined = carsReady ? preview.data : undefined;
  const subtotal = priced?.subtotal ?? 0;
  const maxDiscount = isAdmin ? Math.floor(subtotal) : Math.floor((subtotal * MANAGER_MAX_DISCOUNT_PCT) / 100);
  const discountTooBig = !!priced && discountNum > maxDiscount;

  // Field problems (shown after the first Save attempt).
  const problems: Record<string, string> = {};
  if (!fixed) {
    if (name.trim().length < 2) problems.name = "Enter the customer's name.";
    if (!cleanPhone) problems.phone = "Enter a valid 10-digit mobile number.";
  }
  cars.forEach((c) => {
    if (c.mode === "saved" && !c.vehicleId) problems[`${c.key}:car`] = "Pick one of their cars.";
    if (c.mode === "new") {
      if (!c.plate.trim()) problems[`${c.key}:plate`] = "Enter the number plate.";
      if (!c.vehicleType) problems[`${c.key}:type`] = "Pick the car type.";
    }
    if (!c.items.some((i) => i.serviceId)) problems[`${c.key}:items`] = "Add at least one wash.";
  });
  const show = (k: string) => (tried ? problems[k] : undefined);

  const submit = async () => {
    setTried(true);
    setError("");
    if (Object.keys(problems).length || discountTooBig || submitting) return;
    setSubmitting(true);
    try {
      const saved = renewing
        ? await customPlanApi.renew(renewing.id, { cars: payloadCars, discount_amount: discountNum, note: note.trim() || undefined })
        : revising
        ? await customPlanApi.revise(revising.id, {
            expected_revision: revising.revision,
            cars: payloadCars,
            discount_amount: discountNum,
            note: note.trim(),
          })
        : await customPlanApi.create({
            // Phone + name like Sell A Plan: the server finds (or creates) the
            // account. A typeahead pick outside this center's history would
            // 404 as a customer_id, but is still found by phone.
            ...(cleanPhone ? { customer_phone: cleanPhone, customer_name: name.trim() || undefined } : { customer_id: customerId }),
            cars: payloadCars,
            discount_amount: discountNum,
            note: note.trim() || undefined,
          });
      queryClient.invalidateQueries({ queryKey: ["custom-plans"] });
      queryClient.setQueryData(["custom-plan", saved.id], saved);
      if (renewing) queryClient.invalidateQueries({ queryKey: ["custom-plan", renewing.id] });
      pushToast({
        tone: "success",
        title: renewing ? "Renewal Saved" : revising ? "Plan Updated" : "Plan Saved",
        message: "Now send the payment link or mark it paid in cash.",
      });
      onSaved(saved);
    } catch (err) {
      if (getErrorStatus(err) === 409) queryClient.invalidateQueries({ queryKey: ["custom-plan", revising?.id] });
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const previewError = carsReady && preview.isError && !preview.isFetching ? getErrorMessage(preview.error) : "";
  const discountError = discountTooBig
    ? `You can give at most ₹${maxDiscount} off${isAdmin ? "" : ` (${MANAGER_MAX_DISCOUNT_PCT}%)`}.`
    : previewError && /discount|% off/i.test(previewError)
      ? previewError
      : undefined;

  return (
    <div className="max-w-xl space-y-5">
      {/* 1 — Customer */}
      <section className="space-y-3 rounded-2xl border border-[var(--color-card-border)] bg-white p-4">
        <h2 className="text-sm font-semibold text-black">Customer</h2>
        {fixed ? (
          <>
            <p className="text-sm text-black">
              <span className="font-semibold">{fixed.customer_name || "Customer"}</span>
              {fixed.customer_phone ? <span className="text-gray-500"> · {fixed.customer_phone}</span> : null}
            </p>
            {renewing && (
              <p className="text-xs text-gray-500" data-testid="custom-plan-renewing">
                Renewing Plan #{renewing.id.slice(-6).toUpperCase()} — same cars and washes, at today&apos;s prices. Change anything below.
              </p>
            )}
          </>
        ) : (
          <>
            <CustomerNamePhoneFields
              name={name}
              phone={phone}
              onChangeName={setName}
              onChangePhone={setPhone}
              onPick={(u) => setPicked({ id: u.id, phone: u.phone || "" })}
              nameError={show("name")}
              phoneError={show("phone")}
              phoneHint={customerId ? "Existing customer — their saved cars are below." : pickedId ? "Existing customer." : "A new number creates their account."}
            />
          </>
        )}
      </section>

      {/* 2 — Cars and washes */}
      {cars.map((c, idx) => {
        const typeId = carType(c);
        const menu = menuFor(typeId);
        const pricedCar = priced?.cars[idx];
        const chosen = new Set(c.items.map((i) => i.serviceId).filter(Boolean));
        const usedSaved = new Set(cars.filter((o) => o.key !== c.key && o.mode === "saved").map((o) => o.vehicleId));
        return (
          <section key={c.key} className="space-y-3 rounded-2xl border border-[var(--color-card-border)] bg-white p-4" data-testid="custom-plan-builder-car">
            <div className="flex items-center justify-between gap-2">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-black">
                <Car className="h-4 w-4 text-gray-500" /> Car {idx + 1}
                {pricedCar && <span className="font-mono-num font-normal text-gray-500">· {rupees(pricedCar.price)}</span>}
              </h2>
              {cars.length > 1 && (
                <button
                  type="button"
                  onClick={() => setCars((prev) => prev.filter((o) => o.key !== c.key))}
                  className="flex h-11 w-11 items-center justify-center rounded-full text-gray-400 hover:bg-gray-100 hover:text-[var(--color-error)]"
                  aria-label={`Remove car ${idx + 1}`}
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              )}
            </div>

            {(customerId || fixed) && (
              <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Which car">
                {(
                  [
                    { id: "saved", label: "Saved Car" },
                    { id: "new", label: "New Car" },
                  ] as const
                ).map((o) => (
                  <button
                    key={o.id}
                    type="button"
                    role="radio"
                    aria-checked={c.mode === o.id}
                    onClick={() => update(c.key, (x) => ({ ...x, mode: o.id }))}
                    className={`min-h-11 rounded-xl border px-3 text-sm font-medium ${
                      c.mode === o.id ? "border-black bg-[var(--color-primary-light)] text-black" : "border-gray-200 text-gray-600 hover:border-gray-400"
                    }`}
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            )}

            {c.mode === "saved" ? (
              <Select
                label="Their Car"
                value={c.vehicleId}
                error={show(`${c.key}:car`)}
                onChange={(e) => update(c.key, (x) => ({ ...x, vehicleId: e.target.value, items: x.vehicleId === e.target.value ? x.items : [newItem()] }))}
              >
                <option value="">{savedVehicles.length ? "Pick A Car" : "No Saved Cars"}</option>
                {savedVehicles.map((v) => (
                  <option key={v.id} value={v.id} disabled={usedSaved.has(v.id) || carsOnPlan.has(v.id)}>
                    {[v.registration_number, toTitle(v.vehicle_type_name), [v.brand, v.model].filter(Boolean).join(" ")].filter(Boolean).join(" · ")}
                    {carsOnPlan.has(v.id) ? " (Has A Plan)" : ""}
                  </option>
                ))}
              </Select>
            ) : (
              <>
                <Input
                  label="Number Plate"
                  value={c.plate}
                  maxLength={20}
                  autoCapitalize="characters"
                  onChange={(e) => update(c.key, (x) => ({ ...x, plate: e.target.value.toUpperCase() }))}
                  placeholder="MP09AB1234"
                  error={show(`${c.key}:plate`)}
                />
                <div>
                  <p className="mb-1.5 text-sm font-medium text-black">Car Type</p>
                  <div className="flex flex-wrap gap-2">
                    {types.map((t) => (
                      <button
                        key={t.id}
                        type="button"
                        aria-pressed={c.vehicleType === t.id}
                        onClick={() => update(c.key, (x) => ({ ...x, vehicleType: t.id, items: x.vehicleType === t.id ? x.items : [newItem()] }))}
                        className={`flex min-h-11 items-center gap-2 rounded-xl border px-3.5 text-sm font-medium ${
                          c.vehicleType === t.id ? "border-black bg-[var(--color-primary-light)] text-black" : "border-gray-200 text-gray-700 hover:border-gray-400"
                        }`}
                      >
                        <VehicleIcon vehicleTypeId={t.id} className="h-4 w-4 text-gray-500" />
                        {toTitle(t.name)}
                      </button>
                    ))}
                  </div>
                  {show(`${c.key}:type`) && <p className="mt-1 text-xs text-[var(--color-error)]">{show(`${c.key}:type`)}</p>}
                </div>
              </>
            )}

            {/* Washes on this car */}
            <div>
              <p className="mb-1.5 text-sm font-medium text-black">Washes</p>
              {!typeId ? (
                <p className="text-xs text-gray-500">Pick the car first.</p>
              ) : servicesFailed && !servicesData ? (
                <p className="text-sm text-gray-500">
                  Couldn&apos;t load the services.{" "}
                  <button type="button" className="font-semibold underline" onClick={() => void refetchServices()}>
                    Try Again
                  </button>
                </p>
              ) : (
                <ul className="space-y-2">
                  {c.items.map((it, j) => {
                    const line = pricedCar?.items.find((p) => p.service_id === it.serviceId);
                    return (
                      <li key={it.key} className="rounded-xl border border-gray-200 p-2.5">
                        <div className="flex items-start gap-2">
                          <div className="min-w-0 flex-1">
                            <Select
                              value={it.serviceId}
                              aria-label={`Service ${j + 1}`}
                              onChange={(e) => update(c.key, (x) => ({ ...x, items: x.items.map((y) => (y.key === it.key ? { ...y, serviceId: e.target.value } : y)) }))}
                            >
                              <option value="">Pick A Service</option>
                              {menu.map((s) => (
                                <option key={s.id} value={s.id} disabled={chosen.has(s.id) && s.id !== it.serviceId}>
                                  {toTitle(s.name)}
                                </option>
                              ))}
                            </Select>
                          </div>
                          {c.items.length > 1 && (
                            <button
                              type="button"
                              onClick={() => update(c.key, (x) => ({ ...x, items: x.items.filter((y) => y.key !== it.key) }))}
                              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-gray-400 hover:bg-gray-100 hover:text-[var(--color-error)]"
                              aria-label="Remove this wash"
                            >
                              <Trash2 className="h-4 w-4" />
                            </button>
                          )}
                        </div>
                        <div className="mt-2 flex items-center justify-between gap-2">
                          <Counter
                            value={it.count}
                            label="washes"
                            onChange={(n) => update(c.key, (x) => ({ ...x, items: x.items.map((y) => (y.key === it.key ? { ...y, count: n } : y)) }))}
                          />
                          <span className="text-right font-mono-num text-xs text-gray-500">
                            {line ? (
                              <>
                                {it.count} × {rupees(line.unit_price)} = <span className="font-semibold text-black">{rupees(line.line_total)}</span>
                              </>
                            ) : (
                              `${it.count} wash${it.count === 1 ? "" : "es"}`
                            )}
                          </span>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
              {show(`${c.key}:items`) && <p className="mt-1 text-xs text-[var(--color-error)]">{show(`${c.key}:items`)}</p>}
              {typeId && c.items.length < Math.min(MAX_ITEMS, menu.length) && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="mt-2 min-h-11"
                  onClick={() => update(c.key, (x) => ({ ...x, items: [...x.items, newItem()] }))}
                >
                  <Plus className="h-4 w-4" /> Add Another Service
                </Button>
              )}
            </div>
          </section>
        );
      })}

      {cars.length < MAX_CARS && (
        <Button
          variant="outline"
          className="min-h-11 w-full"
          onClick={() => setCars((prev) => [...prev, newCar(customerId && savedVehicles.length ? "saved" : "new")])}
        >
          <Plus className="h-4 w-4" /> Add Another Car
        </Button>
      )}

      {/* 3 — Discount, note, price */}
      <section className="space-y-3 rounded-2xl border border-[var(--color-card-border)] bg-white p-4">
        <Input
          label="Discount (₹, Optional)"
          inputMode="numeric"
          value={discount}
          onChange={(e) => setDiscount(e.target.value.replace(/\D/g, "").slice(0, 6))}
          placeholder="0"
          error={discountError}
          hint={!discountError && priced ? `Up to ₹${maxDiscount}${isAdmin ? "" : ` (${MANAGER_MAX_DISCOUNT_PCT}%)`}.` : undefined}
        />
        <Input label="Note (Optional)" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Office fleet — 3 cars" />

        <div className="rounded-xl border border-[var(--color-card-border)] bg-gray-50 p-3.5" data-testid="custom-plan-price">
          {!carsReady ? (
            <p className="text-sm text-gray-500">Pick each car and at least one wash to see the price.</p>
          ) : previewError && !discountError ? (
            <p className="text-sm text-[var(--color-error)]">{previewError}</p>
          ) : discountError ? (
            <p className="text-sm text-[var(--color-text-secondary)]">Lower the discount to see the total.</p>
          ) : !priced ? (
            <p className="flex items-center gap-2 text-sm text-gray-500">
              <Spinner className="h-4 w-4" /> Working out the price…
            </p>
          ) : (
            <div className={preview.isFetching ? "opacity-60" : ""}>
              <ul className="space-y-1 text-sm">
                {priced.cars.map((pc, i) => (
                  <li key={i} className="flex items-baseline justify-between gap-3">
                    <span className="min-w-0 text-gray-700">
                      <span className="font-mono-num">{pc.registration_number || cars[i]?.plate || `Car ${i + 1}`}</span>
                      {pc.vehicle_type_name || typeName(cars[i]?.vehicleType || "") ? ` · ${toTitle(pc.vehicle_type_name) || typeName(cars[i]?.vehicleType || "")}` : ""}
                      <span className="text-gray-500"> · {pc.washes} wash{pc.washes === 1 ? "" : "es"}</span>
                    </span>
                    <span className="shrink-0 font-mono-num">{rupees(pc.price)}</span>
                  </li>
                ))}
              </ul>
              {priced.discount_amount > 0 && (
                <div className="mt-2 space-y-1 border-t border-gray-200 pt-2 text-sm">
                  <p className="flex justify-between gap-3 text-gray-600">
                    <span>Subtotal</span>
                    <span className="font-mono-num">{rupees(priced.subtotal)}</span>
                  </p>
                  <p className="flex justify-between gap-3 text-[var(--color-success)]">
                    <span>Discount</span>
                    <span className="font-mono-num">−{rupees(priced.discount_amount)}</span>
                  </p>
                </div>
              )}
              <p className="mt-2 flex items-baseline justify-between gap-3 border-t border-gray-200 pt-2">
                <span className="text-sm font-semibold text-black">Total</span>
                <span className="font-mono-num text-2xl font-bold text-black">{rupees(priced.total_amount)}</span>
              </p>
              {renewing ? (
                <>
                  <p className="mt-1 text-sm font-semibold text-black">{priced.period_days} More Days Per Car</p>
                  <p className="text-xs text-gray-500">Each car starts the day after its current pass ends. Add-ons are paid at each booking.</p>
                </>
              ) : (
                <>
                  <p className="mt-1 text-sm font-semibold text-black">Valid {priced.period_days} Days From Payment</p>
                  <p className="text-xs text-gray-500">All cars start the same day. Add-ons are paid at each booking.</p>
                </>
              )}
            </div>
          )}
        </div>
      </section>

      {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
      <div className="flex gap-2">
        {onCancel && (
          <Button variant="outline" className="min-h-11 flex-1" onClick={onCancel}>
            Back
          </Button>
        )}
        <Button className="min-h-11 flex-1" isLoading={submitting} disabled={submitting || discountTooBig} onClick={submit}>
          {renewing ? "Save Renewal" : revising ? "Save Changes" : "Save Plan"}
        </Button>
      </div>
      <p className="text-xs text-gray-500">
        {revising && revising.payment_link ? "Saving voids the link already sent — send the new one after." : "Next: send the payment link, or mark it paid in cash."}
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* All plans                                                           */
/* ------------------------------------------------------------------ */

const STATUS_FILTERS: { label: string; value: "" | CustomPlanStatus }[] = [
  { label: "All", value: "" },
  { label: "Awaiting Payment", value: "awaiting_payment" },
  { label: "Not Sent Yet", value: "draft" },
  { label: "Active", value: "active" },
  { label: "Needs Review", value: "needs_review" },
  { label: "Refunded", value: "refunded" },
  { label: "Cancelled", value: "cancelled" },
];

export function CustomPlanList({
  onOpenCustomer,
  onRevise,
  onRenew,
  onOpenPlan,
  readOnly = false,
  adminActions = false,
  centerId,
  centerNames,
}: {
  onOpenCustomer?: (id: string) => void;
  onRevise?: (p: CustomPlan) => void;
  onRenew?: (p: CustomPlan) => void;
  onOpenPlan?: (id: string) => void;
  readOnly?: boolean;
  /** Admin (with readOnly): Renew Plan / Refund Car, and finishing a renewal. */
  adminActions?: boolean;
  /** Admin: one center only. */
  centerId?: string;
  centerNames?: Record<string, string>;
}) {
  const [status, setStatus] = useState<"" | CustomPlanStatus>("");
  const [page, setPage] = useState(1);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    setPage(1);
  }, [status, centerId]);
  const { data, isLoading, isFetching, isError, refetch } = useQuery({
    queryKey: ["custom-plans", status, centerId || "", page],
    queryFn: () => customPlanApi.list({ status: status || undefined, service_center_id: centerId || undefined, page, page_size: 12 }),
    placeholderData: keepPreviousData,
  });
  const rows = data?.data ?? [];
  return (
    <div className="space-y-4">
      <div className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.label}
            type="button"
            onClick={() => setStatus(f.value)}
            aria-pressed={status === f.value}
            className={`min-h-11 shrink-0 rounded-full border px-3.5 text-sm font-medium transition-colors sm:min-h-9 ${
              status === f.value ? "border-black bg-[var(--color-primary-light)] text-black" : "border-gray-200 bg-white text-gray-600 hover:border-gray-400"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>
      {isLoading ? (
        <PageLoader />
      ) : isError && !data ? (
        <ErrorState message="Couldn't load custom plans." busy={isFetching} onRetry={() => void refetch()} />
      ) : !rows.length ? (
        <EmptyState icon={Layers} title="No Custom Plans" description={status ? "Nothing with this status." : "Build one from New Plan."} />
      ) : (
        <div className={`grid grid-cols-1 gap-3 lg:grid-cols-2 ${isFetching ? "opacity-70" : ""}`}>
          {rows.map((p) => (
            <CustomPlanCard
              key={p.id}
              plan={p}
              canAct={!readOnly}
              adminActions={adminActions}
              centerName={centerNames && p.service_center_id ? centerNames[p.service_center_id] : undefined}
              onOpenCustomer={onOpenCustomer}
              onRevise={readOnly && !adminActions ? undefined : onRevise}
              onRenew={readOnly && !adminActions ? undefined : onRenew}
              onOpenPlan={onOpenPlan}
            />
          ))}
        </div>
      )}
      {data?.meta && <Pager page={page} totalPages={data.meta.total_pages} total={data.meta.total} onPage={setPage} busy={isFetching} />}
    </div>
  );
}
