import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Bell, BellOff, ChevronRight } from "lucide-react";
import { notificationApi } from "../../api/engagement";
import { useAuth } from "../../context/AuthContext";
import { Button, Card, EmptyState, PageLoader } from "../../components/ui";
import { formatDateTime } from "../../lib/date";
import { notificationTargetPath } from "../../lib/notifications";
import type { Notification } from "../../types";

export default function NotificationsPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [page, setPage] = useState(1);
  const { data, isLoading } = useQuery({ queryKey: ["notifications", page], queryFn: () => notificationApi.list({ page, page_size: 15 }) });

  const markReadMutation = useMutation({
    mutationFn: notificationApi.markRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });

  const openNotification = (n: Notification) => {
    if (!n.is_read) markReadMutation.mutate(n.id);
    const path = user ? notificationTargetPath(n, user.role) : null;
    if (path) navigate(path);
  };

  const markAllReadMutation = useMutation({
    mutationFn: notificationApi.markAllRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Notifications</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">{data?.unread_count || 0} unread</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => markAllReadMutation.mutate()}>
          <BellOff className="h-4 w-4" /> Mark all read
        </Button>
      </div>

      {isLoading ? (
        <PageLoader />
      ) : !data?.data.length ? (
        <EmptyState icon={Bell} title="No notifications yet" />
      ) : (
        <div className="space-y-2">
          {data.data.map((n) => {
            const path = user ? notificationTargetPath(n, user.role) : null;
            return (
              <Card
                key={n.id}
                className={`cursor-pointer p-4 transition-colors hover:bg-gray-50 ${!n.is_read ? "border-[var(--color-primary)]" : ""}`}
                onClick={() => openNotification(n)}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-[var(--color-text-primary)]">{n.title}</p>
                    <p className="mt-0.5 text-sm text-[var(--color-text-secondary)]">{n.message}</p>
                    <p className="mt-1.5 text-xs text-gray-400">{formatDateTime(n.created_at)}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {!n.is_read && <span className="h-2 w-2 rounded-full bg-[var(--color-primary)]" />}
                    {path && <ChevronRight className="h-4 w-4 text-gray-300" />}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {data && data.meta.total_pages > 1 && (
        <div className="flex justify-center gap-2">
          <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            Previous
          </Button>
          <Button size="sm" variant="outline" disabled={page >= data.meta.total_pages} onClick={() => setPage((p) => p + 1)}>
            Next
          </Button>
        </div>
      )}
    </div>
  );
}
