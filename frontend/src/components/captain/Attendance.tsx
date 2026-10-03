/**
 * Clock in / clock out — geo-stamped wherever he is (deliberately no
 * centre geofence: a work day can start anywhere; GPS denied still records
 * the punch, just without a pin).
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, LogIn, LogOut } from "lucide-react";
import { attendanceApi } from "../../api/staffOps";
import { getErrorMessage } from "../../lib/api-client";
import { translateCaptainError } from "../../lib/captainErrorTranslations";
import { todayIST } from "../../lib/date";
import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import { clock } from "./jobState";
import { tryPosition } from "./photo";
import { Btn, Panel } from "./ui";

export function useTodayAttendance() {
  const q = useQuery({ queryKey: ["attendance"], queryFn: () => attendanceApi.mine({ page: 1, page_size: 15 }) });
  const today = (q.data?.data || []).find((a) => a.attendance_date === todayIST()) || null;
  return { ...q, today };
}

export function useClock() {
  const { language } = useCaptainTranslation();
  const queryClient = useQueryClient();
  const [error, setError] = useState("");
  const done = () => {
    setError("");
    queryClient.invalidateQueries({ queryKey: ["attendance"] });
  };
  const fail = (e: unknown) => setError(translateCaptainError(getErrorMessage(e), language));
  const clockIn = useMutation({ mutationFn: async () => attendanceApi.checkIn(await tryPosition()), onSuccess: done, onError: fail });
  const clockOut = useMutation({ mutationFn: async () => attendanceApi.checkOut(await tryPosition()), onSuccess: done, onError: fail });
  return { clockIn, clockOut, error };
}

const hours = (m?: number | null) => (m == null ? "" : m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`);

export function AttendanceCard() {
  const { t } = useCaptainTranslation();
  const { today, isLoading } = useTodayAttendance();
  const { clockIn, clockOut, error } = useClock();
  if (isLoading) return null;
  return (
    <Panel className="p-4">
      {!today ? (
        <>
          <p className="text-[15px] font-bold text-[#0E1A33]">{t("captain.v2.att.notIn")}</p>
          <p className="mt-0.5 text-sm text-[#5F6878]">{t("captain.v2.att.locNote")}</p>
          <Btn className="mt-3 w-full" loading={clockIn.isPending} onClick={() => clockIn.mutate()}>
            <LogIn className="h-5 w-5" /> {t("captain.v2.att.clockIn")}
          </Btn>
        </>
      ) : (
        <>
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#E7F6EC] text-[#15803D]">
              <CheckCircle2 className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <p className="text-[15px] font-bold text-[#0E1A33]">{t("captain.v2.att.inAt").replace("{time}", clock(today.check_in_time))}</p>
              <p className="text-sm text-[#5F6878]">
                {today.check_out_time
                  ? t("captain.v2.att.outAt").replace("{time}", clock(today.check_out_time)).replace("{hours}", hours(today.worked_minutes))
                  : t("captain.attendance.on_duty")}
              </p>
            </div>
          </div>
          {!today.check_out_time && (
            <Btn variant="outline" className="mt-3 w-full" loading={clockOut.isPending} onClick={() => clockOut.mutate()}>
              <LogOut className="h-5 w-5" /> {t("captain.v2.att.clockOut")}
            </Btn>
          )}
        </>
      )}
      {error && <p className="mt-2 text-sm font-medium text-[#B91C1C]">{error}</p>}
    </Panel>
  );
}
