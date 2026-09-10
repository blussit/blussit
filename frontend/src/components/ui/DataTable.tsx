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
    // Console table: hairline yellow frame, quiet grey header, and a warm
    // row hover so a clickable row reads as clickable without borders or
    // shadows shouting on every line.
    <div className="overflow-x-auto rounded-2xl border border-[#F3E5B5] bg-white">
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead>
          <tr className="border-b border-[#F3E5B5]">
            {columns.map((col) => (
              <th key={col.header} className="px-4 py-3 text-xs font-medium text-gray-500">
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr
              key={row.id}
              className={`border-b border-[#FAF3DF] last:border-0 transition-colors ${onRowClick ? "cursor-pointer hover:bg-[#FFFCF0]" : ""}`}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((col) => (
                <td key={col.header} className={`px-4 py-3.5 text-black ${col.className || ""}`}>
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
