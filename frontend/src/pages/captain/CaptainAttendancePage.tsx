/**
 * Captain attendance — geo-stamped check-in/out (recorded wherever the
 * captain is; deliberately no center geofence, the manager just sees the
 * pin), a big today-status card with hours worked, history and leave.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, LogIn, LogOut, MapPin, Plus } from "lucide-react";
import { attendanceApi, leaveApi, type GeoPayload } from "../../api/staffOps";
import { Button, Card, CardBody, CardHeader, DataTable, Input, Modal, StatusBadge } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";
import { formatDateTime } from "../../lib/date";
import type { AttendanceRecord, LeaveRequest } from "../../api/staffOps";

const timeOnly = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : null;

function hoursLabel(minutes?: number | null): string | null {
  if (minutes == null) return null;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h ? `${h}h ${m}m` : `${m}m`;
}

/** Blocking GPS fix, same pattern as the job workflow's heading step —
 * attendance without a location is still accepted (GPS denied/broken
 * shouldn't stop a work day), it just records nothing. */
function withLocation(run: (loc: GeoPayload) => void) {
  if (!navigator.geolocation) return run({});
  navigator.geolocation.getCurrentPosition(
    (pos) => run({ latitude: pos.coords.latitude, longitude: pos.coords.longitude, accuracy_m: pos.coords.accuracy ?? undefined }),
    () => run({}),
    { enableHighAccuracy: true, timeout: 15000 }
  );
}

export default function CaptainAttendancePage() {
  const queryClient = useQueryClient();
  const { data: attendance } = useQuery({ queryKey: ["attendance"], queryFn: () => attendanceApi.mine({ page: 1, page_size: 15 }) });
  const { data: leaves } = useQuery({ queryKey: ["leaves"], queryFn: () => leaveApi.mine({ page: 1, page_size: 10 }) });

  const [leaveOpen, setLeaveOpen] = useState(false);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [locating, setLocating] = useState<"in" | "out" | null>(null);

  const todayStr = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const today = (attendance?.data || []).find((a) => a.attendance_date === todayStr) || null;

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["attendance"] });
  const checkInMutation = useMutation({
    mutationFn: (loc: GeoPayload) => attendanceApi.checkIn(loc),
    onSuccess: () => {
      setError("");
      invalidate();
    },
    onError: (err) => setError(getErrorMessage(err)),
    onSettled: () => setLocating(null),
  });
  const checkOutMutation = useMutation({
    mutationFn: (loc: GeoPayload) => attendanceApi.checkOut(loc),
    onSuccess: () => {
      setError("");
      invalidate();
    },
    onError: (err) => setError(getErrorMessage(err)),
    onSettled: () => setLocating(null),
  });

  const leaveRequestMutation = useMutation({
    mutationFn: () => leaveApi.request({ start_date: startDate, end_date: endDate, reason }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leaves"] });
      setLeaveOpen(false);
      setReason("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  return (
    <div className="space-y-6">
      <div>
        <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-black">Attendance</p>
        <h1 className="mt-1 font-display text-2xl font-bold text-[var(--color-text-primary)]">Your day</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Check in when you start, check out when you're done — your location is saved with both.</p>
      </div>

      {/* Today — one big state card */}
      <div className="rounded-2xl border border-[#F3E5B5] bg-white p-5">
        {!today ? (
          <div className="flex flex-col items-center gap-3 py-2 text-center">
            <p className="text-sm text-[var(--color-text-secondary)]">You haven't checked in today.</p>
            <Button
              className="w-full max-w-xs !py-3"
              isLoading={locating === "in" || checkInMutation.isPending}
              onClick={() => {
                setLocating("in");
                withLocation((loc) => checkInMutation.mutate(loc));
              }}
            >
              <LogIn className="h-4 w-4" /> Check in
            </Button>
            <p className="flex items-center gap-1 text-[11px] text-[var(--color-text-secondary)]">
              <MapPin className="h-3 w-3" /> Your current location is recorded
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-green-100 text-green-700">
                <CheckCircle2 className="h-5 w-5" />
              </span>
              <div>
                <p className="text-sm font-bold text-black">Checked in at {timeOnly(today.check_in_time) || "—"}</p>
                <p className="text-xs text-[var(--color-text-secondary)]">
                  {today.check_out_time
                    ? `Checked out ${timeOnly(today.check_out_time)}${hoursLabel(today.worked_minutes) ? ` · ${hoursLabel(today.worked_minutes)} worked` : ""}`
                    : "On duty — check out when your day ends."}
                </p>
              </div>
            </div>
            {!today.check_out_time && (
              <Button
                variant="outline"
                className="w-full"
                isLoading={locating === "out" || checkOutMutation.isPending}
                onClick={() => {
                  setLocating("out");
                  withLocation((loc) => checkOutMutation.mutate(loc));
                }}
              >
                <LogOut className="h-4 w-4" /> Check out
              </Button>
            )}
          </div>
        )}
        {error && <p className="mt-2 text-sm text-[var(--color-error)]">{error}</p>}
      </div>

      <Card>
        <CardHeader>
          <h2 className="font-semibold text-[var(--color-text-primary)]">History</h2>
        </CardHeader>
        <CardBody className="p-0">
          <DataTable<AttendanceRecord>
            data={attendance?.data || []}
            emptyTitle="No attendance recorded yet"
            columns={[
              { header: "Date", accessor: (a) => a.attendance_date },
              { header: "Status", accessor: (a) => <StatusBadge status={a.status} /> },
              { header: "Check-in", accessor: (a) => (a.check_in_time ? formatDateTime(a.check_in_time) : "—") },
              { header: "Check-out", accessor: (a) => (a.check_out_time ? formatDateTime(a.check_out_time) : "—") },
              { header: "Hours", accessor: (a) => hoursLabel(a.worked_minutes) || "—" },
            ]}
          />
        </CardBody>
      </Card>

      <div>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-semibold text-[var(--color-text-primary)]">Leave requests</h2>
          <Button size="sm" onClick={() => setLeaveOpen(true)}>
            <Plus className="h-4 w-4" /> Request leave
          </Button>
        </div>
        <DataTable<LeaveRequest>
          data={leaves?.data || []}
          emptyTitle="No leave requests"
          columns={[
            { header: "From", accessor: (l) => l.start_date },
            { header: "To", accessor: (l) => l.end_date },
            { header: "Reason", accessor: (l) => l.reason },
            { header: "Status", accessor: (l) => <StatusBadge status={l.status} /> },
          ]}
        />
      </div>

      <Modal open={leaveOpen} onClose={() => setLeaveOpen(false)} title="Request leave">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            leaveRequestMutation.mutate();
          }}
        >
          <div className="grid grid-cols-2 gap-3">
            <Input label="Start date" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} required />
            <Input label="End date" type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} required />
          </div>
          <Input label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} required />
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" isLoading={leaveRequestMutation.isPending}>
            Submit request
          </Button>
        </form>
      </Modal>
    </div>
  );
}
