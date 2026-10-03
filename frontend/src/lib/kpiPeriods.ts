/**
 * The one period picker and Bookings/Plans revenue-scope toggle, shared by
 * the admin dashboard's hero revenue tile and a manager's own Sales
 * section — same options, same labels, same KpiPeriodParams shape, so
 * both consumers of RevenueDrillModal behave identically.
 */
export const PERIODS = [
  { key: "today", label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "7d", label: "Last 7 Days" },
  { key: "30d", label: "Last 30 Days" },
  { key: "this_month", label: "This Month" },
  { key: "last_month", label: "Last Month" },
] as const;

export type RevenueScope = "combined" | "bookings" | "plans";

export const REVENUE_SCOPES: { key: RevenueScope; label: string }[] = [
  { key: "combined", label: "Bookings + Plans" },
  { key: "bookings", label: "Bookings Only" },
  { key: "plans", label: "Plans Only" },
];
