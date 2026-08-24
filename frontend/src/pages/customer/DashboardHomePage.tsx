import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { CalendarPlus, Car, Gift, ListChecks } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { subscriptionApi } from "../../api/engagement";
import { Badge, Button, Card, CardBody, CardHeader, EmptyState, PageLoader, StatusBadge } from "../../components/ui";
import { useAuth } from "../../context/AuthContext";
import { format } from "../../lib/date";

export default function CustomerDashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();

  const { data: bookings, isLoading: bookingsLoading } = useQuery({
    queryKey: ["my-bookings", "recent"],
    queryFn: () => bookingApi.myBookings({ page: 1, page_size: 5 }),
  });

  const { data: subscriptions } = useQuery({
    queryKey: ["my-subscriptions"],
    queryFn: subscriptionApi.mySubscriptions,
  });

  const activeSubs = (subscriptions || []).filter((s) => s.status === "active");
  const upcoming = (bookings?.data || []).filter((b) => !["completed", "cancelled"].includes(b.status));

  return (
    <div className="space-y-8">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Welcome back, {user?.full_name?.split(" ")[0]}</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Here's what's happening with your vehicles.</p>
        </div>
        <Button onClick={() => navigate("/app/book")}>
          <CalendarPlus className="h-4 w-4" /> Book a service
        </Button>
      </div>

      <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
        <Card>
          <CardBody className="flex items-center gap-4">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-[var(--color-primary-light)] text-[var(--color-primary)]">
              <ListChecks className="h-5 w-5" />
            </span>
            <div>
              <p className="font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">{upcoming.length}</p>
              <p className="text-sm text-[var(--color-text-secondary)]">Upcoming bookings</p>
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardBody className="flex items-center gap-4">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-[var(--color-accent-light)] text-[var(--color-accent)]">
              <Gift className="h-5 w-5" />
            </span>
            <div>
              <p className="font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">{activeSubs.length}</p>
              <p className="text-sm text-[var(--color-text-secondary)]">Active subscriptions</p>
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardBody className="flex items-center gap-4">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-[var(--color-secondary-light)] text-sky-600">
              <Car className="h-5 w-5" />
            </span>
            <div>
              <p className="font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">{bookings?.meta.total ?? 0}</p>
              <p className="text-sm text-[var(--color-text-secondary)]">Total bookings</p>
            </div>
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex items-center justify-between">
          <h2 className="font-semibold text-[var(--color-text-primary)]">Recent bookings</h2>
          <Button variant="ghost" size="sm" onClick={() => navigate("/app/bookings")}>
            View all
          </Button>
        </CardHeader>
        <CardBody>
          {bookingsLoading ? (
            <PageLoader />
          ) : !bookings?.data.length ? (
            <EmptyState title="No bookings yet" description="Book your first doorstep service to see it here." action={<Button onClick={() => navigate("/app/book")}>Book now</Button>} />
          ) : (
            <div className="divide-y divide-gray-100">
              {bookings.data.map((b) => (
                <div key={b.id} className="flex items-center justify-between gap-4 py-3.5">
                  <div>
                    <p className="font-mono-num text-sm font-semibold text-[var(--color-text-primary)]">{b.booking_number}</p>
                    <p className="text-xs text-[var(--color-text-secondary)]">
                      {format(b.scheduled_date)} · {b.scheduled_slot}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <Badge tone="neutral" className="hidden sm:inline-flex">₹{b.total_amount}</Badge>
                    <StatusBadge status={b.status} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
