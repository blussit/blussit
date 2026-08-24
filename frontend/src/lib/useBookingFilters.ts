import { useMemo, useState } from "react";
import type { Booking } from "../types";

export type SortOrder = "newest" | "oldest";

/**
 * Search + sort + date-range filtering shared across every role's bookings
 * list (admin/manager/captain/customer) so they behave identically instead
 * of each page reinventing it. Operates entirely client-side on whatever
 * booking list the caller already fetched — matches the pattern already
 * established for the manager's booking queue (fetch a full page, filter in
 * memory) rather than adding new backend query params.
 *
 * Date range is two plain dates rather than a mode dropdown — simpler to
 * use: leave both empty for no filter, fill in just one for an open-ended
 * range (from this date onward, or up to this date), fill in both for a
 * bounded range.
 *
 * scheduled_date is a naive IST-digit string by design (see
 * backend/app/utils/timezone.py) — its first 10 characters ARE the IST
 * calendar date already, no timezone conversion needed to read it.
 */
export function useBookingFilters(bookings: Booking[], initialSort: SortOrder = "newest") {
  const [search, setSearch] = useState("");
  const [sortOrder, setSortOrder] = useState<SortOrder>(initialSort);
  const [dateFrom, setDateFrom] = useState(""); // "YYYY-MM-DD"
  const [dateTo, setDateTo] = useState(""); // "YYYY-MM-DD"

  const filtered = useMemo(() => {
    let result = bookings;

    const q = search.trim().toLowerCase();
    if (q) {
      result = result.filter(
        (b) => b.booking_number.toLowerCase().includes(q) || (b.customer_name?.toLowerCase().includes(q) ?? false)
      );
    }

    if (dateFrom || dateTo) {
      result = result.filter((b) => {
        const key = b.scheduled_date.slice(0, 10);
        if (dateFrom && key < dateFrom) return false;
        if (dateTo && key > dateTo) return false;
        return true;
      });
    }

    return [...result].sort((a, b) => {
      const at = new Date(a.scheduled_date).getTime();
      const bt = new Date(b.scheduled_date).getTime();
      return sortOrder === "newest" ? bt - at : at - bt;
    });
  }, [bookings, search, sortOrder, dateFrom, dateTo]);

  return { filtered, search, setSearch, sortOrder, setSortOrder, dateFrom, setDateFrom, dateTo, setDateTo };
}
