import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageCircle } from "lucide-react";
import { staffMessagingApi } from "../../api/crm";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { Card, CardBody, CardHeader, ErrorState, Spinner, Switch } from "../ui";

/**
 * The manager's own "WhatsApp Alerts For New Bookings" switch (on by
 * default). Managers get WhatsApp ONLY for new bookings — everything else
 * is in-app — so this switches the one WhatsApp they get.
 */
export function WhatsAppAlertsCard() {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ["notification-preferences"],
    queryFn: staffMessagingApi.preferences,
  });
  const save = useMutation({
    mutationFn: (enabled: boolean) => staffMessagingApi.setNewBookingAlerts(enabled),
    onSuccess: (prefs) => {
      queryClient.setQueryData(["notification-preferences"], prefs);
      pushToast({ tone: "success", title: prefs.whatsapp_new_booking_alerts ? "WhatsApp Alerts On" : "WhatsApp Alerts Off" });
    },
    onError: (err) => pushToast({ tone: "error", title: "Couldn't Save", message: getErrorMessage(err) }),
  });
  const on = save.isPending ? !!save.variables : data?.whatsapp_new_booking_alerts !== false;
  return (
    <Card>
      <CardHeader className="flex items-center gap-2">
        <MessageCircle className="h-4 w-4 text-[var(--color-primary)]" />
        <h2 className="font-semibold text-[var(--color-text-primary)]">Notifications</h2>
      </CardHeader>
      <CardBody>
        {isLoading ? (
          <Spinner className="h-4 w-4" />
        ) : isError && !data ? (
          <ErrorState className="p-4" message="Couldn't load your alert settings." busy={isFetching} onRetry={() => void refetch()} />
        ) : (
          <Switch
            checked={on}
            disabled={save.isPending}
            onChange={(next) => save.mutate(next)}
            label="WhatsApp Alerts For New Bookings"
            description="A WhatsApp to your phone for every new booking. Everything else stays in the app."
          />
        )}
      </CardBody>
    </Card>
  );
}

/** Admin, per manager: a compact on/off for that manager's alerts. */
export function ManagerAlertsToggle({ userId, enabled, name }: { userId: string; enabled: boolean; name?: string }) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const save = useMutation({
    mutationFn: (next: boolean) => staffMessagingApi.setNewBookingAlerts(next, userId),
    onSuccess: (prefs) => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      pushToast({ tone: "success", title: `${name || "Manager"}: WhatsApp Alerts ${prefs.whatsapp_new_booking_alerts ? "On" : "Off"}` });
    },
    onError: (err) => pushToast({ tone: "error", title: "Couldn't Save", message: getErrorMessage(err) }),
  });
  const on = save.isPending ? !!save.variables : enabled;
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={`WhatsApp alerts for new bookings — ${name || "manager"}`}
      title="WhatsApp alerts for new bookings"
      disabled={save.isPending}
      onClick={(e) => {
        e.stopPropagation();
        save.mutate(!on);
      }}
      className="inline-flex min-h-11 items-center gap-2 text-xs font-medium text-[var(--color-text-secondary)] disabled:opacity-60 sm:min-h-0"
    >
      <span aria-hidden className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${on ? "bg-[var(--ui-accent,var(--color-primary))]" : "bg-gray-300"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${on ? "translate-x-[18px]" : "translate-x-0.5"}`} />
      </span>
      {on ? "On" : "Off"}
    </button>
  );
}
