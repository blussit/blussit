import { type ReactNode, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Modal, PageLoader } from "../../ui";
import { Pager } from "../../shared/ListControls";
import { type ApiPaginated } from "../../../lib/api-client";

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
  /** One page of the list (server-paginated). */
  fetchFn: (page: number) => Promise<ApiPaginated<T>>;
  renderRow: (item: T) => ReactNode;
  getRowKey: (item: T) => string;
  emptyText?: string;
}) {
  const [page, setPage] = useState(1);
  const drillKey = JSON.stringify(queryKey);
  useEffect(() => setPage(1), [drillKey, open]);
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: [...queryKey, page],
    queryFn: () => fetchFn(page),
    enabled: open,
  });

  return (
    <Modal open={open} onClose={onClose} title={title} maxWidth="max-w-2xl">
      {isError && !data ? (
        <ErrorState message="Couldn't load this list." onRetry={() => void refetch()} busy={isFetching} />
      ) : isLoading || !data ? (
        <PageLoader />
      ) : !data.data.length ? (
        <p className="rounded-xl bg-gray-50 p-4 text-center text-sm text-gray-500">{emptyText}</p>
      ) : (
        <div className="space-y-2">
          <p className="text-xs text-gray-400">{data.meta.total} Total</p>
          <div className="max-h-[60vh] divide-y divide-[#FAF3DF] overflow-y-auto rounded-xl border border-[#F3E5B5]">
            {data.data.map((item) => (
              <div key={getRowKey(item)} className="px-3.5 py-2.5">
                {renderRow(item)}
              </div>
            ))}
          </div>
          <Pager page={page} totalPages={data.meta.total_pages} onPage={setPage} busy={isFetching} />
        </div>
      )}
    </Modal>
  );
}
