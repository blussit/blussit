import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Clock, IndianRupee, XCircle } from "lucide-react";
import { adminBookingPolicyApi, adminPricingApi } from "../../api/admin";
import { paymentApi } from "../../api/payment";
import { CollectionsReportCard } from "../../components/shared/CollectionsReport";
import { SettingsHistory } from "../../components/admin/SettingsHistory";
import { walletApi } from "../../api/wallet";
import { getErrorMessage } from "../../lib/api-client";
import { Badge, Button, Card, CardBody, DataTable, Input, PageLoader } from "../../components/ui";
import { format } from "../../lib/date";
import type { BookingPolicy, WithdrawalRequest } from "../../types";

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
  | "repeat_reminder_days";

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
    title: "Booking & slots",
    appliesTo: "Customer app · guest booking page · WhatsApp bot · manager bookings",
    fields: [
      { key: "slot_duration_minutes", label: "Slot length", unit: "min", effect: "How long each bookable slot is. A center can override this with its own value.", min: 5 },
      { key: "slot_booking_cutoff_minutes", label: "Booking cutoff", unit: "min before slot ends", effect: "A slot stops accepting bookings this close to its end.", min: 0 },
      { key: "max_advance_days", label: "Advance window", unit: "days", effect: "How far ahead a customer can book (today included).", min: 1, max: 60 },
      { key: "max_vehicles_per_booking", label: "Vehicles per visit", unit: "cars", effect: "How many of their cars a customer can add to one visit (one slot, one captain, one payment).", min: 1, max: 10 },
      { key: "payment_window_minutes", label: "Online payment hold", unit: "min", effect: "How long an unpaid 'pay online' booking keeps its slot before it's released.", min: 5, max: 240 },
      { key: "payment_reminder_minutes_before", label: "Payment reminder", unit: "min before the hold ends", effect: "One 'finish your payment' nudge, in-app and on WhatsApp. 0 turns it off.", min: 0, max: 120 },
    ],
  },
  {
    title: "Captain timing",
    appliesTo: "Captain app · manager queue flags · captain pay",
    fields: [
      { key: "captain_travel_buffer_minutes", label: "Travel buffer", unit: "min", effect: "Blocked before and after each job so a captain isn't booked back-to-back across town.", min: 0 },
      { key: "late_start_grace_minutes", label: "Late-start grace", unit: "min", effect: "Past the slot, a start within this is 'late' (25% fee penalty); beyond it 'very late' (50%).", min: 0 },
      { key: "late_assignment_grace_minutes", label: "Last-minute assignment grace", unit: "min", effect: "A captain handed a job after its slot began gets this long to head out before he counts as late.", min: 0, max: 120 },
      { key: "captain_start_lockout_hours", label: "Start lockout", unit: "hours after grace", effect: "After this the captain can't start at all — the manager must reschedule or reassign.", min: 1 },
      { key: "arrival_to_start_tolerance_minutes", label: "Reached → started gap", unit: "min", effect: "Flags the manager when a captain has 'reached' but not started the wash within this.", min: 1 },
      { key: "delay_tolerance_minutes", label: "Wash overrun tolerance", unit: "min", effect: "A wash running this far past its planned time is flagged as delayed.", min: 0 },
    ],
  },
  {
    title: "Checks & reminders",
    appliesTo: "Captain app · WhatsApp · customer notifications",
    fields: [
      { key: "photo_geofence_radius_m", label: "Photo geofence", unit: "metres", effect: "Before/after photos and the 'reached' tap taken farther than this from the address are flagged (never blocked).", min: 10 },
      { key: "repeat_reminder_days", label: "'Time for a wash?' after", unit: "days", effect: "Days after a customer's last wash before a gentle reminder — only if nothing is booked and no pass is live, at most once a month.", min: 3, max: 365 },
    ],
  },
];

const POLICY_LABELS: Record<string, string> = Object.fromEntries(
  POLICY_GROUPS.flatMap((g) => g.fields.map((f) => [f.key, f.label])),
);

