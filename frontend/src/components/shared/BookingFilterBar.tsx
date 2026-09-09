import { Input, Select } from "../ui";
import type { SortOrder } from "../../lib/useBookingFilters";

export function BookingFilterBar({
  search,
  onSearchChange,
  sortOrder,
  onSortOrderChange,
  dateFrom,
  onDateFromChange,
  dateTo,
  onDateToChange,
  searchPlaceholder = "Booking # or customer name",
}: {
  search: string;
  onSearchChange: (v: string) => void;
  sortOrder: SortOrder;
  onSortOrderChange: (v: SortOrder) => void;
  dateFrom: string;
  onDateFromChange: (v: string) => void;
  dateTo: string;
  onDateToChange: (v: string) => void;
  searchPlaceholder?: string;
}) {
  return (
    // Mobile: a deliberate 2×2 grid — Search+Sort share the first row,
    // From+To the second (flex-wrap used to stack them into four sloppy
    // rows on a phone). Desktop keeps the one-line flex layout.
    <div className="grid grid-cols-2 items-end gap-3 sm:flex sm:flex-wrap">
      <div className="sm:min-w-[220px] sm:flex-1">
        <Input label="Search" placeholder={searchPlaceholder} value={search} onChange={(e) => onSearchChange(e.target.value)} />
      </div>
      <div className="sm:w-40">
        <Select label="Sort" value={sortOrder} onChange={(e) => onSortOrderChange(e.target.value as SortOrder)}>
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
        </Select>
      </div>
      <div className="sm:w-40">
        <Input
          label="From"
          type="date"
          value={dateFrom}
          max={dateTo || undefined}
          onChange={(e) => onDateFromChange(e.target.value)}
        />
      </div>
      <div className="sm:w-40">
        <Input label="To" type="date" value={dateTo} min={dateFrom || undefined} onChange={(e) => onDateToChange(e.target.value)} />
      </div>
      {(dateFrom || dateTo) && (
        <button
          type="button"
          onClick={() => {
            onDateFromChange("");
            onDateToChange("");
          }}
          className="pb-2.5 text-xs font-medium text-[var(--color-primary)] hover:underline"
        >
          Clear dates
        </button>
      )}
    </div>
  );
}
