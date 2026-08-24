import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, ListChecks, Package, Users } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { complaintApi } from "../../api/engagement";
import { staffDirectoryApi, inventoryApi } from "../../api/admin";
import { Badge, Button, Card, CardBody, PageLoader } from "../../components/ui";
import { useAuth } from "../../context/AuthContext";
import { ISSUE_LABELS, isOpenIssue, needsCaptain } from "../../lib/constants";
import { format, minutesUntilSlotStart, URGENT_ASSIGNMENT_MINUTES } from "../../lib/date";

export default function ManagerDashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const centerId = user?.service_center_id || "";

  const { data: bookings, isLoading } = useQuery({
    queryKey: ["center-bookings-summary", centerId],
    queryFn: () => bookingApi.forCenter(centerId, { status: "pending", page: 1, page_size: 5 }),
    enabled: !!centerId,
  });

  // A wider, unfiltered fetch just to compute urgency — "pending" alone (the
  // stat tile query above) misses rescheduled bookings that also still need
  // a captain, and its page_size:5 + creation-date sort could miss the
  // actually-soonest one if there are more than 5 pending bookings.
  const { data: allBookings } = useQuery({
    queryKey: ["center-bookings-urgent", centerId],
    queryFn: () => bookingApi.forCenter(centerId, { page: 1, page_size: 100 }),
    enabled: !!centerId,
    refetchInterval: 30000,
  });
  const urgentBookings = (allBookings?.data || [])
    .filter((b) => needsCaptain(b) && minutesUntilSlotStart(b.scheduled_date, b.scheduled_slot) <= URGENT_ASSIGNMENT_MINUTES)
    .sort((a, b) => minutesUntilSlotStart(a.scheduled_date, a.scheduled_slot) - minutesUntilSlotStart(b.scheduled_date, b.scheduled_slot));

  // Same "open issue" flags the booking queue shows (captain never started,
  // running late, wash overrunning, etc.) — surfaced here too so a manager
  // doesn't have to go looking in the queue to notice something's wrong.
  const openIssueBookings = (allBookings?.data || []).filter(isOpenIssue);

  const { data: captains } = useQuery({
    queryKey: ["center-captains", centerId],
    queryFn: () => staffDirectoryApi.captainsForCenter(centerId, { page: 1, page_size: 1 }),
    enabled: !!centerId,
  });

  const { data: complaints } = useQuery({
    queryKey: ["center-complaints", centerId],
    queryFn: () => complaintApi.forCenter(centerId, { status: "open", page: 1, page_size: 1 }),
    enabled: !!centerId,
  });

  const { data: inventory } = useQuery({
    queryKey: ["center-inventory-low", centerId],
    queryFn: () => inventoryApi.forCenter(centerId, { page: 1, page_size: 50, low_stock_only: true }),
    enabled: !!centerId,
  });

  if (!centerId) {
    return (
      <Card>
        <CardBody>
          <p className="text-sm text-[var(--color-text-secondary)]">
            You are not yet assigned to a service center. Please contact the admin team.
          </p>
        </CardBody>
      </Card>
    );
  }

  const stats = [
    { label: "Pending bookings", value: bookings?.meta.total ?? 0, icon: ListChecks },
    { label: "Captains on team", value: captains?.meta.total ?? 0, icon: Users },
    { label: "Open complaints", value: complaints?.meta.total ?? 0, icon: AlertTriangle },
    { label: "Low stock items", value: inventory?.meta.total ?? 0, icon: Package },
  ];

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Service center overview</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Daily operations at a glance.</p>
      </div>

      {urgentBookings.length > 0 && (
        <Card className="border-l-4 border-l-[var(--color-error)] bg-red-50/40 p-4">
          <div className="flex items-center gap-2 font-semibold text-[var(--color-error)]">
            <AlertTriangle className="h-5 w-5" />
            {urgentBookings.length} booking{urgentBookings.length > 1 ? "s" : ""} starting soon still need{urgentBookings.length > 1 ? "" : "s"} a captain
          </div>
          <div className="mt-3 space-y-2">
            {urgentBookings.map((b) => {
              const minutesLeft = minutesUntilSlotStart(b.scheduled_date, b.scheduled_slot);
              return (
                <div key={b.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-white px-3 py-2 text-sm">
                  <span>
                    <span className="font-mono-num font-semibold">{b.booking_number}</span> — {format(b.scheduled_date)} · {b.scheduled_slot}
                    {b.customer_name ? ` · ${b.customer_name}` : ""}
                  </span>
                  <span className="font-semibold text-[var(--color-error)]">
                    {minutesLeft <= 0 ? "Starting now" : `Starts in ${Math.round(minutesLeft)} min`}
                  </span>
                </div>
              );
            })}
          </div>
          <Button size="sm" className="mt-3" onClick={() => navigate("/manager/bookings")}>
            Assign now
          </Button>
        </Card>
      )}

      {openIssueBookings.length > 0 && (
        <Card className="border-l-4 border-l-amber-500 bg-amber-50/40 p-4">
          <div className="flex items-center gap-2 font-semibold text-amber-700">
            <AlertTriangle className="h-5 w-5" />
            {openIssueBookings.length} booking{openIssueBookings.length > 1 ? "s" : ""} flagged for attention
          </div>
          <div className="mt-3 space-y-2">
            {openIssueBookings.map((b) => (
              <div key={b.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-white px-3 py-2 text-sm">
                <span>
                  <span className="font-mono-num font-semibold">{b.booking_number}</span> — {format(b.scheduled_date)} · {b.scheduled_slot}
                  {b.customer_name ? ` · ${b.customer_name}` : ""}
                </span>
                <Badge tone="warning">{ISSUE_LABELS[b.issue_flag!] || b.issue_flag}</Badge>
              </div>
            ))}
          </div>
          <Button size="sm" className="mt-3" onClick={() => navigate("/manager/bookings")}>
            Review flagged bookings
          </Button>
        </Card>
      )}

      {isLoading ? (
        <PageLoader />
      ) : (
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {stats.map((s) => (
            <Card key={s.label}>
              <CardBody className="flex items-center gap-4">
                <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-[var(--color-primary-light)] text-[var(--color-primary)]">
                  <s.icon className="h-5 w-5" />
                </span>
                <div>
                  <p className="font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">{s.value}</p>
                  <p className="text-sm text-[var(--color-text-secondary)]">{s.label}</p>
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
