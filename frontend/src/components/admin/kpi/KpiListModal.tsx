import { type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Modal, PageLoader } from "../../ui";
import type { ApiPaginated } from "../../../lib/api-client";

/**
 * A KPI tile that maps to REAL records — "click on it to see the list of
 * bookings done today" (founder's own words). Fetches through the exact
 * same period the dashboard tile itself used, so the count on the tile and
 * the number of rows here always agree.
 */
export function KpiListModal<T>({
  open,
  onClose,
  title,
  queryKey,
  fetchFn,
  renderRow,
  getRowKey,
  emptyText = "Nothing in this period.",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  queryKey: unknown[];
  fetchFn: () => Promise<ApiPaginated<T>>;
  renderRow: (item: T) => ReactNode;
  getRowKey: (item: T) => string;
  emptyText?: string;
}) {
  const { data, isLoading } = useQuery({ queryKey, queryFn: fetchFn, enabled: open });

  return (
    <Modal open={open} onClose={onClose} title={title} maxWidth="max-w-2xl">
      {isLoading || !data ? (
        <PageLoader />
      ) : !data.data.length ? (
        <p className="rounded-xl bg-gray-50 p-4 text-center text-sm text-gray-500">{emptyText}</p>
      ) : (
        <div className="space-y-2">
          <p className="text-xs text-gray-400">
            {data.meta.total} total{data.meta.total > data.data.length ? ` — showing the first ${data.data.length}` : ""}
          </p>
          <div className="max-h-[60vh] divide-y divide-[#FAF3DF] overflow-y-auto rounded-xl border border-[#F3E5B5]">
            {data.data.map((item) => (
              <div key={getRowKey(item)} className="px-3.5 py-2.5">
                {renderRow(item)}
              </div>
            ))}
          </div>
        </div>
      )}
    </Modal>
  );
}
