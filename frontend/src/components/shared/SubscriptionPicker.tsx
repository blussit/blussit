import type { SubscriptionPlan, UserSubscription } from "../../types";

/**
 * "Pay with a plan" picker — shared between the customer's own booking flow
 * (NewBookingPage) and the manager's book-on-behalf-of-customer flow
 * (ManagerNewBookingPage). The caller is responsible for filtering
 * `subscriptions` down to what's actually eligible for the booking in
 * progress (effective_status === "active", remaining_service_count > 0,
 * and vehicle_id matching the selected vehicle) — this component just
 * renders whatever list it's given.
 */
export function SubscriptionPicker({
  subscriptions,
  plans,
  selectedId,
  onSelect,
}: {
  subscriptions: UserSubscription[];
  plans: SubscriptionPlan[] | undefined;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  if (!subscriptions.length) return null;

  return (
    <div>
      <div className="flex flex-wrap gap-2">
        {subscriptions.map((sub) => {
          const plan = plans?.find((p) => p.id === sub.plan_id);
          const selected = selectedId === sub.id;
          return (
            <button
              key={sub.id}
              type="button"
              onClick={() => onSelect(selected ? null : sub.id)}
              className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors ${
                selected
                  ? "border-[var(--color-success)] bg-[var(--color-success)] text-white"
                  : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
              }`}
            >
              {plan?.name || "Subscription"} · {sub.remaining_service_count} left
            </button>
          );
        })}
      </div>
      {selectedId && (
        <p className="mt-1.5 text-xs text-[var(--color-text-secondary)]">
          This booking will use one service from this plan instead of being charged directly.
        </p>
      )}
    </div>
  );
}
