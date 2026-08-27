import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { CalendarClock, ChevronLeft, Lock, Unlock, Wand2, X } from "lucide-react";
import { adminCapacityPolicyApi, adminServiceCenterApi, adminSlotCapacityApi } from "../../api/admin";
import { Badge, Button, Card, Input, PageLoader } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";
import { format, todayIST } from "../../lib/date";
import { useConfirm } from "../../context/ConfirmContext";

type Section = "overview" | "policy";

/**
 * The single source of truth for a service center's capacity (BLUSSIT
 * ops-UX update, Sections 1-4) — everything capacity-related lives here,
 * not duplicated into the Service Center Edit form:
 *   - Overview: today's (or any chosen date's) actual booked/remaining
 *     counters per slot, with per-date/per-slot overrides (close/reopen,
 *     one-off capacity bump) — the existing real-time reservation view.
 *   - Capacity policy: the effective-dated BASELINE a not-yet-touched
 *     date starts out with — daily maximum, auto/manual slot
 *     distribution, "apply immediately" vs "apply from a future date",
 *     and an auditable history of every change.
 */
export default function AdminSlotCapacityPage() {
  const { centerId } = useParams<{ centerId: string }>();
  const navigate = useNavigate();
  const confirm = useConfirm();
  const [section, setSection] = useState<Section>("overview");

  const { data: center } = useQuery({ queryKey: ["admin-center-detail", centerId], queryFn: () => adminServiceCenterApi.get(centerId!), enabled: !!centerId });

  return (
    <div className="space-y-6">
      <div>
        <button onClick={() => navigate("/admin/service-centers")} className="mb-2 flex items-center gap-1 text-sm text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]">
          <ChevronLeft className="h-4 w-4" /> Service centers
        </button>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Capacity — {center?.name || "…"}</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">The single place to manage this center's booking capacity.</p>
      </div>

      <div className="flex gap-2">
        <button
          onClick={() => setSection("overview")}
          className={`rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors ${section === "overview" ? "bg-[var(--color-primary)] text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}
        >
          Overview
        </button>
        <button
          onClick={() => setSection("policy")}
          className={`rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors ${section === "policy" ? "bg-[var(--color-primary)] text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}
        >
          Capacity policy & scheduling
        </button>
      </div>

      {section === "overview" ? <OverviewSection centerId={centerId!} /> : <PolicySection centerId={centerId!} confirm={confirm} />}
    </div>
  );
}

function OverviewSection({ centerId }: { centerId: string }) {
  const queryClient = useQueryClient();
  const [date, setDate] = useState(todayIST());
  const [error, setError] = useState("");
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["admin-slot-capacity", centerId, date],
    queryFn: () => adminSlotCapacityApi.get(centerId, date),
    enabled: !!centerId && !!date,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin-slot-capacity", centerId, date] });

  const setMutation = useMutation({
    mutationFn: (payload: { slot_key: string; capacity?: number; is_closed?: boolean }) => adminSlotCapacityApi.set(centerId, { date, ...payload }),
    onSuccess: () => {
      invalidate();
      setEditingKey(null);
      setError("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  if (isLoading || !data) return <PageLoader />;

  const totals = data.slots.reduce(
    (acc, s) => ({ capacity: acc.capacity + s.capacity, booked: acc.booked + s.booked_count, remaining: acc.remaining + Math.max(s.capacity - s.booked_count, 0) }),
    { capacity: 0, booked: 0, remaining: 0 }
  );

  return (
    <div className="space-y-6">
      <Card className="p-4">
        <Input label="Date" type="date" min={todayIST()} value={date} onChange={(e) => setDate(e.target.value)} className="max-w-xs" />
      </Card>

      <div className="grid grid-cols-3 gap-3">
        <Card className="p-4 text-center">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">Capacity</p>
          <p className="mt-1 font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">{data.daily?.capacity ?? totals.capacity}</p>
        </Card>
        <Card className="p-4 text-center">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">Booked</p>
          <p className="mt-1 font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">{data.daily?.booked_count ?? totals.booked}</p>
        </Card>
        <Card className="p-4 text-center">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">Remaining</p>
          <p className="mt-1 font-mono-num text-2xl font-bold text-[var(--color-success)]">{data.daily?.remaining ?? totals.remaining}</p>
        </Card>
      </div>

      {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50/60">
                <th className="px-4 py-3 font-medium text-[var(--color-text-secondary)]">Time slot</th>
                <th className="px-4 py-3 font-medium text-[var(--color-text-secondary)]">Capacity</th>
                <th className="px-4 py-3 font-medium text-[var(--color-text-secondary)]">Booked</th>
                <th className="px-4 py-3 font-medium text-[var(--color-text-secondary)]">Remaining</th>
                <th className="px-4 py-3 font-medium text-[var(--color-text-secondary)]"></th>
              </tr>
            </thead>
            <tbody>
              {data.slots.map((s) => (
                <tr key={s.key} className={`border-b border-gray-50 last:border-0 ${s.is_closed ? "bg-gray-50/50" : ""}`}>
                  <td className="px-4 py-3 font-mono-num font-medium text-[var(--color-text-primary)]">
                    {s.start}–{s.end}
                    {s.is_closed && (
                      <Badge tone="neutral" className="ml-2">
                        Closed
                      </Badge>
                    )}
                  </td>
                  <td className="px-4 py-3 font-mono-num">
                    {editingKey === s.key ? (
                      <Input type="number" min={0} value={editValue} onChange={(e) => setEditValue(e.target.value)} className="w-20" />
                    ) : (
                      s.capacity
                    )}
                  </td>
                  <td className="px-4 py-3 font-mono-num text-[var(--color-text-secondary)]">{s.booked_count}</td>
                  <td className="px-4 py-3">
                    <Badge tone={s.remaining <= 5 ? "warning" : "success"}>{s.remaining}</Badge>
                  </td>
                  <td className="px-4 py-3">
                    {editingKey === s.key ? (
                      <div className="flex gap-1.5">
                        <Button size="sm" isLoading={setMutation.isPending} onClick={() => setMutation.mutate({ slot_key: s.key, capacity: Number(editValue) })}>
                          Save
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setEditingKey(null)}>
                          Cancel
                        </Button>
                      </div>
                    ) : (
                      <div className="flex gap-1.5">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            setEditingKey(s.key);
                            setEditValue(String(s.capacity));
                          }}
                        >
                          Edit
                        </Button>
                        <Button size="sm" variant="ghost" isLoading={setMutation.isPending} onClick={() => setMutation.mutate({ slot_key: s.key, is_closed: !s.is_closed })}>
                          {s.is_closed ? (
                            <>
                              <Unlock className="h-3.5 w-3.5" /> Reopen
                            </>
                          ) : (
                            <>
                              <Lock className="h-3.5 w-3.5" /> Close
                            </>
                          )}
                        </Button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="bg-gray-50/60 font-semibold text-[var(--color-text-primary)]">
                <td className="px-4 py-3">Total</td>
                <td className="px-4 py-3 font-mono-num">{totals.capacity}</td>
                <td className="px-4 py-3 font-mono-num">{totals.booked}</td>
                <td className="px-4 py-3 font-mono-num">{totals.remaining}</td>
                <td></td>
              </tr>
            </tfoot>
          </table>
        </div>
      </Card>
      <p className="text-xs text-[var(--color-text-secondary)]">
        Edits here override just this one date's slot — they take effect immediately. To change the baseline every future date
        starts out with, use "Capacity policy &amp; scheduling" above.
      </p>
    </div>
  );
}

function PolicySection({ centerId, confirm }: { centerId: string; confirm: (opts: { title: string; message?: string; tone?: "danger" | "default" }) => Promise<boolean> }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState("");
  const [maxDaily, setMaxDaily] = useState("");
  const [applyMode, setApplyMode] = useState<"immediate" | "future">("immediate");
  const [effectiveDate, setEffectiveDate] = useState(todayIST());
  const [distribution, setDistribution] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [editingChangeId, setEditingChangeId] = useState<string | null>(null);

  const { data: overview, isLoading } = useQuery({ queryKey: ["capacity-policy-overview", centerId], queryFn: () => adminCapacityPolicyApi.overview(centerId), enabled: !!centerId });
  const { data: history } = useQuery({ queryKey: ["capacity-policy-history", centerId], queryFn: () => adminCapacityPolicyApi.history(centerId), enabled: !!centerId });

  const slotKeys = overview?.slot_keys || [];

  // Seed the form from the current policy the first time it loads, so the
  // admin edits from a real starting point instead of a blank form.
  useEffect(() => {
    if (!overview || maxDaily) return;
    const source = overview.current;
    if (source.max_bookings_per_day) {
      setMaxDaily(String(source.max_bookings_per_day));
      setDistribution(Object.fromEntries(Object.entries(source.slot_distribution).map(([k, v]) => [k, String(v)])));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overview]);

  const total = useMemo(() => Object.values(distribution).reduce((sum, v) => sum + (Number(v) || 0), 0), [distribution]);
  const maxDailyNum = Number(maxDaily) || 0;
  const totalMismatch = maxDaily !== "" && total !== maxDailyNum;

  const autoDistribute = (max: number) => {
    if (!slotKeys.length) return;
    const base = Math.floor(max / slotKeys.length);
    const remainder = max - base * slotKeys.length;
    const next: Record<string, string> = {};
    slotKeys.forEach((key, i) => {
      next[key] = String(i === slotKeys.length - 1 ? base + remainder : base);
    });
    setDistribution(next);
  };

  const resetForm = () => {
    setEditingChangeId(null);
    setMaxDaily("");
    setDistribution({});
    setApplyMode("immediate");
    setEffectiveDate(todayIST());
    setNote("");
    setError("");
  };

  const editScheduled = () => {
    if (!overview?.scheduled) return;
    const s = overview.scheduled;
    setEditingChangeId(s.id);
    setMaxDaily(String(s.max_bookings_per_day));
    setDistribution(Object.fromEntries(Object.entries(s.slot_distribution).map(([k, v]) => [k, String(v)])));
    setApplyMode("future");
    setEffectiveDate(s.effective_date);
    setNote(s.note || "");
  };

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["capacity-policy-overview", centerId] });
    queryClient.invalidateQueries({ queryKey: ["capacity-policy-history", centerId] });
    // The Overview tab reads the actual per-date slot/daily counters, which
    // the backend resyncs the moment a policy is applied/cancelled for an
    // already-touched date (see CapacityPolicyService._resync_touched_date)
    // — but that resynced data still needs to be re-fetched here, or
    // switching to Overview would keep showing whatever it last cached
    // (react-query's 30s staleTime means a same-page tab switch won't
    // naturally refetch on its own). Not scoped to one date: an admin could
    // apply "now" then flip to Overview on a different date than what's
    // currently in the date picker's default.
    queryClient.invalidateQueries({ queryKey: ["admin-slot-capacity", centerId] });
  };

  const scheduleMutation = useMutation({
    mutationFn: () =>
      adminCapacityPolicyApi.schedule(centerId, {
        effective_date: applyMode === "immediate" ? todayIST() : effectiveDate,
        max_bookings_per_day: maxDailyNum,
        slot_distribution: Object.fromEntries(Object.entries(distribution).map(([k, v]) => [k, Number(v) || 0])),
        note: note || undefined,
      }),
    onSuccess: () => {
      invalidateAll();
      resetForm();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const cancelMutation = useMutation({
    mutationFn: (changeId: string) => adminCapacityPolicyApi.cancel(centerId, changeId),
    onSuccess: () => {
      invalidateAll();
      resetForm();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  if (isLoading || !overview) return <PageLoader />;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Card className="p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">Current capacity</p>
          <p className="mt-1 font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">
            {overview.current.max_bookings_per_day != null ? `${overview.current.max_bookings_per_day} / day` : "Not configured"}
          </p>
          <p className="mt-1 text-xs text-[var(--color-text-secondary)]">Active today</p>
        </Card>
        <Card className={`p-4 ${overview.scheduled ? "border-[var(--color-primary)]" : ""}`}>
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">Scheduled capacity</p>
          {overview.scheduled ? (
            <>
              <p className="mt-1 font-mono-num text-2xl font-bold text-[var(--color-primary)]">{overview.scheduled.max_bookings_per_day} / day</p>
              <p className="mt-1 flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
                <CalendarClock className="h-3.5 w-3.5" /> Effective from {format(overview.scheduled.effective_date)}
              </p>
              <div className="mt-2 flex gap-2">
                <Button size="sm" variant="outline" onClick={editScheduled}>
                  Edit
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  isLoading={cancelMutation.isPending}
                  onClick={async () => {
                    if (await confirm({ title: "Cancel this scheduled capacity change?", tone: "danger" })) cancelMutation.mutate(overview.scheduled!.id);
                  }}
                >
                  <X className="h-3.5 w-3.5" /> Cancel
                </Button>
              </div>
            </>
          ) : (
            <p className="mt-1 text-sm text-[var(--color-text-secondary)]">No upcoming change scheduled.</p>
          )}
        </Card>
      </div>

      <Card className="p-5">
        <p className="mb-1 text-sm font-semibold text-[var(--color-text-primary)]">{editingChangeId ? "Edit scheduled capacity change" : "Change capacity"}</p>
        <p className="mb-4 text-xs text-[var(--color-text-secondary)]">
          Set the maximum bookings this center should accept per day, and how those are split across its time slots.
        </p>

        <div className="space-y-4">
          <Input
            label="Maximum daily bookings"
            type="number"
            min={0}
            className="max-w-xs"
            value={maxDaily}
            onChange={(e) => {
              setMaxDaily(e.target.value);
            }}
          />

          <div>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-sm font-medium text-[var(--color-text-primary)]">Slot capacity</p>
              <Button size="sm" variant="outline" onClick={() => autoDistribute(maxDailyNum)} disabled={!maxDailyNum}>
                <Wand2 className="h-3.5 w-3.5" /> Auto-distribute
              </Button>
            </div>
            <div className="space-y-2">
              {slotKeys.map((key) => (
                <div key={key} className="flex items-center justify-between gap-3 rounded-lg border border-gray-100 p-2.5">
                  <span className="font-mono-num text-sm text-[var(--color-text-primary)]">{key}</span>
                  <Input
                    type="number"
                    min={0}
                    className="w-24"
                    value={distribution[key] ?? ""}
                    onChange={(e) => setDistribution({ ...distribution, [key]: e.target.value })}
                  />
                </div>
              ))}
            </div>
            <div className={`mt-2 flex items-center justify-between rounded-lg px-3 py-2 text-sm font-medium ${totalMismatch ? "bg-red-50 text-[var(--color-error)]" : "bg-[var(--color-accent-light)] text-[var(--color-success)]"}`}>
              <span>Total</span>
              <span className="font-mono-num">
                {total} / {maxDailyNum}
              </span>
            </div>
            {totalMismatch && <p className="mt-1 text-xs text-[var(--color-error)]">Slot capacities must add up to exactly the daily maximum before saving.</p>}
          </div>

          <div>
            <p className="mb-2 text-sm font-medium text-[var(--color-text-primary)]">When should this apply?</p>
            <div className="flex flex-wrap gap-4">
              <label className="flex items-center gap-2 text-sm text-[var(--color-text-primary)]">
                <input type="radio" checked={applyMode === "immediate"} onChange={() => setApplyMode("immediate")} /> Apply immediately
              </label>
              <label className="flex items-center gap-2 text-sm text-[var(--color-text-primary)]">
                <input type="radio" checked={applyMode === "future"} onChange={() => setApplyMode("future")} /> Apply from a specific date
              </label>
            </div>
            {applyMode === "future" && (
              <Input type="date" min={todayIST()} value={effectiveDate} onChange={(e) => setEffectiveDate(e.target.value)} className="mt-2 max-w-xs" />
            )}
            <p className="mt-1.5 text-xs text-[var(--color-text-secondary)]">
              {applyMode === "immediate"
                ? "Today's capacity changes right away and Overview updates immediately — except for any slot you've individually edited under Overview, which keeps its own number until you change it there."
                : "Today's capacity stays exactly as it is until the date above, then this becomes the new baseline for every date from then on."}
            </p>
          </div>

          <Input label="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Festival season ramp-up" />

          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <div className="flex gap-2">
            <Button
              isLoading={scheduleMutation.isPending}
              disabled={!maxDaily || totalMismatch}
              onClick={() => scheduleMutation.mutate()}
            >
              {editingChangeId ? "Save changes" : applyMode === "immediate" ? "Apply now" : "Schedule change"}
            </Button>
            {editingChangeId && (
              <Button variant="outline" onClick={resetForm}>
                Cancel edit
              </Button>
            )}
          </div>
        </div>
      </Card>

      <Card className="overflow-hidden">
        <div className="border-b border-gray-100 px-5 py-3">
          <p className="text-sm font-semibold text-[var(--color-text-primary)]">Capacity history</p>
        </div>
        {!history?.length ? (
          <p className="p-5 text-sm text-[var(--color-text-secondary)]">No capacity changes recorded yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-left text-sm">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50/60">
                  <th className="px-4 py-3 font-medium text-[var(--color-text-secondary)]">Effective date</th>
                  <th className="px-4 py-3 font-medium text-[var(--color-text-secondary)]">Daily max</th>
                  <th className="px-4 py-3 font-medium text-[var(--color-text-secondary)]">Status</th>
                  <th className="px-4 py-3 font-medium text-[var(--color-text-secondary)]">Note</th>
                  <th className="px-4 py-3 font-medium text-[var(--color-text-secondary)]"></th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.id} className="border-b border-gray-50 last:border-0">
                    <td className="px-4 py-3 font-mono-num">{format(h.effective_date)}</td>
                    <td className="px-4 py-3 font-mono-num">{h.max_bookings_per_day}</td>
                    <td className="px-4 py-3">
                      <Badge tone={h.status === "active" ? "success" : h.status === "scheduled" ? "info" : "neutral"}>{h.status}</Badge>
                    </td>
                    <td className="px-4 py-3 text-xs text-[var(--color-text-secondary)]">{h.note || "—"}</td>
                    <td className="px-4 py-3">
                      {h.status === "scheduled" && (
                        <Button
                          size="sm"
                          variant="ghost"
                          isLoading={cancelMutation.isPending}
                          onClick={async () => {
                            if (await confirm({ title: "Cancel this scheduled capacity change?", tone: "danger" })) cancelMutation.mutate(h.id);
                          }}
                        >
                          Cancel
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
