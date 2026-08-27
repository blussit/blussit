import type { ReactNode } from "react";
import { PageLoader } from "./Spinner";
import { EmptyState } from "./EmptyState";

export interface Column<T> {
  header: string;
  accessor: (row: T) => ReactNode;
  className?: string;
}

export function DataTable<T extends { id: string }>({
  columns,
  data,
  isLoading,
  emptyTitle = "Nothing here yet",
  emptyDescription,
  onRowClick,
}: {
  columns: Column<T>[];
  data: T[];
  isLoading?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  /** When set, the whole row opens `row` (e.g. a detail drawer) instead of
   * requiring a separate small "View" button/icon — the row itself IS the
   * click target. Any column that renders its own interactive element
   * (a button, another link) should stopPropagation() in its own handler
   * so it doesn't also fire the row click. */
  onRowClick?: (row: T) => void;
}) {
  if (isLoading) return <PageLoader />;
  if (!data.length) return <EmptyState title={emptyTitle} description={emptyDescription} />;

  return (
    <div className="overflow-x-auto rounded-2xl border border-gray-100">
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead>
          <tr className="border-b border-gray-100 bg-gray-50/60">
            {columns.map((col) => (
              <th key={col.header} className="px-4 py-3 font-medium text-[var(--color-text-secondary)]">
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr
              key={row.id}
              className={`border-b border-gray-50 last:border-0 hover:bg-gray-50/50 ${onRowClick ? "cursor-pointer" : ""}`}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((col) => (
                <td key={col.header} className={`px-4 py-3.5 text-[var(--color-text-primary)] ${col.className || ""}`}>
                  {col.accessor(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
