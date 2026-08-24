import { useQuery } from "@tanstack/react-query";
import { IndianRupee, Percent, ShoppingBag, Users } from "lucide-react";
import { analyticsApi } from "../../api/admin";
import { Card, CardBody, CardHeader, PageLoader } from "../../components/ui";

export default function AdminDashboardPage() {
  const { data, isLoading } = useQuery({ queryKey: ["admin-dashboard"], queryFn: analyticsApi.dashboard });
  const { data: trends } = useQuery({ queryKey: ["admin-trends"], queryFn: () => analyticsApi.bookingTrends(14) });

  if (isLoading || !data) return <PageLoader />;

  const stats = [
    { label: "Total revenue", value: `₹${data.total_revenue}`, icon: IndianRupee },
    { label: "Today's orders", value: String(data.todays_orders), icon: ShoppingBag },
    { label: "Active customers", value: String(data.active_customers), icon: Users },
    { label: "Repeat customer rate", value: `${data.repeat_customer_rate}%`, icon: Percent },
  ];

  const maxRevenue = Math.max(...(trends || []).map((t) => t.revenue), 1);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Analytics dashboard</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Business performance at a glance.</p>
      </div>

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

      <Card>
        <CardHeader>
          <h2 className="font-semibold text-[var(--color-text-primary)]">Booking trends (last 14 days)</h2>
        </CardHeader>
        <CardBody>
          <div className="flex h-40 items-end gap-2">
            {(trends || []).map((t) => (
              <div key={t.date} className="flex flex-1 flex-col items-center gap-1.5">
                <div
                  className="w-full rounded-t-md bg-[var(--color-primary)] transition-all"
                  style={{ height: `${Math.max((t.revenue / maxRevenue) * 100, 4)}%` }}
                  title={`₹${t.revenue} · ${t.bookings} bookings`}
                />
              </div>
            ))}
          </div>
        </CardBody>
      </Card>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <h2 className="font-semibold text-[var(--color-text-primary)]">Best performing captains</h2>
          </CardHeader>
          <CardBody className="space-y-3">
            {(data.best_performing_captains as { captain_id: string; jobs_completed: number; revenue: number }[]).map((c) => (
              <div key={c.captain_id} className="flex items-center justify-between text-sm">
                <span className="font-mono-num text-xs text-[var(--color-text-secondary)]">{c.captain_id.slice(-6)}</span>
                <span>{c.jobs_completed} jobs · ₹{c.revenue}</span>
              </div>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <h2 className="font-semibold text-[var(--color-text-primary)]">Best service centers</h2>
          </CardHeader>
          <CardBody className="space-y-3">
            {(data.best_service_centers as { service_center_id: string; jobs_completed: number; revenue: number }[]).map((c) => (
              <div key={c.service_center_id} className="flex items-center justify-between text-sm">
                <span className="font-mono-num text-xs text-[var(--color-text-secondary)]">{c.service_center_id.slice(-6)}</span>
                <span>{c.jobs_completed} jobs · ₹{c.revenue}</span>
              </div>
            ))}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
