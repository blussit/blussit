import type { ReactNode } from "react";
import { PageLoader } from "./Spinner";
import { EmptyState } from "./EmptyState";
import { ErrorState } from "./ErrorState";

export interface Column<T> {
  header: string;
  accessor: (row: T) => ReactNode;
  className?: string;
}

export function DataTable<T extends { id: string }>({
  columns,
  data,
  isLoading,
  emptyTitle = "Nothing Here Yet",
  emptyDescription,
  onRowClick,
  error,
  onRetry,
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
  /** The read behind `data` failed (and there's nothing cached to show):
   *  an error card with "Try Again" instead of a misleading empty state. */
  error?: unknown;
  onRetry?: () => void;
}) {
  if (isLoading) return <PageLoader />;
  if (error && !data.length) return <ErrorState message="Couldn't load this list." onRetry={onRetry} />;
  if (!data.length) return <EmptyState title={emptyTitle} description={emptyDescription} />;

  return (
    // Console table: hairline frame, quiet grey header, and a cool
    // row hover so a clickable row reads as clickable without borders or
    // shadows shouting on every line.
    <div className="overflow-x-auto rounded-2xl border border-[var(--color-card-border)] bg-white">
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead>
          <tr className="border-b border-[var(--color-card-border)]">
            {columns.map((col) => (
              <th key={col.header} className="px-4 py-3 text-xs font-medium text-[var(--ui-muted,#5F6878)]">
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr
              key={row.id}
              className={`border-b border-[var(--ui-row-line,#EEF2F7)] last:border-0 transition-colors ${onRowClick ? "cursor-pointer hover:bg-[var(--ui-row-hover,#F7F9FC)]" : ""}`}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((col) => (
                <td key={col.header} className={`px-4 py-3.5 text-[var(--ui-ink,#0E1A33)] ${col.className || ""}`}>
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
