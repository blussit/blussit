import { useState } from "react";
import { useMutation, type UseQueryResult } from "@tanstack/react-query";
import { Button, ErrorState, Input, Modal, Select, Switch } from "../ui";
import { payrollApi, type AttendanceDay, type AttendanceOverrideStatus, type AttendanceStatus, type PayrollCaptainDetail, type PayrollStatement } from "../../api/payroll";
import { getErrorMessage } from "../../lib/api-client";
import { formatDay, todayIST } from "../../lib/date";
import { cn } from "../../lib/cn";
import { ATTENDANCE_LABEL, ATTENDANCE_TONE, Banner, SkeletonRows } from "./payrollUi";
import { usePayrollWrite } from "./usePayrollWrite";

const CELL_TONE: Record<string, string> = {
  success: "border-green-200 bg-green-50 text-green-800",
  warning: "border-amber-200 bg-amber-50 text-amber-800",
  info: "border-[#C9D6EA] bg-[#E8F0FE] text-[#0A66F0]",
  error: "border-red-200 bg-red-50 text-[var(--color-error)]",
  neutral: "border-gray-200 bg-gray-50 text-gray-600",
};
const SHORT: Record<AttendanceStatus, string> = { present: "P", half_day: "½", leave: "L", absent: "A", weekly_off: "Off", holiday: "Hol", not_joined: "", left: "", upcoming: "", pending: "…" };
// What a manager can set by hand; the other statuses (weekly off, holiday, …) are worked out.
const SETTABLE: AttendanceOverrideStatus[] = ["present", "half_day", "leave", "absent"];
const SUMMARY: AttendanceStatus[] = ["present", "half_day", "leave", "absent", "weekly_off", "holiday"];
const LOCKED: AttendanceStatus[] = ["not_joined", "left", "upcoming"];

/**
 * The month as a calendar: each day shows the status the server derived
 * (check-in or a finished job = present, approved leave, a past working day
 * with neither = absent) or the one a manager set. Tap a day to override it
 * with a note — every override is audited.
 */
export function AttendanceTab({ detail, month, statement }: { detail: PayrollCaptainDetail; month: string; statement: UseQueryResult<PayrollStatement> }) {
  const [pick, setPick] = useState<AttendanceDay | null>(null);
  const days = statement.data?.attendance || [];

  if (statement.isLoading) return <SkeletonRows rows={5} />;
  if (statement.isError) return <ErrorState message="Couldn't load attendance." busy={statement.isFetching} onRetry={() => void statement.refetch()} />;

  const byDate = new Map(days.map((d) => [d.date, d]));
  const [y, m] = month.split("-").map(Number);
  const first = new Date(Date.UTC(y, m - 1, 1));
  const count = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const lead = (first.getUTCDay() + 6) % 7; // Monday first
  const today = todayIST();
  const cells: (string | null)[] = [...Array(lead).fill(null), ...Array.from({ length: count }, (_, i) => `${month}-${String(i + 1).padStart(2, "0")}`)];

  const counts = days.reduce<Record<string, number>>((acc, d) => ({ ...acc, [d.status]: (acc[d.status] || 0) + 1 }), {});

  return (
    <div className="space-y-4">
      {detail.pay.basis !== "salary" && <Banner>Attendance only affects pay for captains on a salary. It is still recorded here.</Banner>}
      {!days.length ? (
        <Banner>No attendance has been worked out for this month yet.</Banner>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {SUMMARY.filter((s) => counts[s]).map((s) => (
            <span key={s} className={cn("rounded-full border px-2.5 py-1 text-xs font-medium", CELL_TONE[ATTENDANCE_TONE[s]])}>
              {ATTENDANCE_LABEL[s]}: {counts[s]}
            </span>
          ))}
        </div>
      )}

      <div>
        <div className="mb-1 grid grid-cols-7 gap-1 text-center text-[11px] font-medium text-[var(--color-text-secondary)]">
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
            <span key={d}>{d}</span>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-1">
          {cells.map((date, i) => {
            if (!date) return <span key={`pad-${i}`} />;
            const rec = byDate.get(date);
            const future = date > today;
            const tone = rec ? CELL_TONE[ATTENDANCE_TONE[rec.status]] : "border-gray-100 bg-white text-gray-300";
            return (
              <button
                key={date}
                type="button"
                disabled={future || (!!rec && LOCKED.includes(rec.status))}
                aria-label={`${formatDay(date)}: ${rec ? ATTENDANCE_LABEL[rec.status] : "No record"}${rec?.overridden ? ", set by a manager" : ""}`}
                onClick={() => setPick(rec || { date, status: "absent" })}
                className={cn(
                  "relative flex h-12 flex-col items-center justify-center rounded-lg border text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40",
                  tone,
                  !future && "hover:border-[#0A66F0]",
                  date === today && "ring-2 ring-[#0A66F0]/40",
                )}
              >
                <span className="font-mono-num text-[11px] opacity-70">{Number(date.slice(8))}</span>
                <span className="font-semibold">{rec ? SHORT[rec.status] : ""}</span>
                {rec?.overridden && <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-[#0A66F0]" />}
              </button>
            );
          })}
        </div>
        <p className="mt-2 text-[11px] text-[var(--color-text-secondary)]">Tap a day to change it. A blue dot marks a day a manager set.</p>
      </div>

      <Modal open={!!pick} onClose={() => setPick(null)} title={pick ? `Set ${formatDay(pick.date)}` : "Set Day"}>
        {pick && <OverrideForm captainId={detail.id} day={pick} onClose={() => setPick(null)} />}
      </Modal>
    </div>
  );
}

function OverrideForm({ captainId, day, onClose }: { captainId: string; day: AttendanceDay; onClose: () => void }) {
  const write = usePayrollWrite();
  const [status, setStatus] = useState<AttendanceOverrideStatus>((SETTABLE as string[]).includes(day.status) ? (day.status as AttendanceOverrideStatus) : "present");
  const [paidLeave, setPaidLeave] = useState(!!day.paid_leave);
  const [note, setNote] = useState(day.overridden ? day.note || "" : "");
  const [error, setError] = useState("");
  const mutation = useMutation({
    mutationFn: () => payrollApi.markAttendance(captainId, { date: day.date, status, paid_leave: status === "leave" ? paidLeave : false, note: note.trim() }),
    onSuccess: (res) => {
      write.ok("Attendance Updated", res.message);
      onClose();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        setError("");
        if (note.trim().length < 3) return setError("A note is required — say why you are changing this day.");
        if (!mutation.isPending) mutation.mutate();
      }}
    >
      {day.overridden && day.marked_by && <p className="text-xs text-[var(--color-text-secondary)]">Last set by {day.marked_by}.</p>}
      <Select label="Status" value={status} onChange={(e) => setStatus(e.target.value as AttendanceOverrideStatus)}>
        {SETTABLE.map((s) => (
          <option key={s} value={s}>
            {ATTENDANCE_LABEL[s]}
          </option>
        ))}
      </Select>
      {status === "leave" && <Switch checked={paidLeave} onChange={setPaidLeave} label="Paid Leave" description="Off = it only counts as paid while this month's paid leaves last." />}
      <Input label="Note" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Forgot to check in — was on site" autoFocus />
      {error && (
        <p role="alert" className="text-sm text-[var(--color-error)]">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="button" variant="outline" className="flex-1" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" className="flex-1" isLoading={mutation.isPending}>
          Save Day
        </Button>
      </div>
    </form>
  );
}