export default function AdminPricingPage() {
  const queryClient = useQueryClient();
  const [perKm, setPerKm] = useState("");
  const [captainFee, setCaptainFee] = useState("");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [policyForm, setPolicyForm] = useState<Partial<Record<NumericPolicyKey, string>>>({});
  const [policySaved, setPolicySaved] = useState(false);
  const [policyError, setPolicyError] = useState<string | null>(null);

  const { data: config, isLoading } = useQuery({ queryKey: ["pricing-config"], queryFn: adminPricingApi.get });
  const { data: policy, isLoading: policyLoading } = useQuery({ queryKey: ["booking-policy"], queryFn: adminBookingPolicyApi.get });
  const { data: withdrawals, isLoading: withdrawalsLoading } = useQuery({
    queryKey: ["pending-withdrawals"],
    queryFn: () => walletApi.pendingWithdrawals({ page: 1, page_size: 50 }),
  });

  const invalidatePolicy = () => {
    queryClient.invalidateQueries({ queryKey: ["booking-policy"] });
    queryClient.invalidateQueries({ queryKey: ["settings-history", "booking_policy"] });
  };

  const saveMutation = useMutation({
    mutationFn: () => adminPricingApi.set(Number(perKm || config?.per_km_rate), Number(captainFee || config?.default_captain_service_fee)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pricing-config"] });
      queryClient.invalidateQueries({ queryKey: ["settings-history", "pricing_config"] });
      setPerKm("");
      setCaptainFee("");
      setSaved(true);
      setError(null);
      setTimeout(() => setSaved(false), 2500);
    },
    onError: (e) => setError(getErrorMessage(e)),
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
    mutationFn: ({ id, status }: { id: string; status: "approved" | "rejected" | "paid" }) => walletApi.reviewWithdrawal(id, status),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pending-withdrawals"] }),
  });

  // Instant on/off switches — deliberately not bundled into the "Save"
  // form; a feature switch should flip the moment it's tapped.
  const toggleMutation = useMutation({
    mutationFn: (patch: Partial<BookingPolicy>) => adminBookingPolicyApi.set(patch),
    onSuccess: invalidatePolicy,
  });

  if (isLoading || policyLoading) return <PageLoader />;

  const currentOf = (key: NumericPolicyKey) => (policy as Record<string, unknown> | undefined)?.[key];
  const dirtyCount = Object.values(policyForm).filter((v) => v !== undefined && v !== "").length;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Settings & pricing</h1>
        <p className="mt-1 max-w-3xl text-sm text-[var(--color-text-secondary)]">
          Every value here is live across the whole system the moment you save it — customer app, guest booking page, WhatsApp bot,
          manager and captain panels all read the same rules. Each card shows who changed it last; open the history for every change.
        </p>
      </div>

      {/* ------------------------------------------------ Captain pay */}
      <Card>
        <CardBody>
          <h2 className="font-semibold text-[var(--color-text-primary)]">Captain pay</h2>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            What a captain earns per booking, frozen on each booking when it's created: travel (₹ per km from the center) plus a flat
            service fee. A service can set its own fee in the Services page; this is the default.
          </p>
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Input
              label="Travel pay (₹ per km)"
              type="number"
              min={0}
              placeholder={String(config?.per_km_rate ?? 0)}
              value={perKm}
              onChange={(e) => setPerKm(e.target.value)}
              hint={`Now ₹${config?.per_km_rate ?? 0}/km — paid once per visit, however many cars are on it`}
            />
            <Input
              label="Service fee (₹ per booking)"
              type="number"
              min={0}
              placeholder={String(config?.default_captain_service_fee ?? 0)}
              value={captainFee}
              onChange={(e) => setCaptainFee(e.target.value)}
              hint={`Now ₹${config?.default_captain_service_fee ?? 0} per car washed, unless the service overrides it`}
            />
          </div>
          {error && <p className="mt-2 text-sm text-[var(--color-error)]">{error}</p>}
          {saved && <p className="mt-2 text-sm text-[var(--color-success)]">Captain pay updated.</p>}
          <Button className="mt-4" isLoading={saveMutation.isPending} disabled={!perKm && !captainFee} onClick={() => saveMutation.mutate()}>
            <IndianRupee className="h-4 w-4" /> Save captain pay
          </Button>
          <SettingsHistory settingKey="pricing_config" labels={{ per_km_rate: "Travel pay ₹/km", default_captain_service_fee: "Service fee ₹" }} />
        </CardBody>
      </Card>

      {/* ------------------------------------------------ Booking rules */}
      <Card>
        <CardBody>
          <h2 className="font-semibold text-[var(--color-text-primary)]">Booking rules</h2>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            Type a new value only where you want a change — empty boxes keep their current value, shown under each field.
          </p>

          <div className="mt-5 space-y-7">
            {POLICY_GROUPS.map((group) => (
              <div key={group.title}>
                <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="text-sm font-bold uppercase tracking-wide text-[var(--color-text-primary)]">{group.title}</h3>
                  <span className="text-xs text-[var(--color-text-secondary)]">Applies to: {group.appliesTo}</span>
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
          <Button className="mt-5" isLoading={savePolicyMutation.isPending} disabled={!dirtyCount} onClick={() => savePolicyMutation.mutate()}>
            <Clock className="h-4 w-4" /> {dirtyCount ? `Save ${dirtyCount} change${dirtyCount > 1 ? "s" : ""}` : "Save booking rules"}
          </Button>

          {/* Switches */}
          <div className="mt-6 space-y-3 border-t border-gray-100 pt-5">
            <Switch
              label="'Time for a wash?' reminders"
              description="A gentle nudge after a customer's last wash (days set above). WhatsApp goes out only through the approved marketing template, never to opted-out customers."
              checked={policy?.repeat_reminder_enabled !== false}
              pending={toggleMutation.isPending}
              onChange={(v) => toggleMutation.mutate({ repeat_reminder_enabled: v })}
            />
            <Switch
              label="Wallet balance gating"
              description="Blocks a captain below the minimum wallet balance from new assignments. Leave off until captains have a way to top up."
              checked={!!policy?.wallet_gating_enabled}
              pending={toggleMutation.isPending}
              onChange={(v) => toggleMutation.mutate({ wallet_gating_enabled: v })}
            />
          </div>

          <SettingsHistory settingKey="booking_policy" labels={{ ...POLICY_LABELS, wallet_gating_enabled: "Wallet gating", repeat_reminder_enabled: "Repeat reminders" }} />
        </CardBody>
      </Card>

      {/* ------------------------------------------------ Money */}
      <CollectionsReportCard
        title="Collections by center"
        entityLabel="Center"
        queryKey="admin-collections"
        fetcher={(params) => paymentApi.adminCollections(params)}
      />

      <Card>
        <CardBody>
          <h2 className="mb-4 font-semibold text-[var(--color-text-primary)]">Pending withdrawal requests</h2>
          <DataTable<WithdrawalRequest>
            data={withdrawals?.data ?? []}
            isLoading={withdrawalsLoading}
            emptyTitle="No pending withdrawals"
            emptyDescription="Captain withdrawal requests will show up here for review."
            columns={[
              { header: "Captain ID", accessor: (w) => <span className="font-mono-num text-xs">{w.captain_id}</span> },
              { header: "Amount", accessor: (w) => <span className="font-mono-num font-semibold">₹{w.amount}</span> },
              { header: "Requested", accessor: (w) => format(w.created_at) },
              { header: "Status", accessor: (w) => <Badge tone="warning">{w.status}</Badge> },
              {
                header: "Action",
                accessor: (w) => (
                  <div className="flex gap-2">
                    <Button size="sm" variant="outline" isLoading={reviewMutation.isPending} onClick={() => reviewMutation.mutate({ id: w.id, status: "approved" })}>
                      <CheckCircle2 className="h-3.5 w-3.5" /> Approve
                    </Button>
                    <Button size="sm" variant="ghost" isLoading={reviewMutation.isPending} onClick={() => reviewMutation.mutate({ id: w.id, status: "rejected" })}>
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
