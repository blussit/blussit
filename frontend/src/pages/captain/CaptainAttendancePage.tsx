/**
 * Captain · Attendance history & leave (reached from Profile). Today's
 * clock in/out is the same card Profile shows.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { leaveApi } from "../../api/staffOps";
import { getErrorMessage } from "../../lib/api-client";
import { translateCaptainError } from "../../lib/captainErrorTranslations";
import { formatShortDate, todayIST } from "../../lib/date";
import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import { AttendanceCard, useTodayAttendance } from "../../components/captain/Attendance";
import { clock, statusWord } from "../../components/captain/jobState";
import { Btn, Panel, Pill, Sheet, TopBar } from "../../components/captain/ui";

const field =
  "h-12 w-full rounded-2xl border border-[#E4E9F1] px-4 text-[15px] text-[#0E1A33] outline-none focus:border-[#0A66F0] focus:ring-2 focus:ring-[#E8F0FE]";
const hours = (m?: number | null) => (m == null ? "" : m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`);
const tone = (s: string) => (s === "approved" || s === "present" ? "green" : s === "rejected" || s === "absent" ? "red" : "amber");

export default function CaptainAttendancePage() {
  const { t, language } = useCaptainTranslation();
  const queryClient = useQueryClient();
  const { data: attendance } = useTodayAttendance();
  const { data: leaves } = useQuery({ queryKey: ["leaves"], queryFn: () => leaveApi.mine({ page: 1, page_size: 10 }) });
  const [open, setOpen] = useState(false);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");

  const request = useMutation({
    mutationFn: () => leaveApi.request({ start_date: startDate, end_date: endDate, reason }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leaves"] });
      setOpen(false);
      setReason("");
      setError("");
    },
    onError: (e) => setError(translateCaptainError(getErrorMessage(e), language)),
  });

  return (
    <div className="space-y-4">
      <TopBar title={t("captain.v2.attendance")} back="/captain/profile" />
      <AttendanceCard />

      <div>
        <h2 className="mb-2 text-[15px] font-extrabold text-[#0E1A33]">{t("captain.attendance.history")}</h2>
        {!attendance?.data.length ? (
          <Panel className="px-4 py-6 text-center text-sm font-semibold text-[#5F6878]">{t("captain.attendance.no_history")}</Panel>
        ) : (
          <Panel className="divide-y divide-[#E4E9F1]">
            {attendance.data.map((a) => (
              <div key={a.id} className="flex min-h-[56px] items-center gap-3 px-4 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="text-[15px] font-bold text-[#0E1A33]">{a.attendance_date === todayIST() ? t("captain.v2.today") : formatShortDate(a.attendance_date)}</p>
                  <p className="tabular-nums text-[13px] text-[#5F6878]">
                    {clock(a.check_in_time) || "—"} → {clock(a.check_out_time) || "—"}
                    {a.worked_minutes != null && ` · ${hours(a.worked_minutes)}`}
                  </p>
                </div>
                <Pill tone={tone(a.status)}>{statusWord(a.status)}</Pill>
              </div>
            ))}
          </Panel>
        )}
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-[15px] font-extrabold text-[#0E1A33]">{t("captain.attendance.leave_requests")}</h2>
          <button type="button" onClick={() => setOpen(true)} className="flex min-h-[44px] items-center gap-1 rounded-xl px-2 text-sm font-bold text-[#0A66F0]">
            <Plus className="h-4 w-4" /> {t("captain.attendance.req_leave")}
          </button>
        </div>
        {!leaves?.data.length ? (
          <Panel className="px-4 py-6 text-center text-sm font-semibold text-[#5F6878]">{t("captain.attendance.no_leaves")}</Panel>
        ) : (
          <Panel className="divide-y divide-[#E4E9F1]">
            {leaves.data.map((l) => (
              <div key={l.id} className="flex min-h-[56px] items-center gap-3 px-4 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="text-[15px] font-bold text-[#0E1A33]">
                    {formatShortDate(l.start_date)}
                    {l.end_date !== l.start_date && ` – ${formatShortDate(l.end_date)}`}
                  </p>
                  <p className="truncate text-[13px] text-[#5F6878]">{l.reason}</p>
                </div>
                <Pill tone={tone(l.status)}>{statusWord(l.status)}</Pill>
              </div>
            ))}
          </Panel>
        )}
      </div>

      <Sheet open={open} onClose={() => setOpen(false)} title={t("captain.attendance.req_leave")}>
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            request.mutate();
          }}
        >
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs font-bold text-[#5F6878]">
              {t("captain.attendance.from")}
              <input type="date" required className={`${field} mt-1`} min={todayIST()} value={startDate} onChange={(e) => setStartDate(e.target.value)} />
            </label>
            <label className="text-xs font-bold text-[#5F6878]">
              {t("captain.attendance.to")}
              <input type="date" required className={`${field} mt-1`} min={startDate || todayIST()} value={endDate} onChange={(e) => setEndDate(e.target.value)} />
            </label>
          </div>
          <input required className={field} placeholder={t("captain.attendance.reason")} value={reason} onChange={(e) => setReason(e.target.value)} />
          {error && <p className="text-sm font-medium text-[#B91C1C]">{error}</p>}
          <Btn type="submit" className="w-full" loading={request.isPending}>
            {t("captain.attendance.submit_req")}
          </Btn>
        </form>
      </Sheet>
    </div>
  );
}
