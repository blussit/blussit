import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Clock, IndianRupee, XCircle } from "lucide-react";
import { adminBookingPolicyApi, adminPricingApi } from "../../api/admin";
import { paymentApi } from "../../api/payment";
import { CollectionsReportCard } from "../../components/shared/CollectionsReport";
import { walletApi } from "../../api/wallet";
import { getErrorMessage } from "../../lib/api-client";
import { Badge, Button, Card, CardBody, DataTable, Input, PageLoader } from "../../components/ui";
import { format } from "../../lib/date";
import type { WithdrawalRequest } from "../../types";

export default function AdminPricingPage() {
  const queryClient = useQueryClient();
  const [perKm, setPerKm] = useState("");
  const [captainFee, setCaptainFee] = useState("");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [policyForm, setPolicyForm] = useState({
    slot_duration_minutes: "",
    slot_booking_cutoff_minutes: "",
    max_advance_days: "",
    delay_tolerance_minutes: "",
    captain_travel_buffer_minutes: "",
    photo_geofence_radius_m: "",
    late_start_grace_minutes: "",
    late_assignment_grace_minutes: "",
    captain_start_lockout_hours: "",
  });
  const [policySaved, setPolicySaved] = useState(false);
  const [policyError, setPolicyError] = useState<string | null>(null);

  const { data: config, isLoading } = useQuery({
    queryKey: ["pricing-config"],
    queryFn: adminPricingApi.get,
  });

  const { data: policy, isLoading: policyLoading } = useQuery({
    queryKey: ["booking-policy"],
    queryFn: adminBookingPolicyApi.get,
  });

  const { data: withdrawals, isLoading: withdrawalsLoading } = useQuery({
    queryKey: ["pending-withdrawals"],
    queryFn: () => walletApi.pendingWithdrawals({ page: 1, page_size: 50 }),
  });

  const saveMutation = useMutation({
    mutationFn: () => adminPricingApi.set(Number(perKm || config?.per_km_rate), Number(captainFee || config?.default_captain_service_fee)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pricing-config"] });
      setSaved(true);
      setError(null);
      setTimeout(() => setSaved(false), 2500);
    },
    onError: (e) => setError(getErrorMessage(e)),
  });

  const savePolicyMutation = useMutation({
    mutationFn: () =>
      adminBookingPolicyApi.set({
        slot_duration_minutes: policyForm.slot_duration_minutes ? Number(policyForm.slot_duration_minutes) : undefined,
        slot_booking_cutoff_minutes: policyForm.slot_booking_cutoff_minutes ? Number(policyForm.slot_booking_cutoff_minutes) : undefined,
        max_advance_days: policyForm.max_advance_days ? Number(policyForm.max_advance_days) : undefined,
        delay_tolerance_minutes: policyForm.delay_tolerance_minutes ? Number(policyForm.delay_tolerance_minutes) : undefined,
        captain_travel_buffer_minutes: policyForm.captain_travel_buffer_minutes ? Number(policyForm.captain_travel_buffer_minutes) : undefined,
        photo_geofence_radius_m: policyForm.photo_geofence_radius_m ? Number(policyForm.photo_geofence_radius_m) : undefined,
        late_start_grace_minutes: policyForm.late_start_grace_minutes ? Number(policyForm.late_start_grace_minutes) : undefined,
        late_assignment_grace_minutes: policyForm.late_assignment_grace_minutes ? Number(policyForm.late_assignment_grace_minutes) : undefined,
        captain_start_lockout_hours: policyForm.captain_start_lockout_hours ? Number(policyForm.captain_start_lockout_hours) : undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["booking-policy"] });
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

  // Instant on/off — deliberately not bundled into the "Save booking rules"
  // form above, this should flip immediately like any other feature switch.
  const walletGatingMutation = useMutation({
    mutationFn: (enabled: boolean) => adminBookingPolicyApi.set({ wallet_gating_enabled: enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["booking-policy"] }),
  });

  if (isLoading || policyLoading) return <PageLoader />;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Pricing & wallets</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Set the commission engine's global rate, the booking scheduling rules, and clear the withdrawal queue.
        </p>
      </div>

      <Card>
        <CardBody>
          <h2 className="mb-1 font-semibold text-[var(--color-text-primary)]">Commission engine</h2>
          <p className="mb-4 text-sm text-[var(--color-text-secondary)]">
            The captain/platform split is computed and frozen on every booking at creation time using these values. Individual
            services can override the captain fee.
          </p>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Input
              label="Per-km rate (₹)"
              type="number"
              min={0}
              placeholder={String(config?.per_km_rate ?? 0)}
              value={perKm}
              onChange={(e) => setPerKm(e.target.value)}
            />
            <Input
              label="Default captain fee (₹)"
              type="number"
              min={0}
              placeholder={String(config?.default_captain_service_fee ?? 0)}
              value={captainFee}
              onChange={(e) => setCaptainFee(e.target.value)}
            />
          </div>
          {error && <p className="mt-2 text-sm text-[var(--color-error)]">{error}</p>}
          {saved && <p className="mt-2 text-sm text-[var(--color-success)]">Pricing configuration updated.</p>}
          <Button className="mt-4" isLoading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
            <IndianRupee className="h-4 w-4" /> Save pricing config
          </Button>
        </CardBody>
      </Card>

      {/* Platform money roll-up: per-center cash/online/uncollected,
          subscription revenue, and payments parked for manual attention. */}
      <CollectionsReportCard
        title="Collections by center"
        entityLabel="Center"
        queryKey="admin-collections"
        fetcher={(params) => paymentApi.adminCollections(params)}
      />

      <Card>
        <CardBody>
          <h2 className="mb-1 font-semibold text-[var(--color-text-primary)]">Booking rules</h2>
          <p className="mb-4 text-sm text-[var(--color-text-secondary)]">
            Slots are generated from each service center's own opening/closing hours (set per center) — the rules below are the
            platform-wide defaults layered on top: how long each slot is, how close to a slot's end it can still be booked, how
            far ahead captains must be scheduled, and when a proof photo or a running-long service gets flagged.
          </p>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Input
              label="Default slot duration (minutes)"
              type="number"
              min={5}
              placeholder={String(policy?.slot_duration_minutes ?? 0)}
              value={policyForm.slot_duration_minutes}
              onChange={(e) => setPolicyForm({ ...policyForm, slot_duration_minutes: e.target.value })}
              hint={`Current: ${policy?.slot_duration_minutes} min — a center can override this with its own value`}
            />
            <Input
              label="Slot booking cutoff (minutes before slot end)"
              type="number"
              min={0}
              placeholder={String(policy?.slot_booking_cutoff_minutes ?? 0)}
              value={policyForm.slot_booking_cutoff_minutes}
              onChange={(e) => setPolicyForm({ ...policyForm, slot_booking_cutoff_minutes: e.target.value })}
              hint={`Current: a slot stops being bookable ${policy?.slot_booking_cutoff_minutes} min before it ends`}
            />
            <Input
              label="Advance booking window (days)"
              type="number"
              min={1}
              max={60}
              placeholder={String(policy?.max_advance_days ?? 7)}
              value={policyForm.max_advance_days}
              onChange={(e) => setPolicyForm({ ...policyForm, max_advance_days: e.target.value })}
              hint={`Current: bookings open up to ${policy?.max_advance_days ?? 7} days ahead (today included)`}
            />
            <Input
              label="Delay tolerance (minutes)"
              type="number"
              min={0}
              placeholder={String(policy?.delay_tolerance_minutes ?? 0)}
              value={policyForm.delay_tolerance_minutes}
              onChange={(e) => setPolicyForm({ ...policyForm, delay_tolerance_minutes: e.target.value })}
              hint={`Current: a service is flagged as delayed once it runs ${policy?.delay_tolerance_minutes} min past its expected duration`}
            />
            <Input
              label="Captain travel buffer (minutes)"
              type="number"
              min={0}
              placeholder={String(policy?.captain_travel_buffer_minutes ?? 0)}
              value={policyForm.captain_travel_buffer_minutes}
              onChange={(e) => setPolicyForm({ ...policyForm, captain_travel_buffer_minutes: e.target.value })}
              hint={`Current: ${policy?.captain_travel_buffer_minutes} min blocked before/after each job`}
            />
            <Input
              label="Photo geofence radius (meters)"
              type="number"
              min={10}
              placeholder={String(policy?.photo_geofence_radius_m ?? 0)}
              value={policyForm.photo_geofence_radius_m}
              onChange={(e) => setPolicyForm({ ...policyForm, photo_geofence_radius_m: e.target.value })}
              hint={`Current: ${policy?.photo_geofence_radius_m}m`}
            />
            <Input
              label="Late-start grace period (minutes)"
              type="number"
              min={0}
              placeholder={String(policy?.late_start_grace_minutes ?? 0)}
              value={policyForm.late_start_grace_minutes}
              onChange={(e) => setPolicyForm({ ...policyForm, late_start_grace_minutes: e.target.value })}
              hint={`Current: ${policy?.late_start_grace_minutes} min past the slot before a late start is "severe"`}
            />
            <Input
              label="Last-minute assignment grace (minutes)"
              type="number"
              min={0}
              max={120}
              placeholder={String(policy?.late_assignment_grace_minutes ?? 15)}
              value={policyForm.late_assignment_grace_minutes}
              onChange={(e) => setPolicyForm({ ...policyForm, late_assignment_grace_minutes: e.target.value })}
              hint={`Current: a captain assigned after the slot began gets ${policy?.late_assignment_grace_minutes ?? 15} min to head out before he counts as late`}
            />
            <Input
              label="Captain start lockout (hours)"
              type="number"
              min={1}
              placeholder={String(policy?.captain_start_lockout_hours ?? 0)}
              value={policyForm.captain_start_lockout_hours}
              onChange={(e) => setPolicyForm({ ...policyForm, captain_start_lockout_hours: e.target.value })}
              hint={`Current: ${policy?.captain_start_lockout_hours}h past the grace period before a captain can no longer start it — needs reschedule/reassign instead`}
            />
          </div>
          {policyError && <p className="mt-2 text-sm text-[var(--color-error)]">{policyError}</p>}
          {policySaved && <p className="mt-2 text-sm text-[var(--color-success)]">Booking rules updated.</p>}
          <Button className="mt-4" isLoading={savePolicyMutation.isPending} onClick={() => savePolicyMutation.mutate()}>
            <Clock className="h-4 w-4" /> Save booking rules
          </Button>
        </CardBody>
      </Card>

      <Card>
        <CardBody>
          <div className="flex items-center justify-between gap-4">
            <div>
              <h2 className="font-semibold text-[var(--color-text-primary)]">Wallet balance gating</h2>
              <p className="mt-1 max-w-xl text-sm text-[var(--color-text-secondary)]">
                When on, a captain below the minimum wallet balance can't be assigned new bookings until they top up. There's no
                real payment gateway wired up yet, so there's currently no way for a captain to actually top up — leaving this
                on with no way to clear it just blocks assignment. Off by default; switch it on once real top-ups exist.
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={!!policy?.wallet_gating_enabled}
              disabled={walletGatingMutation.isPending}
              onClick={() => walletGatingMutation.mutate(!policy?.wallet_gating_enabled)}
              className={`relative h-7 w-12 shrink-0 rounded-full transition-colors disabled:opacity-60 ${
                policy?.wallet_gating_enabled ? "bg-[var(--color-success)]" : "bg-gray-300"
              }`}
            >
              <span
                className={`absolute top-1 h-5 w-5 rounded-full bg-white shadow transition-transform ${
                  policy?.wallet_gating_enabled ? "translate-x-6" : "translate-x-1"
                }`}
              />
            </button>
          </div>
          <p className="mt-2 text-xs font-medium text-[var(--color-text-secondary)]">
            Currently {policy?.wallet_gating_enabled ? "ON — captains can be blocked by wallet balance" : "OFF — captains are assigned regardless of wallet balance"}
          </p>
        </CardBody>
      </Card>

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
                    <Button
                      size="sm"
                      variant="outline"
                      isLoading={reviewMutation.isPending}
                      onClick={() => reviewMutation.mutate({ id: w.id, status: "approved" })}
                    >
                      <CheckCircle2 className="h-3.5 w-3.5" /> Approve
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      isLoading={reviewMutation.isPending}
                      onClick={() => reviewMutation.mutate({ id: w.id, status: "rejected" })}
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
