import { MapPin } from "lucide-react";
import type { Address } from "../../types";

/**
 * "Use a saved address" chips — shared between the customer's own booking
 * flow (NewBookingPage) and the subscription quick-book flow
 * (SubscriptionQuickBook). Tapping a chip hands the whole Address back to
 * the caller to fill its own fields with; tapping "+ New address" just
 * signals intent, the caller owns whatever manual-entry form it shows.
 */
export function AddressPicker({
  addresses,
  selectedId,
  showingNewForm,
  onSelect,
  onAddNew,
}: {
  addresses: Address[] | undefined;
  selectedId: string | null;
  showingNewForm: boolean;
  onSelect: (address: Address) => void;
  onAddNew: () => void;
}) {
  if (!addresses?.length) return null;

  return (
    <div>
      <p className="mb-1.5 flex items-center gap-1.5 text-sm font-medium text-[var(--color-text-primary)]">
        <MapPin className="h-3.5 w-3.5" /> Use a saved address
      </p>
      <div className="flex flex-wrap gap-2">
        {addresses.map((a) => {
          const selected = !showingNewForm && selectedId === a.id;
          return (
            <button
              key={a.id}
              type="button"
              onClick={() => onSelect(a)}
              className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors ${
                selected ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
              }`}
            >
              {a.label} · {a.line1}
              {a.city ? `, ${a.city}` : ""}
            </button>
          );
        })}
        <button
          type="button"
          onClick={onAddNew}
          className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors ${
            showingNewForm ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white" : "border-dashed border-gray-300 text-[var(--color-text-secondary)] hover:border-gray-400"
          }`}
        >
          + New address
        </button>
      </div>
    </div>
  );
}
