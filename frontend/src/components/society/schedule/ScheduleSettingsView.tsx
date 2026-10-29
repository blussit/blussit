import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Lock, Save } from "lucide-react";
import { scheduleApi, type ScheduleSettings } from "../../../api/societySchedule";
import { Button, ErrorState, Input, PageLoader, Panel } from "../../ui";
import { useToast } from "../../../context/ToastContext";
import { getErrorMessage } from "../../../lib/api-client";
import { formatDateTime } from "../../../lib/date";

/** Same ranges the server enforces (ScheduleSettingsRequest). */
const DAYS_MIN = 1;
const DAYS_MAX = 5;
const WASHES_MIN = 1;
const WASHES_MAX = 20;

const parse = (v: string): number | null => (/^\d{1,3}$/.test(v.trim()) ? Number(v) : null);

/**
 * Society schedule settings. They are GLOBAL — one pair of numbers for every
 * center — so an admin edits them and a manager sees them read-only (the
 * server refuses a manager's save). Both numbers only steer FUTURE planning:
 * visit days already booked are never touched.
 */
export function ScheduleSettingsView({ canEdit, plannerPath }: { canEdit: boolean; plannerPath: string }) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const { data, isLoading, isError, isFetching, refetch } = useQuery({ queryKey: ["society-schedule-settings"], queryFn: scheduleApi.settings });

  // null = untouched, so the fields follow the saved values until someone types.
  const [days, setDays] = useState<string | null>(null);
  const [washes, setWashes] = useState<string | null>(null);
  const [formError, setFormError] = useState("");

  const daysValue = days ?? String(data?.generate_days_ahead ?? "");
  const washesValue = washes ?? String(data?.default_washes_per_captain ?? "");
  const daysNum = parse(daysValue);
  const washesNum = parse(washesValue);
  // The server refuses a lead longer than bookings can open ahead.
  const daysCeiling = Math.min(DAYS_MAX, Math.max(DAYS_MIN, (data?.max_advance_days ?? DAYS_MAX + 1) - 1));
  const daysError =
    daysNum === null || daysNum < DAYS_MIN || daysNum > DAYS_MAX
      ? `Enter a whole number from ${DAYS_MIN} to ${DAYS_MAX}`
      : daysNum > daysCeiling
        ? `Bookings only open ${data?.max_advance_days} days ahead, so the most is ${daysCeiling}`
        : "";
  const washesError = washesNum === null || washesNum < WASHES_MIN || washesNum > WASHES_MAX ? `Enter a whole number from ${WASHES_MIN} to ${WASHES_MAX}` : "";
  const dirty = !!data && (daysNum !== data.generate_days_ahead || washesNum !== data.default_washes_per_captain);

  const save = useMutation({
    mutationFn: () => scheduleApi.saveSettings({ generate_days_ahead: daysNum as number, default_washes_per_captain: washesNum as number } satisfies ScheduleSettings),
    onSuccess: (res) => {
      void queryClient.invalidateQueries({ queryKey: ["society-schedule-settings"] });
      // The planner and each society's schedule quote these numbers.
      void queryClient.invalidateQueries({ queryKey: ["society-planner"] });
      void queryClient.invalidateQueries({ queryKey: ["society-schedule"] });
      setDays(null);
      setWashes(null);
      setFormError("");
      pushToast({ tone: "success", title: "Schedule Settings Saved", message: res.message });
    },
    onError: (err) => setFormError(getErrorMessage(err)),
  });

  if (isLoading) return <PageLoader />;
  if (isError || !data)
    return (
      <div className="space-y-6">
        <Heading plannerPath={plannerPath} />
        <ErrorState message="Couldn't load the schedule settings." onRetry={() => void refetch()} busy={isFetching} />
      </div>
    );

  return (
    <div className="space-y-6">
      <Heading plannerPath={plannerPath} />

      {!canEdit && (
        <div className="flex items-start gap-2 rounded-2xl border border-[var(--color-card-border)] bg-white p-4 text-sm text-[var(--color-text-secondary)]">
          <Lock className="mt-0.5 h-4 w-4 shrink-0" />
          <p>These settings are the same for every service center, so only an admin can change them. Ask an admin if they need to change.</p>
        </div>
      )}

      <Panel
        title="Booking Lead Time"
        description={data.updated_at ? `Last saved ${formatDateTime(data.updated_at)}${data.updated_by_name ? ` by ${data.updated_by_name}` : ""}` : "Never changed — using the standard values"}
      >
        <form
          className="grid gap-6 md:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            setFormError("");
            if (canEdit && !daysError && !washesError && dirty) save.mutate();
          }}
        >
          <div>
            <Input
              label="Book Visit Days This Many Days Ahead"
              type="number"
              inputMode="numeric"
              min={DAYS_MIN}
              max={DAYS_MAX}
              value={daysValue}
              disabled={!canEdit}
              error={canEdit && days !== null ? daysError : ""}
              hint={`${DAYS_MIN} to ${DAYS_MAX} days. Currently ${data.generate_days_ahead}.`}
              onChange={(e) => setDays(e.target.value)}
            />
            <p className="mt-2 text-xs text-[var(--color-text-secondary)]">
              The system books each society's premium washes this many days before the visit day, and every resident gets the normal booking confirmation then.
              A bigger number means residents hear sooner; a smaller one leaves more room to change a plan. A day planned inside this window is booked on the next sweep.
            </p>
          </div>

          <div>
            <Input
              label="Washes Per Captain On A Visit Day"
              type="number"
              inputMode="numeric"
              min={WASHES_MIN}
              max={WASHES_MAX}
              value={washesValue}
              disabled={!canEdit}
              error={canEdit && washes !== null ? washesError : ""}
              hint={`${WASHES_MIN} to ${WASHES_MAX} washes. Currently ${data.default_washes_per_captain}.`}
              onChange={(e) => setWashes(e.target.value)}
            />
            <p className="mt-2 text-xs text-[var(--color-text-secondary)]">
              How many premium washes one captain is expected to finish in one visit. It only pre-fills the "washes per captain" box when a manager adds a visit
              day or builds a rotation — each day and each repeat can still use its own number.
            </p>
          </div>

          {canEdit && (
            <div className="md:col-span-2">
              {formError && (
                <p role="alert" className="mb-3 text-sm text-[var(--color-error)]">
                  {formError}
                </p>
              )}
              <div className="flex flex-wrap items-center gap-3">
                <Button type="submit" isLoading={save.isPending} disabled={!dirty || !!daysError || !!washesError}>
                  <Save className="h-4 w-4" /> Save Settings
                </Button>
                {dirty && !save.isPending && (
                  <button
                    type="button"
                    className="text-sm font-medium text-[#0E1A33] hover:underline"
                    onClick={() => {
                      setDays(null);
                      setWashes(null);
                      setFormError("");
                    }}
                  >
                    Discard Changes
                  </button>
                )}
                <span className="text-xs text-[var(--color-text-secondary)]">{dirty ? "You have unsaved changes." : "Nothing to save."}</span>
              </div>
            </div>
          )}
        </form>
      </Panel>

      <p className="text-xs text-[var(--color-text-secondary)]">
        Changes apply to visit days planned from now on. Days that are already booked keep their bookings, captains and cars.
      </p>
    </div>
  );
}

function Heading({ plannerPath }: { plannerPath: string }) {
  return (
    <div>
      <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Society Schedule Settings</h1>
      <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
        How premium-wash visit days are booked for housing societies. Plan the days themselves in the{" "}
        <Link to={plannerPath} className="font-medium text-[#0E1A33] underline">
          Society Planner
        </Link>
        .
      </p>
    </div>
  );
}
