import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Clock, IndianRupee, XCircle } from "lucide-react";
import { adminBookingPolicyApi, adminPricingApi } from "../../api/admin";
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
    operating_start: "",
    operating_end: "",
    min_lead_minutes: "",
    captain_travel_buffer_minutes: "",
    photo_geofence_radius_m: "",
    late_start_grace_minutes: "",
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
        operating_start: policyForm.operating_start || undefined,
        operating_end: policyForm.operating_end || undefined,
        min_lead_minutes: policyForm.min_lead_minutes ? Number(policyForm.min_lead_minutes) : undefined,
        captain_travel_buffer_minutes: policyForm.captain_travel_buffer_minutes ? Number(policyForm.captain_travel_buffer_minutes) : undefined,
        photo_geofence_radius_m: policyForm.photo_geofence_radius_m ? Number(policyForm.photo_geofence_radius_m) : undefined,
        late_start_grace_minutes: policyForm.late_start_grace_minutes ? Number(policyForm.late_start_grace_minutes) : undefined,
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

      <Card>
        <CardBody>
          <h2 className="mb-1 font-semibold text-[var(--color-text-primary)]">Booking rules</h2>
          <p className="mb-4 text-sm text-[var(--color-text-secondary)]">
            Controls what times customers can book, how far ahead captains must be scheduled, and when a proof photo gets
            flagged for being too far from the address.
          </p>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Input
              label="Opens at"
              type="time"
              placeholder={policy?.operating_start}
              value={policyForm.operating_start}
              onChange={(e) => setPolicyForm({ ...policyForm, operating_start: e.target.value })}
              hint={`Current: ${policy?.operating_start}`}
            />
            <Input
              label="Closes at"
              type="time"
              placeholder={policy?.operating_end}
              value={policyForm.operating_end}
              onChange={(e) => setPolicyForm({ ...policyForm, operating_end: e.target.value })}
              hint={`Current: ${policy?.operating_end}`}
            />
            <Input
              label="Minimum lead time (minutes)"
              type="number"
              min={0}
              placeholder={String(policy?.min_lead_minutes ?? 0)}
              value={policyForm.min_lead_minutes}
              onChange={(e) => setPolicyForm({ ...policyForm, min_lead_minutes: e.target.value })}
              hint={`Current: ${policy?.min_lead_minutes} min`}
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
