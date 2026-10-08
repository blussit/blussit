import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Clock, IndianRupee, MapPin, XCircle } from "lucide-react";
import { adminBookingPolicyApi, adminPricingApi } from "../../api/admin";
import { paymentApi } from "../../api/payment";
import { CollectionsReportCard } from "../../components/shared/CollectionsReport";
import { SettingsHistory } from "../../components/admin/SettingsHistory";
import { walletApi } from "../../api/wallet";
import { getErrorMessage } from "../../lib/api-client";
import { useToast } from "../../context/ToastContext";
import { useConfirm } from "../../context/ConfirmContext";
import { Badge, Button, Card, CardBody, DataTable, ErrorState, Input, PageLoader } from "../../components/ui";
import { format } from "../../lib/date";
import { toTitle } from "../../lib/titleCase";
import type { BookingPolicy, PricingConfig, WithdrawalRequest } from "../../types";

/**
 * One page for every number an admin can turn. Three rules keep it
 * readable: every field says in one line what it changes and where; every
 * card shows who changed it last (with the full history a tap away); and
 * nothing here is a draft — a saved value is live everywhere at once
 * (customer app, guest booking page, WhatsApp bot, manager and captain
 * panels), because they all read the same policy.
 */

type NumericPolicyKey =
  | "slot_duration_minutes"
  | "slot_booking_cutoff_minutes"
  | "max_advance_days"
  | "max_vehicles_per_booking"
  | "payment_window_minutes"
  | "payment_reminder_minutes_before"
  | "captain_travel_buffer_minutes"
  | "late_start_grace_minutes"
  | "late_assignment_grace_minutes"
  | "captain_start_lockout_hours"
  | "arrival_to_start_tolerance_minutes"
  | "delay_tolerance_minutes"
  | "photo_geofence_radius_m"
  | "repeat_reminder_days"
  | "cancellation_fee_1_to_4h"
  | "cancellation_fee_under_1h"
  | "cancellation_fee_after_captain_left";

interface PolicyField {
  key: NumericPolicyKey;
  label: string;
  unit: string;
  /** What changes when this moves — plain words, one line. */
  effect: string;
  min?: number;
  max?: number;
}

const POLICY_GROUPS: { title: string; appliesTo: string; fields: PolicyField[] }[] = [
  {
    title: "Booking & Slots",
    appliesTo: "Customer App · Guest Booking Page · WhatsApp Bot · Manager Bookings",
    fields: [
      { key: "slot_duration_minutes", label: "Slot Length", unit: "Min", effect: "How long each bookable slot is. A center can override this with its own value.", min: 5 },
      { key: "slot_booking_cutoff_minutes", label: "Booking Cutoff", unit: "Min Before Slot Ends", effect: "A slot stops accepting bookings this close to its end.", min: 0 },
      { key: "max_advance_days", label: "Advance Window", unit: "Days", effect: "How far ahead a customer can book (today included).", min: 1, max: 60 },
      { key: "max_vehicles_per_booking", label: "Vehicles Per Visit", unit: "Cars", effect: "How many of their cars a customer can add to one visit (one slot, one captain, one payment).", min: 1, max: 10 },
      { key: "payment_window_minutes", label: "Online Payment Hold", unit: "Min", effect: "How long an unpaid 'pay online' booking keeps its slot before it's released.", min: 5, max: 240 },
      { key: "payment_reminder_minutes_before", label: "Payment Reminder", unit: "Min Before The Hold Ends", effect: "One 'finish your payment' nudge, in-app and on WhatsApp. 0 turns it off.", min: 0, max: 120 },
    ],
  },
  {
    title: "Captain Timing",
    appliesTo: "Captain App · Manager Queue Flags · Captain Pay",
    fields: [
      { key: "captain_travel_buffer_minutes", label: "Travel Buffer", unit: "Min", effect: "Blocked before and after each job so a captain isn't booked back-to-back across town.", min: 0 },
      { key: "late_start_grace_minutes", label: "Late-Start Grace", unit: "Min", effect: "Past the slot, a start within this is 'late' (25% fee penalty); beyond it 'very late' (50%).", min: 0 },
      { key: "late_assignment_grace_minutes", label: "Last-Minute Assignment Grace", unit: "Min", effect: "A captain handed a job after its slot began gets this long to head out before he counts as late.", min: 0, max: 120 },
      { key: "captain_start_lockout_hours", label: "Start Lockout", unit: "Hours After Grace", effect: "After this the captain can't start at all — the manager must reschedule or reassign.", min: 1 },
      { key: "arrival_to_start_tolerance_minutes", label: "Reached → Started Gap", unit: "Min", effect: "Flags the manager when a captain has 'reached' but not started the wash within this.", min: 1 },
      { key: "delay_tolerance_minutes", label: "Wash Overrun Tolerance", unit: "Min", effect: "A wash running this far past its planned time is flagged as delayed.", min: 0 },
    ],
  },
  {
    title: "Late Cancellation Charge",
    appliesTo: "Customer Cancel · Staff Cancel (At The Customer's Request) · Cancellation Policy Page",
    fields: [
      { key: "cancellation_fee_1_to_4h", label: "Cancelled 1–4 Hours Before", unit: "₹", effect: "Put on the customer's account and added to their next booking. More than 4 hours before is always free.", min: 0, max: 2000 },
      { key: "cancellation_fee_under_1h", label: "Cancelled Under 1 Hour Before", unit: "₹", effect: "Same — for a cancel less than an hour before the slot (or after it started).", min: 0, max: 2000 },
      { key: "cancellation_fee_after_captain_left", label: "Cancelled After The Captain Left", unit: "₹", effect: "Same — once the captain is on the way. 0 turns a tier off.", min: 0, max: 2000 },
    ],
  },
  {
    title: "Checks & Reminders",
    appliesTo: "Captain App · WhatsApp · Customer Notifications",
    fields: [
      { key: "photo_geofence_radius_m", label: "Photo Geofence", unit: "Metres", effect: "Before/after photos and the 'reached' tap taken farther than this from the address are flagged (never blocked).", min: 10 },
      { key: "repeat_reminder_days", label: "'Time For A Wash?' After", unit: "Days", effect: "Days after a customer's last wash before a gentle reminder — only if nothing is booked and no pass is live, at most once a month.", min: 3, max: 365 },
    ],
  },
];

