import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LogIn, LogOut, Plus } from "lucide-react";
import { attendanceApi, leaveApi } from "../../api/staffOps";
import { Button, Card, CardBody, CardHeader, DataTable, Input, Modal, StatusBadge } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";
import { formatDateTime } from "../../lib/date";
import type { AttendanceRecord, LeaveRequest } from "../../api/staffOps";

export default function CaptainAttendancePage() {
  const queryClient = useQueryClient();
  const { data: attendance } = useQuery({ queryKey: ["attendance"], queryFn: () => attendanceApi.mine({ page: 1, page_size: 10 }) });
  const { data: leaves } = useQuery({ queryKey: ["leaves"], queryFn: () => leaveApi.mine({ page: 1, page_size: 10 }) });

  const [leaveOpen, setLeaveOpen] = useState(false);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");

  const checkInMutation = useMutation({
    mutationFn: () => attendanceApi.checkIn(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["attendance"] }),
    onError: (err) => setError(getErrorMessage(err)),
  });

  const checkOutMutation = useMutation({
    mutationFn: attendanceApi.checkOut,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["attendance"] }),
    onError: (err) => setError(getErrorMessage(err)),
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
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Attendance & leave</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Check in for the day and manage your leave requests.</p>
      </div>

      <Card>
        <CardHeader>
          <h2 className="font-semibold text-[var(--color-text-primary)]">Today</h2>
        </CardHeader>
        <CardBody className="flex flex-wrap items-center gap-3">
          <Button isLoading={checkInMutation.isPending} onClick={() => checkInMutation.mutate()}>
            <LogIn className="h-4 w-4" /> Check in
          </Button>
          <Button variant="outline" isLoading={checkOutMutation.isPending} onClick={() => checkOutMutation.mutate()}>
            <LogOut className="h-4 w-4" /> Check out
          </Button>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <h2 className="font-semibold text-[var(--color-text-primary)]">Attendance history</h2>
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
