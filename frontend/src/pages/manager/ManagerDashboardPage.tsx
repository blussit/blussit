import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, ListChecks, Package, Users } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { complaintApi } from "../../api/engagement";
import { staffDirectoryApi, inventoryApi } from "../../api/admin";
import { Badge, Button, EmptyState, PageLoader, Panel, StatCard } from "../../components/ui";
import { useAuth } from "../../context/AuthContext";
import { ISSUE_LABELS } from "../../lib/constants";
import { format, minutesUntilSlotStart, URGENT_ASSIGNMENT_MINUTES, formatSlot } from "../../lib/date";

export default function ManagerDashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const centerId = user?.service_center_id || "";

  // All three read the server's own queue filters (the same ones the
  // booking queue uses), so the counts are exact however many bookings the
  // center has — nothing is inferred from "the newest N".
  const { data: bookings, isLoading } = useQuery({
    queryKey: ["center-bookings-summary", centerId],
    queryFn: () => bookingApi.forCenter(centerId, { scope: "needs_captain", page: 1, page_size: 1 }),
    enabled: !!centerId,
  });

  // Soonest first, so every booking that is about to start (or already
  // late) without a captain is on this first page.
  const { data: waiting } = useQuery({
    queryKey: ["center-bookings-urgent", centerId],
    queryFn: () => bookingApi.forCenter(centerId, { scope: "needs_captain", sort: "scheduled_asc", page: 1, page_size: 100 }),
    enabled: !!centerId,
    refetchInterval: 30000,
  });
  const urgentBookings = (waiting?.data || [])
    .filter((b) => minutesUntilSlotStart(b.scheduled_date, b.scheduled_slot) <= URGENT_ASSIGNMENT_MINUTES)
    .sort((a, b) => minutesUntilSlotStart(a.scheduled_date, a.scheduled_slot) - minutesUntilSlotStart(b.scheduled_date, b.scheduled_slot));

  // Same "open issue" flags the booking queue shows (captain never started,
  // running late, wash overrunning, etc.) — surfaced here too so a manager
  // doesn't have to go looking in the queue to notice something's wrong.
  const { data: issues } = useQuery({
    queryKey: ["center-bookings-issues", centerId],
    queryFn: () => bookingApi.forCenter(centerId, { scope: "open_issues", sort: "scheduled_asc", page: 1, page_size: 5 }),
    enabled: !!centerId,
    refetchInterval: 30000,
  });
  const openIssueBookings = issues?.data || [];
  const openIssueCount = issues?.meta.total ?? 0;

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
      <EmptyState
        icon={Users}
        title="No service center linked"
        description="Your manager account isn't assigned to a center yet — an admin can link it, then this dashboard fills in."
      />
    );
  }

  const lowStock = inventory?.meta.total ?? 0;
  const openComplaints = complaints?.meta.total ?? 0;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-black">Overview</h1>
        <p className="mt-1 text-sm text-gray-500">Daily operations at your center.</p>
      </div>

      {/* Things that need a decision right now come FIRST and stay quiet
          otherwise — one panel each, no colored page-wide washes. */}
      {urgentBookings.length > 0 && (
        <Panel
          title={`${urgentBookings.length} booking${urgentBookings.length > 1 ? "s" : ""} starting soon without a captain`}
          actions={
            <Button size="sm" onClick={() => navigate("/manager/bookings")}>
              Assign now
            </Button>
          }
          className="border-[var(--color-error)]"
        >
          <div className="space-y-1.5">
            {urgentBookings.slice(0, 5).map((b) => {
              const minutesLeft = minutesUntilSlotStart(b.scheduled_date, b.scheduled_slot);
              return (
                <div key={b.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="text-gray-600">
                    <span className="font-mono-num font-semibold text-black">{b.booking_number}</span> · {format(b.scheduled_date)} · {formatSlot(b.scheduled_slot)}
                    {b.customer_name ? ` · ${b.customer_name}` : ""}
                  </span>
                  <span className="font-semibold text-[var(--color-error)]">
                    {minutesLeft <= 0 ? "Starting now" : `in ${Math.round(minutesLeft)} min`}
                  </span>
                </div>
              );
            })}
          </div>
        </Panel>
      )}

      {openIssueCount > 0 && (
        <Panel
          title={`${openIssueCount} booking${openIssueCount > 1 ? "s" : ""} flagged for attention`}
          actions={
            <Button size="sm" variant="outline" onClick={() => navigate("/manager/bookings")}>
              Review
            </Button>
          }
        >
          <div className="space-y-1.5">
            {openIssueBookings.slice(0, 5).map((b) => (
              <div key={b.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="text-gray-600">
                  <span className="font-mono-num font-semibold text-black">{b.booking_number}</span> · {format(b.scheduled_date)} · {formatSlot(b.scheduled_slot)}
                  {b.customer_name ? ` · ${b.customer_name}` : ""}
                </span>
                <Badge tone="warning">{ISSUE_LABELS[b.issue_flag!] || b.issue_flag}</Badge>
              </div>
            ))}
          </div>
        </Panel>
      )}

      {isLoading ? (
        <PageLoader />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Pending bookings" value={bookings?.meta.total ?? 0} hint="Waiting for a captain" icon={ListChecks} to="/manager/bookings" />
          <StatCard label="Captains on team" value={captains?.meta.total ?? 0} hint="Active at this center" icon={Users} to="/manager/captains" />
          <StatCard
            label="Open complaints"
            value={openComplaints}
            hint={openComplaints > 0 ? "Needs a reply" : "Nothing open"}
            icon={AlertTriangle}
            tone={openComplaints > 0 ? "warning" : "muted"}
            to="/manager/complaints"
          />
          <StatCard
            label="Low stock items"
            value={lowStock}
            hint={lowStock > 0 ? "Reorder soon" : "Stock is healthy"}
            icon={Package}
            tone={lowStock > 0 ? "warning" : "muted"}
            to="/manager/inventory"
          />
        </div>
      )}
    </div>
  );
}