const POLICY_LABELS: Record<string, string> = Object.fromEntries(
  POLICY_GROUPS.flatMap((g) => g.fields.map((f) => [f.key, f.label])),
);

export default function AdminPricingPage() {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const [perKm, setPerKm] = useState("");
  const [captainFee, setCaptainFee] = useState("");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [freeKm, setFreeKm] = useState("");
  const [customerRate, setCustomerRate] = useState("");
  const [distanceSaved, setDistanceSaved] = useState(false);
  const [distanceError, setDistanceError] = useState<string | null>(null);

  const [policyForm, setPolicyForm] = useState<Partial<Record<NumericPolicyKey, string>>>({});
  const [policySaved, setPolicySaved] = useState(false);
  const [policyError, setPolicyError] = useState<string | null>(null);

  const confirm = useConfirm();
  const {
    data: config,
    isLoading,
    isError: configFailed,
    isFetching: configFetching,
    refetch: refetchConfig,
  } = useQuery({ queryKey: ["pricing-config"], queryFn: adminPricingApi.get });
  const {
    data: policy,
    isLoading: policyLoading,
    isError: policyFailed,
    isFetching: policyFetching,
    refetch: refetchPolicy,
  } = useQuery({ queryKey: ["booking-policy"], queryFn: adminBookingPolicyApi.get });
  const {
    data: withdrawals,
    isLoading: withdrawalsLoading,
    error: withdrawalsError,
    refetch: refetchWithdrawals,
  } = useQuery({
    queryKey: ["pending-withdrawals"],
    queryFn: () => walletApi.pendingWithdrawals({ page: 1, page_size: 50 }),
  });

  // Approved = already taken out of the wallet, waiting for the bank transfer.
  const {
    data: payouts,
    isLoading: payoutsLoading,
    error: payoutsError,
    refetch: refetchPayouts,
  } = useQuery({
    queryKey: ["pending-withdrawals", "approved"],
    queryFn: () => walletApi.pendingWithdrawals({ status: "approved", page: 1, page_size: 50 }),
  });

  const invalidatePolicy = () => {
    queryClient.invalidateQueries({ queryKey: ["booking-policy"] });
    queryClient.invalidateQueries({ queryKey: ["settings-history", "booking_policy"] });
  };

  // The endpoint requires the two captain-pay fields on every save, so a
  // section sends their LOADED values when it doesn't change them; the
  // distance fields are optional there and are sent only when changed (the
  // server keeps the stored ones). Never a save without a loaded config —
  // that used to write defaults (₹0 fee, 5 km free) over the live values.
  const savePricing = (patch: Partial<Omit<PricingConfig, "updated_at">>) => {
    if (!config) return Promise.reject(new Error("The current pricing didn't load — reload it before saving."));
    return adminPricingApi.set({
      per_km_rate: Number(patch.per_km_rate ?? config.per_km_rate),
      default_captain_service_fee: Number(patch.default_captain_service_fee ?? config.default_captain_service_fee),
      ...(patch.customer_free_km !== undefined ? { customer_free_km: patch.customer_free_km } : {}),
      ...(patch.customer_per_km_rate !== undefined ? { customer_per_km_rate: patch.customer_per_km_rate } : {}),
    } as Omit<PricingConfig, "updated_at">);
  };
  const invalidatePricing = () => {
    queryClient.invalidateQueries({ queryKey: ["pricing-config"] });
    queryClient.invalidateQueries({ queryKey: ["settings-history", "pricing_config"] });
  };

  const saveMutation = useMutation({
    mutationFn: () =>
      savePricing({
        ...(perKm !== "" ? { per_km_rate: Number(perKm) } : {}),
        ...(captainFee !== "" ? { default_captain_service_fee: Number(captainFee) } : {}),
      }),
    onSuccess: () => {
      invalidatePricing();
      setPerKm("");
      setCaptainFee("");
      setSaved(true);
      setError(null);
      setTimeout(() => setSaved(false), 2500);
    },
    onError: (e) => setError(getErrorMessage(e)),
  });

  const saveDistanceMutation = useMutation({
    mutationFn: () =>
      savePricing({
        ...(freeKm !== "" ? { customer_free_km: Number(freeKm) } : {}),
        ...(customerRate !== "" ? { customer_per_km_rate: Number(customerRate) } : {}),
      }),
    onSuccess: () => {
      invalidatePricing();
      setFreeKm("");
      setCustomerRate("");
      setDistanceSaved(true);
      setDistanceError(null);
      setTimeout(() => setDistanceSaved(false), 2500);
    },
    onError: (e) => setDistanceError(getErrorMessage(e)),
  });

  const savePolicyMutation = useMutation({
    mutationFn: () => {
      // Only fields the admin actually typed into are sent — an empty box
      // means "leave it as it is", never "set it to zero".
      const payload: Partial<BookingPolicy> = {};
      for (const [key, raw] of Object.entries(policyForm)) {
        if (raw !== undefined && raw !== "") (payload as Record<string, number>)[key] = Number(raw);
      }
      return adminBookingPolicyApi.set(payload);
    },
    onSuccess: () => {
      invalidatePolicy();
      setPolicyForm({});
      setPolicySaved(true);
      setPolicyError(null);
      setTimeout(() => setPolicySaved(false), 2500);
    },
    onError: (e) => setPolicyError(getErrorMessage(e)),
  });

  const reviewMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "approved" | "rejected" | "paid"; amount?: number }) => walletApi.reviewWithdrawal(id, status),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pending-withdrawals"] }),
    onError: (e) => pushToast({ tone: "error", title: getErrorMessage(e) }),
  });

  // Instant on/off switches — deliberately not bundled into the "Save"
  // form; a feature switch should flip the moment it's tapped.
  const toggleMutation = useMutation({
    mutationFn: (patch: Partial<BookingPolicy>) => adminBookingPolicyApi.set(patch),
    onSuccess: invalidatePolicy,
    // A refused switch used to just snap back with no explanation.
    onError: (e) => pushToast({ tone: "error", title: getErrorMessage(e) }),
  });

  if (isLoading || policyLoading) return <PageLoader />;
  const pricingUnavailable = configFailed && !config;
  const policyUnavailable = policyFailed && !policy;
  const reviewWithdrawal = async (w: WithdrawalRequest, status: "approved" | "rejected" | "paid") => {
    if (reviewMutation.isPending) return;
    const ok = await confirm(
      status === "approved"
        ? { title: `Approve ₹${w.amount} Withdrawal?`, message: "The captain is told it's approved and the amount is set aside for payout.", confirmLabel: "Approve" }
        : status === "paid"
          ? { title: `Mark ₹${w.amount} As Paid?`, message: "Only after the bank transfer has gone through — the captain is told it's paid.", confirmLabel: "Mark Paid" }
          : w.status === "approved"
            ? { title: `Cancel ₹${w.amount} Payout?`, message: "The amount goes back into the captain's wallet and they're told it was cancelled.", confirmLabel: "Cancel Payout", tone: "danger" }
            : { title: `Reject ₹${w.amount} Withdrawal?`, message: "The request is closed and the captain is told it was rejected.", confirmLabel: "Reject", tone: "danger" }
    );
    if (ok) reviewMutation.mutate({ id: w.id, status, amount: w.amount });
  };

  const currentOf = (key: NumericPolicyKey) => (policy as Record<string, unknown> | undefined)?.[key];
  const dirtyCount = Object.values(policyForm).filter((v) => v !== undefined && v !== "").length;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Settings & Pricing</h1>
        <p className="mt-1 max-w-3xl text-sm text-[var(--color-text-secondary)]">
          Every value here is live across the whole system the moment you save it — customer app, guest booking page, WhatsApp bot,
          manager and captain panels all read the same rules. Each card shows who changed it last; open the history for every change.
        </p>
      </div>

      {/* ------------------------------------------------ Captain pay */}
      <Card>
        <CardBody>
          <h2 className="font-semibold text-[var(--color-text-primary)]">Captain Pay</h2>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            What a captain earns per booking, frozen on each booking when it's created: travel (₹ per km from the center) plus a flat
            service fee. A service can set its own fee in the Services page; this is the default.
          </p>
          {pricingUnavailable && (
            <ErrorState className="mt-4" message="Couldn't load the current pricing — saving is off until it loads." busy={configFetching} onRetry={() => void refetchConfig()} />
          )}
          <div className={`mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 ${pricingUnavailable ? "hidden" : ""}`}>
            <Input
              label="Travel Pay (₹ Per Km)"
              type="number"
              min={1}
              placeholder={String(config?.per_km_rate ?? 0)}
              value={perKm}
              onChange={(e) => setPerKm(e.target.value)}
              hint={`Now ₹${config?.per_km_rate ?? 0}/km — paid once per visit, however many cars are on it`}
            />
            <Input
              label="Service Fee (₹ Per Booking)"
              type="number"
              min={0}
              placeholder={String(config?.default_captain_service_fee ?? 0)}
              value={captainFee}
              onChange={(e) => setCaptainFee(e.target.value)}
              hint={`Now ₹${config?.default_captain_service_fee ?? 0} per car wash, unless the service overrides it`}
            />
          </div>
          {error && <p className="mt-2 text-sm text-[var(--color-error)]">{error}</p>}
          {saved && <p className="mt-2 text-sm text-[var(--color-success)]">Captain pay updated.</p>}
          <Button className="mt-4" isLoading={saveMutation.isPending} disabled={!config || (!perKm && !captainFee)} onClick={() => saveMutation.mutate()}>
            <IndianRupee className="h-4 w-4" /> Save Captain Pay
          </Button>
          <SettingsHistory
            settingKey="pricing_config"
            labels={{
              per_km_rate: "Travel Pay ₹/km",
              default_captain_service_fee: "Service Fee ₹",
              customer_free_km: "Customer Free Km",
              customer_per_km_rate: "Customer ₹/km",
            }}
          />
        </CardBody>
      </Card>

      {/* ------------------------------------------------ Customer distance charge */}
      <Card>
        <CardBody>
          <h2 className="font-semibold text-[var(--color-text-primary)]">Customer Distance Charge</h2>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            Applies only to services marked "Charge distance" in the Services page — once per visit, for the distance past the free km.
          </p>
          {pricingUnavailable && (
            <ErrorState className="mt-4" message="Couldn't load the current distance charge — saving is off until it loads." busy={configFetching} onRetry={() => void refetchConfig()} />
          )}
          <div className={`mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 ${pricingUnavailable ? "hidden" : ""}`}>
            <Input
              label="Free Km"
              type="number"
              min={0}
              step="any"
              placeholder={String(config?.customer_free_km ?? "")}
              value={freeKm}
              onChange={(e) => setFreeKm(e.target.value)}
              hint={`Now ${config?.customer_free_km ?? "—"} km by road from the center at no charge`}
            />
            <Input
              label="₹ Per Km After That"
              type="number"
              min={0}
              step="any"
              placeholder={String(config?.customer_per_km_rate ?? "")}
              value={customerRate}
              onChange={(e) => setCustomerRate(e.target.value)}
              hint={`Now ₹${config?.customer_per_km_rate ?? "—"}/km beyond the free km`}
            />
          </div>
          {distanceError && <p className="mt-2 text-sm text-[var(--color-error)]">{distanceError}</p>}
          {distanceSaved && <p className="mt-2 text-sm text-[var(--color-success)]">Distance charge updated.</p>}
          <Button className="mt-4" isLoading={saveDistanceMutation.isPending} disabled={!config || (!freeKm && !customerRate)} onClick={() => saveDistanceMutation.mutate()}>
            <MapPin className="h-4 w-4" /> Save Distance Charge
          </Button>
        </CardBody>
      </Card>

      {/* ------------------------------------------------ Booking rules */}
      <Card>
        <CardBody>
          <h2 className="font-semibold text-[var(--color-text-primary)]">Booking Rules</h2>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            Type a new value only where you want a change — empty boxes keep their current value, shown under each field.
          </p>

          {policyUnavailable && (
            <ErrorState className="mt-4" message="Couldn't load the booking rules — saving and switches are off until they load." busy={policyFetching} onRetry={() => void refetchPolicy()} />
          )}
          <div className={`mt-5 space-y-7 ${policyUnavailable ? "hidden" : ""}`}>
            {POLICY_GROUPS.map((group) => (
              <div key={group.title}>
                <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="text-sm font-bold uppercase tracking-wide text-[var(--color-text-primary)]">{group.title}</h3>
                  <span className="text-xs text-[var(--color-text-secondary)]">Applies To: {group.appliesTo}</span>
                </div>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  {group.fields.map((f) => (
                    <Input
                      key={f.key}
                      label={`${f.label} (${f.unit})`}
                      type="number"
                      min={f.min}
                      max={f.max}
                      placeholder={String(currentOf(f.key) ?? "")}
                      value={policyForm[f.key] ?? ""}
                      onChange={(e) => setPolicyForm({ ...policyForm, [f.key]: e.target.value })}
                      hint={`Now ${currentOf(f.key) ?? "—"} · ${f.effect}`}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>

          {policyError && <p className="mt-2 text-sm text-[var(--color-error)]">{policyError}</p>}
          {policySaved && <p className="mt-2 text-sm text-[var(--color-success)]">Booking rules updated — live everywhere.</p>}
          <Button className="mt-5" isLoading={savePolicyMutation.isPending} disabled={!policy || !dirtyCount} onClick={() => savePolicyMutation.mutate()}>
            <Clock className="h-4 w-4" /> {dirtyCount ? `Save ${dirtyCount} Change${dirtyCount > 1 ? "s" : ""}` : "Save Booking Rules"}
          </Button>

          {/* Switches — hidden when the rules didn't load: an unknown state
              shown as ON would flip the live setting OFF on the first tap. */}
          <div className={`mt-6 space-y-3 border-t border-gray-100 pt-5 ${policyUnavailable ? "hidden" : ""}`}>
            <Switch
              label="'Time For A Wash?' Reminders"
              description="A gentle nudge after a customer's last wash (days set above). WhatsApp goes out only through the approved marketing template, never to opted-out customers."
              checked={policy?.repeat_reminder_enabled !== false}
              pending={toggleMutation.isPending}
              onChange={(v) => toggleMutation.mutate({ repeat_reminder_enabled: v })}
            />
            <Switch
              label="Wallet Balance Gating"
              description="Blocks a captain below the minimum wallet balance from new assignments. Leave off until captains have a way to top up."
              checked={!!policy?.wallet_gating_enabled}
              pending={toggleMutation.isPending}
              onChange={(v) => toggleMutation.mutate({ wallet_gating_enabled: v })}
            />
          </div>

          <SettingsHistory settingKey="booking_policy" labels={{ ...POLICY_LABELS, wallet_gating_enabled: "Wallet Gating", repeat_reminder_enabled: "Repeat Reminders" }} />
        </CardBody>
      </Card>

      {/* ------------------------------------------------ Money */}
      <CollectionsReportCard
        title="Collections By Center"
        entityLabel="Center"
        queryKey="admin-collections"
        fetcher={(params) => paymentApi.adminCollections(params)}
      />

      <Card>
        <CardBody>
          <h2 className="font-semibold text-[var(--color-text-primary)]">Awaiting Payout</h2>
          <p className="mb-4 mt-1 text-sm text-[var(--color-text-secondary)]">Approved withdrawals — send the bank transfer, then mark it paid. Cancelling returns the money to the captain&apos;s wallet.</p>
          <DataTable<WithdrawalRequest>
            data={payouts?.data ?? []}
            isLoading={payoutsLoading}
            error={payoutsError}
            onRetry={() => void refetchPayouts()}
            emptyTitle="Nothing Awaiting Payout"
            emptyDescription="Approved withdrawals wait here until they're paid."
            columns={[
              { header: "Captain", accessor: (w) => (w.captain_name ? <span className="font-medium">{w.captain_name}</span> : <span className="font-mono-num text-xs">{w.captain_id}</span>) },
              { header: "Amount", accessor: (w) => <span className="font-mono-num font-semibold">₹{w.amount}</span> },
              { header: "Requested", accessor: (w) => format(w.created_at) },
              { header: "Status", accessor: () => <Badge tone="info">Approved</Badge> },
              {
                header: "Action",
                accessor: (w) => (
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      isLoading={reviewMutation.isPending && reviewMutation.variables?.id === w.id && reviewMutation.variables?.status === "paid"}
                      disabled={reviewMutation.isPending}
                      onClick={() => void reviewWithdrawal(w, "paid")}
                    >
                      <CheckCircle2 className="h-3.5 w-3.5" /> Mark Paid
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      isLoading={reviewMutation.isPending && reviewMutation.variables?.id === w.id && reviewMutation.variables?.status === "rejected"}
                      disabled={reviewMutation.isPending}
                      onClick={() => void reviewWithdrawal(w, "rejected")}
                    >
                      <XCircle className="h-3.5 w-3.5" /> Cancel Payout
                    </Button>
                  </div>
                ),
              },
            ]}
          />
        </CardBody>
      </Card>

      <Card>
        <CardBody>
          <h2 className="mb-4 font-semibold text-[var(--color-text-primary)]">Pending Withdrawal Requests</h2>
          <DataTable<WithdrawalRequest>
            data={withdrawals?.data ?? []}
            isLoading={withdrawalsLoading}
            error={withdrawalsError}
            onRetry={() => void refetchWithdrawals()}
            emptyTitle="No Pending Withdrawals"
            emptyDescription="Captain withdrawal requests will show up here for review."
            columns={[
              { header: "Captain", accessor: (w) => (w.captain_name ? <span className="font-medium">{w.captain_name}</span> : <span className="font-mono-num text-xs">{w.captain_id}</span>) },
              { header: "Amount", accessor: (w) => <span className="font-mono-num font-semibold">₹{w.amount}</span> },
              { header: "Requested", accessor: (w) => format(w.created_at) },
              { header: "Status", accessor: (w) => <Badge tone="warning">{toTitle(w.status)}</Badge> },
              {
                header: "Action",
                accessor: (w) => (
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      isLoading={reviewMutation.isPending && reviewMutation.variables?.id === w.id && reviewMutation.variables?.status === "approved"}
                      disabled={reviewMutation.isPending}
                      onClick={() => void reviewWithdrawal(w, "approved")}
                    >
                      <CheckCircle2 className="h-3.5 w-3.5" /> Approve
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      isLoading={reviewMutation.isPending && reviewMutation.variables?.id === w.id && reviewMutation.variables?.status === "rejected"}
                      disabled={reviewMutation.isPending}
                      onClick={() => void reviewWithdrawal(w, "rejected")}
                    >
                      <XCircle className="h-3.5 w-3.5" /> Reject
                    </Button>
                  </div>
                ),
              },
            ]}
          />
        </CardBody>
      </Card>
    </div>
  );
}

function Switch({
  label,
  description,
  checked,
  pending,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  pending: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <p className="text-sm font-semibold text-[var(--color-text-primary)]">
          {label} <span className={`ml-1.5 text-xs font-medium ${checked ? "text-[var(--color-success)]" : "text-gray-400"}`}>{checked ? "ON" : "OFF"}</span>
        </p>
        <p className="mt-0.5 max-w-xl text-xs text-[var(--color-text-secondary)]">{description}</p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={pending}
        onClick={() => onChange(!checked)}
        className={`relative h-7 w-12 shrink-0 rounded-full transition-colors disabled:opacity-60 ${checked ? "bg-[var(--color-success)]" : "bg-gray-300"}`}
      >
        <span className={`absolute top-1 h-5 w-5 rounded-full bg-white shadow transition-transform ${checked ? "translate-x-6" : "translate-x-1"}`} />
      </button>
    </div>
  );
}
