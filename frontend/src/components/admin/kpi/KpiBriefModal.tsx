import { type ReactNode } from "react";
import { Modal } from "../../ui";

/**
 * A KPI tile that's a RATIO or computed figure — no underlying record list
 * exists for "churn rate" or "ROAS" the way one does for "bookings". Shows
 * the definition (the same text already in the hover tip) plus whatever
 * breakdown numbers the tab already has loaded — no extra fetch, since the
 * section query that renders the tile already carries them.
 */
export function KpiBriefModal({
  open,
  onClose,
  title,
  value,
  tip,
  breakdown,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  value: ReactNode;
  tip: string;
  breakdown?: { label: string; value: ReactNode }[];
}) {
  return (
    <Modal open={open} onClose={onClose} title={title}>
      <div className="space-y-4">
        <p className="font-mono-num text-3xl font-bold text-black">{value}</p>
        <p className="text-sm text-gray-600">{tip}</p>
        {breakdown && breakdown.length > 0 && (
          <div className="divide-y divide-[#FAF3DF] rounded-xl border border-[#F3E5B5]">
            {breakdown.map((b) => (
              <div key={b.label} className="flex items-center justify-between px-3.5 py-2.5 text-sm">
                <span className="text-gray-500">{b.label}</span>
                <span className="font-mono-num font-semibold text-black">{b.value}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </Modal>
  );
}
